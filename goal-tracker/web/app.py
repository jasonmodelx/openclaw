#!/usr/bin/env python3
"""
Goal Tracker Web Service - 目标打卡 Web 表单
"""

import os
import sys
import json
import csv
import secrets
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, request, render_template_string, jsonify

SCRIPT_DIR = Path(__file__).parent
GOAL_TRACKER_SCRIPT = SCRIPT_DIR.parent / "scripts" / "goal_tracker.py"
GOALS_CSV = Path("/root/.openclaw/workspace/notebooklm-library/个人管理/目标库.csv")
TOKENS_FILE = SCRIPT_DIR / "tokens.json"

CST = timezone(timedelta(hours=8))

app = Flask(__name__)

# Load or create tokens
def load_tokens():
    if TOKENS_FILE.exists():
        with open(TOKENS_FILE) as f:
            return json.load(f)
    return {}

def save_tokens(tokens):
    with open(TOKENS_FILE, 'w') as f:
        json.dump(tokens, f, indent=2)

def create_token(user="Jason"):
    """Create a new checkin token"""
    tokens = load_tokens()
    token = secrets.token_urlsafe(16)
    tokens[token] = {
        "user": user,
        "created": datetime.now(CST).isoformat(),
        "used": False
    }
    save_tokens(tokens)
    return token

def validate_token(token, mark_used=False):
    """Validate token and optionally mark as used"""
    tokens = load_tokens()
    if token in tokens and not tokens[token].get("used"):
        if mark_used:
            tokens[token]["used"] = True
            tokens[token]["used_at"] = datetime.now(CST).isoformat()
            save_tokens(tokens)
        return tokens[token]["user"]
    return None

def load_goals():
    """Load goals from CSV, grouped by person"""
    from collections import defaultdict
    goals_by_person = defaultdict(list)
    
    with open(GOALS_CSV, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            goals_by_person[row["person"]].append({
                "id": row["id"],
                "name": row["item"],
                "unit": row["unit"],
                "category": row["category"],
                "person": row["person"],
                "type": row.get("type", ""),
            })
    
    return dict(goals_by_person)

def parse_natural_input(text, goals):
    """Parse natural language input like '跑步5公里 俯卧撑30个'"""
    # Simple keyword matching
    results = []
    text = text.lower()
    
    for goal in goals:
        name_lower = goal["name"].lower()
        # Try to find goal name in text
        if name_lower in text:
            # Extract number near the goal name
            import re
            # Find all numbers in text
            numbers = re.findall(r'\d+\.?\d*', text)
            if numbers:
                # Use first number found (simple heuristic)
                results.append({
                    "goal": goal["name"],
                    "goal_id": goal["id"],
                    "value": float(numbers[0])
                })
    
    return results

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>目标打卡</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .container {
            background: white;
            border-radius: 16px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            max-width: 500px;
            width: 100%;
            padding: 32px;
            max-height: 90vh;
            overflow-y: auto;
        }
        h1 {
            font-size: 28px;
            color: #333;
            margin-bottom: 16px;
            text-align: center;
        }
        .date-picker {
            margin-bottom: 16px;
        }
        .date-picker input {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            text-align: center;
        }
        .person-tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 24px;
            background: #f5f5f5;
            padding: 4px;
            border-radius: 8px;
        }
        .person-tab {
            flex: 1;
            padding: 10px;
            border: none;
            background: transparent;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.2s;
        }
        .person-tab.active {
            background: white;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            color: #667eea;
            font-weight: 600;
        }
        .person-content { display: none; }
        .person-content.active { display: block; }
        
        .goal-item {
            background: #f9f9f9;
            padding: 16px;
            border-radius: 8px;
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .goal-item.checked {
            opacity: 0.5;
            display: none;
        }
        .goal-item input[type="checkbox"] {
            width: 20px;
            height: 20px;
            cursor: pointer;
        }
        .goal-info {
            flex: 1;
        }
        .goal-name {
            font-weight: 600;
            color: #333;
            margin-bottom: 4px;
        }
        .goal-category {
            font-size: 12px;
            color: #999;
        }
        .goal-input {
            width: 80px;
            padding: 8px;
            border: 2px solid #e0e0e0;
            border-radius: 6px;
            text-align: center;
            font-size: 16px;
        }
        .goal-input:disabled {
            background: #f5f5f5;
            color: #ccc;
        }
        
        .submit-btn {
            width: 100%;
            padding: 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            margin-top: 24px;
            transition: transform 0.2s, box-shadow 0.2s;
        }
        .submit-btn:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(102, 126, 234, 0.4);
        }
        .submit-btn:active {
            transform: translateY(0);
        }
        .submit-btn:disabled {
            background: #ccc;
            cursor: not-allowed;
            transform: none;
        }
        
        .result {
            display: none;
            text-align: center;
            padding: 32px;
        }
        .result.show { display: block; }
        .result-icon {
            font-size: 64px;
            margin-bottom: 16px;
        }
        .result-text {
            font-size: 20px;
            color: #333;
            margin-bottom: 8px;
        }
        .result-detail {
            color: #666;
            font-size: 14px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div id="form-view">
            <h1>✅ 目标打卡</h1>
            
            <div class="date-picker">
                <input type="date" id="checkin-date" value="{{ today }}">
            </div>
            
            <div class="person-tabs">
                {% for person in goals.keys() %}
                <button class="person-tab {% if loop.first %}active{% endif %}" onclick="switchPerson('{{ person }}')">{{ person }}</button>
                {% endfor %}
            </div>
            
            {% for person, person_goals in goals.items() %}
            <div class="person-content {% if loop.first %}active{% endif %}" data-person="{{ person }}">
                {% for goal in person_goals %}
                <div class="goal-item" data-goal-id="{{ goal.id }}">
                    <input type="checkbox" id="goal-{{ goal.id }}" onchange="toggleInput('{{ goal.id }}', '{{ goal.type }}')">
                    <div class="goal-info">
                        <div class="goal-name">{{ goal.name }}</div>
                        <div class="goal-category">{{ goal.category }}</div>
                    </div>
                    {% if goal.type != 'check' %}
                    <input type="number" 
                           id="input-{{ goal.id }}" 
                           class="goal-input" 
                           placeholder="{{ goal.unit }}"
                           disabled
                           step="0.1">
                    {% endif %}
                </div>
                {% endfor %}
            </div>
            {% endfor %}
            
            <button class="submit-btn" onclick="submitCheckin()">提交打卡</button>
        </div>
        
        <div id="result-view" class="result">
            <div class="result-icon">✅</div>
            <div class="result-text">打卡成功</div>
            <div class="result-detail" id="result-detail"></div>
        </div>
    </div>
    
    <script>
        const goalsData = {{ goals_json | safe }};
        const token = "{{ token }}";
        let currentPerson = Object.keys(goalsData)[0];
        
        // Load checked goals from backend on date change
        document.getElementById('checkin-date').addEventListener('change', async function() {
            const date = this.value;
            await loadCheckedGoals(date);
        });
        
        async function loadCheckedGoals(date) {
            try {
                const response = await fetch(`/checked-goals?date=${date}&person=${currentPerson}`);
                const data = await response.json();
                
                // Hide checked goals
                document.querySelectorAll('.goal-item').forEach(item => {
                    const goalId = item.dataset.goalId;
                    if (data.checked_goals && data.checked_goals.includes(goalId)) {
                        item.classList.add('checked');
                    } else {
                        item.classList.remove('checked');
                    }
                });
            } catch (error) {
                console.error('Failed to load checked goals:', error);
            }
        }
        
        function switchPerson(person) {
            currentPerson = person;
            document.querySelectorAll('.person-tab').forEach(btn => btn.classList.remove('active'));
            document.querySelectorAll('.person-content').forEach(el => el.classList.remove('active'));
            
            event.target.classList.add('active');
            document.querySelector(`.person-content[data-person="${person}"]`).classList.add('active');
            
            // Reload checked goals for new person
            const date = document.getElementById('checkin-date').value;
            loadCheckedGoals(date);
        }
        
        function toggleInput(goalId, goalType) {
            const checkbox = document.getElementById('goal-' + goalId);
            if (goalType !== 'check') {
                const input = document.getElementById('input-' + goalId);
                input.disabled = !checkbox.checked;
                if (checkbox.checked) {
                    input.focus();
                } else {
                    input.value = '';
                }
            }
        }
        
        async function submitCheckin() {
            const date = document.getElementById('checkin-date').value;
            const goals = goalsData[currentPerson];
            let checkins = [];
            
            goals.forEach(goal => {
                const checkbox = document.getElementById('goal-' + goal.id);
                if (checkbox && checkbox.checked) {
                    if (goal.type === 'check') {
                        // check 类型直接记录为 1
                        checkins.push({
                            goal_id: goal.id,
                            goal_name: goal.name,
                            value: 1
                        });
                    } else {
                        const input = document.getElementById('input-' + goal.id);
                        if (input && input.value) {
                            checkins.push({
                                goal_id: goal.id,
                                goal_name: goal.name,
                                value: parseFloat(input.value)
                            });
                        }
                    }
                }
            });
            
            if (checkins.length === 0) {
                alert('请至少选择一个目标并填写数值');
                return;
            }
            
            const btn = document.querySelector('.submit-btn');
            btn.disabled = true;
            btn.textContent = '提交中...';
            
            try {
                const response = await fetch('/submit', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        token: token,
                        person: currentPerson,
                        date: date,
                        checkins: checkins
                    })
                });
                
                const result = await response.json();
                
                if (result.success) {
                    document.getElementById('result-detail').textContent = 
                        `已记录 ${result.count} 项打卡`;
                    document.getElementById('form-view').style.display = 'none';
                    document.getElementById('result-view').classList.add('show');
                    
                    setTimeout(() => {
                        window.close();
                    }, 2000);
                } else {
                    alert('提交失败: ' + result.error);
                    btn.disabled = false;
                    btn.textContent = '提交打卡';
                }
            } catch (error) {
                alert('网络错误: ' + error.message);
                btn.disabled = false;
                btn.textContent = '提交打卡';
            }
        }
        
        // Load initial checked goals
        window.addEventListener('load', function() {
            const date = document.getElementById('checkin-date').value;
            loadCheckedGoals(date);
        });
    </script>
</body>
</html>
"""

@app.route('/checkin/<token>')
def checkin_form(token):
    """Display checkin form"""
    user = validate_token(token)
    if not user:
        return "Invalid or expired token", 403
    
    goals = load_goals()  # Now returns dict grouped by person
    today = datetime.now(CST).strftime('%Y-%m-%d')
    
    return render_template_string(
        HTML_TEMPLATE,
        goals=goals,
        goals_json=json.dumps(goals, ensure_ascii=False),
        token=token,
        today=today
    )


@app.route('/checked-goals')
def get_checked_goals():
    """Get list of checked goal IDs for a specific date and person"""
    date = request.args.get('date')
    person = request.args.get('person', '陈坚')
    
    if not date:
        return jsonify({"checked_goals": []})
    
    # Read checkin CSV to find what's already checked
    checked_goal_ids = []
    checkin_csv = Path("/root/.openclaw/workspace/notebooklm-library/个人管理/打卡记录.csv")
    
    if checkin_csv.exists():
        with open(checkin_csv, encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('日期') == date and row.get('姓名') == person:
                    checked_goal_ids.append(row.get('目标ID'))
    
    return jsonify({"checked_goals": checked_goal_ids})

@app.route('/submit', methods=['POST'])
def submit_checkin():
    """Handle checkin submission"""
    data = request.json
    token = data.get('token')
    
    user = validate_token(token, mark_used=True)  # Mark as used on submit
    if not user:
        return jsonify({"success": False, "error": "Invalid token"}), 403
    
    person = data.get('person', '陈坚')
    date = data.get('date')  # Get selected date
    checkins = data.get('checkins', [])
    
    if not checkins:
        return jsonify({"success": False, "error": "No checkins found"})
    
    # Execute checkins with date parameter
    success_count = 0
    for item in checkins:
        goal_name = item.get('goal_name') or item.get('goal')
        value = item.get('value')
        
        # Build command with optional date
        cmd = ['python3', str(GOAL_TRACKER_SCRIPT), 'checkin', person, goal_name, str(value)]
        if date:
            cmd.extend(['--date', date])
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        
        if result.returncode == 0:
            success_count += 1
    
    return jsonify({
        "success": True,
        "count": success_count
    })

@app.route('/create-token')
def create_token_endpoint():
    """Create a new checkin token (for testing)"""
    token = create_token()
    base_url = request.host_url.rstrip('/')
    return jsonify({
        "token": token,
        "url": f"{base_url}/checkin/{token}"
    })

if __name__ == '__main__':
    # Create tokens directory
    TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=False)
