"""The AI generation layer.

`TextGenerator` is the interface metering depends on; `MockGenerator` is a
deterministic stand-in that returns realistic token usage. Swapping in a real LLM
provider means writing one class with the same `generate` signature.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models import Usage


def count_tokens(text: str) -> int:
    """Approximate tokens as ~4 characters per token (a common rough heuristic).

    Used for the pre-request estimate. The real billable count comes from the
    provider after generation -- this is only how we size the reservation up front.
    """
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class GenerationResult:
    text: str
    usage: Usage


class GenerationError(Exception):
    """Raised when the AI layer fails.

    `usage` is None if the failure happened before any tokens were consumed, or a
    partial `Usage` if some tokens were incurred before the failure.
    """

    def __init__(self, message: str, usage: Usage | None = None):
        super().__init__(message)
        self.usage = usage


class TextGenerator(Protocol):
    def generate(self, prompt: str, max_tokens: int) -> GenerationResult: ...


class MockGenerator:
    """Deterministic LLM stand-in. Completion size is configurable so the demo can
    show actual usage landing below the reserved estimate (the common case)."""

    def __init__(self, completion_tokens: int = 60, text: str = "This is a mock-generated response."):
        self._completion_tokens = completion_tokens
        self._text = text

    def generate(self, prompt: str, max_tokens: int) -> GenerationResult:
        prompt_tokens = count_tokens(prompt)
        completion_tokens = min(max_tokens, self._completion_tokens)
        return GenerationResult(text=self._text, usage=Usage(prompt_tokens, completion_tokens))
