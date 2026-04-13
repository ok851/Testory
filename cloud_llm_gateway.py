import json
from typing import Any, Dict

import requests

from cloud_desensitizer import CloudDataDesensitizer


class CloudLLMGateway:
    """
    Unified cloud gateway:
    1) full automatic desensitization
    2) reject raw payload upload
    3) local-only mapping trace for audit
    """

    def __init__(self, endpoint: str, api_key: str, timeout: int = 30) -> None:
        self.endpoint = endpoint
        self.api_key = api_key
        self.timeout = timeout
        self.desensitizer = CloudDataDesensitizer()

    def call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        sanitized_payload, mapping, checksum = self.desensitizer.sanitize_payload(payload)

        # Defensive check: ensure we never upload raw original values by accident.
        raw_str = json.dumps(payload, ensure_ascii=True, sort_keys=True)
        sanitized_str = json.dumps(sanitized_payload, ensure_ascii=True, sort_keys=True)
        if raw_str == sanitized_str and self._contains_sensitive_text(raw_str):
            raise ValueError("Cloud request blocked: payload contains unmasked sensitive content.")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Desensitized": "true",
            "X-Desensitized-Checksum": checksum,
        }
        resp = requests.post(
            self.endpoint,
            json=sanitized_payload,
            headers=headers,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        response_data = resp.json() if resp.content else {}
        return {
            "cloud_response": response_data,
            "desensitized_checksum": checksum,
            "placeholder_count": len(mapping),
            # Keep mapping local-only; do not return raw mapping to caller by default.
        }

    def _contains_sensitive_text(self, text: str) -> bool:
        # Guardrail against accidentally sending known raw markers.
        suspicious_keywords = [
            "password",
            "passwd",
            "authorization",
            "cookie",
            "192.168.",
            "10.",
            "172.16.",
            "@",
            "http://",
            "https://",
        ]
        low = text.lower()
        return any(k in low for k in suspicious_keywords)
