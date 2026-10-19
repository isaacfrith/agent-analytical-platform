"""The hosted server's watchdog: when to stop, and how runtime adds up."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "hosted"))

import live_watchdog as watchdog

BOOT = 1_000_000.0
MIN = 60


def decide(minutes_since_boot, ready_after=None, question_after=None, runtime_min=0):
    now = BOOT + minutes_since_boot * MIN
    ready_at = BOOT + ready_after * MIN if ready_after is not None else BOOT - 999
    last_q = BOOT + question_after * MIN if question_after is not None else 0
    return watchdog.decide(now, BOOT, ready_at, last_q, runtime_min * MIN)


def test_keeps_running_while_starting_up():
    assert decide(30) is None


def test_stops_if_startup_never_finishes():
    assert decide(46) == "startup did not finish"


def test_idle_counts_from_ready_then_from_the_last_question():
    assert decide(25, ready_after=10) is None  # 15 idle minutes
    assert decide(31, ready_after=10) == "idle"  # 21
    assert decide(31, ready_after=10, question_after=20) is None  # 11 since the question


def test_daily_runtime_allowance_wins():
    assert decide(15, ready_after=10, question_after=14, runtime_min=180) == (
        "daily runtime allowance used"
    )


def test_runtime_adds_one_tick_per_run(tmp_path, monkeypatch):
    monkeypatch.setattr(watchdog, "STATE_DIR", tmp_path)
    assert watchdog.add_runtime("2026-10-20") == 60
    assert watchdog.add_runtime("2026-10-20") == 120
    assert watchdog.add_runtime("2026-10-21") == 60  # a new day starts again
