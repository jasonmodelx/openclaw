#!/usr/bin/env python3
"""Send email notifications for youtube skill results."""

from __future__ import annotations

import json
import smtplib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"
CST = timezone(timedelta(hours=8))


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def build_html(results):
    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    parts = [f"<h2>NotebookLM 处理报告 - {date_str}</h2>", f"<p>共 {len(results)} 个条目</p>", "<hr>"]

    for result in results:
        item = result.get("item", {})
        artifacts = result.get("artifacts", {})
        errors = result.get("errors", [])
        ok_count = sum(1 for status in artifacts.values() if status == "ok")
        fail_count = sum(1 for status in artifacts.values() if status == "failed")

        parts.append(f"<h3>{item.get('title', 'Unknown')}</h3>")
        if item.get("channel"):
            parts.append(f"<p>频道: {item['channel']}</p>")
        if item.get("url"):
            parts.append(f"<p>链接: <a href=\"{item['url']}\">{item['url']}</a></p>")
        parts.append(f"<p>制品: {ok_count} 成功 / {fail_count} 失败</p>")

        if artifacts:
            parts.append("<ul>")
            for name, status in artifacts.items():
                parts.append(f"<li>{name}: {status}</li>")
            parts.append("</ul>")

        if errors:
            parts.append("<details><summary>错误</summary><ul>")
            for error in errors:
                parts.append(f"<li>{error[:300]}</li>")
            parts.append("</ul></details>")
        parts.append("<hr>")

    parts.append("<p>报告附件优先为 PDF，转换失败时回退为 Markdown。</p>")
    return "\n".join(parts)


def build_plain(results):
    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    lines = [f"NotebookLM 处理报告 - {date_str}", f"共 {len(results)} 个条目", "=" * 50]

    for result in results:
        item = result.get("item", {})
        artifacts = result.get("artifacts", {})
        ok_count = sum(1 for status in artifacts.values() if status == "ok")
        fail_count = sum(1 for status in artifacts.values() if status == "failed")
        lines.append("")
        lines.append(item.get("title", "Unknown"))
        if item.get("channel"):
            lines.append(f"频道: {item['channel']}")
        if item.get("url"):
            lines.append(f"链接: {item['url']}")
        lines.append(f"制品: {ok_count} 成功 / {fail_count} 失败")

    lines.append("")
    lines.append("报告附件优先为 PDF，转换失败时回退为 Markdown。")
    return "\n".join(lines)


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)[:80] or "report"


def convert_markdown_to_pdf(markdown_path: Path) -> Path | None:
    pdf_path = markdown_path.with_suffix(".pdf")
    try:
        subprocess.run(
            ["pandoc", str(markdown_path), "-o", str(pdf_path), "--pdf-engine=xelatex"],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        return pdf_path if pdf_path.exists() else None
    except Exception:
        return None


def collect_attachments(results):
    attachments = []
    for result in results:
        downloaded = result.get("downloaded") or {}
        report_path = downloaded.get("report")
        if not report_path:
            continue

        markdown_path = Path(report_path)
        if not markdown_path.exists():
            continue

        pdf_path = convert_markdown_to_pdf(markdown_path)
        attach_path = pdf_path or markdown_path
        mime_type = ("application", "pdf") if pdf_path else ("text", "markdown")

        title = result.get("item", {}).get("title", markdown_path.stem)
        attachments.append(
            {
                "path": attach_path,
                "filename": safe_name(f"{title}{attach_path.suffix}"),
                "mime_type": mime_type,
            }
        )
    return attachments


def attach_file(msg: MIMEMultipart, path: Path, filename: str, mime_type: tuple[str, str]):
    part = MIMEBase(*mime_type)
    part.set_payload(path.read_bytes())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f'attachment; filename="{filename}"')
    msg.attach(part)


def send_email(results):
    results = [
        result
        for result in results
        if result.get("artifacts")
        and not any(status == "pending" for status in result["artifacts"].values())
        and any(status == "ok" for status in result["artifacts"].values())
    ]

    config = load_config()
    email_cfg = config.get("email", {})

    if not email_cfg.get("enabled"):
        print("📧 邮件通知未启用")
        return False
    if not email_cfg.get("to"):
        print("❌ 收件人邮箱未配置")
        return False
    if not results:
        print("📭 无结果，跳过邮件")
        return True

    date_str = datetime.now(CST).strftime("%Y-%m-%d")
    subject = f"NotebookLM 处理报告 [{date_str}] - {len(results)} 项"

    msg = MIMEMultipart("mixed")
    msg["Subject"] = subject
    msg["From"] = email_cfg.get("smtp_user", "noreply@localhost")
    msg["To"] = email_cfg["to"]

    body = MIMEMultipart("alternative")
    body.attach(MIMEText(build_plain(results), "plain", "utf-8"))
    body.attach(MIMEText(build_html(results), "html", "utf-8"))
    msg.attach(body)

    for attachment in collect_attachments(results):
        attach_file(msg, attachment["path"], attachment["filename"], attachment["mime_type"])
        print(f"📎 附件: {attachment['filename']}")

    try:
        with smtplib.SMTP(email_cfg["smtp_host"], email_cfg.get("smtp_port", 587)) as server:
            server.starttls()
            server.login(email_cfg["smtp_user"], email_cfg["smtp_pass"])
            server.sendmail(msg["From"], [email_cfg["to"]], msg.as_string())
        print(f"✅ 邮件已发送至 {email_cfg['to']}")
        return True
    except Exception as exc:
        print(f"❌ 邮件发送失败: {exc}")
        return False


def load_results_from_argv():
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as f:
            return json.load(f)
    return json.load(sys.stdin)


if __name__ == "__main__":
    send_email(load_results_from_argv())
