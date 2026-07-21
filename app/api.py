"""HTTP routes. Thin layer: validate -> call the service/repo -> shape the response.

Domain errors (UserNotConfigured / QuotaExceeded / GenerationFailed) are raised by
the service and translated to HTTP status codes by handlers registered in main.py,
so the routes stay free of error-mapping noise.

Routes are sync `def` on purpose: FastAPI runs them in a threadpool, giving real
concurrency, while the SQLite atomic reservation guarantees correctness.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.metering import MeteringService, UserNotConfiguredError
from app.models import UserConfig
from app.repository import Repository
from app.schemas import (
    ConfigRequest,
    GenerateRequest,
    GenerateResponse,
    QuotaOut,
    RecordOut,
    UsageOut,
    UsageSummaryResponse,
)

router = APIRouter()


def _repo(request: Request) -> Repository:
    return request.app.state.repo


def _service(request: Request) -> MeteringService:
    return request.app.state.service


@router.put("/users/{user_id}/config", response_model=UsageSummaryResponse)
def upsert_config(user_id: str, body: ConfigRequest, request: Request) -> UsageSummaryResponse:
    repo = _repo(request)
    repo.upsert_config(UserConfig(user_id, body.quota_credits, body.credit_multiplier))
    summary = repo.get_summary(user_id)
    assert summary is not None
    return UsageSummaryResponse(
        user_id=user_id,
        quota_credits=body.quota_credits,
        credit_multiplier=body.credit_multiplier,
        quota=QuotaOut(limit=summary.quota, used=summary.used,
                       reserved=summary.reserved, remaining=summary.remaining),
    )


@router.post("/users/{user_id}/generations", response_model=GenerateResponse)
def generate(user_id: str, body: GenerateRequest, request: Request) -> GenerateResponse:
    outcome = _service(request).generate(user_id, body.prompt, body.max_tokens)
    s = outcome.summary
    return GenerateResponse(
        text=outcome.text,
        usage=UsageOut(
            prompt_tokens=outcome.usage.prompt_tokens,
            completion_tokens=outcome.usage.completion_tokens,
            total_tokens=outcome.usage.total_tokens,
            multiplier=outcome.multiplier,
            credits_charged=outcome.credits_charged,
        ),
        quota=QuotaOut(limit=s.quota, used=s.used, reserved=s.reserved, remaining=s.remaining),
    )


@router.get("/users/{user_id}/usage", response_model=UsageSummaryResponse)
def get_usage(user_id: str, request: Request) -> UsageSummaryResponse:
    repo = _repo(request)
    config = repo.get_config(user_id)
    if config is None:
        raise UserNotConfiguredError(user_id)
    summary = repo.get_summary(user_id)
    assert summary is not None
    return UsageSummaryResponse(
        user_id=user_id,
        quota_credits=config.quota_credits,
        credit_multiplier=config.credit_multiplier,
        quota=QuotaOut(limit=summary.quota, used=summary.used,
                       reserved=summary.reserved, remaining=summary.remaining),
    )


@router.get("/users/{user_id}/usage/records", response_model=list[RecordOut])
def list_records(user_id: str, request: Request) -> list[RecordOut]:
    records = _repo(request).list_records(user_id)
    return [
        RecordOut(
            id=r.id, status=r.status.value,
            prompt_tokens=r.prompt_tokens, completion_tokens=r.completion_tokens,
            total_tokens=r.total_tokens, multiplier=r.multiplier,
            credits_charged=r.credits_charged, created_at=r.created_at.isoformat(),
        )
        for r in records
    ]
