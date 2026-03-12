#!/usr/bin/env python3
"""
Podcast RSS 抓取 + NotebookLM 生成（v2：使用 notebooklm-py Python API）

正确流程：
1. 从 Apple Podcasts API 获取 RSS 地址
2. 解析 RSS 获取最新单集（修复 apple_id 写死 Bug）
3. 检查/创建频道 notebook（名称=频道名）
4. 添加播客 URL 为 source
5. 生成中文报告（内置 wait_for_completion 轮询）
6. 下载报告并发送邮件通知

改动说明：
- 完全使用 notebooklm-py Python async API，移除所有 subprocess CLI 调用
- 修复原 fetch.py 中 apple_id 写死为 1559695855 的 Bug
- 支持 asyncio.gather() 并发处理多个播客频道
"""

import argparse
import asyncio
import json
import os
import re
import smtplib
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from notebooklm import NotebookLMClient

# ─── 常量 ──────────────────────────────────────────────
CST = timezone(timedelta(hours=8))

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR.parent / "config.json"
CONFIG = json.loads(CONFIG_FILE.read_text())

OUTPUT_DIR = Path(CONFIG.get("output_dir", "/root/.openclaw/workspace/notebooklm-library/podcast"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PENDING_FILE = OUTPUT_DIR / "pending.json"
PROCESSED_FILE = OUTPUT_DIR / "processed.json"
TODAY = datetime.now(CST).strftime("%Y-%m-%d")


# ─── RSS 工具函数（纯同步，不涉及 NotebookLM）────────────


def get_feed_url_from_apple(apple_id: str) -> str | None:
    """通过 Apple Podcasts ID 获取 RSS feed URL"""
    try:
        r = requests.get(
            f"https://itunes.apple.com/lookup?id={apple_id}",
            timeout=10
        )
        data = r.json()
        if data.get("results"):
            return data["results"][0].get("feedUrl")
    except Exception as e:
        print(f"  ⚠️ Apple API 错误: {e}")
    return None


def fetch_latest_episode(rss_url: str, apple_id: str) -> dict | None:
    """
    获取最新单集信息。

    修复：原 fetch.py 将 apple_id 写死为 1559695855（知行小酒馆）。
    现在通过参数传入每个频道自己的 apple_id，构建正确的 Apple Podcast URL。
    """
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            xml_data = resp.read()

        root = ET.fromstring(xml_data)
        item = root.find(".//item")
        if item is None:
            return None

        title = item.findtext("title", "").strip()
        guid = item.findtext("guid", "")

        # 从 guid 中提取数字 ID（Apple 单集 ID）
        match = re.search(r"(\d+)", guid)
        episode_id = match.group(1) if match else None

        if title and episode_id:
            # ✅ 使用传入的 apple_id，不再写死
            apple_url = f"https://podcasts.apple.com/podcast/id{apple_id}?i={episode_id}"
            return {"title": title, "source_url": apple_url}

    except Exception as e:
        print(f"  ⚠️ RSS 解析错误: {e}")
    return None


# ─── 持久化工具 ─────────────────────────────────────────


def load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


# ─── 邮件通知 ───────────────────────────────────────────


def send_success_notification(channel: str, episode_title: str, report_file: str) -> None:
    """发送成功通知邮件"""
    email_cfg = CONFIG.get("email", {})
    if not email_cfg.get("enabled"):
        return

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎙️ 播客报告 - {channel}"
        msg["From"] = email_cfg.get("smtp_user")
        msg["To"] = email_cfg.get("to")

        content = (
            f"# 🎙️ 播客报告\n\n"
            f"## 频道: {channel}\n"
            f"## 单集: {episode_title}\n"
            f"## 状态: ✅ 生成成功\n\n"
            f"报告: {report_file}\n"
        )
        msg.attach(MIMEText(content, "plain", "utf-8"))

        server = smtplib.SMTP(email_cfg.get("smtp_host"), email_cfg.get("smtp_port"))
        server.starttls()
        server.login(email_cfg.get("smtp_user"), email_cfg.get("smtp_pass"))
        server.send_message(msg)
        server.quit()

        print(f"  ✅ 邮件已发送")
    except Exception as e:
        print(f"  ❌ 邮件失败: {e}")


# ─── 核心异步逻辑 ────────────────────────────────────────

# 定义支持的 artifact 类型及其生成/下载方法
# mind_map 的 generate_mind_map 返回的是 dict 数据，不需要 wait_for_completion
SUPPORTED_ARTIFACTS = {
    "report": {
        "generate": lambda c, n: c.artifacts.generate_report(n, language="zh_Hans"),
        "download": lambda c, n, p: c.artifacts.download_report(n, f"{p}.md")
    },
    "audio": {
        "generate": lambda c, n: c.artifacts.generate_audio(n, language="zh_Hans"),
        "download": lambda c, n, p: c.artifacts.download_audio(n, f"{p}.mp3")
    },
    "video": {
        "generate": lambda c, n: c.artifacts.generate_video(n, language="zh_Hans"),
        "download": lambda c, n, p: c.artifacts.download_video(n, f"{p}.mp4")
    },
    "quiz": {
        "generate": lambda c, n: c.artifacts.generate_quiz(n, language="zh_Hans"),
        "download": lambda c, n, p: c.artifacts.download_quiz(n, f"{p}.md", output_format="markdown")
    },
    "slide_deck": {
        "generate": lambda c, n: c.artifacts.generate_slide_deck(n, language="zh_Hans"),
        "download": lambda c, n, p: c.artifacts.download_slide_deck(n, f"{p}.pdf")
    },
    "mind_map": {
        # mind_map 同步返回 dict 数据，下载为 JSON
        "generate": lambda c, n: c.artifacts.generate_mind_map(n),
        "download": lambda c, n, p: c.artifacts.download_mind_map(n, f"{p}.json")
    }
}


async def get_or_create_notebook(client: NotebookLMClient, channel_name: str) -> str | None:
    """获取或创建频道 notebook（名称=频道名）"""
    try:
        notebooks = await client.notebooks.list()
        for nb in notebooks:
            if channel_name in (nb.title or ""):
                print(f"  ✅ 找到已有 notebook: {nb.id}")
                return nb.id

        print(f"  📝 创建新 notebook: {channel_name}")
        nb = await client.notebooks.create(channel_name)
        print(f"  ✅ 创建成功: {nb.id}")

        # 设置语言
        try:
            await client.settings.set_language(nb.id, "zh_Hans")
        except Exception:
            pass  # 语言设置失败不阻断主流程

        return nb.id

    except Exception as e:
        print(f"  ❌ notebook 操作失败: {e}")
    return None


async def generate_and_download_artifact(client: NotebookLMClient, notebook_id: str,
                                         artifact_type: str, output_base_path: Path) -> Path | None:
    """生成并下载单一指定的 Artifact"""
    if artifact_type not in SUPPORTED_ARTIFACTS:
        print(f"  ⚠️ 不支持的类型: {artifact_type}")
        return None

    handler = SUPPORTED_ARTIFACTS[artifact_type]
    print(f"  📄 生成 [{artifact_type}]...")

    try:
        # 1. 触发生成
        result = await handler["generate"](client, notebook_id)

        # 2. 等待完成（mind_map 是直接返回字典的特例，没有 task_id）
        if artifact_type != "mind_map":
            status = result
            print(f"  ⏳ 等待 [{artifact_type}] 完成 (task_id={status.task_id})...")
            final = await client.artifacts.wait_for_completion(
                notebook_id,
                status.task_id,
                timeout=300,
                initial_interval=10
            )
            if not final.is_complete:
                print(f"  ❌ [{artifact_type}] 生成失败或超时: {final.status}")
                return None

        # 3. 下载
        download_path = await handler["download"](client, notebook_id, output_base_path)
        print(f"  ✅ [{artifact_type}] 已保存: {Path(download_path).name}")
        return Path(download_path)

    except Exception as e:
        print(f"  ❌ [{artifact_type}] 失败: {e}")
        return None


async def process_single_url(client: NotebookLMClient, url: str, name: str, artifact_types: list[str]) -> None:
    """处理单一 URL 模式"""
    print(f"\n📡 单次抓取: {name}")
    print(f"  🔗 URL: {url}")
    print(f"  📦 目标: {', '.join(artifact_types)}")

    # 1. 获取/创建 notebook
    notebook_id = await get_or_create_notebook(client, name)
    if not notebook_id:
        print("  ❌ 无法获取 notebook")
        return

    # 2. 添加源
    try:
        print(f"  📥 添加源: {url}")
        source = await client.sources.add_url(notebook_id, url)
        print(f"  ✅ 源已添加: {source.id}")
    except Exception as e:
        print(f"  ❌ 添加源失败: {e}")
        return

    # 3. 生成并下载 Artifacts
    output_subdir = OUTPUT_DIR / name.replace(" ", "_")
    output_subdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    output_base = output_subdir / f"{timestamp}_Capture"

    results = []
    for atype in artifact_types:
        res = await generate_and_download_artifact(client, notebook_id, atype, output_base)
        if res:
            results.append(str(res))

    print(f"  ✅ 单次抓取完成，共生成 {len(results)} 个文件。")


async def process_podcast(client: NotebookLMClient, podcast: dict) -> None:
    """处理单个播客频道的完整流程（RSS 模式）"""
    name = podcast.get("name", "Unknown")
    apple_id = podcast.get("apple_id")

    print(f"\n📡 处理: {name}")

    if not apple_id:
        print(f"  ⚠️ 未配置 apple_id，跳过")
        return

    # 1. 获取 RSS Feed URL
    rss_url = get_feed_url_from_apple(apple_id)
    if not rss_url:
        print(f"  ⚠️ 无法获取 RSS URL")
        return

    # 2. 获取最新单集（传入正确的 apple_id）
    episode = fetch_latest_episode(rss_url, apple_id)
    if not episode:
        print(f"  ⚠️ 无单集或解析失败")
        return

    print(f"  📰 最新: {episode['title']}")
    print(f"  🔗 URL: {episode['source_url']}")

    # 3. 检查是否已处理
    processed = load_json(PROCESSED_FILE)
    key = f"{name}:{episode['title'][:50]}"
    if key in processed:
        print(f"  ⏭️ 已处理，跳过")
        return

    # 4. 获取/创建 notebook
    notebook_id = await get_or_create_notebook(client, name)
    if not notebook_id:
        print(f"  ❌ 无法获取 notebook")
        return

    # 5. 添加播客源
    try:
        print(f"  📥 添加源: {episode['source_url']}")
        source = await client.sources.add_url(notebook_id, episode["source_url"])
        print(f"  ✅ 源已添加: {source.id}")
    except Exception as e:
        print(f"  ❌ 添加源失败: {e}")
        return

    # 6. 生成并下载 Artifacts
    output_subdir = OUTPUT_DIR / name.replace(" ", "_")
    output_subdir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', episode['title'])[:40]
    output_base_path = output_subdir / f"{timestamp}_{safe_title}"

    # 支持频道级配置覆盖全局配置
    artifact_types = podcast.get("artifacts", CONFIG.get("artifacts", ["report"]))
    downloaded_files = []

    for atype in artifact_types:
        res = await generate_and_download_artifact(client, notebook_id, atype, output_base_path)
        if res:
            downloaded_files.append(str(res))

    if not downloaded_files:
        print(f"  ❌ 未成功生成任何内容")
        return

    # 7. 发送通知 & 记录已处理
    # 通知里只拿第一个文件作为代表展示
    send_success_notification(name, episode["title"], downloaded_files[0])

    processed[key] = {
        "done": True,
        "reports": downloaded_files,
        "processed_at": datetime.now(CST).isoformat()
    }
    save_json(PROCESSED_FILE, processed)
    print(f"  ✅ 完成")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Podcast / Webpage to NotebookLM")
    parser.add_argument("--url", type=str, help="单链接抓取模式：独立的网址")
    parser.add_argument("--name", type=str, default="Quick_Capture", help="单链接模式下的 Notebook 频道名")
    parser.add_argument("--artifacts", type=str, help="单链接模式下要生成的类型，逗号分隔，如: report,mind_map")
    
    args = parser.parse_args()

    print(f"🚀 Podcast/URL 抓取（v2）- {TODAY}")

    async with await NotebookLMClient.from_storage() as client:
        # 单 URL 模式
        if args.url:
            atypes = [a.strip() for a in args.artifacts.split(",")] if args.artifacts else ["report"]
            await process_single_url(client, args.url, args.name, atypes)
            
        # 批量 RSS 模式
        else:
            podcasts = [p for p in CONFIG.get("podcasts", []) if p.get("enabled", True)]
            if not podcasts:
                print("⚠️ 没有启用的播客频道")
                return
            
            # 并发处理所有启用的播客频道
            await asyncio.gather(
                *[process_podcast(client, p) for p in podcasts]
            )

    print(f"\n✨ 完成")


if __name__ == "__main__":
    asyncio.run(main())
