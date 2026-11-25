#!/usr/bin/env python3
"""Animate a *real* Playwright-recorded HiJenny demo into GIF / MP4.

The recording (``demo_recording/timeline.json``) is produced by
``tools/record_demo_walkthrough.py``: every cursor target is the bounding box
of a real DOM element and every before/after screenshot pair is separated by a
real browser click/navigation/filter. This script only adds the presentation
layer: brand bars, captions, cursor, click ripple, result highlights and
crossfades between the real before/after states.

Usage:
    python3 tools/record_demo_walkthrough.py --base http://127.0.0.1:8123
    python3 tools/make_demo_gif.py --recording-dir demo_recording
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
W, H = 1000, 680
TOP_H = 44
SHOT_W, SHOT_H = 920, 575
SHOT_X, SHOT_Y = 40, 48
CAP_Y = 632
FPS = 10

NAVY = (15, 39, 67)
BLUE = (46, 117, 182)
TEAL = (30, 125, 79)
WHITE = (255, 255, 255)
MUTED = (138, 151, 168)
LINE = (219, 226, 236)

CJK = "/System/Library/Fonts/PingFang.ttc"
CJK_ALT = "/System/Library/Fonts/STHeiti Medium.ttc"
EN = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"

STEPS = ["打开项目", "Asset 360 → 原文", "发现与问题", "筛选 Critical", "报告交付"]
STEPS_EN = ["Open project", "Asset 360 → source", "Findings", "Filter Critical", "Report"]

# Highlight boxes on the *after* screenshot, normalized to the real viewport.
AFTER_HIGHLIGHTS: dict[int, list[tuple[float, float, float, float]]] = {
    0: [(0.60, 0.055, 0.995, 0.36)],
    1: [(0.33, 0.35, 0.71, 0.465), (0.78, 0.575, 0.995, 0.735)],
    2: [(0.05, 0.42, 0.96, 0.55)],
    3: [(0.05, 0.40, 0.96, 0.56), (0.775, 0.355, 0.995, 0.425)],
    4: [(0.60, 0.065, 0.995, 0.17)],
}


def font(size: int, cjk: bool = True) -> ImageFont.FreeTypeFont:
    size = int(round(size))
    for path in ((CJK if cjk else EN), CJK_ALT, EN):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def ease_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1.0 - (1.0 - t) ** 3


def rounded(img: Image.Image, radius: int = 12) -> Image.Image:
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius=radius, fill=255)
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out


def canvas_coords(target: dict, viewport: dict) -> tuple[float, float]:
    cx = SHOT_X + (target["x"] + target["w"] / 2) / viewport["width"] * SHOT_W
    cy = SHOT_Y + (target["y"] + target["h"] / 2) / viewport["height"] * SHOT_H
    return cx, cy


def draw_cursor(draw: ImageDraw.ImageDraw, x: float, y: float, scale: float = 1.15, pressed: bool = False) -> None:
    pts = [(0, 0), (0, 22), (6, 17), (11, 28), (15, 26), (10, 16), (18, 16)]
    pts = [(x + px * scale, y + py * scale) for px, py in pts]
    shadow = [(px + 2, py + 2) for px, py in pts]
    draw.polygon(shadow, fill=(0, 0, 0, 70))
    draw.polygon(pts, fill=(255, 255, 255, 255), outline=NAVY + (255,))
    if pressed:
        draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=BLUE + (255,), width=2)


def click_ripple(base: Image.Image, x: float, y: float, progress: float) -> None:
    if progress <= 0 or progress >= 1:
        return
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    radius = 6 + 34 * ease_out(progress)
    alpha = int(190 * (1 - progress))
    d.ellipse([x - radius, y - radius, x + radius, y + radius], outline=BLUE + (alpha,), width=2)
    base.alpha_composite(overlay)


def highlight(base: Image.Image, box: tuple[float, float, float, float], progress: float, color=TEAL) -> None:
    if progress <= 0:
        return
    x0, y0, x1, y1 = box
    pulse = 0.55 + 0.45 * (0.5 + 0.5 * math.sin(progress * math.pi * 2))
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle([x0, y0, x1, y1], radius=8,
                        fill=color + (int(20 * pulse),), outline=color + (int(235 * pulse),), width=2)
    base.alpha_composite(overlay)


def brand_bar(frame: Image.Image, step: int | None) -> None:
    d = ImageDraw.Draw(frame)
    d.rectangle([0, 0, W, TOP_H], fill=WHITE)
    d.line([0, TOP_H, W, TOP_H], fill=LINE, width=1)
    d.rounded_rectangle([16, 9, 44, 35], radius=7, fill=NAVY)
    d.text((30, 22), "H", font=font(14), fill=WHITE, anchor="mm")
    d.text((54, 21), "HiJenny", font=font(17), fill=NAVY, anchor="lm")
    d.text((54, 34), "evidence-first due diligence", font=font(8, cjk=False), fill=MUTED, anchor="lm")
    if step is not None:
        d.text((W - 22, 14), f"STEP {step + 1}/5 · {STEPS[step]}", font=font(11), fill=NAVY, anchor="rm")
        d.text((W - 22, 27), STEPS_EN[step], font=font(8, cjk=False), fill=MUTED, anchor="rm")
        d.text((W - 22, 38), "REAL BROWSER INTERACTION", font=font(7, cjk=False), fill=TEAL, anchor="rm")


def caption_bar(frame: Image.Image, step: int, zh: str, en: str) -> None:
    d = ImageDraw.Draw(frame)
    d.rectangle([0, CAP_Y, W, H], fill=NAVY)
    d.text((22, CAP_Y + 9), f"{step + 1}/5", font=font(12, cjk=False), fill=(127, 168, 232), anchor="lt")
    d.text((58, CAP_Y + 7), zh, font=font(14), fill=WHITE, anchor="lt")
    d.text((58, CAP_Y + 27), en, font=font(9, cjk=False), fill=(185, 203, 224), anchor="lt")
    for i in range(5):
        cx = W - 24 - (4 - i) * 18
        if i == step:
            d.ellipse([cx - 4, CAP_Y + 22, cx + 4, CAP_Y + 30], fill=WHITE)
        else:
            d.ellipse([cx - 3, CAP_Y + 23, cx + 3, CAP_Y + 29], fill=(74, 104, 143))


def screen_layer(shot_path: Path) -> Image.Image:
    frame = Image.new("RGBA", (W, H), (243, 245, 249, 255))
    img = Image.open(shot_path).convert("RGB").resize((SHOT_W, SHOT_H), Image.LANCZOS).convert("RGBA")
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.rounded_rectangle([SHOT_X + 3, SHOT_Y + 5, SHOT_X + SHOT_W + 3, SHOT_Y + SHOT_H + 5],
                         radius=13, fill=(15, 39, 67, 50))
    frame.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(5)))
    frame.alpha_composite(rounded(img), (SHOT_X, SHOT_Y))
    d = ImageDraw.Draw(frame)
    d.rounded_rectangle([SHOT_X, SHOT_Y, SHOT_X + SHOT_W, SHOT_Y + SHOT_H], radius=12,
                        outline=(210, 219, 231, 255), width=1)
    return frame


def compose(screen: Image.Image, step: int, zh: str, en: str) -> Image.Image:
    frame = screen.copy()
    brand_bar(frame, step)
    caption_bar(frame, step, zh, en)
    return frame


def intro_frames() -> list[Image.Image]:
    frames = []
    n = 12
    for i in range(n):
        t = i / (n - 1)
        bg = Image.new("RGBA", (W, H), NAVY + (255,))
        d = ImageDraw.Draw(bg)
        for y in range(H):
            k = y / H
            d.line([0, y, W, y], fill=(int(15 + 10 * k), int(39 + 18 * k), int(67 + 28 * k), 255))
        d.rectangle([0, 0, 6, H], fill=BLUE)
        d.text((64, 200), "HIJENNY · REAL INTERACTION DEMO", font=font(11, cjk=False), fill=(127, 168, 232), anchor="lt")
        d.text((62, 226), "HiJenny", font=font(62, cjk=False), fill=WHITE, anchor="lt")
        d.text((64, 304), "医药资产交易智能尽调系统", font=font(24), fill=WHITE, anchor="lt")
        d.text((64, 346), "5 步真实浏览器交互：每次点击都触发真实页面变化", font=font(12), fill=(185, 203, 224), anchor="lt")
        labels = ["1 打开项目", "2 Asset 360", "3 原文定位", "4 发现与问题", "5 报告交付"]
        x = 64
        for idx, label in enumerate(labels):
            width = 132 if idx != 1 else 148
            d.rounded_rectangle([x, 420, x + width, 456], radius=18, outline=(74, 104, 143), width=1)
            d.text((x + width / 2, 438), label, font=font(11), fill=(216, 228, 242), anchor="mm")
            x += width + 10
        d.text((64, 520), "DEMO WALKTHROUGH · real clicks · synthetic Phase 2 package", font=font(10, cjk=False),
               fill=(122, 141, 165), anchor="lt")
        d.rectangle([64, 558, 64 + int((W - 128) * (0.15 + 0.5 * t)), 560], fill=BLUE)
        frames.append(bg)
    return frames


def outro_frames() -> list[Image.Image]:
    frames = []
    n = 12
    for i in range(n):
        t = i / (n - 1)
        bg = Image.new("RGBA", (W, H), NAVY + (255,))
        d = ImageDraw.Draw(bg)
        for y in range(H):
            k = y / H
            d.line([0, y, W, y], fill=(int(15 + 10 * k), int(39 + 18 * k), int(67 + 28 * k), 255))
        d.rectangle([0, 0, 6, H], fill=TEAL)
        d.text((64, 190), "EVERY CLAIM TRACEABLE.", font=font(30, cjk=False), fill=WHITE, anchor="lt")
        d.text((64, 238), "每条结论可点回原文 · 每项风险可解释 · 每个问题可行动", font=font(17), fill=(216, 228, 242), anchor="lt")
        d.text((64, 330), "136 条证据全部通过原文校验 · 11 条 active findings", font=font(13), fill=(185, 203, 224), anchor="lt")
        d.text((64, 356), "6 条卖方问题 · 65/100 尽调就绪度 · 8-Sheet Excel", font=font(13), fill=(185, 203, 224), anchor="lt")
        d.text((64, 470), "github.com/james-lebron2000/HiJenny", font=font(13, cjk=False), fill=WHITE, anchor="lt")
        d.text((64, 500), "演示：./scripts/demo.sh  ·  动图：python3 tools/make_demo_gif.py", font=font(11), fill=(141, 164, 192), anchor="lt")
        d.rectangle([64, 552, 64 + int((W - 128) * (0.3 + 0.6 * t)), 554], fill=TEAL)
        frames.append(bg)
    return frames


def build_frames(recording: Path, timeline: dict) -> list[Image.Image]:
    viewport = timeline.get("viewport") or {"width": 1600, "height": 1000}
    steps = timeline["steps"]
    frames = intro_frames()
    cursor = (SHOT_X + SHOT_W * 0.86, SHOT_Y + SHOT_H * 0.90)
    for idx, step in enumerate(steps):
        before = screen_layer(recording / step["before"])
        after = screen_layer(recording / step["after"])
        target = canvas_coords(step["target"], viewport)
        zh, en = step["caption_zh"], step["caption_en"]
        zh_after, en_after = step.get("caption_after_zh", zh), step.get("caption_after_en", en)

        move_n = 5
        for i in range(1, move_n + 1):
            t = ease_out(i / move_n)
            frame = before.copy()
            cx = cursor[0] + (target[0] - cursor[0]) * t
            cy = cursor[1] + (target[1] - cursor[1]) * t
            overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            draw_cursor(ImageDraw.Draw(overlay), cx, cy)
            frame.alpha_composite(overlay)
            frames.append(compose(frame, idx, zh, en))
        for i in range(2):
            frame = before.copy()
            click_ripple(frame, target[0], target[1], (i + 1) / 3)
            overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            draw_cursor(ImageDraw.Draw(overlay), target[0], target[1], pressed=True)
            frame.alpha_composite(overlay)
            frames.append(compose(frame, idx, zh, en))
        for i in range(3):
            alpha = (i + 1) / 4
            blended = Image.blend(before, after, alpha)
            frames.append(compose(blended, idx, zh_after, en_after))
        boxes = AFTER_HIGHLIGHTS.get(idx, [])
        for i in range(7):
            frame = after.copy()
            progress = 0.5 + 0.5 * i / 4
            for box in boxes:
                px_box = (
                    SHOT_X + box[0] * SHOT_W, SHOT_Y + box[1] * SHOT_H,
                    SHOT_X + box[2] * SHOT_W, SHOT_Y + box[3] * SHOT_H,
                )
                highlight(frame, px_box, progress)
            overlay = Image.new("RGBA", frame.size, (0, 0, 0, 0))
            draw_cursor(ImageDraw.Draw(overlay), target[0], target[1])
            frame.alpha_composite(overlay)
            frames.append(compose(frame, idx, zh_after, en_after))
        cursor = target
    frames += outro_frames()
    return frames


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recording-dir", default="demo_recording")
    parser.add_argument("--out-dir", default="docs/assets")
    args = parser.parse_args()

    recording = ROOT / args.recording_dir
    timeline_path = recording / "timeline.json"
    if not timeline_path.is_file():
        raise SystemExit(
            f"missing {timeline_path}; run tools/record_demo_walkthrough.py while the demo server is running"
        )
    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg is required")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    frames = build_frames(recording, timeline)

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = Path(tempfile.mkdtemp(prefix="hijenny_gif_"))
    for idx, frame in enumerate(frames):
        frame.convert("RGB").save(frames_dir / f"frame_{idx:04d}.png", optimize=True)

    poster_index = 12 + 5 + 2 + 3
    frames[poster_index].convert("RGB").save(out_dir / "demo_walkthrough.png", optimize=True)

    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-vf", "split[s0][s1];[s0]palettegen=max_colors=128:stats_mode=diff[p];"
               "[s1][p]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle",
        "-loop", "0", str(out_dir / "demo_walkthrough.gif"),
    ], check=True)
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS),
        "-i", str(frames_dir / "frame_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart",
        str(out_dir / "demo_walkthrough.mp4"),
    ], check=True)
    shutil.rmtree(frames_dir, ignore_errors=True)

    gif = out_dir / "demo_walkthrough.gif"
    mp4 = out_dir / "demo_walkthrough.mp4"
    print(f"frames={len(frames)} duration={len(frames)/FPS:.1f}s "
          f"gif={gif.stat().st_size/1e6:.2f}MB mp4={mp4.stat().st_size/1e6:.2f}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
