"""Phase 5 memory: Mem0 store over Valkey (user-scoped, cross-session).

Mem0 does extraction (Bedrock LLM), embedding (local FastEmbed), and scoped
vector search (Valkey). The agent reaches it through three @tool functions.

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

import os
import sys

from dotenv import load_dotenv
from mem0 import Memory
from strands import tool
from valkey import Valkey

from repo_config import COLLECTION_NAME, VALKEY_URL, is_memory, store_key

MEMORY_USER_ID = "analyst"  # single-user project; both test runs share this
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


def as_list(response: object) -> list:
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        return response.get("results", [])
    return []


def _tag(memory_id: str) -> str:
    return "mem" if is_memory(memory_id) else "entity"


@tool
def save_user_preference(fact: str) -> str:
    """Save a user preference or definition for future sessions.

    Call when the user states something to remember ("remember...",
    "X means Y...", "I prefer..."). Pass their words through unchanged.
    Returns what the store actually did, verified against Valkey — an
    unverified ADD is reported as a failure, never as success. An empty
    result means nothing stored: retry once, then report failure.
    """
    print(f"[mem] save_user_preference(fact={fact!r})", file=sys.stderr)
    response = get_store().add(
        [{"role": "user", "content": fact}], user_id=MEMORY_USER_ID
    )
    results = [r for r in as_list(response) if isinstance(r, dict)]
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
    client = Valkey.from_url(VALKEY_URL)
    lines = []
    for r in results:
        rid = r.get("id", "")
        landed = bool(rid) and bool(client.exists(store_key(rid)))
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
def search_memory(query: str) -> str:
    """Search the user's saved preferences and definitions.

    Call when the question uses user-relative terms (high-value, preferred,
    my region...). Results are tagged [mem] (real facts with actionable IDs)
    or [entity] (fragment mentions — context only, never pass their IDs to
    forget_preference). States plainly when nothing matches.
    """
    print(f"[mem] search_memory(query={query!r})", file=sys.stderr)
    response = get_store().search(
        query, filters={"user_id": MEMORY_USER_ID}, top_k=5
    )
    lines = []
    for r in as_list(response):
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
def forget_preference(memory_id: str) -> str:
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
    get_store().delete(memory_id)
    client = Valkey.from_url(VALKEY_URL)
    still = bool(client.exists(store_key(memory_id)))
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


def debug_dump() -> None:
    """Print every stored memory for the user ( powers `make memory-check`)."""
    results = as_list(get_store().get_all(filters={"user_id": MEMORY_USER_ID}))
    if not results:
        print("No memories stored.")
        return
    for r in results:
        if isinstance(r, dict):
            rid = r.get("id", "?")
            print(f"- [{_tag(rid)} {rid}] {r.get('memory', r)}")
        else:
            print(f"- {r}")
