"""Stop the hosted live-questions server when it's idle or has run enough today.

Run every minute by systemd (live-watchdog.timer), as root so it can power
off; the instance is set to *stop* (not terminate) on shutdown. Each tick:

  - adds the tick to today's runtime and publishes it to SSM ($PARAM_PREFIX/runtime),
    which the wake Lambda reads to refuse wakes once the day's allowance is used;
  - stops the server when no question has started for IDLE_S (counting from
    when it became ready), when today's runtime reaches MAX_RUNTIME_S, or when
    startup never finished within STARTUP_GRACE_S.

Before stopping it withdraws the tunnel URL, so the website never offers a
server that's going away.
"""

from __future__ import annotations

import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

STATE_DIR = Path("/var/lib/live")
TICK_S = 60
IDLE_S = 20 * 60
MAX_RUNTIME_S = 3 * 60 * 60
STARTUP_GRACE_S = 45 * 60


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


def _ready_at() -> float:
    try:
        return float((STATE_DIR / "ready-at").read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0.0


def decide(
    now: float, boot: float, ready_at: float, last_question: float, runtime_today: int
) -> str | None:
    """Why to stop now, or None to keep running. Pure, for tests."""
    if runtime_today >= MAX_RUNTIME_S:
        return "daily runtime allowance used"
    if ready_at < boot:  # not ready since this boot
        return "startup did not finish" if now - boot > STARTUP_GRACE_S else None
    if now - max(ready_at, last_question) > IDLE_S:
        return "idle"
    return None


def add_runtime(today: str) -> int:
    """Add one tick to today's runtime on disk; return the new total in seconds."""
    path = STATE_DIR / f"runtime-{today}"
    try:
        total = int(path.read_text().strip()) + TICK_S
    except (FileNotFoundError, ValueError):
        total = TICK_S
    path.write_text(str(total))
    return total


def main() -> int:
    import boto3

    region, prefix = os.environ["AWS_REGION"], os.environ["PARAM_PREFIX"].rstrip("/")
    ssm = boto3.client("ssm", region_name=region)

    def put(name: str, value: str) -> None:
        ssm.put_parameter(Name=f"{prefix}/{name}", Value=value, Type="String", Overwrite=True)

    now = time.time()
    boot = now - float(Path("/proc/uptime").read_text().split()[0])
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    runtime = add_runtime(today)
    put("runtime", f"{today} {runtime}")

    reason = decide(now, boot, _ready_at(), _mtime(STATE_DIR / "last-question"), runtime)
    if reason is None:
        return 0
    print(f"stopping: {reason}")
    put("url", "-")
    put("stage", f"stopped ({reason})")
    subprocess.run(["systemctl", "poweroff"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
