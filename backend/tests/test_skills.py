import pytest

from ranker.ingest.skills import SkillEntry, SkillMatcher, SkillsConfig, extract_skills, skills_text


def _e(canonical: str, category: str = "language", **kw: object) -> SkillEntry:
    return SkillEntry.model_validate({"canonical": canonical, "category": category, **kw})


CONFIG = SkillsConfig(
    provenance=["test"],
    skills=[
        _e("cpp", aliases=["C++"], token="cplusplus"),
        _e("csharp", aliases=["C#"]),
        _e("dotnet", "framework", aliases=[".NET", "dotnet", ".NET Core"]),
        _e("aspnet", "framework", aliases=["ASP.NET"], implies=["dotnet"]),
        _e(
            "nodejs",
            "framework",
            ambiguous=True,
            aliases=["node"],
            strong_aliases=["Node.js", "NodeJS"],
            context=[r"(?i:npm|express|backend)"],
        ),
        _e(
            "go",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Go"],
            strong_aliases=["Golang"],
            context=[r"\bin Go\b", r"Go (?:developer|engineer|services)"],
            token="golang",
        ),
        _e("java", aliases=["Java"]),
        _e("javascript", aliases=["JavaScript", "JS"]),
        _e("typescript", aliases=["TypeScript"]),
        _e("python", aliases=["Python"]),
        _e("sql", "database", aliases=["SQL"]),
        _e("react", "framework", aliases=["React", "ReactJS"]),
        _e("react-native", "mobile", aliases=["React Native"]),
        _e(
            "spring",
            "framework",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Spring"],
            context=[r"(?i:java|mvc|jpa)"],
        ),
        _e("spring-boot", "framework", aliases=["Spring Boot"]),
        _e("r", ambiguous=True, case_sensitive=True, aliases=["R"]),
        _e("c", ambiguous=True, case_sensitive=True, aliases=["C"]),
        _e(
            "swift",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Swift"],
            context=[r"(?i:ios|xcode|apple|developer)"],
        ),
        _e(
            "rust",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Rust"],
            context=[r"(?i:cargo|tokio|systems)"],
        ),
        _e(
            "express",
            "framework",
            ambiguous=True,
            aliases=["Express"],
            strong_aliases=["Express.js", "ExpressJS"],
            context=[r"(?i:node|middleware)"],
        ),
        _e(
            "excel",
            "tool",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Excel"],
            context=[r"(?i:spreadsheet|pivot|vlookup)"],
        ),
        _e(
            "make",
            "tool",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Make"],
            context=[r"(?i:makefile|cmake)"],
        ),
        _e(
            "unity",
            "platform",
            ambiguous=True,
            case_sensitive=True,
            aliases=["Unity"],
            context=[r"(?i:game|3d)"],
        ),
        _e("kubernetes", "devops", aliases=["Kubernetes", "k8s"]),
        _e("docker", "devops", aliases=["Docker"]),
        _e("aws", "cloud", aliases=["AWS", "Amazon Web Services"]),
        _e("ios", "mobile", aliases=["iOS"]),
    ],
)
M = SkillMatcher(CONFIG)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C++ and C# developer", ["cpp", "csharp"]),
        ("C++, C#", ["cpp", "csharp"]),
        (".NET Core, ASP.NET MVC", ["aspnet", "dotnet"]),
        ("ASP.NET", ["aspnet", "dotnet"]),
        ("We use dotnet daily", ["dotnet"]),
        ("Node.js / NodeJS / node", ["nodejs"]),
        ("Built on Node.js.", ["nodejs"]),
        ("Kubernetes node pool autoscaling", ["kubernetes"]),
        ("Backend on node with npm", ["nodejs"]),
        ("Golang", ["go"]),
        ("We write services in Go.", ["go"]),
        ("Go developer wanted", ["go"]),
        ("Go to market strategy", []),
        ("go-live next week", []),
        ("Go-live next week", []),
        ("JavaScript and TypeScript", ["javascript", "typescript"]),
        ("JavaScript", ["javascript"]),
        ("Java, Spring Boot", ["java", "spring-boot"]),
        ("Spring Boot", ["spring-boot"]),
        ("Spring is lovely this time of year", []),
        ("Spring MVC", ["spring"]),
        ("React Native and React", ["react", "react-native"]),
        ("React Native", ["react-native"]),
        ("Skills: Python, R, SQL", ["python", "r", "sql"]),
        ("Python, R, SQL", ["python", "r", "sql"]),
        ("R&D team", []),
        ("R and D team", []),
        ("Skills: C, C++", ["c", "cpp"]),
        ("Languages: C, C++, Python", ["c", "cpp", "python"]),
        ("Vitamin C is good", []),
        ("Swift developer for iOS", ["ios", "swift"]),
        ("SWIFT payments messaging", []),
        ("Swift payments messaging", []),
        ("Express.js", ["express"]),
        ("Express delivery partner", []),
        ("Express middleware for APIs", ["express"]),
        ("excel at communication", []),
        ("Excel at communication", []),
        ("Advanced Excel with pivot tables", ["excel"]),
        ("Rust and cargo tooling", ["rust"]),
        ("rust on the pipes", []),
        ("Unity game developer", ["unity"]),
        ("Unity of purpose", []),
        ("Make sure you apply", []),
        ("Write a Makefile; Make builds", ["make"]),
        ("python3 scripts", []),
        ("PYTHON", ["python"]),
        ("", []),
    ],
)
def test_extract(text: str, expected: list[str]) -> None:
    assert extract_skills(text, M) == expected


def test_javascript_never_yields_java() -> None:
    assert "java" not in extract_skills("JavaScript JavaScript.", M)


def test_output_sorted_and_deduplicated() -> None:
    out = extract_skills("Python python PYTHON, Docker, AWS, Docker", M)
    assert out == ["aws", "docker", "python"]


def test_skills_text_tokens() -> None:
    assert (
        skills_text(["cpp", "csharp", "dotnet", "go", "nodejs"], M)
        == "cplusplus csharp dotnet golang nodejs"
    )


def test_text_truncated_to_20k() -> None:
    assert extract_skills("x " * 10_001 + "Docker", M) == []
    assert extract_skills("Docker " + "x " * 20_000, M) == ["docker"]


def test_duplicate_canonical_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        SkillsConfig(provenance=["t"], skills=[_e("a", aliases=["A1"]), _e("a", aliases=["A2"])])


def test_alias_conflict_rejected() -> None:
    with pytest.raises(ValueError, match="maps to"):
        SkillsConfig(provenance=["t"], skills=[_e("a", aliases=["X"]), _e("b", aliases=["x"])])
