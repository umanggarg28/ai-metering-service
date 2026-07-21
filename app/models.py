"""Domain types for the metering service.

These are plain data containers (the 'nouns' of the system). Keeping them free of
DB or HTTP concerns is what lets the storage and API layers be swapped independently.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RecordStatus(str, Enum):
    """Final state of a usage record (an immutable ledger entry)."""

    SETTLED = "settled"                    # generation succeeded, actual usage billed
    FAILED_NO_USAGE = "failed_no_usage"    # provider failed before any usage
    FAILED_PARTIAL = "failed_partial"      # provider failed but reported partial usage
    FAILED_UNKNOWN = "failed_unknown"      # provider failed, usage unknown (reconcile later)


@dataclass(frozen=True)
class UserConfig:
    """A user's commercial arrangement."""

    user_id: str
    quota_credits: int          # total credit allowance
    credit_multiplier: Decimal  # credit cost per token, kept as Decimal (never float)


@dataclass(frozen=True)
class Usage:
    """Raw token usage reported by the AI layer."""

    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class Reservation:
    """A hold placed on a user's balance before generation.

    Snapshots the multiplier so an in-flight request settles at the price it was
    quoted, even if an admin changes the user's multiplier mid-flight.
    """

    id: str
    user_id: str
    estimated_credits: int
    multiplier: Decimal  # snapshot at reserve-time


@dataclass(frozen=True)
class UsageRecord:
    """An immutable ledger entry describing one request's outcome."""

    id: str
    user_id: str
    status: RecordStatus
    prompt_tokens: int
    completion_tokens: int
    multiplier: Decimal        # the multiplier actually applied
    credits_charged: int
    created_at: datetime = field(default_factory=_now)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens
