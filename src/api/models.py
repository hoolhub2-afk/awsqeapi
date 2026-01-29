# API模型定义
from dataclasses import dataclass
from typing import Optional, Dict, Any, List


@dataclass
class APIRequest:
    """API请求模型"""
    method: str
    path: str
    headers: Dict[str, str]
    body: Optional[Dict[str, Any]] = None
    params: Optional[Dict[str, str]] = None


@dataclass
class APIResponse:
    """API响应模型"""
    status_code: int
    data: Optional[Dict[str, Any]] = None
    message: Optional[str] = None
    headers: Optional[Dict[str, str]] = None

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "status_code": self.status_code,
            "data": self.data,
            "message": self.message,
            "headers": self.headers
        }


@dataclass
class ErrorResponse(APIResponse):
    """错误响应模型"""
    error_code: Optional[str] = None
    details: Optional[List[str]] = None

    def __init__(self, status_code: int, message: str, error_code: Optional[str] = None, details: Optional[List[str]] = None):
        super().__init__(status_code=status_code, data=None, message=message)
        self.error_code = error_code
        self.details = details