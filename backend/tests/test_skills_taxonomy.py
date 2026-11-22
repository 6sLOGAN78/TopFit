from pathlib import Path

import pytest
import yaml

from ranker.config_loader import load_config
from ranker.ingest.skills import SkillsConfig, extract_skills, skills_text

FIXTURE = Path(__file__).parent / "fixtures" / "skills_jd_samples.yaml"


@pytest.fixture(scope="module")
def config() -> SkillsConfig:
    return load_config("skills.yaml", SkillsConfig)


def test_taxonomy_size_and_provenance(config: SkillsConfig) -> None:
    assert len(config.skills) >= 400
    canonicals = [s.canonical for s in config.skills]
    assert len(canonicals) == len(set(canonicals))
    joined = " ".join(config.provenance)
    assert "Linguist" in joined
    assert "MIT" in joined
    assert "O*NET" in joined
    assert "CC BY 4.0" in joined


def test_no_conflicting_licence_sources() -> None:
    raw = (Path(__file__).parents[1] / "config" / "skills.yaml").read_text().lower()
    assert "lightcast" not in raw
    assert "stackoverflow" not in raw
    assert "stack overflow" not in raw


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C++ and C# developer", ["cpp", "csharp"]),
        (".NET Core, ASP.NET MVC", ["aspnet", "dotnet"]),
        ("Node.js / NodeJS", ["nodejs"]),
        ("Golang", ["go"]),
        ("We write services in Go.", ["go"]),
        ("Go to market", []),
        ("JavaScript and TypeScript", ["javascript", "typescript"]),
        ("Java, Spring Boot", ["java", "spring-boot"]),
        ("React Native and React", ["react", "react-native"]),
        ("Skills: Python, R, SQL", ["python", "r", "sql"]),
        ("R&D team", []),
        ("Skills: C, C++", ["c", "cpp"]),
        ("SWIFT payments messaging", []),
        ("Swift developer for iOS", ["ios", "swift"]),
        ("Express.js", ["express"]),
        ("excel at communication", []),
    ],
)
def test_required_cases_on_real_taxonomy(text: str, expected: list[str]) -> None:
    assert extract_skills(text) == expected


def test_skills_text_tokens() -> None:
    assert (
        skills_text(["cpp", "csharp", "dotnet", "go", "nodejs"])
        == "cplusplus csharp dotnet golang nodejs"
    )


def test_precision_recall_on_labelled_jds() -> None:
    samples = yaml.safe_load(FIXTURE.read_text())
    assert len(samples) >= 30
    tp = fp = fn = 0
    problems: list[str] = []
    for s in samples:
        got = set(extract_skills(s["text"]))
        want = set(s["expected"])
        tp += len(got & want)
        fp += len(got - want)
        fn += len(want - got)
        if got != want:
            problems.append(f"{s['text'][:50]!r}: FP={sorted(got - want)} FN={sorted(want - got)}")
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    detail = "\n".join(problems)
    assert precision >= 0.90, f"precision {precision:.3f}\n{detail}"
    assert recall >= 0.80, f"recall {recall:.3f}\n{detail}"
