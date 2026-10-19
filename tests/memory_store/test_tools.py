"""The agent's memory tools render store outcomes as the text the model reads."""

from __future__ import annotations

import pytest

from agent.memory import MemoryTools
from memory import MemoryStore

from .fake_backend import FakeBackend


@pytest.fixture
def tools(store: MemoryStore) -> MemoryTools:
    return MemoryTools(store)


def test_search_marks_memories_and_entity_mentions(tools: MemoryTools, backend: FakeBackend):
    tools.save_user_preference("I prefer AUD")
    entity_id = backend.add_entity("AUD")

    out = tools.search_memory("AUD")

    assert "[mem id-1] (score 0.9) I prefer AUD" in out
    assert f"[entity {entity_id}] (score 0.9) AUD" in out


def test_search_says_plainly_when_nothing_matches(tools: MemoryTools):
    assert tools.search_memory("region") == "No saved preferences match."


def test_save_reports_a_stored_write(tools: MemoryTools):
    assert tools.save_user_preference("I prefer AUD") == (
        "Store result:\n- [id-1] ADD: I prefer AUD [stored]"
    )


def test_empty_save_hands_the_agent_the_rival_recovery_path(tools: MemoryTools, backend: FakeBackend):
    backend.extract_nothing = True

    out = tools.save_user_preference("I prefer AUD")

    assert out.startswith("Nothing was stored")
    assert "forget_preference on the rival" in out


def test_unlanded_save_is_reported_as_failure(tools: MemoryTools, backend: FakeBackend):
    backend.ghost_writes = True

    out = tools.save_user_preference("I prefer AUD")

    assert "[NOT FOUND in store]" in out
    assert "did NOT stick" in out


def test_forget_of_an_entity_mention_is_refused(tools: MemoryTools, backend: FakeBackend):
    entity_id = backend.add_entity("AUD")

    assert tools.forget_preference(entity_id).startswith(f"Refused: {entity_id}")


def test_forget_reports_verified_deletion(tools: MemoryTools):
    tools.save_user_preference("I prefer AUD")

    assert tools.forget_preference("id-1") == (
        "Deleted id-1 (verified absent). Tell the user it is forgotten."
    )


def test_forget_reports_a_delete_that_did_not_take(tools: MemoryTools, backend: FakeBackend):
    tools.save_user_preference("I prefer AUD")
    backend.sticky("id-1")

    assert "did NOT take effect" in tools.forget_preference("id-1")


def test_the_agent_gets_all_three_tools(tools: MemoryTools):
    assert [t.tool_name for t in tools.as_list()] == [
        "search_memory",
        "save_user_preference",
        "forget_preference",
    ]
