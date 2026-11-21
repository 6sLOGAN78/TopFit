from fastapi.testclient import TestClient


def test_ping_sets_signed_cookie_and_round_trips(client: TestClient) -> None:
    first = client.get("/api/session/ping")
    assert first.status_code == 200
    assert first.json() == {"cookie_received": False}
    set_cookie = first.headers["set-cookie"]
    lowered = set_cookie.lower()
    assert "rk_ping=" in set_cookie
    assert "httponly" in lowered
    assert "secure" in lowered
    assert "samesite=lax" in lowered
    assert "path=/api" in lowered
    assert "max-age=3600" in lowered
    assert first.headers["cache-control"] == "no-store"

    second = client.get("/api/session/ping")
    assert second.json() == {"cookie_received": True}


def test_tampered_cookie_is_rejected(client: TestClient) -> None:
    client.cookies.set("rk_ping", "tampered.value.signature", path="/api")
    r = client.get("/api/session/ping")
    assert r.json() == {"cookie_received": False}
