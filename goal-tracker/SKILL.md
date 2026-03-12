# Goal Tracker Skill

个人目标管理系统，支持 Jason / Alexander / Lawrence 三人的目标设定、打卡、提醒和总结。

## 触发条件
- 来自 Telegram 群 `-5236155467` 的消息
- 定时 cron 任务

## 核心规则

### 消息处理（铁律：先执行后确认）

**绝对禁止**：不执行脚本就说"已记录"/"已打卡"/"已写入"。必须先用 exec 工具调用脚本，看到脚本输出 `✅` 后才能回复用户确认。如果脚本报错，必须如实告知用户"写入失败"并附错误信息。

1. 群里收到消息后，判断内容类型：
   - **以 `/nolog` 开头**：只对话不记录，正常回复但不写入任何文件
   - **打卡类**（"运动30分钟"、"冥想完成"、"阅读1小时"等）：
     - 识别姓名（默认陈坚/Jason，提到嘉嘉/Alexander则对应，提到诺诺/Lawrence则对应）
     - 识别目标名称（匹配目标库中的目标）
     - 识别数值（分钟/完成）
     - **必须执行**：`python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py checkin <姓名> <目标名称或ID> <值>`
     - **必须确认**：看到脚本输出 `✅ 已记录:` 后才回复用户
     - 如果一条消息包含多项打卡，逐个执行脚本，全部成功后统一回复
   - **非打卡类**（情绪、事件、想法等）：
     - 判断分类：健康/育儿/投资/学习/社交/其他
     - **必须执行**：`python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py diary <分类> "<内容>"`
     - **必须确认**：看到脚本输出 `✅ 已写入日记` 后才回复用户

### 提醒（每天 14:00-22:00 每小时）
- 日目标：当日未完成/未记录
- 周目标：周三（含）起未完成
- 月目标：20号（含）起未完成

### 补打卡（通讯工具优先）
- 当用户输入“补打卡”时，不再生成网页链接
- 改为输出待补打卡 Markdown 表格，用户复制后在“备注”列补值，再发回系统解析
- 输出模板命令：
  - `python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py backfill-template [YYYY-MM-DD]`
- 解析补打卡命令：
  - `python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py parse-backfill [YYYY-MM-DD]`
- 用户回传格式要求：
  - 保留 Markdown 表格表头
  - 仅填写“备注”列
  - `check` 类型填 `1`
  - `duration` 类型填数值或带单位文本，系统会自动抽取数字

### 日总结（每天 22:00）
内容：
1. 今日各项目标完成情况（日目标 + 周目标本周累计进度）
2. 今日学习、育儿、情绪等回顾（基于日记内容）
3. AI 建议（3条）
4. 三个反思问题（留给用户回答）

输出：写入 `个人管理/日总结/YYYY-MM-DD.md`，Telegram 群发通知

### 周总结（每周日 22:00）
- 基于当周 7 篇日记 + 打卡数据
- 含月目标本月累计完成度
- 使用周总结模板

### 月总结（每月最后一天 22:00）
- 基于当月所有日记 + 打卡数据
- 使用月度仪表板模板

## 文件路径
- 目标库: `~/.openclaw/workspace/notebooklm-library/个人管理/目标库.csv`
- 打卡记录: `~/.openclaw/workspace/notebooklm-library/个人管理/打卡记录.csv`
- 日记: `~/.openclaw/workspace/notebooklm-library/个人管理/日记/YYYY-MM-DD.md`
- 日总结: `~/.openclaw/workspace/notebooklm-library/个人管理/日总结/YYYY-MM-DD.md`
- 周总结: `~/.openclaw/workspace/notebooklm-library/个人管理/周总结/YYYY-Www.md`
- 月总结: `~/.openclaw/workspace/notebooklm-library/个人管理/月总结/YYYY-MM.md`
- 工具脚本: `~/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py`

## 脚本用法
```bash
# 打卡
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py checkin Jason 运动 30

# 写日记
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py diary 学习 "学了1小时DeFi策略"

# 检查提醒
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py remind

# 输出补打卡模板
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py backfill-template

# 解析用户回填的补打卡表格
cat backfill.md | python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py parse-backfill

# 查看进度
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py progress daily
python3 /root/.openclaw/workspace-life/skills/goal-tracker/scripts/goal_tracker.py progress weekly
```

## Telegram 群
- 群 ID: `-5236155467`
- 所有个人管理相关记录通过此群进行
