import hashlib
import json
import time
from datetime import UTC, datetime

from fastapi import HTTPException

from filemind_server.ai import (
    AIError,
    AISettings,
    DeepSeekProvider,
    MockAIProvider,
    estimate_cost,
    validate_plan,
)
from filemind_server.key_store import KeyStore


def load_config(db, key, model):
    with db.transaction() as conn:
        row = conn.execute("SELECT value FROM platform_settings WHERE key=?", (key,)).fetchone()
    return model.model_validate_json(row[0]) if row else model()


def save_config(db, key, value):
    with db.transaction(True) as conn:
        conn.execute(
            "INSERT INTO platform_settings VALUES (?,?) ON CONFLICT(key) "
            "DO UPDATE SET value=excluded.value",
            (key, value.model_dump_json()),
        )


class ClassificationPlanner:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        self.keys = KeyStore(settings.data_dir)
        self.provider_override = None

    def config(self):
        return load_config(self.db, "ai_v2_2", AISettings)

    def public_config(self):
        return {
            **self.config().model_dump(),
            "key_configured": bool(self.keys.get()),
            "real_api_enabled": self.settings.allow_paid_ai,
        }

    def acceptance_status(self):
        """Local recorded evidence, scoped to the selected model; never makes a request."""
        cfg = self.config()
        with self.db.transaction() as conn:
            proof = conn.execute(
                "SELECT value FROM platform_settings WHERE key='deepseek_model_preflight'"
            ).fetchone()
            proof = json.loads(proof[0]) if proof else {}
            live = conn.execute(
                "SELECT count(*) requests,max(finished_at) last_success FROM ai_usage_records u "
                "LEFT JOIN ai_request_inputs i ON i.usage_id=u.id "
                "WHERE u.provider='deepseek' AND u.model=? AND u.simulated=0 "
                "AND u.status='COMPLETED' AND i.cache_source_id IS NULL",
                (cfg.model,),
            ).fetchone()
            archived = conn.execute(
                "SELECT count(*) total,max(j.completed_at) last_success FROM archive_jobs j "
                "JOIN ai_usage_records u ON u.id=j.id LEFT JOIN ai_request_inputs i "
                "ON i.usage_id=u.id WHERE j.state='COMPLETED' AND u.status='COMPLETED' "
                "AND u.provider='deepseek' AND u.model=? AND u.simulated=0 "
                "AND i.cache_source_id IS NULL",
                (cfg.model,),
            ).fetchone()
            counts = dict(
                conn.execute(
                    "SELECT count(*) tasks,coalesce(sum(CASE WHEN i.cache_source_id IS NULL "
                    "THEN 1 ELSE 0 END),0) provider_attempts,coalesce(sum(CASE WHEN "
                    "i.cache_source_id IS NOT NULL THEN 1 ELSE 0 END),0) cache_reuses,"
                    "coalesce(sum(CASE WHEN u.status IN ('FAILED','UNKNOWN') THEN 1 ELSE 0 END),0) "
                    "errors FROM ai_usage_records u LEFT JOIN ai_request_inputs i "
                    "ON i.usage_id=u.id "
                    "WHERE u.simulated=0 AND u.provider='deepseek' AND u.model=?",
                    (cfg.model,),
                ).fetchone()
            )
        key = self.keys.get()
        binding = proof.pop("_key_binding", None)
        current = (
            bool(key)
            and binding == hashlib.sha256(key.encode()).hexdigest()
            and proof.get("model") == cfg.model
            and 0 <= time.time() - proof.get("checked_at", 0) <= 86400
        )
        states = {
            "API_KEY_CONFIGURED": "CONFIGURED" if key else "NOT_CONFIGURED",
            "API_MODEL_AVAILABLE": "VERIFIED" if current and proof.get("available") else "PENDING",
            "API_AUTHENTICATION_VERIFIED": "VERIFIED"
            if current and proof.get("authenticated")
            else "PENDING",
            "LIVE_CLASSIFICATION_TESTED": "RECORDED_SUCCESS" if live["requests"] else "PENDING",
            "LIVE_ARCHIVE_TESTED": "RECORDED_SUCCESS" if archived["total"] else "PENDING",
        }
        return {
            "model": cfg.model,
            "selected_provider": cfg.provider,
            "states": states,
            "model_preflight": proof or None,
            "preflight_current": current,
            "live_classifications": live["requests"],
            "live_archives": archived["total"],
            "last_live_success_at": live["last_success"],
            "last_live_archive_at": archived["last_success"],
            "counts": counts,
            "scope": "本数据库所选模型的历史记录；不证明当前密钥、其他设备或生产验收",
            "production_verified": False,
        }

    def plan(self, request_id, user_id, payload):
        settings = self.config()
        payload = {**payload, "allowed_categories": settings.categories, "allowed_related_ids": []}
        now = time.time()
        month = (
            datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        )
        upper = estimate_cost(
            len(json.dumps(payload).encode()) + 8192, settings.max_tokens, settings
        )
        simulated = settings.provider == "mock"
        request_hash = hashlib.sha256(
            json.dumps(
                {
                    "payload": payload,
                    "provider": settings.provider,
                    "model": settings.model,
                    "max_tokens": settings.max_tokens,
                    "prompt_version": "v2.5-nonthinking-1",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with self.db.transaction(True) as conn:
            previous = conn.execute(
                "SELECT * FROM ai_usage_records WHERE id=?", (request_id,)
            ).fetchone()
            if previous:
                if previous["user_id"] != user_id:
                    raise AIError("REQUEST_OWNER_MISMATCH")
                prior_input = conn.execute(
                    "SELECT request_hash FROM ai_request_inputs WHERE usage_id=?", (request_id,)
                ).fetchone()
                if prior_input and prior_input[0] != request_hash:
                    raise AIError("REQUEST_INPUT_MISMATCH")
                if previous["status"] == "COMPLETED":
                    return validate_plan(
                        previous["result_json"], payload["filename"], settings.categories
                    )
                raise AIError("PREVIOUS_REQUEST_REQUIRES_REVIEW", uncertain=True)
            if not simulated:
                if not self.settings.allow_paid_ai:
                    raise AIError("PAID_API_NOT_AUTHORIZED_ON_SERVER")
                if not self.keys.get():
                    raise AIError("KEY_NOT_CONFIGURED")
                if (
                    settings.price_version == "unconfigured"
                    or settings.request_limit <= 0
                    or settings.input_per_million <= 0
                    or settings.output_per_million <= 0
                ):
                    raise AIError("PRICING_OR_LIMIT_NOT_CONFIGURED")
                if upper > settings.request_limit:
                    raise AIError("REQUEST_BUDGET_EXCEEDED")
                used = conn.execute(
                    "SELECT coalesce(sum(max(estimated_cost,reserved_cost)),0) "
                    "FROM ai_usage_records WHERE created_at>=? AND currency=? AND simulated=0",
                    (month, settings.currency),
                ).fetchone()[0]
                if used + upper > settings.monthly_budget:
                    raise AIError("MONTHLY_BUDGET_EXCEEDED")
            active = conn.execute(
                "SELECT count(*) FROM ai_usage_records WHERE status='STARTED'"
            ).fetchone()[0]
            if active >= settings.concurrency:
                raise AIError("AI_BUSY")
            conn.execute(
                "INSERT INTO ai_usage_records(id,user_id,provider,model,status,reserved_cost,"
                "currency,price_version,pricing_json,simulated,created_at) VALUES "
                "(?,?,?,?,'STARTED',?,?,?,?,?,?)",
                (
                    request_id,
                    user_id,
                    settings.provider,
                    settings.model,
                    0 if simulated else upper,
                    settings.currency,
                    settings.price_version,
                    settings.model_dump_json(),
                    int(simulated),
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO ai_request_inputs VALUES (?,?,NULL)", (request_id, request_hash)
            )
            cached = conn.execute(
                "SELECT u.* FROM ai_usage_records u JOIN ai_request_inputs i ON i.usage_id=u.id "
                "WHERE i.request_hash=? AND u.user_id=? AND u.status='COMPLETED' "
                "AND u.created_at>? ORDER BY u.finished_at DESC LIMIT 1",
                (request_hash, user_id, now - 86400),
            ).fetchone()
            if cached:
                plan = validate_plan(
                    cached["result_json"], payload["filename"], settings.categories
                )
                conn.execute(
                    "UPDATE ai_usage_records SET status='COMPLETED',reserved_cost=0,"
                    "result_json=?,model=?,finished_at=?,error='CACHE_REUSE' WHERE id=?",
                    (cached["result_json"], cached["model"], now, request_id),
                )
                conn.execute(
                    "UPDATE ai_request_inputs SET cache_source_id=? WHERE usage_id=?",
                    (cached["id"], request_id),
                )
                return plan
        provider = self.provider_override or (
            MockAIProvider() if simulated else DeepSeekProvider(self.keys.get())
        )
        response = None
        try:
            response = provider.complete(payload, settings)
            plan = validate_plan(response.content, payload["filename"], settings.categories)
            with self.db.transaction(True) as conn:
                conn.execute(
                    "UPDATE ai_usage_records SET status='COMPLETED',input_tokens=?,output_tokens=?,"
                    "estimated_cost=?,reserved_cost=0,result_json=?,model=?,finished_at"
                    "=? WHERE id=?",
                    (
                        response.input_tokens,
                        response.output_tokens,
                        0
                        if simulated
                        else estimate_cost(response.input_tokens, response.output_tokens, settings),
                        plan.model_dump_json(),
                        response.model,
                        time.time(),
                        request_id,
                    ),
                )
            return plan
        except Exception as exc:
            code = exc.code if isinstance(exc, AIError) else "INVALID_PLAN_OR_RESULT_COMMIT_FAILED"
            uncertain = not isinstance(exc, AIError) or exc.uncertain
            response = response or (exc.response if isinstance(exc, AIError) else None)
            with self.db.transaction(True) as conn:
                conn.execute(
                    "UPDATE ai_usage_records SET status=?,error=?,finished_at=?,input_tokens=?,"
                    "output_tokens=?,estimated_cost=? WHERE id=? AND status='STARTED'",
                    (
                        "UNKNOWN" if uncertain else "FAILED",
                        code,
                        time.time(),
                        response.input_tokens if response else 0,
                        response.output_tokens if response else 0,
                        estimate_cost(response.input_tokens, response.output_tokens, settings)
                        if response and not simulated
                        else 0,
                        request_id,
                    ),
                )
            raise AIError(code, uncertain=uncertain) from None

    def recover(self):
        with self.db.transaction(True) as conn:
            conn.execute(
                "UPDATE ai_usage_records SET "
                "status='UNKNOWN',error='WORKER_RESTART_OUTCOME_UNKNOWN' "
                "WHERE status='STARTED'"
            )

    def test_connection(self):
        import httpx

        if self.config().provider != "deepseek":
            raise HTTPException(409, "请选择 DeepSeek 后再检查模型列表。")
        key = self.keys.get()
        if not key:
            raise HTTPException(409, "NOT_CONFIGURED")
        proof = {
            "checked_at": time.time(),
            "model": self.config().model,
            "authenticated": False,
            "available": False,
            "status": "CONNECTION_FAILED",
            # Internal only: invalidate stale evidence even if a key is rotated
            # concurrently or through the existing environment/vault mechanism.
            "_key_binding": hashlib.sha256(key.encode()).hexdigest(),
        }
        try:
            with httpx.Client(timeout=10, trust_env=False, follow_redirects=False) as client:
                response = client.get(
                    "https://api.deepseek.com/models",
                    headers={"Authorization": "Bearer " + key},
                )
                if not response.is_success:
                    proof["status"] = f"HTTP_{response.status_code}"
                    raise ValueError("model list rejected")
                available = [r["id"] for r in response.json()["data"]]
                proof.update(
                    authenticated=True, available=proof["model"] in available, status="VERIFIED"
                )
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            pass
        with self.db.transaction(True) as conn:
            conn.execute(
                "INSERT INTO platform_settings VALUES (?,?) ON CONFLICT(key) "
                "DO UPDATE SET value=excluded.value",
                ("deepseek_model_preflight", json.dumps(proof)),
            )
        if not proof["authenticated"]:
            raise HTTPException(502, "模型列表读取失败（凭据和响应内容未记录）")
        return {
            "available": proof["available"],
            "checked_at": proof["checked_at"],
            "note": "模型列表连通性，不代表付费分类调用验收",
        }
