---
name: youtube-notebooklm
description: |
  YouTube 频道监控 + NotebookLM 自动化制品生成。
  支持定期检查频道、分析单个 YouTube 链接、按需指定 artifact 类型、
  控制每个频道抓取的最新视频数量，并在下载完成后自动删除 notebook。
  当用户提到 YouTube 监控、NotebookLM 自动生成、频道订阅检查时激活。
---

# YouTube → NotebookLM 自动化

## 概述

监控指定 YouTube 频道的新视频，或直接分析单个 YouTube 链接。脚本会把视频 URL 传入 NotebookLM，生成指定制品并下载到本地；默认下载完成后自动删除临时 notebook。

## 前置依赖

- `notebooklm-py` Python API（依赖 `~/.notebooklm/storage_state.json` 登录态）
- Google 认证（`~/.notebooklm/storage_state.json`）

## 配置文件

`scripts/config.json`：

```json
{
  "channels": [
    {
      "name": "频道名称",
      "url": "https://www.youtube.com/@channel_handle",
      "enabled": true
    }
  ],
  "output_dir": "~/.openclaw/workspace/notebooklm-output",
  "artifacts": ["audio", "video", "slide-deck", "mind-map", "quiz", "report", "infographic", "flashcards", "data-table"],
  "email": {
    "enabled": true,
    "to": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_pass": ""
  },
  "check_interval_hours": 4,
  "last_check_file": "scripts/last_check.json"
}
```

## 核心流程

### 1. 检查新视频

```bash
SKILL_DIR=~/.openclaw/workspace-news/skills/youtube-notebooklm

python3 $SKILL_DIR/scripts/check_channels.py
```

脚本逻辑：
1. 读取 `config.json` 中的频道列表
2. 对每个频道抓取最新视频列表
3. 与 `last_check.json` 对比，找出新视频
4. 输出新视频列表（JSON），更新 `last_check.json`

### 2. 创建 Notebook 并生成制品

对每个新视频或单独链接：

```bash
# 单个 YouTube 链接
python3 scripts/check_channels.py \
  --url "https://www.youtube.com/watch?v=VIDEO_ID" \
  --artifacts report,mind-map,slide-deck

# 指定某个频道，分析最新 5 条视频
python3 scripts/check_channels.py \
  --channel "Coin Bureau" \
  --limit 5 \
  --artifacts report
```

脚本行为：
- 每个视频创建独立 notebook
- 只对当前 source 生成指定 artifacts
- 下载完成后默认删除 notebook
- 需要保留 notebook 时显式传 `--keep-notebook`

### 3. 邮件通知

生成完成后，自动调用 `scripts/notify.py` 发送邮件摘要：
- 新视频标题和链接
- 生成的制品列表和本地路径
- 任何失败的制品及错误信息
- report 附件优先转换为 PDF；若转换失败则回退为 Markdown 附件

## 定时触发

通过 OpenClaw cron 或 HEARTBEAT.md 每 4 小时触发：

```
sessions_spawn(
  task = "运行 YouTube-NotebookLM 检查：python3 ~/.openclaw/workspace-news/skills/youtube-notebooklm/scripts/check_channels.py，对每个新视频执行完整的 NotebookLM 制品生成流程，完成后发送邮件通知。",
  mode = "run",
  label = "youtube-notebooklm-check"
)
```

## 命令

### 添加频道
用户说："添加 YouTube 频道 https://www.youtube.com/@xxx"
→ 运行 `python3 scripts/manage.py add "https://www.youtube.com/@xxx"`

### 列出频道
用户说："列出监控的 YouTube 频道"
→ 运行 `python3 scripts/manage.py list`

### 删除频道
用户说："删除 YouTube 频道 xxx"
→ 运行 `python3 scripts/manage.py delete "xxx"`

### 手动检查
用户说："检查 YouTube 更新" / "手动跑一次"
→ spawn 子任务执行完整流程

### 分析单个 YouTube 链接
用户说："分析这个 YouTube 链接并下载报告"
→ 运行 `python3 scripts/check_channels.py --url "<链接>" --artifacts report`

### 指定 artifact 类型
用户说："分析这个视频，只要 report 和 mind-map"
→ 运行 `python3 scripts/check_channels.py --url "<链接>" --artifacts report,mind-map`

### 指定频道最新 N 条
用户说："分析 Coin Bureau 最新 5 条视频"
→ 运行 `python3 scripts/check_channels.py --channel "Coin Bureau" --limit 5`

## 注意事项

- NotebookLM 制品生成需要时间（每个 2-8 分钟），多种 artifacts 会并发生成
- 生成失败不阻塞其他制品，记录错误继续
- `storage_state.json` 权限必须为 600
- YouTube 频道 URL 支持 `@handle`、`/channel/ID`、`/c/name` 格式
- 默认会在下载后删除 notebook；如需保留，使用 `--keep-notebook`
