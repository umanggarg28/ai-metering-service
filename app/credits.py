from decimal import Decimal, ROUND_CEILING


def calculate_credits(total_tokens: int, multiplier: float | Decimal) -> int:
    """Convert raw token usage into billable credits.

    credits = ceil(total_tokens * multiplier)

    The multiplication is done with ``Decimal`` (exact base-10 arithmetic) rather
    than binary ``float`` so rounding is never corrupted by float representation
    error -- e.g. 100 tokens x 1.1 must bill as 110 credits, not 111.

    Credits are rounded UP per request so we never under-charge. This introduces a
    small-request rounding bias (many tiny requests can cost more than one large
    request); production billing would likely track fixed-point fractional units.
    """
    if total_tokens < 0:
        raise ValueError("total_tokens must be non-negative")
    if multiplier < 0:
        raise ValueError("multiplier must be non-negative")

    raw = Decimal(total_tokens) * Decimal(str(multiplier))
    return int(raw.to_integral_value(rounding=ROUND_CEILING))
