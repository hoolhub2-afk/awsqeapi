"""
测试账号错误处理和自动切换机制
"""
import pytest
from src.integrations.amazonq_client import (
    _is_quota_exhausted_error,
    _is_account_suspended_error,
    QuotaExhaustedException,
    AccountSuspendedException
)


class TestErrorDetection:
    """测试错误检测函数"""

    def test_quota_exhausted_detection(self):
        """测试配额耗尽检测"""
        # JSON 格式错误
        error1 = '{"__type": "com.amazon.aws.codewhisperer#ThrottlingException", "reason": "MONTHLY_REQUEST_COUNT"}'
        assert _is_quota_exhausted_error(error1) is True

        # 文本格式错误
        error2 = "ThrottlingException: MONTHLY_REQUEST_COUNT exceeded"
        assert _is_quota_exhausted_error(error2) is True

        # 其他限流错误
        error3 = "rate limit exceeded"
        assert _is_quota_exhausted_error(error3) is True

        # 非配额错误
        error4 = "Internal server error"
        assert _is_quota_exhausted_error(error4) is False

    def test_account_suspended_detection(self):
        """测试账号封禁检测"""
        # JSON 格式 - 临时封禁
        error1 = '{"reason": "TEMPORARILY_SUSPENDED"}'
        assert _is_account_suspended_error(error1) is True

        # JSON 格式 - 访问拒绝(不应误判为封禁)
        error2 = '{"__type": "com.amazon.aws.codewhisperer#AccessDeniedException"}'
        assert _is_account_suspended_error(error2) is False

        # 文本格式 - 封禁
        error3 = "account suspended due to policy violation"
        assert _is_account_suspended_error(error3) is True

        # 文本格式 - 访问拒绝(不应误判为封禁)
        error4 = "AccessDeniedException: Forbidden"
        assert _is_account_suspended_error(error4) is False

        # 文本格式 - Forbidden(不应误判为封禁)
        error5 = "403 Forbidden: access denied"
        assert _is_account_suspended_error(error5) is False

        # 非封禁错误
        error6 = "Connection timeout"
        assert _is_account_suspended_error(error6) is False

    def test_error_priority(self):
        """测试错误优先级 - 配额耗尽 vs 账号封禁"""
        # 同时包含两种错误特征时,应该能正确识别
        error1 = '{"reason": "MONTHLY_REQUEST_COUNT"}'
        assert _is_quota_exhausted_error(error1) is True
        assert _is_account_suspended_error(error1) is False

        error2 = '{"reason": "TEMPORARILY_SUSPENDED"}'
        assert _is_quota_exhausted_error(error2) is False
        assert _is_account_suspended_error(error2) is True


class TestExceptionTypes:
    """测试异常类型"""

    def test_quota_exhausted_exception(self):
        """测试配额耗尽异常"""
        with pytest.raises(QuotaExhaustedException) as exc_info:
            raise QuotaExhaustedException("Quota exhausted")

        assert "Quota exhausted" in str(exc_info.value)

    def test_account_suspended_exception(self):
        """测试账号封禁异常"""
        with pytest.raises(AccountSuspendedException) as exc_info:
            raise AccountSuspendedException("Account suspended")

        assert "Account suspended" in str(exc_info.value)


class TestEdgeCases:
    """测试边界情况"""

    def test_empty_error_text(self):
        """测试空错误文本"""
        assert _is_quota_exhausted_error("") is False
        assert _is_account_suspended_error("") is False

    def test_invalid_json(self):
        """测试无效 JSON"""
        error = '{"invalid json'
        assert _is_quota_exhausted_error(error) is False
        assert _is_account_suspended_error(error) is False

    def test_case_sensitivity(self):
        """测试大小写敏感性"""
        # 关键词应该区分大小写
        error1 = "temporarily_suspended"  # 小写
        assert _is_account_suspended_error(error1) is False

        error2 = "TEMPORARILY_SUSPENDED"  # 大写
        assert _is_account_suspended_error(error2) is True

    def test_partial_match(self):
        """测试部分匹配"""
        # 应该能匹配包含关键词的长文本
        error = "Error occurred: ThrottlingException with reason MONTHLY_REQUEST_COUNT has been exceeded"
        assert _is_quota_exhausted_error(error) is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
