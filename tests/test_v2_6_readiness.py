"""Isolated regression tests. All provider responses here are protocol mocks."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from test_server import bearer, central, login, register  # noqa: F401
from test_transfers import create, relay  # noqa: F401
from test_v2_2_ai import response
from test_v2_2_archives import archive  # noqa: F401
from test_v2_3_library import archived

from filemind_server.ai import AIError, AISettings, DeepSeekProvider
from filemind_server.ai_control import save_config


def test_paid_runner_default_does_not_touch_vault_or_network():
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts/verify_deepseek_live_v2_6.py")],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0 and "No API request sent" in result.stdout
    assert '"max_requests": 20' in result.stdout and '"budget_cny": 4.0' in result.stdout


@pytest.mark.parametrize("model", ["other-model", None])
def test_provider_requires_observed_requested_model_and_keeps_usage(model):
    provider = DeepSeekProvider(
        "synthetic", httpx.MockTransport(lambda r: httpx.Response(200, json=response(model=model)))
    )
    with pytest.raises(AIError, match="MODEL_RESPONSE_MISMATCH") as caught:
        provider.complete({}, AISettings(retries=0))
    assert caught.value.uncertain and caught.value.response.input_tokens == 200


def test_truncation_usage_is_not_lost_in_planner(central):  # noqa: F811
    c, app, _ = central
    user = register(c)
    app.state.settings.allow_paid_ai = True
    app.state.ai.keys.get = lambda: "synthetic"
    save_config(
        app.state.db,
        "ai_v2_2",
        AISettings(
            retries=0,
            monthly_budget=1,
            request_limit=0.1,
            input_per_million=2,
            output_per_million=8,
            price_version="synthetic-test",
        ),
    )
    data = response()
    data["choices"][0]["finish_reason"] = "length"
    app.state.ai.provider_override = DeepSeekProvider(
        "synthetic", httpx.MockTransport(lambda r: httpx.Response(200, json=data))
    )
    with pytest.raises(AIError, match="OUTPUT_TRUNCATED"):
        app.state.ai.plan("truncated", user["id"], {"filename": "notes.txt"})
    with app.state.db.transaction() as conn:
        usage = conn.execute("SELECT * FROM ai_usage_records WHERE id='truncated'").fetchone()
    assert usage["status"] == "UNKNOWN" and usage["reserved_cost"] > 0
    assert usage["input_tokens"] == 200 and usage["output_tokens"] == 100
    assert usage["estimated_cost"] == pytest.approx(0.0012)


def test_mock_and_configuration_never_imply_live_acceptance(archive):  # noqa: F811
    _, app, _, user = archive
    ai = app.state.ai
    ai.keys.get = lambda: "synthetic"
    ai.plan("mock-only", user["id"], {"filename": "notes.txt"})
    status = ai.acceptance_status()
    assert status["states"]["API_KEY_CONFIGURED"] == "CONFIGURED"
    assert all(v == "PENDING" for k, v in status["states"].items() if k != "API_KEY_CONFIGURED")
    assert not status["production_verified"]


def test_acceptance_counts_only_real_noncache_model_scoped_records(central):  # noqa: F811
    c, app, _ = central
    user = register(c)
    ai = app.state.ai
    app.state.settings.allow_paid_ai = True
    ai.keys.get = lambda: "synthetic"
    cfg = AISettings(
        retries=0,
        monthly_budget=1,
        request_limit=0.1,
        input_per_million=2,
        output_per_million=8,
        price_version="synthetic-test",
    )
    save_config(app.state.db, "ai_v2_2", cfg)
    ai.provider_override = DeepSeekProvider(
        "synthetic", httpx.MockTransport(lambda r: httpx.Response(200, json=response()))
    )
    for task in ("first", "cached"):
        ai.plan(task, user["id"], {"filename": "notes.txt"})
    status = ai.acceptance_status()
    assert status["live_classifications"] == 1
    assert status["counts"] == {"tasks": 2, "provider_attempts": 1, "cache_reuses": 1, "errors": 0}
    assert status["states"]["LIVE_ARCHIVE_TESTED"] == "PENDING"
    cfg.model = "other-model"
    save_config(app.state.db, "ai_v2_2", cfg)
    assert ai.acceptance_status()["live_classifications"] == 0


def test_model_list_is_free_bounded_and_failure_replaces_old_success(central, monkeypatch):  # noqa: F811
    _, app, _ = central
    app.state.ai.keys.get = lambda: "synthetic-secret-never-returned"
    statuses = [200, 401]
    seen = []

    def handler(request):
        seen.append((request.method, str(request.url)))
        return httpx.Response(statuses.pop(0), json={"data": [{"id": "deepseek-flash"}]})

    client = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: client(transport=httpx.MockTransport(handler), **kw)
    )
    assert not app.state.settings.allow_paid_ai
    assert app.state.ai.test_connection()["available"]
    assert app.state.ai.acceptance_status()["states"]["API_MODEL_AVAILABLE"] == "VERIFIED"
    public = app.state.ai.acceptance_status()
    assert "_key_binding" not in json.dumps(public)
    app.state.ai.keys.get = lambda: "synthetic-rotated-while-request-was-running"
    assert not app.state.ai.acceptance_status()["preflight_current"]
    assert app.state.ai.acceptance_status()["states"]["API_AUTHENTICATION_VERIFIED"] == "PENDING"
    app.state.ai.keys.get = lambda: "synthetic-secret-never-returned"
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        app.state.ai.test_connection()
    assert app.state.ai.acceptance_status()["states"]["API_AUTHENTICATION_VERIFIED"] == "PENDING"
    assert seen == [("GET", "https://api.deepseek.com/models")] * 2
    with app.state.db.transaction() as conn:
        assert (
            "synthetic-secret"
            not in conn.execute(
                "SELECT value FROM platform_settings WHERE key='deepseek_model_preflight'"
            ).fetchone()[0]
        )


def test_old_model_list_proof_is_stale_and_acceptance_requires_admin(central):  # noqa: F811
    c, app, _ = central
    register(c)
    user = bearer(login(c))
    assert c.get("/api/admin/ai/acceptance", headers=user).status_code == 403
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "INSERT INTO platform_settings VALUES (?,?)",
            (
                "deepseek_model_preflight",
                json.dumps(
                    {
                        "model": "deepseek-flash",
                        "checked_at": time.time() - 86401,
                        "authenticated": True,
                        "available": True,
                    }
                ),
            ),
        )
    admin = bearer(login(c, "admin_test", admin=True))
    report = c.get("/api/admin/ai/acceptance", headers=admin)
    assert report.is_success
    assert report.json()["states"]["API_AUTHENTICATION_VERIFIED"] == "PENDING"


def test_relay_can_stop_new_sessions_without_hiding_existing_tasks(relay):  # noqa: F811
    c, app, devices, account = relay
    job, body = create(c, *devices[:2])
    app.state.settings.relay_enabled = False
    headers = devices[0]["headers"]
    assert not c.get("/api/transfers/config", headers=headers).json()["enabled"]
    assert c.post("/api/transfers", headers=headers, json=body).status_code == 503
    assert c.get("/api/transfers/" + job["id"], headers=headers).is_success
    assert c.get("/api/library", headers=account).is_success


def test_admin_operational_endpoints_do_not_leak_private_ai_text(archive):  # noqa: F811
    from filemind_server.accounts import create_account
    from filemind_server.models import Registration

    c, app, owner, _ = archive
    item = archived(archive)
    sentinel = "SYNTHETIC_PRIVATE_TEXT_ONLY_OWNER_MAY_READ"
    with app.state.db.transaction(True) as conn:
        create_account(
            conn,
            Registration(
                username="privacy_admin",
                display_name="Synthetic",
                password="Synthetic-admin-password",
            ),
            "system_admin",
        )
        plan = json.loads(
            conn.execute(
                "SELECT plan_json FROM archive_jobs WHERE id=?", (item["version_id"],)
            ).fetchone()[0]
        )
        plan.update(summary=sentinel, reason=sentinel, keywords=[sentinel])
        conn.execute(
            "UPDATE archive_jobs SET plan_json=? WHERE id=?", (json.dumps(plan), item["version_id"])
        )
        conn.execute(
            "UPDATE file_versions SET summary=? WHERE id=?", (sentinel, item["version_id"])
        )
    session = c.post(
        "/api/admin/auth/login",
        json={"username": "privacy_admin", "password": "Synthetic-admin-password"},
    )
    assert session.is_success
    admin = bearer(session.json())
    for path in ("/api/admin/archive/jobs", "/api/admin/library", "/api/admin/ai/usage"):
        result = c.get(path, headers=admin)
        assert result.is_success and sentinel not in result.text
    own = c.get("/api/archive/jobs/" + item["version_id"], headers=owner)
    assert sentinel in own.text
    assert c.get(
        "/api/library/versions/" + item["version_id"] + "/content", headers=admin
    ).status_code in (401, 403, 404)
    with app.state.db.transaction(True) as conn:
        conn.execute(
            "UPDATE archive_jobs SET state='FAILED',error=? WHERE id=?",
            ("ValueError:" + sentinel, item["version_id"]),
        )
    retried = c.post("/api/admin/archive/jobs/" + item["version_id"] + "/retry", headers=admin)
    assert retried.is_success and sentinel not in retried.text
