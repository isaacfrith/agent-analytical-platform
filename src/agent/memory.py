"""Phase 5 memory: Mem0 store over Valkey (user-scoped, cross-session).

Mem0 does extraction (Bedrock LLM), embedding (local FastEmbed), and scoped
vector search (Valkey). The agent reaches it through MemoryTools class methods.

Lifecycle ownership (the dedupe lesson): mem0's generic update judge cannot
be trusted with redefinitions, so the agent owns the invariant — one current
value per named definition — via search → save → conditional forget. The
prompt encodes the rule; these tools make each step observable and verified.

Two store realities this module defends against (both observed live):
- Entity bleed: mem0 writes entity mentions under mem0:{collection}_entities,
  and the main index prefix overlaps them, so entities surface in search/list
  results. Every result here is tagged [mem] (real fact, actionable ID) or
  [entity] (fragment mention — context only). forget_preference REFUSES
  non-memory IDs instead of vacuously "verifying" a no-op delete.
- Judge nondeterminism: identical inputs occasionally return empty results.
  Tools report emptiness honestly (never as success) so the agent can retry.

Config contracts (verified against installed mem0ai 2.2.1 + provider docs):
- vector_store valkey needs exactly: valkey_url, collection_name,
  embedding_model_dims.
- embedding_model_dims MUST equal the FastEmbed model's output (1024 for
  thenlper/gte-large). Change the model later → drop the index or rename the
  collection, or every search silently degrades.
- The au.* Bedrock ID auto-resolves its family; no provider_override needed.
- mem0's Bedrock path uses the standard boto chain: load_dotenv() first so
  AWS_PROFILE/AWS_REGION are in environ before the store is built.
"""

from __future__ import annotations

import sys

from mem0 import Memory
from strands import tool
from valkey import Valkey

from config import Config
from repo_config import is_memory, store_key


def _build_store(config: Config) -> Memory:
    """Build the Mem0 store from configuration.

    Args:
        config: Configuration object with memory settings and AWS credentials.

    Returns:
        Initialized Memory instance.
    """
    mem_config = {
        "vector_store": {
            "provider": "valkey",
            "config": {
                "valkey_url": config.memory.valkey_url,
                "collection_name": config.memory.collection_name,
                "embedding_model_dims": config.memory.embedding_dims,
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {"model": "thenlper/gte-large"},
        },
        "llm": {
            "provider": "aws_bedrock",
            "config": {
                "model": config.aws.bedrock_model_id,
                "temperature": 0,
                "max_tokens": 2000,
            },
        },
    }
    return Memory.from_config(mem_config)


def _as_list(response: object) -> list:
    """Convert Mem0 response to list."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        return response.get("results", [])
    return []


def _tag(memory_id: str) -> str:
    """Tag a memory ID as [mem] or [entity]."""
    return "mem" if is_memory(memory_id) else "entity"


class MemoryTools:
    """Mem0-backed memory tools for the agent.

    Encapsulates search, save, and forget operations with verification
    and explicit error handling. Accepts a Memory store at initialization
    to enable dependency injection and testing.

    Usage:
        store = Memory.from_config(...)
        tools = MemoryTools(store)
        agent = Agent(..., tools=[tools.search_memory, tools.save_user_preference, tools.forget_preference])
    """

    def __init__(self, store: Memory, config: Config):
        """Initialize memory tools with a store and configuration.

        Args:
            store: Initialized Mem0 Memory instance.
            config: Configuration object (for user_id).
        """
        self.store = store
        self.config = config
        self.valkey_client = Valkey.from_url(config.memory.valkey_url)

    @tool
    def search_memory(self, query: str) -> str:
        """Search the user's saved preferences and definitions.

        Call when the question uses user-relative terms (high-value, preferred,
        my region...). Results are tagged [mem] (real facts with actionable IDs)
        or [entity] (fragment mentions — context only, never pass their IDs to
        forget_preference). States plainly when nothing matches.
        """
        print(f"[mem] search_memory(query={query!r})", file=sys.stderr)
        response = self.store.search(
            query, filters={"user_id": self.config.memory.user_id}, top_k=5
        )
        lines = []
        for r in _as_list(response):
            if isinstance(r, dict):
                rid = r.get("id", "?")
                lines.append(
                    f"- [{_tag(rid)} {rid}] "
                    f"(score {r.get('score', '?')}) {r.get('memory', '?')}"
                )
            else:
                lines.append(f"- {r}")
        print(f"[mem] search() -> {len(lines)} factor(s)", file=sys.stderr)
        return "\n".join(lines) if lines else "No saved preferences match."

    @tool
    def save_user_preference(self, fact: str) -> str:
        """Save a user preference or definition for future sessions.

        Call when the user states something to remember ("remember...",
        "X means Y...", "I prefer..."). Pass their words through unchanged.
        Returns what the store actually did, verified against Valkey — an
        unverified ADD is reported as a failure, never as success. An empty
        result means nothing stored: retry once, then report failure.
        """
        print(f"[mem] save_user_preference(fact={fact!r})", file=sys.stderr)
        response = self.store.add(
            [{"role": "user", "content": fact}], user_id=self.config.memory.user_id
        )
        results = [r for r in _as_list(response) if isinstance(r, dict)]
        print(
            f"[mem] add() -> {[(r.get('event'), r.get('memory')) for r in results]}",
            file=sys.stderr,
        )
        if not results:
            return (
                "Nothing was stored (no new fact extracted). Recovery path: "
                "this often means the fact contradicts an already-stored rival "
                "that the extractor refused to supersede. If your pre-save "
                "search_memory found a rival [mem] ID on this topic, call "
                "forget_preference on the rival, then save this fact again — "
                "with the rival gone the save lands as novel. If no rival "
                "exists, retry the save once; if still empty, tell the user "
                "it did NOT stick."
            )
        lines = []
        for r in results:
            rid = r.get("id", "")
            landed = bool(rid) and bool(self.valkey_client.exists(store_key(rid)))
            print(
                f"[mem] verify {rid} -> {'exists' if landed else 'MISSING'}",
                file=sys.stderr,
            )
            status = "stored" if landed else "NOT FOUND in store"
            lines.append(
                f"- [{rid or '?'}] {r.get('event', '?')}: "
                f"{r.get('memory', '?')} [{status}]"
            )
        if all("NOT FOUND" in line for line in lines):
            return (
                "Store result:\n" + "\n".join(lines) + "\nThe write did not land. "
                "Tell the user it did NOT stick."
            )
        return "Store result:\n" + "\n".join(lines)

    @tool
    def forget_preference(self, memory_id: str) -> str:
        """Delete a saved preference by ID.

        Call with a [mem]-tagged ID from search_memory output when replacing a
        rival or when the user says to forget something. Non-memory IDs
        ([entity] fragments, ghosts, guesses) are REFUSED — deleting those is
        either a no-op or destroys the wrong thing. Reports verified deletion,
        never silent success.
        """
        print(f"[mem] forget_preference(memory_id={memory_id!r})", file=sys.stderr)
        if not is_memory(memory_id):
            print(f"[mem] refuse {memory_id} (not a stored memory ID)", file=sys.stderr)
            return (
                f"Refused: {memory_id} is not a stored memory ID (entity mention, "
                "ghost, or guess). Only [mem]-tagged IDs from search_memory "
                "output can be forgotten. Tell the user nothing was deleted."
            )
        self.store.delete(memory_id)
        still = bool(self.valkey_client.exists(store_key(memory_id)))
        print(
            f"[mem] delete {memory_id} -> {'STILL PRESENT' if still else 'gone'}",
            file=sys.stderr,
        )
        if still:
            return (
                f"Deletion of {memory_id} did NOT take effect. "
                "Tell the user it is still stored."
            )
        return f"Deleted {memory_id} (verified absent). Tell the user it is forgotten."


# Backward compatibility: module-level functions
def get_memory_tools(config: Config) -> MemoryTools:
    """Factory function to create memory tools (for backward compat)."""
    store = _build_store(config)
    return MemoryTools(store, config)
