#!/usr/bin/env python3
"""
Search YouTube for trending videos by keywords and analyze with NotebookLM (async).
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
import urllib.request

SCRIPT_DIR = Path(__file__).parent
CONFIG_PATH = SCRIPT_DIR / "config.json"
NOTEBOOKLM_BIN = os.path.expanduser("~/.openclaw/workspace-news/.venvs/notebooklm/bin/notebooklm")
PENDING_FILE = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/pending.json")

CST = timezone(timedelta(hours=8))

LLM_API_BASE = "https://api.aicodewith.com"
LLM_API_KEY = "sk-acw-c22780b7-7965caba8e570302"
LLM_MODEL = "claude-sonnet-4-6"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def youtube_search(api_key, keyword, max_results=5, hours=24):
    """Search YouTube for recent videos by keyword, only zh/en content."""
    published_after = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat().replace('+00:00', 'Z')
    results = []
    for lang in ['zh-Hans', 'en']:
        params = {
            'part': 'snippet',
            'q': keyword,
            'type': 'video',
            'order': 'viewCount',
            'publishedAfter': published_after,
            'maxResults': max_results,
            'relevanceLanguage': lang,
            'key': api_key,
        }
        url = f"https://www.googleapis.com/youtube/v3/search?{urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.loads(response.read().decode())
            for item in data.get('items', []):
                video_id = item['id']['videoId']
                if any(r['id'] == video_id for r in results):
                    continue
                snippet = item['snippet']
                results.append({
                    'id': video_id,
                    'title': snippet['title'],
                    'url': f"https://www.youtube.com/watch?v={video_id}",
                    'channel': snippet['channelTitle'],
                    'published': snippet['publishedAt'],
                })
        except Exception as e:
            print(f"⚠️ YouTube search failed for '{keyword}' ({lang}): {e}")
    results.sort(key=lambda x: x['published'], reverse=True)
    return results[:max_results]


def create_notebook_async(videos, keyword):
    """创建 notebook，添加视频，异步触发生成（不等待），返回 (notebook_id, artifact_id)"""
    if not videos:
        return None, None

    title = f"YouTube Trending: {keyword} - {datetime.now(CST).strftime('%Y-%m-%d')}"
    result = subprocess.run([NOTEBOOKLM_BIN, "create", title], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Failed to create notebook: {result.stderr}")
        return None, None

    import re
    notebook_id = None
    m = re.search(r'Created notebook:\s*([0-9a-f\-]{36})', result.stdout)
    if m:
        notebook_id = m.group(1)
    if not notebook_id:
        print(f"❌ Could not extract notebook_id: {result.stdout}")
        return None, None

    print(f"✅ Created notebook: {notebook_id}")
    
    # 先设置语言，再添加视频和生成
    subprocess.run([NOTEBOOKLM_BIN, "language", "set", "zh_Hans"], capture_output=True)
    
    subprocess.run([NOTEBOOKLM_BIN, "use", notebook_id], capture_output=True)

    for video in videos:
        r = subprocess.run([NOTEBOOKLM_BIN, "source", "add", video['url']], capture_output=True, text=True)
        if r.returncode != 0:
            print(f"⚠️ Failed to add {video['url']}: {r.stderr[:60]}")
    print(f"✅ Added {len(videos)} videos to notebook")

    result = subprocess.run(
        [NOTEBOOKLM_BIN, "generate", "report", "--json",
         "请用中文生成一份详细的简报，包括核心观点、主要内容和关键结论"],
        capture_output=True, text=True,
    )
    artifact_id = None
    try:
        data = json.loads(result.stdout)
        artifact_id = data.get('task_id') or data.get('id') or data.get('artifact_id')
    except Exception:
        pass

    if artifact_id:
        print(f"🚀 已触发生成: artifact={artifact_id[:8]}...")
    else:
        print(f"⚠️ Could not get artifact id: {result.stdout[:100]}")

    return notebook_id, artifact_id


def translate_to_chinese(content):
    if not content:
        return content
    chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
    
    # 如果已经是中文（>30%中文字符），跳过翻译
    if chinese_chars / max(len(content), 1) > 0.3:
        return content
    
    # 分段翻译，每段 4000 字，避免超时
    CHUNK = 4000
    chunks = [content[i:i+CHUNK] for i in range(0, min(len(content), 12000), CHUNK)]
    translated_parts = []
    for idx, chunk in enumerate(chunks):
        payload = json.dumps({
            "model": LLM_MODEL,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": f"请将以下内容翻译成中文，保持原有的markdown格式和结构，直接输出翻译结果：\n\n{chunk}"}]
        }).encode('utf-8')
        req = urllib.request.Request(
            f"{LLM_API_BASE}/v1/messages",
            data=payload,
            headers={'Content-Type': 'application/json', 'x-api-key': LLM_API_KEY, 'anthropic-version': '2023-06-01'}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                translated_parts.append(json.loads(resp.read())['content'][0]['text'])
        except Exception as e:
            print(f"⚠️ 翻译第{idx+1}段失败: {e}")
            translated_parts.append(chunk)
    return "\n\n".join(translated_parts)


def load_pending():
    if not PENDING_FILE.exists():
        return []
    try:
        return json.loads(PENDING_FILE.read_text())["pending"]
    except Exception:
        return []


def save_pending(items):
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps({"pending": items}, ensure_ascii=False, indent=2))


def main():
    config = load_config()

    if not config.get('keyword_search', {}).get('enabled'):
        print("⚠️ Keyword search is disabled in config")
        return

    api_key = config.get('youtube_api_key')
    if not api_key:
        print("❌ Missing youtube_api_key in config")
        return 1

    keywords = config.get('keywords', [])
    if not keywords:
        print("⚠️ No keywords configured")
        return

    search_config = config['keyword_search']
    top_n = search_config.get('top_n', 5)
    hours = search_config.get('time_range_hours', 24)

    print(f"🔍 Searching YouTube for keywords: {', '.join(keywords)}")
    print(f"📊 Top {top_n} videos from last {hours} hours\n")

    all_results = []
    new_pending = []

    for keyword in keywords:
        print(f"\n{'='*60}")
        print(f"Keyword: {keyword}")
        print('='*60)

        videos = youtube_search(api_key, keyword, max_results=top_n, hours=hours)
        if not videos:
            print(f"⚠️ No videos found for '{keyword}'")
            continue
        print(f"✅ Found {len(videos)} videos")

        notebook_id, artifact_id = create_notebook_async(videos, keyword)

        for i, video in enumerate(videos):
            all_results.append({
                "item": {
                    "id": video['id'],
                    "title": video['title'],
                    "url": video['url'],
                    "type": "youtube",
                    "channel": video['channel'],
                    "keyword": keyword,
                },
                "notebook_id": notebook_id or "",
                "artifacts": {"report": "pending"},
                "errors": [],
                "report_content": None,
            })

        if notebook_id and artifact_id:
            new_pending.append({
                "notebook_id": notebook_id,
                "artifact_id": artifact_id,
                "type": "youtube",
                "title": f"YouTube: {keyword}",
                "url": videos[0]['url'],  # 代表 URL，用于 NewsHub 匹配第一条
                "keyword": keyword,
                "all_urls": [v['url'] for v in videos],  # 关联所有视频
            })

    # 写入 keyword_results.json 供 check_channels.py 合并
    if all_results:
        shared_file = SCRIPT_DIR / "keyword_results.json"
        with open(shared_file, 'w', encoding='utf-8') as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\n✅ 结果已写入 {shared_file}")
    else:
        print("\n⚠️ No videos found")

    # 追加 pending（去重）
    existing_pending = load_pending()
    existing_urls = {p.get("url", "") for p in existing_pending}
    merged = existing_pending + [p for p in new_pending if p.get("url") not in existing_urls]
    save_pending(merged)
    print(f"✅ 已记录 {len(new_pending)} 个 artifacts 到 pending.json")

    print("\n✅ Done")


if __name__ == "__main__":
    sys.exit(main() or 0)
