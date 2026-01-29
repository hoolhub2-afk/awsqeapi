from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.integrations.amazonq_client import AccountUnauthorizedException


OPENAI_ERROR_PATHS = frozenset(("/v1/chat/completions", "/v1/models"))


def _is_openai_path(path: str) -> bool:
    return path in OPENAI_ERROR_PATHS


def register_openai_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AccountUnauthorizedException)
    async def _account_unauthorized_handler(request: Request, exc: AccountUnauthorizedException):
        message = "所有账号认证失败/Token过期,请联系管理员"
        return JSONResponse(status_code=403, content={"detail": message})

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(request: Request, exc: StarletteHTTPException):
        if not _is_openai_path(request.url.path):
            return await http_exception_handler(request, exc)

        detail = exc.detail
        headers = getattr(exc, "headers", None)

        if isinstance(detail, dict):
            return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)

        return JSONResponse(status_code=exc.status_code, content={"detail": detail}, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(request: Request, exc: RequestValidationError):
        if not _is_openai_path(request.url.path):
            return await request_validation_exception_handler(request, exc)

        errors = exc.errors() or []
        message: Any = "Invalid request"
        if errors:
            first = errors[0]
            loc = ".".join([str(x) for x in first.get("loc", []) if x is not None])
            msg = first.get("msg", "")
            message = f"{loc}: {msg}".strip(": ")

        return JSONResponse(status_code=400, content={"detail": message})
