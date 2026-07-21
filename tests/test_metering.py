from decimal import Decimal

import pytest

from app.generation import GenerationError, GenerationResult, MockGenerator
from app.metering import (
    GenerationFailedError,
    MeteringService,
    QuotaExceededError,
    UserNotConfiguredError,
)
from app.models import RecordStatus, Usage, UserConfig
from app.repository import SqliteRepository

PROMPT = "x" * 160  # count_tokens -> 160 // 4 = 40 prompt tokens


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(str(tmp_path / "m.db"))


def configure(repo, user="alice", quota=1000, mult="1.5"):
    repo.upsert_config(UserConfig(user, quota, Decimal(mult)))


def test_unconfigured_user_raises(repo):
    svc = MeteringService(repo, MockGenerator())
    with pytest.raises(UserNotConfiguredError):
        svc.generate("ghost", "hi", 100)


def test_successful_generation_settles_actual_usage(repo):
    configure(repo, quota=1000, mult="1.5")
    svc = MeteringService(repo, MockGenerator(completion_tokens=60))
    out = svc.generate("alice", PROMPT, max_tokens=100)
    # estimate reserved 210 (140 tokens x 1.5); actual = 40+60 = 100 tokens -> 150 credits
    assert out.credits_charged == 150
    assert out.summary.used == 150
    assert out.summary.reserved == 0        # the 210 hold was released
    assert out.summary.remaining == 850
    recs = repo.list_records("alice")
    assert len(recs) == 1 and recs[0].status == RecordStatus.SETTLED


def test_quota_exceeded_rejects_before_calling_ai(repo):
    configure(repo, quota=100, mult="1.5")

    class BoomGenerator:
        def generate(self, prompt, max_tokens):
            raise AssertionError("AI must NOT be called when the estimate is unaffordable")

    svc = MeteringService(repo, BoomGenerator())
    with pytest.raises(QuotaExceededError):
        svc.generate("alice", PROMPT, max_tokens=100)  # estimate 210 > 100 remaining
    assert repo.get_summary("alice").reserved == 0     # no leaked hold
    assert repo.list_records("alice") == []            # nothing recorded


def test_different_users_have_independent_multiplier(repo):
    configure(repo, "alice", quota=1000, mult="1.0")
    configure(repo, "bob", quota=1000, mult="2.0")
    svc = MeteringService(repo, MockGenerator(completion_tokens=60))
    a = svc.generate("alice", PROMPT, 100)
    b = svc.generate("bob", PROMPT, 100)
    assert a.credits_charged == 100   # 100 tokens x 1.0
    assert b.credits_charged == 200   # 100 tokens x 2.0


def test_failure_before_usage_releases_hold_and_charges_nothing(repo):
    configure(repo, quota=1000, mult="1.5")

    class FailBefore:
        def generate(self, prompt, max_tokens):
            raise GenerationError("provider down")  # usage is None

    svc = MeteringService(repo, FailBefore())
    with pytest.raises(GenerationFailedError):
        svc.generate("alice", PROMPT, 100)
    s = repo.get_summary("alice")
    assert s.used == 0 and s.reserved == 0
    recs = repo.list_records("alice")
    assert len(recs) == 1 and recs[0].status == RecordStatus.FAILED_NO_USAGE


def test_failure_with_partial_usage_bills_partial(repo):
    configure(repo, quota=1000, mult="1.5")

    class FailPartial:
        def generate(self, prompt, max_tokens):
            raise GenerationError("cut off", usage=Usage(prompt_tokens=40, completion_tokens=20))

    svc = MeteringService(repo, FailPartial())
    with pytest.raises(GenerationFailedError):
        svc.generate("alice", PROMPT, 100)
    s = repo.get_summary("alice")
    assert s.used == 90 and s.reserved == 0   # 60 tokens x 1.5 = 90 billed, hold released
    assert repo.list_records("alice")[0].status == RecordStatus.FAILED_PARTIAL


def test_actual_usage_above_estimate_is_billed_and_blocks_future(repo):
    # A provider can report MORE usage than we estimated (hidden/system tokens, or a
    # provider that doesn't honour max_tokens). We already generated, so we honour the
    # request and bill the real cost -- a controlled overage -- then block the NEXT one.
    configure(repo, quota=300, mult="1.0")

    class Overproducer:
        def generate(self, prompt, max_tokens):
            return GenerationResult(text="long", usage=Usage(prompt_tokens=40, completion_tokens=400))

    svc = MeteringService(repo, Overproducer())
    out = svc.generate("alice", PROMPT, max_tokens=100)  # estimate reserved = 140
    assert out.credits_charged == 440         # actual 440 tokens x 1.0, billed in full
    assert out.summary.remaining == -140      # controlled overage: balance goes negative
    with pytest.raises(QuotaExceededError):   # the next request is now rejected
        svc.generate("alice", PROMPT, max_tokens=10)


def test_in_flight_multiplier_change_settles_at_reserved_rate(repo):
    configure(repo, quota=100_000, mult="1.0")

    class ChangesConfigMidflight:
        def generate(self, prompt, max_tokens):
            # admin bumps the price WHILE this request is in flight
            repo.upsert_config(UserConfig("alice", 100_000, Decimal("5.0")))
            return GenerationResult(text="ok", usage=Usage(prompt_tokens=40, completion_tokens=60))

    svc = MeteringService(repo, ChangesConfigMidflight())
    out = svc.generate("alice", PROMPT, 100)
    # settles at the SNAPSHOT multiplier 1.0 -> 100 credits, NOT the new 5.0 -> 500
    assert out.credits_charged == 100
