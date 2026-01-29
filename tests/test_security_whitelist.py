import time

from src.security.auth import IPWhitelist


def test_dynamic_ip_added_after_allow():
    whitelist = IPWhitelist(["127.0.0.1"], auto_ttl=60)
    assert whitelist.is_allowed("127.0.0.1")
    assert not whitelist.is_allowed("8.8.8.8")

    whitelist.allow_ip("8.8.8.8", ttl=1)
    assert whitelist.is_allowed("8.8.8.8")

    # Expire the entry manually and ensure it is removed on next check
    whitelist.dynamic_ips["8.8.8.8"] = time.time() - 10
    assert not whitelist.is_allowed("8.8.8.8")


def test_dynamic_ip_disabled_when_ttl_zero():
    whitelist = IPWhitelist(["10.0.0.1"], auto_ttl=0)
    whitelist.allow_ip("8.8.4.4")
    assert not whitelist.is_allowed("8.8.4.4")


def test_empty_whitelist_allows_all_when_default_allow_all_enabled():
    whitelist = IPWhitelist([], default_allow_all_when_empty=True)
    assert whitelist.is_allowed("8.8.8.8")


def test_empty_whitelist_denies_non_local_when_default_allow_all_disabled():
    whitelist = IPWhitelist([], default_allow_all_when_empty=False)
    assert not whitelist.is_allowed("8.8.8.8")
    assert whitelist.is_allowed("127.0.0.1")


def test_dynamic_only_whitelist_allows_only_dynamic_when_no_static():
    whitelist = IPWhitelist([], default_allow_all_when_empty=False)
    whitelist.allow_ip("8.8.8.8", ttl=60)
    assert whitelist.is_allowed("8.8.8.8")
    assert not whitelist.is_allowed("1.1.1.1")
