from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from filemind_server.accounts import audit
from filemind_server.ai import AISettings
from filemind_server.ai_control import save_config
from filemind_server.dependencies import AdminPrincipal

router = APIRouter()


class AIConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    settings: AISettings
    api_key: SecretStr | None = Field(default=None, max_length=512)


@router.get("/admin/ai")
def config(request: Request, p: AdminPrincipal):
    return request.app.state.ai.public_config()


@router.put("/admin/ai")
def configure(data: AIConfigUpdate, request: Request, p: AdminPrincipal):
    planner = request.app.state.ai
    if data.api_key is not None:
        planner.keys.set(data.api_key.get_secret_value())
        with request.app.state.db.transaction(True) as conn:
            conn.execute("DELETE FROM platform_settings WHERE key='deepseek_model_preflight'")
    save_config(request.app.state.db, "ai_v2_2", data.settings)
    with request.app.state.db.transaction(True) as conn:
        audit(
            conn,
            p.user["id"],
            "ai.settings_updated",
            None,
            {
                "provider": data.settings.provider,
                "model": data.settings.model,
                "key_updated": data.api_key is not None,
            },
        )
    return planner.public_config()


@router.post("/admin/ai/test")
def connection(request: Request, p: AdminPrincipal):
    return request.app.state.ai.test_connection()


@router.get("/admin/ai/acceptance")
def acceptance(request: Request, p: AdminPrincipal):
    return request.app.state.ai.acceptance_status()


@router.get("/admin/ai/usage")
def usage(request: Request, p: AdminPrincipal):
    with request.app.state.db.transaction() as conn:
        return {
            "items": [
                dict(r)
                for r in conn.execute(
                    "SELECT "
                    "id,user_id,provider,model,status,input_tokens,output_tokens,estima"
                    "ted_cost,"
                    "reserved_cost,currency,price_version,simulated,error,created_at,finished_at "
                    "FROM ai_usage_records ORDER BY created_at DESC LIMIT 200"
                )
            ],
            "totals": [
                dict(r)
                for r in conn.execute(
                    "SELECT currency,simulated,count(*) requests,sum(input_tokens) input_tokens,"
                    "sum(output_tokens) output_tokens,sum(estimated_cost) estimated_cost,"
                    "sum(CASE WHEN status IN ('FAILED','UNKNOWN') THEN 1 ELSE 0 END) failures,"
                    "sum(CASE WHEN i.cache_source_id IS NULL THEN 1 ELSE 0 END) provider_attempts,"
                    "sum(CASE WHEN i.cache_source_id IS NOT NULL THEN 1 ELSE 0 END) cache_reuses "
                    "FROM ai_usage_records u LEFT JOIN ai_request_inputs i ON i.usage_id=u.id "
                    "GROUP BY currency,simulated"
                )
            ],
            "billing": "估算；并非服务商实际账单。UNKNOWN保留预算预留。",
        }
