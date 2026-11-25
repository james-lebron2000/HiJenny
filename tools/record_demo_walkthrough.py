#!/usr/bin/env python3
"""Record a *real* HiJenny demo walkthrough with Playwright.

Unlike the previous screenshot-only GIF, every cursor destination in the
recording is an actual DOM element and every click triggers a real state
change (page navigation / finding filter). The recorder stores before/after
screenshots plus a timeline JSON that ``tools/make_demo_gif.py`` animates.

Usage:
    ./scripts/demo.sh                                  # terminal 1
    python3 tools/record_demo_walkthrough.py           # terminal 2
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
VIEWPORT = {"width": 1600, "height": 1000}


def _get(base: str, path: str) -> dict:
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _rect(page, locator) -> dict:
    box = locator.bounding_box()
    if not box:
        raise RuntimeError(f"element has no bounding box: {locator}")
    return {"x": box["x"], "y": box["y"], "w": box["width"], "h": box["height"]}


def record(base: str, out: Path, project_id: str | None) -> dict:
    from playwright.sync_api import sync_playwright

    if not project_id:
        project_id = _get(base, "/api/projects")["projects"][0]["project_id"]

    out.mkdir(parents=True, exist_ok=True)
    timeline: list[dict] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            executable_path=CHROME if Path(CHROME).exists() else None,
            args=["--no-sandbox", "--disable-crash-reporter", "--hide-scrollbars"],
        )
        page = browser.new_page(viewport=VIEWPORT, device_scale_factor=1)
        page.goto(f"{base}/#/projects?project={project_id}", wait_until="networkidle")
        page.wait_for_timeout(700)
        shot = lambda name: page.screenshot(path=str(out / name), full_page=False)  # noqa: E731

        # Step 1 — open the project from the Projects table (real click).
        shot("step1_before.png")
        open_btn = page.locator('[data-action="open-project"]').first
        open_btn.wait_for(state="visible")
        step1_target = _rect(page, open_btn)
        open_btn.click()
        page.wait_for_url("**/#/overview**", timeout=15000)
        page.wait_for_selector(".ov-page-head", state="visible", timeout=15000)
        page.wait_for_timeout(900)
        shot("step2_before.png")

        # Step 2 — click the ORR key-fact card; it deep-links to the CSR page + quote.
        orr_card = page.locator(".ov-fact", has_text="客观缓解率").first
        orr_card.wait_for(state="visible")
        step2_target = _rect(page, orr_card)
        orr_card.click()
        page.wait_for_url("**/#/evidence**", timeout=15000)
        page.wait_for_selector(".ev-card.active, .ev-card", state="visible", timeout=15000)
        page.wait_for_timeout(900)
        shot("step3_before.png")

        # Step 3 — click the Findings & Questions navigation item (real navigation).
        findings_nav = page.locator('[data-nav="findings"]').first
        step3_target = _rect(page, findings_nav)
        findings_nav.click()
        page.wait_for_url("**/#/findings**", timeout=15000)
        page.wait_for_selector('[data-change="finding-filter"][data-field="severity"]', timeout=15000)
        page.wait_for_timeout(800)
        shot("step4_before.png")

        # Step 4 — filter severity to Critical (real select change -> filtered table).
        severity = page.locator('[data-change="finding-filter"][data-field="severity"]').first
        step4_target = _rect(page, severity)
        severity.select_option("critical")
        page.wait_for_timeout(700)
        shot("step5_before.png")

        # Step 5 — open Report via navigation (real navigation).
        report_nav = page.locator('[data-nav="report"]').first
        step5_target = _rect(page, report_nav)
        report_nav.click()
        page.wait_for_url("**/#/report**", timeout=15000)
        page.wait_for_selector(".report-body, #view", timeout=15000)
        page.wait_for_timeout(1000)
        shot("step6_before.png")

        steps = [
            {
                "step": 0,
                "before": "step1_before.png",
                "after": "step2_before.png",
                "target": step1_target,
                "action": "click_project",
                "caption_zh": "1/5 打开卖方资料包项目（真实点击 Open →）",
                "caption_en": "Open the seller package project — real click on Open →",
                "caption_after_zh": "结果：进入 Asset 360 — 13 文档 / 284 页 / 136 证据 / 11 findings",
                "caption_after_en": "Result: Asset 360 — 13 documents, 284 pages, 136 evidence rows, 11 findings",
            },
            {
                "step": 1,
                "before": "step2_before.png",
                "after": "step3_before.png",
                "target": step2_target,
                "action": "click_asset360_orr",
                "caption_zh": "2/5 点击 Asset 360 的 ORR 卡片（真实跳转 + 定位原文）",
                "caption_en": "Click the ORR card on Asset 360 — really opens the evidence page",
                "caption_after_zh": "结果：Evidence 自动定位 CSR 第 10 页，ORR 42.0% 原文高亮",
                "caption_after_en": "Result: Evidence opens CSR p.10 with the ORR 42.0% quote highlighted",
            },
            {
                "step": 2,
                "before": "step3_before.png",
                "after": "step4_before.png",
                "target": step3_target,
                "action": "click_nav_findings",
                "caption_zh": "3/5 点击 Findings & Questions（真实导航）",
                "caption_en": "Open Findings & Questions — real navigation",
                "caption_after_zh": "结果：11 条 active findings（1 critical / 3 high）",
                "caption_after_en": "Result: 11 active findings — 1 critical, 3 high",
            },
            {
                "step": 3,
                "before": "step4_before.png",
                "after": "step5_before.png",
                "target": step4_target,
                "action": "filter_critical",
                "caption_zh": "4/5 选择 Critical 筛选（真实过滤 11 → 1 条）",
                "caption_en": "Filter severity = Critical — table really filters from 11 to 1",
                "caption_after_zh": "结果：1 of 11 — 仅保留 critical ORR 矛盾",
                "caption_after_en": "Result: 1 of 11 — only the critical ORR contradiction remains",
            },
            {
                "step": 4,
                "before": "step5_before.png",
                "after": "step6_before.png",
                "target": step5_target,
                "action": "click_nav_report",
                "caption_zh": "5/5 点击 Report（真实打开交付报告）",
                "caption_en": "Open Report — real navigation to the deliverables",
                "caption_after_zh": "结果：Decision Brief + 报告下载（DOCX / MD / XLSX）",
                "caption_after_en": "Result: Decision Brief and report downloads (DOCX / MD / XLSX)",
            },
        ]
        page.close()
        browser.close()

    payload = {
        "base": base,
        "project_id": project_id,
        "viewport": VIEWPORT,
        "recorded_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "steps": steps,
    }
    (out / "timeline.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8123")
    parser.add_argument("--out", default="demo_recording")
    parser.add_argument("--project", default=None)
    args = parser.parse_args()
    payload = record(args.base, ROOT / args.out, args.project)
    print(f"recorded {len(payload['steps'])} real interactions → {ROOT / args.out}")
    print(json.dumps({"project_id": payload["project_id"],
                      "steps": [s["action"] for s in payload["steps"]]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
