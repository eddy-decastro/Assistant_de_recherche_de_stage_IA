"""Gestionnaire de tâches en arrière-plan (Background Task Manager).

Permet d'exécuter n'importe quelle action longue (collecte, backfill, re-notation, pipeline)
dans un sous-processus détaché sans bloquer Streamlit. L'utilisateur peut naviguer
librement entre les pages : la tâche continue de tourner, ses journaux sont écrits
sur disque et sa progression reste consultable en temps réel sur n'importe quelle page.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import streamlit as st

from src.config import PROJECT_ROOT
from utils.data import bump_data_version

logger = logging.getLogger("utils.task_manager")

TASKS_DIR = PROJECT_ROOT / "data" / "tasks"
ACTIVE_TASK_FILE = TASKS_DIR / "active_task.json"

# Stockage des processus en mémoire pour le runtime Streamlit
_RUNNING_PROCESSES: dict[str, subprocess.Popen[str]] = {}


def _ensure_tasks_dir() -> None:
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def is_pid_running(pid: int) -> bool:
    """Vérifie si un PID est toujours actif sur le système d'exploitation."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            cmd = ["tasklist", "/FI", f"PID eq {pid}", "/NH"]
            res = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=3)
            return str(pid) in res.stdout
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False


def start_background_task(
    key: str,
    name: str,
    command: list[str],
    description: str = "",
) -> tuple[bool, str]:
    """Démarre une nouvelle tâche en arrière-plan.

    Retourne (succès: bool, message: str). Si une tâche tourne déjà, refuse le lancement.
    """
    _ensure_tasks_dir()
    current = get_active_task()
    if current and current.get("status") == "running":
        return False, f"Une tâche est déjà en cours d'exécution : {current.get('name')}."

    log_path = TASKS_DIR / f"{key}.log"
    try:
        # Ouvrir le fichier de log en écriture (vidange du log précédent)
        log_file = open(log_path, "w", encoding="utf-8", errors="replace", buffering=1)
    except Exception as exc:
        return False, f"Impossible d'ouvrir le fichier de journalisation : {exc}"

    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
    }

    try:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            env=env,
        )
    except Exception as exc:
        log_file.close()
        return False, f"Échec lors du démarrage du sous-processus : {exc}"

    _RUNNING_PROCESSES[key] = process

    state: dict[str, Any] = {
        "key": key,
        "name": name,
        "description": description,
        "command": command,
        "pid": process.pid,
        "log_path": str(log_path),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "running",
        "return_code": None,
    }

    try:
        ACTIVE_TASK_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as exc:
        logger.warning("Échec d'écriture du fichier d'état : %s", exc)

    logger.info("Tâche d'arrière-plan démarrée : %s (PID %d)", name, process.pid)
    return True, "Tâche démarrée en arrière-plan avec succès."


def get_active_task() -> dict[str, Any] | None:
    """Retourne l'état consolidé de la tâche active ou None."""
    _ensure_tasks_dir()
    if not ACTIVE_TASK_FILE.exists():
        return None

    try:
        state = json.loads(ACTIVE_TASK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None

    key = str(state.get("key", ""))
    pid = int(state.get("pid", 0))
    current_status = state.get("status", "idle")

    # Si marquée comme en cours, vérifier si elle tourne toujours
    if current_status == "running":
        process = _RUNNING_PROCESSES.get(key)
        if process is not None:
            ret = process.poll()
            if ret is not None:
                state["status"] = "completed" if ret == 0 else "failed"
                state["return_code"] = ret
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
                ACTIVE_TASK_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
        else:
            if not is_pid_running(pid):
                state["status"] = "failed"
                state["return_code"] = -1
                state["finished_at"] = datetime.now(timezone.utc).isoformat()
                ACTIVE_TASK_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")

    # Lire les derniers logs
    log_path = Path(state.get("log_path", ""))
    lines: list[str] = []
    if log_path.exists():
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
            lines = [line for line in content.splitlines() if line.strip()]
        except Exception:
            lines = []

    state["logs"] = lines[-250:]  # 250 dernières lignes

    # Extraction d'indicateurs de progression
    progress = _parse_progress(lines)
    state["progress"] = progress

    return state


def _parse_progress(lines: list[str]) -> dict[str, Any] | None:
    """Analyse les lignes de logs pour déduire le statut d'avancement."""
    total = 0
    current = 0
    eta = ""
    last_title = ""

    # 1. Rerank LLM : "  14/50 [ 68] BON Titre de l'offre... [restant: ~2m10s]" ou "  15/460 [ERR] ..."
    p_prog = re.compile(
        r"^\s*(\d+)/(\d+)\s+\[(?:\s*(\d+)|ERR)\]\s+(?:(\w+)\s+)?(.*?)(?:\s+\[restant:\s*(~[^\]]+)\])?$"
    )
    # 2. Backfill : " linkedin 12/80 : 1420 caractères (enregistrée) — Titre de l'offre"
    p_backfill = re.compile(
        r"^\s*(?:linkedin|jobteaser|\w+)\s+(\d+)/(\d+)\s*:\s*(?:.*?)(?:—\s*(.*?))?$"
    )
    # 3. Chercher aussi le total annoncé : "workers pour 50 offre(s) à évaluer" ou "Reranking LLM : 50 offre(s)"
    p_total = re.compile(r"pour\s+(\d+)\s+offre\(s\)\s+à\s+évaluer|Reranking LLM\s*:\s*(\d+)\s+offre\(s\)")

    for line in reversed(lines):
        m_prog = p_prog.match(line)
        if m_prog:
            current = int(m_prog.group(1))
            total = int(m_prog.group(2))
            last_title = (m_prog.group(5) or "").strip()
            if m_prog.group(6):
                eta = m_prog.group(6)
            break

        m_back = p_backfill.match(line)
        if m_back:
            current = int(m_back.group(1))
            total = int(m_back.group(2))
            last_title = (m_back.group(3) or "").strip()
            break

    if total == 0:
        for line in reversed(lines):
            m_tot = p_total.search(line)
            if m_tot:
                total = int(m_tot.group(1) or m_tot.group(2))
                break

    if total > 0:
        percent = min(100, int((current / total) * 100))
        return {
            "current": current,
            "total": total,
            "percent": percent,
            "eta": eta,
            "last_title": last_title,
        }
    return None


def stop_active_task() -> tuple[bool, str]:
    """Arrête immédiatement la tâche en cours."""
    state = get_active_task()
    if not state or state.get("status") != "running":
        return False, "Aucune tâche active à arrêter."

    key = str(state.get("key", ""))
    pid = int(state.get("pid", 0))

    logger.warning("Demande d'arrêt utilisateur pour la tâche %s (PID %d)", state.get("name"), pid)

    # 1. Terminer le processus via taskkill sous Windows ou SIGTERM
    if pid > 0:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, timeout=5)
            except Exception as exc:
                logger.warning("Erreur taskkill : %s", exc)
        else:
            try:
                os.kill(pid, 9)
            except Exception:
                pass

    process = _RUNNING_PROCESSES.pop(key, None)
    if process:
        try:
            process.terminate()
        except Exception:
            pass

    state["status"] = "stopped"
    state["finished_at"] = datetime.now(timezone.utc).isoformat()
    try:
        ACTIVE_TASK_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    bump_data_version()
    return True, "Traitement arrêté avec succès."


def clear_active_task() -> None:
    """Efface l'historique de la dernière tâche."""
    if ACTIVE_TASK_FILE.exists():
        try:
            ACTIVE_TASK_FILE.unlink()
        except Exception:
            pass
    bump_data_version()


def render_task_monitor(target_key: str | None = None) -> bool:
    """Affiche le moniteur de tâche Streamlit interactif.

    Retourne True si une tâche est actuellement en cours (active).
    """
    task = get_active_task()
    if not task:
        return False

    # Si target_key est spécifiée et ne correspond pas à la tâche en cours
    if target_key and task.get("key") != target_key:
        return False

    status = task.get("status", "idle")
    name = task.get("name", "Tâche d'arrière-plan")
    logs = task.get("logs", [])
    logs_text = "\n".join(logs) if logs else "(En attente des premières sorties...)"
    progress = task.get("progress")

    if status == "running":
        st.markdown(f"### ⚡ {name} (en cours)")
        if progress:
            st.progress(
                progress["percent"] / 100.0,
                text=f"Avancement : {progress['current']} / {progress['total']} offres ({progress['percent']}%)",
            )
            c1, c2, c3 = st.columns(3)
            with c1:
                st.metric("Offres traitées", f"{progress['current']} / {progress['total']}")
            with c2:
                st.metric("Temps restant estimé", progress.get("eta") or "calcul en cours…")
            with c3:
                st.metric("Dernière offre évaluée", (progress.get("last_title") or "-")[:25])
        else:
            st.info("Traitement en cours... Vous pouvez naviguer sur d'autres pages sans interrompre le calcul.")

        with st.expander("Voir le journal en temps réel", expanded=True):
            st.code(logs_text[-4000:], language="text")

        c_stop, c_refresh = st.columns([1, 2])
        with c_stop:
            if st.button("⏹️ Arrêter le traitement", type="secondary", key=f"btn_stop_{target_key or 'task'}"):
                stop_active_task()
                st.rerun()
        with c_refresh:
            auto_refresh = st.checkbox(
                "Actualisation automatique du journal",
                value=True,
                key=f"chk_refresh_{target_key or 'task'}",
            )

        if auto_refresh:
            time.sleep(2)
            st.rerun()

        return True

    elif status == "completed":
        st.success(f"✅ **{name}** s'est achevé avec succès !")
        with st.expander("Voir le journal complet de l'exécution", expanded=False):
            st.code(logs_text[-4000:], language="text")

        if st.button("Fermer la notification", type="primary", key=f"btn_dismiss_{target_key or 'task'}"):
            clear_active_task()
            st.rerun()
        return False

    elif status == "failed":
        st.error(f"❌ **{name}** s'est arrêté avec une erreur (code {task.get('return_code')}).")
        with st.expander("Détails du journal d'erreur", expanded=True):
            st.code(logs_text[-3000:], language="text")

        if st.button("Fermer l'alerte", key=f"btn_dismiss_{target_key or 'task'}"):
            clear_active_task()
            st.rerun()
        return False

    elif status == "stopped":
        st.warning(f"⚠️ **{name}** a été interrompu par l'utilisateur.")
        with st.expander("Dernières lignes du journal", expanded=False):
            st.code(logs_text[-2000:], language="text")

        if st.button("Fermer", key=f"btn_dismiss_{target_key or 'task'}"):
            clear_active_task()
            st.rerun()
        return False

    return False


def render_sidebar_task_badge() -> None:
    """Affiche un badge d'avancement discret dans la barre latérale si une tâche tourne."""
    task = get_active_task()
    if not task or task.get("status") != "running":
        return

    progress = task.get("progress")
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**⚡ {task.get('name', 'Tâche')} en cours**")
    if progress:
        st.sidebar.progress(progress["percent"] / 100.0)
        st.sidebar.caption(f"{progress['current']}/{progress['total']} offres {progress.get('eta', '')}")
    else:
        st.sidebar.caption("Calcul en cours en arrière-plan…")
