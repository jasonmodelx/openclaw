#!/bin/bash
# 检查 goal-tracker web server 是否在运行，没有则启动

APP_DIR="/root/.openclaw/workspace-life/skills/goal-tracker/web"
LOG_FILE="/tmp/goal-tracker-web.log"
PID_FILE="/tmp/goal-tracker-web.pid"

# 检查是否在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        exit 0  # 已在运行
    fi
fi

# 启动服务
cd "$APP_DIR"
nohup python3 app.py >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
echo "[$(date)] goal-tracker web server started (PID: $!)" >> "$LOG_FILE"
