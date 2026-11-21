# V2.6 实际架构

版本 2.6.0rc1；中央 schema 11、桌面 schema 2，保留旧迁移校验。本轮增量维护，不引入大型常驻中间件或本地大模型。

```text
Windows 离线整理 → 分析/规则 → 计划 → 明确确认 → 移动/重命名 → 历史/条件撤销
Windows / React 客户端 → FastAPI 认证/设备授权 → DeviceLink 单槽流式中继 → 接收校验/确认
完成传输 → 独立归档任务 → 接收端优先提供副本 → staging 配额/租约
→ 按用户授权提取 → AI Provider → JSON/Pydantic/路径校验 → 必要人工确认
→ 持久化 provider 绑定 → 正式存储/完整哈希 → 事务发布目录与版本
→ 其他客户端修订同步 → 按需下载 → 指定不可变版本分享 → 原子预留/流式交付/计数
```

`src/filemind/services/` 不依赖 Qt；界面调用后台任务。离线整理不依赖中央服务。`src/filemind_server/ai.py` 保留 Provider 协议和 DeepSeek/Mock 两种实现，AI 不执行文件操作。DeepSeek 地址固定为官方 HTTPS，默认模型 deepseek-flash，非思考 JSON 输出。

`transfers.py` 保留有界分块、速率和队列，使用条件通知替代固定 20ms 轮询，同时周期检查断线/超时，重启后中继失败需重发；没有断点续传。新增 FILEMIND_RELAY_ENABLED 只拒绝新会话，允许既有会话收尾；API、文件库、分享及本地整理可以继续使用。中继状态在单个中央进程内，不能用多个 worker 或跨节点共享 SQLite 来扩容。

`archives.py` 保留租约 fencing、AI 先于正式存储、哈希后发布、崩溃恢复、重试/结果未知保护。`storage_reads.py` 保留本地对象身份缓存和受限远程完整校验副本。切换当前写 provider 不改变旧对象的归属。

`library.py` / `routes/library.py` 保留修订/ACL、版本和冲突、回收恢复、同步收据。`shares.py` / `share_downloads.py` 在 SQLite 写事务内预留额度，真实交付后记数，各链接独立。临时分享和归档 staging 分开配额与清理。

后台新接口 GET /api/admin/ai/acceptance 只查询证据；模型列表 GET 不再被误认为付费分类。状态按模型区分、列表证据 24 小时过期、更新密钥清除旧列表证据；历史成功保留时间与范围，不声称当前密钥或生产均已验证。缓存复用不算新的 Provider 任务。

管理员运营接口不返回私有 AI 摘要、关键词、理由；全局库管理员列表也删除 summary 和正式对象定位字段。所属用户仍可读取自己的完整计划；系统管理员角色不提供私有内容下载授权。本项目不含病毒检测、风险评分、检疫或专用扫描服务。
