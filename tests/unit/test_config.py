import os

import boto3.session
import pytest

from pitadvisor.config import Settings, boto_session, get_settings


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for name in [n for n in os.environ if n.startswith("PITADV_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)  # env_file is relative, a dev's real .env would leak in otherwise
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def session_kwargs(monkeypatch):
    seen = {}

    def fake(**kwargs):
        seen.update(kwargs)
        return "session"

    monkeypatch.setattr(boto3.session, "Session", fake)
    return seen


def test_defaults():
    s = Settings()
    assert s.env == "dev"
    assert s.aws_profile == "pitadvisor"
    assert s.aws_region == "ap-southeast-1"
    assert s.account_id == "352445792687"
    assert s.data_bucket == "pit-advisor-data-dev-352445792687"
    assert s.glue_database == "pitadvisor_dev"
    assert s.athena_workgroup == "pitadvisor"
    assert s.budget_name == "pit-advisor-monthly"
    assert s.max_scanned_bytes == 1073741824


def test_env_prefix_overrides(monkeypatch):
    monkeypatch.setenv("PITADV_ENV", "prod")
    monkeypatch.setenv("PITADV_MAX_SCANNED_BYTES", "2048")
    s = Settings()
    assert s.env == "prod"
    assert s.max_scanned_bytes == 2048


def test_unprefixed_env_ignored(monkeypatch):
    monkeypatch.setenv("ENV", "somebody-elses-env")
    monkeypatch.setenv("AWS_PROFILE", "taskbuddy")
    s = Settings()
    assert s.env == "dev"
    assert s.aws_profile == "pitadvisor"


def test_tags(monkeypatch):
    monkeypatch.setenv("PITADV_ENV", "staging")
    tags = Settings().tags
    assert tags == {"project": "pit-advisor", "env": "staging"}
    assert "component" not in tags


def test_settings_are_cached():
    assert get_settings() is get_settings()


def test_cache_ignores_a_later_env_change(monkeypatch):
    first = get_settings()
    monkeypatch.setenv("PITADV_ENV", "prod")
    assert get_settings() is first
    assert get_settings().env == "dev"


def test_boto_session_pins_the_profile(session_kwargs):
    boto_session()
    assert session_kwargs == {"profile_name": "pitadvisor"}


def test_boto_session_explicit_settings(session_kwargs):
    boto_session(Settings(aws_profile="other"))
    assert session_kwargs == {"profile_name": "other"}


def test_boto_session_without_a_profile_falls_through(session_kwargs):
    boto_session(Settings(aws_profile=""))
    assert session_kwargs == {"profile_name": None}


def test_boto_session_ignores_ambient_profile(monkeypatch, session_kwargs):
    monkeypatch.setenv("AWS_PROFILE", "taskbuddy")
    boto_session()
    assert session_kwargs["profile_name"] == "pitadvisor"
