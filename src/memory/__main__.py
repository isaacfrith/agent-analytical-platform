"""Inspect or wipe the Memory store.

Usage:
    uv run python -m memory check   # list Memories, count Entity mentions
    uv run python -m memory wipe    # remove every Memory and Entity mention
"""

from __future__ import annotations

import sys

from config import Config
from memory import build_memory_store


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else ""
    if command not in ("check", "wipe"):
        print(__doc__.strip())
        return 2
    config = Config.from_env()
    store = build_memory_store(config)
    where = f"{config.memory.valkey_url} / {config.memory.collection_name}"
    if command == "wipe":
        print(f"wiped {store.wipe()} record(s) from {where}")
        return 0
    hits = store.contents()
    memories = [h for h in hits if h.is_memory]
    print(f"{where} (user {config.memory.user_id!r})")
    print(f"{len(memories)} Memory(ies), {len(hits) - len(memories)} Entity mention(s)")
    for h in memories:
        print(f"- [{h.id}] {h.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
