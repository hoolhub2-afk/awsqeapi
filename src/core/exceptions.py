# 自定义异常类


class Q2APIException(Exception):
    """Q2API基础异常"""
    pass


class AuthException(Q2APIException):
    """认证相关异常"""
    pass


class DatabaseException(Q2APIException):
    """数据库相关异常"""
    pass


class ValidationException(Q2APIException):
    """验证相关异常"""
    pass