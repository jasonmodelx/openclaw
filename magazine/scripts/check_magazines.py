#!/usr/bin/env python3
"""Magazine Skill: 检查 GitHub 杂志仓库更新，下载 PDF，上传 NotebookLM，生成中文制品，邮件通知。"""

from __future__ import annotations

import asyncio
import json
import os
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.request import Request, urlopen

from notebooklm import NotebookLMClient

SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
ROOT_DIR = SKILL_DIR.parent
CONFIG_PATH = SKILL_DIR / "config.json"
LAST_CHECK_PATH = SKILL_DIR / "data" / "last_check.json"
DOWNLOAD_DIR = SKILL_DIR / "data" / "downloads"
API_BASE = "https://api.github.com/repos"

sys.path.insert(0, str(ROOT_DIR / "youtube" / "scripts"))
from notebooklm_async import (  # noqa: E402
    DEFAULT_OUTPUT_ROOT,
    delete_notebook,
    generate_artifacts,
    get_or_create_notebook,
    log_stage,
    run_with_timeout,
    sanitize_path_component,
)


def load_json(path: Path) -> dict:
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def github_api(url: str):
    req = Request(url, headers={"Accept": "application/vnd.github.v3+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        print(f"  ⚠️ GitHub API 失败: {url} - {exc}")
        return []


def get_latest_issues(repo: str, mag_path: str, count: int = 1):
    url = f"{API_BASE}/{repo}/contents/{mag_path}"
    items = github_api(url)
    dirs = [item for item in items if item["type"] == "dir"]
    dirs.sort(key=lambda item: item["name"], reverse=True)
    return dirs[:count]


def find_pdf_in_issue(repo: str, issue_path: str):
    url = f"{API_BASE}/{repo}/contents/{issue_path}"
    items = github_api(url)
    for item in items:
        if item["name"].lower().endswith(".pdf"):
            return item["name"], item.get("download_url", "")
    return None, None


def find_all_ebooks(repo: str, issue_path: str):
    url = f"{API_BASE}/{repo}/contents/{issue_path}"
    items = github_api(url)
    files = []
    for item in items:
        name = item["name"].lower()
        if name.endswith(".pdf") or name.endswith(".epub"):
            files.append((item["name"], item.get("download_url", "")))
    return files


def download_file(url: str, save_path: Path) -> bool:
    save_path.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urlopen(req, timeout=120) as resp:
            with open(save_path, "wb") as f:
                f.write(resp.read())
        return True
    except Exception as exc:
        print(f"  ❌ 下载失败: {exc}")
        return False


def send_email(config: dict, issues: list[dict]) -> None:
    email_cfg = config.get("email", {})
    if not email_cfg.get("enabled") or not issues:
        return

    html = "<h2>📰 杂志更新通知</h2>\n"
    for item in issues:
        report_line = ""
        if item.get("report_path"):
            report_line = f"中文报告: <code>{item['report_path']}</code><br>"
        html += f"""
<div style="margin:10px 0;padding:15px;border:1px solid #ddd;border-radius:8px;">
  <h3>{item['magazine']} - {item['issue']}</h3>
  制品: {item['artifacts']}<br>
  {report_line}
  NotebookLM: 已处理后删除
</div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📰 杂志更新: {len(issues)} 期新内容"
    msg["From"] = email_cfg["smtp_user"]
    msg["To"] = email_cfg["to"]
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg["smtp_port"]) as server:
            server.starttls()
            server.login(email_cfg["smtp_user"], email_cfg["smtp_pass"])
            server.send_message(msg)
        print(f"✅ 邮件已发送至 {email_cfg['to']}")
    except Exception as exc:
        print(f"❌ 邮件发送失败: {exc}")


async def add_pdf_source(client: NotebookLMClient, notebook_id: str, pdf_path: Path):
    source = await run_with_timeout(
        "sources",
        f"add_file to {notebook_id}: {pdf_path.name}",
        client.sources.add_file(notebook_id, str(pdf_path)),
        120,
    )
    return await run_with_timeout(
        "sources",
        f"wait_until_ready source={source.id}",
        client.sources.wait_until_ready(notebook_id, source.id, timeout=300),
        310,
    )


async def process_issue(
    client: NotebookLMClient,
    repo: str,
    mag_name: str,
    issue_name: str,
    issue_path: str,
    artifacts: list[str],
) -> dict | None:
    print(f"\n  📖 新期刊: {issue_name}")

    pdf_name, pdf_url = find_pdf_in_issue(repo, issue_path)
    if not pdf_name or not pdf_url:
        print("    ⚠️ 未找到 PDF")
        return None

    safe_mag = sanitize_path_component(mag_name)
    book_dir = Path(os.path.expanduser(str(DEFAULT_OUTPUT_ROOT))) / safe_mag / issue_name
    book_dir.mkdir(parents=True, exist_ok=True)

    ebooks = find_all_ebooks(repo, issue_path)
    for ebook_name, ebook_url in ebooks:
        ebook_path = book_dir / ebook_name
        if not ebook_path.exists():
            print(f"    ⬇️ 下载: {ebook_name}")
            download_file(ebook_url, ebook_path)

    save_path = DOWNLOAD_DIR / pdf_name
    print(f"    ⬇️ 下载 PDF（NotebookLM）: {pdf_name}")
    if not download_file(pdf_url, save_path):
        return None

    notebook_id = await get_or_create_notebook(client, f"{mag_name} - {issue_name}", language="zh_Hans")
    if not notebook_id:
        return None

    source = None
    try:
        print("    📎 添加到 NotebookLM...")
        source = await add_pdf_source(client, notebook_id, save_path)
        print(f"    ✅ Source ready: {source.id}")

        generated = await generate_artifacts(
            client,
            notebook_id,
            artifacts,
            book_dir,
            source_ids=[source.id],
            language="zh_Hans",
            title=f"{mag_name} {issue_name}",
            wait=True,
        )

        ok_count = sum(1 for status in generated["artifacts"].values() if status == "ok")
        report_path = generated["downloaded"].get("report")
        return {
            "magazine": mag_name,
            "issue": issue_name,
            "artifacts": f"{ok_count}/{len(artifacts)}",
            "artifacts_detail": generated["artifacts"],
            "report_path": report_path,
            "errors": generated["errors"],
        }
    finally:
        try:
            save_path.unlink(missing_ok=True)
        except Exception:
            pass
        deleted = await delete_notebook(client, notebook_id)
        print(f"    {'🗑️' if deleted else '⚠️'} notebook {'已删除' if deleted else '删除失败'}")


async def main_async() -> None:
    config = load_json(CONFIG_PATH)
    last_check = load_json(LAST_CHECK_PATH)
    repo = config["repo"]
    artifacts = config["artifacts"]
    new_issues = []

    proxy_snapshot = {
        key: os.environ.get(key)
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        if os.environ.get(key)
    }
    log_stage("env", f"proxy env: {proxy_snapshot or 'none'}")
    client = await run_with_timeout("client", "NotebookLMClient.from_storage", NotebookLMClient.from_storage(), 60)

    async with client:
        for mag in config["magazines"]:
            if not mag.get("enabled"):
                continue

            mag_name = mag["name"]
            mag_path = mag["path"]
            print(f"\n📰 检查: {mag_name}")

            processed = set(last_check.get(mag_name, {}).get("processed_issues", []))
            issues = get_latest_issues(repo, mag_path, count=1)
            if not issues:
                print("  ⚠️ 无法获取目录")
                continue

            for issue in issues:
                issue_name = issue["name"]
                issue_path = f"{mag_path}/{issue_name}"
                if issue_name in processed:
                    continue

                result = await process_issue(client, repo, mag_name, issue_name, issue_path, artifacts)
                if not result:
                    continue

                last_check.setdefault(mag_name, {})
                last_check[mag_name].setdefault("processed_issues", [])
                last_check[mag_name]["processed_issues"].append(issue_name)
                save_json(LAST_CHECK_PATH, last_check)
                new_issues.append(result)

    if new_issues:
        send_email(config, new_issues)
    else:
        print("\n📭 没有新的杂志更新")

    print(f"\n📊 汇总: {len(new_issues)} 期新内容")
    for item in new_issues:
        print(f"  📰 {item['magazine']} - {item['issue']}: {item['artifacts']} 制品 | report={item.get('report_path') or '-'}")


if __name__ == "__main__":
    asyncio.run(main_async())
    subprocess.run([os.path.expanduser("~/.openclaw/workspace/notebooklm-library/auto-push.sh")], timeout=30)
