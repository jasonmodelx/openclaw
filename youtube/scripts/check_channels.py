#!/usr/bin/env python3
"""检查 YouTube 频道和 Podcast RSS 的新内容，并用 NotebookLM Python API 异步生成制品。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from notebooklm import NotebookLMClient

from notebooklm_async import (
    DEFAULT_OUTPUT_ROOT,
    NOTEBOOK_URL,
    add_source,
    delete_notebook,
    generate_artifacts,
    get_or_create_notebook,
    resolve_artifact_types,
    sanitize_path_component,
)

try:
    import feedparser
except ImportError:
    feedparser = None

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
WAIT = os.environ.get("CHECK_CHANNELS_WAIT", "true").lower() != "false"


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_last_check():
    path = SCRIPT_DIR / Path(load_config().get("last_check_file", "last_check.json")).name
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_last_check(data):
    path = SCRIPT_DIR / Path(load_config().get("last_check_file", "last_check.json")).name
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def get_recent_videos(channel_url, max_count=5, channel_id=None):
    """通过 YouTube 频道页面获取最新视频列表。"""
    import json as _json
    import urllib.request

    if channel_id:
        page_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    else:
        page_url = channel_url.rstrip("/") + "/videos"

    try:
        req = urllib.request.Request(
            page_url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")

        match = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.DOTALL)
        if not match:
            video_ids = list(dict.fromkeys(re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)))
            return [
                {
                    "id": vid,
                    "title": vid,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                    "type": "youtube",
                }
                for vid in video_ids[:max_count]
            ]

        data = _json.loads(match.group(1))
        videos = []

        def extract(obj):
            if isinstance(obj, dict):
                if "videoRenderer" in obj:
                    renderer = obj["videoRenderer"]
                    vid = renderer.get("videoId", "")
                    title_runs = renderer.get("title", {}).get("runs", [])
                    title = title_runs[0].get("text", vid) if title_runs else vid
                    if vid:
                        videos.append(
                            {
                                "id": vid,
                                "title": title,
                                "url": f"https://www.youtube.com/watch?v={vid}",
                                "type": "youtube",
                            }
                        )
                else:
                    for value in obj.values():
                        if len(videos) >= max_count:
                            return
                        extract(value)
            elif isinstance(obj, list):
                for value in obj:
                    if len(videos) >= max_count:
                        return
                    extract(value)

        extract(data)
        return videos[:max_count]
    except Exception as exc:
        print(f"    ⚠️ 页面解析失败: {exc}")
        return []


def get_recent_episodes(feed_url, max_count=5):
    if feedparser is None:
        print("❌ feedparser 未安装", file=sys.stderr)
        return []

    try:
        feed = feedparser.parse(feed_url)
    except Exception:
        return []

    episodes = []
    for entry in feed.entries[:max_count]:
        audio_url = ""
        for link in entry.get("links", []):
            if link.get("type", "").startswith("audio/") or link.get("href", "").endswith((".mp3", ".m4a")):
                audio_url = link["href"]
                break
        if not audio_url:
            for enc in entry.get("enclosures", []):
                if enc.get("type", "").startswith("audio/"):
                    audio_url = enc.get("href", "")
                    break

        item_id = hashlib.md5(
            (entry.get("id", "") or entry.get("link", "") or entry.get("title", "")).encode()
        ).hexdigest()
        web_url = entry.get("link", "")
        episodes.append(
            {
                "id": item_id,
                "title": entry.get("title", "Unknown"),
                "url": web_url or audio_url,
                "source_url": web_url or audio_url,
                "channel": feed.feed.get("title", "Unknown Podcast"),
                "type": "podcast",
            }
        )
    return episodes


def parse_args():
    parser = argparse.ArgumentParser(description="YouTube / Podcast NotebookLM analyzer")
    parser.add_argument("--url", help="单独分析一个 YouTube 链接")
    parser.add_argument("--name", help="单链接模式下的频道/分组名称")
    parser.add_argument("--artifacts", help="逗号分隔的 artifact 类型，如 report,mind-map,slide-deck")
    parser.add_argument("--limit", type=int, default=2, help="每个频道分析最新多少条内容，默认 2")
    parser.add_argument("--channel", help="只分析指定频道，匹配名称或 URL")
    parser.add_argument("--delete-notebook", dest="delete_notebook", action="store_true", default=True)
    parser.add_argument("--keep-notebook", dest="delete_notebook", action="store_false")
    return parser.parse_args()


def get_artifact_types(config: dict, override: str | None) -> list[str]:
    if override:
        requested = [part.strip() for part in override.split(",") if part.strip()]
    else:
        requested = config.get("artifacts", ["report"])
    resolved = resolve_artifact_types(requested)
    return resolved or ["report"]


async def process_item(
    client: NotebookLMClient,
    item: dict,
    notebook_id: str,
    config: dict,
    artifact_types: list[str],
) -> dict | None:
    source_url = item.get("source_url") or item["url"]
    print(f"    🔗 添加源: {source_url}")

    try:
        source = await add_source(client, notebook_id, source_url)
        print(f"    📎 Source ID: {source.id}")
    except Exception as exc:
        print(f"    ❌ 添加源失败: {exc}")
        return None

    output_root = Path(os.path.expanduser(config.get("output_dir", str(DEFAULT_OUTPUT_ROOT))))
    channel_name = sanitize_path_component(item.get("channel") or "unknown")
    item_title = sanitize_path_component(item.get("title") or "unknown")
    item_dir = output_root / channel_name / item_title

    generated = await generate_artifacts(
        client,
        notebook_id,
        artifact_types,
        item_dir,
        source_ids=[source.id],
        language=config.get("language", "zh_Hans"),
        title=item["title"],
        wait=WAIT,
    )

    for artifact_type, status in generated["artifacts"].items():
        icon = "✅" if status == "ok" else "⏳" if status == "pending" else "❌"
        print(f"    {icon} {artifact_type}")

    report_content = None
    report_path = generated["downloaded"].get("report")
    if report_path:
        try:
            report_content = Path(report_path).read_text(encoding="utf-8")
        except Exception:
            report_content = None

    return {
        "item": item,
        "notebook_id": notebook_id,
        "notebook_url": NOTEBOOK_URL.format(id=notebook_id),
        "artifacts": generated["artifacts"],
        "downloaded": generated["downloaded"],
        "errors": generated["errors"],
        "report_content": report_content,
    }


async def process_single_item(
    client: NotebookLMClient,
    item: dict,
    channel_name: str,
    config: dict,
    artifact_types: list[str],
    delete_after: bool,
) -> dict | None:
    notebook_id = await get_or_create_notebook(
        client,
        f"{channel_name} - {item['title']}",
        language=config.get("language", "zh_Hans"),
    )
    if not notebook_id:
        print("  ❌ 无法创建 notebook")
        return None

    item["channel"] = channel_name
    result = None
    try:
        result = await process_item(client, item, notebook_id, config, artifact_types)
        return result
    finally:
        if delete_after:
            deleted = await delete_notebook(client, notebook_id)
            print(f"    {'🗑️' if deleted else '⚠️'} notebook {'已删除' if deleted else '删除失败'}")
            if result is not None and deleted:
                result["deleted_notebook"] = True


async def process_feed(
    client: NotebookLMClient,
    feed: dict,
    kind: str,
    last_check: dict,
    config: dict,
    artifact_types: list[str],
    limit: int,
    delete_after: bool,
) -> tuple[list[dict], int]:
    name = feed["name"]
    url = feed["url"]
    label = "YouTube" if kind == "youtube" else "Podcast"
    print(f"\n{'📺' if kind == 'youtube' else '🎙️'} 检查 {label}: {name}")

    if kind == "youtube":
        items = get_recent_videos(url, max_count=limit, channel_id=feed.get("channel_id"))
    else:
        items = get_recent_episodes(url, max_count=limit)

    if not items:
        print("  ⚠️ 未获取到内容")
        return [], 0

    seen_ids = set(last_check.get(url, {}).get("seen_ids", []))
    new_items = [item for item in items if item["id"] and item["id"] not in seen_ids]

    if not new_items:
        print(f"  ✅ 无新{'视频' if kind == 'youtube' else '单集'}")
        last_check[url] = {
            "seen_ids": list(seen_ids | {item["id"] for item in items if item["id"]}),
            "last_checked": datetime.now(timezone.utc).isoformat(),
            "channel_name": name,
            "notebook_id": last_check.get(url, {}).get("notebook_id"),
        }
        return [], 0

    results = []
    for item in new_items:
        print(f"\n  {'🎬' if kind == 'youtube' else '🎧'} 处理: {item['title']}")
        result = await process_single_item(
            client,
            item,
            name,
            config,
            artifact_types,
            delete_after=delete_after,
        )
        if result:
            results.append(result)

    last_check[url] = {
        "seen_ids": list(seen_ids | {item["id"] for item in items if item["id"]}),
        "last_checked": datetime.now(timezone.utc).isoformat(),
        "channel_name": name,
        "notebook_id": None,
    }
    return results, len(results)


async def process_direct_url(
    client: NotebookLMClient,
    url: str,
    name: str | None,
    config: dict,
    artifact_types: list[str],
    delete_after: bool,
) -> list[dict]:
    title = url
    video_match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", url)
    item_id = video_match.group(1) if video_match else hashlib.md5(url.encode()).hexdigest()
    item = {
        "id": item_id,
        "title": title,
        "url": url,
        "source_url": url,
        "channel": name or "direct_youtube",
        "type": "youtube",
    }
    print(f"\n🔎 单链接分析: {url}")
    result = await process_single_item(
        client,
        item,
        name or "direct_youtube",
        config,
        artifact_types,
        delete_after=delete_after,
    )
    return [result] if result else []


def save_results(all_results: list[dict]) -> None:
    if not all_results:
        return

    results_file = SCRIPT_DIR / "latest_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, ensure_ascii=False)

    summary_items = []
    for result in all_results:
        item = result["item"]
        summary_items.append(
            {
                "id": item.get("id", ""),
                "url": item.get("url", ""),
                "title": item.get("title", ""),
                "channel": item.get("channel", ""),
                "keyword": item.get("keyword", ""),
                "type": item.get("type", "youtube"),
                "notebook_id": result.get("notebook_id", ""),
                "report_content": result.get("report_content", ""),
                "artifacts": result.get("artifacts", {}),
            }
        )

    summary_path = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/youtube/logs/latest_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({"ok": True, "items": summary_items}, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已保存 {len(summary_items)} 条到 latest_summary.json")


def send_notification_if_needed(all_results: list[dict]) -> None:
    if not all_results:
        return
    notify_script = SCRIPT_DIR / "notify.py"
    results_file = SCRIPT_DIR / "latest_results.json"
    subprocess.run(
        [sys.executable, str(notify_script), str(results_file)],
        timeout=180,
        check=False,
    )


async def main_async(args) -> str:
    config = load_config()
    last_check = load_last_check()
    all_results = []
    new_count = 0
    artifact_types = get_artifact_types(config, args.artifacts)
    limit = max(args.limit, 1)

    async with await NotebookLMClient.from_storage() as client:
        if args.url:
            results = await process_direct_url(
                client,
                args.url,
                args.name,
                config,
                artifact_types,
                delete_after=args.delete_notebook,
            )
            all_results.extend(results)
            new_count += len(results)
        else:
            channels = config.get("channels", [])
            podcasts = config.get("podcasts", [])

            if args.channel:
                channel_filter = args.channel.lower()
                channels = [
                    channel
                    for channel in channels
                    if channel_filter in channel.get("name", "").lower() or channel_filter in channel.get("url", "").lower()
                ]
                podcasts = [
                    podcast
                    for podcast in podcasts
                    if channel_filter in podcast.get("name", "").lower() or channel_filter in podcast.get("url", "").lower()
                ]

            for channel in channels:
                if not channel.get("enabled", True):
                    continue
                results, count = await process_feed(
                    client,
                    channel,
                    "youtube",
                    last_check,
                    config,
                    artifact_types,
                    limit,
                    delete_after=args.delete_notebook,
                )
                all_results.extend(results)
                new_count += count

            for podcast in podcasts:
                if not podcast.get("enabled", True):
                    continue
                results, count = await process_feed(
                    client,
                    podcast,
                    "podcast",
                    last_check,
                    config,
                    artifact_types,
                    limit,
                    delete_after=args.delete_notebook,
                )
                all_results.extend(results)
                new_count += count

    save_last_check(last_check)

    keyword_results_file = SCRIPT_DIR / "keyword_results.json"
    if keyword_results_file.exists():
        try:
            with open(keyword_results_file, encoding="utf-8") as f:
                keyword_results = json.load(f)
            all_results.extend(keyword_results)
            keyword_results_file.unlink()
            print(f"\n📎 合并关键词搜索结果: {len(keyword_results)} 条")
        except Exception as exc:
            print(f"⚠️ 合并 keyword_results.json 失败: {exc}")

    save_results(all_results)
    send_notification_if_needed(all_results)

    print(f"\n{'=' * 60}")
    print(
        f"📊 汇总: {len(config.get('channels', []))} YouTube + "
        f"{len(config.get('podcasts', []))} Podcast 频道，{new_count} 个新内容"
    )
    for result in all_results:
        item = result["item"]
        ok_count = sum(1 for status in result["artifacts"].values() if status == "ok")
        total = len(result["artifacts"])
        icon = "🎙️" if item.get("type") == "podcast" else "📺"
        notebook_url = result.get("notebook_url")
        if not notebook_url and result.get("notebook_id"):
            notebook_url = NOTEBOOK_URL.format(id=result["notebook_id"])
        print(f"  {icon} {item['title']}: {ok_count}/{total} 制品 | 📓 {notebook_url or '-'}")

    return json.dumps(all_results, ensure_ascii=False)


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main_async(args))
    subprocess.run([os.path.expanduser("~/.openclaw/workspace/notebooklm-library/auto-push.sh")], timeout=30)
