"""Phase 5 memory tools: the agent's adapter over the Memory store.

The store (src/memory) owns the rules: Memory vs Entity mention tagging,
write verification, refusal of non-Memory IDs. This module only turns its
typed outcomes into the text the agent reads — including the recovery
instructions for Rival retirement, which the agent drives via
search → save → conditional forget (mem0's update judge cannot be trusted
with redefinitions; the prompt encodes the rule).

Tool docstrings are the tool descriptions the model sees. Edit with care.
"""

from __future__ import annotations

import sys

from strands import tool

from memory import ForgetStatus, MemoryStore, SaveStatus

_NOTHING_STORED = (
    "Nothing was stored (no new fact extracted). Recovery path: "
    "this often means the fact contradicts an already-stored rival "
    "that the extractor refused to supersede. If your pre-save "
    "search_memory found a rival [mem] ID on this topic, call "
    "forget_preference on the rival, then save this fact again — "
    "with the rival gone the save lands as novel. If no rival "
    "exists, retry the save once; if still empty, tell the user "
    "it did NOT stick."
)


def _log(message: str) -> None:
    print(f"[mem] {message}", file=sys.stderr)


class MemoryTools:
    """The three memory tools the agent gets, over one Memory store.

    Usage:
        tools = MemoryTools(build_memory_store(config))
        agent = Agent(..., tools=[*mcp_tools, *tools.as_list()])
    """

    def __init__(self, store: MemoryStore):
        self.store = store

    def as_list(self) -> list:
        return [self.search_memory, self.save_user_preference, self.forget_preference]

    @tool
    def search_memory(self, query: str) -> str:
        """Search the user's saved preferences and definitions.

        Call when the question uses user-relative terms (high-value, preferred,
        my region...). Results are tagged [mem] (real facts with actionable IDs)
        or [entity] (fragment mentions — context only, never pass their IDs to
        forget_preference). States plainly when nothing matches.
        """
        _log(f"search_memory(query={query!r})")
        hits = self.store.search(query)
        _log(f"search() -> {len(hits)} factor(s)")
        if not hits:
            return "No saved preferences match."
        return "\n".join(
            f"- [{'mem' if h.is_memory else 'entity'} {h.id or '?'}] "
            f"(score {'?' if h.score is None else h.score}) {h.text}"
            for h in hits
        )

    @tool
    def save_user_preference(self, fact: str) -> str:
        """Save a user preference or definition for future sessions.

        Call when the user states something to remember ("remember...",
        "X means Y...", "I prefer..."). Pass their words through unchanged.
        Returns what the store actually did, verified against Valkey — an
        unverified ADD is reported as a failure, never as success. An empty
        result means nothing stored: retry once, then report failure.
        """
        _log(f"save_user_preference(fact={fact!r})")
        outcome = self.store.save(fact)
        _log(f"add() -> {[(w.event, w.text) for w in outcome.writes]}")
        if outcome.status is SaveStatus.NOTHING_EXTRACTED:
            return _NOTHING_STORED
        for w in outcome.writes:
            _log(f"verify {w.id} -> {'exists' if w.landed else 'MISSING'}")
        lines = "\n".join(
            f"- [{w.id or '?'}] {w.event}: {w.text} "
            f"[{'stored' if w.landed else 'NOT FOUND in store'}]"
            for w in outcome.writes
        )
        if outcome.status is SaveStatus.NOT_LANDED:
            return (
                "Store result:\n" + lines + "\nThe write did not land. "
                "Tell the user it did NOT stick."
            )
        return "Store result:\n" + lines

    @tool
    def forget_preference(self, memory_id: str) -> str:
        """Delete a saved preference by ID.

        Call with a [mem]-tagged ID from search_memory output when replacing a
        rival or when the user says to forget something. Non-memory IDs
        ([entity] fragments, ghosts, guesses) are REFUSED — deleting those is
        either a no-op or destroys the wrong thing. Reports verified deletion,
        never silent success.
        """
        _log(f"forget_preference(memory_id={memory_id!r})")
        outcome = self.store.forget(memory_id)
        if outcome.status is ForgetStatus.REFUSED:
            _log(f"refuse {memory_id} (not a stored memory ID)")
            return (
                f"Refused: {memory_id} is not a stored memory ID (entity mention, "
                "ghost, or guess). Only [mem]-tagged IDs from search_memory "
                "output can be forgotten. Tell the user nothing was deleted."
            )
        if outcome.status is ForgetStatus.STILL_PRESENT:
            _log(f"delete {memory_id} -> STILL PRESENT")
            return f"Deletion of {memory_id} did NOT take effect. Tell the user it is still stored."
        _log(f"delete {memory_id} -> gone")
        return f"Deleted {memory_id} (verified absent). Tell the user it is forgotten."
