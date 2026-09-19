"""統一錯誤契約：body 恆為 `{"error": {"code", "message", "details"}}`。

前端只需要認一種形狀；`code` 是穩定的機器可讀值，`message` 才是給人看的。
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

CODE_NOT_FOUND = "not_found"
CODE_INVALID_REQUEST = "invalid_request"
CODE_CONFLICT = "conflict"
CODE_UPSTREAM_FAILED = "upstream_failed"
CODE_VAULT_LOCKED = "vault_locked"
CODE_UNSUPPORTED = "unsupported"


class ApiError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }


class NotFoundError(ApiError):
    status_code = 404
    code = CODE_NOT_FOUND


class InvalidRequestError(ApiError):
    status_code = 400
    code = CODE_INVALID_REQUEST


class ConflictError(ApiError):
    status_code = 409
    code = CODE_CONFLICT


class UpstreamFailedError(ApiError):
    status_code = 502
    code = CODE_UPSTREAM_FAILED


class VaultLockedError(ApiError):
    status_code = 409
    code = CODE_VAULT_LOCKED


class UnsupportedError(ApiError):
    status_code = 400
    code = CODE_UNSUPPORTED


def error_body(
    code: str, message: str, details: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details}}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_body(
                CODE_INVALID_REQUEST,
                "request validation failed",
                {"errors": _safe_validation_details(exc)},
            ),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(_request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Internal server error"),
        )


def _safe_validation_details(exc: RequestValidationError) -> list[dict[str, Any]]:
    """只回欄位位置與原因，**不回** `input`——那可能就是使用者剛送進來的機密。"""
    out: list[dict[str, Any]] = []
    for err in exc.errors():
        out.append(
            {
                "loc": [str(p) for p in err.get("loc", ())],
                "type": err.get("type", "value_error"),
                "msg": err.get("msg", ""),
            }
        )
    return out
