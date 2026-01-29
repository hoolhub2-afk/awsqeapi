"""
测试账户错误检测器
验证 Blocker #6 修复
"""

import pytest
from src.core.error_detector import AccountErrorDetector, AccountErrorType


class TestAWSErrorCodeDetection:
    """测试AWS错误代码检测"""

    def test_detects_resource_not_found(self):
        """测试：检测ResourceNotFoundException"""
        error = Exception("ResourceNotFoundException: Resource not found")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            error_code="ResourceNotFoundException"
        )

        assert error_type == AccountErrorType.SUSPENDED
        assert "ResourceNotFoundException" in reason

    def test_detects_access_denied(self):
        """测试：检测AccessDeniedException"""
        error = Exception("Access denied")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            error_code="AccessDeniedException"
        )

        assert error_type == AccountErrorType.SUSPENDED

    def test_detects_invalid_access_key(self):
        """测试：检测InvalidAccessKeyId"""
        error = Exception("Invalid access key ID")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            error_code="InvalidAccessKeyId"
        )

        assert error_type == AccountErrorType.SUSPENDED


class TestHTTPStatusCodeDetection:
    """测试HTTP状态码检测"""

    def test_detects_401_as_auth_error(self):
        """测试：401识别为认证错误"""
        error = Exception("Unauthorized")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            status_code=401
        )

        assert error_type == AccountErrorType.AUTH_ERROR
        assert "401" in reason

    def test_detects_403_as_suspended(self):
        """测试：403识别为账户暂停"""
        error = Exception("Forbidden")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            status_code=403
        )

        assert error_type == AccountErrorType.SUSPENDED
        assert "403" in reason

    def test_detects_429_as_rate_limit(self):
        """测试：429识别为限速"""
        error = Exception("Too many requests")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            status_code=429
        )

        assert error_type == AccountErrorType.RATE_LIMITED


class TestSuspensionPatternDetection:
    """测试暂停模式检测"""

    def test_detects_account_suspended_message(self):
        """测试：检测账户暂停消息"""
        error = Exception("Your account has been suspended due to violation of terms")

        error_type, reason = AccountErrorDetector.detect_error_type(error)

        assert error_type == AccountErrorType.SUSPENDED
        assert "suspended" in reason.lower()

    def test_detects_access_revoked_message(self):
        """测试：检测访问撤销消息"""
        error = Exception("Access has been revoked for this account")

        error_type, reason = AccountErrorDetector.detect_error_type(error)

        assert error_type == AccountErrorType.SUSPENDED

    def test_detects_subscription_expired_message(self):
        """测试：检测订阅过期消息"""
        error = Exception("Your subscription has expired")

        error_type, reason = AccountErrorDetector.detect_error_type(error)

        assert error_type == AccountErrorType.SUSPENDED


class TestRateLimitDetection:
    """测试限速检测"""

    def test_distinguishes_temporary_rate_limit(self):
        """测试：区分临时限速"""
        error = Exception("Rate limit exceeded, please retry after 60 seconds")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            status_code=429
        )

        assert error_type == AccountErrorType.RATE_LIMITED

    def test_detects_permanent_rate_limit(self):
        """测试：检测永久限速（配额耗尽）"""
        error = Exception("Daily quota exceeded, upgrade required")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            status_code=429
        )

        assert error_type == AccountErrorType.SUSPENDED
        assert "Permanent" in reason

    def test_detects_throttling_exception(self):
        """测试：检测ThrottlingException"""
        error = Exception("ThrottlingException: Request rate exceeded")

        error_type, reason = AccountErrorDetector.detect_error_type(
            error,
            error_code="ThrottlingException"
        )

        assert error_type == AccountErrorType.RATE_LIMITED


class TestNetworkErrorDetection:
    """测试网络错误检测"""

    def test_detects_connection_timeout(self):
        """测试：检测连接超时"""
        error = Exception("Connection timeout after 30 seconds")

        error_type, reason = AccountErrorDetector.detect_error_type(error)

        assert error_type == AccountErrorType.NETWORK_ERROR

    def test_detects_network_unreachable(self):
        """测试：检测网络不可达"""
        error = Exception("Network unreachable")

        error_type, reason = AccountErrorDetector.detect_error_type(error)

        assert error_type == AccountErrorType.NETWORK_ERROR


class TestErrorActionDecisions:
    """测试错误处理决策"""

    def test_should_disable_for_suspension(self):
        """测试：暂停类型应该禁用账户"""
        assert AccountErrorDetector.should_disable_account(AccountErrorType.SUSPENDED) is True

    def test_should_disable_for_quota_exceeded(self):
        """测试：配额耗尽应该禁用账户"""
        assert AccountErrorDetector.should_disable_account(AccountErrorType.QUOTA_EXCEEDED) is True

    def test_should_not_disable_for_rate_limit(self):
        """测试：临时限速不应该禁用账户"""
        assert AccountErrorDetector.should_disable_account(AccountErrorType.RATE_LIMITED) is False

    def test_should_mark_rate_limited(self):
        """测试：应该标记限速"""
        assert AccountErrorDetector.should_mark_rate_limited(AccountErrorType.RATE_LIMITED) is True

    def test_get_retry_delay_for_rate_limit(self):
        """测试：获取限速重试延迟"""
        delay = AccountErrorDetector.get_retry_delay(AccountErrorType.RATE_LIMITED)
        assert delay == 60  # 1分钟

    def test_no_retry_for_suspension(self):
        """测试：暂停不重试"""
        delay = AccountErrorDetector.get_retry_delay(AccountErrorType.SUSPENDED)
        assert delay is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
