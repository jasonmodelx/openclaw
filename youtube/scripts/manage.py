#!/usr/bin/env python3
"""YouTube 频道管理：添加/列出/删除监控频道"""

import json
import sys
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config():
    with open(CONFIG_PATH) as f:
        return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def add_channel(url, name=None):
    config = load_config()
    # 检查重复
    for ch in config["channels"]:
        if ch["url"] == url:
            print(f"❌ 频道已存在: {ch['name']} ({url})")
            return False

    channel = {
        "name": name or url.split("@")[-1].split("/")[-1],
        "url": url,
        "enabled": True
    }
    config["channels"].append(channel)
    save_config(config)
    print(f"✅ 已添加频道: {channel['name']} ({url})")
    return True


def list_channels():
    config = load_config()
    if not config["channels"]:
        print("📭 没有监控的频道")
        return

    print(f"📺 监控中的频道 ({len(config['channels'])} 个):\n")
    for i, ch in enumerate(config["channels"], 1):
        status = "✅" if ch.get("enabled", True) else "⏸️"
        print(f"  {i}. {status} {ch['name']}")
        print(f"     {ch['url']}")


def delete_channel(identifier):
    config = load_config()
    # 按名称或索引删除
    try:
        idx = int(identifier) - 1
        if 0 <= idx < len(config["channels"]):
            removed = config["channels"].pop(idx)
            save_config(config)
            print(f"✅ 已删除频道: {removed['name']}")
            return True
    except ValueError:
        pass

    # 按名称或 URL 匹配
    for i, ch in enumerate(config["channels"]):
        if identifier.lower() in ch["name"].lower() or identifier in ch["url"]:
            removed = config["channels"].pop(i)
            save_config(config)
            print(f"✅ 已删除频道: {removed['name']}")
            return True

    print(f"❌ 未找到频道: {identifier}")
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: manage.py <add|list|delete> [参数]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 3:
            print("用法: manage.py add <url> [name]")
            sys.exit(1)
        url = sys.argv[2]
        name = sys.argv[3] if len(sys.argv) > 3 else None
        add_channel(url, name)

    elif cmd == "list":
        list_channels()

    elif cmd == "delete":
        if len(sys.argv) < 3:
            print("用法: manage.py delete <name|index>")
            sys.exit(1)
        delete_channel(sys.argv[2])

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)
