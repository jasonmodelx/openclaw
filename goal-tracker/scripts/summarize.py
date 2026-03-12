#!/usr/bin/env python3
"""Goal-tracker summaries (daily/weekly/monthly)."""
import os
import shlex
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess

tzh = timezone(timedelta(hours=8))

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent

REPO_DIR = Path(os.getenv("GOAL_TRACKER_NOTEBOOK_DIR", "/root/.openclaw/workspace/notebooklm-library"))
PM_DIR = REPO_DIR / "个人管理"
DIARY_DIR = PM_DIR / "日记"
DAILY_DIR = PM_DIR / "日总结"
TEMPLATE_DAILY = SKILL_DIR / "templates/日总结.md"
DAILY_PROMPT_FILE = SKILL_DIR / "templates/日总结提示词.md"
GOAL_TRACKER = SCRIPT_DIR / "goal_tracker.py"
AI_COMMAND = shlex.split(os.getenv("GOAL_TRACKER_AI_CMD", "openclaw chat"))

DEFAULT_DAILY_PROMPT = """【角色】你是一个专业的生活复盘教练，擅长从当日日记和目标进度中提炼重点、识别情绪与模式，并给出克制且可执行的建议

【任务】分析以下当天日记内容和目标进度，生成结构化的日总结

【输出要求】
请严格按以下 Markdown 结构输出，不要添加结构之外的说明：

## 📝 今日回顾

### 学习
- 提炼今天学习上的关键投入、主题、产出
- 如果没有学习记录，明确写“无明显记录”

### 育儿
- 提炼今天育儿中的关键互动、冲突或亮点
- 如果没有育儿记录，明确写“无明显记录”

### 情绪
- 判断整体情绪状态（平稳/波动/低落/积极等）
- 说明主要触发因素，以及是否影响了行动

### 其他要点
- 提取今天最值得记录的 2-4 个事项
- 可以包含健康、投资、社交、生活琐事等

---

## 💡 AI 建议
1. 给出 3 条具体、克制、明天就能执行的建议
2. 建议必须基于当天真实情况，不要空泛说教
3. 优先指出最重要的约束或杠杆点

---

## ❓ 今日反思（请回答）
1. 生成 3 个高质量反思问题
2. 问题要贴合当天发生的事，帮助用户识别模式、优先级或情绪触发点

【额外约束】
- 不要杜撰日记中没有出现的事实
- 语言简洁，少用套话
- 如果记录不足，直接指出信息有限，不要强行分析
"""

def load_daily_prompt():
    try:
        prompt = DAILY_PROMPT_FILE.read_text(encoding="utf-8").strip()
        if prompt:
            return prompt
        print(f"⚠️ 日总结提示词文件为空，改用内置提示词: {DAILY_PROMPT_FILE}")
    except FileNotFoundError:
        print(f"⚠️ 日总结提示词文件不存在，改用内置提示词: {DAILY_PROMPT_FILE}")
    except Exception as exc:
        print(f"⚠️ 读取日总结提示词失败，改用内置提示词: {exc}")
    return DEFAULT_DAILY_PROMPT

def get_progress():
    """获取目标进度"""
    try:
        result = subprocess.run(
            ["python3", str(GOAL_TRACKER), "progress", "daily"],
            capture_output=True, text=True, timeout=30
        )
        return result.stdout if result.returncode == 0 else "获取进度失败"
    except:
        return "获取进度失败"

def strip_code_fence(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

def fallback_daily_summary(diary_text):
    """降级方案：在 AI 不可用时，至少生成可读的日总结。"""
    lines = diary_text.split("\n")
    sections = {"学习": [], "育儿": [], "情绪": [], "其他要点": []}
    current = "其他要点"

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if "###" in line:
            if "学习" in line:
                current = "学习"
            elif "育儿" in line:
                current = "育儿"
            elif any(key in line for key in ("情绪", "心情", "状态", "健康", "运动")):
                current = "情绪"
            else:
                current = "其他要点"
            continue
        if line.startswith("-"):
            sections[current].append(line)

    def render_items(name):
        items = sections[name][:4]
        return "\n".join(items) if items else "- 无明显记录"

    return "\n".join(
        [
            "## 📝 今日回顾",
            "",
            "### 学习",
            render_items("学习"),
            "",
            "### 育儿",
            render_items("育儿"),
            "",
            "### 情绪",
            render_items("情绪"),
            "",
            "### 其他要点",
            render_items("其他要点"),
            "",
            "---",
            "",
            "## 💡 AI 建议",
            "1. AI 生成失败，先手动补充今天最关键的复盘结论。",
            "2. 结合目标进度，明确明天只能优先推进的一件事。",
            "3. 回看日记里最强烈的情绪触发点，避免明天重复。",
            "",
            "---",
            "",
            "## ❓ 今日反思（请回答）",
            "1. 今天最值得保留的做法是什么？",
            "2. 今天哪个情绪或事件最影响执行？",
            "3. 明天最重要的一件事是什么？",
        ]
    )

def generate_daily_summary(date, diary_text, progress):
    system_prompt = load_daily_prompt()
    user_prompt = f"""【日期】{date}

【输入数据：当天日记原文】
{diary_text}

【目标进度】
{progress}
"""
    try:
        result = subprocess.run(
            [*AI_COMMAND, "--system", system_prompt],
            input=user_prompt,
            capture_output=True,
            text=True,
            timeout=180,
        )
        if result.returncode == 0 and result.stdout.strip():
            return strip_code_fence(result.stdout)
        error = result.stderr.strip() or result.stdout.strip() or "未知错误"
        print(f"⚠️ AI 日总结生成失败，改用降级方案: {error}")
    except FileNotFoundError:
        print(f"⚠️ 未找到 AI 命令: {' '.join(AI_COMMAND)}，改用降级方案")
    except Exception as exc:
        print(f"⚠️ AI 日总结生成异常，改用降级方案: {exc}")
    return fallback_daily_summary(diary_text)

def daily(date):
    diary = DIARY_DIR / f"{date}.md"
    if not diary.exists():
        print(f"❌ {date} 日记不存在")
        return

    diary_text = diary.read_text(encoding="utf-8")
    tpl = TEMPLATE_DAILY.read_text(encoding="utf-8")
    progress = get_progress()
    ai_summary = generate_daily_summary(date, diary_text, progress)

    out = tpl.replace("{{date}}", date)
    out = out.replace("{{datetime}}", datetime.now(tzh).strftime("%Y-%m-%d %H:%M CST"))
    out = out.replace("{{progress}}", progress)
    out = out.replace("{{ai_summary}}", ai_summary)

    DAILY_DIR.mkdir(parents=True, exist_ok=True)
    dest = DAILY_DIR / f"{date}.md"
    dest.write_text(out, encoding="utf-8")
    print(f"✅ {date} 日总结已生成: {dest}")

def today_str():
    return datetime.now(tzh).strftime("%Y-%m-%d")

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: summarize.py daily|weekly|monthly")
    elif sys.argv[1] == "daily":
        daily(today_str())
