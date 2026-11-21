# V2.6 API 与配置增量

所有管理 API 保留 AdminPrincipal 权限；用户文件 API 独立 UserPrincipal/DevicePrincipal/空间 ACL。管理员登录不会自动获得他人内容读取。

| 接口/配置 | 本轮实际行为 |
|---|---|
| GET /api/admin/ai/acceptance | 只读、无外部调用；五项状态、所选模型、历史成功数/时间、非缓存 Provider 任务/缓存/失败计数、证据范围 |
| POST /api/admin/ai/test | 明确选择 DeepSeek 且已配置密钥时仅 GET 官方 /models；不需要开启收费分类；保存脱敏结果；失败替换旧成功，24h 后过期 |
| PUT /api/admin/ai | 空白 UI 密钥字段保留现有值；实际更新密钥时清除旧模型列表证明；不删除历史用量；列表证据在服务端内部绑定凭据，绑定值不会返回 API 或报告 |
| GET /api/admin/ai/usage | 保留 tasks requests 兼容字段，增加 provider_attempts / cache_reuses；Token/估算费用/错误按真实/Mock 分组 |
| GET /api/admin/system-status | 当前配置、工作器/数据库/存储/目录/分享统计和动态 DeepSeek 证据；其他外部验收保持未验证 |
| GET /api/transfers/config | 新增 enabled；其他协议字段兼容 |
| POST /api/transfers | relay_enabled=false 时拒绝新任务 503，既有任务仍可查询/完成/取消 |
| FILEMIND_RELAY_ENABLED | 默认 true，可独立停止新中继；未修改用户现有 .env |
| 管理归档/文件库响应 | 移除私有摘要/理由/关键词和正式对象定位；所属用户计划不变 |

用量的 provider_attempts 表示非缓存的 Provider 任务尝试，不等于开启 429 重试时的底层 HTTP 次数。V2.6 付费验收强制 retries=0，因此九条任务对应九次分类请求；缓存复用不增加该数。未知结果保留预算，统计是估算而非实际账单。

DeepSeek 响应必须带与请求一致的 model；JSON/分类/路径/扩展名、关联权限校验保持。已知截断的 Token/费用写入 UNKNOWN，不能释放预留再盲重试。

无新增数据库迁移。`.env.example` 仅空凭据和本地默认；`scripts/deploy-v2_6/filemind.env.example` 为待批准的共机低资源模板。完整既有接口仍见 V2_5_API、V2_4_API、V2_3_API，不移除旧路由。
