import copy
import hashlib
import json
import re
from typing import Any, Dict, Tuple


class CloudDataDesensitizer:
    """
    Enforce full masking before any cloud LLM transmission.
    """

    _IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
    _DOMAIN_PATTERN = re.compile(
        r"\b(?=.{1,255}\b)([a-zA-Z0-9-]{1,63}\.)+[a-zA-Z]{2,63}\b"
    )
    _URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
    _EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
    _PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
    _DOM_ATTR_PATTERN = re.compile(
        r"(id|name|class|value|placeholder|data-[a-zA-Z0-9_-]+)\s*=\s*['\"][^'\"]+['\"]",
        re.IGNORECASE,
    )

    _SENSITIVE_KEY_HINTS = (
        "password",
        "passwd",
        "secret",
        "token",
        "authorization",
        "cookie",
        "username",
        "account",
        "email",
        "mobile",
        "phone",
        "address",
        "idcard",
        "domain",
        "host",
        "ip",
    )

    def __init__(self) -> None:
        self._counter: Dict[str, int] = {}
        self._mapping: Dict[str, str] = {}

    def sanitize_payload(self, payload: Any) -> Tuple[Any, Dict[str, str], str]:
        """
        Returns:
            sanitized_payload: payload after masking
            mapping: placeholder -> original value (for local audit only)
            checksum: deterministic checksum for integrity tracing
        """
        self._counter = {}
        self._mapping = {}

        cloned_payload = copy.deepcopy(payload)
        sanitized = self._sanitize_object(cloned_payload, "")
        checksum = self._checksum(sanitized)
        return sanitized, self._mapping.copy(), checksum

    def _sanitize_object(self, value: Any, key_path: str) -> Any:
        if isinstance(value, dict):
            sanitized = {}
            for k, v in value.items():
                next_path = f"{key_path}.{k}" if key_path else str(k)
                sanitized[k] = self._sanitize_object(v, next_path)
            return sanitized

        if isinstance(value, list):
            return [self._sanitize_object(v, key_path) for v in value]

        if isinstance(value, str):
            return self._sanitize_text(value, key_path)

        return value

    def _sanitize_text(self, text: str, key_path: str) -> str:
        masked = text

        if self._is_sensitive_key_path(key_path):
            return self._replace_full_value(masked, "BUSINESS_FIELD")

        masked = self._replace_pattern(masked, self._URL_PATTERN, "BUSINESS_SYSTEM")
        masked = self._replace_pattern(masked, self._IP_PATTERN, "INTRANET_IP")
        masked = self._replace_pattern(masked, self._EMAIL_PATTERN, "ACCOUNT")
        masked = self._replace_pattern(masked, self._PHONE_PATTERN, "ACCOUNT")
        masked = self._replace_pattern(masked, self._DOMAIN_PATTERN, "DOMAIN")
        masked = self._replace_dom_attributes(masked)
        return masked

    def _replace_dom_attributes(self, text: str) -> str:
        def replacer(match: re.Match) -> str:
            original = match.group(0)
            key_name = match.group(1)
            placeholder = self._build_placeholder("DOM_FIELD")
            self._mapping[placeholder] = original
            return f'{key_name}="{placeholder}"'

        return self._DOM_ATTR_PATTERN.sub(replacer, text)

    def _replace_pattern(self, text: str, pattern: re.Pattern, token_name: str) -> str:
        def replacer(match: re.Match) -> str:
            original = match.group(0)
            placeholder = self._build_placeholder(token_name)
            self._mapping[placeholder] = original
            return placeholder

        return pattern.sub(replacer, text)

    def _replace_full_value(self, text: str, token_name: str) -> str:
        placeholder = self._build_placeholder(token_name)
        self._mapping[placeholder] = text
        return placeholder

    def _build_placeholder(self, token_name: str) -> str:
        current = self._counter.get(token_name, 0) + 1
        self._counter[token_name] = current
        return f"{token_name}_{current:03d}"

    def _is_sensitive_key_path(self, key_path: str) -> bool:
        key_path_lower = key_path.lower()
        return any(hint in key_path_lower for hint in self._SENSITIVE_KEY_HINTS)

    def _checksum(self, payload: Any) -> str:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
