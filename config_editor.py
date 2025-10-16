# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot.
# WeChatBot is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
# 
# WeChatBot is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
# 
# You should have received a copy of the GNU General Public License
# along with WeChatBot.  If not, see <http://www.gnu.org/licenses/>.
# ***********************************************************************

from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response, send_file, abort, stream_with_context
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect, generate_csrf, CSRFError
from waitress import serve
import re
import ast
import os
import subprocess
import psutil
import openai
import tempfile
import shutil
from filelock import FileLock
from functools import wraps
import webbrowser
from threading import Timer
import logging
from queue import Queue, Empty
import time
import json
from werkzeug.utils import secure_filename
import uuid
import base64
import mimetypes
from datetime import datetime
import zipfile
import requests

app = Flask(__name__)

# 确保 HTML 响应默认使用 UTF-8 编码（兼容部分浏览器/工具对 charset 的要求）
@app.after_request
def ensure_utf8_charset(response):
    try:
        content_type = response.headers.get('Content-Type', '')
        if content_type:
            lower = content_type.lower()
            if 'text/html' in lower and 'charset=' not in lower:
                response.headers['Content-Type'] = 'text/html; charset=utf-8'
    except Exception:
        # 安全兜底：不影响原始响应
        pass
    return response

# 上传文件安全配置
MAX_UPLOAD_SIZE = 100 * 1024 * 1024  # 100MB (单个文件限制)
ALLOWED_CONFIG_EXTENSIONS = {'.py'}
ALLOWED_IMPORT_EXTENSIONS = {'.py', '.json', '.md', '.txt', '.png', '.jpg', '.jpeg', '.gif', '.webp'}
MAX_FILES_PER_UPLOAD = 2000  # 最多文件数

# ===== 统一的论坛数据目录 =====
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FORUM_DATA_DIR = os.path.join(BASE_DIR, 'forum_data')
FORUM_AVATAR_DIR = os.path.join(FORUM_DATA_DIR, 'avatar')

def validate_file_size(file_storage):
    """验证上传文件大小"""
    file_storage.seek(0, os.SEEK_END)
    file_size = file_storage.tell()
    file_storage.seek(0)
    
    if file_size > MAX_UPLOAD_SIZE:
        raise ValueError(f"文件大小超过限制 ({MAX_UPLOAD_SIZE / 1024 / 1024}MB)")
    return True

def validate_file_extension(filename, allowed_extensions):
    """验证文件扩展名"""
    ext = os.path.splitext(filename.lower())[1]
    if ext not in allowed_extensions:
        raise ValueError(f"不允许的文件类型: {ext}. 允许的类型: {', '.join(allowed_extensions)}")
    return True

def safe_path_join(base_dir, user_path):
    """
    安全的路径拼接，防止路径遍历攻击
    
    Args:
        base_dir: 基础目录（必须是绝对路径）
        user_path: 用户提供的相对路径
    
    Returns:
        安全的完整路径
    
    Raises:
        ValueError: 如果检测到路径遍历攻击
    """
    # 规范化基础目录
    base_dir = os.path.abspath(base_dir)
    
    # 清理用户路径中的危险字符
    user_path = user_path.replace('\\', '/').strip()
    
    # 移除路径开头的斜杠和点
    while user_path.startswith(('/', './')):
        user_path = user_path.lstrip('/.').lstrip()
    
    # 拆分路径并过滤危险部分
    path_parts = []
    for part in user_path.split('/'):
        part = part.strip()
        if not part or part == '.':
            continue
        if part == '..':
            raise ValueError(f"检测到路径遍历攻击尝试: {user_path}")
        # 只允许安全的文件名字符
        if not re.match(r'^[\w\-\. \u4e00-\u9fff]+$', part):
            raise ValueError(f"文件名包含非法字符: {part}")
        path_parts.append(part)
    
    # 拼接路径
    if not path_parts:
        raise ValueError("无效的文件路径")
    
    full_path = os.path.join(base_dir, *path_parts)
    full_path = os.path.abspath(full_path)
    
    # 确保最终路径仍在基础目录内
    if not full_path.startswith(base_dir + os.sep):
        raise ValueError(f"路径遍历攻击被阻止: {user_path}")
    
    return full_path

# ===== 输入验证函数（安全修复：漏洞4） =====
def validate_username(username):
    """
    验证用户名/用户ID的合法性
    
    Args:
        username: 用户提供的用户名
    
    Returns:
        清理后的用户名
    
    Raises:
        ValueError: 如果用户名不合法
    """
    if not username:
        raise ValueError("用户名不能为空")
    
    # 转换为字符串并去除首尾空白
    username = str(username).strip()
    
    # 长度验证
    if len(username) < 1:
        raise ValueError("用户名不能为空")
    if len(username) > 100:
        raise ValueError("用户名长度不能超过100字符")
    
    # 字符验证：只允许字母、数字、中文、下划线、短横线、点、空格
    if not re.match(r'^[\w\u4e00-\u9fff\-\.\s]+$', username):
        raise ValueError("用户名包含非法字符")
    
    # 不允许特殊模式
    dangerous_patterns = [
        r'\.\.',  # 路径遍历
        r'<script',  # XSS
        r'javascript:',  # XSS
        r'on\w+=',  # 事件处理器
        r'<!--',  # 注释注入
    ]
    
    for pattern in dangerous_patterns:
        if re.search(pattern, username, re.IGNORECASE):
            raise ValueError("用户名包含危险字符")
    
    return username

def validate_path(path_str, allow_absolute=False):
    """
    验证路径字符串的安全性
    
    Args:
        path_str: 路径字符串
        allow_absolute: 是否允许绝对路径
    
    Returns:
        清理后的路径
    
    Raises:
        ValueError: 如果路径不安全
    """
    if not path_str:
        raise ValueError("路径不能为空")
    
    path_str = str(path_str).strip()
    
    # 长度验证
    if len(path_str) > 500:
        raise ValueError("路径长度不能超过500字符")
    
    # 检测路径遍历
    if '..' in path_str:
        raise ValueError("路径包含非法的'..'字符")
    
    # 如果不允许绝对路径
    if not allow_absolute:
        if path_str.startswith('/') or (len(path_str) > 1 and path_str[1] == ':'):
            raise ValueError("不允许使用绝对路径")
    
    # 检测危险字符
    dangerous_chars = ['<', '>', '|', '\0', '\n', '\r']
    for char in dangerous_chars:
        if char in path_str:
            raise ValueError(f"路径包含非法字符: {repr(char)}")
    
    # Windows特定检查
    if os.name == 'nt':
        # 不允许Windows设备名
        device_names = ['CON', 'PRN', 'AUX', 'NUL', 'COM1', 'COM2', 'COM3', 'COM4',
                       'LPT1', 'LPT2', 'LPT3']
        path_upper = path_str.upper()
        for device in device_names:
            if device in path_upper:
                raise ValueError(f"路径包含Windows保留设备名: {device}")
    
    return path_str

def _ensure_forum_dir_exists():
    try:
        os.makedirs(FORUM_DATA_DIR, exist_ok=True)
        os.makedirs(FORUM_AVATAR_DIR, exist_ok=True)
    except Exception as e:
        try:
            app.logger.error(f"创建论坛数据目录失败: {e}")
        except Exception:
            pass


def _npc_config_file_path():
    _ensure_forum_dir_exists()
    return os.path.join(FORUM_DATA_DIR, 'npc_config.json')

# ===== 头像文件处理函数 =====
def get_prompt_filename_by_character(character_name):
    """根据角色名获取对应的prompt文件名"""
    # 在论坛系统中，character_name就是prompt文件名
    # 因为LISTEN_LIST的结构是[微信名, prompt文件名]，
    # 而character_name就是从LISTEN_LIST[1]（即prompt文件名）获取的
    return character_name

def get_avatar_filename(character_name, file_extension):
    """生成头像文件名"""
    prompt_filename = get_prompt_filename_by_character(character_name)
    safe_filename = secure_filename(f"{prompt_filename}_avatar")
    return f"{safe_filename}.{file_extension}"

def allowed_avatar_file(filename):
    """检查文件是否为允许的图片格式"""
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_avatar_file_path(character_name):
    """获取头像文件路径"""
    _ensure_forum_dir_exists()
    prompt_filename = get_prompt_filename_by_character(character_name)
    safe_filename = secure_filename(f"{prompt_filename}_avatar")
    
    # 查找现有的头像文件
    for ext in ['png', 'jpg', 'jpeg', 'gif', 'webp']:
        filepath = os.path.join(FORUM_AVATAR_DIR, f"{safe_filename}.{ext}")
        if os.path.exists(filepath):
            return filepath
    return None

def hide_api_key(api_key):
    """
    隐藏API Key，只显示前4位和后4位，中间用*替代
    """
    if not api_key or len(api_key) <= 8:
        return api_key  # 太短的key不处理
    
    # 显示前4位和后4位，中间用*替代
    return api_key[:4] + '*' * max(4, len(api_key) - 8) + api_key[-4:]

def is_hidden_api_key(api_key):
    """
    检查API Key是否是隐藏版本
    """
    return api_key and '*' in api_key

def safe_type_convert(value, target_type, default_value=None, field_name=""):
    """
    安全的类型转换函数，防止整数转换为字符串
    
    Args:
        value: 要转换的值
        target_type: 目标类型 (int, float, bool)
        default_value: 转换失败时的默认值
        field_name: 字段名，用于日志记录
    
    Returns:
        转换后的值或默认值
    """
    try:
        str_value = str(value).strip()
        
        if target_type == int:
            if str_value and str_value.isdigit():
                return int(str_value)
            elif str_value == '':
                return 0 if default_value is None else default_value
            else:
                if field_name:
                    app.logger.warning(f"配置项 {field_name} 的值 '{value}' 包含非数字字符，使用默认值。")
                return default_value if default_value is not None else 0
                
        elif target_type == float:
            if str_value:
                import re
                if re.match(r'^-?\d+(\.\d+)?$', str_value):
                    return float(str_value)
                else:
                    if field_name:
                        app.logger.warning(f"配置项 {field_name} 的值 '{value}' 不是有效的数字格式，使用默认值。")
                    return default_value if default_value is not None else 0.0
            else:
                return 0.0 if default_value is None else default_value
                
        elif target_type == bool:
            return str_value.lower() in ('on', 'true', '1', 'yes')
            
    except (ValueError, TypeError) as e:
        if field_name:
            app.logger.warning(f"配置项 {field_name} 类型转换失败: {e}，使用默认值。")
        return default_value if default_value is not None else (0 if target_type == int else 0.0 if target_type == float else False)
    
    return value

def validate_config_types(config_path):
    """
    验证config.py中的数据类型是否正确
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否有字符串形式的数字
        import re
        
        # 查找可能的问题配置项
        issues = []
        
        # 检查应该是整数但被保存为字符串的配置项
        int_fields = ['MAX_GROUPS', 'MAX_TOKEN', 'QUEUE_WAITING_TIME', 'EMOJI_SENDING_PROBABILITY', 
                     'MAX_MESSAGE_LOG_ENTRIES', 'MAX_MEMORY_NUMBER', 'PORT', 'ONLINE_API_MAX_TOKEN',
                     'REQUESTS_TIMEOUT', 'MAX_WEB_CONTENT_LENGTH', 'RESTART_INACTIVITY_MINUTES',
                     'GROUP_CHAT_RESPONSE_PROBABILITY', 'ASSISTANT_MAX_TOKEN']
        
        # 检查应该是浮点数但被保存为字符串的配置项  
        float_fields = ['TEMPERATURE', 'MOONSHOT_TEMPERATURE', 'MIN_COUNTDOWN_HOURS', 'MAX_COUNTDOWN_HOURS',
                       'AVERAGE_TYPING_SPEED', 'RANDOM_TYPING_SPEED_MIN', 'RANDOM_TYPING_SPEED_MAX',
                       'ONLINE_API_TEMPERATURE', 'RESTART_INTERVAL_HOURS', 'ASSISTANT_TEMPERATURE']
        
        for field in int_fields:
            pattern = rf'{field}\s*=\s*[\'"](\d+)[\'"]'
            matches = re.findall(pattern, content)
            if matches:
                issues.append(f"{field} 被保存为字符串 '{matches[0]}'，应为整数 {matches[0]}")
        
        for field in float_fields:
            pattern = rf'{field}\s*=\s*[\'"](\d+\.?\d*)[\'"]'
            matches = re.findall(pattern, content)
            if matches:
                issues.append(f"{field} 被保存为字符串 '{matches[0]}'，应为浮点数 {matches[0]}")
        
        if issues:
            app.logger.warning(f"配置文件类型验证发现问题: {'; '.join(issues)}")
            return False
        
        return True
        
    except Exception as e:
        app.logger.error(f"配置文件类型验证失败: {e}")
        return False

# 会话密钥配置（每次重启生成新密钥，适合个人使用）
app.secret_key = os.urandom(24).hex()
bot_process = None

# 简易500错误处理，便于快速定位问题
@app.errorhandler(500)
def handle_internal_error(e):
    try:
        app.logger.error(f"[500] Internal Server Error: {e}")
    except Exception:
        pass
    return "Internal Server Error", 500

# ===== CSRF保护配置 =====
csrf = CSRFProtect(app)

# CSRF豁免端点列表（用于API调用，需要其他方式验证）
CSRF_EXEMPT_ENDPOINTS = [
    'bot_heartbeat',  # bot.py发送心跳，使用IP验证
    'receive_bot_log',  # bot.py发送日志，使用内容验证
]

@app.errorhandler(CSRFError)
def handle_csrf_error(e):
    """处理CSRF验证失败"""
    app.logger.warning(f"CSRF验证失败: {request.remote_addr} - {request.endpoint}")
    return jsonify({'error': 'CSRF验证失败，请刷新页面重试'}), 400

# ===== 速率限制配置 =====
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://",
    strategy="fixed-window"
)

# 全局日志队列
log_queue = Queue()

# 基于IP的登录尝试跟踪（安全修复）
from datetime import datetime, timedelta
from collections import defaultdict

login_attempts = defaultdict(list)  # {ip: [timestamp1, timestamp2, ...]}
LOCKOUT_DURATION = timedelta(minutes=30)
MAX_LOGIN_ATTEMPTS = 5

def is_ip_locked(ip):
    """检查IP是否因多次失败登录而被锁定"""
    if ip not in login_attempts:
        return False
    
    # 清理过期的尝试记录
    cutoff_time = datetime.now() - LOCKOUT_DURATION
    login_attempts[ip] = [t for t in login_attempts[ip] if t > cutoff_time]
    
    return len(login_attempts[ip]) >= MAX_LOGIN_ATTEMPTS

def record_failed_login(ip):
    """记录失败的登录尝试"""
    login_attempts[ip].append(datetime.now())
    app.logger.warning(f"登录失败: IP {ip}, 当前尝试次数: {len(login_attempts[ip])}")

def clear_login_attempts(ip):
    """清除IP的登录尝试记录"""
    if ip in login_attempts:
        login_attempts[ip] = []

CHAT_CONTEXTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_contexts.json')
CHAT_CONTEXTS_LOCK_FILE = CHAT_CONTEXTS_FILE + '.lock'

last_heartbeat_time = 0  # 上次收到心跳的时间戳
HEARTBEAT_TIMEOUT = 15   # 心跳超时阈值（秒），应大于 bot.py 的 HEARTBEAT_INTERVAL
current_bot_pid = None

def get_chat_context_users():
    """从 chat_contexts.json 读取用户列表 (即顶级键)"""
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return []
    try:
        with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return list(data.keys())
    except (json.JSONDecodeError, IOError) as e:
        app.logger.error(f"读取 chat_contexts.json 失败: {e}")
        return []

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute")  # 速率限制：每分钟最多10次登录尝试
def login():
    config = parse_config()
    client_ip = request.remote_addr
    
    password_is_valid = config.get('PASSWORD_IS_VALID', False)
    
    # 未设置密码时，强制跳转到密码设置页面
    if not password_is_valid:
        return redirect(url_for('password_setup', force='true'))
    
    # 检查IP是否被锁定（安全修复）
    if is_ip_locked(client_ip):
        lockout_minutes = LOCKOUT_DURATION.seconds // 60
        app.logger.warning(f"IP {client_ip} 因多次登录失败被锁定")
        return render_template('login.html', 
            error=f"IP地址已被锁定{lockout_minutes}分钟，请稍后再试"), 429
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        stored_pwd = config.get('LOGIN_PASSWORD', '')
        
        if password == stored_pwd:
            session['logged_in'] = True
            clear_login_attempts(client_ip)  # 清除登录失败记录
            app.logger.info(f"用户从 {client_ip} 成功登录")
            return redirect(url_for('index'))
        else:
            record_failed_login(client_ip)  # 记录失败尝试
            remaining = MAX_LOGIN_ATTEMPTS - len(login_attempts[client_ip])
            
            if remaining <= 0:
                app.logger.warning(f"IP {client_ip} 达到最大登录尝试次数")
                return render_template('login.html', 
                    error=f"登录失败次数过多，IP已被锁定{LOCKOUT_DURATION.seconds // 60}分钟"), 429
            else:
                return render_template('login.html', 
                    error=f"密码错误，剩余尝试次数：{remaining}")
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

@app.route('/password_setup', methods=['GET', 'POST'])
@limiter.limit("10 per minute")  # 速率限制：防止暴力破解密码设置
def password_setup():
    config = parse_config()
    password_is_valid = config.get('PASSWORD_IS_VALID', False)
    
    # 获取返回目标参数
    return_to = request.args.get('return', 'login')
    
    # 如果已设置密码：未登录且非强制重置时，先去登录
    if password_is_valid:
        if not session.get('logged_in') and request.args.get('force') != 'true':
            return redirect(url_for('login'))
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        
        # 验证密码复杂度
        if len(password) < 8:
            return render_template('password_setup.html', error="密码长度不能少于8位", return_to=return_to, allow_skip=False)
        
        if not any(c.isupper() for c in password):
            return render_template('password_setup.html', error="密码必须包含大写字母", return_to=return_to, allow_skip=False)
        
        if not any(c.islower() for c in password):
            return render_template('password_setup.html', error="密码必须包含小写字母", return_to=return_to, allow_skip=False)
        
        if not any(c.isdigit() for c in password):
            return render_template('password_setup.html', error="密码必须包含数字", return_to=return_to, allow_skip=False)
        
        if password != confirm_password:
            return render_template('password_setup.html', error="两次输入的密码不一致", return_to=return_to, allow_skip=False)
        
        # 更新配置文件
        try:
            update_config({
                'LOGIN_PASSWORD': password,
                'PASSWORD_IS_VALID': True
            })
            
            # 设置成功后，引导用户去登录
            redirect_url = "/login"
            success_msg = "密码设置成功！请使用新密码登录。"
            return render_template('password_setup.html', success=success_msg, redirect_url=redirect_url, return_to=return_to)
        except Exception as e:
            return render_template('password_setup.html', error=f"密码设置失败：{str(e)}", return_to=return_to, allow_skip=not password_is_valid)
    
    # GET请求：未设置密码时禁止跳过
    allow_skip = False if not password_is_valid else (request.args.get('manual') == 'true')
    return render_template('password_setup.html', allow_skip=allow_skip, return_to=return_to)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = parse_config()
        password_is_valid = config.get('PASSWORD_IS_VALID', False)
        
        # 全局规则：
        # - 若未设置密码，则强制跳转到密码设置页面
        # - 若已设置密码但未登录，则要求先登录
        if not password_is_valid:
            return redirect(url_for('password_setup', force='true'))
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        
        return f(*args, **kwargs)
    return decorated_function

@app.route('/start_bot', methods=['POST'])
@login_required
@limiter.limit("10 per minute")  # 速率限制：防止频繁启动
def start_bot():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        # 如果目录下存在 user_timers.json 则删除
        user_timers_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_timers.json')
        if os.path.exists(user_timers_path):
            try:
                os.remove(user_timers_path)
            except Exception as e:
                app.logger.warning(f"重置主动消息定时器失败: {e}")

        bot_dir = os.path.dirname(os.path.abspath(__file__))
        
        bot_py = os.path.join(bot_dir, 'bot.py')
        bot_exe = os.path.join(bot_dir, 'bot.exe')
        
        if os.path.exists(bot_py):
            cmd = ['python', bot_py]
        elif os.path.exists(bot_exe):
            cmd = [bot_exe]
        else:
            return {'error': 'No bot executable found'}, 404

        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        bot_process = subprocess.Popen(
            cmd,
            creationflags=creation_flags
        )
    return {'status': 'started'}, 200

@app.route('/stop_bot', methods=['POST'])
@login_required
@limiter.limit("10 per minute")  # 速率限制：防止频繁停止
def stop_bot():
    global bot_process, last_heartbeat_time, current_bot_pid
    # 检查状态时，也考虑 current_bot_pid 是否指示有活跃进程
    is_considered_running = False
    if bot_process and bot_process.poll() is None:
        is_considered_running = True
    elif (time.time() - last_heartbeat_time) < HEARTBEAT_TIMEOUT and current_bot_pid is not None:
        try:
            if psutil.pid_exists(current_bot_pid): # 确保PID对应的进程还存在
                 is_considered_running = True
        except Exception: # psutil.pid_exists 可能会抛出异常，例如权限问题
            pass

    if not is_considered_running:
        app.logger.info("尝试停止机器人，但根据进程对象和心跳判断，机器人似乎已停止。")
        # 即使如此，也调用stop_bot_process来清理状态
        stop_bot_process(pid_to_kill=current_bot_pid if current_bot_pid else (bot_process.pid if bot_process else None))
        return {'status': 'stopped'}, 200
    else:
        pid_from_flask_process = bot_process.pid if bot_process else None
        # 优先使用 current_bot_pid，因为它更可能是最新的
        # 如果 current_bot_pid 和 flask 记录的 pid 不同，且 flask 的 pid 进程也存在，都尝试杀掉
        pids_to_attempt_kill = set()
        if current_bot_pid:
            pids_to_attempt_kill.add(current_bot_pid)
        if pid_from_flask_process:
            pids_to_attempt_kill.add(pid_from_flask_process)

        app.logger.info(f"准备停止机器人，目标PID(s): {pids_to_attempt_kill}")
        for pid in pids_to_attempt_kill:
            stop_bot_process(pid_to_kill=pid) # 传入要杀死的PID

        # 最终状态由 stop_bot_process 设置 current_bot_pid 和 last_heartbeat_time
        return {'status': 'stopped'}, 200
    
@app.route('/bot_status')
@limiter.exempt  # 豁免速率限制：前端频繁轮询，只读操作
@login_required
def bot_status():
    global bot_process, last_heartbeat_time, current_bot_pid
    
    process_alive_via_flask_obj = bot_process is not None and bot_process.poll() is None
    heartbeat_is_recent = (time.time() - last_heartbeat_time) < HEARTBEAT_TIMEOUT
    
    # 新增：检查 current_bot_pid 对应的进程是否实际存活
    process_alive_via_current_pid = False
    if current_bot_pid is not None:
        try:
            if psutil.pid_exists(current_bot_pid):
                process_alive_via_current_pid = True 
        except psutil.Error:
            pass

    current_status = "stopped"

    if process_alive_via_flask_obj:
        current_status = "running"
    elif heartbeat_is_recent and process_alive_via_current_pid: # 优先检查通过PID确认的存活
        current_status = "running"
    elif heartbeat_is_recent and not process_alive_via_current_pid and current_bot_pid is not None:
        app.logger.warning(f"Bot status: Heartbeat recent, but PID {current_bot_pid} does not exist. Marking as stopped for now. Last heartbeat: {time.time() - last_heartbeat_time:.1f}s ago")
        current_status = "stopped" # 倾向于保守
    elif heartbeat_is_recent : # 心跳最近，但没有 current_bot_pid 信息 (例如 bot.py 未发送PID)
        current_status = "running" # 保持原逻辑：心跳最近则认为运行

    return {"status": current_status}

@app.route('/proxy/weapis/endpoint', methods=['GET'])
@limiter.limit("20 per minute")  # 速率限制：防止频繁请求
def proxy_weapis_endpoint():
    """代理WeAPIs endpoint请求，避免前端CORS问题"""
    # WeAPIs endpoint地址列表
    endpoints = [
        'https://vg.v1api.cc/endpoint',
        'https://vg.v1chat.cc/endpoint', 
        'https://vg.a3e.top/endpoint',
        'https://vg.littlewheat.com/endpoint'
    ]
    
    # 尝试从每个endpoint获取节点列表
    for endpoint in endpoints:
        try:
            response = requests.get(endpoint, timeout=5)
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data.get('data'), list) and len(data['data']) > 0:
                    app.logger.info(f"成功从 {endpoint} 获取节点列表")
                    return jsonify(data), 200
        except requests.exceptions.Timeout:
            app.logger.warning(f"从 {endpoint} 获取节点列表超时")
            continue
        except requests.exceptions.RequestException as e:
            app.logger.warning(f"从 {endpoint} 获取节点列表失败: {str(e)}")
            continue
        except Exception as e:
            app.logger.warning(f"解析 {endpoint} 响应失败: {str(e)}")
            continue
    
    # 如果所有endpoint都失败，返回备用节点列表
    fallback_nodes = [
        'https://vg.v1api.cc',
        'https://vg.v1chat.cc',
        'https://vg.a3e.top'
    ]
    app.logger.warning('所有endpoint都无法获取节点列表，返回备用节点')
    return jsonify({'data': fallback_nodes}), 200

@app.route('/proxy/weapis/models', methods=['GET'])
@limiter.limit("20 per minute")  # 速率限制：防止频繁请求
def proxy_weapis_models():
    """代理WeAPIs模型列表请求，避免前端CORS问题"""
    try:
        response = requests.get('https://vg.v1api.cc/weapi_models', timeout=5)
        if response.status_code == 200:
            models = response.json()
            app.logger.info(f"成功获取WeAPIs模型列表，共 {len(models)} 个模型")
            return jsonify(models), 200
        else:
            app.logger.warning(f"获取WeAPIs模型列表失败，状态码: {response.status_code}")
            return jsonify({'error': '获取模型列表失败'}), response.status_code
    except requests.exceptions.Timeout:
        app.logger.warning("获取WeAPIs模型列表超时")
        return jsonify({'error': '请求超时'}), 504
    except requests.exceptions.RequestException as e:
        app.logger.warning(f"获取WeAPIs模型列表失败: {str(e)}")
        return jsonify({'error': str(e)}), 500
    except Exception as e:
        app.logger.error(f"处理WeAPIs模型列表请求时出错: {str(e)}")
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/submit_config', methods=['POST'])
@login_required
@limiter.limit("30 per minute")  # 速率限制：防止频繁提交配置
def submit_config():
    global bot_process
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再保存配置'}), 400
    try:
        if not request.form:
            return jsonify({'error': '空的表单提交'}), 400
        
        current_config_before_update = parse_config()
        old_listen_list_map = {item[0]: item[1] for item in current_config_before_update.get('LISTEN_LIST', [])}

        new_values_for_config_py = {}
        
        # 处理API Key字段的特殊逻辑
        api_key_fields = ['DEEPSEEK_API_KEY', 'MOONSHOT_API_KEY', 'ONLINE_API_KEY', 'ASSISTANT_API_KEY', 'FORUM_API_KEY']
        for field in api_key_fields:
            if field in request.form:
                submitted_value = request.form[field].strip()
                if is_hidden_api_key(submitted_value):
                    # 如果提交的是隐藏版本，保持原值不变
                    new_values_for_config_py[field] = current_config_before_update.get(field, '')
                else:
                    # 如果提交的是新值，使用新值
                    new_values_for_config_py[field] = submitted_value

        nicknames_from_form = request.form.getlist('nickname')
        prompt_files_from_form = request.form.getlist('prompt_file')
        
        processed_listen_list = []
        if nicknames_from_form and prompt_files_from_form and len(nicknames_from_form) == len(prompt_files_from_form):
            for nick, pf in zip(nicknames_from_form, prompt_files_from_form):
                nick_stripped = nick.strip()
                pf_stripped = pf.strip()
                if nick_stripped and pf_stripped: 
                    processed_listen_list.append([nick_stripped, pf_stripped])
        new_values_for_config_py['LISTEN_LIST'] = processed_listen_list
        
        new_listen_list_map = {item[0]: item[1] for item in processed_listen_list}
        
        users_whose_prompt_changed = []
        for nickname, new_prompt in new_listen_list_map.items():
            if nickname in old_listen_list_map and old_listen_list_map[nickname] != new_prompt:
                users_whose_prompt_changed.append(nickname)

        # 注意：PASSWORD_IS_VALID 不应在此列表中，它只能通过密码设置页面修改
        boolean_fields = [
            'ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION',
            'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE', 'ENABLE_MEMORY',
            'UPLOAD_MEMORY_TO_AI', 'ALLOW_OPEN_PORT', 'ENABLE_REMINDERS',
            'ALLOW_REMINDERS_IN_QUIET_TIME', 'USE_VOICE_CALL_FOR_REMINDERS',
            'ENABLE_ONLINE_API', 'SEPARATE_ROW_SYMBOLS','ENABLE_SCHEDULED_RESTART',
            'ENABLE_GROUP_AT_REPLY', 'ENABLE_GROUP_KEYWORD_REPLY','GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY', 'REMOVE_PARENTHESES',
            'ENABLE_ASSISTANT_MODEL', 'USE_ASSISTANT_FOR_MEMORY_SUMMARY', 'ENABLE_FORUM_CUSTOM_MODEL',
            'IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE', 'ENABLE_SENSITIVE_CONTENT_CLEARING', 'SAVE_MEMORY_TO_SEPARATE_FILE',
            'ENABLE_TEXT_COMMANDS'
        ]
        for field in boolean_fields:
            new_values_for_config_py[field] = field in request.form

        for key_from_form in request.form:
            if key_from_form in ['nickname', 'prompt_file'] or key_from_form in boolean_fields or key_from_form in api_key_fields:
                continue 

            value_from_form = request.form[key_from_form].strip()
            
            if key_from_form == 'GROUP_KEYWORD_LIST':
                if value_from_form:
                    normalized_value = re.sub(r'，|\s+', ',', value_from_form)
                    keywords_list = [kw.strip() for kw in normalized_value.split(',') if kw.strip()]
                    new_values_for_config_py[key_from_form] = keywords_list
                else:
                    new_values_for_config_py[key_from_form] = []
                continue

            if key_from_form in current_config_before_update:
                original_type_source = current_config_before_update[key_from_form]
                if isinstance(original_type_source, bool):
                    new_values_for_config_py[key_from_form] = (value_from_form.lower() == 'true')
                elif key_from_form in ["MIN_COUNTDOWN_HOURS", "MAX_COUNTDOWN_HOURS", "AVERAGE_TYPING_SPEED", "RANDOM_TYPING_SPEED_MIN", "RANDOM_TYPING_SPEED_MAX", "TEMPERATURE", "MOONSHOT_TEMPERATURE", "ONLINE_API_TEMPERATURE", "ASSISTANT_TEMPERATURE", "RESTART_INTERVAL_HOURS", "FORUM_TEMPERATURE"]: 
                    try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value:
                            # 验证是否为有效的数字格式
                            import re
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                # 如果不是有效数字格式，保留原值
                                new_values_for_config_py[key_from_form] = original_type_source
                                app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values_for_config_py[key_from_form] = 0.0
                    except (ValueError, TypeError) as e: 
                        new_values_for_config_py[key_from_form] = original_type_source 
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, int) or key_from_form in ["GROUP_CHAT_RESPONSE_PROBABILITY", "RESTART_INACTIVITY_MINUTES", "ASSISTANT_MAX_TOKEN", "FORUM_MAX_TOKEN"]:
                    try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 0
                        else:
                            # 如果包含非数字字符，保留原值
                            new_values_for_config_py[key_from_form] = original_type_source
                            app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已保留旧值。")
                    except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, float):
                     try:
                        # 先确保值是字符串类型，然后进行转换
                        str_value = str(value_from_form).strip()
                        if str_value:
                            # 验证是否为有效的数字格式
                            import re
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                # 如果不是有效数字格式，保留原值
                                new_values_for_config_py[key_from_form] = original_type_source
                                app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values_for_config_py[key_from_form] = 0.0
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")
                elif isinstance(original_type_source, list):
                    try:
                        evaluated_list = ast.literal_eval(value_from_form)
                        if isinstance(evaluated_list, list):
                            new_values_for_config_py[key_from_form] = evaluated_list
                        else:
                            new_values_for_config_py[key_from_form] = original_type_source
                            app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 解析后不是列表，已保留旧值。")
                    except:
                        new_values_for_config_py[key_from_form] = original_type_source
                        app.logger.warning(f"配置项 {key_from_form} 的值 '{value_from_form}' 无法解析为列表，已保留旧值。")
                else: 
                    new_values_for_config_py[key_from_form] = value_from_form
            else: 
                if key_from_form == "GROUP_CHAT_RESPONSE_PROBABILITY":
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 0
                        else:
                            new_values_for_config_py[key_from_form] = 100
                            app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已设为默认值100。")
                    except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 100
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已设为默认值100。错误: {e}")
                elif key_from_form == "RESTART_INACTIVITY_MINUTES":
                     try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values_for_config_py[key_from_form] = int(str_value)
                        elif str_value == '':
                            new_values_for_config_py[key_from_form] = 15
                        else:
                            new_values_for_config_py[key_from_form] = 15
                            app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 包含非数字字符，已设为默认值15。")
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 15 
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为整数，已设为默认值15。错误: {e}")

                elif key_from_form == "RESTART_INTERVAL_HOURS":
                     try:
                        str_value = str(value_from_form).strip()
                        if str_value:
                            import re
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values_for_config_py[key_from_form] = float(str_value)
                            else:
                                new_values_for_config_py[key_from_form] = 2.0
                                app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 不是有效的数字格式，已设为默认值2.0。")
                        else:
                            new_values_for_config_py[key_from_form] = 2.0
                     except (ValueError, TypeError) as e:
                        new_values_for_config_py[key_from_form] = 2.0
                        app.logger.warning(f"新配置项 {key_from_form} 的值 '{value_from_form}' 无法转换为浮点数，已设为默认值2.0。错误: {e}")
                else:
                    new_values_for_config_py[key_from_form] = value_from_form
        
        update_config(new_values_for_config_py)
        
        # 验证配置文件类型正确性
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, 'config.py')
        validate_config_types(config_path)

        if users_whose_prompt_changed:
            with FileLock(CHAT_CONTEXTS_LOCK_FILE):
                try:
                    if os.path.exists(CHAT_CONTEXTS_FILE):
                        with open(CHAT_CONTEXTS_FILE, 'r+', encoding='utf-8') as f:
                            chat_data = json.load(f)
                            modified_chat_data = False
                            for user_to_clear in users_whose_prompt_changed:
                                if user_to_clear in chat_data:
                                    del chat_data[user_to_clear]
                                    modified_chat_data = True
                                    app.logger.info(f"因Prompt文件变更，用户 '{user_to_clear}' 的聊天上下文已清除。")
                            if modified_chat_data:
                                f.seek(0)
                                json.dump(chat_data, f, ensure_ascii=False, indent=4)
                                f.truncate()
                except (json.JSONDecodeError, IOError) as e:
                    app.logger.error(f"清除因Prompt变更导致的聊天上下文时出错: {e}")
                    
        return '', 204 
    except Exception as e:
        app.logger.error(f"配置保存失败: {str(e)}")
        return jsonify({'error': f'配置保存失败: {str(e)}'}), 500

def stop_bot_process(pid_to_kill=None):
    global bot_process, last_heartbeat_time, current_bot_pid
    
    process_killed_successfully = False

    if pid_to_kill:
        try:
            if psutil.pid_exists(pid_to_kill):
                bot_psutil = psutil.Process(pid_to_kill)
                app.logger.info(f"尝试终止PID为 {pid_to_kill} 的机器人进程...")
                bot_psutil.terminate()
                bot_psutil.wait(timeout=5) # 等待进程终止
                app.logger.info(f"通过 terminate 成功停止了PID {pid_to_kill}。")
                process_killed_successfully = True
            else:
                app.logger.info(f"PID {pid_to_kill} 指定的进程不存在。")
                process_killed_successfully = True # 认为已停止
        except psutil.NoSuchProcess:
            app.logger.info(f"尝试终止PID {pid_to_kill} 时，进程已不存在。")
            process_killed_successfully = True # 认为已停止
        except psutil.TimeoutExpired: # psutil.TimeoutExpired
            app.logger.warning(f"Terminate PID {pid_to_kill} 超时，尝试 kill。")
            try:
                if psutil.pid_exists(pid_to_kill): # 再次确认存在
                    bot_psutil_kill = psutil.Process(pid_to_kill)
                    bot_psutil_kill.kill()
                    bot_psutil_kill.wait(timeout=3)
                    app.logger.info(f"通过 kill 成功停止了PID {pid_to_kill}。")
                    process_killed_successfully = True
            except psutil.NoSuchProcess:
                 app.logger.info(f"尝试 kill PID {pid_to_kill} 时，进程已不存在。")
                 process_killed_successfully = True
            except Exception as e_kill:
                app.logger.error(f"Kill PID {pid_to_kill} 失败: {e_kill}")
        except Exception as e:
            app.logger.error(f"停止PID {pid_to_kill} 时发生错误: {e}")

    # 如果被杀死的PID是Flask自己启动的进程，则清空bot_process
    if bot_process and pid_to_kill == bot_process.pid and process_killed_successfully:
        app.logger.info(f"清空 Flask 维护的 bot_process 对象 (原PID: {bot_process.pid})。")
        bot_process = None
    
    # 如果被杀死的PID是当前记录的机器人PID，则清空current_bot_pid
    if current_bot_pid and pid_to_kill == current_bot_pid and process_killed_successfully:
        app.logger.info(f"清空 current_bot_pid (原PID: {current_bot_pid})。")
        current_bot_pid = None

    last_heartbeat_time = 0
    if not current_bot_pid and not bot_process: # 确保如果所有已知进程句柄都清了，才彻底标记
        app.logger.info("所有已知的机器人进程句柄均已清理。重置心跳时间。")
    elif current_bot_pid:
        app.logger.warning(f"调用 stop_bot_process 后，current_bot_pid ({current_bot_pid}) 仍有值。可能存在未完全停止的实例或状态不同步。但心跳已重置。")

@app.route('/bot_heartbeat', methods=['POST'])
@csrf.exempt  # CSRF豁免：bot.py的心跳请求，使用其他验证方式
@limiter.limit("120 per minute")  # 速率限制：每分钟最多120次心跳
def bot_heartbeat():
    """接收bot进程心跳（安全修复：添加了基本验证）"""
    global last_heartbeat_time, current_bot_pid
    
    try:
        # 安全检查1：验证Content-Type
        if not request.is_json:
            app.logger.warning(f"心跳请求格式错误: {request.remote_addr}")
            return jsonify({'error': 'Invalid content type'}), 415
        
        # 安全检查2：仅接受本地请求（可选，根据需求调整）
        # 如果bot在不同机器上运行，需要使用更安全的认证方式
        client_ip = request.remote_addr
        if client_ip not in ['127.0.0.1', 'localhost', '::1']:
            # 检查是否已登录（作为备选认证方式）
            config = parse_config()
            if config.get('ALLOW_OPEN_PORT', False):
                if not session.get('logged_in'):
                    app.logger.warning(f"未授权的心跳请求来自: {client_ip}")
                    return jsonify({'error': 'Unauthorized'}), 401
        
        last_heartbeat_time = time.time()
        data = request.get_json()
        
        if data and 'pid' in data:
            received_pid = data.get('pid')
            
            # 安全检查3：验证PID的合理性
            if received_pid and isinstance(received_pid, int) and received_pid > 0:
                # 验证PID是否真实存在（可选）
                try:
                    if psutil.pid_exists(received_pid):
                        if current_bot_pid != received_pid:
                            app.logger.info(f"Bot PID updated via heartbeat: old={current_bot_pid}, new={received_pid}")
                            current_bot_pid = received_pid
                    else:
                        app.logger.warning(f"收到的PID {received_pid} 不存在")
                        return jsonify({'error': 'Invalid PID'}), 400
                except Exception as pid_check_err:
                    app.logger.error(f"PID验证失败: {pid_check_err}")
            else:
                app.logger.warning(f"Received heartbeat with invalid PID: {received_pid}")
                return jsonify({'error': 'Invalid PID format'}), 400
        else:
            app.logger.debug("Received heartbeat without PID information.")

        return jsonify({'status': 'heartbeat_received'}), 200
    except Exception as e:
        app.logger.error(f"Error processing heartbeat: {e}")
        return jsonify({'error': 'Failed to process heartbeat'}), 500

def parse_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    config = {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('#') or not line:
                    continue
                match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
                if match:
                    var_name = match.group(1)
                    var_value_str = match.group(2)
                    try:
                        var_value = ast.literal_eval(var_value_str)
                        config[var_name] = var_value
                    except:
                        config[var_name] = var_value_str
        return config
    except FileNotFoundError:
        raise Exception(f"配置文件不存在于: {config_path}")

def update_config(new_values):
    """
    更新配置文件内容，确保文件写入安全性和原子性，避免文件被清空或损坏。
    """
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    lock_path = config_path + '.lock'  # 文件锁路径

    # 使用文件锁，确保只有一个进程/线程能操作 config.py
    with FileLock(lock_path):
        try:
            # 读取现有配置文件内容
            with open(config_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()

            new_lines = []
            for line in lines:
                line_stripped = line.strip()
                # 保留注释或空行
                if line_stripped.startswith('#') or not line_stripped:
                    new_lines.append(line)
                    continue

                # 匹配配置项的键值对
                match = re.match(r'^\s*(\w+)\s*=.*', line)
                if match:
                    var_name = match.group(1)
                    # 如果新配置中包含此变量，更新其值
                    if var_name in new_values:
                        value = new_values[var_name]
                        new_line = f"{var_name} = {repr(value)}\n"
                        new_lines.append(new_line)
                    else:
                        # 保留未修改的变量
                        new_lines.append(line)
                else:
                    # 如果行不符合格式，则直接保留
                    new_lines.append(line)

            # 写入临时文件，确保写入成功后再替换原文件
            with tempfile.NamedTemporaryFile('w', delete=False, dir=script_dir, encoding='utf-8') as temp_file:
                temp_file_name = temp_file.name
                temp_file.writelines(new_lines)

            # 替换原配置文件
            shutil.move(temp_file_name, config_path)
        except Exception as e:
            # 捕获并记录异常，以便排查问题
            raise Exception(f"更新配置文件失败: {e}")

@app.route('/quick_start', methods=['GET', 'POST'])
@login_required
def quick_start():
    if request.method == 'POST':
        try:
            config = parse_config()
            new_values = {}

            api_provider = request.form.get('quick_start_api_provider', 'weapis')
            api_key_raw = request.form.get('quick_start_api_key', '').strip()
            
            # 处理API Key，如果是隐藏版本则保持原值
            if is_hidden_api_key(api_key_raw):
                api_key = config.get('DEEPSEEK_API_KEY', '')
            else:
                api_key = api_key_raw

            keys_to_clear_for_non_weapis = [
                'MOONSHOT_API_KEY', 'ONLINE_API_KEY',
                'MOONSHOT_BASE_URL', 'ONLINE_BASE_URL',
                'MOONSHOT_MODEL', 'ONLINE_MODEL'
            ]

            if api_provider == 'weapis':
                if api_key:
                    new_values['DEEPSEEK_API_KEY'] = api_key
                    new_values['MOONSHOT_API_KEY'] = api_key
                    new_values['ONLINE_API_KEY'] = api_key
                new_values['DEEPSEEK_BASE_URL'] = 'https://vg.v1api.cc/v1'
                new_values['MOONSHOT_BASE_URL'] = 'https://vg.v1api.cc/v1'
                new_values['ONLINE_BASE_URL'] = 'https://vg.v1api.cc/v1'
                new_values['MOONSHOT_MODEL'] = 'gpt-4o'
                new_values['ONLINE_MODEL'] = 'net-gpt-4o-mini'
                if not config.get('MODEL','').strip():
                    new_values['MODEL'] = 'deepseek-ai/DeepSeek-V3'
                new_values['ENABLE_ONLINE_API'] = 'ENABLE_ONLINE_API' in request.form
            
            else:
                if api_provider == 'siliconflow':
                    new_values['DEEPSEEK_BASE_URL'] = 'https://api.siliconflow.cn/v1/'
                elif api_provider == 'deepseek_official':
                    new_values['DEEPSEEK_BASE_URL'] = 'https://api.deepseek.com'
                elif api_provider == 'other':
                    custom_base_url = request.form.get('quick_start_custom_base_url', '').strip()
                    if custom_base_url:
                        new_values['DEEPSEEK_BASE_URL'] = custom_base_url
                    else:
                        new_values['DEEPSEEK_BASE_URL'] = ""
                
                if api_key:
                    new_values['DEEPSEEK_API_KEY'] = api_key
                
                for key_to_clear in keys_to_clear_for_non_weapis:
                    new_values[key_to_clear] = "" 
                new_values['ENABLE_ONLINE_API'] = False

            nicknames = request.form.getlist('nickname')
            prompt_files_form = request.form.getlist('prompt_file')
            new_values['LISTEN_LIST'] = [
                [nick.strip(), pf.strip()]
                for nick, pf in zip(nicknames, prompt_files_form)
                if nick.strip() and pf.strip()
            ]
            new_values['ENABLE_AUTO_MESSAGE'] = 'ENABLE_AUTO_MESSAGE' in request.form
            
            update_config(new_values)
            return redirect(url_for('index'))
        except Exception as e:
            app.logger.error(f"快速配置保存错误: {e}")
            return redirect(url_for('quick_start'))

    try:
        config = parse_config()
        prompt_files_dir = 'prompts'
        if not os.path.exists(prompt_files_dir):
            os.makedirs(prompt_files_dir)
        prompt_files_list = [f[:-3] for f in os.listdir(prompt_files_dir) if f.endswith('.md')]
        
        current_api_provider = 'weapis'
        current_custom_base_url = ''
        
        deepseek_url = config.get('DEEPSEEK_BASE_URL', '')
        
        is_weapis_setup = (
            deepseek_url == 'https://vg.v1api.cc/v1' and
            config.get('MOONSHOT_BASE_URL') == 'https://vg.v1api.cc/v1' and
            config.get('ONLINE_BASE_URL') == 'https://vg.v1api.cc/v1'
        )

        if is_weapis_setup:
            current_api_provider = 'weapis'
        elif deepseek_url == 'https://api.siliconflow.cn/v1/':
            current_api_provider = 'siliconflow'
        elif deepseek_url == 'https://api.deepseek.com':
            current_api_provider = 'deepseek_official'
        elif deepseek_url and deepseek_url != 'https://vg.v1api.cc/v1': 
            current_api_provider = 'other'
            current_custom_base_url = deepseek_url

        # 为快速配置页面也隐藏API Key
        display_config = config.copy()
        api_key_fields = ['DEEPSEEK_API_KEY']
        for field in api_key_fields:
            if field in display_config:
                display_config[field] = hide_api_key(display_config[field])

        return render_template('quick_start.html',
                               config=display_config,
                               prompt_files=prompt_files_list,
                               current_api_provider=current_api_provider,
                               current_custom_base_url=current_custom_base_url)
    except Exception as e:
        app.logger.error(f"加载快速配置页面错误: {e}")
        return "加载快速配置页面错误，请检查日志。"

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
    # 在处理 POST 或渲染模板之前检查 API KEY
    current_config_check = parse_config()
    # 检查是否从 quick_start 页面明确跳过
    was_skipped = request.args.get('skipped') == 'true'

    if not current_config_check.get('DEEPSEEK_API_KEY', '').strip():
        # 只有当不是明确跳过，并且是GET请求时，才重定向到 quick_start
        if request.method == 'GET' and not was_skipped:
             return redirect(url_for('quick_start'))

    if request.method == 'POST':
        try:
            config = parse_config()
            new_values = {}

             # 处理二维数组的LISTEN_LIST
            nicknames = request.form.getlist('nickname')
            prompt_files = request.form.getlist('prompt_file')
            new_values['LISTEN_LIST'] = [
                [nick.strip(), pf.strip()] 
                for nick, pf in zip(nicknames, prompt_files) 
                if nick.strip() and pf.strip()
            ]

            # 处理其他字段
            submitted_fields = set(request.form.keys()) - {'listen_list'} # listen_list 已处理
            # 修正: submitted_fields应为 {'nickname', 'prompt_file'}
            submitted_fields = set(request.form.keys()) - {'nickname', 'prompt_file'}

            for var in submitted_fields:
                if var not in config and not var.startswith('temp_'): # 忽略不存在于config中的字段, 但保留temp_字段
                    # 如果是 quick_start_api_key 这样的临时字段，则忽略
                    if var == 'quick_start_api_key':
                        continue
                    # 对于其他未知字段，可以打印警告或跳过
                    app.logger.warning(f"表单中存在未知配置项: {var}, 已忽略。")
                    continue
                
                original_value = config.get(var) # 获取原始值及其类型
                value_from_form = request.form[var].strip()

                if var.startswith('temp_'): # 处理 temp_ 前缀的字段，它们决定最终字段的值
                    final_field_name = var.replace('temp_', '')
                    if final_field_name in config: # 确保最终字段名在配置中存在
                        # 这部分逻辑通常在前端JS处理好，后端直接取最终字段
                        # 但为保险起见，这里也处理下
                        # 假设最终字段已由JS写入隐藏input，如 DEEPSEEK_BASE_URL
                        # 这里仅作示例，实际应依赖js将正确的值填入如DEEPSEEK_BASE_URL的name中
                        pass # 依赖js，后端直接用 DEEPSEEK_BASE_URL 等
                    continue # temp_ 字段本身不直接写入配置

                # 类型转换逻辑
                if isinstance(original_value, bool):
                    new_values[var] = value_from_form.lower() in ('on', 'true', '1', 'yes')
                elif isinstance(original_value, int):
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value and str_value.isdigit():
                            new_values[var] = int(str_value)
                        elif str_value == '':
                            new_values[var] = 0
                        else:
                            new_values[var] = original_value
                            app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 包含非数字字符，已保留旧值。")
                    except (ValueError, TypeError) as e:
                        new_values[var] = original_value # 保留旧值
                        app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 无法转换为整数，已保留旧值。错误: {e}")
                elif isinstance(original_value, float):
                    try:
                        str_value = str(value_from_form).strip()
                        if str_value:
                            import re
                            if re.match(r'^-?\d+(\.\d+)?$', str_value):
                                new_values[var] = float(str_value)
                            else:
                                new_values[var] = original_value
                                app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 不是有效的数字格式，已保留旧值。")
                        else:
                            new_values[var] = 0.0
                    except (ValueError, TypeError) as e:
                        new_values[var] = original_value # 保留旧值
                        app.logger.warning(f"配置项 {var} 的值 '{value_from_form}' 无法转换为浮点数，已保留旧值。错误: {e}")

                elif original_value is None and value_from_form: # 如果原配置中某项不存在 (None), 但表单提交了值
                     # 尝试推断类型或默认为字符串
                    try:
                        new_values[var] = ast.literal_eval(value_from_form)
                    except:
                        new_values[var] = value_from_form
                else: # 默认为字符串
                    new_values[var] = value_from_form
            
            # 再次检查布尔字段，确保未勾选时为 False
            # 注意：PASSWORD_IS_VALID 不应在此列表中，它只能通过密码设置页面修改
            boolean_fields_from_editor = [
                'ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION',
                'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE', 'ENABLE_MEMORY',
                'UPLOAD_MEMORY_TO_AI', 'ALLOW_OPEN_PORT', 'ENABLE_REMINDERS',
                'ALLOW_REMINDERS_IN_QUIET_TIME', 'USE_VOICE_CALL_FOR_REMINDERS',
                'ENABLE_ONLINE_API', 'SEPARATE_ROW_SYMBOLS','ENABLE_SCHEDULED_RESTART',
                'ENABLE_GROUP_AT_REPLY', 'ENABLE_GROUP_KEYWORD_REPLY','GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY','REMOVE_PARENTHESES',
                'ENABLE_ASSISTANT_MODEL', 'USE_ASSISTANT_FOR_MEMORY_SUMMARY',
                'IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE', 'ENABLE_SENSITIVE_CONTENT_CLEARING', 'SAVE_MEMORY_TO_SEPARATE_FILE'
            ]
            for field in boolean_fields_from_editor:
                 # 确保这些字段在表单中存在才处理，否则它们可能来自 quick_start
                if field in request.form or field not in new_values: # 如果在表单中，或尚未设置
                    new_values[field] = field in request.form # 统一处理，在表单中出现即为True

            update_config(new_values)
            
            # 验证配置文件类型正确性
            script_dir = os.path.dirname(os.path.abspath(__file__))
            config_path = os.path.join(script_dir, 'config.py')
            validate_config_types(config_path)
            
            return redirect(url_for('index')) # 保存后重定向到自身以刷新GET请求
        except Exception as e:
            app.logger.error(f"主配置页保存配置错误: {e}")
            # 渲染错误信息，或重定向到GET并带上错误提示
            return f"保存配置失败: {str(e)}"

    # GET 请求
    try:
        prompt_files_dir = 'prompts'
        if not os.path.exists(prompt_files_dir):
            os.makedirs(prompt_files_dir)
        prompt_files = [f[:-3] for f in os.listdir(prompt_files_dir) if f.endswith('.md')]
        config = parse_config() # 重新解析以获取最新配置
        chat_context_users = get_chat_context_users()

        # 创建一个隐藏API Key的配置副本用于显示
        display_config = config.copy()
        api_key_fields = ['DEEPSEEK_API_KEY', 'MOONSHOT_API_KEY', 'ONLINE_API_KEY', 'ASSISTANT_API_KEY', 'FORUM_API_KEY']
        for field in api_key_fields:
            if field in display_config:
                display_config[field] = hide_api_key(display_config[field])

        return render_template('config_editor.html',
                             config=display_config,
                             prompt_files=prompt_files,
                             chat_context_users=chat_context_users)
    except Exception as e:
        app.logger.error(f"加载主配置页面错误: {e}")
        return "加载配置页面错误，请检查日志。"

# 替换secure_filename的汉字过滤逻辑
def safe_filename(filename):
    # 只保留汉字、字母、数字、下划线和点，其他字符替换为_
    filename = re.sub(r'[^\w\u4e00-\u9fff.]', '_', filename)
    # 防止路径穿越
    filename = filename.replace('../', '_').replace('/', '_')
    return filename

@app.route('/edit_prompt/<filename>', methods=['GET', 'POST'])
@login_required
def edit_prompt(filename):
    safe_dir = os.path.abspath('prompts')
    
    # 输入验证
    try:
        filename = validate_path(filename)
    except ValueError as e:
        app.logger.warning(f"无效的文件名: {filename}, 错误: {e}")
        return jsonify({'error': f'无效的文件名: {str(e)}'}), 400
    
    # 从path中移除.md后缀，如果存在的话，因为safe_filename会处理
    if filename.endswith('.md'):
        filename_no_ext = filename[:-3]
    else:
        filename_no_ext = filename
    
    # 使用 safe_filename 处理，并确保.md后缀
    # 注意：前端JS在调用此接口时，filename参数应该是包含.md的
    # 所以这里的safe_filename应该针对传入的filename
    processed_filename = safe_filename(filename) 
    filepath = os.path.join(safe_dir, processed_filename)

    if request.method == 'POST':
        content = request.form.get('content', '')
        new_filename_from_form = request.form.get('filename', '').strip()

        if not new_filename_from_form.endswith('.md'):
            new_filename_from_form += '.md'
        new_filename_safe = safe_filename(new_filename_from_form)
        new_filepath = os.path.join(safe_dir, new_filename_safe)

        try:
            # 如果文件名改变了
            if new_filename_safe != processed_filename:
                if os.path.exists(new_filepath):
                    return "新文件名已存在", 400 # 返回错误状态码
                # 检查旧文件是否存在
                if not os.path.exists(filepath):
                     return "原文件不存在，无法重命名", 404
                os.rename(filepath, new_filepath)
                filepath = new_filepath # 更新filepath为新路径，以便写入内容
            
            # 写入内容
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            # 修改后，不需要重定向到 prompt_list，前端会刷新或处理
            return jsonify({'status': 'success', 'message': 'Prompt已保存'}), 200
        except Exception as e:
            app.logger.error(f"保存Prompt失败: {str(e)}")
            return f"保存失败: {str(e)}", 500

    # GET 请求部分: 返回JSON数据
    try:
        if not os.path.exists(filepath): # 确保文件存在
            return jsonify({'error': '文件不存在'}), 404
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        # 返回JSON，而不是渲染模板
        return jsonify({'filename': processed_filename, 'content': content})
    except FileNotFoundError:
        return jsonify({'error': '文件不存在'}), 404
    except Exception as e:
        app.logger.error(f"读取Prompt失败: {str(e)}")
        return jsonify({'error': f'读取Prompt失败: {str(e)}'}), 500

@app.route('/create_prompt', methods=['GET', 'POST'])
@login_required
def create_prompt():
    if request.method == 'POST':
        filename = request.form.get('filename', '').strip()
        content = request.form.get('content', '')
        
        if not filename:
            return "文件名不能为空", 400 # 返回错误状态码
            
        if not filename.endswith('.md'):
            filename += '.md'
        filename = safe_filename(filename) # 应用安全文件名处理
        
        filepath = os.path.join('prompts', filename)
        if os.path.exists(filepath):
            return "文件已存在", 409 # 409 Conflict 更合适
            
        try:
            if not os.path.exists('prompts'): # 确保目录存在
                os.makedirs('prompts')
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            # 返回成功JSON，而不是重定向
            return jsonify({'status': 'success', 'message': 'Prompt已创建'}), 201 # 201 Created
        except Exception as e:
            app.logger.error(f"创建Prompt失败: {str(e)}")
            return f"创建失败: {str(e)}", 500
    
    return "此端点用于POST创建Prompt，或GET请求已被整合处理。", 405 # Method Not Allowed for GET

@app.route('/delete_prompt/<filename>', methods=['POST'])
@login_required
@limiter.limit("20 per minute")  # 速率限制：防止滥用删除
def delete_prompt(filename):
    # 输入验证
    try:
        filename = validate_path(filename)
    except ValueError as e:
        app.logger.warning(f"无效的文件名: {filename}, 错误: {e}")
        return jsonify({'error': f'无效的文件名: {str(e)}'}), 400
    
    safe_dir = os.path.abspath('prompts')
    filepath = os.path.join(safe_dir, safe_filename(filename))
    
    if os.path.isfile(filepath) and filepath.startswith(safe_dir):
        try:
            os.remove(filepath)
            return '', 204
        except Exception as e:
            return str(e), 500
    return "无效文件", 400

@app.route('/generate_prompt', methods=['POST'])
@login_required
def generate_prompt():
    try:
        # 从config.py获取配置
        from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL
        
        client = openai.OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY
        )
        
        prompt = request.json.get('prompt', '')
        FixedPrompt = (
            "\n请严格按照以下格式生成提示词（仅参考以下格式，将...替换为合适的内容，不要输出其他多余内容）。"
            "\n注意：仅在<# 输出示例>部分需要输出以'\\'进行分隔的短句，且不输出逗号和句号，其它部分应当正常输出。"
            "\n\n# 任务"
            "\n你需要扮演指定角色，根据角色的经历，模仿她的语气进行线上的日常对话。"
            "\n\n# 角色"
            "\n你将扮演...。"
            "\n\n# 外表"
            "\n...。"
            "\n\n# 经历"
            "\n...。"
            "\n\n# 性格"
            "\n...。"
            "\n\n# 输出示例"
            "\n...\...\..."
            "\n...\..."
            "\n\n# 喜好"
            "\n...。\n"
        )  # 固定提示词
        
        config = parse_config()
        temperature = config.get('TEMPERATURE', 0.7)

        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{
            "role": "user",
            "content": prompt + FixedPrompt
            }],
            temperature=temperature,
            max_tokens=5000
        )
        
        reply = completion.choices[0].message.content
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()

        return jsonify({
            'result': reply
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# 获取所有提醒 
@app.route('/get_all_reminders')
@limiter.exempt  # 豁免速率限制：前端频繁轮询，只读操作
@login_required
def get_all_reminders():
    """
    获取 JSON 文件中所有的提醒记录 (包括 recurring 和 one-off)。
    """
    try:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recurring_reminders.json')
        if not os.path.exists(json_path):
            return jsonify([]) # 文件不存在则返回空列表

        with open(json_path, 'r', encoding='utf-8') as f:
            all_reminders = json.load(f)

        # 基本验证，确保返回的是列表
        if not isinstance(all_reminders, list):
             app.logger.warning(f"文件 {json_path} 内容不是有效的JSON列表，将返回空列表。")
             return jsonify([])

        return jsonify(all_reminders) # <--- 返回所有提醒

    except json.JSONDecodeError:
        app.logger.error(f"文件 recurring_reminders.json 格式错误，无法解析。")
        return jsonify([]) # 格式错误也返回空列表
    except Exception as e:
        app.logger.error(f"获取所有提醒失败: {str(e)}")
        return jsonify({'error': f'获取所有提醒失败: {str(e)}'}), 500


# 重命名: 保存所有提醒 (覆盖整个文件)
@app.route('/save_all_reminders', methods=['POST']) # <--- Route Renamed
@login_required
def save_all_reminders():
    """
    接收前端提交的所有提醒列表 (recurring 和 one-off)，
    验证后覆盖写入 recurring_reminders.json 文件。
    """
    try:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'recurring_reminders.json')
        # 获取前端提交的完整提醒列表
        reminders_data = request.get_json()

        # --- 验证前端提交的数据 ---
        if not isinstance(reminders_data, list):
            raise ValueError("无效的数据格式，应为提醒列表")

        validated_reminders = []
        # 定义验证规则
        recurring_required = ['reminder_type', 'user_id', 'time_str', 'content']
        one_off_required = ['reminder_type', 'user_id', 'target_datetime_str', 'content']
        time_pattern = re.compile(r'^([01]\d|2[0-3]):([0-5]\d)$') # HH:MM
        # YYYY-MM-DD HH:MM (允许个位数月/日，但通常前端datetime-local会补零)
        datetime_pattern = re.compile(r'^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01]) ([01]\d|2[0-3]):([0-5]\d)$')

        for idx, item in enumerate(reminders_data, 1):
            if not isinstance(item, dict):
                 raise ValueError(f"第{idx}条记录不是有效的对象")

            reminder_type = item.get('reminder_type')
            user_id = str(item.get('user_id', '')).strip()
            content = str(item.get('content', '')).strip()

            # 通用验证
            if not reminder_type in ['recurring', 'one-off']:
                 raise ValueError(f"第{idx}条记录类型无效: {reminder_type}")
            if not user_id: raise ValueError(f"第{idx}条用户ID不能为空")
            if len(user_id) > 50: raise ValueError(f"第{idx}条用户ID过长（最大50字符）")
            if not content: raise ValueError(f"第{idx}条内容不能为空")
            if len(content) > 200: raise ValueError(f"第{idx}条内容过长（最大200字符）")

            # 特定类型验证
            if reminder_type == 'recurring':
                if not all(field in item for field in recurring_required):
                    raise ValueError(f"第{idx}条(recurring)记录字段缺失")
                time_str = str(item.get('time_str', '')).strip()
                if not time_pattern.match(time_str):
                    raise ValueError(f"第{idx}条(recurring)时间格式错误，应为 HH:MM ({time_str})")
                validated_reminders.append({
                    'reminder_type': 'recurring',
                    'user_id': user_id,
                    'time_str': time_str,
                    'content': content
                })
            elif reminder_type == 'one-off':
                if not all(field in item for field in one_off_required):
                     raise ValueError(f"第{idx}条(one-off)记录字段缺失")
                target_datetime_str = str(item.get('target_datetime_str', '')).strip()
                # 验证 YYYY-MM-DD HH:MM 格式
                if not datetime_pattern.match(target_datetime_str):
                    raise ValueError(f"第{idx}条(one-off)日期时间格式错误，应为 YYYY-MM-DD HH:MM ({target_datetime_str})")
                validated_reminders.append({
                    'reminder_type': 'one-off',
                    'user_id': user_id,
                    'target_datetime_str': target_datetime_str,
                    'content': content
                })

        # --- 原子化写入操作 ---
        # 使用临时文件确保写入安全，覆盖原文件
        temp_dir = os.path.dirname(json_path)
        with tempfile.NamedTemporaryFile('w', delete=False, dir=temp_dir, encoding='utf-8', suffix='.tmp') as temp_f:
            json.dump(validated_reminders, temp_f, ensure_ascii=False, indent=2) # 写入验证后的完整列表
            temp_path = temp_f.name
        # 替换原文件
        shutil.move(temp_path, json_path)

        return jsonify({'status': 'success', 'message': '所有提醒已更新'})

    except ValueError as ve: # 捕获验证错误
         app.logger.error(f'提醒保存验证失败: {str(ve)}')
         return jsonify({'error': f'数据验证失败: {str(ve)}'}), 400
    except Exception as e:
        app.logger.error(f'提醒保存失败: {str(e)}')
        return jsonify({'error': f'服务器内部错误: {str(e)}'}), 500

@app.route('/import_config', methods=['POST'])
@login_required
@limiter.limit("5 per hour")  # 速率限制：导入操作限制更严格
def import_config():
    global bot_process
    # 如果 bot 正在运行，则不允许导入配置
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再导入配置'}), 400

    try:
        if 'config_file' not in request.files:
            return jsonify({'error': '未找到上传的配置文件'}), 400
            
        config_file = request.files['config_file']
        
        # 安全验证1: 检查文件名是否为空
        if not config_file.filename:
            return jsonify({'error': '文件名不能为空'}), 400
        
        # 安全验证2: 验证文件扩展名
        try:
            validate_file_extension(config_file.filename, ALLOWED_CONFIG_EXTENSIONS)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        
        # 安全验证3: 验证文件大小
        try:
            validate_file_size(config_file)
        except ValueError as e:
            return jsonify({'error': str(e)}), 400
        
        # 安全验证4: 使用secure_filename清理文件名
        safe_filename_str = secure_filename(config_file.filename)
        if not safe_filename_str or not safe_filename_str.endswith('.py'):
            return jsonify({'error': '文件名不安全或格式错误'}), 400
            
        # 创建临时文件用于解析配置
        with tempfile.NamedTemporaryFile('wb', suffix='.py', delete=False) as temp_f:
            temp_path = temp_f.name
            config_file.save(temp_path)
        
        # 解析临时配置文件
        imported_config = {}
        try:
            with open(temp_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
                    if match:
                        var_name = match.group(1)
                        var_value_str = match.group(2)
                        try:
                            var_value = ast.literal_eval(var_value_str)
                            imported_config[var_name] = var_value
                        except:
                            imported_config[var_name] = var_value_str
        finally:
            # 清理临时文件
            try:
                os.unlink(temp_path)
            except:
                pass
        
        # 获取当前配置作为基础
        current_config = parse_config()
        
        # 定义不应该从旧版本导入的敏感设置
        PROTECTED_SETTINGS = ['ALLOW_OPEN_PORT', 'LOGIN_PASSWORD', 'PASSWORD_IS_VALID', 'PORT']
        
        # 合并配置：只更新导入配置中存在的项，但排除受保护的设置
        for key, value in imported_config.items():
            if key in current_config and key not in PROTECTED_SETTINGS:  # 只更新当前配置中已存在的项，且不在保护列表中
                current_config[key] = value
        
        # 更新配置文件
        update_config(current_config)
        
        return jsonify({'success': True, 'message': '配置导入成功，共导入了{}个有效参数'.format(len(imported_config))}), 200
    except Exception as e:
        app.logger.error(f"配置导入失败: {str(e)}")
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

def create_backup_directory():
    """创建备份目录"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = os.path.join(BASE_DIR, "数据备份", f"{timestamp}_导入备份")
    os.makedirs(backup_dir, exist_ok=True)
    return backup_dir

def backup_existing_data(backup_dir):
    """备份现有数据到指定目录"""
    backed_up_items = []
    
    try:
        # 备份 prompts 文件夹
        prompts_dir = os.path.join(BASE_DIR, 'prompts')
        if os.path.exists(prompts_dir):
            backup_prompts = os.path.join(backup_dir, 'prompts')
            shutil.copytree(prompts_dir, backup_prompts)
            backed_up_items.append('prompts文件夹')
        
        # 备份 emojis 文件夹
        emojis_dir = os.path.join(BASE_DIR, 'emojis')
        if os.path.exists(emojis_dir):
            backup_emojis = os.path.join(backup_dir, 'emojis')
            shutil.copytree(emojis_dir, backup_emojis)
            backed_up_items.append('emojis文件夹')
        
        # 备份 forum_data 文件夹
        forum_data_dir = FORUM_DATA_DIR
        if os.path.exists(forum_data_dir):
            backup_forum = os.path.join(backup_dir, 'forum_data')
            shutil.copytree(forum_data_dir, backup_forum)
            backed_up_items.append('forum_data文件夹')
        
        # 备份 CoreMemory 文件夹
        config = parse_config()
        core_memory_dir = os.path.join(BASE_DIR, config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        if os.path.exists(core_memory_dir):
            backup_core = os.path.join(backup_dir, os.path.basename(core_memory_dir))
            shutil.copytree(core_memory_dir, backup_core)
            backed_up_items.append('CoreMemory文件夹')
        
        # 备份 recurring_reminders.json 文件
        reminders_file = os.path.join(BASE_DIR, 'recurring_reminders.json')
        if os.path.exists(reminders_file):
            backup_reminders = os.path.join(backup_dir, 'recurring_reminders.json')
            shutil.copy2(reminders_file, backup_reminders)
            backed_up_items.append('recurring_reminders.json文件')
        
        # 备份 chat_contexts.json 文件
        chat_contexts_file = os.path.join(BASE_DIR, 'chat_contexts.json')
        if os.path.exists(chat_contexts_file):
            backup_chat_contexts = os.path.join(backup_dir, 'chat_contexts.json')
            shutil.copy2(chat_contexts_file, backup_chat_contexts)
            backed_up_items.append('chat_contexts.json文件')
        
        # 备份 Memory_Temp 文件夹
        config = parse_config()
        memory_temp_dirname = config.get('MEMORY_TEMP_DIR', 'Memory_Temp')
        memory_temp_dir = os.path.join(BASE_DIR, memory_temp_dirname)
        if os.path.exists(memory_temp_dir):
            backup_memory_temp = os.path.join(backup_dir, memory_temp_dirname)
            shutil.copytree(memory_temp_dir, backup_memory_temp)
            backed_up_items.append('Memory_Temp文件夹')
        
        # 备份 config.py 文件
        config_file = os.path.join(BASE_DIR, 'config.py')
        if os.path.exists(config_file):
            backup_config = os.path.join(backup_dir, 'config.py')
            shutil.copy2(config_file, backup_config)
            backed_up_items.append('config.py文件')
        
        return backed_up_items
    except Exception as e:
        app.logger.error(f"备份数据失败: {str(e)}")
        raise Exception(f"备份失败: {str(e)}")

def import_directory_data(source_dir):
    """从源目录导入数据"""
    imported_items = []
    
    try:
        # 导入 config.py 文件
        source_config = os.path.join(source_dir, 'config.py')
        if os.path.exists(source_config):
            # 解析源配置文件
            imported_config = {}
            with open(source_config, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('#') or not line:
                        continue
                    match = re.match(r'^(\w+)\s*=\s*(.+)$', line)
                    if match:
                        var_name = match.group(1)
                        var_value_str = match.group(2)
                        try:
                            var_value = ast.literal_eval(var_value_str)
                            imported_config[var_name] = var_value
                        except:
                            imported_config[var_name] = var_value_str
            
            # 获取当前配置并合并
            current_config = parse_config()
            
            # 定义不应该从旧版本导入的敏感设置
            PROTECTED_SETTINGS = ['ALLOW_OPEN_PORT', 'LOGIN_PASSWORD', 'PASSWORD_IS_VALID', 'PORT']
            
            for key, value in imported_config.items():
                if key in current_config and key not in PROTECTED_SETTINGS:  # 只更新当前配置中已存在的项，且不在保护列表中
                    current_config[key] = value
            
            # 更新配置文件
            update_config(current_config)
            imported_items.append(f'config.py文件（导入了{len(imported_config)}个参数）')
        
        # 导入 prompts 文件夹
        source_prompts = os.path.join(source_dir, 'prompts')
        if os.path.exists(source_prompts):
            target_prompts = os.path.join(BASE_DIR, 'prompts')
            if os.path.exists(target_prompts):
                shutil.rmtree(target_prompts)
            shutil.copytree(source_prompts, target_prompts)
            imported_items.append('prompts文件夹')
        
        # 导入 emojis 文件夹
        source_emojis = os.path.join(source_dir, 'emojis')
        if os.path.exists(source_emojis):
            target_emojis = os.path.join(BASE_DIR, 'emojis')
            if os.path.exists(target_emojis):
                shutil.rmtree(target_emojis)
            shutil.copytree(source_emojis, target_emojis)
            imported_items.append('emojis文件夹')
        
        # 导入 forum_data 文件夹
        source_forum = os.path.join(source_dir, 'forum_data')
        if os.path.exists(source_forum):
            target_forum = FORUM_DATA_DIR
            if os.path.exists(target_forum):
                shutil.rmtree(target_forum)
            shutil.copytree(source_forum, target_forum)
            imported_items.append('forum_data文件夹')
        
        # 导入 CoreMemory 文件夹
        config = parse_config()
        core_memory_dirname = config.get('CORE_MEMORY_DIR', 'CoreMemory')
        source_core = os.path.join(source_dir, core_memory_dirname)
        if os.path.exists(source_core):
            target_core = os.path.join(BASE_DIR, core_memory_dirname)
            if os.path.exists(target_core):
                shutil.rmtree(target_core)
            shutil.copytree(source_core, target_core)
            imported_items.append('CoreMemory文件夹')
        
        # 导入 recurring_reminders.json 文件
        source_reminders = os.path.join(source_dir, 'recurring_reminders.json')
        if os.path.exists(source_reminders):
            target_reminders = os.path.join(BASE_DIR, 'recurring_reminders.json')
            shutil.copy2(source_reminders, target_reminders)
            imported_items.append('recurring_reminders.json文件')
        
        # 导入 chat_contexts.json 文件
        source_chat_contexts = os.path.join(source_dir, 'chat_contexts.json')
        if os.path.exists(source_chat_contexts):
            target_chat_contexts = os.path.join(BASE_DIR, 'chat_contexts.json')
            shutil.copy2(source_chat_contexts, target_chat_contexts)
            imported_items.append('chat_contexts.json文件')
        
        # 导入 Memory_Temp 文件夹
        config = parse_config()
        memory_temp_dirname = config.get('MEMORY_TEMP_DIR', 'Memory_Temp')
        source_memory_temp = os.path.join(source_dir, memory_temp_dirname)
        if os.path.exists(source_memory_temp):
            target_memory_temp = os.path.join(BASE_DIR, memory_temp_dirname)
            if os.path.exists(target_memory_temp):
                shutil.rmtree(target_memory_temp)
            shutil.copytree(source_memory_temp, target_memory_temp)
            imported_items.append('Memory_Temp文件夹')
        
        return imported_items
    except Exception as e:
        app.logger.error(f"导入目录数据失败: {str(e)}")
        raise Exception(f"导入失败: {str(e)}")

def import_files_data(files_dict):
    """从上传的文件字典导入数据"""
    imported_items = []
    
    try:
        # 创建临时目录来重建文件结构
        temp_dir = tempfile.mkdtemp()
        
        try:
            # 文件计数器，防止上传过多文件
            file_count = 0
            
            # 重建文件结构
            for relative_path, file_data in files_dict.items():
                file_count += 1
                if file_count > MAX_FILES_PER_UPLOAD:
                    raise ValueError(f"上传文件数量超过限制 ({MAX_FILES_PER_UPLOAD})")
                
                # 安全验证1: 验证文件大小
                try:
                    validate_file_size(file_data)
                except ValueError as e:
                    app.logger.warning(f"文件 {relative_path} 大小超限，已跳过: {e}")
                    continue
                
                # 安全验证2: 验证文件扩展名
                try:
                    validate_file_extension(relative_path, ALLOWED_IMPORT_EXTENSIONS)
                except ValueError as e:
                    app.logger.warning(f"文件 {relative_path} 类型不允许，已跳过: {e}")
                    continue
                
                # 标准化路径分隔符
                relative_path = relative_path.replace('\\', '/')
                
                # 处理路径，去除顶级目录（如果有的话）
                path_parts = relative_path.split('/')
                if len(path_parts) > 1:
                    # 如果路径有多级，可能需要去除第一级目录
                    relative_path = '/'.join(path_parts[1:]) if path_parts[0] and path_parts[0] != '.' else relative_path
                
                # 跳过空路径
                if not relative_path:
                    continue
                
                # 安全验证3: 使用safe_path_join防止路径遍历
                try:
                    full_path = safe_path_join(temp_dir, relative_path)
                except ValueError as e:
                    app.logger.warning(f"路径不安全，已拒绝: {relative_path}, 原因: {e}")
                    continue
                
                # 确保目录存在
                dir_path = os.path.dirname(full_path)
                if dir_path and dir_path != temp_dir:
                    os.makedirs(dir_path, exist_ok=True)
                
                # 保存文件
                file_data.save(full_path)
                
                app.logger.debug(f"安全保存文件: {relative_path} -> {full_path}")
            
            # 使用现有的导入函数
            imported_items = import_directory_data(temp_dir)
            
            return imported_items
            
        finally:
            # 清理临时目录
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
                
    except Exception as e:
        app.logger.error(f"导入文件数据失败: {str(e)}")
        raise Exception(f"导入失败: {str(e)}")

@app.route('/import_full_directory', methods=['POST'])
@login_required
@limiter.limit("3 per hour")  # 速率限制：完整目录导入限制非常严格
def import_full_directory():
    """导入完整的旧版本程序目录"""
    global bot_process
    
    # 如果 bot 正在运行，则不允许导入
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再导入数据'}), 400

    try:
        # 检查是否上传了文件
        if 'directory_files' not in request.files:
            return jsonify({'error': '未找到上传的目录文件'}), 400
            
        uploaded_files = request.files.getlist('directory_files')
        if not uploaded_files:
            return jsonify({'error': '未找到任何文件'}), 400
        
        # 安全验证1: 检查文件数量
        if len(uploaded_files) > MAX_FILES_PER_UPLOAD:
            return jsonify({'error': f'上传文件数量超过限制（最多{MAX_FILES_PER_UPLOAD}个文件）'}), 400
        
        # 安全验证2: 预先验证所有文件
        total_size = 0
        config_found = False
        files_dict = {}
        
        for file in uploaded_files:
            if not file.filename:
                continue
                
            # 验证文件大小
            file.seek(0, os.SEEK_END)
            file_size = file.tell()
            file.seek(0)
            
            total_size += file_size
            
            # 检查单个文件大小
            if file_size > MAX_UPLOAD_SIZE:
                return jsonify({'error': f'文件 {file.filename} 大小超过限制'}), 400
            
            # 检查总大小（防止通过多个小文件绕过限制）
            if total_size > MAX_UPLOAD_SIZE * 10:  # 总大小不超过1GB
                return jsonify({'error': f'上传文件总大小超过限制（最多{MAX_UPLOAD_SIZE * 10 // 1024 // 1024}MB）'}), 400
            
            # 获取相对路径（webkitRelativePath）
            relative_path = file.filename
            files_dict[relative_path] = file
            
            # 检查是否有config.py
            if file.filename.endswith('config.py') or file.filename == 'config.py':
                config_found = True
        
        if not config_found:
            return jsonify({'error': '选择的目录中没有找到config.py文件'}), 400
        
        # 创建备份目录
        backup_dir = create_backup_directory()
        
        # 备份现有数据
        backed_up_items = backup_existing_data(backup_dir)
        
        # 导入新数据
        imported_items = import_files_data(files_dict)
        
        # 构建结果消息
        message = f"完整目录导入成功！\n"
        message += f"共处理了 {len(uploaded_files)} 个文件\n"
        if backed_up_items:
            message += f"已备份的数据: {', '.join(backed_up_items)}\n"
        if imported_items:
            message += f"已导入的数据: {', '.join(imported_items)}\n"
        message += f"备份位置: {backup_dir}"
        
        return jsonify({
            'success': True, 
            'message': message,
            'backed_up_items': backed_up_items,
            'imported_items': imported_items,
            'backup_location': backup_dir,
            'files_count': len(uploaded_files)
        }), 200
                
    except Exception as e:
        app.logger.error(f"完整目录导入失败: {str(e)}")
        return jsonify({'error': f'导入失败: {str(e)}'}), 500

@app.route('/reset_default_config', methods=['POST'])
@login_required
def reset_default_config():
    global bot_process
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再恢复默认配置'}), 400
    
    try:
        # 获取默认配置
        default_config = get_default_config()
        
        # 保留当前的端口号和登录密码设置（避免被锁在外）
        current_config = parse_config()
        if 'PORT' in current_config:
            default_config['PORT'] = current_config['PORT']
        if 'LOGIN_PASSWORD' in current_config:
            default_config['LOGIN_PASSWORD'] = current_config['LOGIN_PASSWORD']
        if 'ALLOW_OPEN_PORT' in current_config:
            default_config['ALLOW_OPEN_PORT'] = current_config['ALLOW_OPEN_PORT']
        if 'PASSWORD_IS_VALID' in current_config:
            default_config['PASSWORD_IS_VALID'] = current_config['PASSWORD_IS_VALID']
        
        # 使用 update_config 函数来保留原有的注释和格式
        update_config(default_config)
        
        app.logger.info("配置已恢复到默认值")
        return jsonify({'message': '配置已恢复到默认值'}), 200
        
    except Exception as e:
        app.logger.error(f"恢复默认配置失败: {e}")
        return jsonify({'error': f'恢复默认配置失败: {str(e)}'}), 500

class WebLogHandler(logging.Handler):
    def emit(self, record):
        try:
            log_entry = self.format(record)
            # 使用非阻塞方式放入队列，避免阻塞
            try:
                log_queue.put(log_entry, block=False)
            except:
                pass  # 队列满时静默丢弃
        except Exception as e:
            # 避免日志处理器内部错误导致程序崩溃
            print(f"WebLogHandler错误: {e}")

# 配置日志处理器
web_handler = WebLogHandler()
web_handler.setFormatter(logging.Formatter('[WEB] %(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(web_handler)

# 发送初始化日志，验证日志系统正常工作
app.logger.info("配置编辑器日志系统已初始化")

@app.route('/stream')
@limiter.exempt  # SSE长连接不计入默认速率限制，避免返回429/500
@login_required
def stream():
    try:
        app.logger.info(f"SSE连接建立请求：/stream 来自 {request.remote_addr}")
    except Exception:
        pass
    def event_stream():
        try:
            # 发送初始连接确认消息
            yield "data: [系统] 日志流已连接，等待日志数据...\n\n"
            
            retry_count = 0
            consecutive_errors = 0
            max_consecutive_errors = 10
            
            while True:
                try:
                    # 尝试从队列获取日志，超时时间5秒
                    log = log_queue.get(timeout=5)
                    yield f"data: {log}\n\n"
                    retry_count = 0  # 成功时重置重试计数器
                    consecutive_errors = 0  # 重置连续错误计数
                except Empty:
                    # 队列为空时发送心跳包保持连接
                    yield ":keep-alive\n\n"
                    retry_count = min(retry_count + 1, 5)
                    # 使用指数退避，但最多延迟不超过32秒
                    time.sleep(min(2 ** retry_count, 32))
                except GeneratorExit:
                    # 客户端主动断开连接
                    app.logger.info("客户端已断开日志流连接")
                    break
                except Exception as e:
                    consecutive_errors += 1
                    app.logger.error(f"日志流错误 ({consecutive_errors}/{max_consecutive_errors}): {str(e)}")
                    
                    if consecutive_errors >= max_consecutive_errors:
                        yield "data: [系统] 日志流发生错误，连接已关闭\n\n"
                        break
                    
                    # 短暂延迟后继续
                    time.sleep(1)
        except GeneratorExit:
            app.logger.info("日志流生成器已退出")
        except Exception as e:
            app.logger.error(f"event_stream严重错误: {str(e)}")
            try:
                yield f"data: [系统] 日志流发生严重错误: {str(e)}\n\n"
            except:
                pass
    
    response = Response(
        stream_with_context(event_stream()),
        content_type="text/event-stream; charset=utf-8",
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )
    # 让 Flask 处理编码与传输
    response.direct_passthrough = False

    return response

@app.route('/api/log', methods=['POST'])
@csrf.exempt  # CSRF豁免：bot.py的日志上传，使用内容验证
@limiter.limit("200 per minute")  # 速率限制：每分钟最多200条日志
# 注意：此端点不使用 @login_required，因为 bot.py 进程需要无认证访问
# 安全性通过 localhost 限制和速率限制保证
def receive_bot_log():
    try:
        # 增加Content-Type检查
        if not request.is_json:
            app.logger.warning(f"收到非JSON请求，Content-Type: {request.content_type}")
            return jsonify({'error': 'Unsupported Media Type'}), 415

        # 支持两种格式：单个日志或日志数组
        if 'logs' in request.json:  # 批量日志
            logs_data = request.json.get('logs', [])
            if isinstance(logs_data, list):
                processed_count = 0
                for log_entry in logs_data:
                    if log_entry:
                        # 添加进程标识和颜色标记
                        colored_log = f"[BOT] \033[34m{log_entry.strip()}\033[0m"
                        try:
                            log_queue.put(colored_log, block=False)
                            processed_count += 1
                        except:
                            # 队列满时，记录警告但不中断
                            app.logger.warning(f"日志队列已满，丢弃日志")
                            pass
                # 定期输出接收统计（每收到100条日志输出一次）
                if processed_count > 0 and processed_count % 100 == 0:
                    print(f"[配置编辑器] 已接收 bot.py 日志: {processed_count} 条")
                return jsonify({'status': 'success', 'processed': processed_count})
            return jsonify({'error': 'Invalid logs format'}), 400
            
        elif 'log' in request.json:  # 兼容单条日志格式
            log_data = request.json.get('log')
            if log_data:
                # 添加进程标识和颜色标记
                colored_log = f"[BOT] \033[34m{log_data.strip()}\033[0m"
                try:
                    log_queue.put(colored_log, block=False)
                except:
                    app.logger.warning(f"日志队列已满，丢弃日志")
            return jsonify({'status': 'success'})
            
        else:
            app.logger.warning("收到的请求中缺少 'logs' 或 'log' 字段")
            return jsonify({'error': 'Missing log data'}), 400
            
    except Exception as e:
        app.logger.error(f"日志接收失败: {str(e)}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/api/get_chat_context_users', methods=['GET'])
@limiter.exempt  # 豁免速率限制：前端频繁轮询，只读操作
@login_required
def api_get_chat_context_users():
    users = get_chat_context_users()
    return jsonify({'users': users})

@app.route('/clear_chat_context/<username>', methods=['POST'])
@login_required
def clear_chat_context(username):
    """清除指定用户的聊天上下文"""
    # 输入验证
    try:
        username = validate_username(username)
    except ValueError as e:
        app.logger.warning(f"无效的用户名: {username}, 错误: {e}")
        return jsonify({'status': 'error', 'message': f'无效的用户名: {str(e)}'}), 400
    
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return jsonify({'status': 'error', 'message': '聊天上下文文件不存在'}), 404

    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            with open(CHAT_CONTEXTS_FILE, 'r+', encoding='utf-8') as f:
                data = json.load(f)
                if username in data:
                    del data[username]
                    f.seek(0) # 回到文件开头
                    json.dump(data, f, ensure_ascii=False, indent=4)
                    f.truncate() # 删除剩余内容
                    return jsonify({'status': 'success', 'message': f"用户 '{username}' 的聊天上下文已清除"})
                else:
                    return jsonify({'status': 'error', 'message': f"用户 '{username}' 未找到"}), 404
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"处理 chat_contexts.json 失败: {e}")
            return jsonify({'status': 'error', 'message': '处理聊天上下文文件失败'}), 500

# 聊天上下文编辑API
@app.route('/api/get_chat_context/<username>', methods=['GET'])
@login_required
def get_user_chat_context(username):
    """获取指定用户的聊天上下文"""
    # 输入验证
    try:
        username = validate_username(username)
    except ValueError as e:
        app.logger.warning(f"无效的用户名: {username}, 错误: {e}")
        return jsonify({'error': f'无效的用户名: {str(e)}'}), 400
    
    if not os.path.exists(CHAT_CONTEXTS_FILE):
        return jsonify({'error': '聊天上下文文件未找到'}), 404

    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
                contexts = json.load(f)
            user_context = contexts.get(username)
            if user_context is None:
                return jsonify({'error': f"用户 '{username}' 在上下文中不存在"}), 404
            pretty_context = json.dumps(user_context, ensure_ascii=False, indent=4)
            return jsonify({'status': 'success', 'context': pretty_context})
        except (json.JSONDecodeError, IOError) as e:
            app.logger.error(f"读取或解析聊天上下文文件失败: {e}")
            return jsonify({'error': f'读取或解析文件失败: {e}'}), 500

@app.route('/api/save_chat_context/<username>', methods=['POST'])
@login_required
def save_user_chat_context(username):
    """保存指定用户修改后的聊天上下文，强制合并连续user消息，确保user→assistant结构"""
    # 输入验证
    try:
        username = validate_username(username)
    except ValueError as e:
        app.logger.warning(f"无效的用户名: {username}, 错误: {e}")
        return jsonify({'status': 'error', 'message': f'无效的用户名: {str(e)}'}), 400
    
    if bot_process and bot_process.poll() is not None:
        return jsonify({'error': '程序正在运行，请先停止再保存上下文'}), 400
    data = request.get_json()
    if not data or 'context' not in data:
        return jsonify({'status': 'error', 'message': '请求无效，缺少 "context" 字段'}), 400
    new_context_str = data['context']
    try:
        new_context_data = json.loads(new_context_str)
        if not isinstance(new_context_data, list):
            raise ValueError("上下文数据必须是一个JSON数组 (list)")
        # --- 强制合并连续user，保证user→assistant结构 ---
        merged_context = []
        user_buffer = []
        for item in new_context_data:
            if item.get('role') == 'user':
                user_buffer.append(item.get('content', ''))
            elif item.get('role') == 'assistant':
                if user_buffer:
                    merged_context.append({'role': 'user', 'content': '\n'.join(user_buffer)})
                    user_buffer = []
                merged_context.append(item)
        if user_buffer:
            merged_context.append({'role': 'user', 'content': '\n'.join(user_buffer)})
        # --- END ---
    except (json.JSONDecodeError, ValueError) as e:
        return jsonify({'status': 'error', 'message': f'格式错误: {str(e)}'}), 400
    with FileLock(CHAT_CONTEXTS_LOCK_FILE):
        try:
            if not os.path.exists(CHAT_CONTEXTS_FILE):
                return jsonify({'status': 'error', 'message': '聊天上下文文件未找到'}), 404
            with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
                all_contexts = json.load(f)
            if username not in all_contexts:
                return jsonify({'status': 'error', 'message': '用户在上下文中不存在'}), 404
            all_contexts[username] = merged_context
            temp_file_path = CHAT_CONTEXTS_FILE + ".tmp"
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(all_contexts, f, ensure_ascii=False, indent=4)
            shutil.move(temp_file_path, CHAT_CONTEXTS_FILE)
        except Exception as e:
            app.logger.error(f"保存聊天上下文失败: {e}")
            return jsonify({'status': 'error', 'message': f'保存失败: {str(e)}'}), 500
    return jsonify({'status': 'success', 'message': f"用户 '{username}' 的上下文已更新"})

@app.route('/api/npc/save_settings', methods=['POST'])
@login_required
def save_npc_settings():
    """保存NPC配置"""
    try:
        data = request.json
        if not data:
            return jsonify({'error': '无效的数据'}), 400
        
        app.logger.info(f"接收到NPC配置数据: {data}")
        
        # 保存NPC配置到文件
        npc_config_file = _npc_config_file_path()
        
        # 读取现有配置
        existing_config = {}
        if os.path.exists(npc_config_file):
            try:
                with open(npc_config_file, 'r', encoding='utf-8') as f:
                    existing_config = json.load(f)
                app.logger.info(f"读取现有配置: {existing_config}")
            except Exception as read_error:
                app.logger.warning(f"读取现有配置失败，使用空配置: {read_error}")
                existing_config = {}
        
        # 更新配置 - 使用更精确的更新逻辑
        app.logger.info(f"接收到的数据: {data}")
        
        # 更新选中的NPC列表
        if 'selected_npcs' in data:
            existing_config['selected_npcs'] = data['selected_npcs']
            app.logger.info(f"更新选中的NPC列表: {data['selected_npcs']}")
        
        # 更新NPC设置
        if 'npc_settings' in data:
            if 'npc_settings' not in existing_config:
                existing_config['npc_settings'] = {}
            
            for npc_name, npc_data in data['npc_settings'].items():
                # 清理不需要的字段（移除 model_type）
                try:
                    if isinstance(npc_data, dict) and 'model_type' in npc_data:
                        npc_data.pop('model_type', None)
                except Exception:
                    pass
                
                existing_config['npc_settings'][npc_name] = npc_data
                app.logger.info(f"更新NPC {npc_name} 的设置: {npc_data}")

        
        app.logger.info(f"合并后的配置: {existing_config}")
        
        # 验证配置格式
        if 'selected_npcs' in existing_config and 'npc_settings' in existing_config:
            # 确保所有选中的NPC都有对应的设置
            for npc_name in existing_config['selected_npcs']:
                if npc_name not in existing_config['npc_settings']:
                    app.logger.warning(f"NPC {npc_name} 被选中但没有设置，将添加默认设置")
                    existing_config['npc_settings'][npc_name] = {
                        'language_style': 'casual',
                        'relationship': 'friend',
                        'example_output': '',
                        'other_settings': ''
                    }
                else:
                    app.logger.info(f"NPC {npc_name} 设置已更新")
        
        # 保存配置
        with open(npc_config_file, 'w', encoding='utf-8') as f:
            json.dump(existing_config, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"成功保存NPC配置到文件: {npc_config_file}")
        
        # 验证保存是否成功
        try:
            with open(npc_config_file, 'r', encoding='utf-8') as f:
                saved_config = json.load(f)
            app.logger.info(f"验证保存的配置: {saved_config}")
        except Exception as verify_error:
            app.logger.error(f"验证保存配置失败: {verify_error}")
            return jsonify({'error': '配置保存后验证失败'}), 500
        
        return jsonify({
            'success': True, 
            'message': 'NPC配置保存成功',
            'saved_config': saved_config
        })
        
    except Exception as e:
        app.logger.error(f"保存NPC配置失败: {e}")
        return jsonify({'error': f'保存失败: {str(e)}'}), 500

@app.route('/api/npc/get_settings', methods=['GET'])
@login_required
def get_npc_settings():
    """获取NPC配置"""
    try:
        npc_config_file = _npc_config_file_path()
        
        if os.path.exists(npc_config_file):
            try:
                with open(npc_config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                app.logger.info(f"成功读取NPC配置: {config}")
                
                # 确保配置格式完整
                if 'selected_npcs' not in config:
                    config['selected_npcs'] = []
                if 'npc_settings' not in config:
                    config['npc_settings'] = {}
                
                # 为每个选中的NPC确保有默认设置
                for npc_name in config.get('selected_npcs', []):
                    if npc_name not in config.get('npc_settings', {}):
                        config['npc_settings'][npc_name] = {
                            'language_style': 'casual',
                            'relationship': 'friend',
                            'example_output': '',
                            'other_settings': ''
                        }
                        app.logger.info(f"为NPC {npc_name} 添加默认设置")
                    else:
                        # 清理不需要的字段（移除 model_type）
                        if isinstance(config['npc_settings'][npc_name], dict):
                            config['npc_settings'][npc_name].pop('model_type', None)
                        app.logger.info(f"NPC {npc_name} 已有设置: {config['npc_settings'][npc_name]}")
                
                return jsonify(config)
            except Exception as read_error:
                app.logger.error(f"读取NPC配置文件失败: {read_error}")
                return jsonify({'error': f'读取配置失败: {str(read_error)}'}), 500
        else:
            app.logger.info("NPC配置文件不存在，返回空配置")
            return jsonify({
                'selected_npcs': [],
                'npc_settings': {}
            })
            
    except Exception as e:
        app.logger.error(f"获取NPC配置失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

# 核心记忆管理API
@app.route('/api/get_core_memory_files', methods=['GET'])
@login_required
def get_core_memory_files():
    """获取核心记忆文件列表"""
    try:
        # 从配置中获取核心记忆目录
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        
        # 确保目录存在
        os.makedirs(core_memory_dir, exist_ok=True)
        
        files = []
        if os.path.exists(core_memory_dir):
            for filename in os.listdir(core_memory_dir):
                if filename.endswith('_core_memory.json'):
                    file_path = os.path.join(core_memory_dir, filename)
                    try:
                        # 读取文件获取记忆片段数量
                        with open(file_path, 'r', encoding='utf-8') as f:
                            memories = json.load(f)
                        
                        memory_count = len(memories) if isinstance(memories, list) else 0
                        
                        # 获取文件修改时间
                        mtime = os.path.getmtime(file_path)
                        last_modified = datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')
                        
                        # 从文件名提取显示名称
                        display_name = filename.replace('_core_memory.json', '').replace('_', ' - ')
                        
                        files.append({
                            'filename': filename,
                            'display_name': display_name,
                            'memory_count': memory_count,
                            'last_modified': last_modified
                        })
                    except Exception as e:
                        app.logger.warning(f"读取核心记忆文件 {filename} 失败: {e}")
                        continue
        
        # 按修改时间倒序排列
        files.sort(key=lambda x: x['last_modified'], reverse=True)
        
        return jsonify({'status': 'success', 'files': files})
        
    except Exception as e:
        app.logger.error(f"获取核心记忆文件列表失败: {e}")
        return jsonify({'status': 'error', 'message': f'获取失败: {str(e)}'}), 500

@app.route('/api/get_core_memory/<filename>', methods=['GET'])
@login_required  
def get_core_memory(filename):
    """获取指定核心记忆文件的内容"""
    try:
        # 输入验证
        try:
            filename = validate_path(filename)
        except ValueError as e:
            app.logger.warning(f"无效的文件名: {filename}, 错误: {e}")
            return jsonify({'status': 'error', 'error': f'无效的文件名: {str(e)}'}), 400
        
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'error': '无效的文件名'}), 400
            
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        file_path = os.path.join(core_memory_dir, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'error': '文件不存在'}), 404
        
        # 读取记忆文件
        with open(file_path, 'r', encoding='utf-8') as f:
            memories = json.load(f)
        
        # 确保返回的是列表格式
        if not isinstance(memories, list):
            memories = []
        
        # 字段名兼容性处理：处理旧版本的time字段，统一使用timestamp字段
        for memory in memories:
            if isinstance(memory, dict):
                # 如果有旧版本的time字段但没有timestamp字段，则将time转换为timestamp
                if 'time' in memory and 'timestamp' not in memory:
                    memory['timestamp'] = memory['time']
                    del memory['time']  # 删除旧字段，避免冗余
            
        return jsonify({'status': 'success', 'memories': memories})
        
    except Exception as e:
        app.logger.error(f"获取核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'error': f'读取失败: {str(e)}'}), 500

@app.route('/api/save_core_memory/<filename>', methods=['POST'])
@login_required
@limiter.limit("30 per minute")  # 速率限制：核心记忆保存
def save_core_memory(filename):
    """保存核心记忆到指定文件"""
    try:
        # 输入验证
        try:
            filename = validate_path(filename)
        except ValueError as e:
            app.logger.warning(f"无效的文件名: {filename}, 错误: {e}")
            return jsonify({'status': 'error', 'message': f'无效的文件名: {str(e)}'}), 400
        
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'message': '无效的文件名'}), 400
            
        data = request.json
        memories = data.get('memories', [])
        
        # 验证数据格式
        if not isinstance(memories, list):
            return jsonify({'status': 'error', 'message': '记忆数据必须是数组格式'}), 400
        
        # 验证每个记忆片段的格式
        for i, memory in enumerate(memories):
            if not isinstance(memory, dict):
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段格式错误'}), 400
            
            # 字段名兼容性检查：支持time字段，但统一转换为timestamp
            if 'time' in memory and 'timestamp' not in memory:
                memory['timestamp'] = memory['time']
                del memory['time']  # 删除旧字段，避免冗余
            
            # 验证必要字段
            if 'timestamp' not in memory or 'importance' not in memory or 'summary' not in memory:
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段缺少必要字段(timestamp、importance、summary)'}), 400
                
            if not isinstance(memory['importance'], int) or not (1 <= memory['importance'] <= 10):
                return jsonify({'status': 'error', 'message': f'第{i+1}个记忆片段的重要度必须是1-10的整数'}), 400
        
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        os.makedirs(core_memory_dir, exist_ok=True)
        
        file_path = os.path.join(core_memory_dir, filename)
        
        # 保存记忆文件
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"已保存核心记忆文件: {filename}")
        return jsonify({'status': 'success', 'message': '核心记忆已保存'})
        
    except Exception as e:
        app.logger.error(f"保存核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'message': f'保存失败: {str(e)}'}), 500

@app.route('/api/delete_core_memory/<filename>', methods=['DELETE'])
@login_required
@limiter.limit("10 per minute")  # 速率限制：核心记忆删除
def delete_core_memory(filename):
    """删除核心记忆文件"""
    try:
        # 输入验证
        try:
            filename = validate_path(filename)
        except ValueError as e:
            app.logger.warning(f"无效的文件名: {filename}, 错误: {e}")
            return jsonify({'status': 'error', 'message': f'无效的文件名: {str(e)}'}), 400
        
        # 验证文件名安全性
        if not filename.endswith('_core_memory.json'):
            return jsonify({'status': 'error', 'message': '无效的文件名'}), 400
            
        config = parse_config()
        core_memory_dir = os.path.join(os.path.dirname(__file__), config.get('CORE_MEMORY_DIR', 'CoreMemory'))
        file_path = os.path.join(core_memory_dir, filename)
        
        if not os.path.exists(file_path):
            return jsonify({'status': 'error', 'message': '文件不存在'}), 404
        
        # 删除文件
        os.remove(file_path)
        
        app.logger.info(f"已删除核心记忆文件: {filename}")
        return jsonify({'status': 'success', 'message': '核心记忆文件已删除'})
        
    except Exception as e:
        app.logger.error(f"删除核心记忆文件 {filename} 失败: {e}")
        return jsonify({'status': 'error', 'message': f'删除失败: {str(e)}'}), 500

def run_bat_file():
    bat_file_path = "一键检测.bat"
    if os.path.exists(bat_file_path):
        os.system(f"start {bat_file_path}")

from multiprocessing import Process
import random
from datetime import datetime, timedelta
@app.route('/forum/<character_name>')
@login_required
def character_forum(character_name):
    """AI角色论坛页面"""
    try:
        # 输入验证
        try:
            character_name = validate_path(character_name)
        except ValueError as e:
            app.logger.warning(f"无效的角色名: {character_name}, 错误: {e}")
            return f"无效的角色名: {str(e)}", 400
        
        config = parse_config()
        
        # 验证角色名是否存在于配置中
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return "角色不存在", 404
        
        return render_template('character_forum.html', character_name=character_name)
    
    except Exception as e:
        app.logger.error(f"加载论坛页面失败: {e}")
        return "加载论坛页面失败", 500

@app.route('/api/forum/refresh/<character_name>', methods=['POST'])
@login_required
@limiter.limit("20 per hour")  # 速率限制：论坛刷新（调用AI，成本较高）
def refresh_forum(character_name):
    """刷新论坛内容API"""
    try:
        # 输入验证
        try:
            character_name = validate_path(character_name)
        except ValueError as e:
            app.logger.warning(f"无效的角色名: {character_name}, 错误: {e}")
            return jsonify({'error': f'无效的角色名: {str(e)}'}), 400
        
        config = parse_config()
        
        # 验证角色名
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        # 调用AI判断是否发论坛
        app.logger.info(f"开始为角色 {character_name} 刷新论坛")
        should_post, content = check_should_post_forum(character_name)
        app.logger.info(f"AI判断结果: should_post={should_post}, content='{content}'")
        
        if should_post:
            # 添加新论坛内容
            forum_post = add_forum_post(character_name, content)
            app.logger.info(f"成功添加论坛帖子: {forum_post['id'] if forum_post else 'None'}")
            return jsonify({
                'has_new_content': True,
                'post': forum_post
            })
        else:
            return jsonify({
                'has_new_content': False,
                'message': '暂时没有新的论坛动态'
            })
    
    except Exception as e:
        app.logger.error(f"刷新论坛失败: {e}")
        return jsonify({'error': f'刷新失败: {str(e)}'}), 500

@app.route('/api/forum/posts/<character_name>')
@login_required
def get_forum_posts(character_name):
    """获取论坛历史帖子"""
    try:
        config = parse_config()
        
        posts = get_character_forum_posts(character_name)
        return jsonify({'posts': posts})
    
    except Exception as e:
        app.logger.error(f"获取论坛帖子失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/delete/<character_name>/<post_id>', methods=['DELETE'])
@login_required
@limiter.limit("20 per minute")  # 速率限制：防止滥用删除
def delete_forum_post(character_name, post_id):
    """删除角色论坛帖子API"""
    try:
        # 输入验证
        try:
            character_name = validate_path(character_name)
            post_id = validate_path(post_id)
        except ValueError as e:
            app.logger.warning(f"无效的参数, 错误: {e}")
            return jsonify({'error': f'无效的参数: {str(e)}'}), 400
        
        app.logger.info(f"开始删除角色 {character_name} 的帖子 {post_id}")
        
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        # 删除帖子
        success = delete_forum_post_by_id(character_name, post_id)
        
        if success:
            app.logger.info(f"成功删除帖子 {post_id}")
            return jsonify({'message': '删除成功'})
        else:
            app.logger.error(f"删除帖子 {post_id} 失败")
            return jsonify({'error': '帖子不存在或删除失败'}), 404
    
    except Exception as e:
        app.logger.error(f"删除论坛帖子失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

@app.route('/test_forum_ai/<character_name>')
@login_required
def test_forum_ai(character_name):
    """测试AI论坛判断逻辑"""
    try:
        config = parse_config()

        
        app.logger.info(f"测试AI判断逻辑，角色: {character_name}")
        should_post, content = check_should_post_forum(character_name)
        
        result = {
            "character": character_name,
            "should_post": should_post,
            "content": content,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        
        return f"""
        <h2>AI判断测试结果</h2>
        <p><strong>角色:</strong> {character_name}</p>
        <p><strong>判断结果:</strong> {'发布' if should_post else '不发布'}</p>
        <p><strong>生成内容:</strong> {content}</p>
        <p><strong>测试时间:</strong> {result['timestamp']}</p>
        <hr>
        <p><a href="javascript:history.back()">返回</a> | <a href="/forum/{character_name}" target="_blank">查看论坛</a></p>
        """
        
    except Exception as e:
        app.logger.error(f"测试AI判断失败: {e}")
        return f"测试失败: {str(e)}", 500

@app.route('/run_one_key_detection', methods=['GET'])
@login_required
def run_one_key_detection():
    bat_file_path = "一键检测.bat"
    if os.path.exists(bat_file_path):
        # 使用独立的进程运行 .bat 文件
        p = Process(target=run_bat_file)
        p.start()
        return """
        <h2>启动成功！</h2>
        <p>一键检测工具已成功启动！</p>
        <ul>
            <li>检测工具将在单独的端口上运行，并自动打开浏览器窗口显示检测结果。</li>
            <li>启动后，检测工具将在3分钟后自动关闭进程。</li>
        </ul>
        <h3>检测功能：</h3>
        <ul>
            <li>微信环境检测：检查微信版本、登录状态和窗口状态。</li>
            <li>API配置检测：验证API密钥和连接状态。</li>
            <li>系统资源检测：分析CPU、内存使用情况。</li>
            <li>生成详细的诊断报告：提供问题解决建议。</li>
        </ul>
        <p style="color: green; font-weight: bold;">提示：本页面可以安全关闭，检测工具将在后台运行。</p>
        """
    return """
    <h2>启动失败</h2>
    <p style="color: red;">未找到一键检测.bat，请检查路径是否正确。</p>
    <p>请确保<b>一键检测.bat</b>文件位于程序当前运行目录下。</p>
    """

def check_should_post_forum(character_name):
    """调用AI判断是否发论坛内容"""
    try:
        app.logger.info(f"检查角色 {character_name} 是否应该发布论坛内容")
        config = parse_config()
        
        # 读取角色设定
        character_prompt = load_character_prompt(character_name)
        if not character_prompt:
            app.logger.warning(f"无法加载角色 {character_name} 的设定文件")
            return False, ""
        
        app.logger.info(f"成功加载角色设定，长度: {len(character_prompt)} 字符")
        
        # 构建AI提示
        current_time = datetime.now()
        time_str = current_time.strftime("%Y-%m-%d %H:%M")
        
        # 获取最近的论坛历史（用于避免重复）
        recent_posts = get_character_forum_posts(character_name, limit=5)
        recent_content = ""
        if recent_posts:
            recent_content = "\n最近发过的内容：\n" + "\n".join([f"- {post['content']}" for post in recent_posts[:3]])
        
        # --- 新增：50%概率的联网热点检索 ---
        import random
        online_hot_brief = ''
        should_use_online = random.random() < 0.5  # 50%概率
        
        if should_use_online and bool(config.get('ENABLE_ONLINE_API', False)):
            online_api_key = (config.get('ONLINE_API_KEY') or '').strip()
            online_base_url = config.get('ONLINE_BASE_URL', 'https://vg.v1api.cc/v1')
            online_model = config.get('ONLINE_MODEL', 'net-gpt-4o-mini')
            online_temperature = float(config.get('ONLINE_API_TEMPERATURE', 0.7))
            online_max_tokens = int(config.get('ONLINE_API_MAX_TOKEN', 2000))

            if online_api_key:
                try:
                    app.logger.info(f"尝试使用联网模型获取实时热点信息")
                    online_prompt = (
                        "请用简洁要点汇总'今天'中文互联网主要新闻与热点（3-5条），不要涉及政治敏感话题和政治人物。"
                        "偏向话题与趋势，不要细节长文；每条不超过30字；"
                        "只输出纯文本多行，不要编号、不要任何额外解释。"
                    )
                    online_client = openai.OpenAI(base_url=online_base_url, api_key=online_api_key, timeout=20)
                    online_completion = online_client.chat.completions.create(
                        model=online_model,
                        messages=[{"role": "user", "content": online_prompt}],
                        temperature=online_temperature,
                        max_tokens=min(online_max_tokens, 300),
                        timeout=20
                    )
                    online_hot_brief = (online_completion.choices[0].message.content or '').strip()
                    # 只保留前5行，避免上下文过长
                    if online_hot_brief:
                        lines_hot = [ln.strip() for ln in online_hot_brief.split('\n') if ln.strip()]
                        online_hot_brief = "\n".join(lines_hot[:5])
                        app.logger.info(f"成功获取联网热点信息，长度: {len(online_hot_brief)} 字符")
                except Exception as online_err:
                    app.logger.warning(f"联网热点检索失败，将使用常规生成: {online_err}")
                    online_hot_brief = ''
            else:
                app.logger.info("联网API密钥未配置，跳过热点检索")
        else:
            if should_use_online:
                app.logger.info("联网功能未启用，跳过热点检索")
            else:
                app.logger.info("本次未触发联网热点检索（50%概率）")
        
        # 构建融合热点的prompt
        if online_hot_brief:
            prompt = f"""
你有一个瞒着用户的小号推特账号，人设中的朋友圈人物都知道这个账号并会互动，你会在这里发一些比较真实的想法和日常。

角色设定：
{character_prompt}

当前时间：{time_str}
{recent_content}

今日热点参考（可选择性融合，避免机械复述，可以结合相关话题氛围发表个人感受）：
{online_hot_brief}

内容类型和tag使用：
1. 普通动态 - 直接发内容，不需要特殊标记
2. 备忘录类 - 用 #备忘录 tag
3. 情感宣泄 - 用 #心情 #想法 等tag
4. 日常记录 - 用 #日常 #生活 等tag
5. 重要事件 - 用 #记录 #重要 等tag
6. 热点评论 - 结合热点话题发表个人看法，可用相关tag

请根据角色性格和当前状态，可以结合今日热点话题（如果感兴趣），生成一条自然的推特动态。

要求：
- 情感表达直接、真实
- 具有活人感、可以适当结合网络热梗
- 短句不用句号
- 可以使用相关的tag（用#开头）
- 内容长度适中，像真实的推特发言
- 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线
- 如果要评论热点，请结合个人角色特点和感受，不要流水账式复述

直接输出动态内容，不需要"内容："前缀。
"""
        else:
            prompt = f"""
你有一个瞒着用户的小号推特账号，人设中的朋友圈人物都知道这个账号并会互动，你会在这里发一些比较真实的想法和日常。

角色设定：
{character_prompt}

当前时间：{time_str}
{recent_content}

内容类型和tag使用：
1. 普通动态 - 直接发内容，不需要特殊标记
2. 备忘录类 - 用 #备忘录 tag
3. 情感宣泄 - 用 #心情 #想法 等tag
4. 日常记录 - 用 #日常 #生活 等tag
5. 重要事件 - 用 #记录 #重要 等tag

请根据角色性格和当前状态，随机选择一种内容类型，生成一条自然的推特动态。

要求：
- 情感表达直接、真实
- 具有活人感、可以适当结合网络热梗
- 短句不用句号
- 可以使用相关的tag（用#开头）
- 内容长度适中，像真实的推特发言
- 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线

直接输出动态内容，不需要"内容："前缀。
"""

        # 打印完整提示词到控制台，方便调试
        print("\n" + "="*80)
        print(f"🎯 论坛拉取请求 - 角色: {character_name}")
        print("="*80)
        print(f"📊 提示词统计:")
        print(f"   角色设定长度: {len(character_prompt)} 字符")
        print(f"   总提示词长度: {len(prompt)} 字符")
        print(f"   当前时间: {time_str}")
        print(f"   最近内容数量: {len(recent_posts) if recent_posts else 0}")
        print(f"   联网热点检索: {'已启用' if online_hot_brief else '未启用'}")
        if online_hot_brief:
            print(f"   热点内容长度: {len(online_hot_brief)} 字符")
        print("="*80 + "\n")
        
        # 调用AI API - 优先使用论坛自定义模型，其次主模型
        use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))
        if use_forum_custom:
            api_key = (config.get('FORUM_API_KEY') or config.get('DEEPSEEK_API_KEY', '')).strip()
            base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1'))
            # 如果未填写论坛模型则回落到主模型
            model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
            temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.1))
            max_tokens = int(config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000)))
        else:
            api_key = config.get('DEEPSEEK_API_KEY', '')
            base_url = config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1')
            model = config.get('MODEL', 'deepseek-v3-0324')
            temperature = config.get('TEMPERATURE', 1.1)
            max_tokens = config.get('MAX_TOKEN', 2000)
        
        app.logger.info(f"准备调用AI API，模型: {model}, base_url: {base_url}")
        
        if not api_key:
            app.logger.error("API密钥未配置")
            return False, ""
        
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=30  # 设置30秒超时
        )
        
        app.logger.info("开始调用AI API...")
        completion = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=30  # API调用超时设置
        )
        
        reply = completion.choices[0].message.content.strip()
        app.logger.info(f"AI回复: {reply}")
        
        # 打印AI响应到控制台，方便调试
        print(f"🤖 AI模型响应:")
        print(f"   模型: {model}")
        print(f"   联网热点: {'已使用' if online_hot_brief else '未使用'}")
        print(f"   响应内容: '{reply}'")
        print(f"   响应长度: {len(reply) if reply else 0} 字符")
        print(f"   是否为空: {'是' if not reply else '否'}")
        print("="*80 + "\n")
        
        # 简化的解析逻辑 - 直接使用AI的回复作为内容
        if reply and len(reply) > 5:
            # 清理可能的格式标记
            content = reply
            
            # 移除可能的"内容："前缀（如果AI还是加了的话）
            if content.startswith("内容：") or content.startswith("内容:"):
                content = content.split("：", 1)[-1].split(":", 1)[-1].strip()
            
            # 移除一些明显的格式字符
            content = content.replace("[具体的动态内容]", "").strip()
            
            # 自动替换反斜线为换行符
            content = content.replace("\\", "\n")
            
            if len(content) > 5:
                return True, content
        
        return False, ""
    
    except Exception as e:
        app.logger.error(f"调用AI判断论坛发布失败: {e}")
        # 如果API调用失败，生成一个简单的默认内容
        import random
        default_contents = [
            "今天心情不错呢~",
            "分享一下今天的小心情",
            "生活总是充满惊喜",
            "又是美好的一天",
            "想和大家分享一些想法",
            "今天有点特别的感觉",
            "心情很好，想说点什么",
            "日常的小确幸"
        ]
        return True, random.choice(default_contents)

def load_character_prompt(character_name):
    """读取角色设定文件"""
    try:
        prompt_file = os.path.join('prompts', f'{character_name}.md')
        if os.path.exists(prompt_file):
            with open(prompt_file, 'r', encoding='utf-8') as f:
                return f.read()
        return None
    except Exception as e:
        app.logger.error(f"读取角色设定失败: {e}")
        return None

def get_character_forum_posts(character_name, limit=20):
    """获取角色论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        if not os.path.exists(forum_file):
            return []
        
        with open(forum_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        posts = data.get('posts', [])
        # 按时间倒序排列
        posts.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        
        return posts[:limit] if limit else posts
    
    except Exception as e:
        app.logger.error(f"获取论坛帖子失败: {e}")
        return []

def _forum_file_path(character_name):
    _ensure_forum_dir_exists()
    filename = f'forum_data_{character_name}.json'
    return os.path.join(FORUM_DATA_DIR, filename)

def load_forum_data(character_name):
    """加载论坛数据文件"""
    forum_file = _forum_file_path(character_name)
    if os.path.exists(forum_file):
        try:
            with open(forum_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {'posts': [], 'npcs': []}
    return {'posts': [], 'npcs': []}

def save_forum_data(character_name, data):
    """保存论坛数据文件（覆盖写入）"""
    forum_file = _forum_file_path(character_name)
    with open(forum_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _find_post_by_id(posts, post_id):
    for post in posts:
        if post.get('id') == post_id:
            return post
    return None

def toggle_like_forum_post(character_name, post_id, like_state=None):
    """切换或设置帖子点赞状态。like_state=None 表示切换；True/False 表示显式设置"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    current_liked = bool(post.get('liked_by_me', False))
    new_liked = (not current_liked) if like_state is None else bool(like_state)
    likes_count = int(post.get('likes', 0) or 0)
    if new_liked != current_liked:
        if new_liked:
            likes_count += 1
        else:
            likes_count = max(0, likes_count - 1)
        post['likes'] = likes_count
        post['liked_by_me'] = new_liked
        save_forum_data(character_name, data)
    return {'likes': post.get('likes', 0), 'liked_by_me': post.get('liked_by_me', False)}

def add_forum_comment(character_name, post_id, content, author_display_name=None):
    """为指定帖子添加一条评论。返回新评论对象。"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    if 'comments' not in post or not isinstance(post['comments'], list):
        post['comments'] = []
    now = datetime.now()
    comment = {
        'id': f"comment_{int(now.timestamp()*1000)}",
        'npc_name': author_display_name or f"{character_name} 的小号",
        'content': content,
        'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
        'author': 'self'
    }
    post['comments'].append(comment)
    save_forum_data(character_name, data)
    return comment

def ensure_comment_ids_in_post(post):
    """为旧数据中的评论补充id字段"""
    if not post or 'comments' not in post:
        return
    for c in post.get('comments', []):
        if 'id' not in c:
            # 以时间戳或随机数填补
            c['id'] = f"comment_{int(time.time()*1000)}_{random.randint(100,999)}"

def add_forum_comment_with_parent(character_name, post_id, content, author, author_display, parent_comment_id=None):
    """为帖子添加一条评论，支持父评论。author: 'me' 或 'character'"""
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    post = _find_post_by_id(posts, post_id)
    if not post:
        return None
    if 'comments' not in post or not isinstance(post['comments'], list):
        post['comments'] = []
    ensure_comment_ids_in_post(post)
    now = datetime.now()
    comment = {
        'id': f"comment_{int(now.timestamp()*1000)}",
        'npc_name': author_display,
        'content': content,
        'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
        'author': author,  # 'me' 或 'character'
    }
    if parent_comment_id:
        comment['parent_id'] = parent_comment_id
    post['comments'].append(comment)
    save_forum_data(character_name, data)
    return comment

def generate_character_conversation_reply(character_name, post_content, user_message):
    """基于角色设定，生成角色对用户的回复"""
    try:
        config = parse_config()
        character_prompt = load_character_prompt(character_name) or ''
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        prompt = f"""
你是“{character_name}”，以下是你的角色设定：
{character_prompt}

场景：你的小号在社交平台发了一条动态，下面有一条来自用户的回复。请以你的人设与语气，回复用户的这条话。

原始动态：{post_content}
用户的话：{user_message}

要求：
1. 中文回复，口语化，贴近真实社交平台风格
2. 35字以内，尽量一到两句
3. 可以自然使用emoji但不要太多
4. 直接输出回复文本，不要任何前后缀
"""

        # 优先使用论坛自定义模型
        use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))
        if use_forum_custom:
            api_key = (config.get('FORUM_API_KEY') or config.get('DEEPSEEK_API_KEY', '')).strip()
            base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1'))
            model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
            temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.0))
            max_tokens = min(int(config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000))), 120)
        else:
            api_key = config.get('DEEPSEEK_API_KEY', '')
            base_url = config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1')
            model = config.get('MODEL', 'deepseek-v3-0324')
            temperature = config.get('TEMPERATURE', 1.0)
            max_tokens = min(config.get('MAX_TOKEN', 2000), 120)

        if not api_key:
            return "收到啦～"

        client = openai.OpenAI(base_url=base_url, api_key=api_key, timeout=20)
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=20
        )
        reply = completion.choices[0].message.content.strip()
        if not reply:
            reply = "收到啦～"
        # 基础清理
        if reply.startswith("回复：") or reply.startswith("回复:"):
            reply = reply.split("：", 1)[-1].split(":", 1)[-1].strip()
        reply = reply.replace("\\", "\n").strip()
        return reply[:100]
    except Exception as e:
        app.logger.error(f"生成角色会话回复失败: {e}")
        return "了解！"

def get_liked_forum_posts(character_name):
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    liked = [p for p in posts if p.get('liked_by_me')]
    # 保持与其他接口一致：按时间倒序
    try:
        liked.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    except Exception:
        pass
    return liked

def get_thread_posts(character_name):
    data = load_forum_data(character_name)
    posts = data.get('posts', [])
    threads = [p for p in posts if p.get('comments') and isinstance(p.get('comments'), list) and len(p.get('comments')) > 0]
    try:
        threads.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
    except Exception:
        pass
    return threads

@app.route('/api/forum/like/<character_name>/<post_id>', methods=['POST'])
@login_required
def like_forum_post(character_name, post_id):
    """点赞/取消点赞 指定帖子。请求体可包含 like: true/false；不含则为切换。"""
    try:
        like_state = None
        if request.is_json and isinstance(request.json, dict) and 'like' in request.json:
            like_state = bool(request.json.get('like'))
        result = toggle_like_forum_post(character_name, post_id, like_state)
        if not result:
            return jsonify({'error': '帖子不存在'}), 404
        return jsonify(result)
    except Exception as e:
        app.logger.error(f"点赞操作失败: {e}")
        return jsonify({'error': f'点赞失败: {str(e)}'}), 500

@app.route('/api/forum/likes/<character_name>')
@login_required
def get_likes(character_name):
    """获取我喜欢的帖子列表"""
    try:
        posts = get_liked_forum_posts(character_name)
        return jsonify({'posts': posts})
    except Exception as e:
        app.logger.error(f"获取喜欢列表失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/reply/<character_name>/<post_id>', methods=['POST'])
@login_required
def reply_to_post(character_name, post_id):
    """对帖子进行回复（添加一条评论），并自动生成角色的AI回复"""
    try:
        if not request.is_json:
            return jsonify({'error': '请求格式错误'}), 400
        content = str(request.json.get('content', '')).strip()
        parent_comment_id = request.json.get('parent_comment_id')
        if not content:
            return jsonify({'error': '回复内容不能为空'}), 400
        if len(content) > 500:
            return jsonify({'error': '回复内容过长（最多500字）'}), 400
        # 加载帖子用于上下文
        data = load_forum_data(character_name)
        post = _find_post_by_id(data.get('posts', []), post_id)
        if not post:
            return jsonify({'error': '帖子不存在，无法回复'}), 404

        ensure_comment_ids_in_post(post)

        # 先写入用户的评论
        my_comment = add_forum_comment_with_parent(
            character_name=character_name,
            post_id=post_id,
            content=content,
            author='me',
            author_display='我',
            parent_comment_id=parent_comment_id
        )

        # 生成角色AI回复
        ai_reply_text = generate_character_conversation_reply(
            character_name, post.get('content', ''), content
        )
        ai_comment = add_forum_comment_with_parent(
            character_name=character_name,
            post_id=post_id,
            content=ai_reply_text,
            author='character',
            author_display=f"{character_name} 的小号",
            parent_comment_id=my_comment['id']
        )

        return jsonify({'my_comment': my_comment, 'ai_comment': ai_comment})
    except Exception as e:
        app.logger.error(f"添加回复失败: {e}")
        return jsonify({'error': f'添加回复失败: {str(e)}'}), 500

@app.route('/api/forum/threads/<character_name>')
@login_required
def get_forum_threads(character_name):
    """获取包含回复的帖子列表（用于“推文和回复”）"""
    try:
        posts = get_thread_posts(character_name)
        # 为兼容旧数据，补全评论id
        for p in posts:
            ensure_comment_ids_in_post(p)
        return jsonify({'posts': posts})
    except Exception as e:
        app.logger.error(f"获取线程列表失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

# ===== 喜欢动态（AI生成） =====
def generate_likes_feed_items(character_name, use_online=False):
    """生成角色会喜欢的外部博文（AI生成，最多3条）。
    当启用联网模型时：先用联网模型检索“今日热点摘要”，再把摘要交给论坛模型（若启用）或主模型生成三条博文。
    """
    try:
        config = parse_config()
        character_prompt = load_character_prompt(character_name) or ''
        time_str = datetime.now().strftime("%Y-%m-%d %H:%M")

        # --- 阶段一：可选的联网热点检索 ---
        online_hot_brief = ''
        if use_online and bool(config.get('ENABLE_ONLINE_API', False)):
            online_api_key = (config.get('ONLINE_API_KEY') or '').strip()
            online_base_url = config.get('ONLINE_BASE_URL', 'https://vg.v1api.cc/v1')
            online_model = config.get('ONLINE_MODEL', 'net-gpt-4o-mini')
            online_temperature = float(config.get('ONLINE_API_TEMPERATURE', 0.7))
            online_max_tokens = int(config.get('ONLINE_API_MAX_TOKEN', 2000))

            if online_api_key:
                try:
                    online_prompt = (
                        "请用简洁要点汇总'今天'中文互联网主要新闻与热点（5-8条），不要涉及政治敏感话题和政治人物"
                        "偏向话题与趋势，不要细节长文；每条不超过30字；"
                        "只输出纯文本多行，不要编号、不要任何额外解释。"
                    )
                    online_client = openai.OpenAI(base_url=online_base_url, api_key=online_api_key, timeout=20)
                    online_completion = online_client.chat.completions.create(
                        model=online_model,
                        messages=[{"role": "user", "content": online_prompt}],
                        temperature=online_temperature,
                        max_tokens=min(online_max_tokens, 500),
                        timeout=20
                    )
                    online_hot_brief = (online_completion.choices[0].message.content or '').strip()
                    # 只保留前8行，避免上下文过长
                    if online_hot_brief:
                        lines_hot = [ln.strip() for ln in online_hot_brief.split('\n') if ln.strip()]
                        online_hot_brief = "\n".join(lines_hot[:8])
                except Exception as online_err:
                    app.logger.warning(f"联网热点检索失败，将回退本地生成: {online_err}")
                    online_hot_brief = ''

        # --- 阶段二：用论坛模型（若启用）或主模型生成三条内容 ---
        # 选择生成阶段所用模型
        gen_base_url = config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1')
        gen_model = config.get('MODEL', 'deepseek-v3-0324')
        gen_api_key = (config.get('DEEPSEEK_API_KEY', '')).strip()
        gen_temperature = float(config.get('TEMPERATURE', 1.0))
        gen_max_tokens = min(int(config.get('MAX_TOKEN', 2000)), 400)

        if bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False)):
            gen_base_url = config.get('FORUM_BASE_URL', gen_base_url)
            gen_model = (config.get('FORUM_MODEL') or gen_model)
            gen_api_key = (config.get('FORUM_API_KEY') or gen_api_key).strip()
            gen_temperature = float(config.get('FORUM_TEMPERATURE', gen_temperature))
            gen_max_tokens = min(int(config.get('FORUM_MAX_TOKEN', gen_max_tokens)), 400)

        if not gen_api_key:
            # 无可用key，返回一些占位
            return [
                {'author': '热榜·话题', 'content': '今天的小确幸：晚霞好看到想停下脚步 #日常', 'timestamp': time_str},
                {'author': '城市·夜跑', 'content': '月亮今晚很好看，风也温柔，继续跑下去吧', 'timestamp': time_str},
            ]

        # 组合最终生成提示词
        if online_hot_brief:
            final_prompt = f"""
你在做内容聚合，给角色“{character_name}”挑选他会喜欢的3条中文博文（每条不超过60字）。
角色设定：
{character_prompt}

今日热点（仅作参考，避免机械复述，可融合相关话题与氛围）：
{online_hot_brief}

请严格按以下要求输出：
1) 仅输出三行内容，不要任何额外说明或序号
2) 每行格式：作者名 - 文本内容（可含#话题）
3) 不要使用反斜线 \\, 不要用 \n 或 \\ 分隔，直接正常标点与空格
4) 不要使用引号或代码块
"""
        else:
            final_prompt = f"""
你在做内容聚合，给角色“{character_name}”挑选他会喜欢的3条中文博文（每条不超过60字）。
角色设定：
{character_prompt}

如果具备联网能力，请结合当日热门趋势或资讯做简洁融合；否则输出贴合其性格与日常审美的生活化内容。

请严格按以下要求输出：
1) 仅输出三行内容，不要任何额外说明或序号
2) 每行格式：作者名 - 文本内容（可含#话题）
3) 不要使用反斜线 \\, 不要用 \n 或 \\ 分隔，直接正常标点与空格
4) 不要使用引号或代码块
"""

        gen_client = openai.OpenAI(base_url=gen_base_url, api_key=gen_api_key, timeout=25)
        gen_completion = gen_client.chat.completions.create(
            model=gen_model,
            messages=[{"role": "user", "content": final_prompt}],
            temperature=gen_temperature,
            max_tokens=gen_max_tokens,
            timeout=25
        )
        reply = (gen_completion.choices[0].message.content or '').strip()

        lines = [ln.strip() for ln in reply.split('\n') if ln.strip()]
        items = []
        for ln in lines[:3]:
            # 解析“作者 - 内容”
            if ' - ' in ln:
                author, content = ln.split(' - ', 1)
            elif '-' in ln:
                author, content = ln.split('-', 1)
            else:
                author, content = '博主', ln
            # 清理反斜线，保持单行
            safe_content = content.replace('\\', '').strip()
            items.append({
                'author': author.strip()[:20],
                'content': safe_content,
                'timestamp': time_str,
                'likes': random.randint(20, 500),
                'replies': random.randint(0, 120)
            })
        if not items:
            items = [{
                'author': '博主',
                'content': reply.replace('\\', '')[:60],
                'timestamp': time_str,
                'likes': random.randint(20, 500),
                'replies': random.randint(0, 120)
            }]
        return items
    except Exception as e:
        app.logger.error(f"生成喜欢Feed失败: {e}")
        return []

@app.route('/api/forum/likes_feed/<character_name>')
@login_required
def get_likes_feed(character_name):
    try:
        data = load_forum_data(character_name)
        feed = data.get('liked_feed', [])
        # 补齐缺失的统计字段，便于老数据兼容
        changed = False
        for it in feed:
            if 'likes' not in it:
                it['likes'] = random.randint(20, 500)
                changed = True
            if 'replies' not in it:
                it['replies'] = random.randint(0, 120)
                changed = True
        if changed:
            save_forum_data(character_name, data)
        # 倒序（最新在前）
        try:
            feed.sort(key=lambda x: x.get('timestamp', ''), reverse=True)
        except Exception:
            pass
        return jsonify({'items': feed})
    except Exception as e:
        app.logger.error(f"获取喜欢Feed失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/refresh_likes/<character_name>', methods=['POST'])
@login_required
def refresh_likes_feed(character_name):
    try:
        config = parse_config()
        use_online = bool(config.get('ENABLE_ONLINE_API', False))
        items = generate_likes_feed_items(character_name, use_online=use_online)
        if not items:
            return jsonify({'has_new_content': False, 'message': '暂时没有新的喜欢'}), 200
        # 存储
        data = load_forum_data(character_name)
        if 'liked_feed' not in data or not isinstance(data['liked_feed'], list):
            data['liked_feed'] = []
        now_ts = int(time.time()*1000)
        for idx, it in enumerate(items):
            it['id'] = f"liked_{now_ts}_{idx}"
            if 'likes' not in it:
                it['likes'] = random.randint(20, 500)
            if 'replies' not in it:
                it['replies'] = random.randint(0, 120)
            data['liked_feed'].append(it)
        save_forum_data(character_name, data)
        return jsonify({'has_new_content': True, 'items': items})
    except Exception as e:
        app.logger.error(f"刷新喜欢Feed失败: {e}")
        return jsonify({'error': f'刷新失败: {str(e)}'}), 500

@app.route('/api/forum/likes_feed/<character_name>/<item_id>', methods=['DELETE'])
@login_required
def delete_likes_feed_item(character_name, item_id):
    """删除AI生成的喜欢Feed中的一项，不影响论坛原帖"""
    try:
        data = load_forum_data(character_name)
        feed = data.get('liked_feed', [])
        original_len = len(feed)
        feed = [it for it in feed if str(it.get('id')) != str(item_id)]
        if len(feed) == original_len:
            return jsonify({'error': '未找到该喜欢项'}), 404
        data['liked_feed'] = feed
        save_forum_data(character_name, data)
        return jsonify({'message': '已移除'}), 200
    except Exception as e:
        app.logger.error(f"删除喜欢Feed项失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

# ===== 头像管理API =====
@app.route('/api/forum/avatar/upload/<character_name>', methods=['POST'])
@login_required
def upload_avatar(character_name):
    """上传角色头像"""
    try:
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        if 'avatar' not in request.files:
            return jsonify({'error': '没有上传文件'}), 400
        
        file = request.files['avatar']
        if file.filename == '':
            return jsonify({'error': '没有选择文件'}), 400
        
        if not allowed_avatar_file(file.filename):
            return jsonify({'error': '不支持的文件格式，请上传PNG、JPG、JPEG、GIF或WebP格式的图片'}), 400
        
        # 删除旧的头像文件
        old_avatar_path = get_avatar_file_path(character_name)
        if old_avatar_path and os.path.exists(old_avatar_path):
            try:
                os.remove(old_avatar_path)
                app.logger.info(f"删除旧头像文件: {old_avatar_path}")
            except Exception as e:
                app.logger.warning(f"删除旧头像文件失败: {e}")
        
        # 保存新的头像文件
        file_extension = file.filename.rsplit('.', 1)[1].lower()
        filename = get_avatar_filename(character_name, file_extension)
        filepath = os.path.join(FORUM_AVATAR_DIR, filename)
        
        file.save(filepath)
        app.logger.info(f"头像上传成功: {filepath}")
        
        return jsonify({
            'message': '头像上传成功',
            'filename': filename
        })
    
    except Exception as e:
        app.logger.error(f"头像上传失败: {e}")
        return jsonify({'error': f'上传失败: {str(e)}'}), 500

@app.route('/api/forum/avatar/<character_name>')
@login_required
def get_avatar(character_name):
    """获取角色头像"""
    try:
        avatar_path = get_avatar_file_path(character_name)
        if not avatar_path:
            return jsonify({'error': '头像不存在'}), 404
        
        return send_file(avatar_path)
    
    except Exception as e:
        app.logger.error(f"获取头像失败: {e}")
        return jsonify({'error': f'获取失败: {str(e)}'}), 500

@app.route('/api/forum/avatar/<character_name>', methods=['DELETE'])
@login_required
def delete_avatar(character_name):
    """删除角色头像"""
    try:
        # 验证角色名
        config = parse_config()
        listen_list = config.get('LISTEN_LIST', [])
        character_found = False
        for item in listen_list:
            if len(item) >= 2 and item[1] == character_name:
                character_found = True
                break
        
        if not character_found:
            return jsonify({'error': '角色不存在'}), 404
        
        avatar_path = get_avatar_file_path(character_name)
        if not avatar_path:
            return jsonify({'error': '头像不存在'}), 404
        
        os.remove(avatar_path)
        app.logger.info(f"头像删除成功: {avatar_path}")
        
        return jsonify({'message': '头像删除成功'})
    
    except Exception as e:
        app.logger.error(f"头像删除失败: {e}")
        return jsonify({'error': f'删除失败: {str(e)}'}), 500

def add_forum_post(character_name, content):
    """添加新的论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        
        # 读取现有数据
        if os.path.exists(forum_file):
            with open(forum_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
        else:
            data = {'posts': [], 'npcs': []}
        
        # 创建新帖子
        now = datetime.now()
        new_post = {
            'id': f"post_{int(now.timestamp())}",
            'content': content,
            'timestamp': now.strftime("%Y-%m-%d %H:%M:%S"),
            'likes': random.randint(5, 50),
            'comments': []
        }
        
        # 生成NPC评论：在生成函数内部对每个NPC进行独立概率判定
        npc_comments = generate_npc_comments(character_name, content)
        new_post['comments'] = npc_comments
        
        # 添加到数据中
        data['posts'].append(new_post)
        
        # 保持最多100条帖子
        if len(data['posts']) > 100:
            data['posts'] = data['posts'][-100:]
        
        # 保存数据
        with open(forum_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        return new_post
    
    except Exception as e:
        app.logger.error(f"添加论坛帖子失败: {e}")
        return None

def generate_npc_comments(character_name, post_content):
    """生成NPC评论 - 基于用户配置的NPC设定和回复风格"""
    try:
        # 从角色设定中提取可能的NPC名单
        character_prompt = load_character_prompt(character_name)
        
        # 尝试从角色设定中提取人物名称
        extracted_names = []
        if character_prompt:
            import re
            # 查找可能的人名（中文姓名模式）
            name_patterns = [
                r'([小]\w{1,2})',  # 小桃、小明等
                r'([李王张刘陈杨黄赵吴周徐孙朱马胡郭何高林罗郑梁谢宋唐许邓冯韩曹曾彭肖蔡潘田董袁于余叶蒋杜苏魏程吕丁沈任姚卢姜崔钟谭陆汪范金石廖贾夏韦傅方白邹孟熊秦邱江尹薛闫段雷侯龙史陶黎贺顾毛郝龚邵万钱严覃武戴莫孔向汤]\w{1,2})',  # 常见姓氏
                r'(\w{2,3}(?:同学|朋友|室友|同事))',  # XX同学、XX朋友等
            ]
            
            for pattern in name_patterns:
                matches = re.findall(pattern, character_prompt)
                extracted_names.extend(matches)
        

        # 合并名单，优先使用从角色设定中提取的名称
        all_names = list(set(extracted_names ))
        
        # 尝试读取用户配置的NPC设定
        npc_config = load_npc_config()
        configured_npcs = []
        
        if npc_config and 'selected_npcs' in npc_config:
            for npc_name in npc_config['selected_npcs']:
                if npc_name in npc_config.get('npc_settings', {}):
                    npc_settings = npc_config['npc_settings'][npc_name]
                    configured_npcs.append({
                        'name': npc_name,
                        'settings': npc_settings
                    })
        
        # 只使用用户配置的NPC生成智能回复
        if configured_npcs:
            app.logger.info(f"使用配置的NPC生成智能回复: {[npc['name'] for npc in configured_npcs]}")
            return generate_configured_npc_comments(configured_npcs, post_content, character_name)
        
        # 如果没有配置NPC，返回空列表
        app.logger.info("未配置NPC，不生成评论")
        return []
        
    except Exception as e:
        app.logger.error(f"生成NPC评论失败: {e}")
        return []

def generate_configured_npc_comments(configured_npcs, post_content, character_name):
    """基于用户配置生成智能NPC评论（逐条串行，每个NPC独立60%概率）"""
    try:
        comments = []
        reply_probability = 0.6  # 每个NPC独立回复概率

        # 逐条串行生成，避免并行导致的潜在超时/限速问题
        for npc in configured_npcs:
            # 概率判定：每个NPC独立60%概率回复
            if random.random() >= reply_probability:
                continue

            npc_name = npc['name']
            npc_settings = npc['settings']

            # 基于配置生成智能回复（串行调用）
            comment_text = generate_smart_npc_reply(
                npc_name,
                npc_settings,
                post_content,
                character_name
            )

            if comment_text:
                # 随机延迟时间
                base_time = datetime.now()
                delay_minutes = random.randint(2, 120)  # 2分钟到2小时
                comment_time = base_time + timedelta(minutes=delay_minutes)

                comments.append({
                    'npc_name': npc_name,
                    'content': comment_text,
                    'timestamp': comment_time.strftime("%Y-%m-%d %H:%M:%S")
                })

        return comments

    except Exception as e:
        app.logger.error(f"生成配置NPC评论失败: {e}")
        return []

def generate_smart_npc_reply(npc_name, npc_settings, post_content, character_name):
    """基于NPC配置生成智能回复"""
    try:
        # 构建AI提示词
        language_style = npc_settings.get('language_style', 'casual')
        relationship = npc_settings.get('relationship', 'friend')
        example_output = npc_settings.get('example_output', '')
        other_settings = npc_settings.get('other_settings', '')
        
        # 语言风格映射
        style_descriptions = {
            'formal': '正式、礼貌、得体',
            'casual': '随意、轻松、日常',
            'friendly': '友好、温暖、亲切',
            'professional': '专业、严谨、有见地',
            'humorous': '幽默、有趣、轻松',
            'serious': '严肃、认真、深思熟虑'
        }
        
        # 关系映射
        relationship_descriptions = {
            'friend': '朋友关系，可以开玩笑、分享感受',
            'colleague': '同事关系，保持专业但友好',
            'family': '家人关系，关心、温暖、支持',
            'stranger': '陌生人关系，礼貌、适度',
            'student': '学生关系，学习、请教、尊重'
        }
        
        prompt = f"""
你是一个名为"{npc_name}"的NPC，正在回复"{character_name}"的社交动态。

NPC设定：
- 语言风格：{style_descriptions.get(language_style, '随意')}
- 与{character_name}的关系：{relationship_descriptions.get(relationship, '朋友')}
- 示例回复风格：{example_output if example_output else '根据语言风格和关系自然回复'}
- 其他要求：{other_settings if other_settings else '无特殊要求'}

{character_name}的动态内容：
{post_content}

请根据你的NPC设定，生成一条符合你性格和关系的回复。要求：
1. 回复要自然、真实，符合你的语言风格
2. 体现你与{character_name}的关系
3. 回复长度适中,20字以内
4. 可以表达共鸣、关心、建议等情感
5. 不要过于复杂或冗长
6. 此次输出按一句或者一段话话，正常标点符号，不需要加反斜线
7. 如果人物比较活泼，可以适当跟据氛围加入emoji如😥🤣👂👍👎👊👏👌👍👎👊👏👌

直接输出回复内容，不需要引号或其他格式。
"""
        
        # 根据配置选择论坛自定义模型或主模型
        try:
            config = parse_config()
            use_forum_custom = bool(config.get('ENABLE_FORUM_CUSTOM_MODEL', False))

            if use_forum_custom:
                api_key = (config.get('FORUM_API_KEY') or config.get('DEEPSEEK_API_KEY', '')).strip()
                base_url = config.get('FORUM_BASE_URL', config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1'))
                model = (config.get('FORUM_MODEL') or config.get('MODEL', 'deepseek-v3-0324'))
                temperature = config.get('FORUM_TEMPERATURE', config.get('TEMPERATURE', 1.1))
                max_tokens = config.get('FORUM_MAX_TOKEN', config.get('MAX_TOKEN', 2000))
            else:
                api_key = config.get('DEEPSEEK_API_KEY', '')
                base_url = config.get('DEEPSEEK_BASE_URL', 'https://vg.v1api.cc/v1')
                model = config.get('MODEL', 'deepseek-v3-0324')
                temperature = config.get('TEMPERATURE', 1.1)
                max_tokens = config.get('MAX_TOKEN', 2000)
        except Exception as e:
            app.logger.error(f"获取模型配置失败: {e}")
            return generate_fallback_reply(npc_name, language_style, relationship)
        
        if not api_key:
            app.logger.warning(f"API密钥未配置，使用默认回复")
            return generate_fallback_reply(npc_name, language_style, relationship)
        
        client = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=30  # 提高超时阈值，减少长尾超时
        )
        
        completion = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": prompt
            }],
            temperature=temperature,
            max_tokens=min(max_tokens, 120),  # 按20字回复需求收紧，降低超时概率
            timeout=30
        )
        
        reply = completion.choices[0].message.content.strip()
        
        # 清理回复内容
        if reply and len(reply) > 3:
            # 移除可能的格式标记
            reply = reply.replace('"', '').replace('"', '').replace(''', '').replace(''', '')
            reply = reply.replace('回复：', '').replace('回复:', '').replace('NPC回复：', '').replace('NPC回复:', '')
            
            # 自动替换反斜线为换行符
            reply = reply.replace("\\", "\n")
            
            reply = reply.strip()
            
            if len(reply) > 3:
                return reply
        
        # 如果AI回复失败，使用备用回复
        return generate_fallback_reply(npc_name, language_style, relationship)
        
    except Exception as e:
        app.logger.error(f"生成智能NPC回复失败: {e}")
        return generate_fallback_reply(npc_name, 'casual', 'friend')

def generate_fallback_reply(npc_name, language_style, relationship):
    """生成备用回复（当AI调用失败时使用）"""
    try:
        # 基于语言风格和关系的备用回复模板
        fallback_templates = {
            'formal': {
                'friend': ['确实如此', '我理解你的感受', '说得很有道理'],
                'colleague': ['这个观点不错', '值得思考', '有见地'],
                'family': ['我支持你', '相信你能行', '为你感到骄傲'],
                'stranger': ['很有意思', '谢谢分享', '学到了'],
                'mentor': ['很好的想法', '继续努力', '保持这种状态'],
                'student': ['学到了', '谢谢指导', '我会努力的']
            },
            'casual': {
                'friend': ['哈哈哈哈哈', '这个我懂', '真实', '好可爱wwww'],
                'colleague': ['赞同', '同感+1', '确实如此', '支持'],
                'family': ['加油鸭', '支持！', '理解理解', '这就是你'],
                'stranger': ['有意思', '谢谢分享', '学到了', '不错'],
                'mentor': ['很好', '继续', '保持', '不错'],
                'student': ['谢谢', '学到了', '好的', '明白']
            },
            'friendly': {
                'friend': ['好可爱wwww', '理解理解', '加油鸭', '支持！'],
                'colleague': ['赞同', '同感+1', '确实如此', '支持'],
                'family': ['我支持你', '相信你能行', '为你感到骄傲'],
                'stranger': ['很有意思', '谢谢分享', '学到了'],
                'mentor': ['很好的想法', '继续努力', '保持这种状态'],
                'student': ['学到了', '谢谢指导', '我会努力的']
            }
        }
        
        # 获取对应的模板
        templates = fallback_templates.get(language_style, fallback_templates['casual'])
        relationship_templates = templates.get(relationship, templates['friend'])
        
        # 随机选择回复
        return random.choice(relationship_templates)
        
    except Exception as e:
        app.logger.error(f"生成备用回复失败: {e}")
        return "赞同"



def load_npc_config():
    """从配置文件加载NPC配置"""
    try:
        npc_config_file = _npc_config_file_path()
        
        if not os.path.exists(npc_config_file):
            app.logger.info("NPC配置文件不存在")
            return None
        
        with open(npc_config_file, 'r', encoding='utf-8') as f:
            config_data = json.load(f)
        
        # 确保配置格式完整
        if 'selected_npcs' not in config_data:
            config_data['selected_npcs'] = []
        if 'npc_settings' not in config_data:
            config_data['npc_settings'] = {}
        
        # 为每个选中的NPC确保有默认设置
        for npc_name in config_data.get('selected_npcs', []):
            if npc_name not in config_data.get('npc_settings', {}):
                config_data['npc_settings'][npc_name] = {
                    'language_style': 'casual',
                    'relationship': 'friend',
                    'example_output': '',
                    'other_settings': ''
                }
                app.logger.info(f"为NPC {npc_name} 添加默认设置")
            else:
                app.logger.info(f"NPC {npc_name} 保持原有设置")
        
        app.logger.info(f"成功加载NPC配置: {config_data}")
        return config_data
        
    except Exception as e:
        app.logger.error(f"加载NPC配置失败: {e}")
        return None

def get_default_config():
    return {
        "LISTEN_LIST": [['微信名1', '角色1']],
        "DEEPSEEK_API_KEY": '',
        "DEEPSEEK_BASE_URL": 'https://vg.v1api.cc/v1',
        "MODEL": 'deepseek-v3-0324',
        "MAX_GROUPS": 5,
        "MAX_TOKEN": 2000,
        "TEMPERATURE": 1.1,
        "MOONSHOT_API_KEY": '',
        "MOONSHOT_BASE_URL": 'https://vg.v1api.cc/v1',
        "MOONSHOT_MODEL": 'gpt-4o',
        "MOONSHOT_TEMPERATURE": 0.8,
        "ENABLE_IMAGE_RECOGNITION": True,
        "ENABLE_EMOJI_RECOGNITION": True,
        "QUEUE_WAITING_TIME": 7,
        "EMOJI_DIR": 'emojis',
        "ENABLE_EMOJI_SENDING": True,
        "EMOJI_SENDING_PROBABILITY": 25,
        "AUTO_MESSAGE": '请你模拟系统设置的角色，在微信上找对方继续刚刚的话题或者询问对方在做什么',
        "ENABLE_AUTO_MESSAGE": True,
        "MIN_COUNTDOWN_HOURS": 1.0,
        "MAX_COUNTDOWN_HOURS": 2.0,
        "QUIET_TIME_START": '22:00',
        "QUIET_TIME_END": '8:00',
        "AVERAGE_TYPING_SPEED": 0.2,
        "RANDOM_TYPING_SPEED_MIN": 0.05,
        "RANDOM_TYPING_SPEED_MAX": 0.1,
        "SEPARATE_ROW_SYMBOLS": True,
        "ENABLE_MEMORY": True,
        "MEMORY_TEMP_DIR": 'Memory_Temp',
        "MAX_MESSAGE_LOG_ENTRIES": 30,
        "MAX_MEMORY_NUMBER": 50,
        "UPLOAD_MEMORY_TO_AI": True,
        "ACCEPT_ALL_GROUP_CHAT_MESSAGES": False,
        "ENABLE_GROUP_AT_REPLY": True,
        "ENABLE_GROUP_KEYWORD_REPLY": False,
        "GROUP_KEYWORD_LIST": ['你好', '机器人', '在吗'],
        "GROUP_CHAT_RESPONSE_PROBABILITY": 100,
        "GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY": True,
        "ALLOW_OPEN_PORT": False,
        "LOGIN_PASSWORD": '123456',
        "PASSWORD_IS_VALID": False,
        "PORT": 5000,
        "ENABLE_REMINDERS": True,
        "ALLOW_REMINDERS_IN_QUIET_TIME": True,
        "USE_VOICE_CALL_FOR_REMINDERS": False,
        "ENABLE_ONLINE_API": False,
        "ONLINE_BASE_URL": 'https://vg.v1api.cc/v1',
        "ONLINE_MODEL": 'net-gpt-4o-mini',
        "ONLINE_API_KEY": '',
        "ONLINE_API_TEMPERATURE": 0.7,
        "ONLINE_API_MAX_TOKEN": 2000,
        "SEARCH_DETECTION_PROMPT": '是否需要查询今天的天气、最新的新闻事件、特定网站的内容、股票价格、特定人物的最新动态等',
        "ONLINE_FIXED_PROMPT": '',
        "ENABLE_URL_FETCHING": True,
        "REQUESTS_TIMEOUT": 10,
        "REQUESTS_USER_AGENT": 'Mozilla/5.0 (Linux; Android 10; SM-G975F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Mobile Safari/537.36',
        "MAX_WEB_CONTENT_LENGTH": 2000,
        "ENABLE_SCHEDULED_RESTART": True,
        "RESTART_INTERVAL_HOURS": 2.0,
        "RESTART_INACTIVITY_MINUTES": 15,
        "REMOVE_PARENTHESES": False,
        "ENABLE_ASSISTANT_MODEL": False,
        "ASSISTANT_BASE_URL": 'https://vg.v1api.cc/v1',
        "ASSISTANT_MODEL": 'gpt-4o-mini',
        "ASSISTANT_API_KEY": '',
        "ASSISTANT_TEMPERATURE": 0.3,
        "ASSISTANT_MAX_TOKEN": 1000,
        "USE_ASSISTANT_FOR_MEMORY_SUMMARY": False,
        "IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE": False,
        "ENABLE_SENSITIVE_CONTENT_CLEARING": True,
        "SAVE_MEMORY_TO_SEPARATE_FILE": True,
        "CORE_MEMORY_DIR": 'CoreMemory',
        "ENABLE_TEXT_COMMANDS": True,
        "ENABLE_FORUM_CUSTOM_MODEL": False,
        "FORUM_BASE_URL": 'https://vg.v1api.cc/v1',
        "FORUM_MODEL": '',
        "FORUM_API_KEY": '',
        "FORUM_TEMPERATURE": 1.0,
        "FORUM_MAX_TOKEN": 1200
    }

def validate_config():
    """验证config.py配置完整性，若有缺失项则自动补充默认值"""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')
    
    try:
        # 如果配置文件不存在，直接创建完整配置
        if not os.path.exists(config_path):
            print(f"配置文件不存在，正在创建新配置文件: {config_path}")
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write("# -*- coding: utf-8 -*-\n\n")
                f.write("# 自动生成的配置文件\n\n")
                
                for key, value in get_default_config().items():
                    f.write(f"{key} = {repr(value)}\n")
            print("已创建新的配置文件")
            return True
        
        # 尝试解析当前配置
        current_config = parse_config()
        default_config = get_default_config()
        
        # 记录缺少的配置项
        missing_keys = []
        # 构建需要更新的配置字典
        updates_needed = {}
        
        # 检查每个默认配置项是否存在
        for key, default_value in default_config.items():
            if key not in current_config:
                missing_keys.append(key)
                updates_needed[key] = default_value
        
        # 如果存在缺失项，更新配置文件
        if missing_keys:
            print(f"检测到{len(missing_keys)}个缺失的配置项: {', '.join(missing_keys)}")
            print("正在自动补充默认值...")
            
            # 直接修改文件，添加缺失的配置项
            with open(config_path, 'a', encoding='utf-8') as f:
                f.write("\n# 自动补充的配置项\n")
                for key in missing_keys:
                    f.write(f"{key} = {repr(default_config[key])}\n")
            
            print("配置文件已更新完成")
            return True  # 配置已更新
        
        print("配置文件验证完成，所有配置项齐全")
        return False  # 配置无需更新
        
    except Exception as e:
        print(f"验证配置文件时出错: {str(e)}")
        return False
def delete_forum_post_by_id(character_name, post_id):
    """根据ID删除角色的论坛帖子"""
    try:
        forum_file = _forum_file_path(character_name)
        
        if not os.path.exists(forum_file):
            app.logger.warning(f"论坛数据文件不存在: {forum_file}")
            return False
        
        # 读取现有数据
        with open(forum_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        posts = data.get('posts', [])
        
        # 查找并删除指定ID的帖子
        original_count = len(posts)
        posts = [post for post in posts if post.get('id') != post_id]
        
        if len(posts) == original_count:
            app.logger.warning(f"未找到要删除的帖子: {post_id}")
            return False
        
        # 更新数据
        data['posts'] = posts
        
        # 保存数据
        with open(forum_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        app.logger.info(f"成功删除帖子 {post_id}，剩余帖子数量: {len(posts)}")
        return True
        
    except Exception as e:
        app.logger.error(f"删除论坛帖子失败: {e}")
        return False

def is_port_available(port):
    """
    检查指定端口是否可用
    返回 True 表示端口可用，False 表示端口被占用
    """
    try:
        for conn in psutil.net_connections():
            if conn.laddr and conn.laddr.port == port:
                if conn.status in ('LISTEN', 'LISTENING'):
                    return False
        return True
    except Exception as e:
        app.logger.warning(f"检查端口 {port} 可用性时出错: {e}")
        return False

def get_random_available_port(start_port, end_port, max_attempts=50):
    """
    获取指定范围内的随机可用端口
    
    Args:
        start_port: 起始端口
        end_port: 结束端口
        max_attempts: 最大尝试次数
    
    Returns:
        可用的端口号，如果找不到则返回None
    """
    import random
    attempted_ports = set()
    
    for _ in range(max_attempts):
        # 生成随机端口
        port = random.randint(start_port, end_port)
        
        # 避免重复尝试同一个端口
        if port in attempted_ports:
            continue
        attempted_ports.add(port)
        
        # 检查端口是否可用
        if is_port_available(port):
            return port
    
    return None

def kill_process_using_port(port):
    """
    检查指定端口是否被占用，如果被占用则结束占用的进程
    """
    # 遍历所有连接
    for conn in psutil.net_connections():
        # 由于 config 中 PORT 可能为字符串，转换为 int
        if conn.laddr and conn.laddr.port == port:
            # 根据不同平台，监听状态可能不同（Linux一般为 'LISTEN'，Windows为 'LISTENING'）
            if conn.status in ('LISTEN', 'LISTENING'):
                try:
                    proc = psutil.Process(conn.pid)
                    print(f"检测到端口 {port} 被进程 {conn.pid} 占用，尝试结束该进程……")
                    proc.kill()
                    proc.wait(timeout=3)
                    print(f"进程 {conn.pid} 已被成功结束。")
                except Exception as e:
                    print(f"结束进程 {conn.pid} 时出现异常：{e}")

if __name__ == '__main__':
    # 配置应用日志级别
    app.logger.setLevel(logging.INFO)
    
    # 添加控制台处理器确保论坛相关日志显示
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler.setFormatter(formatter)
    app.logger.addHandler(console_handler)
    
    class BotStatusFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            # 允许论坛相关的日志通过
            if '/forum/' in msg or 'forum' in msg.lower():
                return True
            # 如果日志消息中包含以下日志，则返回 False（不记录）
            if '/bot_status' in msg or \
               '/api/log' in msg or \
               '/save_all_reminders' in msg or \
               '/get_all_reminders' in msg or \
               '/api/get_chat_context_users' in msg or \
               '/bot_heartbeat' in msg:
                return False
            return True

    # 获取 werkzeug 的日志记录器并添加过滤器
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addFilter(BotStatusFilter())

    # 验证配置文件完整性
    validate_config()

    # 配置文件存在检查
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"核心配置文件缺失: {config_path}")
    
    config = parse_config()
    PORT = config.get('PORT', '5000')
    
    # 如果端口为默认的5000，则自动修改为5001-5998之间的随机可用端口
    if PORT == 5000 or PORT == '5000':
        print(f"\033[33m检测到使用默认端口 5000，正在自动切换到随机端口...\033[0m")
        new_port = get_random_available_port(5001, 5998)
        
        if new_port:
            print(f"\033[32m已分配新端口: {new_port}\033[0m")
            # 更新配置文件
            try:
                update_config({'PORT': new_port})
                PORT = new_port
                print(f"\033[32m配置文件已更新，新端口: {new_port}\033[0m")
            except Exception as e:
                print(f"\033[31m更新配置文件失败: {e}，将继续使用端口 5000\033[0m")
                PORT = 5000
        else:
            print(f"\033[31m警告: 无法找到5001-5998之间的可用端口，将继续使用端口 5000\033[0m")
            PORT = 5000
    
    # 确保PORT是整数类型
    PORT = int(PORT)

    # 在启动服务器前检查端口是否被占用，若占用则结束该进程
    kill_process_using_port(PORT)

    print(f"\033[31m重要提示：\r\n若您的浏览器没有自动打开网页端，请手动访问 http://localhost:{PORT}/ \r\n \033[0m")
    allow_open_port = config.get('ALLOW_OPEN_PORT', False)
    password_is_valid = config.get('PASSWORD_IS_VALID', False)
    if password_is_valid:
        print(f"\033[32m已启用登录保护：访问网页需输入已设置的密码。\r\n \033[0m")
    else:
        print(f"\033[31m检测到尚未设置登录密码：首次访问将跳转到密码设置页面。\r\n \033[0m")
    if allow_open_port:
        print(f"\033[33m外网访问已开启，请务必妥善保管您的登录密码。\r\n \033[0m")
    
    # 根据配置决定绑定地址
    host = "0.0.0.0" if allow_open_port else "127.0.0.1"
    
    print(f"\033[36m")
    print(f"============================================================")
    print(f"  WeChatBot 配置管理器")
    print(f"监听地址: {host}:{PORT}")
    print(f"访问地址: http://localhost:{PORT}/")
    print(f"============================================================")
    print(f"\033[0m")
    
    # 在启动服务器前设置定时器打开浏览器
    def open_browser():
        webbrowser.open(f'http://localhost:{PORT}/')
    
    Timer(1, open_browser).start()  # 延迟1秒确保服务器已启动
    
    # 使用Waitress生产级WSGI服务器
    serve(
        app, 
        host=host, 
        port=PORT,
        threads=4,              # 线程数
        channel_timeout=60,     # 通道超时
        connection_limit=1000,  # 最大连接数
        cleanup_interval=30,    # 清理间隔
        asyncore_use_poll=True  # 使用poll而不是select（Windows下更稳定）
    )
    