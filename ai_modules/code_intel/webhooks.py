# -*- coding: utf-8 -*-
"""可选 SCM Webhook：GitHub / GitLab 签名校验与 payload 归一化。"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any, Dict, List, Optional, Tuple


def webhook_secrets() -> Dict[str, str]:
    return {
        "github": (os.environ.get("TESTORY_GITHUB_WEBHOOK_SECRET") or "").strip(),
        "gitlab": (os.environ.get("TESTORY_GITLAB_WEBHOOK_SECRET") or "").strip(),
    }


def verify_github_signature(body: bytes, signature_header: str, secret: str) -> bool:
    if not secret:
        return False
    sig = (signature_header or "").strip()
    if sig.startswith("sha256="):
        sig = sig[7:]
    dig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(dig, sig)


def verify_gitlab_token(token_header: str, secret: str) -> bool:
    if not secret:
        return False
    return hmac.compare_digest((token_header or "").strip(), secret)


def normalize_github_event(
    event: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """归一化为 code-change 输入字段。"""
    event = (event or "").strip().lower()
    if event not in ("push", "pull_request"):
        return None

    repo = ""
    try:
        repo = (
            ((payload.get("repository") or {}).get("full_name"))
            or ((payload.get("repository") or {}).get("clone_url"))
            or ""
        )
    except Exception:
        repo = ""

    if event == "push":
        branch = str(payload.get("ref") or "").replace("refs/heads/", "")
        git_sha = str(payload.get("after") or "")[:64]
        commits = payload.get("commits") or []
        changed: List[str] = []
        msgs: List[str] = []
        for c in commits[:30]:
            if not isinstance(c, dict):
                continue
            msgs.append(str(c.get("message") or ""))
            for key in ("added", "modified", "removed"):
                for f in c.get(key) or []:
                    changed.append(str(f))
        return {
            "trigger_source": "github",
            "repo": repo,
            "branch": branch,
            "git_sha": git_sha,
            "changed_files": sorted(set(changed))[:200],
            "mr_description": "\n".join(msgs)[:8000],
            "diff": "",
            "event": event,
        }

    # pull_request
    action = str(payload.get("action") or "")
    if action not in ("opened", "synchronize", "reopened", "ready_for_review"):
        return None
    pr = payload.get("pull_request") or {}
    branch = str((pr.get("head") or {}).get("ref") or "")
    git_sha = str((pr.get("head") or {}).get("sha") or "")[:64]
    files: List[str] = []
    # GitHub PR webhook 默认不含文件列表；留给 CI 补 diff
    return {
        "trigger_source": "github",
        "repo": repo,
        "branch": branch,
        "git_sha": git_sha,
        "changed_files": files,
        "mr_description": f"{pr.get('title') or ''}\n{pr.get('body') or ''}"[:8000],
        "diff": "",
        "event": event,
        "pr_number": (payload.get("number") or pr.get("number")),
        "pr_url": pr.get("html_url") or "",
    }


def normalize_gitlab_event(
    event: str,
    payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    event = (event or "").strip()
    event_l = event.lower()
    if event_l not in ("push hook", "merge request hook", "push", "merge_request"):
        # X-Gitlab-Event: Push Hook / Merge Request Hook
        if "push" not in event_l and "merge" not in event_l:
            return None

    project = payload.get("project") or {}
    repo = str(project.get("path_with_namespace") or project.get("http_url") or "")

    if "merge" in event_l:
        attrs = payload.get("object_attributes") or {}
        action = str(attrs.get("action") or "")
        if action and action not in ("open", "update", "reopen", "merge"):
            # 仍接受无 action 的旧 payload
            if action not in ("",):
                pass
        branch = str(attrs.get("source_branch") or "")
        git_sha = str(attrs.get("last_commit", {}).get("id") or attrs.get("merge_commit_sha") or "")[:64]
        if not git_sha and isinstance(attrs.get("last_commit"), dict):
            git_sha = str(attrs["last_commit"].get("id") or "")[:64]
        return {
            "trigger_source": "gitlab",
            "repo": repo,
            "branch": branch,
            "git_sha": git_sha,
            "changed_files": [],
            "mr_description": f"{attrs.get('title') or ''}\n{attrs.get('description') or ''}"[:8000],
            "diff": "",
            "event": "merge_request",
            "pr_url": attrs.get("url") or "",
        }

    # push
    branch = str(payload.get("ref") or "").replace("refs/heads/", "")
    git_sha = str(payload.get("checkout_sha") or payload.get("after") or "")[:64]
    changed: List[str] = []
    msgs: List[str] = []
    for c in payload.get("commits") or []:
        if not isinstance(c, dict):
            continue
        msgs.append(str(c.get("message") or ""))
        for key in ("added", "modified", "removed"):
            for f in c.get(key) or []:
                changed.append(str(f))
    return {
        "trigger_source": "gitlab",
        "repo": repo,
        "branch": branch,
        "git_sha": git_sha,
        "changed_files": sorted(set(changed))[:200],
        "mr_description": "\n".join(msgs)[:8000],
        "diff": "",
        "event": "push",
    }


def parse_webhook(
    *,
    provider: str,
    headers: Dict[str, str],
    body: bytes,
) -> Tuple[Optional[Dict[str, Any]], Optional[str], int]:
    """
    返回 (normalized_payload, error, http_status)。
    未配置 secret 时拒绝（防未鉴权开放）。
    """
    secrets = webhook_secrets()
    provider = (provider or "").strip().lower()
    try:
        payload = json.loads(body.decode("utf-8") if body else "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None, "invalid JSON body", 400
    if not isinstance(payload, dict):
        return None, "payload must be object", 400

    hdr = {str(k).lower(): str(v) for k, v in (headers or {}).items()}

    if provider == "github":
        secret = secrets["github"]
        if not secret:
            return None, "TESTORY_GITHUB_WEBHOOK_SECRET 未配置", 503
        sig = hdr.get("x-hub-signature-256") or hdr.get("x-hub-signature") or ""
        if not verify_github_signature(body, sig, secret):
            return None, "invalid GitHub signature", 401
        event = hdr.get("x-github-event") or ""
        norm = normalize_github_event(event, payload)
        if not norm:
            return None, f"ignored GitHub event: {event}", 202
        return norm, None, 200

    if provider == "gitlab":
        secret = secrets["gitlab"]
        if not secret:
            return None, "TESTORY_GITLAB_WEBHOOK_SECRET 未配置", 503
        token = hdr.get("x-gitlab-token") or ""
        if not verify_gitlab_token(token, secret):
            return None, "invalid GitLab token", 401
        event = hdr.get("x-gitlab-event") or ""
        norm = normalize_gitlab_event(event, payload)
        if not norm:
            return None, f"ignored GitLab event: {event}", 202
        return norm, None, 200

    return None, "unknown provider", 400
