#!/usr/bin/env python3
"""播客管理脚本"""

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR.parent / "config.json"


def load_config():
    return json.loads(CONFIG_FILE.read_text())


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))


def list_podcasts():
    """列出所有播客"""
    config = load_config()
    podcasts = config.get("podcasts", [])
    
    print("\n📻 播客列表\n")
    for i, p in enumerate(podcasts, 1):
        status = "✅" if p.get("enabled") else "❌"
        print(f"{i}. {p.get('name')} {status}")
        print(f"   Apple ID: {p.get('apple_id')}")
    print()


def add_podcast(name, apple_id):
    """添加播客"""
    config = load_config()
    
    podcasts = config.get("podcasts", [])
    
    # 检查是否已存在
    for p in podcasts:
        if p.get("name") == name:
            print(f"❌ 播客 '{name}' 已存在")
            return
    
    podcasts.append({
        "name": name,
        "apple_id": apple_id,
        "enabled": True
    })
    
    config["podcasts"] = podcasts
    save_config(config)
    print(f"✅ 添加播客: {name} (Apple ID: {apple_id})")


def remove_podcast(name):
    """删除播客"""
    config = load_config()
    
    podcasts = config.get("podcasts", [])
    new_podcasts = [p for p in podcasts if p.get("name") != name]
    
    if len(new_podcasts) == len(podcasts):
        print(f"❌ 未找到播客: {name}")
        return
    
    config["podcasts"] = new_podcasts
    save_config(config)
    print(f"✅ 删除播客: {name}")


def main():
    if len(sys.argv) < 2:
        print("""
📖 Podcast 管理命令

查看列表:
  python3 manage.py list

添加播客:
  python3 manage.py add "播客名称" "Apple ID"

删除播客:
  python3 manage.py remove "播客名称"
""")
        return
    
    cmd = sys.argv[1]
    
    if cmd == "list":
        list_podcasts()
    elif cmd == "add":
        if len(sys.argv) < 4:
            print("用法: add '名称' 'Apple ID'")
            return
        name = sys.argv[2]
        apple_id = sys.argv[3]
        add_podcast(name, apple_id)
    elif cmd == "remove":
        if len(sys.argv) < 3:
            print("用法: remove '名称'")
            return
        name = sys.argv[2]
        remove_podcast(name)
    else:
        print(f"未知命令: {cmd}")


if __name__ == "__main__":
    main()
