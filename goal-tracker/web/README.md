# Goal Tracker Web Service

Web 打卡表单服务，提供美观易用的目标打卡界面。

## 启动服务

### 方式 1：systemd（推荐，开机自启）

```bash
# 安装服务
sudo cp goal-tracker-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable goal-tracker-web
sudo systemctl start goal-tracker-web

# 查看状态
sudo systemctl status goal-tracker-web

# 查看日志
sudo journalctl -u goal-tracker-web -f
```

### 方式 2：screen（临时运行）

```bash
screen -S goal-web
python3 app.py
# 按 Ctrl+A 然后 D 离开 screen
```

## 使用方法

### 1. 在 Telegram 群里请求打卡链接

发送：`网页打卡` 或 `/打卡`

### 2. 机器人生成链接

机器人会调用 `goal_tracker.py web-token` 生成临时链接（5分钟有效）

### 3. 打开链接填写

两种模式：
- **文字输入**：直接输入"跑步5公里 俯卧撑30个"，系统自动识别
- **表单选择**：勾选目标，填写数值

### 4. 提交

数据自动保存到目标数据库，并同步到日记文件

## 配置

- 端口：默认 8080（可通过环境变量 `PORT` 修改）
- Token 有效期：5 分钟（在 `tokens.json` 中管理）
- 目标库：`/root/.openclaw/workspace/notebooklm-library/个人管理/目标库.csv`

## 安全性

- 每个链接包含随机 token，只能使用一次
- Token 5 分钟后自动失效
- 建议配合防火墙限制访问 IP

## 故障排查

### 服务无法启动

```bash
# 检查端口占用
sudo netstat -tlnp | grep 8080

# 手动运行查看错误
python3 /root/.openclaw/workspace-life/skills/goal-tracker/web/app.py
```

### 无法访问

检查防火墙：
```bash
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```
