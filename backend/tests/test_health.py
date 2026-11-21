from fastapi.testclient import TestClient


def test_healthz_ok(client: TestClient) -> None:
    r = client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_openapi_and_docs_live_under_api(client: TestClient) -> None:
    assert client.get("/api/openapi.json").status_code == 200
    assert client.get("/api/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404
