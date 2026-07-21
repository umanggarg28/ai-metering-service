# AI Usage Metering & Quota Service

A small FastAPI service that fronts an AI text-generation API and meters usage
against **per-user credit quotas** — like a prepaid plan. Each user has a quota
(credit allowance) and a multiplier (credit cost per token). The service decides
whether a request is affordable *before* generation, then charges the *actual*
usage *after*.

See **[DESIGN.md](DESIGN.md)** for architecture, tradeoffs, the quota/credit model,
failure handling, and a worked numeric example.

## Requirements

- Python 3.12+
- [`uv`](https://docs.astral.sh/uv/) (dependency + venv manager)

## Setup

```bash
uv sync
```

## Run the service

```bash
uv run uvicorn app.main:app --reload
# API docs at http://127.0.0.1:8000/docs
```

## Run the tests

```bash
uv run pytest -q
```

## API

| Method | Path | Purpose |
|---|---|---|
| `PUT`  | `/users/{user_id}/config` | Create/update a user's quota + multiplier |
| `POST` | `/users/{user_id}/generations` | Generate text and meter usage |
| `GET`  | `/users/{user_id}/usage` | Current usage, remaining credits, config |
| `GET`  | `/users/{user_id}/usage/records` | Immutable usage history |

### Example

```bash
# 1. Configure a user: 1000 credits, 1.5x multiplier
curl -X PUT localhost:8000/users/alice/config \
  -H 'content-type: application/json' \
  -d '{"quota_credits": 1000, "credit_multiplier": 1.5}'

# 2. Generate
curl -X POST localhost:8000/users/alice/generations \
  -H 'content-type: application/json' \
  -d '{"prompt": "Explain reserve-then-settle in one line.", "max_tokens": 100}'
# -> { "text": "...", "usage": {..., "credits_charged": 150}, "quota": {"remaining": 850, ...} }

# 3. Inspect usage
curl localhost:8000/users/alice/usage
curl localhost:8000/users/alice/usage/records
```

### Response codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `402` | `quota_exceeded` — estimated cost exceeds remaining credits (AI not called) |
| `404` | `user_configuration_not_found` |
| `502` | `generation_provider_error` — the AI layer failed (reservation reconciled) |

Errors return a structured body: `{"error": {"code": "...", "message": "...", ...}}`.

## Notes

- The AI layer is a **deterministic mock** (`app/generation.py`) that returns realistic
  token usage. Swapping in a real provider means writing one class with the same
  `generate` signature.
- Storage is **SQLite** (a `metering.db` file, created on first run) behind a
  `Repository` interface — see DESIGN.md for why.
