"""Configuration module for the Agent Analytical Platform.

Single source of truth for all configuration: agent budgets, memory settings,
AWS credentials, timeouts, and paths. Loaded from environment with sensible
defaults for local development; all credentials required.

Usage:
    config = Config.from_env()  # Called at startup
    config.agent.turns_budget
    config.memory.valkey_url
    config.aws.bedrock_model_id
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass
class AgentConfig:
    """Agent orchestration budgets and limits."""

    turns_budget: int = 10
    tokens_budget: int = 150_000

    @classmethod
    def from_env(cls) -> AgentConfig:
        """Load from AGENT_* environment variables, use defaults if not set."""
        turns = os.environ.get("AGENT_TURNS_BUDGET")
        tokens = os.environ.get("AGENT_TOKENS_BUDGET")
        return cls(
            turns_budget=int(turns) if turns else 10,
            tokens_budget=int(tokens) if tokens else 150_000,
        )


@dataclass
class MemoryConfig:
    """Memory (Mem0 + Valkey) configuration."""

    valkey_url: str
    collection_name: str
    embedding_dims: int = 1024  # Must match thenlper/gte-large model
    user_id: str = "analyst"  # Single-user design; both test runs share this

    @classmethod
    def from_env(cls) -> MemoryConfig:
        """Load from MEMORY_* environment variables with local dev defaults."""
        valkey_url = os.environ.get(
            "MEMORY_VALKEY_URL", "redis://localhost:6379"
        )
        collection_name = os.environ.get(
            "MEMORY_COLLECTION_NAME", "agent_memories"
        )
        return cls(
            valkey_url=valkey_url,
            collection_name=collection_name,
            embedding_dims=1024,  # Hardcoded: matches FastEmbed model
            user_id="analyst",  # Hardcoded: single-user design
        )


@dataclass
class AwsConfig:
    """AWS credentials and Bedrock configuration."""

    profile: str
    region: str
    bedrock_model_id: str
    s3_bucket: str

    @classmethod
    def from_env(cls) -> AwsConfig:
        """Load from required AWS_* environment variables.

        Raises ValueError if any required var is missing.
        """
        required_keys = ("AWS_PROFILE", "AWS_REGION", "BEDROCK_MODEL_ID", "AWS_S3_BUCKET")
        missing = [key for key in required_keys if not os.environ.get(key)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Set them in .env or in your shell. See .env.example for the template."
            )
        return cls(
            profile=os.environ["AWS_PROFILE"],
            region=os.environ["AWS_REGION"],
            bedrock_model_id=os.environ["BEDROCK_MODEL_ID"],
            s3_bucket=os.environ["AWS_S3_BUCKET"],
        )


@dataclass
class Config:
    """Root configuration object: single point of access for all settings."""

    agent: AgentConfig
    memory: MemoryConfig
    aws: AwsConfig

    @classmethod
    def from_env(cls) -> Config:
        """Load complete configuration from environment.

        Loads .env file first (from repo root), then reads environment variables.
        Fails loud with clear errors if any required credentials are missing.

        Returns:
            Config object with all settings.

        Raises:
            ValueError: If required AWS credentials are missing.
        """
        # Load .env from repo root (same as scripts do)
        load_dotenv()

        return cls(
            agent=AgentConfig.from_env(),
            memory=MemoryConfig.from_env(),
            aws=AwsConfig.from_env(),
        )
