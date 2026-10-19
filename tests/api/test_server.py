"""Live mode guards (passcode, single run, daily cap) and the SSE event stream."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from starlette.testclient import TestClient

from api.server import DailyCap, create_app
from config import AgentConfig, AwsConfig, Config, DemoConfig, MemoryConfig

PASS = "open-sesame"


class FakeCounter:
    def __init__(self):
        self.values: dict[str, int] = {}
        self.expiries: dict[str, int] = {}

    def incr(self, key):
        self.values[key] = self.values.get(key, 0) + 1
        return self.values[key]

    def get(self, key):
        return self.values.get(key)

    def expire(self, key, seconds):
        self.expiries[key] = seconds


def make_config(**demo) -> Config:
    return Config(
        agent=AgentConfig(),
        memory=MemoryConfig(valkey_url="redis://unused", collection_name="unused"),
        aws=AwsConfig(profile="p", region="r", bedrock_model_id="m", s3_bucket="b"),
        demo=DemoConfig(passcode=PASS, **demo),
    )


def fake_run(question, on_event):
    on_event({"type": "question", "t": 0, "question": question})
    on_event({"type": "answer", "t": 1, "text": "42", "path": "cube"})


@pytest.fixture
def counter() -> FakeCounter:
    return FakeCounter()


def client_for(counter, run=fake_run, cap=2) -> TestClient:
    return TestClient(create_app(make_config(), DailyCap(counter, cap), run))


def events(resp) -> list[dict]:
    return [json.loads(line[5:]) for line in resp.text.splitlines() if line.startswith("data:")]


def test_refuses_to_start_without_passcode():
    config = make_config()
    config.demo.passcode = None
    with pytest.raises(ValueError, match="DEMO_PASSCODE"):
        create_app(config, DailyCap(FakeCounter(), 1), fake_run)


def test_health_reports_remaining(counter):
    assert client_for(counter).get("/api/health").json() == {
        "ok": True,
        "daily_cap": 2,
        "remaining": 2,
        "busy": False,
    }


def test_ask_streams_events(counter):
    resp = client_for(counter).post("/api/ask", json={"question": "price?", "passcode": PASS})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert [e["type"] for e in events(resp)] == ["question", "answer"]


def test_wrong_passcode_is_rejected_and_not_counted(counter, monkeypatch):
    monkeypatch.setattr("api.server.WRONG_PASSCODE_DELAY_S", 0)
    resp = client_for(counter).post("/api/ask", json={"question": "q", "passcode": "nope"})
    assert resp.status_code == 401
    assert counter.values == {}


def test_daily_cap_stops_runs(counter):
    client = client_for(counter, cap=1)
    assert client.post("/api/ask", json={"question": "a", "passcode": PASS}).status_code == 200
    resp = client.post("/api/ask", json={"question": "b", "passcode": PASS})
    assert resp.status_code == 429 and "used up" in resp.json()["detail"]
    assert client.get("/api/health").json()["remaining"] == 0


def test_question_validation(counter):
    client = client_for(counter)
    assert client.post("/api/ask", json={"question": " ", "passcode": PASS}).status_code == 400
    assert (
        client.post("/api/ask", json={"question": "x" * 501, "passcode": PASS}).status_code == 400
    )
    assert counter.values == {}


def test_runner_failure_becomes_one_error_event(counter):
    def boom(question, on_event):
        on_event({"type": "error", "t": 0, "message": "bedrock down"})
        raise RuntimeError("bedrock down")

    resp = client_for(counter, run=boom).post("/api/ask", json={"question": "q", "passcode": PASS})
    assert [e["type"] for e in events(resp)] == ["error"]


def test_cap_counter_is_per_utc_day_and_expires(counter):
    cap = DailyCap(counter, 5, now=lambda: datetime(2026, 10, 19, 23, 59, tzinfo=UTC))
    cap.take()
    assert counter.values == {"demo:asks:2026-10-19": 1}
    assert counter.expiries == {"demo:asks:2026-10-19": 172800}


def test_cors_preflight_allows_the_site(counter):
    resp = client_for(counter).options(
        "/api/ask",
        headers={
            "Origin": "https://isaacfrith.github.io",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code == 200
