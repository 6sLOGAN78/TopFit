"""Guard: every Settings field and deploy/CI variable is documented in .env.example."""

import re
from pathlib import Path

from ranker.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
VAR_RE = re.compile(r"^#?\s*([A-Z][A-Z0-9_]+)=", re.MULTILINE)
EXTRA = [
    "CONFIG_DIR",
    "POSTGRES_PASSWORD",
    "PUBLIC_HOSTNAME",
    "ACME_EMAIL",
    "GHCR_OWNER",
    "IMAGE_TAG",
    "DEPLOY_SSH_KEY",
    "DEPLOY_KNOWN_HOSTS",
    "EVAL_DATA_REPO",
    "EVAL_DATA_TOKEN",
]


def _names() -> set[str]:
    return set(VAR_RE.findall(ENV_EXAMPLE.read_text()))


def test_every_settings_field_documented() -> None:
    missing = {name.upper() for name in Settings.model_fields} - _names()
    assert not missing


def test_server_and_ci_vars_documented() -> None:
    assert not set(EXTRA) - _names()


def test_no_real_looking_secrets() -> None:
    for line in ENV_EXAMPLE.read_text().splitlines():
        assert "-----BEGIN" not in line
        assert not re.search(r"=\s*[0-9a-fA-F]{32,}", line)
