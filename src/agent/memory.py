"""Phase 5 memory: Mem0 store over Valkey (user-scoped, cross-session).

Mem0 does extraction (Bedrock LLM), embedding (local FastEmbed), and scoped
vector search (Valkey). The agent reaches it through two @tool functions only.

Config contracts (verified against installed mem0ai 2.2.1 + provider docs):
- vector_store valkey needs exactly: valkey_url, collection_name,
  embedding_model_dims.
- embedding_model_dims MUST equal the FastEmbed model's output (1024 for
  thenlper/gte-large). Change the model later → drop the index or rename the
  collection, or every search silently degrades.
- The au.* Bedrock ID auto-resolves its family; no provider_override needed.
- mem0's Bedrock path uses the standard boto chain: load_dotenv() first so
  AWS_PROFILE/AWS_REGION are in environ before the store is built.

Honesty rule (learned the hard way): tools report what the store DID.
mem0's add() decision (ADD/UPDATE/...) and the Valkey write are separate
steps, so save_user_preference verifies the key exists before claiming
success — an ADD for a lost write is reported as a failure, never a save.
Both tools also log arguments and outcomes to stderr ([mem] lines) so a
transcript always shows what crossed the tool boundary.

Known store quirk: mem0 also writes entity mentions (e.g. "value orders")
under mem0:{collection}_entities, and the main index prefix overlaps them,
so entities can surface in search/list results alongside real memories.
Harmless at this scale (they score lower than full facts); revisit if a
transcript shows the agent confused by one.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from mem0 import Memory
from strands import tool
from valkey import Valkey

MEMORY_USER_ID = "analyst"  # single-user project; both test runs share this
VALKEY_URL = "redis://localhost:6379"
COLLECTION_NAME = "agent_memories"
EMBEDDING_DIMS = 1024  # thenlper/gte-large output — must match vector_store

_store: Memory | None = None


def build_store() -> Memory:
    """Build the Mem0 store. Env must be loaded first (see module docstring)."""
    load_dotenv()
    config = {
        "vector_store": {
            "provider": "valkey",
            "config": {
                "valkey_url": VALKEY_URL,
                "collection_name": COLLECTION_NAME,
                "embedding_model_dims": EMBEDDING_DIMS,
            },
        },
        "embedder": {
            "provider": "fastembed",
            "config": {"model": "thenlper/gte-large"},
        },
        "llm": {
            "provider": "aws_bedrock",
            "config": {
                "model": os.environ["BEDROCK_MODEL_ID"],
                "temperature": 0,
                "max_tokens": 2000,
            },
        },
    }
    return Memory.from_config(config)


def get_store() -> Memory:
    """Process-wide singleton — one Valkey client + index handle per run."""
    global _store
    if _store is None:
        _store = build_store()
    return _store


def _as_list(response: object) -> list:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        return response.get("results", [])
    return []


@tool
def save_user_preference(fact: str) -> str:
    """Save a user preference or definition for future sessions.

    Call when the user states something to remember ("remember...",
    "X means Y...", "I prefer..."). Pass their words through unchanged.
    Returns what the store actually did, verified against Valkey — an
    unverified ADD is reported as a failure, never as success.
    """
    print(f"[mem] save_user_preference(fact={fact!r})", file=sys.stderr)
    response = get_store().add(
        [{"role": "user", "content": fact}], user_id=MEMORY_USER_ID
    )
    results = [r for r in _as_list(response) if isinstance(r, dict)]
    print(
        f"[mem] add() -> {[(r.get('event'), r.get('memory')) for r in results]}",
        file=sys.stderr,
    )
    if not results:
        return (
            "Nothing was stored (no new fact extracted — it may already be "
            "known). Tell the user nothing changed."
        )
    client = Valkey.from_url(VALKEY_URL)
    lines = []
    for r in results:
        rid = r.get("id", "")
        landed = bool(rid) and bool(
            client.exists(f"mem0:{COLLECTION_NAME}:{rid}")
        )
        print(f"[mem] verify {rid} -> {'exists' if landed else 'MISSING'}",
              file=sys.stderr)
        status = "stored" if landed else "NOT FOUND in store"
        lines.append(f"- {r.get('event', '?')}: {r.get('memory', '?')} [{status}]")
    if all("NOT FOUND" in line for line in lines):
        return (
            "Store result:\n" + "\n".join(lines) + "\nThe write did not land. "
            "Tell the user it did NOT stick."
        )
    return "Store result:\n" + "\n".join(lines)


@tool
def search_memory(query: str) -> str:
    """Search the user's saved preferences and definitions.

    Call when the question uses user-relative terms (high-value, preferred,
    my region...). Returns matching saved facts, or states that none match.
    """
    print(f"[mem] search_memory(query={query!r})", file=sys.stderr)
    response = get_store().search(
        query, filters={"user_id": MEMORY_USER_ID}, top_k=5
    )
    lines = []
    for r in _as_list(response):
        if isinstance(r, dict):
            lines.append(f"- (score {r.get('score', '?')}) {r.get('memory', '?')}")
        else:
            lines.append(f"- {r}")
    print(f"[mem] search() -> {len(lines)} factor(s)", file=sys.stderr)
    return "\n".join(lines) if lines else "No saved preferences match."


def debug_dump() -> None:
    """Print every stored memory for the user ( powers `make memory-check`)."""
    results = _as_list(get_store().get_all(filters={"user_id": MEMORY_USER_ID}))
    if not results:
        print("No memories stored.")
        return
    for r in results:
        if isinstance(r, dict):
            print(f"- [{r.get('id', '?')}] {r.get('memory', r)}")
        else:
            print(f"- {r}")
