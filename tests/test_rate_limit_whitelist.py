"""
测试速率限制IP白名单功能
"""
import sys
import os
import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.security.auth import RateLimiter


class TestRateLimitWithoutWhitelist:
    """测试没有白名单时的速率限制"""

    def test_should_limit_requests_after_threshold(self):
        """当没有白名单时，应该在达到阈值后限制请求"""
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=None)
        identifier = "admin:192.168.1.100"

        # 前5次请求应该通过
        for i in range(5):
            assert limiter.is_allowed(identifier) is True, f"请求 {i+1} 应该被允许"

        # 第6次请求应该被拒绝
        assert limiter.is_allowed(identifier) is False, "第6次请求应该被拒绝"


class TestRateLimitWithWhitelist:
    """测试有白名单时的速率限制"""

    def test_whitelisted_ip_should_bypass_rate_limit(self):
        """白名单中的IP不受速率限制"""
        whitelist = ["192.168.1.100", "10.0.0.0/24"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        # 白名单中的IP，模拟100次请求都应该通过
        identifier = "admin:192.168.1.100"
        for i in range(100):
            assert limiter.is_allowed(identifier) is True, f"白名单IP请求 {i+1} 应该被允许"

    def test_whitelisted_cidr_should_bypass_rate_limit(self):
        """白名单网段中的IP不受速率限制"""
        whitelist = ["192.168.1.100", "10.0.0.0/24"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        # 白名单网段中的IP
        identifier = "admin:10.0.0.50"
        for i in range(100):
            assert limiter.is_allowed(identifier) is True, f"白名单网段IP请求 {i+1} 应该被允许"

    def test_non_whitelisted_ip_should_be_rate_limited(self):
        """非白名单中的IP应该受速率限制"""
        whitelist = ["192.168.1.100", "10.0.0.0/24"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        identifier = "admin:203.0.113.1"
        allowed_count = 0
        for i in range(10):
            if limiter.is_allowed(identifier):
                allowed_count += 1

        assert allowed_count == 5, f"非白名单IP应该只有5次请求通过，实际通过 {allowed_count} 次"


class TestIdentifierFormats:
    """测试不同的identifier格式"""

    def test_admin_ip_format(self):
        """测试 'admin:ip' 格式"""
        whitelist = ["192.168.1.100"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        assert limiter.is_allowed("admin:192.168.1.100") is True

    def test_direct_ip_format(self):
        """测试直接IP格式"""
        whitelist = ["192.168.1.100"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        assert limiter.is_allowed("192.168.1.100") is True

    def test_non_whitelisted_ip_first_request_allowed(self):
        """非白名单IP的第一次请求应该通过"""
        whitelist = ["192.168.1.100"]
        limiter = RateLimiter(max_requests_per_minute=5, ip_whitelist=whitelist)

        assert limiter.is_allowed("admin:203.0.113.1") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
