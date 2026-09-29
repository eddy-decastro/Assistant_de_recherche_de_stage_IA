"""Garde-fous de conventions UI : pas d'emoji ni de use_container_width dans les fichiers migrés."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-⛿✅❌✨⭐️]")

# Fichiers déjà migrés : chaque tâche de la refonte ajoute les siens.
CLEAN_FILES = [
    "app_pages/flux.py",
    "utils/components.py",
    "utils/layout.py",
    "components/job_feed/__init__.py",
    "components/job_feed/feed.js",
    "components/job_feed/feed.html",
    "components/job_feed/feed.css",
]


@pytest.mark.parametrize("relative", CLEAN_FILES)
def test_pas_d_emoji_ni_de_use_container_width(relative: str) -> None:
    text = (ROOT / relative).read_text(encoding="utf-8")
    found = sorted(set(EMOJI.findall(text)))
    assert not found, f"{relative} : emoji décoratifs interdits {found}"
    assert "use_container_width" not in text, f"{relative} : utiliser width=\"stretch\""
