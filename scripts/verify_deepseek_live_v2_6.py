"""Explicitly approved, bounded acceptance using the EXISTING server vault.

Default invocation is offline. Never copy the key to the isolated acceptance DB.
The fixed ledger is retained across restarts; UNKNOWN attempts consume the cap.
"""

import argparse
import hashlib
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from synthetic_acceptance_files import create

from filemind_server.ai import AISettings, DeepSeekProvider
from filemind_server.ai_control import save_config
from filemind_server.app import create_app
from filemind_server.config import Settings
from filemind_server.key_store import KeyStore
from filemind_server.service_lock import ServiceLock

ROOT = Path(__file__).resolve().parents[1]
PLAN = {
    "model": "deepseek-flash",
    "base_url": DeepSeekProvider.base_url,
    "max_requests": 20,
    "planned_requests": 8,
    "budget_cny": 4.0,
    "max_output_tokens_per_request": 1024,
    "retries": 0,
    "concurrency": 1,
    "input": "authored synthetic files only; PNG metadata only, no vision upload",
    "storage": "isolated LocalStorageProvider; original server DB/config/key unchanged",
    "price": "CNY per million tokens: input 2, output 8; conservative peak uncached",
    "status": "PENDING_EXPLICIT_APPROVAL",
}


def run():
    root = ROOT / ".artifacts" / "v26-deepseek-live"
    # Read only the existing vault, before creating the isolated runtime.
    key = KeyStore(Settings.from_env().data_dir).get()
    if not key:
        raise SystemExit("NOT_CONFIGURED")
    root.mkdir(parents=True, exist_ok=True)
    with ServiceLock(root / "acceptance.lock"):
        manifest_path = root / "synthetic-manifest.json"
        previous_report = root / "report.json"
        if manifest_path.exists():
            names = json.loads(manifest_path.read_text(encoding="utf-8"))
            fixtures = [(root / "synthetic" / name, "") for name in names]
        elif previous_report.exists():
            names = [
                r["filename"]
                for r in json.loads(previous_report.read_text(encoding="utf-8"))["cases"]
            ]
            if len(names) != 8:
                raise SystemExit("Incomplete legacy fixture manifest; manual review required.")
            fixtures = [(root / "synthetic" / name, "") for name in names]
        else:
            if (root / "synthetic").exists():
                raise SystemExit("Existing unrecorded fixtures; manual review required.")
            fixtures = create(root / "synthetic")
        manifest_path.write_text(
            json.dumps([p.name for p, _ in fixtures], ensure_ascii=False), encoding="utf-8"
        )
        app = create_app(
            Settings(data_dir=root / "central", allow_paid_ai=True, archive_worker_enabled=False)
        )
        # A closure reads the existing credential; no key setter or vault copy is used.
        app.state.ai.keys.get = lambda: key
        settings = AISettings(
            retries=0,
            monthly_budget=4,
            request_limit=0.20,
            input_per_million=2,
            output_per_million=8,
            price_version="official-2026-10-31-peak-upper",
            max_tokens=1024,
        )
        report = {
            **PLAN,
            "status": "RUNNING",
            "started_at": time.time(),
            "cases": [],
            "real_baidu": False,
            "production": False,
            "simulated": False,
        }
        if previous_report.exists():
            report = json.loads(previous_report.read_text(encoding="utf-8"))
        with TestClient(app, base_url="http://127.0.0.1") as client:
            save_config(app.state.db, "ai_v2_2", settings)
            app.state.ai.test_connection()  # GET /models only; no classification charge.
            credentials = {"username": "v26_synthetic", "password": "Synthetic-V26-only-2026"}
            client.post("/api/auth/register", json={**credentials, "display_name": "Synthetic"})
            auth = client.post("/api/auth/login", json=credentials)
            auth.raise_for_status()
            headers = {"Authorization": "Bearer " + auth.json()["access_token"]}
            client.put(
                "/api/archive/preferences",
                headers=headers,
                json={"auto_archive": False, "ai_mode": "summary", "rules": ""},
            ).raise_for_status()
            for index, (path, _) in enumerate(fixtures):
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                created = client.post(
                    "/api/archive/jobs",
                    headers=headers,
                    json={
                        "filename": path.name,
                        "size_bytes": path.stat().st_size,
                        "sha256": digest,
                        "idempotency_key": f"v26-approved-synthetic-{index}",
                    },
                )
                created.raise_for_status()
                job = created.json()
                with app.state.db.transaction() as conn:
                    attempts = conn.execute("SELECT count(*) FROM ai_usage_records").fetchone()[0]
                    prior = conn.execute(
                        "SELECT id FROM ai_usage_records WHERE id=?", (job["id"],)
                    ).fetchone()
                # Never reissue any previously attempted request, including UNKNOWN outcomes.
                if prior or attempts >= 20:
                    continue
                if job["state"] == "WAITING_UPLOAD":
                    lease = client.post(f"/api/archive/jobs/{job['id']}/claim", headers=headers)
                    lease.raise_for_status()
                    upload = {**headers, "X-Archive-Lease": lease.json()["lease_token"]}
                    client.put(
                        f"/api/archive/jobs/{job['id']}/chunks/0",
                        headers=upload,
                        content=path.read_bytes(),
                    ).raise_for_status()
                    client.post(
                        f"/api/archive/jobs/{job['id']}/finish", headers=upload
                    ).raise_for_status()
                began = time.perf_counter()
                app.state.archives.process_one(job["id"])
                final = app.state.archives.row(job["id"])
                with app.state.db.transaction() as conn:
                    usage = conn.execute(
                        "SELECT status,model,input_tokens,output_tokens,estimated_cost,"
                        "reserved_cost,error FROM ai_usage_records WHERE id=?",
                        (job["id"],),
                    ).fetchone()
                result = {
                    "filename": path.name,
                    "state": final["state"],
                    "seconds": round(time.perf_counter() - began, 4),
                    "usage": dict(usage) if usage else None,
                    "plan": json.loads(final["plan_json"]) if final["plan_json"] else None,
                }
                if final["state"] == "COMPLETED":
                    download = client.get(f"/api/library/{job['id']}/content", headers=headers)
                    result["sha256_matches"] = (
                        download.is_success
                        and hashlib.sha256(download.content).hexdigest() == digest
                    )
                report["cases"].append(result)
                (root / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    json.dumps(
                        {"filename": path.name, "state": final["state"]}, ensure_ascii=False
                    ),
                    flush=True,
                )
            report["acceptance"] = app.state.ai.acceptance_status()
            with app.state.db.transaction() as conn:
                report["ledger"] = dict(
                    conn.execute(
                        "SELECT count(*) attempts,coalesce(sum(input_tokens),0) input_tokens,"
                        "coalesce(sum(output_tokens),0) output_tokens,"
                        "coalesce(sum(estimated_cost),0) estimated_cost,"
                        "coalesce(sum(max(estimated_cost,reserved_cost)),0) budget_accounted "
                        "FROM ai_usage_records"
                    ).fetchone()
                )
            report["status"] = "FINISHED"
            (root / "report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print("Sanitized report: " + str(root / "report.json"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--approved", action="store_true")
    args = parser.parse_args()
    if args.approved:
        run()
    else:
        print(json.dumps(PLAN, ensure_ascii=False, indent=2))
        print("No API request sent. Explicit approval required before --approved.")
