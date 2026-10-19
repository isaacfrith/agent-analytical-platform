"""The seam beneath the Memory store: raw records in, raw records out.

A backend knows nothing about Memories vs Entity mentions, verification, or
refusal — the store owns those rules. It only stores, recalls, and reports
whether an ID has a live record in the memories namespace.

Two adapters: Mem0ValkeyBackend (production) and the scriptable fake in
tests/memory_store/fake_backend.py.

Mem0 config contracts (verified against installed mem0ai 2.2.1):
- vector_store valkey needs exactly: valkey_url, collection_name,
  embedding_model_dims.
- embedding_model_dims MUST equal the FastEmbed model's output (1024 for
  thenlper/gte-large). Change the model later → drop the index or rename the
  collection, or every search silently degrades.
- mem0's Bedrock path uses the standard boto chain: load_dotenv() first
  (Config.from_env does) so AWS_PROFILE/AWS_REGION are in environ.
- Entity mentions live under mem0:{collection}_entities. The memories index
  prefix (mem0:{collection}) overlaps them, so they surface in search and
  get_all results — and so does any collection whose name merely starts
  with ours. Never name a collection with another's name as its prefix.
- Mem0 creates its indexes once, at construction. A wipe must recreate them
  or every later call in the same process fails.
- Every add() feeds the user's last 10 messages from Mem0's SQLite history
  into the extraction prompt. Stale messages make the extractor judge a new
  fact as already known and store nothing — so the history is per collection
  and a wipe clears it too.
- The extractor keeps only facts it reads as the user's own; a bare
  definition with no prior conversation extracts nothing. SAVE_INSTRUCTIONS
  says every save is an explicit request to remember.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path
from typing import Any, Protocol

from valkey import Valkey

from config import AwsConfig, MemoryConfig

LIST_LIMIT = 1000  # mem0's get_all defaults to 20 — far too few for an oracle

# Every add() is the user asking to keep something. Without saying so, the
# extractor drops bare definitions ("X means Y") as not about the user —
# deterministically, whenever there is no prior conversation to imply it.
SAVE_INSTRUCTIONS = (
    "Every new message is the user explicitly asking you to remember it. "
    "Treat definitions (\"X means Y\") and preferences as the user's own and "
    "always extract them as memories."
)


class MemoryBackend(Protocol):
    """Raw record storage, scoped per call by user_id.

    Records are dicts with at least "id" and "memory"; search results add
    "score", add results add "event".
    """

    def add(self, text: str, user_id: str) -> list[dict[str, Any]]: ...

    def search(self, query: str, user_id: str, top_k: int) -> list[dict[str, Any]]: ...

    def list(self, user_id: str) -> list[dict[str, Any]]: ...

    def delete(self, record_id: str) -> None: ...

    def exists(self, record_id: str) -> bool:
        """True iff the ID has a live record in the memories namespace."""
        ...

    def wipe(self) -> int:
        """Remove every record (memories, entity mentions, and the message
        history that conditions extraction); return the record count."""
        ...


def _as_list(response: object) -> list[dict[str, Any]]:
    """Mem0 returns either a list or {"results": [...]}; keep only dicts."""
    if isinstance(response, dict):
        response = response.get("results", [])
    if not isinstance(response, list):
        return []
    return [r for r in response if isinstance(r, dict)]


class Mem0ValkeyBackend:
    """Mem0 (Bedrock extraction, FastEmbed embeddings) over Valkey."""

    def __init__(self, memory: MemoryConfig, aws: AwsConfig):
        self._memory_config = memory
        self._aws_config = aws
        self._collection = memory.collection_name
        self._valkey = Valkey.from_url(memory.valkey_url)

    @cached_property
    def _mem0(self):
        # Deferred: building Mem0 loads the embedding model, which existence
        # checks never need.
        from mem0 import Memory
        from mem0.configs.base import mem0_dir

        return Memory.from_config(
            {
                "history_db_path": str(Path(mem0_dir) / f"history_{self._collection}.db"),
                "custom_instructions": SAVE_INSTRUCTIONS,
                "vector_store": {
                    "provider": "valkey",
                    "config": {
                        "valkey_url": self._memory_config.valkey_url,
                        "collection_name": self._collection,
                        "embedding_model_dims": self._memory_config.embedding_dims,
                    },
                },
                "embedder": {
                    "provider": "fastembed",
                    "config": {"model": "thenlper/gte-large"},
                },
                "llm": {
                    "provider": "aws_bedrock",
                    "config": {
                        "model": self._aws_config.bedrock_model_id,
                        "temperature": 0,
                        "max_tokens": 2000,
                    },
                },
            }
        )

    def add(self, text: str, user_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._mem0.add([{"role": "user", "content": text}], user_id=user_id)
        )

    def search(self, query: str, user_id: str, top_k: int) -> list[dict[str, Any]]:
        return _as_list(
            self._mem0.search(query, filters={"user_id": user_id}, top_k=top_k)
        )

    def list(self, user_id: str) -> list[dict[str, Any]]:
        return _as_list(
            self._mem0.get_all(filters={"user_id": user_id}, top_k=LIST_LIMIT)
        )

    def delete(self, record_id: str) -> None:
        self._mem0.delete(record_id)

    def exists(self, record_id: str) -> bool:
        if not record_id:
            return False
        return bool(self._valkey.exists(f"mem0:{self._collection}:{record_id}"))

    def wipe(self) -> int:
        keys = [
            key
            for pattern in (
                f"mem0:{self._collection}:*",
                f"mem0:{self._collection}_entities:*",
            )
            for key in self._valkey.scan_iter(pattern)
        ]
        removed = self._valkey.delete(*keys) if keys else 0
        # Clears the message history and recreates the memories index; index
        # resets alone leave the keys behind, hence the delete above.
        self._mem0.reset()
        # reset() skips the entity index unless already built; build + reset.
        self._mem0.entity_store.reset()
        return int(removed)
