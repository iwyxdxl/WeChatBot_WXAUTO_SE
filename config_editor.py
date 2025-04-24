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

from flask import Flask, render_template, request, redirect, url_for, jsonify, session, Response
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
from flask import Flask
import logging
from queue import Queue, Empty
import time
import json

app = Flask(__name__)
app.secret_key = os.urandom(24).hex()  # 48位十六进制字符串
bot_process = None

# 全局日志队列
log_queue = Queue()

# 新增登录相关路由
@app.route('/login', methods=['GET', 'POST'])
def login():
    # 如果禁用密码则直接跳转
    config = parse_config()
    if not config.get('ENABLE_LOGIN_PASSWORD', False):
        return redirect(url_for('index'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        stored_pwd = config.get('LOGIN_PASSWORD', '')
        
        if password == stored_pwd:
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error="密码错误")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))

def login_required(f):
    @wraps(f)  # 新增装饰器
    def decorated_function(*args, **kwargs):
        config = parse_config()
        if config.get('ENABLE_LOGIN_PASSWORD', False):
            if not session.get('logged_in'):
                return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        bot_dir = os.path.dirname(os.path.abspath(__file__))
        
        # 优先检查bot.py
        bot_py = os.path.join(bot_dir, 'bot.py')
        bot_exe = os.path.join(bot_dir, 'bot.exe')
        
        if os.path.exists(bot_py):
            cmd = ['python', bot_py]
        elif os.path.exists(bot_exe):
            cmd = [bot_exe]
        else:
            return {'error': 'No bot executable found'}, 404

        # Windows需要CREATE_NEW_PROCESS_GROUP
        creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
        bot_process = subprocess.Popen(
            cmd,
            creationflags=creation_flags
        )
    return {'status': 'started'}, 200

    
@app.route('/stop_bot', methods=['POST'])
def stop_bot():
    global bot_process
    if bot_process is None:
        return {'status': 'stopped'}, 200
    else:
        stop_bot_process()
        return {'status': 'stopped'}, 200
    
@app.route('/bot_status')
def bot_status():
    global bot_process
    status = "running" if bot_process and bot_process.poll() is None else "stopped"
    return {"status": status}

@app.route('/submit_config', methods=['POST'])
def submit_config():
    global bot_process
    # 如果 bot 正在运行，则不允许保存配置
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再保存配置'}), 400

    try:
        # 空表单校验
        if not request.form:
            return jsonify({'error': 'Empty form submission'}), 400
        
        config = parse_config()
        new_values = {}

        # 处理二维数组：微信昵称与对应Prompt配置
        nicknames = request.form.getlist('nickname')
        prompt_files = request.form.getlist('prompt_file')
        new_values['LISTEN_LIST'] = [
            [nick.strip(), pf.strip()] 
            for nick, pf in zip(nicknames, prompt_files) 
            if nick.strip() and pf.strip()
        ]

        # 处理布尔字段
        boolean_fields = [
            'ENABLE_IMAGE_RECOGNITION', 
            'ENABLE_EMOJI_RECOGNITION',
            'ENABLE_EMOJI_SENDING',
            'ENABLE_AUTO_MESSAGE', 
            'ENABLE_MEMORY',
            'UPLOAD_MEMORY_TO_AI',
            'ENABLE_LOGIN_PASSWORD',
            'ENABLE_REMINDERS',
            'ALLOW_REMINDERS_IN_QUIET_TIME',
            'USE_VOICE_CALL_FOR_REMINDERS',
            'ENABLE_ONLINE_API',
            'SEPARATE_ROW_SYMBOLS',
        ]
        for field in boolean_fields:
            new_values[field] = field in request.form  # 直接判断是否存在

        # 处理其他字段，并根据原有配置进行类型转换
        for key in request.form:
            if key in ['listen_list', *boolean_fields]:
                continue
            value = request.form[key].strip()
            if key in config:
                if isinstance(config[key], bool):
                    new_values[key] = value.lower() in ('on', 'true', '1', 'yes')
                # 对主动消息触发时间强制按浮点数处理，避免小数输入出错
                elif key in ["MIN_COUNTDOWN_HOURS", "MAX_COUNTDOWN_HOURS"]:
                    new_values[key] = float(value) if value else 0.0
                elif isinstance(config[key], int):
                    new_values[key] = int(value) if value else 0
                elif isinstance(config[key], float):
                    new_values[key] = float(value) if value else 0.0
                else:
                    new_values[key] = value
            else:
                new_values[key] = value  # 处理新增配置项

        update_config(new_values)
        return '', 204
    except Exception as e:
        app.logger.error(f"配置保存失败: {str(e)}")
        return str(e), 500

def stop_bot_process():
    global bot_process
    if bot_process is not None:
        try:
            bot_psutil = psutil.Process(bot_process.pid)
            bot_psutil.terminate()  # 发送 SIGTERM
            bot_psutil.wait(timeout=5)
        except subprocess.TimeoutExpired:
            bot_psutil.kill()
        finally:
            print("Bot process stopped.")
            bot_process = None

def parse_config():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.py')  # 修正路径
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

@app.route('/', methods=['GET', 'POST'])
@login_required
def index():
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
            submitted_fields = set(request.form.keys()) - {'listen_list'}
            for var in submitted_fields:
                if var not in config:
                    continue  # 忽略无效字段
                value = request.form[var].strip()
                if isinstance(config[var], bool):
                    new_values[var] = value.lower() in ('on', 'true', '1', 'yes')
                elif isinstance(config[var], int):
                    new_values[var] = int(value) if value else 0
                elif isinstance(config[var], float):
                    new_values[var] = float(value) if value else 0.0
                else:
                    new_values[var] = value

            # 明确处理布尔类型字段（如果未提交）
            for var in ['ENABLE_IMAGE_RECOGNITION', 
                        'ENABLE_EMOJI_RECOGNITION', 
                        'ENABLE_EMOJI_SENDING',
                        'ENABLE_AUTO_MESSAGE', 
                        'ENABLE_MEMORY', 
                        'UPLOAD_MEMORY_TO_AI',
                        'ENABLE_LOGIN_PASSWORD'
                        'ENABLE_REMINDERS',
                        'ALLOW_REMINDERS_IN_QUIET_TIME',
                        'USE_VOICE_CALL_FOR_REMINDERS',
                        'ENABLE_ONLINE_API',
                        'SEPARATE_ROW_SYMBOLS',
                        ]:
                if var not in submitted_fields:
                    new_values[var] = False

            update_config(new_values)
            return redirect(url_for('index'))
        except Exception as e:
            # 记录错误信息到日志或者异常捕捉信号
            app.logger.error(f"Error saving configuration: {e}")
            # 返回一个错误页面或提示信息
            return "Configuration save failed. Please check your inputs."

    try:
        # 获取prompt文件列表
        prompt_files = [f[:-3] for f in os.listdir('prompts') if f.endswith('.md')]
        config = parse_config()
        return render_template('config_editor.html', 
                             config=config,
                             prompt_files=prompt_files)
    except Exception as e:
        app.logger.error(f"Error loading configuration: {e}")
        return "Error loading configuration."

# 替换secure_filename的汉字过滤逻辑
def safe_filename(filename):
    # 只保留汉字、字母、数字、下划线和点，其他字符替换为_
    filename = re.sub(r'[^\w\u4e00-\u9fff.]', '_', filename)
    # 防止路径穿越
    filename = filename.replace('../', '_').replace('/', '_')
    return filename

# 新增的prompt管理路由
@app.route('/prompts')
@login_required
def prompt_list():
    if not os.path.exists('prompts'):
        os.makedirs('prompts')
    files = [f for f in os.listdir('prompts') if f.endswith('.md')]
    return render_template('prompts.html', files=files)

@app.route('/edit_prompt/<filename>', methods=['GET', 'POST'])
@login_required
def edit_prompt(filename):
    safe_dir = os.path.abspath('prompts')
    filepath = os.path.join(safe_dir, filename)
    
    if request.method == 'POST':
        content = request.form.get('content', '')
        new_filename = request.form.get('filename', '').strip()
        
        # 文件名安全处理
        if not new_filename.endswith('.md'):
            new_filename += '.md'
        new_filename = safe_filename(new_filename)
        new_filepath = os.path.join(safe_dir, new_filename)
        
        try:
            # 重命名文件
            if new_filename != filename and os.path.exists(new_filepath):
                return "文件名已存在"
            if new_filename != filename:
                os.rename(filepath, new_filepath)
                filepath = new_filepath
                
            # 写入内容
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            return redirect(url_for('prompt_list'))
        except Exception as e:
            return f"保存失败: {str(e)}"

    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        return render_template('edit_prompt.html', 
                             filename=filename,
                             content=content)
    except FileNotFoundError:
        return "文件不存在"

@app.route('/create_prompt', methods=['GET', 'POST'])
@login_required
def create_prompt():
    if request.method == 'POST':
        filename = request.form.get('filename', '').strip()
        content = request.form.get('content', '')
        
        if not filename:
            return "文件名不能为空"
            
        if not filename.endswith('.md'):
            filename += '.md'
        filename = safe_filename(filename)
        
        filepath = os.path.join('prompts', filename)
        if os.path.exists(filepath):
            return "文件已存在"
            
        try:
            with open(filepath, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            return redirect(url_for('prompt_list'))
        except Exception as e:
            return f"创建失败: {str(e)}"
            
    return render_template('create_prompt.html')

@app.route('/delete_prompt/<filename>', methods=['POST'])
@login_required
def delete_prompt(filename):
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
def import_config():
    global bot_process
    # 如果 bot 正在运行，则不允许导入配置
    if bot_process and bot_process.poll() is None:
        return jsonify({'error': '程序正在运行，请先停止再导入配置'}), 400

    try:
        if 'config_file' not in request.files:
            return jsonify({'error': '未找到上传的配置文件'}), 400
            
        config_file = request.files['config_file']
        if not config_file.filename.endswith('.py'):
            return jsonify({'error': '请上传.py格式的配置文件'}), 400
            
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
        
        # 合并配置：只更新导入配置中存在的项
        for key, value in imported_config.items():
            if key in current_config:  # 只更新当前配置中已存在的项
                current_config[key] = value
        
        # 更新配置文件
        update_config(current_config)
        
        return jsonify({'success': True, 'message': '配置导入成功，共导入了{}个有效参数'.format(len(imported_config))}), 200
    except Exception as e:
        app.logger.error(f"配置导入失败: {str(e)}")
        return jsonify({'error': f'导入失败: {str(e)}'}), 500


class WebLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        log_queue.put(log_entry)

# 配置日志处理器
web_handler = WebLogHandler()
web_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(web_handler)

@app.route('/stream')
@login_required
def stream():
    def event_stream():
        retry_count = 0
        while True:
            try:
                log = log_queue.get(timeout=5)
                yield f"data: {log}\n\n"
                retry_count = 0  # 成功时重置重试计数器
            except Empty:
                yield ":keep-alive\n\n"  # 发送心跳包
                retry_count = min(retry_count + 1, 5)
                time.sleep(2 ** retry_count)  # 指数退避
            except Exception as e:
                app.logger.error(f"SSE Error: {str(e)}")
                yield "event: error\ndata: Connection closed\n\n"
                break
    
    return Response(
        event_stream(),
        mimetype="text/event-stream",
        headers={
            'Cache-Control': 'no-cache',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no'
        }
    )

@app.route('/api/log', methods=['POST'])
def receive_bot_log():
    try:
        # 增加Content-Type检查
        if not request.is_json:
            return jsonify({'error': 'Unsupported Media Type'}), 415

        # 支持两种格式：单个日志或日志数组
        if 'logs' in request.json:  # 批量日志
            logs_data = request.json.get('logs', [])
            if isinstance(logs_data, list):
                for log_entry in logs_data:
                    if log_entry:
                        # 添加进程标识和颜色标记
                        colored_log = f"[BOT] \033[34m{log_entry.strip()}\033[0m"
                        log_queue.put(colored_log)
                return jsonify({'status': 'success', 'processed': len(logs_data)})
            return jsonify({'error': 'Invalid logs format'}), 400
            
        elif 'log' in request.json:  # 兼容单条日志格式
            log_data = request.json.get('log')
            if log_data:
                # 添加进程标识和颜色标记
                colored_log = f"[BOT] \033[34m{log_data.strip()}\033[0m"
                log_queue.put(colored_log)
            return jsonify({'status': 'success'})
            
        else:
            return jsonify({'error': 'Missing log data'}), 400
            
    except Exception as e:
        app.logger.error(f"日志接收失败: {str(e)}")
        return jsonify({'error': str(e)}), 500
    
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
    class BotStatusFilter(logging.Filter):
        def filter(self, record):
            # 如果日志消息中包含以下日志，则返回 False（不记录）
            if '/bot_status' in record.getMessage() or '/api/log' in record.getMessage() or '/save_all_reminders' in record.getMessage() or '/get_all_reminders' in record.getMessage():
                return False
            return True

    # 获取 werkzeug 的日志记录器并添加过滤器
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.addFilter(BotStatusFilter())

    # 新增配置文件存在检查
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"核心配置文件缺失: {config_path}")
    
    config = parse_config()
    PORT = config.get('PORT', '5000')

    # 在启动服务器前检查端口是否被占用，若占用则结束该进程
    kill_process_using_port(PORT)

    print(f"\033[31m重要提示：\r\n若您的浏览器没有自动打开网页端，请手动访问http://localhost:{config.get('PORT', '5000')}/ \r\n \033[0m")
    if config.get('ENABLE_LOGIN_PASSWORD', False):
        print(f"\033[31m您已启用登录密码，密码为 {config.get('LOGIN_PASSWORD', '未设置')} 请勿泄露给其它人！\r\n \033[0m")
    
    # 在启动服务器前设置定时器打开浏览器
    def open_browser():
        webbrowser.open(f'http://localhost:{PORT}/')
    
    Timer(1, open_browser).start()  # 延迟1秒确保服务器已启动
    
    app.run(host="0.0.0.0", debug=False, port=PORT)
    