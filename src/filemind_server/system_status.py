"""Read-only operational facts, independent from external acceptance claims."""

import time


def system_status(app):
    db, archives, ai = app.state.db, app.state.archives, app.state.ai
    cfg = ai.config()
    with db.transaction() as conn:
        jobs = {
            r[0]: r[1]
            for r in conn.execute("SELECT state,count(*) FROM archive_jobs GROUP BY state")
        }
        usage = [
            dict(r)
            for r in conn.execute(
                "SELECT simulated,currency,count(*) requests,coalesce(sum(input_tokens),0) "
                "input_tokens,coalesce(sum(output_tokens),0) output_tokens,"
                "coalesce(sum(estimated_cost),0) estimated_cost FROM ai_usage_records "
                "GROUP BY simulated,currency"
            )
        ]
        last = conn.execute(
            "SELECT status,error,simulated,finished_at FROM ai_usage_records "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        counts = {
            k: conn.execute(sql).fetchone()[0]
            for k, sql in {
                "library_files": "SELECT count(*) FROM global_files",
                "library_versions": "SELECT count(*) FROM file_versions",
                "library_download_bytes": "SELECT coalesce(sum(bytes_sent),0) "
                "FROM library_download_tasks",
                "share_links": "SELECT count(*) FROM share_links",
                "completed_share_downloads": "SELECT coalesce(sum(completed_downloads),0) "
                "FROM share_links",
                "share_wire_bytes": "SELECT coalesce(sum(wire_attempt_bytes),0) "
                "FROM share_daily_traffic",
                "temporary_reserved_bytes": "SELECT coalesce(sum(reserved_bytes),0) "
                "FROM ingest_sessions WHERE state!='CLEANED'",
                "temporary_share_bytes": "SELECT coalesce(sum(file_size),0) "
                "FROM temporary_share_objects WHERE deletion_status='LIVE'",
                "active_transfers": "SELECT count(*) FROM transfer_sessions WHERE "
                "state NOT IN ('COMPLETED','FAILED','CANCELLED','EXPIRED','REJECTED')",
            }.items()
        }
        database = {
            "read_query": "passed",
            "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
            "schema": conn.execute("PRAGMA user_version").fetchone()[0],
            "busy_timeout_ms": 5000,
        }
    configured = bool(ai.keys.get())
    acceptance = ai.acceptance_status()
    return {
        "checked_at": time.time(),
        "api": "running",
        "devicelink": "running" if app.state.settings.relay_enabled else "disabled",
        "ai": {
            "provider": cfg.provider,
            "model": cfg.model,
            "key_configured": configured,
            "paid_calls_enabled": app.state.settings.allow_paid_ai,
            "configuration_status": "已配置" if configured else "未配置",
            "connection_status": acceptance["states"]["API_AUTHENTICATION_VERIFIED"],
            "validation": acceptance,
            "production_verified": False,
            "selected_mode": "Mock 模拟" if cfg.provider == "mock" else "真实接口（需授权）",
            "last_request": dict(last) if last else None,
            "usage": usage,
            "billing": "估算，非服务商账单",
        },
        "baidu": archives.provider("baidu").status(),
        "storage": {
            "write_provider": archives.config().provider,
            "local": "本地真实文件适配器",
            "read_cache_budget_bytes": archives.reads.budget,
            "read_cache_reserved_bytes": archives.reads.reserved,
        },
        "archive_jobs": jobs,
        "archive_failures": jobs.get("FAILED", 0),
        "archive_total": sum(jobs.values()),
        "archive_failure_rate": jobs.get("FAILED", 0) / max(1, sum(jobs.values())),
        "archive_worker_enabled": app.state.settings.archive_worker_enabled,
        "archive_worker_errors": app.state.archive_worker_errors,
        "share_worker_errors": app.state.share_worker_errors,
        "database": database,
        "counts": counts,
        "acceptance": {
            "deepseek_live": acceptance["states"]["LIVE_CLASSIFICATION_TESTED"],
            "baidu_live": "未验证",
            "ios_physical": "未验证",
            "android_physical": "未验证",
            "public_https": "未验证",
            "production": "未验证",
        },
    }
