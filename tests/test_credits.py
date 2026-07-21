import pytest

from app.credits import calculate_credits


def test_credits_are_total_tokens_times_multiplier():
    # 100 tokens at a 1.5 multiplier costs 150 credits.
    assert calculate_credits(total_tokens=100, multiplier=1.5) == 150


def test_credits_use_exact_math_not_binary_float():
    # 100 tokens x 1.1 is exactly 110 credits.
    # Naive float: 100 * 1.1 == 110.00000000000001 -> ceil -> 111 (a real billing bug).
    assert calculate_credits(total_tokens=100, multiplier=1.1) == 110


def test_different_multipliers_produce_different_credits():
    # Core requirement: two users, same usage, different commercial multipliers.
    assert calculate_credits(total_tokens=100, multiplier=1.0) == 100
    assert calculate_credits(total_tokens=100, multiplier=2.0) == 200


def test_partial_credits_round_up_so_we_never_undercharge():
    # 10 tokens x 1.25 = 12.5 -> rounds UP to 13.
    assert calculate_credits(total_tokens=10, multiplier=1.25) == 13


def test_zero_tokens_cost_zero_credits():
    assert calculate_credits(total_tokens=0, multiplier=1.5) == 0


def test_negative_inputs_are_rejected():
    with pytest.raises(ValueError):
        calculate_credits(total_tokens=-1, multiplier=1.0)
    with pytest.raises(ValueError):
        calculate_credits(total_tokens=10, multiplier=-1.0)
