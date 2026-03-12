---
name: podcast
description: 播客 RSS 监控 + NotebookLM 生成
user-invocable: true
metadata:
  version: 1.1.0
---

# Podcast Skill

定时监控播客频道，获取最新单集，调用 NotebookLM 生成中文报告，并通过邮件通知。

## 功能

- 播客频道管理（通过 Apple Podcasts ID 配置）
- 每4小时自动获取最新单集
- 调用 NotebookLM 创建/复用频道 notebook
- 支持生成多种 Artifact：播客音频、报告、测验、幻灯片、视频、思维导图
- 并发处理多个播客频道
- 支持单 URL 独立处理模式
- 生成中文报告并下载为 Markdown
- 邮件通知

## 前置条件

**1. 安装依赖**
```bash
pip install notebooklm-py requests
```

**2. 登录 NotebookLM**
```bash
notebooklm login
```
登录态保存在 `~/.notebooklm/storage_state.json`，session 过期后需重新登录。

## 配置说明（config.json）

| 字段 | 说明 |
|------|------|
| `podcasts[].name` | 播客显示名称，也用作 notebook 名称 |
| `podcasts[].apple_id` | Apple Podcasts 频道 ID（从播客链接中获取） |
| `podcasts[].enabled` | 是否启用该频道 |
| `output_dir` | 报告输出目录，每个频道单独子目录 |
| `artifacts` | 每次生成的目标类型数组，支持: `"report"`, `"audio"`, `"mind_map"`, `"quiz"`, `"slide_deck"`, `"video"` |
| `email.enabled` | 是否开启邮件通知 |
| `email.to` | 收件人邮箱 |
| `email.smtp_*` | SMTP 服务器配置 |

## 管理命令

```bash
# 查看频道列表
python3 scripts/manage.py list

# 添加播客（apple_id 从 Apple Podcasts 链接中获取）
python3 scripts/manage.py add "播客名称" "Apple ID"

# 删除播客
python3 scripts/manage.py remove "播客名称"

# （新功能）单 URL 获取模式
# 给定任意网页或播客 URL，提取指定类型的 Artifact，不依赖 config.json 里的 rss 列表
python3 scripts/fetch_v2.py --url "https://example.com/article" --name "临时抓取" --artifacts "report,mind_map"
```

## 定时任务

```bash
# 每4小时执行（路径替换为 skill 实际所在目录）
0 */4 * * * cd /path/to/skills/podcast && python3 scripts/fetch_v2.py
```

## 输出

- 报告及其他文件保存到: `{output_dir}/{频道名}/{时间戳}_{单集标题}.<扩展名>`
