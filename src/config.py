"""Runtime configuration: agent budgets, memory, AWS, and the demo's live mode.

Loaded from the environment (.env) with local-development defaults; AWS
credentials are required. Repo paths and script helpers live in repo_config.

Usage:
    config = Config.from_env()
    config.agent.turns_budget
    config.memory.valkey_url
    config.aws.bedrock_model_id
    config.demo.daily_cap
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

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
        valkey_url = os.environ.get("MEMORY_VALKEY_URL", "redis://localhost:6379")
        collection_name = os.environ.get("MEMORY_COLLECTION_NAME", "agent_memories")
        return cls(
            valkey_url=valkey_url,
            collection_name=collection_name,
            embedding_dims=1024,  # Hardcoded: matches FastEmbed model
            user_id="analyst",  # Hardcoded: single-user design
        )


@dataclass
class AwsConfig:
    """AWS credentials and Bedrock configuration.

    profile is None on the hosted server: boto3 then uses the default chain
    (the instance's IAM role). On a laptop it names an ~/.aws profile.
    """

    profile: str | None
    region: str
    bedrock_model_id: str
    s3_bucket: str

    @classmethod
    def from_env(cls) -> AwsConfig:
        """Load from required AWS_* environment variables.

        Raises ValueError if any required var is missing.
        """
        required_keys = ("AWS_REGION", "BEDROCK_MODEL_ID", "AWS_S3_BUCKET")
        missing = [key for key in required_keys if not os.environ.get(key)]
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}\n"
                "Set them in .env or in your shell. See .env.example for the template."
            )
        return cls(
            profile=os.environ.get("AWS_PROFILE") or None,
            region=os.environ["AWS_REGION"],
            bedrock_model_id=os.environ["BEDROCK_MODEL_ID"],
            s3_bucket=os.environ["AWS_S3_BUCKET"],
        )


@dataclass
class DemoConfig:
    """Live questions for the demo UI (src/api): limits, bot check, kill switch.

    The server refuses to start without a Turnstile secret unless it runs in
    local test mode (scripts/demo_live.py --local).
    """

    daily_cap: int = 20  # live questions per UTC day, across every visitor
    per_visitor_cap: int = 2  # live questions per visitor per UTC day
    turnstile_site_key: str | None = None  # public; the page renders the bot check with it
    turnstile_secret: str | None = None  # private; verifies each question's bot-check token
    owner_cap: int = 30  # owner-pass questions per UTC day, separate from daily_cap
    owner_pass: str | None = None  # private; unlocks owner_cap (off when unset or too short)
    enabled: bool = True  # the kill switch; DEMO_SWITCH_PARAM (SSM) overrides it when set
    switch_param: str | None = None  # SSM parameter name holding "on"/"off"
    allowed_origins: tuple[str, ...] = (
        "https://isaacfrith.github.io",
        "http://localhost:8765",
    )
    port: int = 8787

    @classmethod
    def from_env(cls) -> DemoConfig:
        """Load from DEMO_* environment variables."""
        origins = os.environ.get("DEMO_ALLOWED_ORIGINS")
        return cls(
            daily_cap=int(os.environ.get("DEMO_DAILY_CAP", "20")),
            per_visitor_cap=int(os.environ.get("DEMO_PER_VISITOR_CAP", "2")),
            turnstile_site_key=os.environ.get("DEMO_TURNSTILE_SITE_KEY") or None,
            turnstile_secret=os.environ.get("DEMO_TURNSTILE_SECRET") or None,
            owner_cap=int(os.environ.get("DEMO_OWNER_CAP", "30")),
            owner_pass=os.environ.get("DEMO_OWNER_PASS") or None,
            enabled=os.environ.get("DEMO_ENABLED", "true").lower() not in ("0", "false", "off"),
            switch_param=os.environ.get("DEMO_SWITCH_PARAM") or None,
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else cls.allowed_origins,
            port=int(os.environ.get("DEMO_PORT", "8787")),
        )


@dataclass
class Config:
    """Root configuration object: single point of access for all settings."""

    agent: AgentConfig
    memory: MemoryConfig
    aws: AwsConfig
    demo: DemoConfig = field(default_factory=DemoConfig)

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
            demo=DemoConfig.from_env(),
        )
