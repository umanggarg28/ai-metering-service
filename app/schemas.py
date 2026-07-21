"""Pydantic models = the API contract (request validation + response shape)."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field


class ConfigRequest(BaseModel):
    quota_credits: int = Field(ge=0, description="Total credit allowance for the user.")
    credit_multiplier: Decimal = Field(gt=0, description="Credit cost per token.")


class GenerateRequest(BaseModel):
    prompt: str = Field(min_length=1)
    max_tokens: int = Field(gt=0, le=100_000, description="Max completion tokens (sizes the estimate).")


class QuotaOut(BaseModel):
    limit: int
    used: int
    reserved: int
    remaining: int


class UsageOut(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    multiplier: Decimal
    credits_charged: int


class GenerateResponse(BaseModel):
    text: str
    usage: UsageOut
    quota: QuotaOut


class UsageSummaryResponse(BaseModel):
    user_id: str
    quota_credits: int
    credit_multiplier: Decimal
    quota: QuotaOut


class RecordOut(BaseModel):
    id: str
    status: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    multiplier: Decimal
    credits_charged: int
    created_at: str
