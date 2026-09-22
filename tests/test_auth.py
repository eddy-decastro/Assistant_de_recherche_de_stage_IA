"""Tests unitaires du module d'authentification (utils.auth)."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from utils.auth import check_password, is_auth_enabled


def test_auth_disabled_by_default() -> None:
    with patch("src.storage.cloud_storage._load_env"):
        with patch.dict(os.environ, {}, clear=True):
            assert not is_auth_enabled()
            assert check_password("anything") is True


def test_auth_enabled_when_env_set() -> None:
    with patch.dict(os.environ, {"APP_PASSWORD": "SecretPassword123", "TESTING_AUTH": "1"}):
        assert is_auth_enabled()
        assert check_password("SecretPassword123") is True
        assert check_password(" SecretPassword123 ") is True  # strip
        assert check_password("WrongPassword") is False
        assert check_password("") is False
