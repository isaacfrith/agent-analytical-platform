"""Shared repo setup: paths, env, Valkey namespace, wren runner, report lines.

One module owns everything every script re-derives. Import it; don't
re-derive it. Domain logic (Mem0 assembly, catalog schemas, eval oracles)
stays with its owner — this module holds only namespaces, processes, and
startup validation.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dotenv import load_dotenv
from valkey import Valkey

REPO_ROOT = Path(__file__).resolve().parent.parent
WREN_DIR = REPO_ROOT / "wren"

VALKEY_URL = "redis://localhost:6379"
COLLECTION_NAME = "agent_memories"
VALKEY_INDEXES = (COLLECTION_NAME, f"{COLLECTION_NAME}_entities")

WREN_TIMEOUT_S = 300


def load_env() -> None:
    """Populate os.environ from .env (repo root) so AWS_* keys resolve."""
    load_dotenv()


def require_env(*keys: str) -> None:
    """Load .env and fail loud on missing keys. Call first in every main."""
    load_env()
    missing = [key for key in keys if not os.environ.get(key)]
    if missing:
        raise RuntimeError(f"missing in .env: {', '.join(missing)}")


def store_key(memory_id: str) -> str:
    """Valkey hash key for one stored memory."""
    return f"mem0:{COLLECTION_NAME}:{memory_id}"


def is_memory(memory_id: str) -> bool:
    """True iff the ID has a live hash in the memories namespace.

    Entity mentions live under mem0:{collection}_entities and leak into
    search results via index-prefix overlap — they are context, never
    actionable.
    """
    if not memory_id:
        return False
    return bool(Valkey.from_url(VALKEY_URL).exists(store_key(memory_id)))


def run_wren(
    args: list[str], sql: str | None = None, timeout_s: int = WREN_TIMEOUT_S
) -> tuple[bool, str]:
    """Run one `wren` CLI invocation from the project dir.

    Returns (True, "") on success, (False, output tail) on failure or
    timeout. Pass --sql separately so arg lists stay flag-only.
    """
    cmd = ["uv", "run", "wren", *args]
    if sql is not None:
        cmd += ["--sql", sql]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            cwd=WREN_DIR,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout_s}s: {' '.join(cmd)}"
    if proc.returncode == 0:
        return True, ""
    return False, (proc.stderr or proc.stdout or "")[-1500:]


def pass_line(message: str) -> None:
    print(f"[PASS] {message}")


def fail_line(message: str, hint: str | None = None) -> None:
    print(f"[FAIL] {message}")
    if hint:
        print(f"       hint: {hint}")
