# -*- coding: utf-8 -*-
"""Skill 附录 B 质量校验（R11）。

用法:
  python -m skills.skill_quality
  python -m skills.skill_quality --all
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

_ROOT = Path(__file__).resolve().parents[1]
_BUNDLED = Path(__file__).resolve().parent / "bundled"

# 复赛 / 产品门禁 Skill（必须过附录 B）
GATE_SKILLS = (
    "testory-web-browser",
    "testory-api-http",
    "testory-windows-desktop",
    "testory-android-mobile",
    "testory-cross-end",
    "testory-risk-guard",
)

_IO_HEAD = re.compile(r"(输入|输出|schema|i/o|契约)", re.I)
_FAIL_HEAD = re.compile(r"(失败|错误|超时|诚实)", re.I)
_SEC_HEAD = re.compile(r"(安全|风险|边界|riskguard|hitl)", re.I)
_H2 = re.compile(r"^##\s+(.+)$", re.M)


def _parse_frontmatter(text: str) -> Tuple[Optional[Dict[str, Any]], str]:
    if not text.startswith("---"):
        return None, text
    end = text.find("\n---", 3)
    if end < 0:
        return None, text
    raw = text[3:end].strip()
    body = text[end + 4 :]
    if yaml is None:
        # 无 PyYAML：极简解析 name/description/version/format
        meta: Dict[str, Any] = {}
        for line in raw.splitlines():
            if ":" not in line or line.strip().startswith("#"):
                continue
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip("'\"")
            if k in ("name", "description", "version", "format", "source"):
                meta[k] = v
            if k == "metadata":
                meta.setdefault("metadata", {})
        # 粗提取 platform / risk_default
        m_plat = re.search(r"platform:\s*([^\s#]+)", raw)
        m_risk = re.search(r"risk_default:\s*([^\s#]+)", raw)
        testory: Dict[str, Any] = {}
        if m_plat:
            testory["platform"] = m_plat.group(1).strip()
        if m_risk:
            testory["risk_default"] = m_risk.group(1).strip()
        if testory:
            meta.setdefault("metadata", {})["testory"] = testory
        return meta, body
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        return None, body
    return data, body


def _heading_hits(body: str) -> Dict[str, bool]:
    titles = [m.group(1).strip() for m in _H2.finditer(body or "")]
    return {
        "io": any(_IO_HEAD.search(t) for t in titles),
        "failure": any(_FAIL_HEAD.search(t) for t in titles),
        "security": any(_SEC_HEAD.search(t) for t in titles),
    }


def validate_skill_md(path: Path) -> Dict[str, Any]:
    """校验单个 SKILL.md。返回 {ok, path, errors[], warnings[]}。"""
    errors: List[str] = []
    warnings: List[str] = []
    if not path.is_file():
        return {"ok": False, "path": str(path), "errors": ["文件不存在"], "warnings": []}

    text = path.read_text(encoding="utf-8")
    meta, body = _parse_frontmatter(text)
    if not meta:
        errors.append("缺少或无法解析 YAML frontmatter")
        return {"ok": False, "path": str(path), "errors": errors, "warnings": warnings}

    for key in ("name", "description", "version", "format"):
        if not str(meta.get(key) or "").strip():
            errors.append(f"frontmatter 缺少 {key}")

    testory = ((meta.get("metadata") or {}) if isinstance(meta.get("metadata"), dict) else {})
    testory = testory.get("testory") if isinstance(testory, dict) else None
    if not isinstance(testory, dict) or not str(testory.get("platform") or "").strip():
        errors.append("metadata.testory.platform 必填")
    else:
        risk = str(testory.get("risk_default") or "").strip().upper()
        if risk and risk not in ("L0", "L1", "L2"):
            errors.append(f"risk_default 非法: {risk}")
        if not risk:
            warnings.append("建议填写 metadata.testory.risk_default (L0|L1|L2)")

    hits = _heading_hits(body)
    if not hits["io"]:
        errors.append("缺少「输入/输出/Schema」类二级标题")
    if not hits["failure"]:
        errors.append("缺少「失败/错误/超时」类二级标题")
    if not hits["security"]:
        errors.append("缺少「安全/风险/边界」类二级标题")

    # 正文最低字数，避免空壳标题
    for label, ok in (("io", hits["io"]), ("failure", hits["failure"]), ("security", hits["security"])):
        if ok and len(body.strip()) < 80:
            warnings.append(f"正文过短，请确认 {label} 章节有实质内容")

    return {
        "ok": not errors,
        "path": str(path),
        "name": meta.get("name"),
        "errors": errors,
        "warnings": warnings,
        "headings": hits,
    }


def iter_gate_skill_paths(bundled: Optional[Path] = None) -> List[Path]:
    root = bundled or _BUNDLED
    return [root / sid / "SKILL.md" for sid in GATE_SKILLS]


def validate_gate_skills(bundled: Optional[Path] = None) -> Dict[str, Any]:
    results = [validate_skill_md(p) for p in iter_gate_skill_paths(bundled)]
    failed = [r for r in results if not r.get("ok")]
    return {
        "ok": not failed,
        "checked": len(results),
        "failed": len(failed),
        "results": results,
    }


def validate_all_bundled(bundled: Optional[Path] = None) -> Dict[str, Any]:
    root = bundled or _BUNDLED
    paths = sorted(root.glob("*/SKILL.md"))
    results = [validate_skill_md(p) for p in paths]
    failed = [r for r in results if not r.get("ok")]
    return {
        "ok": not failed,
        "checked": len(results),
        "failed": len(failed),
        "results": results,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Validate Testory skills (Appendix B)")
    p.add_argument("--all", action="store_true", help="校验全部 bundled Skill")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    report = validate_all_bundled() if args.all else validate_gate_skills()
    if args.json:
        import json

        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for r in report["results"]:
            status = "OK" if r["ok"] else "FAIL"
            print(f"[{status}] {r.get('name') or r['path']}")
            for e in r.get("errors") or []:
                print(f"  - ERROR: {e}")
            for w in r.get("warnings") or []:
                print(f"  - WARN: {w}")
        print(f"\nchecked={report['checked']} failed={report['failed']}")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
