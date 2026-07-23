# -*- coding: utf-8 -*-
"""Capture Testory UI screenshots for GOAI PPT."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path("docs/goai/out/screenshots")
OUT.mkdir(parents=True, exist_ok=True)
BASE = "http://127.0.0.1:5000"
USER = "admin"
PASS = "GoaiDemo2026!"

PAGES = [
    ("01_login", "/login"),
    ("02_ai_test", "/ai-test"),
    ("03_cross_end", "/cross-end"),
    ("04_mobile_testing", "/mobile-testing"),
    ("05_ai_hub", "/ai-hub"),
    ("06_projects", "/"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1.25,
        )
        page = context.new_page()

        # login page shot first
        page.goto(f"{BASE}/login", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUT / "01_login.png"), full_page=False)
        print("saved 01_login")

        # login via API cookie in same context
        resp = page.request.post(
            f"{BASE}/api/auth/login",
            data={"username": USER, "password": PASS},
        )
        # try JSON body if form fails
        if resp.status != 200:
            resp = page.request.post(
                f"{BASE}/api/auth/login",
                headers={"Content-Type": "application/json"},
                data=f'{{"username":"{USER}","password":"{PASS}"}}',
            )
        print("login status", resp.status, resp.text()[:120])

        # Also fill UI login as fallback
        if resp.status != 200:
            page.goto(f"{BASE}/login", wait_until="domcontentloaded")
            # try common selectors
            for sel in ['input[name="username"]', "#username", 'input[type="text"]']:
                if page.locator(sel).count():
                    page.fill(sel, USER)
                    break
            for sel in ['input[name="password"]', "#password", 'input[type="password"]']:
                if page.locator(sel).count():
                    page.fill(sel, PASS)
                    break
            page.click('button[type="submit"]')
            page.wait_for_timeout(2000)

        for name, path in PAGES[1:]:
            try:
                page.goto(f"{BASE}{path}", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(1200)
                # if redirected to login, stop
                if "/login" in page.url and name != "01_login":
                    print("redirected to login for", name)
                    continue
                page.screenshot(path=str(OUT / f"{name}.png"), full_page=False)
                print("saved", name, page.url)
            except Exception as e:
                print("fail", name, e)

        browser.close()
        print("done", OUT.resolve())


if __name__ == "__main__":
    main()
