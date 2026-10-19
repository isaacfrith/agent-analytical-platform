"""In-memory MemoryBackend that can reproduce the store realities seen live.

Scripting knobs:
- add_entity(text): an Entity mention that surfaces in search/list but has
  no live record in the memories namespace.
- extract_nothing: the next add() returns no records.
- ghost_writes: the next add() reports a write that never lands.
- sticky(id): delete() on that ID silently does nothing.
"""

from __future__ import annotations

import itertools
from typing import Any


class FakeBackend:
    def __init__(self) -> None:
        self._ids = (f"id-{n}" for n in itertools.count(1))
        self.memories: dict[str, tuple[str, str]] = {}  # id -> (user_id, text)
        self.entities: dict[str, tuple[str, str]] = {}
        self.extract_nothing = False
        self.ghost_writes = False
        self._sticky: set[str] = set()

    # -- scripting --------------------------------------------------------

    def add_entity(self, text: str, user_id: str = "analyst") -> str:
        entity_id = next(self._ids)
        self.entities[entity_id] = (user_id, text)
        return entity_id

    def sticky(self, record_id: str) -> None:
        self._sticky.add(record_id)

    # -- MemoryBackend ----------------------------------------------------

    def add(self, text: str, user_id: str) -> list[dict[str, Any]]:
        if self.extract_nothing:
            self.extract_nothing = False
            return []
        record_id = next(self._ids)
        if self.ghost_writes:
            self.ghost_writes = False
        else:
            self.memories[record_id] = (user_id, text)
        return [{"id": record_id, "event": "ADD", "memory": text}]

    def search(self, query: str, user_id: str, top_k: int) -> list[dict[str, Any]]:
        words = set(query.lower().split())
        hits = [
            {"id": rid, "memory": text, "score": 0.9}
            for rid, (uid, text) in self._records()
            if uid == user_id and words & set(text.lower().split())
        ]
        return hits[:top_k]

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return [
            {"id": rid, "memory": text}
            for rid, (uid, text) in self._records()
            if uid == user_id
        ]

    def delete(self, record_id: str) -> None:
        if record_id not in self._sticky:
            self.memories.pop(record_id, None)

    def exists(self, record_id: str) -> bool:
        return record_id in self.memories

    def wipe(self) -> int:
        removed = len(self.memories) + len(self.entities)
        self.memories.clear()
        self.entities.clear()
        return removed

    def _records(self):
        return itertools.chain(self.memories.items(), self.entities.items())
