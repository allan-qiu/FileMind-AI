import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from filemind_server.accounts import create_account
from filemind_server.app import create_app
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.models import Registration

PASSWORD = "local-test-password-123"


@pytest.fixture
def central(tmp_path):
    app = create_app(Settings(data_dir=tmp_path / "central"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        with app.state.db.transaction(write=True) as conn:
            admin = create_account(
                conn,
                Registration(username="admin_test", display_name="测试管理员", password=PASSWORD),
                "system_admin",
            )
        yield client, app, admin


def register(client, username="alice_test"):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": PASSWORD,
            "display_name": username,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def login(client, username="alice_test", admin=False):
    path = "/api/admin/auth/login" if admin else "/api/auth/login"
    response = client.post(path, json={"username": username, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


def bearer(data):
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_health_and_schema_are_real(central):
    client, app, _ = central
    assert client.get("/api/health").json()["status"] == "ok"
    app.state.db.migrate()
    with app.state.db.transaction() as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(
            list(
                (
                    __import__("pathlib").Path(__file__).parents[1]
                    / "src/filemind_server/migrations"
                ).glob("*.sql")
            )
        )
        assert conn.execute("SELECT count(*) FROM users").fetchone()[0] == 1


def test_registration_password_storage_and_mass_assignment(central):
    client, app, _ = central
    user = register(client)
    assert "password_hash" not in user
    with app.state.db.transaction() as conn:
        encoded = conn.execute(
            "SELECT password_hash FROM users WHERE id=?", (user["id"],)
        ).fetchone()[0]
    assert encoded.startswith("scrypt$") and PASSWORD not in encoded
    assert (
        client.post(
            "/api/auth/register",
            json={
                "username": "evil_test",
                "password": PASSWORD,
                "display_name": "x",
                "role": "system_admin",
            },
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "weak_test", "password": "short", "display_name": "x"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "alice_test", "password": PASSWORD, "display_name": "x"},
        ).status_code
        == 409
    )


def test_user_cannot_access_admin_or_admin_login(central):
    client, _, _ = central
    register(client)
    user = login(client)
    assert client.get("/api/admin/users", headers=bearer(user)).status_code == 403
    assert (
        client.post(
            "/api/admin/auth/login", json={"username": "alice_test", "password": PASSWORD}
        ).status_code
        == 403
    )


def test_csrf_origin_and_independent_admin_session(central):
    client, _, _ = central
    register(client)
    user = login(client)
    assert (
        "HttpOnly"
        in client.post(
            "/api/auth/login", json={"username": "alice_test", "password": PASSWORD}
        ).headers["set-cookie"]
    )
    assert client.post("/api/devices", json={"name": "x"}).status_code == 403
    admin = login(client, "admin_test", admin=True)
    assert client.get("/api/auth/session").json()["user"]["username"] == "alice_test"
    assert client.get("/api/admin/auth/session").json()["user"]["username"] == "admin_test"
    assert (
        client.post(
            "/api/devices",
            json={"name": "x"},
            headers={**bearer(user), "Origin": "https://untrusted.example"},
        ).status_code
        == 403
    )
    assert client.get("/api/admin/users", headers=bearer(admin)).status_code == 200


def test_device_ownership_and_revocation(central):
    client, _, _ = central
    register(client)
    alice = login(client)
    device = client.post(
        "/api/devices", json={"name": "Win PC", "platform": "windows"}, headers=bearer(alice)
    ).json()
    device_id = device["device"]["id"]
    device_headers = {"Authorization": "Bearer " + device["device_token"]}
    assert (
        client.post(f"/api/devices/{device_id}/heartbeat", headers=device_headers).status_code
        == 200
    )
    assert (
        client.post(f"/api/devices/{device_id}/heartbeat", headers=bearer(alice)).status_code == 403
    )
    register(client, "bob_test")
    bob = login(client, "bob_test")
    assert client.get("/api/devices", headers=bearer(bob)).json() == []
    assert client.delete(f"/api/devices/{device_id}", headers=bearer(bob)).status_code == 404
    assert client.delete(f"/api/devices/{device_id}", headers=bearer(alice)).status_code == 200
    assert (
        client.post(f"/api/devices/{device_id}/heartbeat", headers=device_headers).status_code
        == 401
    )
    assert client.get("/api/auth/session", headers=device_headers).status_code == 401


def test_disable_user_invalidates_existing_credentials_and_audits(central):
    client, _, _ = central
    user = register(client)
    session = login(client)
    admin = login(client, "admin_test", admin=True)
    assert (
        client.patch(
            f"/api/admin/users/{user['id']}", json={"enabled": False}, headers=bearer(admin)
        ).status_code
        == 200
    )
    assert client.get("/api/auth/session", headers=bearer(session)).status_code == 401
    logs = client.get("/api/admin/audit", headers=bearer(admin)).json()
    assert any(row["action"] == "admin.user.updated" for row in logs)
    assert PASSWORD not in str(logs)


def test_last_admin_is_protected_and_stats_have_no_fake_modules(central):
    client, _, admin_user = central
    admin = login(client, "admin_test", admin=True)
    assert (
        client.patch(
            f"/api/admin/users/{admin_user['id']}", json={"role": "user"}, headers=bearer(admin)
        ).status_code
        == 409
    )
    stats = client.get("/api/admin/overview", headers=bearer(admin)).json()
    assert stats["users"] == 1
    assert stats["devices"] == 0
    assert stats["process_rss_bytes"] > 0
    assert stats["services"]["devicelink"] == "running"


def test_workspace_isolation_including_system_admin(central):
    client, _, _ = central
    register(client)
    alice = login(client)
    spaces = client.get("/api/workspaces", headers=bearer(alice)).json()
    workspace_id = spaces[0]["id"]
    register(client, "bob_test")
    bob = login(client, "bob_test")
    assert (
        client.get(f"/api/workspaces/{workspace_id}/members", headers=bearer(bob)).status_code
        == 404
    )
    admin_user_session = login(client, "admin_test")
    assert (
        client.get(
            f"/api/workspaces/{workspace_id}/members", headers=bearer(admin_user_session)
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"username": "bob_test", "permission": "viewer"},
            headers=bearer(alice),
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/workspaces/{workspace_id}/members", headers=bearer(bob)).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/workspaces/{workspace_id}/members",
            json={"username": "admin_test", "permission": "admin"},
            headers=bearer(bob),
        ).status_code
        == 403
    )


def test_registration_switch_and_session_logout(central):
    client, _, _ = central
    admin = login(client, "admin_test", admin=True)
    assert (
        client.put(
            "/api/admin/settings",
            json={"registration_open": False, "announcement": "本地测试"},
            headers=bearer(admin),
        ).status_code
        == 200
    )
    assert client.get("/api/public/config").json()["announcement"] == "本地测试"
    assert (
        client.post(
            "/api/auth/register",
            json={"username": "closed_test", "password": PASSWORD, "display_name": "x"},
        ).status_code
        == 403
    )
    assert client.post("/api/admin/auth/logout", headers=bearer(admin)).status_code == 200
    assert client.get("/api/admin/overview", headers=bearer(admin)).status_code == 401


def test_expired_sessions_fail_and_login_is_limited(central):
    client, app, _ = central
    register(client)
    data = login(client)
    with app.state.db.transaction(write=True) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at=?", (int(time.time()) - 1,))
    assert client.get("/api/auth/session", headers=bearer(data)).status_code == 401
    for _ in range(10):
        assert (
            client.post(
                "/api/auth/login", json={"username": "unknown_test", "password": "incorrect"}
            ).status_code
            == 401
        )
    assert (
        client.post(
            "/api/auth/login", json={"username": "unknown_test", "password": "incorrect"}
        ).status_code
        == 429
    )


def test_future_schema_is_preserved(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="拒绝降级"):
        CentralDatabase(path).migrate()
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 999


def test_javascript_mime_is_independent_of_windows_registry(tmp_path):
    assets = tmp_path / "web" / "assets"
    assets.mkdir(parents=True)
    (assets / "main.js").write_text("console.log('local-test')", encoding="utf-8")
    app = create_app(Settings(data_dir=tmp_path / "data", web_dist=assets.parent))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/assets/main.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        assert response.headers["x-content-type-options"] == "nosniff"


def test_validation_does_not_echo_secret_input(central):
    client, _, _ = central
    secret = "sensitive-input-too-short"
    response = client.post("/api/auth/login", json={"username": "!", "password": secret})
    assert response.status_code == 422
    assert secret not in response.text


def test_migration_checksum_mismatch_never_rewrites_existing_schema(tmp_path):
    db = CentralDatabase(tmp_path / "central.sqlite3")
    db.migrate()
    with db.transaction(write=True) as conn:
        conn.execute("UPDATE schema_migrations SET checksum='altered'")
    with pytest.raises(ValueError, match="校验和"):
        db.migrate()
    with db.transaction() as conn:
        assert conn.execute("SELECT checksum FROM schema_migrations").fetchone()[0] == "altered"


def test_workspace_admin_only_manages_authorized_space(central):
    client, app, _ = central
    register(client)
    alice = login(client)
    space = client.get("/api/workspaces", headers=bearer(alice)).json()[0]["id"]
    bob_user = register(client, "bob_test")
    with app.state.db.transaction(write=True) as conn:
        conn.execute("UPDATE users SET role='workspace_admin' WHERE id=?", (bob_user["id"],))
    bob = login(client, "bob_test")
    assert (
        client.post(
            f"/api/workspaces/{space}/members",
            json={"username": "bob_test", "permission": "admin"},
            headers=bearer(alice),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/workspaces/{space}/members",
            json={"username": "admin_test", "permission": "viewer"},
            headers=bearer(bob),
        ).status_code
        == 200
    )
    assert (
        client.delete(
            f"/api/workspaces/{space}/members/{bob_user['id']}", headers=bearer(alice)
        ).status_code
        == 200
    )
    assert client.get(f"/api/workspaces/{space}/members", headers=bearer(bob)).status_code == 404


def test_concurrent_admin_disabling_preserves_one_active_admin(central):
    from concurrent.futures import ThreadPoolExecutor

    client, app, first = central
    with app.state.db.transaction(write=True) as conn:
        second = create_account(
            conn,
            Registration(username="second_admin", display_name="second", password=PASSWORD),
            "system_admin",
        )
    first_session = login(client, "admin_test", admin=True)
    second_session = login(client, "second_admin", admin=True)

    def disable(pair):
        user, session = pair
        return client.patch(
            f"/api/admin/users/{user['id']}", json={"enabled": False}, headers=bearer(session)
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = list(executor.map(disable, [(first, first_session), (second, second_session)]))
    assert sorted(statuses) == [200, 409]
    with app.state.db.transaction() as conn:
        assert (
            conn.execute(
                "SELECT count(*) FROM users WHERE enabled=1 AND role='system_admin'"
            ).fetchone()[0]
            == 1
        )
