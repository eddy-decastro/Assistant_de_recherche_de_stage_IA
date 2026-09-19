import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest

from utils.task_manager import (
    _parse_progress,
    clear_active_task,
    get_active_task,
    is_pid_running,
    render_sidebar_task_badge,
    render_task_monitor,
    start_background_task,
    stop_active_task,
)


@pytest.fixture(autouse=True)
def isolated_tasks_dir(tmp_path: Path):
    """Isole les tests de task_manager dans un dossier temporaire."""
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    active_file = tasks_dir / "active_task.json"

    with patch("utils.task_manager.TASKS_DIR", tasks_dir), \
         patch("utils.task_manager.ACTIVE_TASK_FILE", active_file):
        clear_active_task()
        yield tasks_dir
        clear_active_task()


def test_is_pid_running():
    """Vérifie la détection de PID actif et inactif."""
    assert is_pid_running(os.getpid()) is True
    assert is_pid_running(-1) is False
    assert is_pid_running(0) is False
    assert is_pid_running(99999999) is False


def test_parse_progress_rerank():
    """Vérifie le parsing des lignes de rerank LLM."""
    lines = [
        "19:30:00 INFO run_scrapers — Début de l'évaluation",
        "  14/50 [ 68] BON Offre Data Scientist [restant: ~2m10s]",
    ]
    prog = _parse_progress(lines)
    assert prog is not None
    assert prog["current"] == 14
    assert prog["total"] == 50
    assert prog["percent"] == 28
    assert prog["eta"] == "~2m10s"
    assert "Offre Data Scientist" in prog["last_title"]


def test_parse_progress_backfill():
    """Vérifie le parsing des lignes de backfill de descriptions."""
    lines = [
        "19:30:00 INFO backfill — Rattrapage en cours",
        " linkedin 20/40 : 1520 caractères (enregistrée) — Stage ML Engineer",
    ]
    prog = _parse_progress(lines)
    assert prog is not None
    assert prog["current"] == 20
    assert prog["total"] == 40
    assert prog["percent"] == 50
    assert "Stage ML Engineer" in prog["last_title"]


def test_parse_progress_empty_or_no_match():
    """Vérifie le comportement si aucun format de progression n'est présent."""
    assert _parse_progress([]) is None
    assert _parse_progress(["Ligne aléatoire sans progression", "Autre message"]) is None


def test_start_and_complete_background_task():
    """Vérifie le cycle complet de lancement et terminaison d'une tâche rapide."""
    cmd = [sys.executable, "-c", "import time; print('  5/10 [ 75] BON Test [restant: ~30s]', flush=True)"]
    ok, msg = start_background_task("fast_task", "Fast Task", cmd, description="Test description")
    assert ok is True

    # Récupérer l'état pendant ou juste après l'exécution
    time.sleep(0.5)
    task = get_active_task()
    assert task is not None
    assert task["key"] == "fast_task"
    assert task["name"] == "Fast Task"

    # Attendre la fin
    time.sleep(1.0)
    task_finished = get_active_task()
    assert task_finished is not None
    assert task_finished["status"] == "completed"
    assert task_finished["return_code"] == 0
    assert any("5/10" in line for line in task_finished["logs"])


def test_prevent_duplicate_background_task():
    """Vérifie qu'on ne peut pas lancer deux tâches en parallèle."""
    cmd = [sys.executable, "-c", "import time; time.sleep(2)"]
    ok1, _ = start_background_task("t1", "Task 1", cmd)
    assert ok1 is True

    ok2, msg2 = start_background_task("t2", "Task 2", cmd)
    assert ok2 is False
    assert "déjà en cours" in msg2

    stop_active_task()


def test_stop_active_task():
    """Vérifie l'arrêt forcé d'une tâche longue."""
    cmd = [sys.executable, "-c", "import time; time.sleep(10)"]
    ok, _ = start_background_task("long_task", "Long Task", cmd)
    assert ok is True

    time.sleep(0.3)
    task = get_active_task()
    assert task is not None
    assert task["status"] == "running"

    stop_ok, stop_msg = stop_active_task()
    assert stop_ok is True
    assert "arrêté" in stop_msg

    task_after = get_active_task()
    assert task_after is not None
    assert task_after["status"] == "stopped"


def test_clear_active_task():
    """Vérifie la purge du fichier d'état de la tâche."""
    cmd = [sys.executable, "-c", "print('done')"]
    start_background_task("simple", "Simple", cmd)
    time.sleep(0.5)
    assert get_active_task() is not None

    clear_active_task()
    assert get_active_task() is None


def test_render_monitor_and_badge_smoke():
    """Smoke tests pour render_task_monitor et render_sidebar_task_badge sans tâche."""
    clear_active_task()
    # Sans tâche active, render_task_monitor retourne False
    assert render_task_monitor() is False
    # render_sidebar_task_badge ne lève pas d'exception
    render_sidebar_task_badge()
