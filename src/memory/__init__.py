"""The Memory store: a user's Memories, distinct from Entity mentions.

Usage:
    store = build_memory_store(Config.from_env())
    store.search("high-value") -> list[Hit]
    store.save("high-value means > 1000") -> SaveOutcome
    store.forget(memory_id) -> ForgetOutcome
    store.memories() -> list[Memory]
"""

from __future__ import annotations

from config import Config
from memory.backend import Mem0ValkeyBackend, MemoryBackend
from memory.store import (
    ForgetOutcome,
    ForgetStatus,
    Hit,
    Memory,
    MemoryStore,
    SaveOutcome,
    SaveStatus,
    Write,
)

__all__ = [
    "ForgetOutcome",
    "ForgetStatus",
    "Hit",
    "Memory",
    "MemoryBackend",
    "MemoryStore",
    "SaveOutcome",
    "SaveStatus",
    "Write",
    "build_memory_store",
]


def build_memory_store(config: Config) -> MemoryStore:
    """The production Memory store for config.memory's user and collection."""
    return MemoryStore(
        Mem0ValkeyBackend(config.memory, config.aws), user_id=config.memory.user_id
    )
