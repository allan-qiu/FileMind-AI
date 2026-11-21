import json

import httpx
import pytest
from test_server import bearer, central, login, register  # noqa: F401

from filemind_server.ai import (
    EXAMPLE,
    AIError,
    AISettings,
    DeepSeekProvider,
    estimate_cost,
    validate_plan,
)
from filemind_server.ai_control import save_config
from filemind_server.extraction import extract_excerpt
from filemind_server.key_store import KeyStore


def response(content=None, **changes):
    return {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps(EXAMPLE, ensure_ascii=False)
                    if content is None
                    else content
                },
            }
        ],
        "usage": {"prompt_tokens": 200, "completion_tokens": 100},
        "model": "deepseek-flash",
        **changes,
    }


def test_deepseek_official_json_protocol_and_model_switch():
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.url == "https://api.deepseek.com/chat/completions"
        assert body["response_format"] == {"type": "json_object"}
        assert "JSON" in body["messages"][0]["content"]
        return httpx.Response(200, json=response(model=body["model"]))

    provider = DeepSeekProvider("synthetic-key", httpx.MockTransport(handler))
    for model in ("deepseek-flash", "configured-model"):
        reply = provider.complete({"filename": "notes.txt"}, AISettings(model=model))
        assert reply.input_tokens == 200 and not reply.simulated
    assert [b["model"] for b in seen] == ["deepseek-flash", "configured-model"]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "AUTH_FAILED"),
        (402, "INSUFFICIENT_BALANCE"),
        (429, "RATE_LIMIT"),
        (404, "MODEL_UNAVAILABLE"),
        (503, "PROVIDER_ERROR"),
    ],
)
def test_provider_http_failures(status, code):
    provider = DeepSeekProvider(
        "synthetic-key", httpx.MockTransport(lambda r: httpx.Response(status))
    )
    with pytest.raises(AIError, match=code):
        provider.complete({}, AISettings(retries=0))


@pytest.mark.parametrize(
    "value",
    [
        response(""),
        response("{"),
        response(choices=[{"finish_reason": "length", "message": {"content": "{}"}}]),
        {},
    ],
)
def test_empty_truncated_and_malformed(value):
    provider = DeepSeekProvider(
        "synthetic-key", httpx.MockTransport(lambda r: httpx.Response(200, json=value))
    )
    if value == response("{"):
        result = provider.complete({}, AISettings())
        with pytest.raises(ValueError):
            validate_plan(result.content, "test.txt", AISettings().categories)
    else:
        with pytest.raises(AIError):
            provider.complete({}, AISettings())


def test_timeout_is_uncertain_and_no_blind_retry():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("synthetic", request=request)

    with pytest.raises(AIError) as info:
        DeepSeekProvider("synthetic-key", httpx.MockTransport(handler)).complete(
            {}, AISettings(retries=3)
        )
    assert info.value.uncertain and len(calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"suggested_filename": "../bad.txt"},
        {"suggested_filename": "CON.txt"},
        {"suggested_filename": "changed.exe"},
        {"target_directory": "学习资料/../x"},
        {"category": "不存在"},
        {"target_directory": "其他/Python"},
        {"related_file_ids": ["foreign-file"]},
        {"needs_user_review": "false"},
    ],
)
def test_structured_business_validation(changes):
    with pytest.raises(ValueError):
        validate_plan({**EXAMPLE, **changes}, "original.txt", AISettings().categories)


def test_required_fields_and_valid_directory():
    value = {**EXAMPLE}
    del value["reason"]
    with pytest.raises(ValueError):
        validate_plan(value, "original.txt", AISettings().categories)
    assert (
        validate_plan(EXAMPLE, "original.txt", AISettings().categories).target_directory
        == "学习资料/Python"
    )


def test_consent_metadata_never_reads_private_text(tmp_path, monkeypatch):
    path = tmp_path / "private.txt"
    path.write_text("SYNTHETIC_PRIVATE_CONTENT", encoding="utf-8")
    assert extract_excerpt(path, path.name, "metadata") == ("", "metadata_only")
    assert "SYNTHETIC_PRIVATE" in extract_excerpt(path, path.name, "summary")[0]
    path.unlink()
    assert extract_excerpt(path, path.name, "metadata")[0] == ""


def test_key_vault_roundtrip_no_plaintext_on_windows(tmp_path):
    key = KeyStore(tmp_path)
    key.set("synthetic-v2-2-key")
    assert key.get() == "synthetic-v2-2-key"
    import os

    if os.name == "nt":
        assert b"synthetic-v2-2-key" not in key.path.read_bytes()


def test_admin_key_masking_and_access_control(central):  # noqa: F811
    client, app, _ = central
    register(client)
    account = bearer(login(client))
    assert client.get("/api/admin/ai", headers=account).status_code == 403
    admin = bearer(login(client, "admin_test", admin=True))
    response = client.put(
        "/api/admin/ai",
        headers=admin,
        json={
            "settings": AISettings(provider="mock").model_dump(),
            "api_key": "synthetic-server-secret",
        },
    )
    assert response.is_success and response.json()["key_configured"]
    assert "synthetic-server-secret" not in response.text
    assert client.post("/api/admin/ai/test", headers=admin).status_code == 409
    with app.state.db.transaction() as conn:
        assert "synthetic-server-secret" not in str(
            [tuple(r) for r in conn.execute("SELECT * FROM audit_logs")]
        )


def test_usage_cache_budget_and_crash_recovery(central):  # noqa: F811
    client, app, _ = central
    user = register(client)
    ai = app.state.ai
    save_config(app.state.db, "ai_v2_2", AISettings(provider="mock"))
    payload = {"filename": "synthetic.txt"}
    first = ai.plan("one-job", user["id"], payload)
    assert ai.plan("one-job", user["id"], payload) == first
    with app.state.db.transaction() as conn:
        rows = conn.execute("SELECT * FROM ai_usage_records").fetchall()
    assert len(rows) == 1 and rows[0]["simulated"] and rows[0]["estimated_cost"] == 0
    save_config(app.state.db, "ai_v2_2", AISettings())
    with pytest.raises(AIError, match="NOT_AUTHORIZED"):
        ai.plan("real-job", user["id"], payload)
    assert (
        estimate_cost(1_000_000, 500_000, AISettings(input_per_million=1, output_per_million=2))
        == 2
    )
