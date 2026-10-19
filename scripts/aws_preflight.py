"""Phase 0 preflight: prove the app can see everything Terraform created.

Verifies, as the scoped app user (AWS_PROFILE in .env):
  1. Identity  — the profile resolves and authenticates (STS GetCallerIdentity,
                 which needs no explicit IAM allow).
  2. Bucket    — the S3 bucket exists and both bucket- and object-level
                 permissions work. Distinguishes 404 (wrong name / never
                 created) from 403 (policy ARN mismatch) in the failure message.
  3. Bedrock invoke — a one-token Converse call against BEDROCK_MODEL_ID:
                 proves model access + InvokeModel permission end-to-end.
  4. Bedrock discovery — lists ACTIVE inference profiles, so a bad model ID
                 fails with "here's what you could use" instead of a bare error.

Run from the repo root (python-dotenv finds .env in the cwd):
    uv run python scripts/aws_preflight.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boto3
from botocore.client import BaseClient
from botocore.exceptions import BotoCoreError, ClientError, ProfileNotFound

from repo_config import fail_line, pass_line, require_env

REQUIRED_KEYS: tuple[str, ...] = (
    "AWS_PROFILE",
    "AWS_REGION",
    "AWS_S3_BUCKET",
    "BEDROCK_MODEL_ID",
)


def check_identity(sts: BaseClient) -> bool:
    try:
        response = sts.get_caller_identity()
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        fail_line(
            f"identity: credentials rejected ({code})",
            "keys in ~/.aws/credentials for this profile may be stale — "
            "re-run: aws configure --profile <AWS_PROFILE>",
        )
        return False
    except BotoCoreError as error:
        fail_line(
            f"identity: no usable credentials ({type(error).__name__})",
            f"check the profile has an access key: aws configure --profile {os.environ.get('AWS_PROFILE', '?')}",
        )
        return False

    arn = response["Arn"]
    account = response["Account"]
    pass_line(f"identity: authenticated as {arn} (account {account})")
    return True


def check_bucket(s3: BaseClient, bucket: str) -> bool:
    # head_bucket checks bucket-level access. The 404/403 split tells you
    # which half is broken:
    #   404 -> the bucket name is wrong, or it was never created
    #   403 -> the bucket exists but the policy ARN does not match it
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as error:
        status = error.response["ResponseMetadata"]["HTTPStatusCode"]
        if status == 403:
            fail_line(
                f"bucket: {bucket} exists but access was denied (403)",
                "the S3BucketAccess/S3ObjectAccess statements in "
                "terraform/iam.tf must reference this exact bucket ARN",
            )
        else:
            fail_line(
                f"bucket: {bucket} not found (404)",
                "check AWS_S3_BUCKET in .env, and that terraform apply ran",
            )
        return False
    except BotoCoreError as error:
        fail_line(f"bucket: could not reach S3 ({type(error).__name__})")
        return False

    # list_objects_v2 checks object-level access on the landing/ prefix.
    # An empty bucket is expected at this stage — nothing has been generated.
    try:
        response = s3.list_objects_v2(Bucket=bucket, Prefix="landing/", MaxKeys=1)
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        fail_line(
            f"bucket: {bucket} reachable, but object-level access denied ({code})",
            "check the S3ObjectAccess statement in terraform/iam.tf",
        )
        return False
    except BotoCoreError as error:
        fail_line(f"bucket: could not list objects ({type(error).__name__})")
        return False

    count = len(response.get("Contents", []))
    pass_line(f"bucket: {bucket} reachable; landing/ objects: {count} (0 expected)")
    return True


def check_bedrock_invoke(bedrock_runtime: BaseClient, model_id: str) -> bool:
    # The Converse API is provider-agnostic — one message format for every
    # model on Bedrock — and is what Phase 6's LLM-as-judge will reuse.
    try:
        response = bedrock_runtime.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": "Reply with exactly: OK"}]}],
            inferenceConfig={"maxTokens": 10, "temperature": 0.0},
        )
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        if code == "AccessDeniedException":
            fail_line(
                f"bedrock invoke: access denied for {model_id}",
                "model access may not be enabled in AWS_REGION, or the model ID "
                "is wrong — the discovery check below lists what you can use",
            )
        elif code == "ResourceNotFoundException":
            fail_line(
                f"bedrock invoke: this account cannot use {model_id} yet",
                "for Anthropic models this is usually the use case details form: "
                "Bedrock console -> Model access -> the model -> submit use case "
                "details (propagates in ~15 minutes)",
            )
        elif code == "ValidationException":
            fail_line(
                f"bedrock invoke: {model_id} was rejected as a model ID",
                "some models are only invocable through an inference profile — "
                "the discovery check below lists valid profile IDs",
            )
        else:
            fail_line(f"bedrock invoke: {code} for {model_id}")
        return False
    except BotoCoreError as error:
        fail_line(f"bedrock invoke: could not reach Bedrock ({type(error).__name__})")
        return False

    # Guard against a non-text reply (e.g. stop reason hit before any text).
    text_blocks = [
        block["text"] for block in response["output"]["message"]["content"] if "text" in block
    ]
    if not text_blocks:
        fail_line(
            f"bedrock invoke: {model_id} replied with no text "
            f"(stop reason: {response.get('stopReason')})",
        )
        return False

    pass_line(f"bedrock invoke: {model_id} replied {text_blocks[0]!r}")
    return True


def check_bedrock_discovery(bedrock: BaseClient, model_id: str) -> bool:
    # This check verifies the ListInferenceProfiles permission (the
    # BedrockDiscovery statement). When BEDROCK_MODEL_ID is wrong it also
    # prints every ACTIVE profile, turning a bare failure into a menu.
    # It still PASSes when the ID is absent: the permission is what is being
    # tested — the invoke check above is the functional test of the ID itself.
    try:
        profiles: list[str] = []
        for page in bedrock.get_paginator("list_inference_profiles").paginate():
            for summary in page["inferenceProfileSummaries"]:
                if summary.get("status") == "ACTIVE":
                    profiles.append(summary["inferenceProfileId"])
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        fail_line(f"bedrock discovery: could not list profiles ({code})")
        return False
    except BotoCoreError as error:
        fail_line(f"bedrock discovery: could not reach Bedrock ({type(error).__name__})")
        return False

    profiles.sort()
    if model_id in profiles:
        pass_line(f"bedrock discovery: {len(profiles)} ACTIVE profiles; your ID is among them")
        return True

    pass_line(f"bedrock discovery: {len(profiles)} ACTIVE profiles; your ID is NOT among them")
    print("       available profiles:")
    for profile_id in profiles:
        print(f"         - {profile_id}")
    return True


def main() -> int:
    require_env(*REQUIRED_KEYS)

    aws_profile = os.environ["AWS_PROFILE"]
    aws_region = os.environ["AWS_REGION"]
    bucket = os.environ["AWS_S3_BUCKET"]
    model_id = os.environ["BEDROCK_MODEL_ID"]

    print(f"Preflight — profile: {aws_profile}, region: {aws_region}\n")

    try:
        session = boto3.Session(profile_name=aws_profile, region_name=aws_region)
    except ProfileNotFound:
        fail_line(
            f"identity: profile '{aws_profile}' not found in ~/.aws/credentials",
            f"create it with: aws configure --profile {aws_profile}",
        )
        return 1

    # Everything else depends on working credentials, so stop early if not.
    if not check_identity(session.client("sts")):
        return 1

    results: list[bool] = [True]
    results.append(check_bucket(session.client("s3"), bucket))
    results.append(check_bedrock_invoke(session.client("bedrock-runtime"), model_id))
    results.append(check_bedrock_discovery(session.client("bedrock"), model_id))

    print()
    if all(results):
        print(f"All {len(results)} checks green — Phase 0 AWS side is complete.")
        return 0
    print(f"{sum(results)}/{len(results)} checks passed — fix the [FAIL] lines above.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
