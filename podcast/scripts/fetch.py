#!/usr/bin/env python3
"""
Podcast RSS 抓取 + NotebookLM 生成
正确流程：
1. 获取频道最新播客地址
2. 检查/创建频道 notebook（名称=频道名）
3. 添加源
4. 生成报告（中文）
5. 轮询等待
6. 成功下载并通知
"""

import time
import json
import os
import re
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR.parent / "config.json"
CONFIG = json.loads(CONFIG_FILE.read_text())
NOTEBOOKLM_BIN = os.path.expanduser(CONFIG.get("notebooklm_bin", "~/.openclaw/workspace-news/.venvs/notebooklm/bin/notebooklm"))
OUTPUT_DIR = Path(CONFIG.get("output_dir", "/root/.openclaw/workspace/notebooklm-library/podcast"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PENDING_FILE = OUTPUT_DIR / "pending.json"
PROCESSED_FILE = OUTPUT_DIR / "processed.json"
TODAY = datetime.now().strftime("%Y-%m-%d")


def get_feed_url_from_apple(apple_id):
    """通过 Apple Podcasts ID 获取 RSS feed"""
    try:
        r = requests.get(f"https://itunes.apple.com/lookup?id={apple_id}", timeout=10)
        data = r.json()
        if data.get('results'):
            return data['results'][0].get('feedUrl')
    except Exception as e:
        print(f"  ⚠️ Apple API 错误: {e}")
    return None


def fetch_latest_episode(rss_url):
    """获取最新单集"""
    try:
        req = urllib.request.Request(rss_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            xml = r.read()
        
        root = ET.fromstring(xml)
        item = root.find('.//item')
        if item is None:
            return None
        
        title = item.findtext('title', '').strip()
        
        # Apple Podcasts ID
        guid = item.findtext('guid', '')
        # 从 guid 中提取数字 ID
        import re
        match = re.search(r'(\d+)', guid)
        episode_id = match.group(1) if match else None
        
        if title and episode_id:
            # Apple Podcasts 链接
            apple_url = f"https://podcasts.apple.com/podcast/id1559695855?i={episode_id}"
            return {
                "title": title,
                "source_url": apple_url
            }
    except Exception as e:
        print(f"  ⚠️ RSS 解析错误: {e}")
    return None


def get_or_create_notebook(channel_name):
    """获取或创建频道 notebook（名称=频道名）"""
    try:
        # 列出所有 notebook
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "list", "--json"],
            capture_output=True, text=True, timeout=30
        )
        
        data = json.loads(result.stdout)
        notebooks = data.get("notebooks", [])
        
        # 查找已存在的 notebook
        for nb in notebooks:
            if channel_name in nb.get("title", ""):
                print(f"  ✅ 找到已有 notebook: {nb.get('id')}")
                return nb.get('id')
        
        # 创建新的 notebook（名称=频道名）
        print(f"  📝 创建新 notebook: {channel_name}")
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "create", channel_name],
            capture_output=True, text=True, timeout=30
        )
        
        output = result.stdout + result.stderr
        match = re.search(r'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})', output)
        if match:
            notebook_id = match.group(1)
            print(f"  ✅ 创建成功: {notebook_id}")
            
            # 设置语言
            subprocess.run([NOTEBOOKLM_BIN, "use", notebook_id], capture_output=True, timeout=15)
            subprocess.run([NOTEBOOKLM_BIN, "language", "set", "zh_Hans"], capture_output=True, timeout=15)
            
            return notebook_id
        
    except Exception as e:
        print(f"  ❌ 错误: {e}")
    
    return None


def add_source(notebook_id, source_url):
    """添加播客源"""
    try:
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "source", "add", source_url, "-n", notebook_id],
            capture_output=True, text=True, timeout=60
        )
        output = result.stdout + result.stderr
        if "Added source" in output or result.returncode == 0:
            print(f"  ✅ 源已添加")
            return True
        print(f"  ⚠️ 添加源: {output[:100]}")
    except Exception as e:
        print(f"  ❌ 添加源失败: {e}")
    return False


def generate_report(notebook_id):
    """生成报告"""
    try:
        # 获取 source
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "source", "list", "-n", notebook_id, "--json"],
            capture_output=True, text=True, timeout=30
        )
        
        data = json.loads(result.stdout)
        sources = data.get("sources", [])
        
        if not sources:
            print(f"  ⚠️ 没有源")
            return False
        
        source_id = sources[0].get("id")
        
        # 生成报告
        print(f"  📄 生成报告 (zh_Hans)...")
        cmd = [
            NOTEBOOKLM_BIN, "generate", "report",
            "--language", "zh_Hans",
            "--wait",
            "-n", notebook_id,
            "-s", source_id
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        output = result.stdout + result.stderr
        
        if "ready" in output.lower() or "completed" in output.lower() or result.returncode == 0:
            print(f"  ✅ 报告生成完成")
            return True
        
        print(f"  ⚠️ 生成: {output[:100]}")
    except Exception as e:
        print(f"  ❌ 生成失败: {e}")
    
    return False


def download_report(notebook_id, channel_name):
    """下载报告"""
    try:
        output_subdir = OUTPUT_DIR / channel_name.replace(" ", "_")
        output_subdir.mkdir(parents=True, exist_ok=True)
        
        subprocess.run([NOTEBOOKLM_BIN, "use", notebook_id], capture_output=True, timeout=15)
        
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "download", "report", "-n", notebook_id, "--latest"],
            capture_output=True, text=True, timeout=60, cwd=str(output_subdir)
        )
        
        output = result.stdout + result.stderr
        print(f"  📄 下载: {output[:100]}")
        
        files = list(output_subdir.glob("*.md"))
        if files:
            latest = max(files, key=lambda p: p.stat().st_mtime)
            print(f"  ✅ 报告已保存: {latest.name}")
            return str(latest)
        
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
    
    return None


def load_processed():
    if PROCESSED_FILE.exists():
        return json.loads(PROCESSED_FILE.read_text())
    return {}


def save_processed(data):
    PROCESSED_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load_pending():
    if PENDING_FILE.exists():
        return json.loads(PENDING_FILE.read_text())
    return {}


def save_pending(data):
    PENDING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def send_success_notification(channel, episode_title, report_file):
    """发送成功通知"""
    email_config = CONFIG.get("email", {})
    if not email_config.get("enabled"):
        return
    
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        
        msg = MIMEMultipart('alternative')
        msg['Subject'] = f"🎙️ 播客报告 - {channel}"
        msg['From'] = email_config.get("smtp_user")
        msg['To'] = email_config.get("to")
        
        content = f"""
# 🎙️ 播客报告

## 频道: {channel}
## 单集: {episode_title}
## 状态: ✅ 生成成功

报告: {report_file}
"""
        msg.attach(MIMEText(content, 'plain', 'utf-8'))
        
        server = smtplib.SMTP(email_config.get("smtp_host"), email_config.get("smtp_port"))
        server.starttls()
        server.login(email_config.get("smtp_user"), email_config.get("smtp_pass"))
        server.send_message(msg)
        server.quit()
        
        print(f"  ✅ 邮件已发送")
    except Exception as e:
        print(f"  ❌ 邮件失败: {e}")


def main():
    print(f"🚀 Podcast 监控 - {TODAY}")
    
    podcasts = CONFIG.get("podcasts", [])
    processed = load_processed()
    
    for podcast in podcasts:
        if not podcast.get("enabled", True):
            continue
        
        name = podcast.get("name", "Unknown")
        apple_id = podcast.get("apple_id")
        
        print(f"\n📡 处理: {name}")
        
        # 1. 获取 RSS
        if not apple_id:
            continue
        
        rss_url = get_feed_url_from_apple(apple_id)
        if not rss_url:
            print(f"  ⚠️ 无法获取 RSS")
            continue
        
        # 2. 获取最新单集
        episode = fetch_latest_episode(rss_url)
        if not episode:
            print(f"  ⚠️ 无单集")
            continue
        
        print(f"  📰 最新: {episode['title']}")
        
        # 3. 检查是否已处理
        key = f"{name}:{episode['title'][:50]}"
        if key in processed:
            print(f"  ⏭️ 已处理，跳过")
            continue
        
        # 4. 获取/创建 notebook
        notebook_id = get_or_create_notebook(name)
        if not notebook_id:
            print(f"  ❌ notebook 失败")
            continue
        
        # 5. 添加源
        if not add_source(notebook_id, episode['source_url']):
            print(f"  ❌ 添加源失败")
            continue
        
        # 6. 保存到待轮询
        pending = load_pending()
        pending[key] = {
            "notebook_id": notebook_id,
            "channel": name,
            "title": episode['title'],
            "created_at": datetime.now(CST).isoformat()
        }
        save_pending(pending)
        print(f"  ✅ 已添加到轮询")
    
    # 处理轮询
    pending = load_pending()
    for key, task in pending.items():
        notebook_id = task.get("notebook_id")
        channel = task.get("channel")
        title = task.get("title")
        
        print(f"\n🔄 轮询: {channel}")
        
        # 检查并生成报告
        success = False
        for i in range(5):
            time.sleep(30)
            
            # 获取源状态
            result = subprocess.run(
                [NOTEBOOKLM_BIN, "source", "list", "-n", notebook_id, "--json"],
                capture_output=True, text=True, timeout=30
            )
            
            data = json.loads(result.stdout)
            sources = data.get("sources", [])
            
            if not sources:
                continue
            
            # 检查是否 ready
            status = sources[0].get("status", "")
            if "ready" in status.lower():
                # 生成报告
                if generate_report(notebook_id):
                    success = True
                    break
        
        if success:
            # 下载报告
            report_file = download_report(notebook_id, channel)
            if report_file:
                send_success_notification(channel, title, report_file)
                
                # 保存到已处理
                processed[key] = {"done": True, "report": report_file}
                save_processed(processed)
                
                # 从 pending 删除
                del pending[key]
                save_pending(pending)
                print(f"  ✅ 完成")
        else:
            print(f"  ❌ 失败")
    
    print(f"\n✨ 完成")


if __name__ == "__main__":
    main()
