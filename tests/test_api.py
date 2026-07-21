import pytest
from fastapi.testclient import TestClient

from app.generation import GenerationError, MockGenerator
from app.main import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "api.db"), generator=MockGenerator(completion_tokens=60))
    return TestClient(app)


def test_full_flow_config_generate_usage_records(client):
    r = client.put("/users/alice/config", json={"quota_credits": 1000, "credit_multiplier": "1.5"})
    assert r.status_code == 200

    r = client.post("/users/alice/generations", json={"prompt": "x" * 160, "max_tokens": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["usage"]["credits_charged"] == 150   # 100 tokens x 1.5
    assert body["quota"]["remaining"] == 850

    assert client.get("/users/alice/usage").json()["quota"]["used"] == 150
    assert len(client.get("/users/alice/usage/records").json()) == 1


def test_generate_unconfigured_user_404(client):
    r = client.post("/users/ghost/generations", json={"prompt": "hi", "max_tokens": 10})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "user_configuration_not_found"


def test_quota_exceeded_returns_402_with_details(client):
    client.put("/users/bob/config", json={"quota_credits": 100, "credit_multiplier": "1.5"})
    r = client.post("/users/bob/generations", json={"prompt": "x" * 160, "max_tokens": 100})
    assert r.status_code == 402
    err = r.json()["error"]
    assert err["code"] == "quota_exceeded"
    assert err["remaining"] == 100 and err["estimated_credits"] == 210


def test_provider_failure_returns_502(tmp_path):
    class Boom:
        def generate(self, prompt, max_tokens):
            raise GenerationError("provider down")

    client = TestClient(create_app(db_path=str(tmp_path / "f.db"), generator=Boom()))
    client.put("/users/z/config", json={"quota_credits": 1000, "credit_multiplier": "1.0"})
    r = client.post("/users/z/generations", json={"prompt": "x" * 40, "max_tokens": 50})
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "generation_provider_error"


def test_decimal_multiplier_bills_exactly_end_to_end(client):
    # 1.1 sent as a JSON number must still bill exactly (no float error): 100 x 1.1 = 110
    client.put("/users/c/config", json={"quota_credits": 100000, "credit_multiplier": 1.1})
    r = client.post("/users/c/generations", json={"prompt": "x" * 160, "max_tokens": 100})
    assert r.json()["usage"]["credits_charged"] == 110
