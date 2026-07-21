# Solution Design — AI Usage Metering & Quota Service

## 1. Problem & approach

A service must gate AI text-generation requests against per-user credit quotas. The
defining difficulty: **the true cost of a request is only known *after* generation,
but the admission decision must be made *before* it.**

The whole design turns on one pattern — **reserve → generate → settle** (a credit-card
hold):

1. **Estimate** the worst-case cost from the prompt size + requested `max_tokens`.
2. **Reserve** (hold) that many credits, atomically.
3. **Generate** via the AI layer.
4. **Settle** at the *actual* usage; release the unused hold.

This single pattern resolves the three hard problems in metering:
estimate-vs-actual, concurrency, and mid-generation failure.

## 2. Architecture — one responsibility per module

```
app/
  schemas.py      API contract (Pydantic request/response models)
  api.py          HTTP routes; validate -> call service -> shape response
  main.py         wiring + domain-error -> HTTP-status translation
  metering.py     ★ the policy core: estimate -> reserve -> generate -> settle
  credits.py      pure function: tokens + multiplier -> credits
  generation.py   AI layer: TextGenerator Protocol + deterministic MockGenerator
  repository.py   storage interface + SqliteRepository (atomic reservation)
  models.py       domain types: UserConfig, Reservation, UsageRecord, Usage
```

Dependencies point *inward* to `metering.py`, which depends only on interfaces
(`Repository`, `TextGenerator`). That is what makes each piece independently
swappable and testable (the metering tests inject fake generators; the API tests
inject a temp-file DB).

## 3. Credit calculation model

`credits = ceil(total_tokens × multiplier)` — explicit, deterministic, per-user via
the multiplier. Two deliberate choices:

- **Decimal, not float.** The multiplication uses `Decimal` (via `Decimal(str(x))`)
  because binary floats mis-round money: naively, `100 × 1.1 == 110.00000000000001`,
  which `ceil`s to **111**. A regression test pins this at **110**.
- **Round up per request** so we never under-charge. This creates a small-request
  rounding bias (many tiny requests can cost more than one large one). Acceptable for
  this service; production billing would track fixed-point fractional units.

## 4. Quota & reservation model

Each user row holds `quota_credits`, `credit_multiplier`, `used_credits`,
`reserved_credits`. **Remaining = quota − used − reserved.**

- **Admission:** reserve is a single atomic conditional UPDATE —
  `UPDATE users SET reserved = reserved + est WHERE quota − used − reserved >= est`.
  If it updates a row, the hold is placed; if not, the request is rejected (402)
  **before the AI is ever called**.
- **Settlement:** `used += actual; reserved −= estimate` and an immutable
  `UsageRecord` is appended. The same `finalize()` path handles success and every
  failure mode — a reservation can never leak.

Quota is modelled as a fixed allowance (usage counts up). **Monthly reset periods are
a documented future extension**, not built, to avoid overbuilding.

## 5. Consistency & concurrency

The atomic conditional UPDATE is the **sole** correctness mechanism — not a Python
lock. SQLite serializes writers, so two near-simultaneous requests cannot both reserve
the same balance. `test_concurrent_reserves_cannot_exceed_quota` races 10 threads for a
1000-credit quota and asserts **exactly 5** succeed and `used + reserved ≤ quota` holds.

Routes are synchronous `def` handlers: FastAPI runs them in a threadpool (real
concurrency), while the database guarantees the invariant. This is deliberately simpler
and *more* correct than an async `asyncio.Lock`, which only protects a single process.

## 6. Failure handling around the AI layer

`GenerationError` optionally carries partial `Usage`. Three explicit outcomes, all of
which release the hold via `finalize()`:

| Failure | Charge | Record status |
|---|---|---|
| Before any usage | 0 | `FAILED_NO_USAGE` |
| With reported partial usage | partial (billed honestly) | `FAILED_PARTIAL` |
| Usage unknown | 0 (reconcile later in prod) | `FAILED_UNKNOWN` |

All return HTTP `502 generation_provider_error`.

## 7. Estimate vs. actual, and config changes

- **Actual < estimate** (the normal case): settle the smaller actual amount, release
  the difference.
- **Actual > estimate:** we already generated — we honour the request, settle the real
  cost (balance may reach/pass zero for that one request), record a **controlled
  post-settlement overage**, and block the *next* request. The estimate is an upper
  bound only under the mock's assumptions; a real provider can add hidden/system tokens,
  so we don't claim overspend is impossible.
- **Multiplier/quota changed mid-flight:** the `Reservation` **snapshots** the
  multiplier at reserve-time, and settlement uses the snapshot — an in-flight request
  bills at the price it was quoted, never a surprise doubling. Historical records are
  immutable; quota increases apply immediately; a quota cut below `used + reserved` is
  allowed but admits no new requests until usage falls under it. (Tested end-to-end.)

## 8. Persistence choice — SQLite

**SQLite behind a `Repository` interface.** Rationale: this service is about
consistency, durable history, and concurrency, and SQLite provides **real
transactions and an atomic reservation with zero infrastructure**. In-memory state
would lose history on restart and its per-process lock would break under multiple
workers. The `Repository` Protocol means production can move to **Postgres** (row
locking / atomic conditional updates) without touching `metering.py`. SQLite is used
directly via stdlib `sqlite3` — no ORM — to keep the project small and reviewable.

## 9. Worked numeric example (matches the implementation & tests)

> User `alice`: quota **1000**, multiplier **1.5**, already used **700** (300 remaining).
> Request: prompt = 40 tokens, `max_tokens` = 100.
> **Estimate:** (40 + 100) tokens × 1.5 = **210 credits**, reserved (210 ≤ 300 ✓).
> **Generate:** actual usage = 40 prompt + 60 completion = **100 tokens**.
> **Settle:** ceil(100 × 1.5) = **150 credits**. Release the 60 unused held credits.
> **Result:** used 700 → **850**, remaining **150**.
>
> If she'd had only 200 remaining, 210 > 200 → **402 quota_exceeded, AI never called.**

(This is exactly `test_successful_generation_settles_actual_usage`.)

## 10. Deliberately deferred (avoiding overbuilding)

- **Idempotency keys** for client retries — every record already has a server-side
  `request_id`; client `Idempotency-Key` support is a documented next step.
- **Quota reset periods** (monthly plans).
- **Persisted reservations** for crash recovery (the DB counter is durable; the
  in-flight `Reservation` object is not).
- **Distributed deployment** — Postgres/Redis for multi-instance atomicity.

## 11. Test coverage (26 tests)

Pure credit math (incl. the float bug), repository (incl. the 10-thread concurrency
race), the metering core (success, quota rejection without calling the AI, both failure
modes, per-user multipliers, in-flight multiplier snapshot, and a controlled overage when
actual usage exceeds the estimate), and API integration (full flow, 404/402/502 with
structured bodies, exact-Decimal billing through JSON). Run with `uv run pytest -q`.
