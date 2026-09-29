"""Démonstration isolée du composant job_feed avec des offres factices.

Usage : streamlit run tools/feed_demo.py --server.port 8512
Les statuts sont modifiés en mémoire uniquement (aucune base n'est touchée).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import components.job_feed as feed_module
import components.job_feed.actions as actions_module
from components.job_feed import job_feed

st.set_page_config(page_title="Démo job_feed", layout="wide")
st.header("Démo du composant job_feed", anchor=False)

NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def _job(i: int, **extra):
    base = {
        "id": f"demo-{i}", "title": f"Stage R&D machine learning n°{i}", "company": f"Société {i}",
        "location": "Paris", "url": f"https://example.com/{i}", "source": "linkedin" if i % 2 else "jobteaser",
        "status": "NOUVEAU", "created_at": NOW - timedelta(hours=i), "final_score": 90 - i * 3,
        "rerank_score": 90 - i * 3 if i % 3 else None, "verdict": "EXCELLENT" if i % 3 else None,
        "match_reasons": ["Modélisation avancée"], "red_flags": [], "tech_stack": ["PyTorch", "Jax"],
        "description": "Mission de recherche appliquée. " * 40,
        "sub_scores": {"modeling_depth": 5, "mentorship_team": 4, "engineering_practice": 3, "option_value": 2},
        "reasoning": "Calendrier aligné et encadrement senior.",
    }
    return {**base, **extra}


HOSTILE = '<img src=x onerror="document.title=\'PWNED\'"> & "guillemets"'
JOBS = [
    _job(1), _job(2), _job(3),
    _job(4, title=HOSTILE, company=HOSTILE, description=HOSTILE, url="javascript:document.title='PWNED'"),
    _job(5, url=""),
    _job(6, id='id"avec]guillemets', hard_cap_triggered="Reporting / dashboards BI"),
    _job(7, status="REJETÉ"),
    _job(8, description=None, rerank_score=None, verdict=None, sub_scores=None),
]


def _memory_status(_db, job_id, status):
    """Remplace l'écriture en base : le statut est modifié en mémoire de session."""
    for job in st.session_state["demo_jobs"]:
        if str(job["id"]) == job_id:
            job["status"] = status
    st.session_state["data_version"] = st.session_state.get("data_version", 0) + 1


actions_module._set_status = _memory_status
feed_module.get_database = lambda: None
st.session_state.setdefault("demo_jobs", JOBS)

hide = st.toggle("Masquer les offres traitées", value=True)
grouped = st.toggle("Grouper par plateforme", value=False)
event = job_feed(st.session_state["demo_jobs"], ("PyTorch",), hide_processed=hide, grouped=grouped)
if event:
    st.caption(f"Dernière action : {event}")
