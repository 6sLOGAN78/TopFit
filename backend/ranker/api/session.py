"""Signed-cookie round-trip used by the status panel to prove cookies work end to end."""

from fastapi import APIRouter, Request, Response
from itsdangerous import BadSignature, URLSafeTimedSerializer

from ranker.settings import Settings

router = APIRouter()

COOKIE = "rk_ping"
MAX_AGE = 3600


@router.get("/session/ping")
def session_ping(request: Request, response: Response) -> dict[str, bool]:
    settings: Settings = request.app.state.settings
    s = URLSafeTimedSerializer(settings.session_secret.get_secret_value(), salt="rk-ping-v1")
    received = False
    if raw := request.cookies.get(COOKIE):
        try:
            s.loads(raw, max_age=MAX_AGE)
            received = True
        except BadSignature:  # SignatureExpired subclasses BadSignature
            received = False
    response.set_cookie(
        COOKIE,
        s.dumps({"v": 1}),
        max_age=MAX_AGE,
        path="/api",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return {"cookie_received": received}
