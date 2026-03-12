#!/usr/bin/env python3
"""goal-tracker: 个人目标管理工具库
字段: id,person,category,item,freq,type,target,unit,goal_cycle,notes
type: check(是否完成) / duration(时长) / limit(限制,不超过)
unit: min / clock / times
goal_cycle: 日/周/月 - 统计完成度的周期
"""

import csv
import json
import os
import calendar
import shutil
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess

CST = timezone(timedelta(hours=8))
BASE_DIR = Path(os.path.expanduser("~/.openclaw/workspace/notebooklm-library/个人管理"))
# Prefer a git-backed location when present (your private repo clone).
# This keeps all outputs in the same place that gets auto commit+push.
_GIT_BASE = Path(os.path.expanduser("~/.openclaw/workspace/notebooklm-library/个人管理"))
if _GIT_BASE.exists():
    BASE_DIR = _GIT_BASE
GOALS_CSV = BASE_DIR / "目标库.csv"
CHECKIN_CSV = BASE_DIR / "打卡记录.csv"
DIARY_DIR = BASE_DIR / "日记"
DAILY_DIR = BASE_DIR / "日总结"
WEEKLY_DIR = BASE_DIR / "周总结"
MONTHLY_DIR = BASE_DIR / "月总结"
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"

REPO_DIR = Path(os.path.expanduser("~/.openclaw/workspace/notebooklm-library"))
AUTO_PUSH = REPO_DIR / "auto-push.sh"
PUSH_DEBOUNCE_SEC = 300
PUSH_STAMP = BASE_DIR / ".auto-push.stamp"


def now_cst():
    return datetime.now(CST)


def maybe_git_pull_for_sync():
    """Pull latest changes from GitHub for sync commands only.

    This supports the workflow where notes are edited elsewhere (e.g. Obsidian)
    and pushed to GitHub, then the server sync command reads those files.
    """
    try:
        if not REPO_DIR.exists():
            return True
        # Refuse to pull if there are local modifications to avoid conflicts.
        res = subprocess.run(["git", "status", "--porcelain"], cwd=str(REPO_DIR), capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or "git status failed")
        dirty = res.stdout.strip().splitlines() if res.stdout.strip() else []
        if dirty:
            # Allow sync commands to proceed if the only local change is the diary file
            # for the same date that we are about to (re)generate.
            # Otherwise abort to avoid conflicts.
            if all(line.startswith("D ") for line in dirty):
                return True
            raise RuntimeError("local repo has uncommitted changes; abort pull")

        res = subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=str(REPO_DIR), capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(res.stderr.strip() or res.stdout.strip() or "git pull failed")
        return True
    except Exception as e:
        raise RuntimeError(f"git pull failed: {e}")


def maybe_auto_push():
    """Debounced git commit+push for the notebooklm-library repo.

    We keep personal-management writes (diary/checkin) visible on GitHub,
    but avoid spamming commits by pushing at most once per debounce window.
    """
    try:
        if not AUTO_PUSH.exists() or not REPO_DIR.exists():
            return False

        stamp = PUSH_STAMP
        now_ts = now_cst().timestamp()
        if stamp.exists():
            try:
                last = float(stamp.read_text(encoding="utf-8").strip() or "0")
            except ValueError:
                last = 0.0
            if now_ts - last < PUSH_DEBOUNCE_SEC:
                return False

        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now_ts), encoding="utf-8")

        subprocess.run(["bash", str(AUTO_PUSH)], cwd=str(REPO_DIR), check=False)
        return True
    except Exception:
        return False


def today_str():
    return now_cst().strftime("%Y-%m-%d")


def parse_date_str(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d")


# ─── 目标库 ───

def load_goals():
    """加载目标库，返回 list of dict"""
    goals = []
    if not GOALS_CSV.exists():
        return goals
    with open(GOALS_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("item", "").strip():
                goals.append(row)
    return goals


def get_goals(person=None, goal_cycle=None, category=None):
    """按条件筛选目标"""
    goals = load_goals()
    if person:
        goals = [g for g in goals if g["person"] == person]
    if goal_cycle:
        goals = [g for g in goals if g["goal_cycle"] == goal_cycle]
    if category:
        goals = [g for g in goals if g["category"] == category]
    return goals


# ─── 打卡记录 ───

def append_checkin(date_str, person, goal_id, value):
    """追加一条打卡记录，并同步到日记打卡表格"""
    CHECKIN_CSV.parent.mkdir(parents=True, exist_ok=True)
    file_exists = CHECKIN_CSV.exists() and CHECKIN_CSV.stat().st_size > 0
    with open(CHECKIN_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["日期", "姓名", "目标ID", "目标名称", "记录值"])
        writer.writerow([date_str, person, goal_id, "", value])

    # 同步到日记打卡表格
    _sync_checkin_to_diary(date_str, person, goal_id, value)

    # Debounced git commit+push so backfills land in the repo.
    maybe_auto_push()


def _sync_checkin_to_diary(date_str, person, goal_id, value):
    """将打卡记录同步到日记的打卡表格（更新已有行，不插入重复行）"""
    path = ensure_diary(date_str)
    content = path.read_text(encoding="utf-8")

    # 查找目标信息
    goals = load_goals()
    goal = next((g for g in goals if g["id"] == goal_id and g["person"] == person), None)
    if not goal:
        item_name = goal_id
        gtype = "check"
        target = ""
        unit = ""
    else:
        item_name = goal["item"]
        gtype = goal["type"]
        target = goal.get("target", "")
        unit = goal.get("unit", "")

    # 构建实际值和状态
    if gtype == "duration":
        target_str = f"{target}{unit}" if target else "-"
        actual_str = f"{value}{unit}"
        status = "✅" if target and int(value) >= int(target) else "🔄"
    elif gtype == "check":
        target_str = "-"
        actual_str = "✅"
        status = "✅"
    elif gtype == "limit":
        target_str = f"≤{target}{unit}" if target else "-"
        actual_str = str(value)
        if unit == "clock":
            status = "✅" if target and str(value) <= str(target) else "⚠️"
        else:
            try:
                status = "✅" if target and int(value) <= int(target) else "⚠️"
            except (ValueError, TypeError):
                status = "⚠️"
    else:
        target_str = str(target)
        actual_str = str(value)
        status = "✅"

    person_prefix = f"{person}/" if person != "陈坚" else ""
    display_name = f"{person_prefix}{item_name}"
    new_row = f"| {display_name} | {gtype} | {target_str} | {actual_str} | {status} |  |"

    table_marker = "## ✅ 目标打卡"
    lines = content.split("\n")

    # 尝试找到并更新已有行（匹配目标名称的行，实际值为空的优先更新）
    in_table = False
    updated = False
    empty_match_idx = None  # 找到的空行索引（预填的空行）
    filled_match_idx = None  # 找到的已填行索引

    for i, line in enumerate(lines):
        if table_marker in line:
            in_table = True
            continue
        if in_table:
            # 表格结束（遇到空行后的非表格行）
            if line.startswith("---") or (line.strip() and not line.strip().startswith("|")):
                break
            # 匹配目标名称列
            if line.startswith("|") and f"| {display_name} |" in line:
                cols = [c.strip() for c in line.split("|")]
                # cols[4] 是"实际"列
                if len(cols) > 4 and cols[4] == "":
                    empty_match_idx = i  # 优先更新空行
                elif len(cols) > 4:
                    filled_match_idx = i  # 记录已填行（可能需要更新）

    # 优先更新空行；若没有空行则更新已填行（值叠加情况不处理，直接覆盖最后一次打卡）
    target_idx = empty_match_idx if empty_match_idx is not None else filled_match_idx

    if target_idx is not None:
        lines[target_idx] = new_row
        updated = True
    else:
        # 没找到匹配行，插入到表格头部
        if table_marker in content:
            for i, line in enumerate(lines):
                if table_marker in line:
                    for j in range(i + 1, min(i + 6, len(lines))):
                        if lines[j].strip().startswith("|---"):
                            lines.insert(j + 1, new_row)
                            updated = True
                            break
                    break
        if not updated:
            table = f"\n{table_marker}\n\n| 目标 | 类型 | 目标值 | 实际 | 状态 |\n|------|------|--------|------|------|\n{new_row}\n"
            content += table
            path.write_text(content, encoding="utf-8")
            return

    content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")


def load_checkins(start_date=None, end_date=None, person=None):
    """加载打卡记录"""
    records = []
    if not CHECKIN_CSV.exists():
        return records
    with open(CHECKIN_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if person and row.get("姓名") != person:
                continue
            d = row.get("日期", "")
            if start_date and d < start_date:
                continue
            if end_date and d > end_date:
                continue
            records.append(row)
    return records


def get_today_checkins(person=None):
    return load_checkins(start_date=today_str(), end_date=today_str(), person=person)


def get_week_checkins(person=None):
    now = now_cst()
    monday = now - timedelta(days=now.weekday())
    return load_checkins(start_date=monday.strftime("%Y-%m-%d"), end_date=now.strftime("%Y-%m-%d"), person=person)


def get_month_checkins(person=None):
    now = now_cst()
    first_day = now.replace(day=1).strftime("%Y-%m-%d")
    return load_checkins(start_date=first_day, end_date=now.strftime("%Y-%m-%d"), person=person)


def get_day_checkins_for_date(date_str, person=None):
    return load_checkins(start_date=date_str, end_date=date_str, person=person)


def get_week_checkins_for_date(date_str, person=None):
    day = parse_date_str(date_str)
    monday = day - timedelta(days=day.weekday())
    return load_checkins(start_date=monday.strftime("%Y-%m-%d"), end_date=date_str, person=person)


def get_month_checkins_for_date(date_str, person=None):
    day = parse_date_str(date_str)
    first_day = day.replace(day=1).strftime("%Y-%m-%d")
    return load_checkins(start_date=first_day, end_date=date_str, person=person)


def _get_cycle_checkins(goal_cycle, person=None):
    """根据 goal_cycle 获取对应周期的打卡记录"""
    if goal_cycle == "日":
        return get_today_checkins(person)
    elif goal_cycle == "周":
        return get_week_checkins(person)
    elif goal_cycle == "月":
        return get_month_checkins(person)
    return []


def _get_cycle_checkins_for_date(goal_cycle, date_str, person=None):
    if goal_cycle == "日":
        return get_day_checkins_for_date(date_str, person)
    if goal_cycle == "周":
        return get_week_checkins_for_date(date_str, person)
    if goal_cycle == "月":
        return get_month_checkins_for_date(date_str, person)
    return []


# ─── 日记 ───

def get_diary_path(date_str=None):
    if not date_str:
        date_str = today_str()
    return DIARY_DIR / f"{date_str}.md"


def ensure_diary(date_str=None):
    if not date_str:
        date_str = today_str()
    path = get_diary_path(date_str)
    if not path.exists():
        DIARY_DIR.mkdir(parents=True, exist_ok=True)
        template = (TEMPLATE_DIR / "日记.md").read_text(encoding="utf-8")
        content = template.replace("{{date}}", date_str)
        content = content.replace("{{datetime}}", now_cst().strftime("%Y-%m-%d %H:%M"))
        path.write_text(content, encoding="utf-8")

        # Pre-fill the check-in table with all goals from the goal library.
        # Actual/value/status will be updated by checkins later.
        _prefill_diary_checkin_table(path, date_str)
    return path


def _goal_target_str(goal):
    gtype = goal.get("type", "")
    target = (goal.get("target") or "").strip()
    unit = (goal.get("unit") or "").strip()
    if gtype == "duration":
        return f"{target}{unit}" if target else "-"
    if gtype == "check":
        return "-"
    if gtype == "limit":
        if unit == "clock":
            return target if target else "-"
        return f"≤{target}{unit}" if target else "-"
    return target or "-"


def _prefill_diary_checkin_table(path, date_str):
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return

    marker = "## ✅ 目标打卡"
    if marker not in content:
        return

    goals = load_goals()
    persons = ["陈坚", "嘉嘉", "诺诺"]

    rows = []
    for person in persons:
        my = [g for g in goals if g.get("person") == person]
        if not my:
            continue
        for g in my:
            item = g.get("item", "").strip()
            if not item:
                continue
            person_prefix = f"{person}/" if person != "陈坚" else ""
            gtype = g.get("type", "")
            target_str = _goal_target_str(g)
            rows.append(f"| {person_prefix}{item} | {gtype} | {target_str} |  |  |  |")

    # Insert rows right after the table header separator.
    lines = content.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("|------") and i > 0 and "目标" in lines[i - 1]:
            insert_idx = i + 1
            break
    if insert_idx is None:
        return

    # Only prefill if table currently has no rows.
    if insert_idx < len(lines) and lines[insert_idx].strip().startswith("|"):
        return

    for r in reversed(rows):
        lines.insert(insert_idx, r)

    path.write_text("\n".join(lines), encoding="utf-8")


def append_to_diary(category, text, date_str=None, ts_prefix=True):
    path = ensure_diary(date_str)
    content = path.read_text(encoding="utf-8")

    # Add a time prefix like [14:58] by default (CST).
    if ts_prefix:
        ts = now_cst().strftime("%H:%M")
        if not text.strip().startswith("["):
            text = f"[{ts}] {text}"
    category_map = {
        "健康": "### 健康", "育儿": "### 育儿", "投资": "### 投资",
        "学习": "### 学习", "社交": "### 社交", "其他": "### 其他",
        "英语": "### 学习", "自我管理": "### 其他",
    }
    header = category_map.get(category, "### 其他")
    if header in content:
        lines = content.split("\n")
        header_idx = -1
        for i, line in enumerate(lines):
            if line.strip() == header:
                header_idx = i
                break
        
        if header_idx != -1:
            # 寻找该分类的结束位置（下一个三级标题或二级标题）
            insert_idx = header_idx + 1
            last_item_idx = -1
            placeholder_idx = -1
            
            for i in range(header_idx + 1, len(lines)):
                line = lines[i].strip()
                if line.startswith("###") or line.startswith("##") or line.startswith("---"):
                    break
                if line.startswith("-"):
                    if line == "-" or line == "- ":
                        placeholder_idx = i
                    else:
                        last_item_idx = i
                if line or i == header_idx + 1: # 记录最后一个非空行或紧跟标题的行
                    insert_idx = i + 1

            if placeholder_idx != -1 and last_item_idx == -1:
                # 如果只有占位符，直接替换
                lines[placeholder_idx] = f"- {text}"
            elif last_item_idx != -1:
                # 追加到最后一个列表项之后
                lines.insert(last_item_idx + 1, f"- {text}")
                # 检查并移除可能存在的孤立占位符
                if placeholder_idx != -1:
                    p_idx = placeholder_idx if placeholder_idx < last_item_idx + 1 else placeholder_idx + 1
                    lines.pop(p_idx)
            else:
                # 既无占位符也无列表项，直接在 insert_idx 插入
                lines.insert(insert_idx, f"- {text}")

        content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    return path


# ─── 图片保存 ───

def save_photo_to_diary(src_path, caption=None, date_str=None):
    """将图片保存到日记 assets 目录，并在日记中插入引用。
    返回 (保存路径, md引用文本)"""
    if not date_str:
        date_str = today_str()
    assets_dir = DIARY_DIR / "assets" / date_str
    assets_dir.mkdir(parents=True, exist_ok=True)

    src = Path(src_path)
    if not src.exists():
        return None, None

    # 生成文件名：用时间戳+原始后缀
    ts = now_cst().strftime("%H%M%S")
    name = caption.replace(" ", "-").replace("/", "-")[:30] if caption else ts
    suffix = src.suffix or ".jpg"
    dest_name = f"{name}{suffix}"
    dest = assets_dir / dest_name

    # 避免重名
    counter = 1
    while dest.exists():
        dest = assets_dir / f"{name}-{counter}{suffix}"
        counter += 1

    shutil.copy2(str(src), str(dest))

    # 返回相对路径引用
    rel_path = f"assets/{date_str}/{dest.name}"
    md_ref = f"![{caption or dest.name}]({rel_path})"
    return str(dest), md_ref


def _parse_table_row(line):
    # Expect a markdown table row like:
    # | 目标 | 类型 | 目标值 | 实际 | 状态 | 备注 |
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return cells


def _resolve_goal(person, item_name):
    goals = load_goals()
    matched = next((g for g in goals if g.get("item") == item_name and g.get("person") == person), None)
    if not matched:
        matched = next((g for g in goals if (item_name in g.get("item", "") or g.get("item", "") in item_name) and g.get("person") == person), None)
    return matched


def _parse_backfill_value(gtype, remarks):
    remarks = (remarks or "").strip()
    if not remarks:
        return ""
    if gtype == "check":
        return "1"
    if gtype == "duration":
        match = re.search(r"\d+(?:\.\d+)?", remarks)
        return match.group(0) if match else remarks
    return remarks


def apply_checkins_from_text(text, date_str=None):
    """Parse a markdown check-in table from pasted text and append checkins."""
    if not date_str:
        date_str = today_str()

    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.strip().startswith("| 目标"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("未找到补打卡表格表头")

    applied = 0
    i = header_idx + 2
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("|"):
            break
        cells = _parse_table_row(line)
        if len(cells) < 6:
            i += 1
            continue

        goal_cell, gtype, _, _, _, remarks = cells[:6]
        if not remarks:
            i += 1
            continue

        if "/" in goal_cell:
            person, item_name = goal_cell.split("/", 1)
        else:
            person, item_name = "陈坚", goal_cell

        value = _parse_backfill_value(gtype, remarks)
        if not value:
            i += 1
            continue

        matched = _resolve_goal(person, item_name)
        gid = matched["id"] if matched else item_name
        append_checkin(date_str, person, gid, value)
        applied += 1
        i += 1

    return applied


def sync_checkins_from_diary(date_str=None):
    """Read diary check-in table remarks and turn them into checkins."""
    if not date_str:
        date_str = today_str()

    maybe_git_pull_for_sync()
    path = ensure_diary(date_str)
    content = path.read_text(encoding="utf-8")

    marker = "## ✅ 目标打卡"
    if marker not in content:
        raise RuntimeError("Diary missing check-in table")

    lines = content.split("\n")

    # Find table header
    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("| 目标"):
            start = i
            break
    if start is None:
        raise RuntimeError("Diary check-in table header not found")

    # Determine if remarks column exists
    header_cells = _parse_table_row(lines[start])
    has_remarks = len(header_cells) >= 6 and header_cells[5] == "备注"

    # Iterate rows after separator
    i = start + 2
    updated = False
    applied = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip().startswith("|"):
            break
        cells = _parse_table_row(line)
        if len(cells) < 5:
            i += 1
            continue

        goal_cell = cells[0]
        gtype = cells[1]
        actual = cells[3] if len(cells) > 3 else ""
        status = cells[4] if len(cells) > 4 else ""
        remarks = ""
        if has_remarks and len(cells) >= 6:
            remarks = cells[5].strip()

        if remarks:
            if "/" in goal_cell:
                person, item_name = goal_cell.split("/", 1)
            else:
                person, item_name = "陈坚", goal_cell

            value = _parse_backfill_value(gtype, remarks)

            matched = _resolve_goal(person, item_name)
            gid = matched["id"] if matched else item_name

            append_checkin(date_str, person, gid, value)
            # Update table row: set actual/status, clear remarks
            cells = cells + [""] * (6 - len(cells))
            cells[3] = value if gtype != "check" else "✅"
            cells[4] = "✅"
            cells[5] = ""
            lines[i] = "| " + " | ".join(cells) + " |"
            updated = True
            applied += 1

        i += 1

    if updated:
        path.write_text("\n".join(lines), encoding="utf-8")
        maybe_auto_push()

    return applied


def _parse_time_range(line):
    s = line.strip()
    if "-" not in s:
        return None, None
    left, right = s.split("-", 1)
    left = left.strip()
    right = right.strip()
    if len(left) == 5 and left[2] == ":" and len(right) == 5 and right[2] == ":":
        return left, right
    return None, None


def sync_time_log(date_str=None):
    """Parse personal-management time log for the day and append a summary into diary."""
    if not date_str:
        date_str = today_str()

    maybe_git_pull_for_sync()
    tdir = BASE_DIR / "时间日志"

    # Accept both zero-padded (YYYY-MM-DD.md) and non-padded (YYYY-M-D.md).
    tpath = tdir / f"{date_str}.md"
    if not tpath.exists():
        try:
            y, m, d = date_str.split("-")
            alt = f"{int(y)}-{int(m)}-{int(d)}.md"
        except Exception:
            alt = None
        if alt:
            tpath = tdir / alt

    if not tpath.exists():
        raise RuntimeError(f"time log not found: {tpath}")

    lines = tpath.read_text(encoding="utf-8").split("\n")

    entries = []

    # Format A: simple lines like "09:10-10:00 something"
    for line in lines:
        start, end = _parse_time_range(line)
        if start and end:
            rest = line.split(end, 1)[1].strip() if end in line else ""
            rest = rest.lstrip("- ")
            entries.append((start, end, rest))

    # Format B: a markdown table with a "时间" column (like Obsidian tables)
    if not entries:
        in_table = False
        for line in lines:
            s = line.strip()
            if s.startswith("| 时间"):
                in_table = True
                continue
            if not in_table:
                continue
            if s.startswith("|---") or s.startswith("| ---"):
                continue
            if not s.startswith("|"):
                continue

            cells = [c.strip() for c in s.strip("|").split("|")]
            if not cells:
                continue
            time_cell = cells[0]
            start, end = _parse_time_range(time_cell)
            if start and end:
                plan = cells[1] if len(cells) > 1 else ""
                done = cells[2] if len(cells) > 2 else ""
                note = cells[3] if len(cells) > 3 else ""
                eval_ = cells[4] if len(cells) > 4 else ""
                parts = [p for p in [plan, done, note, eval_] if p]
                rest = " / ".join(parts)
                entries.append((start, end, rest))

    if not entries:
        raise RuntimeError("no time range entries parsed")

    # Ensure diary has a full goal table before syncing.
    diary = ensure_diary(date_str)

    # Keep only non-empty entries and sort by start time.
    def _time_key(hhmm):
        try:
            h, m = hhmm.split(":")
            return int(h) * 60 + int(m)
        except Exception:
            return 0

    entries = [(s, e, txt) for (s, e, txt) in entries if (txt or "").strip()]
    entries.sort(key=lambda t: _time_key(t[0]))

    # Write each entry as a diary flow item under "Other".
    for s, e, txt in entries:
        item = f"[{s}-{e}] {txt}".strip()
        append_to_diary("其他", item, date_str=date_str)

    maybe_auto_push()
    return len(entries)


def append_photo_to_diary(src_path, category, caption=None, date_str=None):
    """保存图片并追加到日记对应分类下"""
    dest, md_ref = save_photo_to_diary(src_path, caption, date_str)
    if not dest:
        return None

    path = ensure_diary(date_str)
    content = path.read_text(encoding="utf-8")
    category_map = {
        "健康": "### 健康", "育儿": "### 育儿", "投资": "### 投资",
        "学习": "### 学习", "社交": "### 社交", "其他": "### 其他",
    }
    header = category_map.get(category, "### 其他")

    if header in content:
        lines = content.split("\n")
        header_idx = -1
        for i, line in enumerate(lines):
            if line.strip() == header:
                header_idx = i
                break
        
        if header_idx != -1:
            text = f"{caption}\n  {md_ref}" if caption else md_ref
            
            # 寻找该分类的结束位置
            insert_idx = header_idx + 1
            last_item_idx = -1
            placeholder_idx = -1
            
            for i in range(header_idx + 1, len(lines)):
                line = lines[i].strip()
                if line.startswith("###") or line.startswith("##") or line.startswith("---"):
                    break
                if line.startswith("-"):
                    if line == "-" or line == "- ":
                        placeholder_idx = i
                    else:
                        last_item_idx = i
                if line or i == header_idx + 1:
                    insert_idx = i + 1

            if placeholder_idx != -1 and last_item_idx == -1:
                lines[placeholder_idx] = f"- {text}"
            elif last_item_idx != -1:
                lines.insert(last_item_idx + 1, f"- {text}")
                if placeholder_idx != -1:
                    p_idx = placeholder_idx if placeholder_idx < last_item_idx + 1 else placeholder_idx + 1
                    lines.pop(p_idx)
            else:
                lines.insert(insert_idx, f"- {text}")

        content = "\n".join(lines)
    path.write_text(content, encoding="utf-8")
    return dest


# ─── 完成度计算 ───

def _calc_progress_for_goal(goal, checkins):
    """计算单个目标的完成度"""
    gid = goal["id"]
    person = goal["person"]
    gtype = goal["type"]
    target = goal.get("target", "")
    unit = goal.get("unit", "")
    item = goal["item"]

    my_checkins = [c for c in checkins if c.get("目标ID") == gid and c.get("姓名") == person]

    result = {
        "id": gid, "person": person, "category": goal["category"],
        "item": item, "type": gtype, "target": target, "unit": unit,
        "goal_cycle": goal["goal_cycle"],
    }

    if gtype == "check":
        # Treat an explicit 0 value as "not done" (useful for backfills).
        done = any(str(c.get("记录值", "1")).strip() not in ("", "0") for c in my_checkins)
        result["actual"] = "✅" if done else "未记录"
        result["status"] = "✅" if done else "⬜"
        result["count"] = len(my_checkins)

    elif gtype == "duration":
        total = sum(int(c.get("记录值", 0) or 0) for c in my_checkins)
        try:
            t = int(target)
        except (ValueError, TypeError):
            t = 0
        result["actual"] = total
        result["target_val"] = t
        result["status"] = "✅" if total >= t else "⬜"
        result["pct"] = f"{int(total / t * 100)}%" if t > 0 else "-"

    elif gtype == "limit":
        if unit == "clock":
            # 时间限制（如 23:30 前睡觉）
            if my_checkins:
                last_val = my_checkins[-1].get("记录值", "")
                result["actual"] = last_val
                result["status"] = "✅" if last_val and str(last_val) <= str(target) else "⬜"
            else:
                result["actual"] = "未记录"
                result["status"] = "⬜"
        elif unit == "times":
            # For times-limit goals, the value itself is the number of occurrences.
            # If users record 0 explicitly, it should count as 0 (not 1 record).
            if my_checkins:
                try:
                    count = sum(int(c.get("记录值", 0) or 0) for c in my_checkins)
                except (ValueError, TypeError):
                    count = len(my_checkins)
            else:
                count = 0
            try:
                t = int(target)
            except (ValueError, TypeError):
                t = 0
            result["actual"] = count
            result["target_val"] = t
            result["status"] = "✅" if count <= t else "❌"
            result["over"] = count > t

    return result


def get_daily_progress():
    """今日完成度：日目标 + 周目标本周累计"""
    goals = load_goals()
    today_ck = get_today_checkins()
    week_ck = get_week_checkins()

    day_goals = []
    week_progress = []

    for g in goals:
        if g["goal_cycle"] == "日":
            day_goals.append(_calc_progress_for_goal(g, today_ck))
        elif g["goal_cycle"] == "周":
            week_progress.append(_calc_progress_for_goal(g, week_ck))

    now = now_cst()
    days_left = 6 - now.weekday()
    for wp in week_progress:
        wp["days_left"] = days_left

    return {"日目标": day_goals, "周目标进度": week_progress}


def get_weekly_progress():
    """本周完成度 + 月目标本月累计"""
    goals = load_goals()
    week_ck = get_week_checkins()
    month_ck = get_month_checkins()

    week_goals = []
    month_progress = []

    for g in goals:
        if g["goal_cycle"] == "周":
            week_goals.append(_calc_progress_for_goal(g, week_ck))
        elif g["goal_cycle"] == "月":
            month_progress.append(_calc_progress_for_goal(g, month_ck))

    now = now_cst()
    days_left_month = calendar.monthrange(now.year, now.month)[1] - now.day
    for mp in month_progress:
        mp["days_left"] = days_left_month

    return {"周目标": week_goals, "月目标进度": month_progress}


def get_monthly_progress():
    """本月完成度"""
    goals = load_goals()
    month_ck = get_month_checkins()
    month_goals = []
    for g in goals:
        if g["goal_cycle"] == "月":
            month_goals.append(_calc_progress_for_goal(g, month_ck))
    return {"月目标": month_goals}


# ─── 提醒逻辑 ───

def check_reminders():
    """检查未完成目标，返回需要提醒的列表"""
    now = now_cst()
    # Only remind between 14:00-22:00 CST (inclusive of 14, exclusive of 22).
    if now.hour < 14 or now.hour >= 22:
        return []

    reminders = []
    goals = load_goals()

    for g in goals:
        person = g["person"]
        item = g["item"]
        cycle = g["goal_cycle"]
        gtype = g["type"]
        target = g.get("target", "")

        if cycle == "日":
            checkins = get_today_checkins(person)
            my_ck = [c for c in checkins if c.get("目标ID") == g["id"]]
            if gtype == "check" and len(my_ck) == 0:
                reminders.append(f"⏰ {person}：「{item}」今日未记录")
            elif gtype == "duration":
                total = sum(int(c.get("记录值", 0) or 0) for c in my_ck)
                try:
                    t = int(target)
                except (ValueError, TypeError):
                    t = 0
                if t > 0 and total < t:
                    reminders.append(f"⏰ {person}：「{item}」今日 {total}/{t} 分钟")
            elif gtype == "limit" and g.get("unit") == "clock":
                # 时间限制只在接近时提醒
                pass

        elif cycle == "周":
            weekday = now.weekday()
            if weekday >= 2:  # 周三起
                checkins = get_week_checkins(person)
                my_ck = [c for c in checkins if c.get("目标ID") == g["id"]]
                if gtype == "duration":
                    total = sum(int(c.get("记录值", 0) or 0) for c in my_ck)
                    try:
                        t = int(target)
                    except (ValueError, TypeError):
                        t = 0
                    if t > 0 and total < t:
                        reminders.append(f"⏰ {person}：「{item}」本周 {total}/{t} 分钟（周{'一二三四五六日'[weekday]}）")
                elif gtype == "check" and len(my_ck) == 0:
                    reminders.append(f"⏰ {person}：「{item}」本周未完成（周{'一二三四五六日'[weekday]}）")
                elif gtype == "limit":
                    try:
                        t = int(target)
                    except (ValueError, TypeError):
                        t = 0
                    if len(my_ck) > t:
                        reminders.append(f"⚠️ {person}：「{item}」本周已 {len(my_ck)} 次，超过限制 {t} 次！")

        elif cycle == "月":
            day = now.day
            if day >= 20:  # 20号起
                checkins = get_month_checkins(person)
                my_ck = [c for c in checkins if c.get("目标ID") == g["id"]]
                if gtype == "duration":
                    total = sum(int(c.get("记录值", 0) or 0) for c in my_ck)
                    try:
                        t = int(target)
                    except (ValueError, TypeError):
                        t = 0
                    if t > 0 and total < t:
                        reminders.append(f"⏰ {person}：「{item}」本月 {total}/{t} 分钟（{day}号）")
                elif gtype == "check" and len(my_ck) == 0:
                    reminders.append(f"⏰ {person}：「{item}」本月未完成（{day}号）")

    return reminders


def get_pending_checkins(date_str=None):
    """Return incomplete check-ins for a date, formatted for manual backfill."""
    if not date_str:
        date_str = today_str()

    day = parse_date_str(date_str)
    weekday = day.weekday()
    month_day = day.day
    pending = []

    for g in load_goals():
        person = g["person"]
        cycle = g["goal_cycle"]
        gtype = g["type"]
        target = g.get("target", "")
        unit = g.get("unit", "")
        checkins = _get_cycle_checkins_for_date(cycle, date_str, person)
        my_ck = [c for c in checkins if c.get("目标ID") == g["id"]]

        if gtype == "duration":
            actual = sum(float(c.get("记录值", 0) or 0) for c in my_ck)
            try:
                target_num = float(target or 0)
            except (ValueError, TypeError):
                target_num = 0
            if target_num <= 0:
                continue

            should_include = (
                cycle == "日"
                or (cycle == "周" and weekday >= 2)
                or (cycle == "月" and month_day >= 20)
            )
            if should_include and actual < target_num:
                pending.append({
                    "person": person,
                    "item": g["item"],
                    "type": gtype,
                    "target": _goal_target_str(g),
                    "actual": f"{actual:g}{unit}",
                    "status": f"{cycle}待补，填写本次补记数值",
                })

        elif gtype == "check":
            should_include = (
                cycle == "日"
                or (cycle == "周" and weekday >= 2)
                or (cycle == "月" and month_day >= 20)
            )
            if should_include and len(my_ck) == 0:
                pending.append({
                    "person": person,
                    "item": g["item"],
                    "type": gtype,
                    "target": _goal_target_str(g),
                    "actual": "未记录",
                    "status": f"{cycle}待补，完成填 1",
                })

    return pending


def format_backfill_template(date_str=None):
    if not date_str:
        date_str = today_str()

    pending = get_pending_checkins(date_str)
    if not pending:
        return f"🎉 {date_str} 没有待补打卡内容。"

    lines = [
        f"# {date_str} 补打卡",
        "",
        "请直接复制这张表，在“备注”列填写补打卡值后回传解析。",
        "",
        "| 目标 | 类型 | 目标值 | 已记录 | 状态 | 备注 |",
        "|------|------|--------|--------|------|------|",
    ]
    for row in pending:
        goal_name = row["item"] if row["person"] == "陈坚" else f"{row['person']}/{row['item']}"
        lines.append(
            f"| {goal_name} | {row['type']} | {row['target']} | {row['actual']} | {row['status']} |  |"
        )
    return "\n".join(lines)


# ─── 格式化输出 ───

def format_daily_progress_md():
    """格式化日进度为 markdown"""
    p = get_daily_progress()
    lines = []

    # 按人分组
    persons = sorted(set(g["person"] for g in p["日目标"] + p["周目标进度"]))

    for person in persons:
        lines.append(f"\n### {person}")

        day_goals = [g for g in p["日目标"] if g["person"] == person]
        if day_goals:
            lines.append("\n**日目标**\n")
            lines.append("| 分类 | 目标 | 类型 | 目标值 | 实际 | 状态 |")
            lines.append("|------|------|------|--------|------|------|")
            for g in day_goals:
                if g["type"] == "check":
                    lines.append(f"| {g['category']} | {g['item']} | 打卡 | - | {g['actual']} | {g['status']} |")
                elif g["type"] == "duration":
                    lines.append(f"| {g['category']} | {g['item']} | 时长 | {g['target']}{g['unit']} | {g['actual']}{g['unit']} | {g['status']} |")
                elif g["type"] == "limit":
                    lines.append(f"| {g['category']} | {g['item']} | 限制 | ≤{g['target']}{g['unit']} | {g['actual']} | {g['status']} |")

        week_goals = [g for g in p["周目标进度"] if g["person"] == person]
        if week_goals:
            lines.append("\n**周目标（本周累计）**\n")
            lines.append("| 分类 | 目标 | 目标值 | 累计 | 进度 | 剩余天 |")
            lines.append("|------|------|--------|------|------|--------|")
            for g in week_goals:
                if g["type"] == "duration":
                    lines.append(f"| {g['category']} | {g['item']} | {g['target']}{g['unit']} | {g['actual']}{g['unit']} | {g.get('pct','-')} | {g['days_left']}天 |")
                elif g["type"] == "limit":
                    lines.append(f"| {g['category']} | {g['item']} | ≤{g['target']}次 | {g['actual']}次 | {g['status']} | {g['days_left']}天 |")
                else:
                    lines.append(f"| {g['category']} | {g['item']} | - | {g.get('count',0)}次 | {g['status']} | {g['days_left']}天 |")

    return "\n".join(lines)


def create_web_token():
    """Create a web checkin token and return URL"""
    import secrets
    
    web_dir = Path(__file__).parent.parent / "web"
    tokens_file = web_dir / "tokens.json"
    tokens_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Load existing tokens
    tokens = {}
    if tokens_file.exists():
        with open(tokens_file, encoding='utf-8') as f:
            tokens = json.load(f)
    
    # Create new token
    token = secrets.token_urlsafe(16)
    tokens[token] = {
        "user": "陈坚",
        "created": now_cst().isoformat(),
        "used": False
    }
    
    with open(tokens_file, 'w', encoding='utf-8') as f:
        json.dump(tokens, f, ensure_ascii=False, indent=2)
    
    # Get server IP (use public IP)
    try:
        import urllib.request
        ip = urllib.request.urlopen('https://ifconfig.me', timeout=5).read().decode().strip()
    except:
        # Fallback to local IP
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except:
            ip = "43.165.133.231"  # Hardcoded fallback
    
    url = f"http://{ip}:8080/checkin/{token}"
    print(f"✅ 打卡链接已生成（5分钟内有效）:")
    print(url)
    return url

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: goal_tracker.py [checkin|remind|progress|diary|backfill-template|parse-backfill|goals]")
        sys.exit(1)

    cmd = sys.argv[1]
    
    if cmd == "web-token":
        create_web_token()
    elif cmd == "checkin":
        # goal_tracker.py checkin <person> <goal_name_or_id> <value>
        if len(sys.argv) < 5:
            print("用法: goal_tracker.py checkin <姓名> <目标名称或ID> <值>")
            sys.exit(1)
        person, gid_or_name, value = sys.argv[2], sys.argv[3], sys.argv[4]
        date_str = today_str()
        if "--date" in sys.argv[5:]:
            idx = sys.argv.index("--date")
            if idx + 1 >= len(sys.argv):
                print("❌ --date 缺少日期参数")
                sys.exit(1)
            date_str = sys.argv[idx + 1]
        # 先按 id 匹配，再按 item 名称匹配
        goals = load_goals()
        matched = next((g for g in goals if g["id"] == gid_or_name and g["person"] == person), None)
        if not matched:
            matched = next((g for g in goals if g["item"] == gid_or_name and g["person"] == person), None)
        if not matched:
            # 模糊匹配：目标名包含输入或输入包含目标名
            matched = next((g for g in goals if (gid_or_name in g["item"] or g["item"] in gid_or_name) and g["person"] == person), None)
        gid = matched["id"] if matched else gid_or_name
        display_name = matched["item"] if matched else gid_or_name
        append_checkin(date_str, person, gid, value)
        maybe_auto_push()
        print(f"✅ 已记录: {date_str} {person} - {display_name}: {value}")

    elif cmd == "remind":
        reminders = check_reminders()
        if reminders:
            print("\n".join(reminders))
        else:
            print("🎉 所有目标都已完成！")

    elif cmd == "progress":
        sub = sys.argv[2] if len(sys.argv) > 2 else "daily"
        if sub == "daily":
            print(format_daily_progress_md())
        elif sub == "weekly":
            p = get_weekly_progress()
            print(json.dumps(p, ensure_ascii=False, indent=2))
        elif sub == "monthly":
            p = get_monthly_progress()
            print(json.dumps(p, ensure_ascii=False, indent=2))

    elif cmd == "diary":
        if len(sys.argv) < 4:
            print("用法: goal_tracker.py diary <分类> <内容>")
            sys.exit(1)
        cat, text = sys.argv[2], " ".join(sys.argv[3:])
        append_to_diary(cat, text)
        maybe_auto_push()
        print(f"✅ 已写入日记 [{cat}]: {text}")

    elif cmd == "backfill-template":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        print(format_backfill_template(date_str))

    elif cmd == "parse-backfill":
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        text = sys.stdin.read()
        if not text.strip():
            print("❌ 未收到补打卡内容，请通过 stdin 传入 Markdown 表格")
            sys.exit(1)
        try:
            n = apply_checkins_from_text(text, date_str)
            print(f"✅ 已解析补打卡: {n} 条")
        except Exception as e:
            print(f"❌ 解析补打卡失败: {e}")
            sys.exit(2)

    elif cmd == "sync-checkins":
        # goal_tracker.py sync-checkins [YYYY-MM-DD]
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        try:
            n = sync_checkins_from_diary(date_str)
            print(f"✅ 已同步补打卡: {n} 条")
        except Exception as e:
            print(f"❌ 同步补打卡失败: {e}")
            sys.exit(2)

    elif cmd == "sync-timelog":
        # goal_tracker.py sync-timelog [YYYY-MM-DD]
        date_str = sys.argv[2] if len(sys.argv) > 2 else None
        try:
            n = sync_time_log(date_str)
            print(f"✅ 已同步时间日志: {n} 条")
        except Exception as e:
            print(f"❌ 同步时间日志失败: {e}")
            sys.exit(2)

    elif cmd == "goals":
        # 目标库管理
        #
        # 用法:
        #   goal_tracker.py goals                 # 列出所有目标
        #   goal_tracker.py goals <person>       # 只列出某个人
        #   goal_tracker.py goals remove <id>    # 删除目标（按 id 精确匹配）
        if len(sys.argv) >= 3 and sys.argv[2] == "remove":
            if len(sys.argv) < 4:
                print("用法: goal_tracker.py goals remove <目标ID>")
                sys.exit(1)
            gid = sys.argv[3]
            goals_path = GOALS_CSV
            if not goals_path.exists():
                print(f"❌ 目标库不存在: {goals_path}")
                sys.exit(1)

            rows = []
            removed = []
            with open(goals_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                fieldnames = reader.fieldnames or []
                for row in reader:
                    if row.get("id") == gid:
                        removed.append(row)
                    else:
                        rows.append(row)

            if not removed:
                print(f"❌ 未找到目标ID: {gid}")
                sys.exit(1)

            tmp = goals_path.with_suffix(goals_path.suffix + ".tmp")
            with open(tmp, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
            tmp.replace(goals_path)

            maybe_auto_push()
            r = removed[0]
            print(f"✅ 已删除目标: {r.get('person','?')} - {r.get('item','?')} ({gid})")
        else:
            # 列出所有目标
            goals = load_goals()
            person = sys.argv[2] if len(sys.argv) > 2 else None
            if person:
                goals = [g for g in goals if g["person"] == person]
            for g in goals:
                t = f" → {g['target']}{g['unit']}" if g.get("target") else ""
                print(f"  {g['id']:20s} | {g['person']} | {g['category']} | {g['item']} | {g['type']}{t} | {g['goal_cycle']}")

    elif cmd == "photo":
        # goal_tracker.py photo <src_path> <category> [caption]
        if len(sys.argv) < 4:
            print("用法: goal_tracker.py photo <图片路径> <分类> [描述]")
            sys.exit(1)
        src = sys.argv[2]
        cat = sys.argv[3]
        cap = " ".join(sys.argv[4:]) if len(sys.argv) > 4 else None
        dest = append_photo_to_diary(src, cat, cap)
        if dest:
            print(f"✅ 已保存图片到日记: {dest}")
        else:
            print("❌ 图片保存失败")
