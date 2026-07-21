import threading
from decimal import Decimal

import pytest

from app.models import RecordStatus, UsageRecord, UserConfig
from app.repository import SqliteRepository


@pytest.fixture
def repo(tmp_path):
    return SqliteRepository(str(tmp_path / "test.db"))


def cfg(user_id="u", quota=1000, mult="1.0"):
    return UserConfig(user_id=user_id, quota_credits=quota, credit_multiplier=Decimal(mult))


def record(user_id="u", credits=150, status=RecordStatus.SETTLED):
    return UsageRecord(
        id=f"rec-{credits}-{status.value}", user_id=user_id, status=status,
        prompt_tokens=40, completion_tokens=60, multiplier=Decimal("1.5"),
        credits_charged=credits,
    )


def test_upsert_and_get_config(repo):
    repo.upsert_config(cfg(quota=500, mult="1.5"))
    got = repo.get_config("u")
    assert got.quota_credits == 500
    assert got.credit_multiplier == Decimal("1.5")


def test_get_config_missing_returns_none(repo):
    assert repo.get_config("nobody") is None


def test_reserve_succeeds_and_tracks_reserved(repo):
    repo.upsert_config(cfg(quota=1000))
    assert repo.try_reserve("u", 200, Decimal("1.0")) is not None
    s = repo.get_summary("u")
    assert s.reserved == 200
    assert s.remaining == 800


def test_reserve_fails_when_insufficient(repo):
    repo.upsert_config(cfg(quota=100))
    assert repo.try_reserve("u", 150, Decimal("1.0")) is None
    assert repo.get_summary("u").reserved == 0


def test_finalize_releases_hold_applies_charge_and_records(repo):
    repo.upsert_config(cfg(quota=1000))
    r = repo.try_reserve("u", 210, Decimal("1.5"))  # held estimate
    repo.finalize(r, 150, record(credits=150))       # actual charge
    s = repo.get_summary("u")
    assert s.used == 150
    assert s.reserved == 0        # the 210 hold was released
    assert s.remaining == 850
    recs = repo.list_records("u")
    assert len(recs) == 1 and recs[0].credits_charged == 150


def test_config_update_preserves_usage_counters(repo):
    repo.upsert_config(cfg(quota=1000, mult="1.0"))
    r = repo.try_reserve("u", 100, Decimal("1.0"))
    repo.finalize(r, 100, record(credits=100))
    repo.upsert_config(cfg(quota=2000, mult="2.0"))  # admin edits config
    s = repo.get_summary("u")
    assert s.used == 100          # usage NOT reset by config change
    assert s.quota == 2000        # quota increase applies


def test_concurrent_reserves_cannot_exceed_quota(repo):
    # quota 1000; 10 threads each try to reserve 200. At most 5 can fit (5*200=1000).
    repo.upsert_config(cfg(quota=1000))
    results: list[bool] = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()  # release all threads at once for a real race
        results.append(repo.try_reserve("u", 200, Decimal("1.0")) is not None)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(results) == 5              # exactly 5 succeeded
    s = repo.get_summary("u")
    assert s.reserved == 1000
    assert s.remaining == 0               # invariant: used + reserved <= quota
