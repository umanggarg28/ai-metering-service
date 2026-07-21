"""Application wiring + error-to-HTTP translation."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import router
from app.generation import MockGenerator, TextGenerator
from app.metering import (
    GenerationFailedError,
    MeteringService,
    QuotaExceededError,
    UserNotConfiguredError,
)
from app.repository import SqliteRepository


def _error(status_code: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message, **extra}})


def create_app(db_path: str = "metering.db", generator: TextGenerator | None = None) -> FastAPI:
    repo = SqliteRepository(db_path)
    service = MeteringService(repo, generator or MockGenerator())

    app = FastAPI(title="AI Usage Metering & Quota Service", version="0.1.0")
    app.state.repo = repo
    app.state.service = service
    app.include_router(router)

    @app.exception_handler(UserNotConfiguredError)
    async def _not_configured(request: Request, exc: UserNotConfiguredError):
        return _error(404, "user_configuration_not_found", "No configuration exists for this user.")

    @app.exception_handler(QuotaExceededError)
    async def _quota_exceeded(request: Request, exc: QuotaExceededError):
        s = exc.summary
        return _error(
            402, "quota_exceeded", str(exc),
            quota=s.quota, used=s.used, reserved=s.reserved,
            remaining=s.remaining, estimated_credits=exc.estimated_credits,
        )

    @app.exception_handler(GenerationFailedError)
    async def _generation_failed(request: Request, exc: GenerationFailedError):
        return _error(502, "generation_provider_error", str(exc))

    return app


app = create_app()
