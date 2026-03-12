#!/usr/bin/env python3
"""播客 RSS 抓取 + NotebookLM 生成"""

import time
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

CST = timezone(timedelta(hours=8))
NOTEBOOKLM_BIN = os.path.expanduser("~/.openclaw/workspace-news/.venvs/notebooklm/bin/notebooklm")

PODCASTS = [
    {"name": "知行小酒馆", "apple_id": "1559695855"},
    {"name": "Lex Fridman", "apple_id": "1434243584"},
]

OUTPUT = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/youtube/logs/latest_summary.json")
PENDING_FILE = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/pending.json")
MAX_EPISODES = 2


def fetch_rss_from_apple(apple_id):
    """通过 Apple Podcasts ID 获取 RSS feed"""
    try:
        import requests
        r = requests.get(f"https://itunes.apple.com/lookup?id={apple_id}", timeout=10)
        data = r.json()
        if data.get('results'):
            return data['results'][0].get('feedUrl')
    except Exception as e:
        print(f"  ⚠️ Apple API 错误: {e}")
    return None


def fetch_episodes_from_rss(rss_url, apple_id):
    """解析 RSS 获取单集列表"""
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read()
        root = ET.fromstring(xml)
        episodes = []
        for item in root.findall('.//item')[:MAX_EPISODES]:
            title = item.findtext('title', '').strip()
            link = item.findtext('link', '').strip()
            pub_date = item.findtext('pubDate', '')
            enclosure = item.find('enclosure')
            audio_url = enclosure.get('url') if enclosure is not None else link
            
            # 尝试获取 Apple Podcasts 链接
            apple_link = None
            if link and apple_id:
                # 从 RSS 获取 Apple 的 guid
                guid = item.findtext('guid', '')
                if guid and len(guid) > 10:
                    apple_link = f"https://podcasts.apple.com/podcast/id{apple_id}?i={guid}"
            
            if title and (audio_url or apple_link):
                episodes.append({
                    "title": title, 
                    "url": link, 
                    "audio_url": apple_link if apple_link else audio_url,
                    "pub_date": pub_date
                })
        return episodes
    except Exception as e:
        print(f"  ⚠️ RSS 解析错误: {e}")
        return []


def parse_podcast_feed(xml_str, channel_name):
    root = ET.fromstring(xml_str)
    episodes = []
    for item in root.findall('.//item')[:MAX_EPISODES]:
        title = item.findtext('title', '').strip()
        link = item.findtext('link', '').strip()
        pub_date = item.findtext('pubDate', '')
        enclosure = item.find('enclosure')
        audio_url = enclosure.get('url') if enclosure is not None else link
        if title and audio_url:
            episodes.append({"title": title, "url": link, "audio_url": audio_url, "pub_date": pub_date, "channel": channel_name})
    return episodes


def create_notebook_with_retry(episode, max_retries=3):
    """创建 notebook 并触发生成，重试机制返回 (notebook_id, artifact_id, success, error_msg)"""
    title = episode["title"][:80]
    audio_url = episode.get("audio_url") or episode.get("url", "")
    last_error = None

    for attempt in range(1, max_retries + 1):
        print(f"  第 {attempt}/{max_retries} 次尝试...")
        
        # 创建 notebook
        result = subprocess.run([NOTEBOOKLM_BIN, "create", title], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            last_error = f"创建失败: {result.stderr[:60]}"
            print(f"    ❌ {last_error}")
            time.sleep(2)
            continue

        m = re.search(r'Created notebook:\s*([0-9a-f\-]{36})', result.stdout)
        if not m:
            last_error = "无法提取 notebook_id"
            print(f"    ❌ {last_error}")
            time.sleep(2)
            continue
        
        notebook_id = m.group(1)
        print(f"    📓 notebook: {notebook_id}")

        # 设置语言
        subprocess.run([NOTEBOOKLM_BIN, "use", notebook_id], capture_output=True, timeout=15)
        subprocess.run([NOTEBOOKLM_BIN, "language", "set", "zh_Hans"], capture_output=True, timeout=15)

        # 添加源
        result = subprocess.run([NOTEBOOKLM_BIN, "source", "add", audio_url], capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            last_error = f"添加源失败: {result.stderr[:60]}"
            print(f"    ⚠️ {last_error}")
            time.sleep(2)
            continue

        # 触发生成
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "generate", "report", "--language", "zh_Hans", "--json", "请用中文生成详细简报"],
            capture_output=True, text=True, timeout=30
        )
        
        # 检查错误
        try:
            data = json.loads(result.stdout) if result.stdout.strip() else {}
            if data.get("error"):
                last_error = f"生成失败: {data.get('message', 'unknown')}"
                print(f"    ❌ {last_error}")
                time.sleep(2)
                continue
            artifact_id = data.get("task_id") or data.get("id") or data.get("artifact_id")
        except:
            artifact_id = None
        
        if not artifact_id:
            last_error = "无法获取 artifact_id"
            print(f"    ❌ {last_error}")
            time.sleep(2)
            continue
        
        print(f"    🚀 成功: artifact={artifact_id[:8]}...")
        return notebook_id, artifact_id, True, None

    # 所有重试都失败
    return notebook_id if 'notebook_id' in locals() else None, None, False, last_error


def send_error_notification(episode, error_msg):
    """发送错误通知到 Telegram"""
    try:
        import requests
        # 从环境变量或配置文件获取
        import os
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID")
        
        # 如果没设置，尝试从 openclaw.json 读取
        if not token or not chat_id:
            import json
            oc_config = json.loads(open("/root/.openclaw/openclaw.json").read())
            tg = oc_config.get("telegram", {}).get("telegram", {})
            token = token or tg.get("botToken")
            chat_id = chat_id or tg.get("chatIds", [None])[0]
        
        if token and chat_id:
            message = f"🔴 **NotebookLM 生成失败**\n\n标题: {episode['title']}\n错误: {error_msg}"
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10
            )
            print(f"  📧 已发送错误通知")
    except Exception as e:
        print(f"  ⚠️ 通知发送失败: {e}")


def main():
    all_items = []
    new_pending = []
    existing_pending = json.loads(PENDING_FILE.read_text()).get("pending", []) if PENDING_FILE.exists() else []
    existing_urls = {p.get("url", "").split("?")[0] for p in existing_pending}

    for podcast in PODCASTS:
        print(f"\n📡 抓取 {podcast['name']}...")
        apple_id = podcast.get('apple_id')
        
        try:
            # 方式1: 通过 Apple ID 获取 RSS
            if apple_id:
                rss_url = fetch_rss_from_apple(apple_id)
                if rss_url:
                    print(f"  📍 RSS: {rss_url[:50]}...")
                    episodes = fetch_episodes_from_rss(rss_url, apple_id)
                else:
                    episodes = []
            else:
                # 方式2: 直接用 RSS URL
                episodes = fetch_episodes_from_rss(podcast.get('url', ''), None)
            
            print(f"  ✅ 获取 {len(episodes)} 集")
        except Exception as e:
            print(f"  ❌ 失败: {e}")
            continue

        for ep in episodes:
            print(f"\n🎙️ {ep['title'][:50]}")
            url_base = ep["url"].split("?")[0]
            
            # 跳过已有的
            if url_base in existing_urls:
                print(f"  ⏭️ 已存在，跳过")
                continue

            notebook_id, artifact_id, success, error_msg = create_notebook_with_retry(ep)

            if success and artifact_id:
                new_pending.append({
                    "notebook_id": notebook_id,
                    "artifact_id": artifact_id,
                    "type": "podcast",
                    "title": ep["title"],
                    "url": ep["url"],
                    "channel": podcast["name"],
                    "pub_date": ep["pub_date"],
                })
            elif error_msg:
                # 所有重试都失败，发送错误通知
                print(f"  🔴 错误: {error_msg}")
                send_error_notification(ep, error_msg)

    # 保存 pending
    merged = existing_pending + [p for p in new_pending if p["url"].split("?")[0] not in existing_urls]
    PENDING_FILE.write_text(json.dumps({"pending": merged}, ensure_ascii=False, indent=2))
    print(f"\n✅ 共 {len(new_pending)} 条加入 pending")

    # 导入 NewsHub（显示为待生成）
    try:
        import requests
        count = 0
        # 收集所有新处理的 episodes
        all_new_episodes = []
        for podcast in PODCASTS:
            # 重新获取 RSS（或者从之前的处理中收集）
            # 这里我们用 new_pending 中的信息来导入
            for p in new_pending:
                data = {
                    "source": "podcast",
                    "category": p.get("channel", podcast["name"]),
                    "title": p.get("title", "")[:100],
                    "content": "等待 NotebookLM 生成报告...",
                    "url": p.get("url", ""),
                    "author": p.get("channel", podcast["name"]),
                    "publishedAt": datetime.now(CST).isoformat(),
                }
                try:
                    r = requests.post("http://localhost:3000/api/news", json=data, timeout=10)
                    if r.status_code == 200:
                        count += 1
                except Exception as e:
                    pass
        print(f"✅ 已导入 {count} 条到 NewsHub")
    except Exception as e:
        print(f"⚠️ 导入失败: {e}")


if __name__ == "__main__":
    main()
