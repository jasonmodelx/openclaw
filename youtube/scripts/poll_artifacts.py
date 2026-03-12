#!/usr/bin/env python3
"""
轮询 NotebookLM artifact 状态，完成后下载报告、翻译、更新 NewsHub。
每 5 分钟由 cron 调用一次。
"""

import json
import os
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = Path(__file__).parent
NOTEBOOKLM_BIN = os.path.expanduser("~/.openclaw/workspace-news/.venvs/notebooklm/bin/notebooklm")
PENDING_FILE = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/pending.json")
REPORT_BASE = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/youtube")
NEWSHUB_API = "http://localhost:3000"

LLM_API_BASE = "https://api.minimaxi.com/anthropic"
LLM_API_KEY = "sk-cp-jHEV8T7Y7maVf_vf1DCLlZDP9p_xn7_8kxQ3f6EdZcTn1BkD7kO6n6AdmSDnd_0AHxeHdAC0dc_xJz-2dy6J4k97si7_G5rUtxh2xc8z909xV58_uAQ0Itk"
LLM_MODEL = "MiniMax-M2.5"

CST = timezone(timedelta(hours=8))


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


def check_artifact_status(artifact_id, notebook_id):
    result = subprocess.run(
        [NOTEBOOKLM_BIN, "artifact", "poll", artifact_id, "-n", notebook_id],
        capture_output=True, text=True, timeout=30
    )
    # 输出格式: status='completed'|'pending'|'failed'
    import re
    m = re.search(r"status='(\w+)'", result.stdout)
    if m:
        return m.group(1)
    return "unknown"


def download_report(notebook_id):
    """下载报告到 /tmp，返回内容"""
    tmp_dir = Path("/tmp/notebooklm-dl")
    tmp_dir.mkdir(exist_ok=True)
    # 清空旧文件
    for f in tmp_dir.glob("*.md"):
        f.unlink()

    result = subprocess.run(
        [NOTEBOOKLM_BIN, "download", "report", "-n", notebook_id],
        capture_output=True, text=True, timeout=180,
        cwd=str(tmp_dir)
    )
    md_files = list(tmp_dir.glob("*.md"))
    if md_files:
        return md_files[0].read_text(encoding="utf-8")
    return None


def delete_notebook(notebook_id):
    """删除指定的 notebook"""
    try:
        result = subprocess.run(
            [NOTEBOOKLM_BIN, "delete", "-n", notebook_id, "--yes"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            print(f"  🗑️ 已删除 notebook")
            return True
        else:
            print(f"  ⚠️ 删除失败: {result.stderr[:60]}")
            return False
    except Exception as e:
        print(f"  ⚠️ 删除异常: {e}")
        return False


def download_artifact(notebook_id, artifact_type):
    """下载 PPT 或 Mind-map"""
    import time
    tmp_dir = Path("/tmp/notebooklm-dl")
    tmp_dir.mkdir(exist_ok=True)
    
    for f in tmp_dir.glob("*.pdf"):
        f.unlink()
    for f in tmp_dir.glob("*.json"):
        f.unlink()
    
    # 使用 --wait 等待生成完成（PPT生成较快）
    if artifact_type == "slide-deck":
        ext = "pdf"
        cmd = f"{NOTEBOOKLM_BIN} download slide-deck -n {notebook_id} --latest --force {tmp_dir}/output.pdf"
    else:
        ext = "json"
        cmd = f"{NOTEBOOKLM_BIN} download mind-map -n {notebook_id} --latest --force {tmp_dir}/output.json"
    
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    
    if ext == "pdf":
        files = list(tmp_dir.glob("*.pdf"))
    else:
        files = list(tmp_dir.glob("*.json"))
    
    if files:
        return files[0].read_bytes()
    return None



def translate_to_chinese(content):
    """使用 Google Translate API 翻译成中文"""
    if not content:
        return content
    
    chinese_chars = sum(1 for c in content if '\u4e00' <= c <= '\u9fff')
    # 如果已经是中文（>30%中文字符），跳过翻译
    if chinese_chars / max(len(content), 1) > 0.3:
        return content
    
    import html
    # Google Translate API
    url = 'https://translate.googleapis.com/translate_a/single'
    params = {
        'client': 'gtx',
        'sl': 'en',
        'tl': 'zh-CN',
        'dt': 't',
        'q': content[:5000]  # 限制长度
    }
    try:
        with urllib.request.urlopen(f"{url}?{urllib.parse.urlencode(params)}", timeout=30) as resp:
            result = json.loads(resp.read())
            translated = ''.join([item[0] for item in result[0] if item[0]])
            return translated
    except Exception as e:
        print(f"  ⚠️ 翻译失败: {e}")
        return content


def update_newshub(item, report_path):
    """在 NewsHub 数据库中更新或创建 news item"""
    try:
        item_type = item.get("type", "youtube")
        item_url = item.get("url", "")
        notebook_id = item.get("notebook_id", "")
        keyword = item.get("keyword", "")
        all_urls = item.get("all_urls", [item_url])

        # 对于 YouTube 关键词搜索：直接 upsert 一条 keyword 级别的 NewsItem
        if item_type == "youtube" and keyword:
            synthetic_url = f"youtube-keyword://{keyword}/{notebook_id}"
            meta = {"report_path": str(report_path), "notebook_id": notebook_id, "keyword": keyword}
            patch_data = json.dumps({
                "source": "youtube",
                "category": keyword,
                "title": f"[{keyword}] YouTube 关键词简报",
                "content": open(report_path, encoding="utf-8").read() if report_path and Path(report_path).exists() else "",
                "url": synthetic_url,
                "author": "YouTube Search",
                "publishedAt": datetime.now(CST).isoformat(),
                "metadata": meta,
            }).encode("utf-8")

            # 先尝试查找已有的
            with urllib.request.urlopen(f"{NEWSHUB_API}/api/news?limit=200", timeout=10) as resp:
                news_items = json.loads(resp.read()).get("items", [])
            matched = next((n for n in news_items if n.get("url") == synthetic_url), None)

            if matched:
                # PATCH 更新
                req = urllib.request.Request(
                    f"{NEWSHUB_API}/api/news/{matched['id']}",
                    data=json.dumps({"metadata": meta}).encode(),
                    headers={"Content-Type": "application/json"},
                    method="PATCH"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
                print(f"  ✅ NewsHub 更新: {matched['id']}")
            else:
                # POST 创建
                req = urllib.request.Request(
                    f"{NEWSHUB_API}/api/news",
                    data=patch_data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                print(f"  ✅ NewsHub 新建: {result.get('id', '?')}")
            return True

        # 对于 podcast：按 URL 匹配更新已有 NewsItem（去掉 query 参数匹配）
        with urllib.request.urlopen(f"{NEWSHUB_API}/api/news?limit=200", timeout=10) as resp:
            news_items = json.loads(resp.read()).get("items", [])

        # 去掉 URL 的 query 参数进行比较
        item_url_base = item_url.split('?')[0]
        matched = next((n for n in news_items if n.get("url", "").split('?')[0] == item_url_base), None)
        if not matched:
            # 如果没找到匹配的，自动创建一个新条目
            print(f"  ⚠️ NewsHub 中未找到匹配条目，自动创建: {item_url[:40]}...")
            meta = {"report_path": str(report_path), "notebook_id": notebook_id}
            content = ""
            try:
                content = open(report_path, encoding="utf-8").read() if Path(report_path).exists() else ""
            except:
                pass
            
            # 清理 URL，去掉 query 参数
            clean_url = item_url.split('?')[0]
            
            post_data = json.dumps({
                "source": "podcast",
                "category": item.get("channel", "podcast"),
                "title": item.get("title", "")[:100],
                "content": content[:50000],  # 限制内容长度
                "url": clean_url,
                "author": item.get("channel", "podcast"),
                "publishedAt": datetime.now(CST).isoformat(),
                "metadata": meta,
            }).encode("utf-8")
            
            try:
                req = urllib.request.Request(
                    f"{NEWSHUB_API}/api/news",
                    data=post_data,
                    headers={"Content-Type": "application/json"},
                    method="POST"
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                print(f"  ✅ NewsHub 已创建: {result.get('id', '?')}")
                return True
            except Exception as e:
                print(f"  ❌ NewsHub 创建失败: {e}")
                return False

        meta = matched.get("metadata") or {}
        if isinstance(meta, str):
            try: meta = json.loads(meta)
            except: meta = {}
        meta["report_path"] = str(report_path)
        meta["notebook_id"] = notebook_id

        # 读取并翻译内容
        try:
            content = open(report_path, encoding="utf-8").read() if Path(report_path).exists() else ""
        except:
            content = ""

        req = urllib.request.Request(
            f"{NEWSHUB_API}/api/news/{matched['id']}",
            data=json.dumps({"metadata": meta, "content": content}).encode(),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read()
        print(f"  ✅ NewsHub 已更新: {matched['id']}")
        return True
    except Exception as e:
        print(f"  ❌ NewsHub 更新失败: {e}")
        return False


def main():
    pending = load_pending()
    if not pending:
        # 无内容不打扰
        return

    print(f"🔍 检查 {len(pending)} 个待处理 artifacts...")
    remaining = []

    for item in pending:
        notebook_id = item.get("notebook_id", "")
        artifact_id = item.get("task_id") or item.get("artifact_id", "")
        title = item.get("title", "")[:50]

        print(f"\n📄 {title}")
        print(f"   notebook={notebook_id[:8]}... artifact={artifact_id[:8]}...")

        status = check_artifact_status(artifact_id, notebook_id)
        print(f"   状态: {status}")

        artifact_type = item.get("type", "report")
        
        if status == "completed":
            if artifact_type == "ppt":
                # 下载 PPT/Mind-map
                atype = item.get("artifact_type", "slide-deck")
                data = download_artifact(notebook_id, atype)
                if data:
                    print(f"  ✅ 下载成功 ({len(data)} bytes)")
                    # 更新 NewsHub
                    import requests
                    news_id = item.get("news_id")
                    if news_id:
                        meta_key = "pdf_path" if atype == "slide-deck" else "epub_path"
                        meta_val = f"/tmp/ppt-{notebook_id}.{'pdf' if atype == 'slide-deck' else 'json'}"
                        # 保存文件
                        Path(meta_val).write_bytes(data)
                        # 更新数据库
                        try:
                            r = requests.get(f"{NEWSHUB_API}/api/news/{news_id}")
                            if r.ok:
                                news = r.json()
                                meta = news.get("metadata", {}) or {}
                                meta[meta_key] = meta_val
                                requests.patch(f"{NEWSHUB_API}/api/news/{news_id}", json={"metadata": meta})
                                print(f"  💾 已更新 NewsHub metadata")
                        except Exception as e:
                            print(f"  ⚠️ 更新失败: {e}")
                    # 删除 notebook
                    delete_notebook(notebook_id)
                else:
                    print(f"  ⚠️ 下载失败")
            else:
                # 下载报告
                content = download_report(notebook_id)
                if content:
                    print(f"  ✅ 下载成功 ({len(content)} chars)，翻译中...")
                    content = translate_to_chinese(content)
                    report_dir = REPORT_BASE / notebook_id
                    report_dir.mkdir(parents=True, exist_ok=True)
                    report_path = report_dir / "report.md"
                    report_path.write_text(content, encoding="utf-8")
                    print(f"  💾 报告已保存: {report_path}")
                    update_newshub(item, report_path)
                    # 删除 notebook
                    delete_notebook(notebook_id)
                else:
                    print(f"  ⚠️ 下载失败，跳过")
            # 无论成功失败，completed/failed 都不再轮询
        elif status == "failed":
            print(f"  ❌ 生成失败，移除")
        else:
            # pending/processing/unknown → 保留
            remaining.append(item)

    save_pending(remaining)
    done_count = len(pending) - len(remaining)
    print(f"\n✅ 完成 {done_count}/{len(pending)}，剩余 {len(remaining)} 个待轮询")


if __name__ == "__main__":
    main()
