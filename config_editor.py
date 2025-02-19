# ***********************************************************************
# Copyright of this file: Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# ***********************************************************************

from flask import Flask, render_template, request, redirect, url_for
import re
import ast
import os
from werkzeug.utils import secure_filename
import subprocess
import psutil

app = Flask(__name__)
bot_process = None

@app.route('/start_bot', methods=['POST'])
def start_bot():
    global bot_process
    if bot_process is None or bot_process.poll() is not None:
        bot_dir = os.path.dirname(os.path.abspath(__file__))
        bot_path = os.path.join(bot_dir, 'bot.py')
        # Windows需要CREATE_NEW_PROCESS_GROUP
        bot_process = subprocess.Popen(
            ['python', bot_path],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
        )
    
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
    try:
        # 复用首页的表单处理逻辑
        config = parse_config()
        new_values = {}

        # 处理监听列表
        new_values['LISTEN_LIST'] = [
            item.strip() 
            for item in request.form.getlist('listen_list') 
            if item.strip()
        ]

        # 处理布尔字段
        boolean_fields = [
            'ENABLE_IMAGE_RECOGNITION', 
            'ENABLE_EMOJI_RECOGNITION',
            'ENABLE_EMOJI_SENDING',
            'ENABLE_AUTO_MESSAGE'
        ]
        for field in boolean_fields:
            new_values[field] = field in request.form  # ✅ 直接判断是否存在

        # 处理其他字段
        for key in request.form:
            if key in ['listen_list', *boolean_fields]:
                continue
            value = request.form[key].strip()
            # 类型转换逻辑
            if key in config:
                if isinstance(config[key], bool):
                    new_values[key] = value.lower() in ('on', 'true', '1', 'yes')
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
    config = {}
    with open('config.py', 'r', encoding='utf-8') as f:
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

def update_config(new_values):
    with open('config.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    for line in lines:
        line_stripped = line.strip()
        if line_stripped.startswith('#') or not line_stripped:
            new_lines.append(line)
            continue
        # 修正正则表达式：允许行中存在其他内容（如注释）
        match = re.match(r'^\s*(\w+)\s*=.*', line)
        if match:
            var_name = match.group(1)
            if var_name in new_values:
                value = new_values[var_name]
                new_line = f"{var_name} = {repr(value)}\n"
                new_lines.append(new_line)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    with open('config.py', 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            config = parse_config()
            new_values = {}

            # 处理 LISTEN_LIST
            new_values['LISTEN_LIST'] = [
                item.strip() for item in request.form.getlist('listen_list') 
                if item.strip()
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
            for var in ['ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION', 'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE']:
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
        config = parse_config()
        return render_template('config_editor.html', config=config)
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
def prompt_list():
    if not os.path.exists('prompts'):
        os.makedirs('prompts')
    files = [f for f in os.listdir('prompts') if f.endswith('.md')]
    return render_template('prompts.html', files=files)

@app.route('/edit_prompt/<filename>', methods=['GET', 'POST'])
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

if __name__ == '__main__':
    app.run(debug=False, port=5000)  # 关闭调试模式
