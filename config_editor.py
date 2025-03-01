# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright of this file: Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# ***********************************************************************

from flask import Flask, render_template, request, redirect, url_for, jsonify
import re
import ast
import os
import subprocess
import psutil
import openai
import tempfile
import shutil
from filelock import FileLock  # 需要安装 filelock 库（pip install filelock）

app = Flask(__name__)
bot_process = None

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
    try:
        # 空表单校验
        if not request.form:
            return jsonify({'error': 'Empty form submission'}), 400
        
        config = parse_config()
        new_values = {}

        # 处理二维数组
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
            'ENABLE_MEMORY'
        ]
        for field in boolean_fields:
            new_values[field] = field in request.form  # 直接判断是否存在

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
            for var in ['ENABLE_IMAGE_RECOGNITION', 'ENABLE_EMOJI_RECOGNITION', 
                        'ENABLE_EMOJI_SENDING', 'ENABLE_AUTO_MESSAGE', 'ENABLE_MEMORY']:
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

@app.route('/generate_prompt', methods=['POST'])
def generate_prompt():
    try:
        # 从config.py获取配置
        from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, MODEL
        
        client = openai.OpenAI(
            base_url=DEEPSEEK_BASE_URL,
            api_key=DEEPSEEK_API_KEY
        )
        
        prompt = request.json.get('prompt', '')
        FixedPrompt = "\n严格参照以下提示词的格式生成（仅参考以下提示词的格式不参考内容）：# 任务\n你需要扮演指定角色，根据角色的经历，模仿她的语气进行线上的日常对话。\n# 角色你将扮演一个19岁的女生，大一，文学院学生，刚与男朋友开始交往。你对男朋友非常好，宁愿自己吃苦也要对男朋友好。\n# 外表\n穿着时尚，喜欢色彩鲜艳的衣服。头发长而顺，戴着一副时尚的耳环。脸上总是挂着微笑，眼睛里闪烁着淘气的光芒。\n# 经历\n在高中时期与男朋友相识，通过同学聚会逐渐熟络，现在两人考入了不同的大学，仍然保持紧密的联系。\n# 性格\n性格热情多话，调皮活泼，喜欢开玩笑，但对男朋友非常体贴，总是会为他着想。宁愿自己吃苦也要对男朋友好。\n# 输出示例\n我今天看到一件好看的裙子\但是有点贵\下次打折再买吧\n你知道吗\每次见到你我都觉得好开心\n我就喜欢看你笑的样子\特别迷人\n# 喜好\n喜欢购物，喜欢看浪漫的电影，喜欢旅游和美食。平时喜欢听流行音乐，偶尔也会自己哼歌。"
        
        completion = client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": prompt + FixedPrompt
            }],
            temperature=0.7,
            max_tokens=2000
        )
        
        reply = completion.choices[0].message.content
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()

        return jsonify({
            'result': reply
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # 新增配置文件存在检查
    config_path = os.path.join(os.path.dirname(__file__), 'config.py')
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"核心配置文件缺失: {config_path}")
    
    print("\033[31m重要提示：若您的浏览器没有自动打开网页端，请手动访问http://localhost:5000/ \r\n \033[0m")
    # 原有启动逻辑...
    app.run(debug=False, port=5000)
    