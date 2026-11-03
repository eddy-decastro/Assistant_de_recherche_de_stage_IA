"""Lanceur unifié du serveur FastAPI (port 8000) et du frontend React Next.js (port 3000).

Usage :
    python run_web.py
    python run_web.py --api-only
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
WEB_DIR = PROJECT_ROOT / "web"


def main() -> int:
    parser = argparse.ArgumentParser(description="Lance Stage Copilot (FastAPI + Next.js React).")
    parser.add_argument("--api-only", action="store_true", help="Lance uniquement le backend FastAPI sur le port 8000.")
    parser.add_argument("--api-port", type=int, default=8000, help="Port du backend FastAPI (défaut: 8000).")
    parser.add_argument("--web-port", type=int, default=3000, help="Port du frontend Next.js (défaut: 3000).")
    args = parser.parse_args()

    api_cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(args.api_port),
        "--reload",
    ]

    if args.api_only:
        print(f"[Stage Copilot] Démarrage de FastAPI sur http://127.0.0.1:{args.api_port} ...")
        return subprocess.call(api_cmd, cwd=str(PROJECT_ROOT))

    npm_bin = shutil.which("npm")
    if not npm_bin:
        print("[Attention] npm introuvable sur le PATH. Démarrage de FastAPI seul.")
        return subprocess.call(api_cmd, cwd=str(PROJECT_ROOT))

    if not (WEB_DIR / "node_modules").exists():
        print("[Stage Copilot] Installation des dépendances Next.js dans web/ ...")
        subprocess.run([npm_bin, "install"], cwd=str(WEB_DIR), check=True)

    print(f"[Stage Copilot] Démarrage de FastAPI sur http://127.0.0.1:{args.api_port} ...")
    api_proc = subprocess.Popen(api_cmd, cwd=str(PROJECT_ROOT))
    try:
        time.sleep(1.0)
        print(f"[Stage Copilot] Démarrage de Next.js sur http://localhost:{args.web_port} ...")
        web_cmd = [npm_bin, "run", "dev", "--", "-p", str(args.web_port)]
        return subprocess.call(web_cmd, cwd=str(WEB_DIR))
    except KeyboardInterrupt:
        return 0
    finally:
        api_proc.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
