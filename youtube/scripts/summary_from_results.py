#!/usr/bin/env python3
"""Build a small machine-readable summary from latest_results.json.

The goal is to keep cron payloads short and stable.
"""

import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
RESULTS = SCRIPT_DIR / "latest_results.json"
LOG_DIR = Path("/root/.openclaw/workspace/notebooklm-library/notebooklm/youtube/logs")
OUT = LOG_DIR / "latest_summary.json"


def main():
    if not RESULTS.exists():
        OUT.write_text(json.dumps({"ok": False, "error": "missing latest_results.json"}, ensure_ascii=False), encoding="utf-8")
        return

    data = json.loads(RESULTS.read_text(encoding="utf-8", errors="replace"))

    new_items = []
    for r in data:
        item = r.get("item", {})
        title = item.get("title")
        channel = item.get("channel")
        url = item.get("url")
        artifacts = r.get("artifacts", {})
        errors = r.get("errors", [])
        new_items.append({
            "type": item.get("type"),
            "channel": channel,
            "title": title,
            "url": url,
            "artifacts": artifacts,
            "errors": errors,
        })

    logs = sorted(LOG_DIR.glob("*.log"))
    latest_log = str(logs[-1]) if logs else None

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps({"ok": True, "log": latest_log, "new": len(new_items), "items": new_items[-10:]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
