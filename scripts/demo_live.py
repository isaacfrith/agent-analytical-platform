"""Serve energy demo live mode from this laptop through a free Cloudflare quick tunnel.

Starts the live API (src/api/server.py) on localhost, opens a quick tunnel
(new random *.trycloudflare.com URL every run), and prints the share link:
the published site with ?live=<tunnel>. Only people with that link (and
the passcode) can ask live questions. Ctrl-C stops both.

Needs: `make up` (Trino, Valkey), AWS creds, DEMO_PASSCODE in .env, and
cloudflared (`brew install cloudflared`). --local skips the tunnel.

Usage: uv run python scripts/demo_live.py [--local]   (or `make demo-live`)
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config import Config
from repo_config import REPO_ROOT

TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--local", action="store_true", help="no tunnel; test against `make demo-serve`"
    )
    args = parser.parse_args()

    config = Config.from_env()
    if not config.demo.passcode:
        print("DEMO_PASSCODE is not set in .env — live mode needs one (see .env.example).")
        return 1
    if not args.local and shutil.which("cloudflared") is None:
        print(
            "cloudflared not found. Install it with `brew install cloudflared`, or run with --local."
        )
        return 1

    port = config.demo.port
    procs: list[subprocess.Popen] = []

    def stop(*_):
        for p in procs:
            p.terminate()
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()

    signal.signal(signal.SIGTERM, lambda *_: (stop(), sys.exit(0)))
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    procs.append(
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "api.server:build_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=REPO_ROOT,
            env=env,
        )
    )
    try:
        if not wait_for(f"http://127.0.0.1:{port}/api/health", STARTUP_TIMEOUT_S):
            print("Live API did not come up — is Valkey running (`make up`)?")
            return 1
        print(f"[PASS] live API on http://127.0.0.1:{port} (cap {config.demo.daily_cap}/day)")

        if args.local:
            print(
                f"\nLocal test link (with `make demo-serve`):\n  http://localhost:8765/?live={quote(f'http://127.0.0.1:{port}', safe='')}"
            )
        else:
            tunnel = subprocess.Popen(
                ["cloudflared", "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )
            procs.append(tunnel)
            public = None
            deadline = time.monotonic() + STARTUP_TIMEOUT_S
            for line in tunnel.stderr:
                if m := TUNNEL_URL.search(line):
                    public = m.group(0)
                    break
                if time.monotonic() > deadline:
                    break
            if public is None or not wait_for(f"{public}/api/health", STARTUP_TIMEOUT_S):
                print("Tunnel did not come up.")
                return 1
            print(f"[PASS] tunnel {public}")
            print(
                f"\nShare link (with the passcode):\n  {config.demo.site_url}?live={quote(public, safe='')}"
            )
        print("\nCtrl-C to stop. Live mode goes offline with this process.")
        while all(p.poll() is None for p in procs):
            time.sleep(1)
        print("A live-mode process exited.")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        stop()


if __name__ == "__main__":
    sys.exit(main())
