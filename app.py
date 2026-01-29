import asyncio
import logging
import os
import signal
import sys
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# 统一环境变量加载入口 - 必须在所有其他 src 导入之前
from src.core.env import env_loaded, env_file_path  # noqa: F401
from src.core.logging_setup import configure_logging

BASE_DIR = Path(__file__).resolve().parent

log_file, error_file = configure_logging(BASE_DIR)
logger = logging.getLogger(__name__)
logger.info("Log file: %s", log_file.relative_to(BASE_DIR))
logger.info("错误Log file: %s", error_file.relative_to(BASE_DIR))

from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.core.database import init_db, close_db
from src.core.http_client import init_global_client, close_global_client
from src.core.limiter import limiter
from src.core.runtime import ensure_admin_credentials, parse_trusted_hosts, parse_cors_origins
from src.security.auth import security_config
from src.security.manager import advanced_key_manager
from src.services.account_service import refresh_stale_tokens_loop, cleanup_auth_sessions_loop, cleanup_expired_data_loop, cleanup_expired_refresh_locks_loop
from src.services.quota_service import QuotaService
from src.services.session_service import SessionService
from src.api.openai_errors import register_openai_error_handlers

# Routers
from src.routers import admin, auth, claude, openai, system, quota, kiro, health, usage

# Validation
def _validate_env_config() -> None:
    errors = []
    try:
        multiplier = float(os.getenv("TOKEN_COUNT_MULTIPLIER", "1.0"))
        if multiplier <= 0 or multiplier > 10:
            errors.append(f"TOKEN_COUNT_MULTIPLIER must be between 0 and 10, got: {multiplier}")
    except ValueError as e:
        errors.append(f"Invalid TOKEN_COUNT_MULTIPLIER: {e}")

    try:
        max_errors = int(os.getenv("MAX_ERROR_COUNT", "100"))
        if max_errors < 1:
            errors.append(f"MAX_ERROR_COUNT must be >= 1, got: {max_errors}")
    except ValueError as e:
        errors.append(f"Invalid MAX_ERROR_COUNT: {e}")

    if errors:
        for err in errors:
            logger.error(f"❌ [CONFIG] {err}")
        raise ValueError("Invalid environment configuration")

_validate_env_config()

CONSOLE_ENABLED = os.getenv("ENABLE_CONSOLE", "true").strip().lower() not in ("false", "0", "no", "disabled")
ensure_admin_credentials(CONSOLE_ENABLED)

# 全局变量用于跟踪关闭状态
_shutdown_event = asyncio.Event()
_background_tasks = []


# 全局异常处理器
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    捕获所有未处理的异常，提供统一的错误响应
    Critical Fix: Blocker #1 - 防止服务崩溃
    """
    request_id = str(uuid.uuid4())

    # 获取客户端信息
    client_host = request.client.host if request.client else "unknown"

    # 记录详细错误信息
    logger.error(
        "🔴 [UNHANDLED EXCEPTION] Caught unhandled exception",
        extra={
            "request_id": request_id,
            "url": str(request.url),
            "method": request.method,
            "client": client_host,
            "error_type": type(exc).__name__,
            "error_message": str(exc),
        },
        exc_info=True
    )

    # 在开发模式下提供更详细的错误信息
    debug_mode = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    error_response = {
        "error": {
            "message": "Internal Server Error",
            "type": "internal_error",
            "code": "internal_server_error"
        },
        "request_id": request_id
    }

    # 开发模式下添加详细信息
    if debug_mode:
        error_response["debug"] = {
            "error_type": type(exc).__name__,
            "error_message": str(exc),
            "traceback": traceback.format_exc().split("\n")
        }

    return JSONResponse(
        status_code=500,
        content=error_response
    )


# 信号处理器 - 优雅关闭
def setup_signal_handlers():
    """
    设置信号处理器以实现优雅关闭
    Critical Fix: Blocker #1 - 确保资源正确清理
    """
    def handle_shutdown_signal(signum, frame):
        signal_name = "SIGTERM" if signum == signal.SIGTERM else "SIGINT"
        logger.info(f"🛑 [SHUTDOWN] Received {signal_name}, initiating graceful shutdown...")
        _shutdown_event.set()

        # 在Unix系统上，使用默认信号处理器
        # 这样第二次收到信号时会强制退出
        if sys.platform != "win32":
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.SIG_DFL)

    # 注册信号处理器
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    logger.info("✅ [STARTUP] Signal handlers configured for graceful shutdown")


# Lifespan 上下文管理器 - 替代已废弃的 on_event 装饰器
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理
    Enhanced: 添加了优雅关闭和更好的资源清理
    """
    global _background_tasks

    # === Startup ===
    logger.info("🚀 [STARTUP] Initializing application...")

    # 设置信号处理器
    setup_signal_handlers()

    # Critical Fix: Blocker #1 - 初始化密码管理器（bcrypt 哈希）
    from src.security.password import password_manager
    password_manager.initialize()

    # 初始化HTTP客户端
    await init_global_client()
    logger.info("✅ [STARTUP] HTTP client initialized")

    # 初始化数据库
    db = await init_db()
    logger.info("✅ [STARTUP] Database initialized")

    # 初始化配额监控表
    await QuotaService.initialize_quota_table()
    logger.info("✅ [STARTUP] Quota monitoring initialized")

    # 初始化会话粘性表
    await SessionService.initialize_session_table()
    logger.info("✅ [STARTUP] Session stickiness initialized")

    # 加载安全密钥
    advanced_key_manager.set_database(db)
    loaded = await advanced_key_manager.load_keys_from_db()
    logger.info(f"🔑 [STARTUP] Loaded {loaded} secure keys from database")

    # 启动后台任务
    _background_tasks = [
        asyncio.create_task(refresh_stale_tokens_loop(), name="refresh_tokens"),
        asyncio.create_task(cleanup_expired_data_loop(), name="cleanup_data"),
        asyncio.create_task(cleanup_expired_refresh_locks_loop(), name="cleanup_locks"),
    ]
    if CONSOLE_ENABLED:
        _background_tasks.append(asyncio.create_task(cleanup_auth_sessions_loop(), name="cleanup_sessions"))

    logger.info(f"✅ [STARTUP] Started {len(_background_tasks)} background tasks")
    logger.info("🎉 [STARTUP] Application startup complete!")

    yield

    # === Shutdown ===
    logger.info("🛑 [SHUTDOWN] Initiating graceful shutdown...")

    # 取消所有后台任务
    logger.info(f"🛑 [SHUTDOWN] Cancelling {len(_background_tasks)} background tasks...")
    for task in _background_tasks:
        if not task.done():
            task.cancel()

    # 等待所有任务完成（最多5秒）
    if _background_tasks:
        done, pending = await asyncio.wait(_background_tasks, timeout=5.0)
        if pending:
            logger.warning(f"⚠️  [SHUTDOWN] {len(pending)} tasks did not complete within timeout")
            for task in pending:
                logger.warning(f"  - Pending task: {task.get_name()}")

    # 关闭HTTP客户端
    try:
        await close_global_client()
        logger.info("✅ [SHUTDOWN] HTTP client closed")
    except Exception as e:
        logger.error(f"❌ [SHUTDOWN] Error closing HTTP client: {e}")

    # 关闭数据库连接
    try:
        await close_db()
        logger.info("✅ [SHUTDOWN] Database connections closed")
    except Exception as e:
        logger.error(f"❌ [SHUTDOWN] Error closing database: {e}")

    logger.info("✅ [SHUTDOWN] Graceful shutdown complete")


app = FastAPI(title="v2 OpenAI-compatible Server (Amazon Q Backend)", lifespan=lifespan)

# 注册全局异常处理器 - Critical Fix: Blocker #1
app.add_exception_handler(Exception, global_exception_handler)

# 注册OpenAI格式的错误处理器
register_openai_error_handlers(app)

# Rate Limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Critical Fix: High Priority #3 - 添加会话超时检查中间件
from src.middleware.session_timeout import SessionTimeoutMiddleware
app.add_middleware(SessionTimeoutMiddleware)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=parse_trusted_hosts(os.getenv("TRUSTED_HOSTS", ""), security_config.debug_mode)
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=parse_cors_origins(os.getenv("CORS_ORIGINS", ""), security_config.debug_mode),
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Requested-With",
        "X-Admin-Key",
        "X-Account-Id",
        "X-End-User-Id",
        "X-User-Id",
        "X-Dedupe-Bypass",
        "Accept",
    ],
    allow_credentials=True,
    expose_headers=["X-Total-Count", "X-Conversation-Id", "X-ConversationId"],
)

# Routers
app.include_router(health.router)  # Health checks - no auth required
app.include_router(openai.router)
app.include_router(claude.router)
app.include_router(system.router)

if CONSOLE_ENABLED:
    app.include_router(admin.router)
    app.include_router(auth.router)
    app.include_router(kiro.router)
    app.include_router(quota.router)
    app.include_router(usage.router)

# Frontend Static Files
FRONTEND_DIR = BASE_DIR / "frontend"
app.mount("/css", StaticFiles(directory=str(FRONTEND_DIR / "css"), check_dir=False), name="css")
app.mount("/js", StaticFiles(directory=str(FRONTEND_DIR / "js"), check_dir=False), name="js")

@app.get("/security.js", response_class=FileResponse)
def security_js():
    path = FRONTEND_DIR / "security.js"
    if not path.exists():
        raise HTTPException(status_code=404, detail="frontend/security.js not found")
    return FileResponse(str(path))


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    path = FRONTEND_DIR / "favicon.svg"
    if not path.exists():
        return Response(status_code=204)
    return FileResponse(str(path), media_type="image/svg+xml")


@app.get("/favicon.ico", include_in_schema=False)
def favicon_ico():
    ico_path = FRONTEND_DIR / "favicon.ico"
    if ico_path.exists():
        return FileResponse(str(ico_path), media_type="image/x-icon")
    # fallback to svg to avoid 404 noise in console
    svg_path = FRONTEND_DIR / "favicon.svg"
    if svg_path.exists():
        return FileResponse(str(svg_path), media_type="image/svg+xml")
    return Response(status_code=204)

# Static Pages
@app.get("/login", response_class=FileResponse)
def login_page():
    path = BASE_DIR / "frontend" / "login.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="frontend/login.html not found")
    return FileResponse(str(path))

@app.get("/", response_class=FileResponse)
def index():
    path = BASE_DIR / "frontend" / "index.html"
    if not path.exists():
        raise HTTPException(status_code=404, detail="frontend/index.html not found")
    return FileResponse(str(path))
