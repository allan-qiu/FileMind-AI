from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, ValidationError


class CloudError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class CloudUser(BaseModel):
    id: str
    username: str
    display_name: str
    role: str


class CloudSession(BaseModel):
    access_token: str = Field(min_length=1, repr=False)
    user: CloudUser


class CloudDevice(BaseModel):
    id: str
    name: str
    platform: str
    revoked_at: int | None
    online: bool


class DeviceRegistration(BaseModel):
    device: CloudDevice
    device_token: str = Field(min_length=1, repr=False)


def validated(model: type[BaseModel], data) -> dict:
    try:
        return model.model_validate(data).model_dump()
    except ValidationError:
        raise CloudError("中央服务返回了不兼容的数据结构。") from None


def validate_server_url(value: str) -> str:
    url = urlsplit(value.strip())
    local = url.hostname in ("127.0.0.1", "localhost", "::1")
    if (
        url.scheme not in ("http", "https")
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
        or (url.scheme == "http" and not local)
    ):
        raise CloudError("服务地址须为本地 HTTP 或 HTTPS 的完整源地址，不包含账号或额外路径。")
    try:
        if url.port is not None and not 1 <= url.port <= 65535:
            raise ValueError
    except ValueError as exc:
        raise CloudError("服务端口无效。") from exc
    return value.strip().rstrip("/")


class CloudClient:
    "Explicit, time-bounded HTTP operations; no Qt, disk writes or saved credentials."

    def __init__(self, server: str, token: str = "", transport=None):
        self.server = validate_server_url(server)
        self.token, self.transport = token, transport

    def request(self, method: str, path: str, body: dict | None = None):
        try:
            with httpx.Client(
                base_url=self.server,
                timeout=5,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.request(
                    method,
                    "/api" + path,
                    json=body,
                    headers={"Authorization": "Bearer " + self.token} if self.token else {},
                )
            data = response.json()
            if not response.is_success:
                detail = data.get("detail", "请求失败。")
                raise CloudError(
                    detail if isinstance(detail, str) else "请求参数校验失败。",
                    response.status_code,
                )
            return data
        except (httpx.HTTPError, ValueError) as exc:
            raise CloudError("无法连接中央服务或响应格式无效，请检查地址并重试。") from exc

    def login(self, username: str, password: str) -> dict:
        data = self.request("POST", "/auth/login", {"username": username, "password": password})
        return validated(CloudSession, data)

    def devices(self) -> list[dict]:
        data = self.request("GET", "/devices")
        if not isinstance(data, list):
            raise CloudError("设备列表响应格式无效。")
        return [validated(CloudDevice, item) for item in data]

    def register_device(self, name: str, installation_id: str | None = None) -> dict:
        return validated(
            DeviceRegistration,
            self.request(
                "POST",
                "/devices",
                {
                    "name": name,
                    "platform": "windows",
                    **({"installation_id": installation_id} if installation_id else {}),
                },
            ),
        )

    def revoke_device(self, device_id: str) -> dict:
        return self.request("DELETE", f"/devices/{device_id}")

    def logout(self) -> dict:
        return self.request("POST", "/auth/logout")
