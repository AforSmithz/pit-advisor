from functools import lru_cache
from pathlib import Path

import boto3.session
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PITADV_", env_file=".env", extra="ignore", validate_assignment=False
    )

    env: str = "dev"
    aws_profile: str = "pitadvisor"
    aws_region: str = "ap-southeast-1"
    account_id: str = "352445792687"
    data_bucket: str = ""
    glue_database: str = ""
    athena_workgroup: str = "pitadvisor"
    budget_name: str = "pit-advisor-monthly"
    max_scanned_bytes: int = 1024**3
    ledger_table: str = ""
    fastf1_cache: Path = Path("fastf1_cache")
    # haiku 4.5 is inference-profile only in ap-southeast-1, and the only profile is global
    bedrock_model: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    knowledge_base_id: str = ""
    data_source_id: str = ""
    guardrail_id: str = ""
    guardrail_version: str = "DRAFT"

    @model_validator(mode="after")
    def _derive_names(self) -> "Settings":
        if not self.data_bucket:
            self.data_bucket = f"pit-advisor-data-{self.env}-{self.account_id}"
        if not self.glue_database:
            self.glue_database = f"pitadvisor_{self.env}"
        if not self.ledger_table:
            self.ledger_table = f"pitadvisor-ingest-{self.env}"
        return self

    @property
    def tags(self) -> dict[str, str]:
        return {"project": "pit-advisor", "env": self.env}


@lru_cache
def get_settings() -> Settings:
    return Settings()


def boto_session(settings: Settings | None = None) -> boto3.session.Session:
    resolved = settings or get_settings()
    # profile is always explicit, the ambient AWS_PROFILE points at a different account.
    # region is deliberately not forced here, so doctor can tell us what the profile resolves to
    return boto3.session.Session(profile_name=resolved.aws_profile or None)
