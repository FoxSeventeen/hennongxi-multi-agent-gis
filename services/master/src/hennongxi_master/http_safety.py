"""Fail-closed response declaration checks shared by bounded HTTP adapters."""

from __future__ import annotations

import httpx


def response_body_is_declared_unsafe(
    response: httpx.Response,
    *,
    max_bytes: int,
) -> bool:
    """Reject encoded or non-canonical lengths before any response body is read."""

    content_encoding = response.headers.get("content-encoding")
    if content_encoding is not None and content_encoding.strip(" \t").lower() != "identity":
        return True

    declared_length = response.headers.get("content-length")
    if declared_length is None:
        return False
    normalized_length = declared_length.strip(" \t")
    if (
        not normalized_length
        or not normalized_length.isascii()
        or not normalized_length.isdecimal()
    ):
        return True
    significant_length = normalized_length.lstrip("0") or "0"
    maximum = str(max_bytes)
    return len(significant_length) > len(maximum) or (
        len(significant_length) == len(maximum) and significant_length > maximum
    )
