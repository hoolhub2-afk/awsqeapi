"""
Delta utilities for streaming content deduplication.

This module provides functions for calculating incremental deltas between
strings, used to remove duplicate content from streaming responses.
"""

from typing import Tuple


def delta_by_prefix(previous: str, current: str) -> Tuple[str, str]:
    """
    Calculate the delta between two strings for streaming deduplication.

    This function computes the incremental difference between two strings,
    which is used to detect and remove duplicate content in streaming
    responses from upstream services like Amazon Q.

    The function handles four cases:
    1. Simple prefix: current starts with previous (common case)
    2. Substring: previous is found in current at position > 0
    3. Partial overlap: strings have overlapping suffixes/prefixes
    4. No overlap: strings have no relationship (concatenate)

    Args:
        previous: The previous full content from the stream
        current: The current content from the stream

    Returns:
        A tuple of (new_previous, delta) where:
        - new_previous: The complete content after incorporating current
        - delta: The incremental part that is new (empty if no new content)

    Examples:
        >>> delta_by_prefix("Hello", "Hello world")
        ('Hello world', ' world')

        >>> delta_by_prefix("Hello world", "Hello world")
        ('Hello world', '')

        >>> delta_by_prefix("Hello wor", "Hello world")
        ('Hello world', 'ld')
    """
    if not current:
        return previous, ""
    if not previous:
        return current, current

    # Case 1: current starts with previous, calculate delta
    if current.startswith(previous):
        delta = current[len(previous):]
        if delta:
            return current, delta
        return previous, ""

    # Case 2: previous is a substring of current (not at the start)
    # Find the position of previous in current
    idx = current.find(previous)
    # Only apply Case 2 if:
    # - previous is found at position > 0, AND
    # - previous is shorter than current (there's potential new content)
    if idx > 0 and len(previous) < len(current):
        # Found previous in current at position > 0
        delta = current[idx + len(previous):]
        if delta:  # There's actual new content after previous
            return previous + delta, delta

    # Case 3: Check for partial overlap at the end
    max_overlap = min(len(previous), len(current))
    for length in range(max_overlap, 0, -1):
        if previous.endswith(current[:length]):
            delta = current[length:]
            return previous + delta, delta

    # Case 4: No reasonable delta found, concatenate
    # This handles cases where upstream is sending complete content
    # rather than incremental updates
    return previous + current, current


# Backward compatibility alias
_delta_by_prefix = delta_by_prefix

__all__ = ["delta_by_prefix", "_delta_by_prefix"]
