"""Errors from publishing and checking games, in one shape scripts and AI tools can read:

    {"ok": false, "detail": "what went wrong, for people",
     "errors": [{"code": "needs_build", "message": "...", "file": "..."}], "warnings": [...]}

detail is the same text start.ng always sent, so older clients that only read it still work.
"""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .checks import Problem
from .publish import PublishError


class ApiError(HTTPException):
    def __init__(self, status_code: int, code: str, message: str, errors: list[Problem] | None = None, warnings: list[Problem] | None = None):
        super().__init__(status_code, message)
        self.errors = errors or [Problem(code, message)]
        self.warnings = warnings or []

    @classmethod
    def rejected(cls, err: PublishError, status_code: int = 422) -> "ApiError":
        return cls(status_code, err.code, str(err), err.errors, err.warnings)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "ok": False,
            "detail": exc.detail,
            "errors": [p.as_dict() for p in exc.errors],
            "warnings": [p.as_dict() for p in exc.warnings],
        },
        headers=exc.headers,
    )
