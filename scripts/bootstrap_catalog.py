"""Phase 1 bootstrap: initialize Lakekeeper and create the S3-backed warehouse.

Two idempotent-tolerant steps against the management API (run `make up` first):
  1. POST /management/v1/bootstrap — one-time per install: accepts the license
     and creates the default project. "Already bootstrapped" counts as success.
  2. POST /management/v1/warehouse — creates the ICEBERG_WAREHOUSE warehouse
     backed by the real S3 bucket, vending STS credentials through the IAM role
     managed in terraform/iam.tf.

The S3 access keys are read from ~/.aws via boto3 and POSTed to Lakekeeper,
which stores them encrypted (LAKEKEEPER__PG_ENCRYPTION_KEY in the compose
file). They are never written to any file in this repo.

Run from the repo root:
    uv run python scripts/bootstrap_catalog.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boto3
import requests
from botocore.credentials import ReadOnlyCredentials

from repo_config import fail_line, pass_line, require_env

REQUIRED_KEYS: tuple[str, ...] = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_S3_BUCKET",
    "LAKEKEEPER_URI",
    "ICEBERG_WAREHOUSE",
    "LAKEKEEPER_STS_ROLE_ARN",
)

TIMEOUT_SECONDS = 30


def load_app_credentials() -> ReadOnlyCredentials | None:
    """Fetch the app user's long-lived keys from ~/.aws — never written to disk."""
    session = boto3.Session(profile_name=os.environ["AWS_PROFILE"])
    credentials = session.get_credentials()
    if credentials is None:
        fail_line(
            "credentials: no credentials for the profile",
            "run: aws configure --profile <AWS_PROFILE>",
        )
        return None
    frozen = credentials.get_frozen_credentials()
    if frozen.token is not None:
        # Temporary credentials cannot serve as the warehouse storage-credential.
        fail_line(
            "credentials: profile resolves to temporary (session) credentials",
            "the storage-credential needs long-lived access keys — use an "
            "access-key profile, not env vars / SSO / assumed-role credentials",
        )
        return None
    return frozen


def bootstrap(base_uri: str) -> bool:
    """One-time-per-install: accept terms, create the default project."""
    url = f"{base_uri}/management/v1/bootstrap"
    try:
        response = requests.post(
            url, json={"accept-terms-of-use": True}, timeout=TIMEOUT_SECONDS
        )
    except requests.RequestException as error:
        fail_line(
            f"bootstrap: could not reach Lakekeeper ({type(error).__name__})",
            "start the stack first: make up",
        )
        return False
    if response.ok:
        pass_line("bootstrap: terms accepted, default project created")
        return True
    if "already" in response.text.lower():
        pass_line("bootstrap: already bootstrapped (skipping)")
        return True
    fail_line(f"bootstrap: HTTP {response.status_code}", response.text[:300])
    return False


def warehouse_already_exists(response: requests.Response) -> bool:
    """Detect the 'warehouse already exists' outcome across Lakekeeper versions.

    A repeat POST does not return 200: current releases answer 400 with error
    type CreateWarehouseStorageProfileOverlap ("storage profile overlaps with
    existing warehouse ..."). Match the typed error, not just the word
    "already", so genuine 400s (bad bucket, bad role ARN) still FAIL loudly.
    """
    if "already" in response.text.lower():
        return True
    try:
        error_type = response.json().get("error", {}).get("type", "")
    except ValueError:
        return False
    return error_type == "CreateWarehouseStorageProfileOverlap"


def create_warehouse(base_uri: str, keys: ReadOnlyCredentials) -> bool:
    """Create the S3-backed warehouse with STS credential vending enabled."""
    url = f"{base_uri}/management/v1/warehouse"
    payload = {
        "warehouse-name": os.environ["ICEBERG_WAREHOUSE"],
        "storage-profile": {
            "type": "s3",
            "bucket": os.environ["AWS_S3_BUCKET"],
            "key-prefix": "warehouse",
            "region": os.environ["AWS_REGION"],
            "sts-enabled": True,
            "sts-role-arn": os.environ["LAKEKEEPER_STS_ROLE_ARN"],
            "flavor": "aws",
        },
        "storage-credential": {
            "type": "s3",
            "credential-type": "access-key",
            "aws-access-key-id": keys.access_key,
            "aws-secret-access-key": keys.secret_key,
        },
    }
    try:
        response = requests.post(url, json=payload, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as error:
        fail_line(f"warehouse: could not reach Lakekeeper ({type(error).__name__})")
        return False
    if response.ok:
        pass_line(f"warehouse: {payload['warehouse-name']} created (S3 + STS vending)")
        return True
    if warehouse_already_exists(response):
        pass_line(f"warehouse: {payload['warehouse-name']} already exists (skipping)")
        return True
    fail_line(f"warehouse: HTTP {response.status_code}", response.text[:300])
    return False


def verify_warehouse(base_uri: str, warehouse_name: str) -> bool:
    """Confirm the warehouse is listed — proves creation persisted."""
    url = f"{base_uri}/management/v1/warehouse"
    try:
        response = requests.get(url, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as error:
        fail_line(f"verify: could not reach Lakekeeper ({type(error).__name__})")
        return False
    if not response.ok:
        fail_line(f"verify: HTTP {response.status_code}", response.text[:300])
        return False
    warehouses = response.json().get("warehouses", [])
    names = [w.get("name") or w.get("warehouse-name") for w in warehouses]
    if warehouse_name in names:
        pass_line(f"verify: {warehouse_name} listed in the management API")
        return True
    fail_line(f"verify: {warehouse_name} not found in {names}")
    return False


def main() -> int:
    require_env(*REQUIRED_KEYS)

    base_uri = os.environ["LAKEKEEPER_URI"].rstrip("/")
    warehouse_name = os.environ["ICEBERG_WAREHOUSE"]

    # Fail early if the credential type is unusable, before touching the API.
    keys = load_app_credentials()
    if keys is None:
        return 1

    results = [
        bootstrap(base_uri),
        create_warehouse(base_uri, keys),
        verify_warehouse(base_uri, warehouse_name),
    ]

    print()
    if all(results):
        print("All checks green — Lakekeeper is ready. UI: http://localhost:8181/ui/")
        return 0
    print(f"{sum(results)}/{len(results)} checks passed — fix the [FAIL] lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
