"""The Memory store: one user's Memories, kept honest.

Two store realities this module defends against (both observed live):
- Entity bleed: Entity mentions surface in search and list results next to
  real Memories. Every Hit says which it is, and forget REFUSES anything
  that is not a live Memory instead of vacuously "verifying" a no-op delete.
- Judge nondeterminism: the extractor occasionally stores nothing for
  identical inputs. Outcomes report that plainly (never as success) so the
  caller can retry.

Every write is verified against the backend after the fact: a save the
extractor claims but the store does not hold is NOT_LANDED, and a forget
whose record survives is STILL_PRESENT.

Rival retirement (one current Memory per term) is driven by the agent via
search → save → forget; the store makes each step observable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from memory.backend import MemoryBackend

SEARCH_TOP_K = 5


@dataclass(frozen=True)
class Hit:
    """One recall result: a Memory, or an Entity mention (context only)."""

    id: str
    text: str
    is_memory: bool
    score: float | None = None


@dataclass(frozen=True)
class Memory:
    id: str
    text: str


@dataclass(frozen=True)
class Write:
    """One record the extractor reported for a save, checked against the store."""

    id: str
    event: str
    text: str
    landed: bool


class SaveStatus(Enum):
    STORED = "stored"  # at least one reported write is in the store
    NOT_LANDED = "not_landed"  # writes reported, none found in the store
    NOTHING_EXTRACTED = "nothing_extracted"  # the extractor stored nothing


@dataclass(frozen=True)
class SaveOutcome:
    status: SaveStatus
    writes: tuple[Write, ...] = ()


class ForgetStatus(Enum):
    DELETED = "deleted"
    REFUSED = "refused"  # not a live Memory: Entity mention, ghost, or guess
    STILL_PRESENT = "still_present"


@dataclass(frozen=True)
class ForgetOutcome:
    status: ForgetStatus
    memory_id: str


class MemoryStore:
    """One user's Memories over a MemoryBackend."""

    def __init__(self, backend: MemoryBackend, user_id: str):
        self._backend = backend
        self._user_id = user_id

    def search(self, query: str, top_k: int = SEARCH_TOP_K) -> list[Hit]:
        return [self._hit(r) for r in self._backend.search(query, self._user_id, top_k)]

    def save(self, text: str) -> SaveOutcome:
        records = self._backend.add(text, self._user_id)
        if not records:
            return SaveOutcome(SaveStatus.NOTHING_EXTRACTED)
        writes = tuple(
            Write(
                id=str(r.get("id") or ""),
                event=str(r.get("event", "?")),
                text=str(r.get("memory", "?")),
                landed=self._backend.exists(str(r.get("id") or "")),
            )
            for r in records
        )
        status = SaveStatus.STORED if any(w.landed for w in writes) else SaveStatus.NOT_LANDED
        return SaveOutcome(status, writes)

    def forget(self, memory_id: str) -> ForgetOutcome:
        if not self._backend.exists(memory_id):
            return ForgetOutcome(ForgetStatus.REFUSED, memory_id)
        self._backend.delete(memory_id)
        if self._backend.exists(memory_id):
            return ForgetOutcome(ForgetStatus.STILL_PRESENT, memory_id)
        return ForgetOutcome(ForgetStatus.DELETED, memory_id)

    def contents(self) -> list[Hit]:
        """Everything recall can surface for this user, tagged."""
        return [self._hit(r) for r in self._backend.list(self._user_id)]

    def memories(self) -> list[Memory]:
        """Live Memories only — the honest oracle for what a save left behind."""
        return [Memory(h.id, h.text) for h in self.contents() if h.is_memory]

    def wipe(self) -> int:
        """Remove every Memory and Entity mention in the store. Destructive."""
        return self._backend.wipe()

    def _hit(self, record: dict) -> Hit:
        record_id = str(record.get("id") or "")
        score = record.get("score")
        return Hit(
            id=record_id,
            text=str(record.get("memory", "?")),
            is_memory=self._backend.exists(record_id),
            score=score if isinstance(score, (int, float)) else None,
        )
