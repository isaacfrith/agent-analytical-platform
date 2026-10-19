"""The Memory store's rules, through its interface, over the fake backend."""

from __future__ import annotations

from memory import ForgetStatus, Memory, MemoryStore, SaveStatus

from .fake_backend import FakeBackend


def test_search_tags_memories_and_entity_mentions(store: MemoryStore, backend: FakeBackend):
    saved = store.save("high-value means total_amount over 1000")
    backend.add_entity("high-value")

    hits = store.search("high-value")

    assert {(h.text, h.is_memory) for h in hits} == {
        ("high-value means total_amount over 1000", True),
        ("high-value", False),
    }
    assert saved.writes[0].id in {h.id for h in hits if h.is_memory}


def test_search_with_no_match_is_empty(store: MemoryStore):
    assert store.search("region") == []


def test_save_reports_a_verified_write(store: MemoryStore):
    outcome = store.save("I prefer AUD")

    assert outcome.status is SaveStatus.STORED
    assert [(w.event, w.text, w.landed) for w in outcome.writes] == [("ADD", "I prefer AUD", True)]
    assert store.memories() == [Memory(outcome.writes[0].id, "I prefer AUD")]


def test_save_reports_when_the_extractor_stores_nothing(store: MemoryStore, backend: FakeBackend):
    backend.extract_nothing = True

    outcome = store.save("I prefer AUD")

    assert outcome.status is SaveStatus.NOTHING_EXTRACTED
    assert outcome.writes == ()
    assert store.memories() == []


def test_save_reports_a_write_that_never_landed(store: MemoryStore, backend: FakeBackend):
    backend.ghost_writes = True

    outcome = store.save("I prefer AUD")

    assert outcome.status is SaveStatus.NOT_LANDED
    assert [w.landed for w in outcome.writes] == [False]
    assert store.memories() == []


def test_forget_deletes_a_memory(store: MemoryStore):
    memory_id = store.save("I prefer AUD").writes[0].id

    outcome = store.forget(memory_id)

    assert outcome.status is ForgetStatus.DELETED
    assert store.memories() == []


def test_forget_refuses_an_entity_mention(store: MemoryStore, backend: FakeBackend):
    entity_id = backend.add_entity("AUD")

    assert store.forget(entity_id).status is ForgetStatus.REFUSED
    assert entity_id in backend.entities


def test_forget_refuses_guesses_and_blanks(store: MemoryStore):
    assert store.forget("made-up-id").status is ForgetStatus.REFUSED
    assert store.forget("").status is ForgetStatus.REFUSED


def test_forget_reports_a_delete_that_did_not_take(store: MemoryStore, backend: FakeBackend):
    memory_id = store.save("I prefer AUD").writes[0].id
    backend.sticky(memory_id)

    assert store.forget(memory_id).status is ForgetStatus.STILL_PRESENT
    assert [m.id for m in store.memories()] == [memory_id]


def test_retiring_a_rival_leaves_one_current_memory(store: MemoryStore):
    rival = store.save("high-value means total_amount over 1000").writes[0].id
    store.save("high-value means total_amount over 5000")

    store.forget(rival)

    assert [m.text for m in store.memories()] == ["high-value means total_amount over 5000"]


def test_memories_are_scoped_to_the_user(backend: FakeBackend):
    mine = MemoryStore(backend, user_id="analyst")
    theirs = MemoryStore(backend, user_id="someone-else")
    theirs.save("I prefer USD")

    assert mine.memories() == []
    assert mine.search("prefer") == []


def test_contents_include_entity_mentions_but_memories_do_not(
    store: MemoryStore, backend: FakeBackend
):
    store.save("I prefer AUD")
    backend.add_entity("AUD")

    assert len(store.contents()) == 2
    assert [m.text for m in store.memories()] == ["I prefer AUD"]


def test_wipe_removes_memories_and_entity_mentions(store: MemoryStore, backend: FakeBackend):
    store.save("I prefer AUD")
    backend.add_entity("AUD")

    assert store.wipe() == 2
    assert store.contents() == []
