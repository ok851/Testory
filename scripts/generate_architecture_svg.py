# -*- coding: utf-8 -*-
"""生成 docs/assets/architecture_local_16x9.svg（UTF-8）。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "assets" / "architecture_local_16x9.svg"

SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1920 1080" width="1920" height="1080">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0f172a"/>
      <stop offset="100%" style="stop-color:#1e293b"/>
    </linearGradient>
    <linearGradient id="boxCore" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#1d4ed8"/>
      <stop offset="100%" style="stop-color:#1e3a8a"/>
    </linearGradient>
    <linearGradient id="boxWeb" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#059669"/>
      <stop offset="100%" style="stop-color:#047857"/>
    </linearGradient>
    <linearGradient id="boxDesk" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#7c3aed"/>
      <stop offset="100%" style="stop-color:#5b21b6"/>
    </linearGradient>
    <linearGradient id="boxOpt" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" style="stop-color:#475569"/>
      <stop offset="100%" style="stop-color:#334155"/>
    </linearGradient>
    <marker id="arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">
      <path d="M0,0 L9,3 L0,6 Z" fill="#94a3b8"/>
    </marker>
    <filter id="shadow" x="-4%" y="-4%" width="108%" height="108%">
      <feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="#000000" flood-opacity="0.35"/>
    </filter>
  </defs>
  <rect width="1920" height="1080" fill="url(#bg)"/>
  <text x="960" y="72" text-anchor="middle" fill="#f8fafc" font-family="Microsoft YaHei, Segoe UI, sans-serif" font-size="40" font-weight="700">HuFirst UAT - &#26412;&#22320;&#28151;&#25490;&#33258;&#21160;&#21270;&#26550;&#26500;</text>
  <text x="960" y="118" text-anchor="middle" fill="#94a3b8" font-family="Microsoft YaHei, Segoe UI, sans-serif" font-size="20">16:9 | Web (Playwright) + Desktop (pywinauto) | DEPLOYMENT_PROFILE=local</text>
  <rect x="80" y="150" width="1760" height="860" rx="20" fill="none" stroke="#64748b" stroke-width="2" stroke-dasharray="12 8"/>
  <text x="110" y="188" fill="#cbd5e1" font-family="Microsoft YaHei, Segoe UI, sans-serif" font-size="18" font-weight="600">&#29992;&#25143; Windows &#26412;&#26426;&#65288;&#20132;&#20114;&#24335;&#26700;&#38754;&#20250;&#35805;&#65289;</text>
  <rect x="120" y="220" width="280" height="100" rx="12" fill="url(#boxCore)" filter="url(#shadow)"/>
  <text x="260" y="262" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="20" font-weight="600">&#27983;&#35272;&#22120; UI</text>
  <text x="260" y="292" text-anchor="middle" fill="#bfdbfe" font-family="Segoe UI, sans-serif" font-size="14">http://127.0.0.1:5000</text>
  <rect x="480" y="200" width="360" height="140" rx="12" fill="url(#boxCore)" filter="url(#shadow)"/>
  <text x="660" y="248" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="22" font-weight="600">&#24179;&#21488;&#26680;&#24515; app.py</text>
  <text x="660" y="278" text-anchor="middle" fill="#bfdbfe" font-family="Microsoft YaHei, sans-serif" font-size="14">&#29992;&#20363;&#31649;&#29702; / &#35843;&#24230; / &#25253;&#21578;</text>
  <text x="660" y="302" text-anchor="middle" fill="#93c5fd" font-family="Segoe UI, sans-serif" font-size="13">SQLite data/</text>
  <rect x="900" y="210" width="200" height="120" rx="12" fill="#b45309" filter="url(#shadow)"/>
  <text x="1000" y="258" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="17" font-weight="600">&#26412;&#26426;&#25191;&#34892;&#38145;</text>
  <text x="1000" y="286" text-anchor="middle" fill="#fde68a" font-family="Segoe UI, sans-serif" font-size="12">execution_lock.py</text>
  <text x="1000" y="308" text-anchor="middle" fill="#fde68a" font-family="Segoe UI, sans-serif" font-size="11">.uat_execution.lock</text>
  <rect x="1160" y="200" width="320" height="140" rx="12" fill="url(#boxCore)" filter="url(#shadow)"/>
  <text x="1320" y="248" text-anchor="middle" fill="#ffffff" font-family="Segoe UI, sans-serif" font-size="20" font-weight="600">ExecutorFactory</text>
  <text x="1320" y="278" text-anchor="middle" fill="#bfdbfe" font-family="Segoe UI, sans-serif" font-size="14">execution_factory.py</text>
  <text x="1320" y="302" text-anchor="middle" fill="#93c5fd" font-family="Microsoft YaHei, sans-serif" font-size="13">automation_layer</text>
  <rect x="1160" y="360" width="320" height="56" rx="10" fill="#1e40af" opacity="0.85"/>
  <text x="1320" y="395" text-anchor="middle" fill="#e0e7ff" font-family="Microsoft YaHei, sans-serif" font-size="15">step_executor</text>
  <rect x="480" y="420" width="400" height="200" rx="14" fill="url(#boxWeb)" filter="url(#shadow)"/>
  <text x="680" y="468" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="22" font-weight="600">Web &#25191;&#34892;&#33410;&#28857;</text>
  <text x="680" y="500" text-anchor="middle" fill="#a7f3d0" font-family="Segoe UI, sans-serif" font-size="14">playwright_automation.py</text>
  <text x="680" y="532" text-anchor="middle" fill="#d1fae5" font-family="Segoe UI, sans-serif" font-size="13">Chromium / Chrome / Edge</text>
  <text x="680" y="562" text-anchor="middle" fill="#d1fae5" font-family="Segoe UI, sans-serif" font-size="12">PLAYWRIGHT_HEADLESS=0</text>
  <rect x="1040" y="420" width="400" height="200" rx="14" fill="url(#boxDesk)" filter="url(#shadow)"/>
  <text x="1240" y="468" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="22" font-weight="600">&#26700;&#38754;&#25191;&#34892;&#33410;&#28857;</text>
  <text x="1240" y="500" text-anchor="middle" fill="#ddd6fe" font-family="Segoe UI, sans-serif" font-size="14">desktop_automation.py</text>
  <text x="1240" y="532" text-anchor="middle" fill="#e9d5ff" font-family="Segoe UI, sans-serif" font-size="13">inprocess / DesktopWorker</text>
  <text x="1240" y="562" text-anchor="middle" fill="#e9d5ff" font-family="Microsoft YaHei, sans-serif" font-size="12">pywinauto</text>
  <rect x="1500" y="440" width="300" height="160" rx="12" fill="url(#boxDesk)" filter="url(#shadow)" opacity="0.92"/>
  <text x="1650" y="488" text-anchor="middle" fill="#ffffff" font-family="Microsoft YaHei, sans-serif" font-size="18" font-weight="600">&#38646;&#37197;&#32622;&#21457;&#29616;</text>
  <text x="1650" y="518" text-anchor="middle" fill="#ddd6fe" font-family="Segoe UI, sans-serif" font-size="13">desktop_discovery.py</text>
  <text x="1650" y="548" text-anchor="middle" fill="#e9d5ff" font-family="Microsoft YaHei, sans-serif" font-size="12">&#36873;&#25321;&#24403;&#21069;&#31383;&#21475;</text>
  <rect x="120" y="420" width="280" height="120" rx="12" fill="#334155" filter="url(#shadow)"/>
  <text x="260" y="468" text-anchor="middle" fill="#f1f5f9" font-family="Microsoft YaHei, sans-serif" font-size="18" font-weight="600">SQLite</text>
  <text x="260" y="498" text-anchor="middle" fill="#94a3b8" font-family="Microsoft YaHei, sans-serif" font-size="13">&#29992;&#20363; / &#27493;&#39588; / &#21382;&#21490;</text>
  <text x="120" y="700" fill="#94a3b8" font-family="Microsoft YaHei, sans-serif" font-size="17" font-weight="600">&#21487;&#36873;&#32452;&#20214;&#65288;Docker profile / &#35843;&#35797;&#32593;&#20851;&#65289;</text>
  <rect x="120" y="720" width="400" height="110" rx="10" fill="url(#boxOpt)" stroke="#64748b"/>
  <text x="320" y="758" text-anchor="middle" fill="#e2e8f0" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">embedded_browser_gateway</text>
  <text x="320" y="786" text-anchor="middle" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="12">profile: with-embedded-browser :8765</text>
  <rect x="560" y="720" width="400" height="110" rx="10" fill="url(#boxOpt)" stroke="#64748b"/>
  <text x="760" y="758" text-anchor="middle" fill="#e2e8f0" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">hermes-gateway</text>
  <text x="760" y="786" text-anchor="middle" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="12">Testory AI :8642</text>
  <rect x="1000" y="720" width="400" height="110" rx="10" fill="url(#boxOpt)" stroke="#64748b"/>
  <text x="1200" y="758" text-anchor="middle" fill="#e2e8f0" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">desktop_automation_gateway</text>
  <text x="1200" y="786" text-anchor="middle" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="12">mode=gateway :8766</text>
  <rect x="1440" y="720" width="360" height="110" rx="10" fill="url(#boxOpt)" stroke="#64748b"/>
  <text x="1620" y="758" text-anchor="middle" fill="#e2e8f0" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">Docker uat-platform</text>
  <text x="1620" y="786" text-anchor="middle" fill="#94a3b8" font-family="Microsoft YaHei, sans-serif" font-size="12">&#20165; Web &#65292;&#26080;&#26700;&#38754;&#27493;&#39588;</text>
  <line x1="400" y1="270" x2="478" y2="270" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="840" y1="270" x2="898" y2="270" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="1100" y1="270" x2="1158" y2="270" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="1280" y1="400" x2="880" y2="418" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="1360" y1="400" x2="1180" y2="418" stroke="#94a3b8" stroke-width="2" marker-end="url(#arrow)"/>
  <line x1="1440" y1="520" x2="1498" y2="520" stroke="#c4b5fd" stroke-width="2" marker-end="url(#arrow)"/>
  <rect x="120" y="880" width="1680" height="110" rx="12" fill="#0f172a" stroke="#475569"/>
  <text x="160" y="920" fill="#f8fafc" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">Start:</text>
  <text x="230" y="920" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="14">packaging/run_uat_local.ps1</text>
  <text x="160" y="952" fill="#f8fafc" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">Docker:</text>
  <text x="240" y="952" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="14">docker compose --profile full-stack up -d</text>
  <text x="160" y="984" fill="#f8fafc" font-family="Segoe UI, sans-serif" font-size="15" font-weight="600">Docs:</text>
  <text x="220" y="984" fill="#94a3b8" font-family="Segoe UI, sans-serif" font-size="14">docs/ARCHITECTURE_LOCAL.md | docs/DOCKER_COMPOSE.md</text>
</svg>
"""


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(SVG.strip() + "\n", encoding="utf-8")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
