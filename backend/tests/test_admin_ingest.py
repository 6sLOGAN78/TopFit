from collections.abc import Callable

import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

pytestmark = pytest.mark.usefixtures("database_url")

TOKEN = "t" * 40
URL = "/api/admin/ingest/remotive"
MakeClient = Callable[[dict[str, str]], TestClient]


@pytest.fixture(autouse=True)
def _clean(database_url: str) -> None:
    eng = create_engine(database_url)
    with eng.begin() as conn:
        conn.execute(text("TRUNCATE tasks RESTART IDENTITY"))
    eng.dispose()


def _tasks(database_url: str) -> list[tuple[str, dict[str, object], str]]:
    eng = create_engine(database_url)
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT kind, payload, status FROM tasks")).all()
    eng.dispose()
    return [(r[0], r[1], r[2]) for r in rows]


def test_unset_token_is_404(make_client: MakeClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    c = make_client({})
    assert c.post(URL, headers={"X-Admin-Token": TOKEN}).status_code == 404


def test_empty_token_is_404(make_client: MakeClient) -> None:
    c = make_client({"ADMIN_TOKEN": ""})
    assert c.post(URL, headers={"X-Admin-Token": ""}).status_code == 404


def test_auth_and_enqueue(make_client: MakeClient, database_url: str) -> None:
    c = make_client({"ADMIN_TOKEN": TOKEN})
    assert c.post(URL).status_code == 401
    assert c.post(URL, headers={"X-Admin-Token": "wrong"}).status_code == 401
    with respx.mock(assert_all_mocked=True):
        r = c.post(URL, headers={"X-Admin-Token": TOKEN})
        assert r.status_code == 202
        task_id = r.json()["task_id"]
        assert isinstance(task_id, int)
        again = c.post(URL, headers={"X-Admin-Token": TOKEN})
    assert again.status_code == 409
    assert again.json() == {"detail": "already_pending"}
    assert _tasks(database_url) == [
        ("ingest", {"source": "remotive", "mode": "incremental"}, "queued")
    ]


def test_unknown_source_and_mode(make_client: MakeClient, database_url: str) -> None:
    c = make_client({"ADMIN_TOKEN": TOKEN})
    h = {"X-Admin-Token": TOKEN}
    assert c.post("/api/admin/ingest/nope", headers=h).status_code == 404
    assert c.post(URL + "?mode=bogus", headers=h).status_code == 422
    assert _tasks(database_url) == []
    assert c.post(URL + "?mode=backfill", headers=h).status_code == 202
    assert _tasks(database_url)[0][1]["mode"] == "backfill"


def test_token_not_logged(make_client: MakeClient, capsys: pytest.CaptureFixture[str]) -> None:
    c = make_client({"ADMIN_TOKEN": TOKEN})
    c.post(URL, headers={"X-Admin-Token": TOKEN})
    c.post(URL, headers={"X-Admin-Token": TOKEN + "bad"})
    out = capsys.readouterr()
    rendered = out.out + out.err
    assert "admin_ingest_enqueued" in rendered
    assert TOKEN not in rendered
