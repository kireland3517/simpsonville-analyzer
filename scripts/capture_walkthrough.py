"""Capture walkthrough tab screenshots (requires server on :8765)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"
OUT.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.goto("http://127.0.0.1:8765/", wait_until="networkidle", timeout=60000)
    page.click('.nav-item[data-tab="walkthrough"]')
    page.wait_for_timeout(3000)
    page.screenshot(path=str(OUT / "walkthrough-desktop-1440.png"))
    page.set_viewport_size({"width": 390, "height": 844})
    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "walkthrough-mobile-390.png"))
    browser.close()
    print(f"Saved to {OUT}")
