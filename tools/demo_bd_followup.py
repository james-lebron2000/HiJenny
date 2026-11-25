"""Replay the ORR review in a fresh local demo-kit using the actual web forms."""
import argparse
import json
import tempfile
from pathlib import Path

import httpx
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright


def workbook(path, sheet, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet
    for row in rows:
        ws.append(row)
    for column in "ABCDE":
        ws.column_dimensions[column].width = 28
    wb.save(path)


def run(workspace, port, output, channel):
    info = json.loads((workspace / "demo_info.json").read_text())
    project = info["project_id"]
    origin = f"http://127.0.0.1:{port}"
    data = httpx.get(f"{origin}/api/projects/{project}/followup").raise_for_status().json()
    q = next(q for q in data["questions"] if "objective response rate" in q["question"].lower())
    if data["history"].get(q["question_id"]):
        raise SystemExit("This question already has a history. Replay against a fresh demo-kit workspace.")
    url = f"{origin}/#/followup?project={project}&question={q['question_id']}"
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as playwright:
        folder = Path(tmp)
        comp = folder / "Synthetic_Comparator.xlsx"
        reply = folder / "Synthetic_Seller_ORR_Response.xlsx"
        workbook(comp, "Comparator", [
            ["Synthetic training fixture only"], ["Asset", "Population", "Line", "Method", "Follow-up"],
            ["Example-B", "Response-evaluable, 40", "2L+", "Investigator RECIST 1.1", "6 months"],
            ["ORR", "18/40", "45.0%", "Single arm"]])
        workbook(reply, "ORR reconciliation", [
            ["Synthetic seller response for demonstration only"],
            ["Source", "Responders", "ITT denominator", "Reported ORR", "Data cutoff"],
            ["CSR p11", 53, 126, "42.0%", "2026-01-15"],
            ["Commercial forecast p3", None, None, "48.4%", "Analysis derivation not supplied"],
            ["Open request", "Signed statistical table and derivation of both percentages"]])
        browser = playwright.chromium.launch(channel=channel, headless=True)
        page = browser.new_page(viewport={"width": 1360, "height": 860}, device_scale_factor=1)
        page.goto(url)
        form = page.locator("#bd-form")
        form.wait_for()
        frames = []

        def capture(element, label):
            element.scroll_into_view_if_needed()
            picture = folder / f"{len(frames)}.png"
            page.screenshot(path=picture)
            frames.append((picture, label))

        def record(kind, actor, **values):
            form.locator('[name=kind]').select_option(kind)
            for name, value in values.items():
                control = form.locator(f'[name={name}]')
                if name == "attachment":
                    control.set_input_files(value)
                elif name == "decision":
                    control.select_option(value)
                else:
                    control.fill(value)
            form.locator('[name=actor]').fill(actor)
            with page.expect_response(lambda r: r.request.method == "POST" and "/followup/" in r.url) as response:
                form.get_by_role("button", name="保存记录").click()
            assert response.value.ok, response.value.text()
            page.wait_for_function("(n) => document.querySelectorAll('.bd ol li').length === n", arg=record.count+1)
            record.count += 1
        record.count = 0
        capture(page.locator('.bd blockquote'), "01  |  Inspect the claim: CSR p11 and the commercial forecast disagree")
        record("comparison", "BD · 合成演示", asset="Example-B（合成）", population="疗效可评价，n=40",
               line="二线及以后", method="研究者 RECIST 1.1", followup="6 个月", endpoint="ORR",
               value="45.0%（18/40）", limits="可评价集与 ITT 不同；治疗线次和随访尚未对齐，不能作优效排序。",
               attachment=comp, locator="Comparator · rows 2–4")
        capture(page.locator('.bd-scroll'), "02  |  Compare populations and assessment methods before comparing response rates")
        record("request", "BD · 合成演示", note="请说明 42.0% 与预测稿 48.4% 的分析口径，补交应答人数、分母、截止日及签核表。")
        record("reply", "卖方联络人 · 合成演示", attachment=reply, locator="ORR reconciliation · rows 2–5",
               note="模拟补件重列 CSR 的 53/126 和截止日；48.4% 的推导及签核统计表仍待提供。")
        capture(page.locator('.bd li').last, "03  |  Attach the seller's reconciliation and retain the outstanding questions")
        record("review", "统计复核人 · 合成演示", decision="documents",
               note="53/126 = 42.0635%，按一位小数为 42.1%。本疑点需核对统计表、版本和取整规则，无需湿实验；48.4% 暂无可核来源。")
        capture(page.locator('.bd li').last, "04  |  Reviewer requests a statistical reconciliation; no wet-lab experiment needed")
        record("publish", "BD 负责人 · 合成演示",
               note="保留 CSR 原报 ORR 42.0%，标注计数复算 42.1%；暂不采用 48.4% 作为估值输入。待签核统计表后重审，Example-B 仅作背景参照。")
        page.locator('#bd-form a').click()
        heading = page.get_by_text('BD 复核更新', exact=True).first
        heading.wait_for()
        capture(page.locator('li').filter(has_text='BD 结论 ·').first, "05  |  Publish the reviewed decision, source and remaining uncertainty to the brief")
        browser.close()
        images = []
        for picture, label in frames:
            canvas = Image.new("RGB", (1088, 734), "#102743")
            with Image.open(picture) as im:
                canvas.paste(im.convert("RGB").resize((1088,688), Image.Resampling.LANCZOS), (0,0))
            draw = ImageDraw.Draw(canvas)
            try:
                font = ImageFont.truetype("Arial.ttf", 18)
            except OSError:
                font = ImageFont.load_default()
            draw.text((20,700), label, font=font, fill="white")
            images.append(canvas)
        output.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(output, save_all=True, append_images=images[1:], duration=3500, loop=0)
    print(url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--port", type=int, default=8137)
    parser.add_argument("--gif", type=Path, default=Path("docs/assets/bd_followup.gif"))
    parser.add_argument("--channel", default="chrome")
    args = parser.parse_args()
    run(args.workspace, args.port, args.gif, args.channel)
