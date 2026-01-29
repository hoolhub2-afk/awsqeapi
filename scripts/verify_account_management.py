#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Account management feature verification script
"""
import sys
import io

# Set UTF-8 output
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

def verify_error_detection():
    """Verify error detection functionality"""
    print("=" * 60)
    print("Verify Error Detection")
    print("=" * 60)

    from src.integrations.amazonq_client import (
        _is_quota_exhausted_error,
        _is_account_suspended_error
    )

    tests = [
        # Quota exhaustion tests
        ('quota-json', '{"__type": "com.amazon.aws.codewhisperer#ThrottlingException", "reason": "MONTHLY_REQUEST_COUNT"}',
         _is_quota_exhausted_error, True),
        ('quota-text', 'ThrottlingException: MONTHLY_REQUEST_COUNT',
         _is_quota_exhausted_error, True),

        # Account suspension tests
        ('suspend-json', '{"reason": "TEMPORARILY_SUSPENDED"}',
         _is_account_suspended_error, True),
        ('access-denied-json', '{"__type": "AccessDeniedException"}',
         _is_account_suspended_error, True),
        ('forbidden-text', '403 Forbidden',
         _is_account_suspended_error, True),

        # Negative tests
        ('non-error-text', 'Connection successful',
         _is_quota_exhausted_error, False),
        ('non-error-text', 'Connection successful',
         _is_account_suspended_error, False),
    ]

    passed = 0
    failed = 0

    for name, error_text, func, expected in tests:
        result = func(error_text)
        status = "PASS" if result == expected else "FAIL"

        if result == expected:
            passed += 1
            print(f"[{status}] {name}: {result}")
        else:
            failed += 1
            print(f"[{status}] {name}: expected {expected}, got {result}")

    print(f"\nTotal: {passed} passed, {failed} failed")
    return failed == 0


def verify_imports():
    """Verify key module imports"""
    print("\n" + "=" * 60)
    print("Verify Module Imports")
    print("=" * 60)

    modules = [
        ('replicate', 'src.integrations.replicate'),
        ('account_service', 'src.services.account_service'),
        ('quota_service', 'src.services.quota_service'),
        ('session_service', 'src.services.session_service'),
        ('openai router', 'src.routers.openai'),
    ]

    passed = 0
    failed = 0

    for name, module_path in modules:
        try:
            __import__(module_path)
            print(f"[PASS] {name}: {module_path}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")
    return failed == 0


def verify_exception_types():
    """Verify exception types"""
    print("\n" + "=" * 60)
    print("Verify Exception Types")
    print("=" * 60)

    from src.integrations.replicate import (
        QuotaExhaustedException,
        AccountSuspendedException
    )

    tests = [
        ('QuotaExhaustedException', QuotaExhaustedException, "Test quota"),
        ('AccountSuspendedException', AccountSuspendedException, "Test suspend"),
    ]

    passed = 0
    failed = 0

    for name, exc_class, message in tests:
        try:
            raise exc_class(message)
        except exc_class as e:
            if message in str(e):
                print(f"[PASS] {name}: Exception raised and caught correctly")
                passed += 1
            else:
                print(f"[FAIL] {name}: Message mismatch")
                failed += 1
        except Exception as e:
            print(f"[FAIL] {name}: Unexpected exception {type(e)}")
            failed += 1

    print(f"\nTotal: {passed} passed, {failed} failed")
    return failed == 0


def main():
    """Main function"""
    print("\n" + "=" * 60)
    print("Account Management Verification")
    print("=" * 60 + "\n")

    results = []

    # Run verifications
    results.append(("Module Imports", verify_imports()))
    results.append(("Error Detection", verify_error_detection()))
    results.append(("Exception Types", verify_exception_types()))

    # Summary
    print("\n" + "=" * 60)
    print("Verification Summary")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            all_passed = False

    if all_passed:
        print("\nAll verifications passed!")
        return 0
    else:
        print("\nSome verifications failed, please check the logs")
        return 1


if __name__ == "__main__":
    sys.exit(main())
