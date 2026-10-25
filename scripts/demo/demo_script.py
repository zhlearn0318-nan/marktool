#!/usr/bin/env python3
"""TraceMark 多模态、场景与合规检测演示。

先按仓库 README 启动 8000 / 8002 / 5173，然后：

    python scripts/demo/demo_script.py --base http://127.0.0.1:5173 --out /tmp/marktool-demo

脚本使用 Playwright 在 1280×800 录屏，并在页面顶部放中文字幕。结束后用 ffmpeg
转成 H.264 MP4。合规检测一段会上传正常的多模态 MP4、带重复标识的多模态 MP4、
内容互相冲突的多模态 MP4、虚拟场景包和交互场景包。重复标识自动合并；冲突标识
先对比候选，选中一份后再修复并复检。显式提示不合格的虚拟场景包先出左右对比预览，
确认后才修复。冲突样本写到 ``<out>/work/multimodal-conflict.mp4``，不合格场景包写到
``<out>/work/virtual-explicit-broken.zip``。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:5173")
ap.add_argument("--out", default="/tmp/marktool-demo")
args = ap.parse_args()
OUT = Path(args.out)
BASE = args.base.rstrip("/")
RAW = OUT / "raw"
WORK = OUT / "work"
ASSETS = OUT / "assets"
for folder in (RAW, WORK, ASSETS):
    folder.mkdir(parents=True, exist_ok=True)
shutil.rmtree(RAW, ignore_errors=True)
RAW.mkdir(parents=True)
T0 = time.time()
TIMELINE: list[tuple[float, str]] = []


def pause(seconds: float = 1.2) -> None:
    time.sleep(seconds)


CAPTION_JS = """(text) => {
  let el = document.getElementById('__demo_caption');
  if (!el) {
    el = document.createElement('div');
    el.id = '__demo_caption';
    document.body.appendChild(el);
  }
  el.style.cssText = 'position:fixed;left:50%;top:18px;transform:translateX(-50%);z-index:2147483647;'
    + 'background:rgba(16,32,51,.92);color:#fff;font:700 24px "Noto Sans CJK SC","WenQuanYi Micro Hei",sans-serif;'
    + 'padding:12px 28px;border-radius:12px;border:2px solid #d4a73a;box-shadow:0 8px 30px rgba(0,0,0,.35);'
    + 'pointer-events:none;white-space:nowrap;';
  el.textContent = text;
}"""


def caption(page, text: str, hold: float = 2.0) -> None:
    TIMELINE.append((round(time.time() - T0, 1), text))
    print(f"[{TIMELINE[-1][0]:6.1f}s] {text}", flush=True)
    page.evaluate(CAPTION_JS, text)
    pause(hold)


def scroll_to(page, locator) -> None:
    locator.first.evaluate("element => element.scrollIntoView({behavior:'smooth', block:'center'})")
    pause(0.8)


def open_view(page, view: str) -> None:
    page.evaluate("(view) => { window.location.hash = view }", view)
    pause(1.2)
    page.evaluate("() => window.scrollTo({top:0, behavior:'smooth'})")
    pause(0.4)


def ensure_assets() -> tuple[Path, Path]:
    video = ASSETS / "demo-ai-video.mp4"
    reply = ASSETS / "ai-reply.txt"
    if not video.exists():
        subtitle = ASSETS / "sub.srt"
        subtitle.write_text(
            "1\n00:00:00,000 --> 00:00:05,000\n这是一段AI生成的演示视频\n",
            encoding="utf-8",
        )
        subprocess.check_call([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25:duration=5",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
            "-i", str(subtitle),
            "-map", "0", "-map", "1", "-map", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "mov_text",
            "-metadata:s:s:0", "language=chi", "-shortest", str(video),
        ])
    reply.write_text("这是一段由人工智能生成的回复，用于交互场景内容文件。\n", encoding="utf-8")
    return video, reply


def choose(page, locator, path: Path) -> None:
    with page.expect_file_chooser() as chooser:
        locator.click()
    chooser.value.set_files(str(path))
    pause(1.0)


def download_bundle(page, url: str, name: str) -> Path:
    target = WORK / name
    target.write_bytes(page.request.get(BASE + url).body())
    return target


def member(package: Path, suffix: str) -> Path:
    with zipfile.ZipFile(package) as archive:
        name = next(item for item in archive.namelist() if item.endswith(suffix))
        target = WORK / Path(name).name
        target.write_bytes(archive.read(name))
    return target


def _xmp_aigc(path: Path) -> str:
    return subprocess.check_output([
        "exiftool", "-config",
        str(Path(__file__).resolve().parents[2] / "labeling-backend/config/exiftool_aigc.config"),
        "-s3", "-XMP-aigc:AIGC", str(path),
    ], text=True).strip()


def duplicate_comment(source: Path, dest: Path) -> None:
    shutil.copyfile(source, dest)
    subprocess.check_call([
        "exiftool", "-overwrite_original", "-QuickTime:Comment=" + _xmp_aigc(dest), str(dest),
    ], stdout=subprocess.DEVNULL)


def conflict_comment(source: Path, dest: Path) -> None:
    """在容器注释里再写一份 ProduceID 不同的标识，形成需要用户选择的冲突。"""
    shutil.copyfile(source, dest)
    payload = json.loads(_xmp_aigc(dest))
    inner = payload["AIGC"]
    inner["ProduceID"] = "PID-CONFLICT-DROP"
    inner["PropagateID"] = "PID-CONFLICT-DROP"
    raw = json.dumps({"AIGC": inner}, ensure_ascii=True, separators=(",", ":"))
    subprocess.check_call([
        "exiftool", "-overwrite_original", "-QuickTime:Comment=" + raw, str(dest),
    ], stdout=subprocess.DEVNULL)


def degrade_virtual(source: Path, dest: Path) -> None:
    """把已合规的虚拟场景包改成显式文字和位置都不合格，像素里的原提示保留。"""
    with zipfile.ZipFile(source) as archive:
        members = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
    descriptor = json.loads(members["scene.json"])
    descriptor["explicit"]["text"] = "未按标准提示"
    descriptor["explicit"]["position"] = "center"
    members["scene.json"] = json.dumps(descriptor, ensure_ascii=False, indent=2).encode("utf-8")
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def main() -> None:
    video, reply = ensure_assets()
    results: dict[str, object] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            record_video_dir=str(RAW),
            record_video_size={"width": 1280, "height": 800},
            accept_downloads=True,
        )
        page = context.new_page()
        page.goto(BASE + "/#overview", wait_until="networkidle")
        caption(page, "TraceMark 源迹 · 多模态、场景与合规检测", 2.2)

        open_view(page, "multimodal")
        caption(page, "多模态：拆分画面、音频和字幕，分别写入隐式标识", 2.0)
        choose(page, page.get_by_text("点击或拖拽含音频、字幕的 MP4"), video)
        with page.expect_response(lambda item: "/api/v1/multimodal/label" in item.url, timeout=180000) as caught:
            page.get_by_role("button", name="拆分、打标并校验").click()
        multimodal = caught.value.json()
        page.get_by_text("多模态标识通过校验").wait_for(timeout=30000)
        bundle = download_bundle(page, multimodal["download_url"], "multimodal-bundle.zip")
        good_mp4 = member(bundle, "composite.mp4")
        bad_mp4 = WORK / "multimodal-duplicate.mp4"
        duplicate_comment(good_mp4, bad_mp4)
        caption(page, "打标完成。复核改到合规检测，这里只保留跳转", 2.0)
        scroll_to(page, page.get_by_role("button", name="打开合规检测"))

        open_view(page, "scenes")
        caption(page, "虚拟场景：起始画面显式提示，并写入隐式标识", 2.0)
        page.get_by_role("tab", name="虚拟场景").click()
        pause(0.6)
        with page.expect_response(lambda item: "/scenes/virtual/label" in item.url, timeout=180000) as response:
            page.get_by_role("button", name="写入显式标识与隐式标识").click()
        virtual = response.value.json()
        page.get_by_text("虚拟场景通过校验").wait_for(timeout=30000)
        virtual_zip = download_bundle(page, virtual["download_url"], "virtual-scene.zip")
        caption(page, "交互场景：界面提示持续显示，内容文件写入隐式标识", 2.0)
        page.get_by_role("tab", name="交互场景").click()
        pause(0.6)
        choose(page, page.get_by_role("button", name="可选：附带文本 / 图片 / 音频 / 视频"), reply)
        with page.expect_response(lambda item: "/scenes/interactive/label" in item.url, timeout=180000) as response:
            page.get_by_role("button", name="生成界面标识并校验").click()
        interactive = response.value.json()
        page.get_by_text("交互场景通过校验").wait_for(timeout=30000)
        interactive_zip = download_bundle(page, interactive["download_url"], "interactive-scene.zip")

        caption(page, "合规检测会自动识别多模态文件和场景包", 1.5)

        def inspect(path: Path, title: str) -> None:
            open_view(page, "mediaInspect")
            caption(page, title, 1.6)
            choose(page, page.get_by_text("点击或拖拽文件到此处"), path)
            with page.expect_response(lambda item: "/api/v1/compliance-inspect" in item.url, timeout=180000):
                page.get_by_role("button", name="开始合规检测").click()
            page.get_by_text("检测结论与明细").wait_for()
            pause(2.2)

        inspect(good_mp4, "合规检测：正常的多模态 MP4")
        results["good_multimodal"] = "shown"
        inspect(bad_mp4, "合规检测：重复标识的多模态 MP4")
        page.get_by_role("button", name="生成修复方案").click()
        pause(1.5)
        caption(page, "方案与单模态一致：相同的重复标识只保留一份", 1.8)
        with page.expect_response(lambda item: "/composite-repair-jobs" in item.url, timeout=180000):
            page.get_by_role("button", name="确认执行并复检").click()
        page.get_by_text("合规", exact=False).first.wait_for(timeout=30000)
        caption(page, "修复后的文件已下载，复检结论为合规", 2.2)
        results["repaired"] = True

        conflict_mp4 = WORK / "multimodal-conflict.mp4"
        conflict_comment(good_mp4, conflict_mp4)
        inspect(conflict_mp4, "合规检测：同一文件里两份标识内容互相冲突")
        scroll_to(page, page.get_by_text("冲突的多份标识"))
        caption(page, "差异字段已高亮。选择要保留的那一份", 1.8)
        page.get_by_role("radio").first.click()
        pause(0.8)
        scroll_to(page, page.get_by_role("button", name="按所选标识修复"))
        caption(page, "选定后按所选标识修复，其余冲突份会被覆盖", 1.6)
        with page.expect_response(lambda item: "/composite-repair-jobs" in item.url, timeout=180000):
            page.get_by_role("button", name="按所选标识修复").click()
        page.locator(".ant-alert-success").wait_for(timeout=60000)
        caption(page, "已按所选标识修复，复检结论为合规", 2.2)
        results["conflict_sample"] = str(conflict_mp4)

        inspect(virtual_zip, "合规检测：虚拟场景包，同时检查显式提示")
        broken_virtual = WORK / "virtual-explicit-broken.zip"
        degrade_virtual(virtual_zip, broken_virtual)
        inspect(broken_virtual, "合规检测：虚拟场景显式提示不合格")
        scroll_to(page, page.get_by_role("button", name="生成修复预览"))
        with page.expect_response(lambda item: "/scene-explicit-previews" in item.url and item.request.method == "POST", timeout=180000):
            page.get_by_role("button", name="生成修复预览").click()
        page.get_by_text("修复后").wait_for(timeout=30000)
        scroll_to(page, page.get_by_role("img", name="修复后的预览"))
        caption(page, "左右对比预览。原提示保留，新提示叠在更不遮挡的位置", 2.0)
        scroll_to(page, page.get_by_role("button", name="确认并修复"))
        with page.expect_response(lambda item: "/composite-repair-jobs" in item.url, timeout=180000):
            page.get_by_role("button", name="确认并修复").click()
        page.locator(".ant-alert-success").wait_for(timeout=60000)
        caption(page, "确认预览后已写回，复检结论为合规", 2.2)
        results["explicit_preview_sample"] = str(broken_virtual)
        inspect(interactive_zip, "合规检测：交互场景包")
        caption(page, "场景包写入结果为合规；有问题的多模态文件已修复", 2.4)

        open_view(page, "overview")
        caption(page, "演示结束", 1.5)
        context.close()
        browser.close()

    webm = next(RAW.glob("*.webm"))
    mp4 = OUT / "marktool-demo.mp4"
    subprocess.check_call([
        "ffmpeg", "-y", "-i", str(webm),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-crf", "30", "-an", str(mp4),
    ])
    (OUT / "timeline.json").write_text(
        json.dumps({"timeline": TIMELINE, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(mp4, mp4.stat().st_size)


if __name__ == "__main__":
    main()
