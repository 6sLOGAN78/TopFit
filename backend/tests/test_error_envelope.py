from fastapi import FastAPI
from fastapi.testclient import TestClient

from ranker.api.errors import RateLimited, install_error_handlers


def test_validation_error_envelope(client: TestClient) -> None:
    r = client.get("/api/jobs/search")
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation_error"
    assert err["details"]["errors"]


def test_validation_error_does_not_echo_input(client: TestClient) -> None:
    r = client.get("/api/jobs/search", params={"q": "secret-resume-text" + "a" * 600})
    assert r.status_code == 422
    assert "secret-resume-text" not in r.text


def test_not_found_envelope(client: TestClient) -> None:
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


def test_rate_limited_envelope() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/limited")
    def limited() -> None:
        raise RateLimited(retry_after=120)

    r = TestClient(app).get("/limited")
    assert r.status_code == 429
    assert r.headers["retry-after"] == "120"
    body = r.json()["error"]
    assert body["code"] == "rate_limited"
    assert body["details"]["retry_after"] == 120
