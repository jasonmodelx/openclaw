#!/usr/bin/env python3
"""Search YouTube by keywords and analyze results with NotebookLM Python API."""

from __future__ import annotations

import asyncio
import os
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
import urllib.request

from notebooklm import NotebookLMClient

from notebooklm_async import (
    DEFAULT_OUTPUT_ROOT,
    add_source,
    delete_notebook,
    log_stage,
    run_with_timeout,
    sanitize_path_component,
)

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
CST = timezone(timedelta(hours=8))


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def youtube_search(api_key, keyword, max_results=5, hours=24):
    """Search YouTube for recent videos by keyword, only zh/en content."""
    published_after = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace("+00:00", "Z")
    results = []
    for lang in ["zh-Hans", "en"]:
        params = {
            "part": "snippet",
            "q": keyword,
            "type": "video",
            "order": "viewCount",
            "publishedAfter": published_after,
            "maxResults": max_results,
            "relevanceLanguage": lang,
            "key": api_key,
        }
        url = f"https://www.googleapis.com/youtube/v3/search?{urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode())
            for item in data.get("items", []):
                video_id = item["id"]["videoId"]
                if any(result["id"] == video_id for result in results):
                    continue
                snippet = item["snippet"]
                results.append(
                    {
                        "id": video_id,
                        "title": snippet["title"],
                        "url": f"https://www.youtube.com/watch?v={video_id}",
                        "channel": snippet["channelTitle"],
                        "published": snippet["publishedAt"],
                    }
                )
        except Exception as exc:
            print(f"⚠️ YouTube search failed for '{keyword}' ({lang}): {exc}")
    results.sort(key=lambda item: item["published"], reverse=True)
    return results[:max_results]


async def analyze_keyword(client: NotebookLMClient, keyword: str, videos: list[dict], config: dict) -> dict | None:
    if not videos:
        return None

    timestamp = datetime.now(CST).strftime("%Y%m%d_%H%M%S")
    notebook_title = f"🔎 {keyword} - {timestamp}"
    notebook = await client.notebooks.create(notebook_title)
    notebook_id = notebook.id
    source_ids = []

    try:
        try:
            await client.settings.set_language(notebook_id, config.get("language", "zh_Hans"))
        except Exception:
            pass

        for video in videos:
            try:
                source = await add_source(client, notebook_id, video["url"])
                source_ids.append(source.id)
            except Exception as exc:
                print(f"⚠️ Failed to add {video['url']}: {exc}")

        if not source_ids:
            return None

        status = await client.artifacts.generate_report(
            notebook_id,
            source_ids=source_ids,
            language=config.get("language", "zh_Hans"),
            custom_prompt="请用中文生成一份详细简报，包括核心观点、主要内容和关键结论。",
        )
        final = await client.artifacts.wait_for_completion(
            notebook_id,
            status.task_id,
            timeout=300,
            initial_interval=5,
        )
        if not final.is_complete:
            return {
                "item": {
                    "id": f"keyword:{keyword}",
                    "title": f"[{keyword}] YouTube 关键词简报",
                    "url": videos[0]["url"],
                    "type": "youtube",
                    "channel": "YouTube Search",
                    "keyword": keyword,
                },
                "notebook_id": notebook_id,
                "artifacts": {"report": "failed"},
                "downloaded": {},
                "errors": [f"report: generation not complete ({final.status})"],
                "report_content": None,
            }

        output_root = Path(config.get("output_dir", str(DEFAULT_OUTPUT_ROOT)))
        output_dir = output_root / "keywords" / sanitize_path_component(keyword)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{timestamp}_report.md"
        await client.artifacts.download_report(notebook_id, str(report_path))

        report_content = report_path.read_text(encoding="utf-8")
        return {
            "item": {
                "id": f"keyword:{keyword}",
                "title": f"[{keyword}] YouTube 关键词简报",
                "url": videos[0]["url"],
                "type": "youtube",
                "channel": "YouTube Search",
                "keyword": keyword,
                "all_urls": [video["url"] for video in videos],
            },
            "notebook_id": notebook_id,
            "artifacts": {"report": "ok"},
            "downloaded": {"report": str(report_path)},
            "errors": [],
            "report_content": report_content,
        }
    finally:
        deleted = await delete_notebook(client, notebook_id)
        print(f"{'🗑️' if deleted else '⚠️'} Keyword notebook {'deleted' if deleted else 'delete failed'}: {keyword}")


async def main_async():
    config = load_config()

    if not config.get("keyword_search", {}).get("enabled"):
        print("⚠️ Keyword search is disabled in config")
        return 0

    api_key = config.get("youtube_api_key")
    if not api_key:
        print("❌ Missing youtube_api_key in config")
        return 1

    keywords = config.get("keywords", [])
    if not keywords:
        print("⚠️ No keywords configured")
        return 0

    search_config = config["keyword_search"]
    top_n = search_config.get("top_n", 5)
    hours = search_config.get("time_range_hours", 24)

    print(f"🔍 Searching YouTube for keywords: {', '.join(keywords)}")
    print(f"📊 Top {top_n} videos from last {hours} hours\n")

    results = []
    proxy_snapshot = {
        key: os.environ.get(key)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        if os.environ.get(key)
    }
    log_stage("env", f"proxy env: {proxy_snapshot or 'none'}")
    client = await run_with_timeout("client", "NotebookLMClient.from_storage", NotebookLMClient.from_storage(), 60)
    async with client:
        for keyword in keywords:
            print(f"\n{'=' * 60}")
            print(f"Keyword: {keyword}")
            print("=" * 60)

            videos = youtube_search(api_key, keyword, max_results=top_n, hours=hours)
            if not videos:
                print(f"⚠️ No videos found for '{keyword}'")
                continue
            print(f"✅ Found {len(videos)} videos")

            result = await analyze_keyword(client, keyword, videos, config)
            if result:
                results.append(result)

    shared_file = SCRIPT_DIR / "keyword_results.json"
    if results:
        with open(shared_file, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 结果已写入 {shared_file}")
    else:
        print("\n⚠️ No keyword reports generated")

    print("\n✅ Done")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
