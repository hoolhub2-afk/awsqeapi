"""
会话超时中间件

Critical Fix: High Priority #3 - 强制执行会话超时检查
确保每次请求都验证会话有效性
"""
import logging
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class SessionTimeoutMiddleware(BaseHTTPMiddleware):
    """
    会话超时检查中间件

    对所有需要认证的管理路由进行会话验证
    """

    # 无需认证的端点（白名单）
    EXCLUDED_PATHS = {
        "/v2/auth/verify",
        "/auth/verify",
        "/v2/auth/logout",
        "/auth/logout",
        "/healthz",
        "/docs",
        "/openapi.json",
        "/favicon.ico",
        "/favicon.svg",
    }

    async def dispatch(self, request: Request, call_next):
        """处理每个请求"""

        # 只对管理路由检查（/v2/ 或 /auth/）
        path = request.url.path

        # 跳过不需要认证的路径
        if path in self.EXCLUDED_PATHS:
            return await call_next(request)

        # 跳过静态文件
        if path.startswith(("/css/", "/js/", "/static/", "/security.js")):
            return await call_next(request)

        # 跳过 API 端点（/v1/）- 这些使用 API Key 认证
        if path.startswith("/v1/"):
            return await call_next(request)

        # 跳过主页和登录页
        if path in ["/", "/login"]:
            return await call_next(request)

        # 检查需要管理员认证的路径
        if path.startswith("/v2/") or path.startswith("/auth/"):
            # 检查 Cookie 中的 admin_token
            admin_token = request.cookies.get("admin_token")

            if not admin_token:
                logger.warning(f"⚠️ [SessionTimeout] 未授权访问: {path}, IP: {request.client.host if request.client else 'unknown'}")

                # 返回 JSON 响应（API 请求）
                if path.startswith("/v2/") or path.startswith("/auth/"):
                    return JSONResponse(
                        status_code=401,
                        content={
                            "error": {
                                "message": "Authentication required",
                                "type": "authentication_error",
                                "code": "session_expired"
                            }
                        }
                    )

        return await call_next(request)
