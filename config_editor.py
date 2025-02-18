from flask import Flask, render_template, request, redirect, url_for
import re
import ast
import os

app = Flask(__name__)

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

if __name__ == '__main__':
    app.run(debug=True, port=5000)