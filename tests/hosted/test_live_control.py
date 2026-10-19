"""The wake/status Lambda: limits, bot check and kill switch before the server starts."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "terraform" / "lambda"))

import live_control

TODAY = datetime.now(UTC).strftime("%Y-%m-%d")
PREFIX = "/live"
CFG = {"instance_id": "i-1", "prefix": PREFIX, "max_wakes": 6, "max_runtime_s": 10800}


class FakeSSM:
    class exceptions:
        class ParameterNotFound(Exception):
            pass

    def __init__(self, **values):
        self.values = {f"{PREFIX}/{k.replace('_', '-')}": v for k, v in values.items()}

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self.values:
            raise self.exceptions.ParameterNotFound()
        return {"Parameter": {"Value": self.values[Name]}}

    def put_parameter(self, Name, Value, Type, Overwrite):
        self.values[Name] = Value

    def get(self, name):
        return self.values.get(f"{PREFIX}/{name}")


class FakeEC2:
    def __init__(self, state="stopped"):
        self.state = state
        self.started = []

    def describe_instances(self, InstanceIds):
        return {"Reservations": [{"Instances": [{"State": {"Name": self.state}}]}]}

    def start_instances(self, InstanceIds):
        self.started += InstanceIds
        self.state = "pending"


def ssm(**extra):
    base = {"switch": "on", "turnstile_site_key": "site", "turnstile_secret": "sec"}
    return FakeSSM(**{**base, **extra})


def ok(secret, token, ip):
    return secret == "sec" and token == "tok"


def test_status_while_asleep():
    s = live_control.status(FakeEC2(), ssm(), CFG)
    assert s["state"] == "stopped" and not s["ready"] and s["url"] is None
    assert s["can_wake"] and s["wakes_left"] == 6 and s["turnstile_site_key"] == "site"


def test_status_only_shares_the_url_once_ready():
    store = ssm(url="https://x.trycloudflare.com", stage="starting services")
    assert live_control.status(FakeEC2("running"), store, CFG)["url"] is None
    store.values[f"{PREFIX}/stage"] = "ready"
    s = live_control.status(FakeEC2("running"), store, CFG)
    assert s["ready"] and s["url"] == "https://x.trycloudflare.com"


def test_wake_starts_the_instance_and_counts_the_wake():
    ec2, store = FakeEC2(), ssm(wakes=f"{TODAY} 2", url="https://old")
    code, _ = live_control.wake(ec2, store, CFG, "tok", "1.1.1.1", verify=ok)
    assert code == 202 and ec2.started == ["i-1"]
    assert store.get("wakes") == f"{TODAY} 3"
    assert store.get("url") == "-" and store.get("stage") == "booting"  # no stale address


def test_yesterdays_counts_reset():
    ec2, store = FakeEC2(), ssm(wakes="2000-01-01 6", runtime="2000-01-01 99999")
    assert live_control.wake(ec2, store, CFG, "tok", "ip", verify=ok)[0] == 202


@pytest.mark.parametrize(
    ("extra", "state", "code", "reason"),
    [
        ({"switch": "off"}, "stopped", 503, "off"),
        ({"wakes": f"{TODAY} 6"}, "stopped", 429, "daily_limit"),
        ({"runtime": f"{TODAY} 10800"}, "stopped", 429, "daily_limit"),
        ({}, "stopping", 409, "busy"),
    ],
)
def test_wake_refusals_never_start_anything(extra, state, code, reason):
    ec2 = FakeEC2(state)
    got_code, body = live_control.wake(ec2, ssm(**extra), CFG, "tok", "ip", verify=ok)
    assert (got_code, body["code"]) == (code, reason) and ec2.started == []


def test_failed_bot_check_never_starts_anything():
    ec2 = FakeEC2()
    code, body = live_control.wake(ec2, ssm(), CFG, "wrong", "ip", verify=ok)
    assert code == 403 and body["code"] == "bot_check" and ec2.started == []


def test_already_running_is_not_a_new_wake():
    ec2, store = FakeEC2("running"), ssm(wakes=f"{TODAY} 1")
    code, _ = live_control.wake(ec2, store, CFG, "tok", "ip", verify=ok)
    assert code == 200 and ec2.started == [] and store.get("wakes") == f"{TODAY} 1"


def test_handler_routes(monkeypatch):
    monkeypatch.setenv("INSTANCE_ID", "i-1")
    monkeypatch.setenv("PARAM_PREFIX", PREFIX)
    monkeypatch.setattr(live_control, "_clients", {"ec2": FakeEC2(), "ssm": ssm()})
    get = {"requestContext": {"http": {"method": "GET"}}, "rawPath": "/status"}
    assert live_control.handler(get, None)["statusCode"] == 200
    nope = {"requestContext": {"http": {"method": "DELETE"}}, "rawPath": "/status"}
    assert live_control.handler(nope, None)["statusCode"] == 404
