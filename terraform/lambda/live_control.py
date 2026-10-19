"""Wake and status for the live-questions server (AWS Lambda, function URL).

The website talks to this while the server sleeps:
  GET  /status  -> is the server running and ready, at what tunnel URL, what's left today
  POST /wake    {turnstile_token} -> start the server, after these checks:
                kill switch on, bot check passes, at most MAX_WAKES_PER_DAY wakes and
                MAX_RUNTIME_S of running per UTC day.

State lives in SSM Parameter Store under PARAM_PREFIX. The server writes `url`,
`stage` and `runtime`; this function writes `wakes` and resets `url`/`stage` on
wake. It may only start, and describe, the one instance (see its IAM policy).
SSM can't store empty strings, so "-" means "none".
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
NONE = "-"

_clients: dict = {}


def _client(name: str):
    if name not in _clients:
        import boto3

        _clients[name] = boto3.client(name)
    return _clients[name]


def _settings() -> dict:
    return {
        "instance_id": os.environ["INSTANCE_ID"],
        "prefix": os.environ["PARAM_PREFIX"].rstrip("/"),
        "max_wakes": int(os.environ.get("MAX_WAKES_PER_DAY", "6")),
        "max_runtime_s": int(os.environ.get("MAX_RUNTIME_S", "10800")),
    }


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _param(ssm, prefix: str, name: str, decrypt: bool = False) -> str:
    try:
        resp = ssm.get_parameter(Name=f"{prefix}/{name}", WithDecryption=decrypt)
        return resp["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return NONE


def _put(ssm, prefix: str, name: str, value: str) -> None:
    ssm.put_parameter(Name=f"{prefix}/{name}", Value=value or NONE, Type="String", Overwrite=True)


def _todays(value: str) -> int:
    """'YYYY-MM-DD N' -> N if it's today's count, else 0."""
    day, _, count = value.partition(" ")
    return int(count) if day == _today() and count.isdigit() else 0


def verify_turnstile(secret: str, token: str, ip: str) -> bool:
    if not token:
        return False
    data = urllib.parse.urlencode({"secret": secret, "response": token, "remoteip": ip}).encode()
    try:
        with urllib.request.urlopen(TURNSTILE_VERIFY_URL, data=data, timeout=10) as resp:
            return bool(json.load(resp).get("success"))
    except (OSError, ValueError):
        return False  # fail closed


def status(ec2, ssm, cfg: dict) -> dict:
    inst = ec2.describe_instances(InstanceIds=[cfg["instance_id"]])["Reservations"][0]
    state = inst["Instances"][0]["State"]["Name"]
    p = cfg["prefix"]
    url, stage = _param(ssm, p, "url"), _param(ssm, p, "stage")
    enabled = _param(ssm, p, "switch") == "on"
    wakes_left = max(0, cfg["max_wakes"] - _todays(_param(ssm, p, "wakes")))
    runtime_left = max(0, cfg["max_runtime_s"] - _todays(_param(ssm, p, "runtime")))
    ready = state == "running" and url != NONE and stage == "ready"
    return {
        "state": state,
        "ready": ready,
        "url": url if ready else None,
        "stage": stage if stage != NONE else None,
        "enabled": enabled,
        "can_wake": enabled and state == "stopped" and wakes_left > 0 and runtime_left > 0,
        "wakes_left": wakes_left,
        "runtime_left_s": runtime_left,
        "turnstile_site_key": _param(ssm, p, "turnstile-site-key"),
    }


def wake(ec2, ssm, cfg: dict, token: str, ip: str, verify=verify_turnstile) -> tuple[int, dict]:
    p = cfg["prefix"]
    current = status(ec2, ssm, cfg)
    if not current["enabled"]:
        return 503, {"code": "off", "detail": "Live questions are switched off right now."}
    if not verify(_param(ssm, p, "turnstile-secret", decrypt=True), token, ip):
        return 403, {
            "code": "bot_check",
            "detail": "The bot check didn't pass. Reload and try again.",
        }
    if current["state"] in ("pending", "running"):
        return 200, current  # already awake or waking
    if current["state"] != "stopped":
        return 409, {
            "code": "busy",
            "detail": "The server is shutting down. Try again in a minute.",
        }
    if current["wakes_left"] <= 0 or current["runtime_left_s"] <= 0:
        return 429, {
            "code": "daily_limit",
            "detail": "Live questions have used today's allowance. Come back tomorrow.",
        }
    _put(ssm, p, "url", NONE)
    _put(ssm, p, "stage", "booting")
    _put(ssm, p, "wakes", f"{_today()} {_todays(_param(ssm, p, 'wakes')) + 1}")
    ec2.start_instances(InstanceIds=[cfg["instance_id"]])
    return 202, status(ec2, ssm, cfg)


def _response(code: int, body: dict) -> dict:
    return {
        "statusCode": code,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body),
    }


def handler(event, context):
    cfg = _settings()
    ec2, ssm = _client("ec2"), _client("ssm")
    http = event.get("requestContext", {}).get("http", {})
    method, path = http.get("method", "GET"), event.get("rawPath", "/")
    if method == "GET" and path == "/status":
        return _response(200, status(ec2, ssm, cfg))
    if method == "POST" and path == "/wake":
        try:
            body = json.loads(event.get("body") or "{}")
        except ValueError:
            body = {}
        token = str(body.get("turnstile_token") or "")
        return _response(*wake(ec2, ssm, cfg, token, http.get("sourceIp", "")))
    return _response(404, {"detail": "Not found"})
