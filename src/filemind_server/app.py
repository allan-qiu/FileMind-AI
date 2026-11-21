import asyncio
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from filemind_server.ai_control import ClassificationPlanner
from filemind_server.archives import ArchiveService
from filemind_server.config import Settings
from filemind_server.database import CentralDatabase
from filemind_server.routes import (
    admin,
    ai,
    archives,
    auth,
    devices,
    library,
    shares,
    transfers,
    workspaces,
)
from filemind_server.service_lock import ServiceLock
from filemind_server.static import WebAssets
from filemind_server.transfers import TransferService


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    db = CentralDatabase(settings.data_dir / "central.sqlite3")

    @asynccontextmanager
    async def runtime(app: FastAPI):
        if settings.data_dir.resolve() != settings.data_dir.absolute():
            raise ValueError("服务数据目录不能通过链接重定向。")
        db.migrate()
        with db.transaction(True) as conn:
            conn.execute(
                "UPDATE library_download_tasks SET state='INTERRUPTED',"
                "error='SERVER_RESTART' WHERE state IN ('VERIFYING','STREAMING')"
            )
        app.state.ai.recover()
        app.state.archives.initialize()
        app.state.archives.recover()
        app.state.transfers.recover()
        app.state.share_temporary.initialize()
        app.state.share_downloads.recover()
        app.state.share_expiry.sweep()
        app.state.started_at = time.time()

        async def cleanup():
            last_share_cleanup = 0
            while True:
                await asyncio.sleep(1)
                await app.state.transfers.sweep()
                from filemind_server.share_config import config as share_config

                share_limits = await asyncio.to_thread(share_config, db)
                if time.time() - last_share_cleanup >= share_limits.cleanup_interval:
                    try:
                        await asyncio.to_thread(app.state.share_expiry.sweep)
                    except Exception:
                        app.state.share_worker_errors += 1
                    last_share_cleanup = time.time()

        async def archive_cycle():
            await asyncio.to_thread(app.state.archives.sweep)
            await asyncio.gather(
                *[
                    asyncio.to_thread(app.state.archives.process_one)
                    for _ in range(app.state.archives.config().concurrency)
                ]
            )

        async def archive_worker():
            while True:
                cycle = asyncio.create_task(archive_cycle())
                try:
                    await asyncio.shield(cycle)
                except asyncio.CancelledError:
                    await cycle
                    raise
                except Exception:
                    # Durable jobs retain recovery context; never emit file contents or secrets.
                    app.state.archive_worker_errors += 1
                await asyncio.sleep(1)

        janitor = asyncio.create_task(cleanup())
        worker = asyncio.create_task(archive_worker()) if settings.archive_worker_enabled else None
        try:
            yield
        finally:
            if worker:
                # Let bounded in-flight work drain before cancelling its scheduler.
                worker.cancel()
                try:
                    await worker
                except asyncio.CancelledError:
                    pass
            janitor.cancel()
            try:
                await janitor
            except asyncio.CancelledError:
                pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        with ServiceLock(settings.data_dir / "service.lock"):
            async with runtime(app):
                yield

    app = FastAPI(
        title="FileMind Central",
        version="2.6.0rc1",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.db, app.state.settings = db, settings
    app.state.transfers = TransferService(db, settings)
    app.state.ai = ClassificationPlanner(db, settings)
    app.state.archives = ArchiveService(db, settings, app.state.ai)
    app.state.archive_worker_errors = 0
    from filemind_server.share_downloads import ShareDownloadService
    from filemind_server.share_expiry import ShareExpiryWorker
    from filemind_server.share_temporary import TemporaryShareStorage
    from filemind_server.shares import ShareManager

    app.state.shares = ShareManager(db, app.state.archives)
    app.state.share_temporary = TemporaryShareStorage(db, settings)
    app.state.share_downloads = ShareDownloadService(app.state.shares, app.state.share_temporary)
    app.state.share_expiry = ShareExpiryWorker(app.state.shares, app.state.share_temporary)
    app.state.share_worker_errors = 0
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never echo password or token input in validation responses.
        return JSONResponse(
            status_code=422,
            content={
                "detail": [
                    {"loc": list(error["loc"]), "msg": error["msg"], "type": error["type"]}
                    for error in exc.errors()
                ]
            },
        )

    @app.middleware("http")
    async def browser_boundary(request: Request, call_next):
        if request.url.path.startswith("/api") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            # Chromium can send Origin:null for a native form download under no-referrer.
            # This exact guest-only endpoint uses a session secret in the bounded form body,
            # never account cookies. All management endpoints retain the strict origin check.
            native_guest_form = (
                origin == "null"
                and request.method == "POST"
                and re.fullmatch(r"/api/public/downloads/[a-f0-9-]{36}/content", request.url.path)
                and request.headers.get("content-type", "").startswith(
                    "application/x-www-form-urlencoded"
                )
            )
            if origin and origin not in settings.allowed_origins and not native_guest_form:
                return JSONResponse({"detail": "不允许的请求来源。"}, status_code=403)
            length = request.headers.get("content-length")
            if length is None and request.method in ("POST", "PUT", "PATCH"):
                return JSONResponse({"detail": "JSON 请求需要 Content-Length。"}, status_code=411)
            limit = settings.transfer_chunk_bytes if "/chunks/" in request.url.path else 65536
            if length is not None and (not length.isdigit() or int(length) > limit):
                return JSONResponse(
                    {
                        "detail": "文件块超过配置上限。"
                        if "/chunks/" in request.url.path
                        else "JSON 请求超过 64 KiB 限制。"
                    },
                    status_code=413,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'"
            )
        return response

    @app.get("/api/health")
    def health():
        with db.transaction() as conn:
            conn.execute("SELECT 1").fetchone()
        return {
            "status": "ok",
            "service": "filemind-central",
            "version": "2.6.0rc1",
            "schema_version": 11,
        }

    for router in (
        auth.router,
        devices.router,
        workspaces.router,
        admin.router,
        transfers.router,
        ai.router,
        archives.router,
        library.router,
        shares.router,
    ):
        app.include_router(router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/app/")

    # Both applications are separate static entrypoints; authorization is always in the API.
    if settings.web_dist.exists():
        for route, folder in (
            ("/app", "app"),
            ("/admin", "admin"),
            ("/s", "share"),
            ("/assets", "assets"),
        ):
            directory = settings.web_dist / folder
            if directory.exists():
                app.mount(route, WebAssets(directory=directory, html=True), name=folder)
    return app
