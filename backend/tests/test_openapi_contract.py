from pathlib import Path

from fastapi.testclient import TestClient

from ranker import openapi_export
from ranker.main import create_app


def test_committed_openapi_is_fresh() -> None:
    assert openapi_export.main(["--check"]) == 0


def test_check_fails_on_stale_copy(tmp_path: Path) -> None:
    stale = tmp_path / "openapi.json"
    stale.write_text(openapi_export.render().replace("TopFit API", "Stale API"))
    assert openapi_export.main(["--check"], path=stale) == 1
    assert openapi_export.main([], path=stale) == 0
    assert openapi_export.main(["--check"], path=stale) == 0


def test_validation_errors_use_error_response() -> None:
    schema = create_app().openapi()
    ref = schema["paths"]["/api/jobs/search"]["get"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"]
    assert ref == {"$ref": "#/components/schemas/ErrorResponse"}
    assert "HTTPValidationError" not in schema["components"]["schemas"]


def test_admin_declares_error_statuses() -> None:
    schema = create_app().openapi()
    responses = schema["paths"]["/api/admin/ingest/{source}"]["post"]["responses"]
    for status in ("401", "404", "409"):
        assert status in responses


def test_readyz_declares_503() -> None:
    schema = create_app().openapi()
    assert "503" in schema["paths"]["/api/readyz"]["get"]["responses"]


def test_scalar_docs_pinned(client: TestClient) -> None:
    r = client.get("/api/docs")
    assert r.status_code == 200
    assert "@scalar/api-reference@1.82.1" in r.text
    assert client.get("/docs").status_code == 404
