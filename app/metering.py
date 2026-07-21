"""The metering core: estimate -> reserve -> generate -> settle.

This is the only place that knows the *policy*. Everything it needs (storage, the
AI layer) is injected, so it can be tested with fakes and the pieces swapped freely.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from app.credits import calculate_credits
from app.generation import GenerationError, TextGenerator, count_tokens
from app.models import RecordStatus, Usage, UsageRecord
from app.repository import QuotaSummary, Repository


class MeteringError(Exception):
    """Base for errors the API layer maps to HTTP status codes."""


class UserNotConfiguredError(MeteringError):
    """No config for this user -> 404."""


class QuotaExceededError(MeteringError):
    """Estimated cost exceeds remaining credits -> 402. Carries the summary."""

    def __init__(self, summary: QuotaSummary, estimated_credits: int):
        super().__init__("Estimated request cost exceeds remaining credits.")
        self.summary = summary
        self.estimated_credits = estimated_credits


class GenerationFailedError(MeteringError):
    """The AI layer failed -> 502. Reservation already reconciled."""


@dataclass(frozen=True)
class GenerationOutcome:
    text: str
    usage: Usage
    credits_charged: int
    multiplier: Decimal
    summary: QuotaSummary


class MeteringService:
    def __init__(self, repository: Repository, generator: TextGenerator):
        self._repo = repository
        self._generator = generator

    def generate(self, user_id: str, prompt: str, max_tokens: int) -> GenerationOutcome:
        config = self._repo.get_config(user_id)
        if config is None:
            raise UserNotConfiguredError(user_id)

        # 1. ESTIMATE the worst case: whole prompt + the full requested completion.
        estimated_tokens = count_tokens(prompt) + max_tokens
        estimated_credits = calculate_credits(estimated_tokens, config.credit_multiplier)

        # 2. RESERVE atomically. The reservation snapshots the multiplier, so this
        #    request settles at the price it was quoted even if config changes now.
        reservation = self._repo.try_reserve(user_id, estimated_credits, config.credit_multiplier)
        if reservation is None:
            summary = self._repo.get_summary(user_id)
            assert summary is not None
            raise QuotaExceededError(summary, estimated_credits)

        # 3. GENERATE. On failure we still reconcile the hold (never leak a reservation).
        try:
            result = self._generator.generate(prompt, max_tokens)
        except GenerationError as exc:
            self._reconcile_failure(reservation, exc.usage)
            raise GenerationFailedError(str(exc)) from exc

        # 4. SETTLE at the ACTUAL usage, using the snapshotted multiplier.
        actual_credits = calculate_credits(result.usage.total_tokens, reservation.multiplier)
        self._repo.finalize(
            reservation,
            credits_charged=actual_credits,
            record=self._record(reservation.user_id, RecordStatus.SETTLED, result.usage,
                                 reservation.multiplier, actual_credits),
        )
        summary = self._repo.get_summary(user_id)
        assert summary is not None
        return GenerationOutcome(result.text, result.usage, actual_credits,
                                 reservation.multiplier, summary)

    def _reconcile_failure(self, reservation, partial_usage: Usage | None) -> None:
        # One code path, three outcomes -- all release the hold via finalize():
        if partial_usage is None:
            # Failed before any usage: charge nothing.
            usage, status, charge = Usage(0, 0), RecordStatus.FAILED_NO_USAGE, 0
        else:
            # Failed but tokens were consumed: honestly bill the partial usage.
            charge = calculate_credits(partial_usage.total_tokens, reservation.multiplier)
            usage, status = partial_usage, RecordStatus.FAILED_PARTIAL
        self._repo.finalize(
            reservation,
            credits_charged=charge,
            record=self._record(reservation.user_id, status, usage, reservation.multiplier, charge),
        )

    @staticmethod
    def _record(user_id, status, usage, multiplier, credits) -> UsageRecord:
        return UsageRecord(
            id=str(uuid.uuid4()),
            user_id=user_id,
            status=status,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            multiplier=multiplier,
            credits_charged=credits,
        )
