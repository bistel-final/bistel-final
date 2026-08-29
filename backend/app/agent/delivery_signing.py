"""Backend→n8n raw-body delivery 서명의 공용 경계."""

from __future__ import annotations

import hashlib
import hmac


def signed_delivery_headers(
    raw_body: bytes,
    secret: bytes,
    timestamp: int,
) -> dict[str, str]:
    """EMAIL·MES가 공유하는 timestamp/raw-body HMAC header를 만든다."""

    if not isinstance(raw_body, bytes) or not raw_body:
        raise ValueError("DELIVERY_RAW_BODY_INVALID")
    if not isinstance(secret, bytes) or not secret:
        raise ValueError("DELIVERY_SECRET_INVALID")
    if type(timestamp) is not int:
        raise ValueError("DELIVERY_TIMESTAMP_INVALID")
    timestamp_text = str(timestamp)
    signature = hmac.new(
        secret,
        timestamp_text.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "content-type": "application/json",
        "x-delivery-timestamp": timestamp_text,
        "x-delivery-signature": f"sha256={signature}",
    }


__all__ = ["signed_delivery_headers"]
