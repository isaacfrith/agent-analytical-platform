"""Round trip through the real Memory store: Mem0 + Valkey + Bedrock.

Needs `make up` (Valkey) and AWS credentials in .env. Runs against its own
collection, so the agent's Memories are never touched. The collection name
must not start with the real one ("agent_memories") — Valkey index prefixes
would make each collection see the other's records.

    make test-integration
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from mem0.configs.base import mem0_dir

from config import Config
from memory import ForgetStatus, SaveStatus, build_memory_store

pytestmark = pytest.mark.integration

TEST_COLLECTION = "test_agent_memories"


def _history_messages(collection: str) -> int:
    path = Path(mem0_dir) / f"history_{collection}.db"
    with sqlite3.connect(path) as db:
        return db.execute("SELECT COUNT(*) FROM messages").fetchone()[0]


@pytest.fixture(scope="module")
def store():
    config = Config.from_env()
    assert not TEST_COLLECTION.startswith(config.memory.collection_name)
    config = dataclasses.replace(
        config,
        memory=dataclasses.replace(config.memory, collection_name=TEST_COLLECTION),
    )
    store = build_memory_store(config)
    store.wipe()
    yield store
    store.wipe()


def test_save_search_forget_round_trip(store):
    saved = store.save("Remember: high-value orders means total_amount greater than 1000.")
    assert saved.status is SaveStatus.STORED, saved

    memories = store.memories()
    assert any("1000" in m.text.replace(",", "") for m in memories), memories

    hits = store.search("what does high-value mean")
    memory_hits = [h for h in hits if h.is_memory]
    assert memory_hits, hits

    for m in memories:
        assert store.forget(m.id).status is ForgetStatus.DELETED
    assert store.memories() == []

    assert store.forget(memories[0].id).status is ForgetStatus.REFUSED


def test_wipe_leaves_a_working_store(store):
    store.save("I prefer amounts in AUD.")
    store.wipe()

    assert store.memories() == []
    assert store.search("AUD") == []
    assert store.save("I prefer amounts in AUD.").status is SaveStatus.STORED


def test_wipe_clears_the_history_that_conditions_extraction(store):
    """Stale messages made the extractor refuse redefinitions (store empty)."""
    store.save("I prefer amounts in AUD.")
    assert _history_messages(TEST_COLLECTION) > 0

    store.wipe()

    assert _history_messages(TEST_COLLECTION) == 0


def test_a_bare_definition_is_stored_on_a_fresh_store(store):
    """No prior conversation must not make the extractor drop a definition."""
    store.wipe()

    outcome = store.save("high-value orders means total_amount greater than 1000")

    assert outcome.status is SaveStatus.STORED, outcome
