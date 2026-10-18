"""Sanitize data before including it in user-visible projections or prompt references."""
import json
import re
from typing import Any


def safe_value(value: Any, depth: int = 0):
    """Allow structured user-visible values while excluding credentials/reasoning."""
    if depth > 8:
        return "[nested value]"
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if any(secret in normalized for secret in (
                "apikey", "password", "secret", "authorization", "credential", "cookie",
                "accesstoken", "refreshtoken", "thinking", "reasoning", "encryptedcontent",
            )):
                continue
            result[str(key)] = safe_value(item, depth + 1)
        return result
    if isinstance(value, list):
        return [safe_value(item, depth + 1) for item in value
                if not isinstance(item, dict) or item.get("type") not in ("thinking", "reasoning")]
    if isinstance(value, str):
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}\b", "[redacted]", value)
        value = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~-]+", "Bearer [redacted]", value)
        return value[:50000]
    return value


def safe_text(value: Any) -> str:
    cleaned = safe_value(value)
    return cleaned if isinstance(cleaned, str) else json.dumps(cleaned, ensure_ascii=False, default=str)
