"""Test live questions on this laptop: the live API, with no tunnel and no bot check.

Starts the live API (src/api/server.py, local mode) on localhost and prints
the link that points the locally served UI at it. The same limits apply as
on the hosted server (2 questions per visitor, 20 a day), counted in Valkey.
Ctrl-C stops it.

Needs: `make up` (Trino, Valkey) and AWS credentials in .env. Serve the UI
with `make demo-serve` in another terminal.

Usage: uv run python scripts/demo_live.py   (or `make demo-live`)
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config
from repo_config import REPO_ROOT

STARTUP_TIMEOUT_S = 60


def wait_for(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    config = Config.from_env()
    port = config.demo.port
    api = f"http://127.0.0.1:{port}"
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "api.server:build_local_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
    )  # fmt: skip
    try:
        if not wait_for(f"{api}/api/health", STARTUP_TIMEOUT_S):
            print("The live API did not come up. Is Valkey running (`make up`)?")
            return 1
        print(f"[PASS] live API on {api} (local mode: no bot check)")
        print(
            f"\nWith `make demo-serve` running, open:\n  http://localhost:8765/?live={quote(api, safe='')}"
        )
        print("\nCtrl-C to stop.")
        server.wait()
        return server.returncode or 0
    except KeyboardInterrupt:
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    sys.exit(main())
