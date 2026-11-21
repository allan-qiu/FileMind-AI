# FileMind AI

Windows 文件整理客户端，配套 FastAPI 中央服务、React 用户端和管理后台。

**V2.6.2 预发布候选版 · MIT**。适合本地试用、自建服务与开发复核。

## 日常使用

启动后直接进入“整理文件”：选择文件或文件夹 → 选择保存位置 → 预览整理结果 → 确认整理。需要恢复时打开“操作记录”。高级选项默认收起，原有页面在“更多功能”中。

“服务器文件”统一进入文件库、分享、互传和归档。本次运行中返回本地后保留登录状态，关闭程序后重新登录。服务器故障不影响离线规则整理。

## 功能与边界

| 模块 | 当前实现 |
| --- | --- |
| Windows | 扫描、离线分类、可选 AI、计划确认、移动/重命名、历史与条件撤销、监控 |
| DeviceLink | 经中央服务流式中继，设备授权与 SHA-256 接收确认 |
| Archive | 独立副本、暂存、分类计划验证、正式存储与目录发布 |
| Library | 搜索、版本、增量目录、按需下载、冲突、回收与恢复 |
| Share | 临时/正式版本分享、独立额度、原子预留、密码、有效期与撤销 |
| Web / Admin | 用户操作与统一管理入口；管理员不自动获得私有文件内容权限 |

默认使用 LocalStorageProvider。百度网盘正式授权尚未验收，不能把会员当作 API 权限。DeepSeek 为可替换 Provider，默认模型配置为 `deepseek-flash`；真实可用性以账户模型与官方文档为准，付费调用默认关闭。项目不提供病毒扫描或隔离检疫功能。

## Windows 安装

维护者可在 GitHub Releases 中上传 `FileMind_AI_2.6.2_Windows_x64.msi`。源码仓库本身不内置安装包或可用云端账户。安装包未签名。

安装版数据保存在当前用户的应用数据目录，独立于程序安装目录。程序不内置项目维护者的服务器、SSH 密钥、账号或 AI 密钥。安装版默认连接本机 `http://127.0.0.1:18765`；如自建服务监听 8765，请在“连接设置”中修改。远程地址须为 HTTPS；本地开发允许回环 HTTP。

## 从源码启动

要求：Windows x64、Python 3.12、Node.js 满足 `web/package.json` engines。锁定依赖见 `requirements.lock` 与 `web/package-lock.json`。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-v2.ps1
.\launch.cmd
.\launch-server.cmd
```

中央服务默认监听 `127.0.0.1:8765`，用户端 `/app/`，后台 `/admin/`，公开分享 `/s/`。桌面离线整理不要求启动中央服务。

没有默认管理员。创建自己的管理员：

```powershell
.\.venv\Scripts\python.exe -m filemind_server create-admin --username your_admin
```

密码由终端隐藏输入。新环境可参考 `.env.example` 创建本地配置；已有配置不要覆盖。不要将真实密钥、Token、数据库或私人文件加入 Git。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check src tests scripts
npm.cmd --prefix web ci --ignore-scripts
npm.cmd --prefix web test
npm.cmd --prefix web run build
```

测试使用合成文件、独立数据库和 Mock AI；CI 不执行收费请求或写入真实云盘。GitHub Actions 的线上结果以实际工作流运行记录为准，不能由本地验证替代。

## 文档

- [架构](docs/V2_6_ARCHITECTURE.md) · [API](docs/V2_6_API.md)
- [部署与发布](docs/PUBLIC_RELEASE.md) · [版本变化](CHANGELOG.md)
- [贡献指南](CONTRIBUTING.md) · [问题报告](SECURITY.md)
- [MIT](LICENSE) · [第三方许可](THIRD_PARTY_NOTICES.md)

## 预发布限制

手机实机、第二台 Windows 与公网 HTTPS 未作为本次公开版的通过项。客户端账户界面的登录后人工验收仍待完成；真实服务器 API 检查与 Mock 窗口测试分别记录。生产部署需要独立资源检查、备份恢复和受控部署授权。
