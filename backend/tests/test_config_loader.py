from pathlib import Path

import pytest
from pydantic import BaseModel

from ranker.config_loader import ConfigError, clear_config_cache, config_dir, load_config


class Sample(BaseModel):
    name: str
    n: int


@pytest.fixture(autouse=True)
def _cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    clear_config_cache()


def test_loads_valid_yaml(tmp_path: Path) -> None:
    (tmp_path / "s.yaml").write_text("name: a\nn: 3\n")
    assert load_config("s.yaml", Sample) == Sample(name="a", n=3)


def test_missing_file_raises() -> None:
    with pytest.raises(ConfigError):
        load_config("nope.yaml", Sample)


def test_invalid_content_raises_without_leaking(tmp_path: Path) -> None:
    (tmp_path / "s.yaml").write_text("name: secretvalue\nn: notanint\n")
    with pytest.raises(ConfigError) as ei:
        load_config("s.yaml", Sample)
    assert "secretvalue" not in str(ei.value)


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    (tmp_path / "s.yaml").write_text("a: [unclosed\n")
    with pytest.raises(ConfigError):
        load_config("s.yaml", Sample)


def test_cache_until_cleared(tmp_path: Path) -> None:
    p = tmp_path / "s.yaml"
    p.write_text("name: a\nn: 1\n")
    first = load_config("s.yaml", Sample)
    p.write_text("name: a\nn: 2\n")
    assert load_config("s.yaml", Sample) is first
    clear_config_cache()
    assert load_config("s.yaml", Sample).n == 2


def test_default_dir_is_backend_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONFIG_DIR")
    assert config_dir().name == "config"
