"""Opt-in paid acceptance. At most 8 attempts, CNY 0.50 total, 0.05 per reservation.

Never automatically invoked by tests, release, benchmarks or independent review.
Uses only a user-confirmed NEW environment key, never the pre-existing server vault.
"""

import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from fastapi.testclient import TestClient
from synthetic_acceptance_files import create

from filemind_server.ai import AISettings, estimate_cost
from filemind_server.ai_control import save_config
from filemind_server.app import create_app
from filemind_server.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    parser.add_argument("--confirm-new-key", action="store_true")
    args = parser.parse_args()
    key = os.environ.get("FILEMIND_DEEPSEEK_API_KEY", "")
    if not args.approved or not args.confirm_new_key or not key:
        print(
            "BLOCKED: approval and a locally configured, newly rotated environment key required. "
            "No API request sent. Never paste a key into chat."
        )
        return
    root = ROOT / ".artifacts" / "v25-deepseek-live"
    fixtures = create(root / "synthetic")
    app = create_app(
        Settings(data_dir=root / "central", allow_paid_ai=True, archive_worker_enabled=False)
    )
    settings = AISettings(
        provider="deepseek",
        retries=0,
        monthly_budget=0.50,
        request_limit=0.05,
        input_per_million=2,
        output_per_million=8,
        price_version="official-2026-10-31-peak-upper",
        max_tokens=1024,
    )
    report = {
        "date": "2026-10-31",
        "scope": "user approved synthetic data only",
        "max_attempts": 8,
        "budget_cny": 0.50,
        "request_reservation_cny": 0.05,
        "old_key_used": False,
        "real_baidu": False,
        "production": False,
        "cases": [],
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        save_config(app.state.db, "ai_v2_2", settings)
        credentials = {"username": "v25_live_synthetic", "password": "Synthetic-v25-only-2026"}
        client.post("/api/auth/register", json={**credentials, "display_name": "Synthetic"})
        auth = client.post("/api/auth/login", json=credentials)
        auth.raise_for_status()
        headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
        # Only authored synthetic extracts; no user paths, accounts, directories or files.
        client.put(
            "/api/archive/preferences",
            headers=headers,
            json={"auto_archive": False, "ai_mode": "summary", "rules": ""},
        )
        for index, (path, _) in enumerate(fixtures):
            with app.state.db.transaction() as conn:
                attempts = conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0]
                reserved = conn.execute(
                    "SELECT coalesce(sum(max(estimated_cost,reserved_cost)),0) "
                    "FROM ai_usage_records"
                ).fetchone()[0]
            if attempts >= 8 or reserved >= 0.50:
                break
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            row = client.post(
                "/api/archive/jobs",
                headers=headers,
                json={
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "sha256": digest,
                    "idempotency_key": f"v25-approved-synthetic-{index}",
                },
            )
            row.raise_for_status()
            job = row.json()
            if job["state"] == "WAITING_UPLOAD":
                lease = client.post(f"/api/archive/jobs/{job['id']}/claim", headers=headers)
                lease.raise_for_status()
                upload_headers = {**headers, "X-Archive-Lease": lease.json()["lease_token"]}
                uploaded = client.put(
                    f"/api/archive/jobs/{job['id']}/chunks/0",
                    headers=upload_headers,
                    content=path.read_bytes(),
                )
                uploaded.raise_for_status()
                client.post(
                    f"/api/archive/jobs/{job['id']}/finish", headers=upload_headers
                ).raise_for_status()
            began = time.perf_counter()
            app.state.archives.process_one(job["id"])
            final = app.state.archives.row(job["id"])
            with app.state.db.transaction() as conn:
                usage = conn.execute(
                    "SELECT status,input_tokens,output_tokens,estimated_cost,"
                    "reserved_cost,error,model FROM ai_usage_records WHERE id=?",
                    (job["id"],),
                ).fetchone()
            result = {
                "filename": path.name,
                "state": final["state"],
                "response_seconds": round(time.perf_counter() - began, 4),
                "usage": dict(usage) if usage else None,
                "plan": json.loads(final["plan_json"]) if final["plan_json"] else None,
                "simulated": False,
            }
            if final["state"] == "COMPLETED":
                download = client.get(f"/api/library/{job['id']}/content", headers=headers)
                result["download_sha256_matches"] = (
                    download.is_success and hashlib.sha256(download.content).hexdigest() == digest
                )
            report["cases"].append(result)
            (root / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(json.dumps({"filename": path.name, "state": final["state"]}, ensure_ascii=False))
        report["conservative_max_output_cost_cny"] = estimate_cost(0, 8 * 1024, settings)
        (root / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("Sanitized report: " + str(root / "report.json"))


if __name__ == "__main__":
    main()
