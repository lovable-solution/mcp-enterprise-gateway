"""HTTP errors as RFC 9457 problem details (``application/problem+json``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    status: ClassVar[int] = 500
    default_code: ClassVar[str] = "internal_error"
    title: ClassVar[str] = "Internal error"

    def __init__(
        self, detail: str, *, code: str | None = None, headers: Mapping[str, str] | None = None
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code or self.default_code
        self.headers = dict(headers or {})


class Unauthorized(ProblemError):
    status = 401
    default_code = "unauthorized"
    title = "Authentication required"

    def __init__(self, detail: str) -> None:
        safe = detail.replace('"', "'")
        super().__init__(
            detail,
            headers={
                "WWW-Authenticate": f'Bearer error="invalid_token", error_description="{safe}"'
            },
        )


class Forbidden(ProblemError):
    status = 403
    default_code = "forbidden"
    title = "Not permitted"


class NotFound(ProblemError):
    status = 404
    default_code = "not_found"
    title = "Not found"


class Conflict(ProblemError):
    status = 409
    default_code = "conflict"
    title = "Conflict"


class ServiceUnavailable(ProblemError):
    status = 503
    default_code = "unavailable"
    title = "Service unavailable"


def problem_body(status: int, title: str, detail: str, code: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": f"urn:mcp-gateway:problem:{code}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        **extra,
    }


async def _problem_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ProblemError)  # noqa: S101 - registered for ProblemError only
    return JSONResponse(
        problem_body(exc.status, exc.title, exc.detail, exc.code),
        status_code=exc.status,
        headers=exc.headers,
        media_type=PROBLEM_MEDIA_TYPE,
    )


async def _validation_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registered for this type only
    errors = [
        {"loc": list(err.get("loc", ())), "msg": err.get("msg", ""), "type": err.get("type", "")}
        for err in exc.errors()
    ]
    body = problem_body(422, "Invalid request", "request validation failed", "invalid_request")
    return JSONResponse({**body, "errors": errors}, status_code=422, media_type=PROBLEM_MEDIA_TYPE)


def install_problem_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ProblemError, _problem_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
