#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Demo：从前端源码识别组件并生成可靠用例草案（不依赖 LLM 也可跑通）。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SAMPLE = '''
export function CheckoutPage() {
  return (
    <div>
      <input data-testid="coupon-input" aria-label="优惠码" placeholder="优惠码" />
      <button data-testid="pay-button" onClick={onPay}>去支付</button>
      <Dialog data-testid="confirm-dialog" aria-label="确认支付">
        <button data-testid="confirm-pay">确认</button>
      </Dialog>
    </div>
  );
}
'''


def main() -> int:
    from ai_modules.code_intel.ui_agent import (
        analyze_frontend_ui,
        generate_reliable_cases_from_frontend,
    )

    analysis = analyze_frontend_ui(file_snippets={"src/Checkout.tsx": SAMPLE})
    drafts, warns, meta = generate_reliable_cases_from_frontend(
        file_snippets={"src/Checkout.tsx": SAMPLE},
        base_url="https://shop.example.com/checkout",
        git_sha="demo-sha",
        use_llm=False,
    )
    out_dir = ROOT / "artifacts" / "frontend-ui-agent"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "drafts.json").write_text(
        json.dumps({"drafts": drafts, "warnings": warns, "meta": meta}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(analysis.get("inventory", {}).get("summary"))
    print(f"drafts={len(drafts)} -> {out_dir}")
    for d in drafts:
        print("-", d.get("case_name"), "stability=", d.get("stability_score"))
    return 0 if drafts else 1


if __name__ == "__main__":
    raise SystemExit(main())
