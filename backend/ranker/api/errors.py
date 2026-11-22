"""Uniform error envelope ``{"error": {"code", "message", "details"}}`` for every API error.

No stack traces and no echoed request input (T-03-06): validation details keep only the
location and message of each problem, never the offending value.
"""

from typing import Any, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from ranker.search.models import ErrorBody, ErrorResponse

_CODES = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    503: "unavailable",
}


class RateLimited(Exception):
    """Raise to answer 429 with a Retry-After header."""

    def __init__(self, retry_after: int) -> None:
        super().__init__("rate_limited")
        self.retry_after = retry_after


def _envelope(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(error=ErrorBody(code=code, message=message, details=details))
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


async def _http_exc(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, exc)
    code = _CODES.get(exc.status_code, "http_error")
    message = exc.detail or code
    return _envelope(exc.status_code, code, message, headers=dict(exc.headers or {}) or None)


async def _validation_exc(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, exc)
    errors = [
        {"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": str(e.get("msg", ""))}
        for e in exc.errors()
    ]
    return _envelope(422, "validation_error", "Request validation failed", {"errors": errors})


async def _rate_limited_exc(_: Request, exc: Exception) -> JSONResponse:
    exc = cast(RateLimited, exc)
    return _envelope(
        429,
        "rate_limited",
        "Too many requests",
        {"retry_after": exc.retry_after},
        {"Retry-After": str(exc.retry_after)},
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _http_exc)
    app.add_exception_handler(RequestValidationError, _validation_exc)
    app.add_exception_handler(RateLimited, _rate_limited_exc)


def use_error_envelope_in_openapi(app: FastAPI) -> None:
    """Point every 422 at ErrorResponse and drop FastAPI's HTTPValidationError schemas."""

    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, routes=app.routes, description=app.description
        )
        ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for path in schema.get("paths", {}).values():
            for op in path.values():
                r422 = op.get("responses", {}).get("422")
                if r422 is not None:
                    r422["content"] = {"application/json": {"schema": ref}}
        comps = schema.get("components", {}).get("schemas", {})
        for name in ("HTTPValidationError", "ValidationError"):
            comps.pop(name, None)
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]
