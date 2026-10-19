from __future__ import annotations

import pytest

from memory import MemoryStore

from .fake_backend import FakeBackend


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def store(backend: FakeBackend) -> MemoryStore:
    return MemoryStore(backend, user_id="analyst")
