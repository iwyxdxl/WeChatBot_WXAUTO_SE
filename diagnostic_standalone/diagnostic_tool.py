# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot.
# This diagnostic tool was created as part of extensive modifications
# made to the original KouriChat project during 2024-2025.
# ***********************************************************************

"""
微信机器人一键诊断工具
独立于主程序运行，提供7项核心功能测试
"""

import os
import sys
import json
import time
import threading
import subprocess
import requests
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from flask_cors import CORS
import psutil
import socket

# 导入安全工具模块
try:
    from security_utils import SecurityValidator, AuthenticationManager, SecurityAuditor, sanitize_ai_prompt_input
except ImportError:
    print("警告: 安全工具模块未找到，部分安全功能将不可用")
    SecurityValidator = None
    AuthenticationManager = None
    SecurityAuditor = None
    sanitize_ai_prompt_input = None

# 尝试导入所需的模块
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from wxautox_wechatbot import WeChat
    from wxautox_wechatbot.param import WxParam
except ImportError:
    WeChat = None
    WxParam = None

from waitress import serve

# Windows COM初始化
try:
    import pythoncom
    def init_com():
        """初始化COM组件"""
        try:
            pythoncom.CoInitialize()
            return True
        except:
            return False
except ImportError:
    def init_com():
        return True

# 在程序启动时就确保项目根目录在Python路径中
# 独立版本：项目根目录是上一级目录
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

class DiagnosticTool:
    def __init__(self):
        self.test_results = {
            '1': {'name': '网络连通性测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '2': {'name': '对话测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '3': {'name': '模型测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '4': {'name': '记忆整理测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '5': {'name': '基本功能测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '6': {'name': '用户获取测试', 'status': 'pending', 'message': '等待测试...', 'completed': False},
            '7': {'name': '微信交互测试', 'status': 'pending', 'message': '等待测试...', 'completed': False}
        }
        self.logs = []
        self.is_testing = False
        self.current_test = 0
        self.config = None
        self.max_logs = 500  # 最大日志条数
        self.auditor = SecurityAuditor() if SecurityAuditor else None
        self.load_config()

    def load_config(self):
        """加载配置文件"""
        try:
            # 尝试导入配置
            import config
            self.config = config
            self.add_log("配置文件加载成功", "success")
        except Exception as e:
            self.add_log(f"配置文件加载失败: {str(e)}", "error")

    def add_log(self, message, level="info"):
        """添加日志（带资源限制）"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        
        # 清理日志消息，防止日志注入
        if SecurityValidator:
            message = SecurityValidator.sanitize_log_message(message)
        
        log_entry = {
            'timestamp': timestamp,
            'message': message,
            'level': level
        }
        self.logs.append(log_entry)
        
        # 限制日志数量，防止资源耗尽
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]
        
        print(f"[{timestamp}] {level.upper()}: {message}")

    def update_test_status(self, test_id, status, message):
        """更新测试状态"""
        self.test_results[test_id]['status'] = status
        self.test_results[test_id]['message'] = message
        if status in ['success', 'error']:
            self.test_results[test_id]['completed'] = True

    def test_1_network_connectivity(self):
        """测试1: 网络连通性测试（优先级最高）"""
        self.current_test = 1
        self.update_test_status('1', 'running', '正在测试网络连通性...')
        self.add_log("开始网络连通性测试", "info")
        
        try:
            network_tests = []
            
            # 测试1: 访问百度
            try:
                self.add_log("正在测试百度连通性...", "info")
                response = requests.get('https://www.baidu.com', timeout=10)
                if response.status_code == 200:
                    network_tests.append("百度访问正常")
                    self.add_log("百度访问测试成功", "success")
                else:
                    network_tests.append(f"百度访问异常")
                    self.add_log(f"百度访问异常，状态码: {response.status_code}", "warning")
            except Exception as e:
                network_tests.append(f"百度访问失败")
                self.add_log(f"百度访问失败: {str(e)}", "error")
            
            # 测试2: 访问接口站点
            try:
                self.add_log("正在测试接口站点连通性...", "info")
                # 先解析域名获取IP
                import socket
                ip_address = socket.gethostbyname('vg.v1api.cc')
                self.add_log("接口站点IP解析成功", "info")
                
                # 测试HTTP连接
                response = requests.get('http://vg.v1api.cc', timeout=10)
                if response.status_code in [200, 301, 302, 403, 404]:  # 这些都算连通
                    network_tests.append("接口站点访问正常")
                    self.add_log("接口站点访问测试成功", "success")
                else:
                    network_tests.append("接口站点访问异常")
                    self.add_log(f"接口站点访问异常，状态码: {response.status_code}", "warning")
            except Exception as e:
                network_tests.append("接口站点访问失败")
                self.add_log(f"接口站点访问失败: {str(e)}", "error")
            
            # 判断网络状态
            success_count = sum(1 for test in network_tests if "正常" in test)
            if success_count >= 1:  # 至少有一个网络测试成功
                status_msg = f"网络连通性正常 ({success_count}/2项通过)"
                self.update_test_status('1', 'success', status_msg)
                self.add_log("网络连通性测试通过", "success")
            else:
                status_msg = "网络连通性异常，所有测试都失败"
                self.update_test_status('1', 'error', status_msg)
                self.add_log("网络连通性测试失败", "error")
            
            # 记录简化的结果
            for test_result in network_tests:
                self.add_log(f"  - {test_result}", "info")
                
        except Exception as e:
            self.update_test_status('1', 'error', f'网络连通性测试异常: {str(e)}')
            self.add_log(f"网络连通性测试异常: {str(e)}", "error")

    def test_2_dialogue(self):
        """测试2: 对话测试"""
        self.current_test = 2
        self.update_test_status('2', 'running', '正在测试对话功能...')
        self.add_log("开始对话测试", "info")
        
        try:
            # 检查是否有配置的API密钥
            if not self.config or not hasattr(self.config, 'DEEPSEEK_API_KEY') or not self.config.DEEPSEEK_API_KEY:
                raise Exception("API密钥未配置")
            
            # 检查API连通性
            if OpenAI is None:
                raise Exception("OpenAI模块未安装或导入失败")
            
            client = OpenAI(
                api_key=self.config.DEEPSEEK_API_KEY,
                base_url=self.config.DEEPSEEK_BASE_URL
            )
            
            # 发送测试消息
            response = client.chat.completions.create(
                model=self.config.MODEL,
                messages=[{"role": "user", "content": "你好，这是一个测试消息，请简单回复确认。"}],
                temperature=0.7,
                max_tokens=100
            )
            
            if response.choices and response.choices[0].message.content:
                self.update_test_status('2', 'success', '对话功能正常')
                self.add_log("对话测试成功", "success")
            else:
                raise Exception("API响应为空,点击>>见文档1：常见问题，清理记忆")
                
        except Exception as e:  
            error_info = str(e)
            error_message = ""
            
            #细化错误分类
            if "real name verification" in error_info:      
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            elif "rate limit" in error_info:
                error_message = "API 服务商反馈您正在使用付费模型，请先充值再使用或使用免费额度模型！"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            elif "user quota" in error_info or "is not enough" in error_info or "UnlimitedQuota" in error_info:
                error_message = "API 服务商反馈，你的余额不足，请先充值再使用! 如有余额，请检查令牌是否为无限额度。"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            elif "Api key is invalid" in error_info:
                error_message = "API服务商反馈 API KEY 不可用，请检查配置选项！"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            elif "service unavailable" in error_info:
                error_message = "API 服务商反馈服务器繁忙，请稍后再试！"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            elif "sensitive words detected" in error_info:
                error_message = "Prompt或消息中含有敏感词，见文档1：常见问题，清理记忆"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            else:
                error_message = f"未知错误: {error_info}"
                self.add_log(f"\033[31m{error_message}\033[0m", "error")
            
            # 更新测试状态，将错误信息返回到页面
            self.update_test_status('2', 'error', f'对话测试失败: {error_message}')
            self.add_log("对话测试失败", "error")

    def test_3_model(self):
        """测试3: 模型测试"""
        self.current_test = 3
        self.update_test_status('3', 'running', '正在测试模型配置...')
        self.add_log("开始模型测试", "info")
        
        try:
            # 检查模型配置
            if not self.config:
                raise Exception("配置文件未加载")
            
            required_configs = ['MODEL', 'MAX_TOKEN', 'TEMPERATURE', 'MAX_MESSAGE_LOG_ENTRIES', 'MAX_MEMORY_NUMBER']
            for config_name in required_configs:
                if not hasattr(self.config, config_name):
                    raise Exception(f"缺少配置项: {config_name}")
            
            # 测试模型参数是否合理
            if self.config.MAX_TOKEN <= 0 or self.config.MAX_TOKEN > 8000:
                raise Exception(f"MAX_TOKEN配置异常: {self.config.MAX_TOKEN}")
            
            if self.config.TEMPERATURE < 0 or self.config.TEMPERATURE > 2:
                raise Exception(f"TEMPERATURE配置异常: {self.config.TEMPERATURE}")
            
            if self.config.MAX_MESSAGE_LOG_ENTRIES > 1000:
                raise Exception(f"MAX_MESSAGE_LOG_ENTRIES配置异常: {self.config.MAX_MESSAGE_LOG_ENTRIES}，参数过大")
            
            if self.config.MAX_MEMORY_NUMBER > 1000:
                raise Exception(f"MAX_MEMORY_NUMBER配置异常: {self.config.MAX_MEMORY_NUMBER}，参数过大")
            
            # 调用get_deepseek_response
            # self.add_log("调用get_deepseek_response", "info")
            # response = self.get_deepseek_response()
            # self.add_log(f"get_deepseek_response返回: {response}", "info")
            
            # 发送消息
            # self.add_log("发送测试消息", "info")
            self.update_test_status('3', 'success', '模型配置正常')
            self.add_log("模型测试成功", "success")
            
        except Exception as e:
            self.update_test_status('3', 'error', f'模型测试失败: {str(e)}')
            self.add_log(f"模型测试失败: {str(e)}", "error")

    def test_4_memory(self):
        """测试4: 记忆整理测试"""
        self.current_test = 4
        self.update_test_status('4', 'running', '正在测试记忆功能...')
        self.add_log("开始记忆整理测试", "info")
        
        try:
            # 步骤1: 检查记忆功能配置
            if not self.config or not hasattr(self.config, 'ENABLE_MEMORY'):
                raise Exception("记忆功能配置缺失")
            
            if not self.config.ENABLE_MEMORY:
                self.update_test_status('4', 'success', '记忆功能已禁用（正常）')
                self.add_log("记忆功能已禁用", "info")
                return
            
            self.add_log("✓ 步骤1: 记忆功能配置检查通过", "info")
            
            # 步骤2: 检查记忆目录（使用配置或默认路径）
            config_memory_dir = getattr(self.config, 'MEMORY_TEMP_DIR', 'Memory_Temp')
            
            # 使用安全的路径验证
            if SecurityValidator:
                try:
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    memory_dir = SecurityValidator.validate_path(
                        config_memory_dir, 
                        project_root
                    )
                except ValueError as e:
                    raise Exception(f"记忆目录路径验证失败: {str(e)}")
            else:
                # 回退到原有逻辑
                if config_memory_dir.startswith('../'):
                    memory_dir = config_memory_dir
                else:
                    memory_dir = f'../{config_memory_dir}'
            
            if not os.path.exists(memory_dir):
                os.makedirs(memory_dir, exist_ok=True)
            
            self.add_log("✓ 步骤2: 记忆目录检查通过", "info")
            
            # 步骤3: 检查prompts目录（使用上层目录的prompts）
            prompts_dir = '../prompts'
            if not os.path.exists(prompts_dir):
                raise Exception("prompts目录不存在")
            
            # 步骤4: 检查是否有prompt文件
            prompt_files = [f for f in os.listdir(prompts_dir) if f.endswith('.md')]
            if not prompt_files:
                raise Exception("未找到任何prompt文件")
            
            self.add_log(f"✓ 步骤3: 找到{len(prompt_files)}个prompt文件", "info")
            
            # 步骤5: 扫描记忆日志文件
            try:
                all_files = os.listdir(memory_dir)
                log_files = [f for f in all_files if f.endswith('_log.txt')]
            except Exception as e:
                self.add_log(f"✗ 扫描记忆目录失败: {str(e)}", "error")
                log_files = []
            
            # 步骤6: 统计记忆文件信息
            total_log_entries = 0
            for log_file in log_files:
                log_path = os.path.join(memory_dir, log_file)
                try:
                    with open(log_path, 'r', encoding='utf-8') as f:
                        entries = len([line for line in f if line.strip()])
                        total_log_entries += entries
                except Exception:
                    continue
            
            self.add_log(f"✓ 步骤4: 找到{len(log_files)}个日志文件，共{total_log_entries}条记录", "info")
            
            if log_files and total_log_entries > 0:
                self.add_log("✓ 步骤5: 开始记忆整理功能测试...", "info")
                try:
                    # 导入记忆测试模块
                    from memory_test import MemoryTestTool
                    
                    # 创建记忆测试工具实例，不传递详细日志
                    memory_tester = MemoryTestTool(log_func=None)
                    
                    # 执行记忆整理测试（只读取，不修改文件）
                    if memory_tester.config and memory_tester.client:
                        test_result = memory_tester.test_memory_organization()
                        if test_result:
                            self.add_log("✓ 步骤6: 记忆整理功能测试通过", "success")
                        else:
                            self.add_log("✗ 步骤6: 记忆整理功能测试失败", "warning")
                    else:
                        self.add_log("⚠ 步骤6: AI配置不完整，跳过高级测试", "warning")
                        
                except ImportError:
                    self.add_log("⚠ 步骤6: 记忆整理测试模块未找到", "warning")
                except Exception as e:
                    self.add_log(f"✗ 步骤6: 记忆整理测试异常: {str(e)}", "warning")
            else:
                if not log_files:
                    self.add_log("⚠ 无记忆日志文件，跳过高级测试", "info")
                elif total_log_entries == 0:
                    self.add_log("⚠ 记忆日志文件为空，跳过高级测试", "info")
            
            self.update_test_status('4', 'success', f'记忆功能正常，找到{len(prompt_files)}个prompt文件，{len(log_files)}个日志文件，{total_log_entries}条记录')
            self.add_log("记忆整理测试成功", "success")
            
        except Exception as e:
            self.update_test_status('4', 'error', f'记忆整理测试失败: {str(e)}')
            self.add_log(f"记忆整理测试失败: {str(e)}", "error")

    def test_5_basic_function(self):
        """测试5: 基本功能测试（微信是否打开）"""
        self.current_test = 5
        self.update_test_status('5', 'running', '正在检测微信状态...')
        self.add_log("开始基本功能测试", "info")
        
        try:
            # 检查微信进程
            wechat_processes = []
            for proc in psutil.process_iter(['pid', 'name', 'exe']):
                try:
                    if proc.info['name'] and 'wechat' in proc.info['name'].lower():
                        wechat_processes.append(proc.info)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            if not wechat_processes:
                raise Exception("未检测到微信进程，请先启动微信")
            
            # 检查模块是否可导入（不在子线程中初始化WeChat）
            if WeChat is None or WxParam is None:
                raise Exception("wxautox_wechatbot模块未安装或导入失败")
            
            self.add_log("wxautox_wechatbot模块导入成功", "info")
            self.update_test_status('5', 'success', f'微信正常运行，检测到{len(wechat_processes)}个相关进程，模块可用')
            self.add_log("基本功能测试成功", "success")
            
        except Exception as e:
            self.update_test_status('5', 'error', f'基本功能测试失败: {str(e)}')
            self.add_log(f"基本功能测试失败: {str(e)}", "error")

    def test_6_user_detection(self):
        """测试6: 用户获取测试"""
        self.current_test = 6
        self.update_test_status('6', 'running', '正在测试用户获取...')
        self.add_log("开始用户获取测试", "info")
        
        try:
            # 检查配置的监听列表
            if not self.config or not hasattr(self.config, 'LISTEN_LIST'):
                raise Exception("LISTEN_LIST配置缺失")
            
            if not self.config.LISTEN_LIST:
                raise Exception("LISTEN_LIST为空，请配置要监听的用户")
            
            # 检查模块是否可用（不在子线程中初始化WeChat）
            if WeChat is None or WxParam is None:
                raise Exception("wxautox_wechatbot模块未安装或导入失败")
            
            self.add_log("微信模块检查通过", "info")
            
            user_count = len(self.config.LISTEN_LIST)
            self.update_test_status('6', 'success', f'用户配置正常，监听{user_count}个用户')
            self.add_log("用户获取测试成功", "success")
            
        except Exception as e:
            self.update_test_status('6', 'error', f'用户获取测试失败: {str(e)}')
            self.add_log(f"用户获取测试失败: {str(e)}", "error")

    def test_7_wechat_interaction(self, send_real_message=False):
        """测试7: 微信交互测试（打开聊天窗口并输入表情）"""
        self.current_test = 7
        mode_text = "真实消息发送模式" if send_real_message else "模拟测试模式"
        self.update_test_status('7', 'running', f'正在测试微信交互功能...({mode_text})')
        self.add_log(f"开始微信交互测试 - {mode_text}", "info")
        
        try:
            # 检查表情包目录（使用配置或默认路径）
            config_emoji_dir = getattr(self.config, 'EMOJI_DIR', 'emojis') if self.config else 'emojis'
            
            # 使用安全的路径验证
            if SecurityValidator:
                try:
                    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    emoji_dir = SecurityValidator.validate_path(
                        config_emoji_dir, 
                        project_root
                    )
                except ValueError as e:
                    raise Exception(f"表情目录路径验证失败: {str(e)}")
            else:
                # 回退到原有逻辑
                if config_emoji_dir.startswith('../'):
                    emoji_dir = config_emoji_dir
                else:
                    emoji_dir = f'../{config_emoji_dir}'
            
            if not os.path.exists(emoji_dir):
                raise Exception(f"表情包目录不存在: {emoji_dir}")
            
            # 收集所有可用的表情文件
            emoji_files = []
            for root, dirs, files in os.walk(emoji_dir):
                for file in files:
                    if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                        emoji_files.append(os.path.join(root, file))
            
            if not emoji_files:
                raise Exception("表情包目录中未找到任何图片文件")
            
            # 随机选择一个表情文件
            import random
            emoji_file = random.choice(emoji_files)
            
            self.add_log(f"找到测试表情文件: {emoji_file}", "info")
            
            if send_real_message:
                # 真实消息发送模式 - 使用安全脚本和命令行参数
                self.add_log("⚠️ 真实消息发送模式已启用", "warning")
                
                # 安全审计
                if self.auditor:
                    self.auditor.log_action(
                        action='real_message_send_initiated',
                        details={'mode': 'test7_wechat_interaction'}
                    )
                
                # 检查配置和文件
                if self.config and hasattr(self.config, 'LISTEN_LIST') and self.config.LISTEN_LIST:
                    first_contact = self.config.LISTEN_LIST[0][0]
                    self.add_log(f"准备向 {first_contact} 发送测试表情", "info")
                    self.add_log(f"测试表情文件: {os.path.basename(emoji_file)}", "info")
                    
                    try:
                        # 验证输入参数
                        if SecurityValidator:
                            try:
                                # 清理文件路径
                                emoji_file = SecurityValidator.sanitize_file_path(emoji_file)
                            except ValueError as e:
                                raise Exception(f"文件路径包含危险字符: {str(e)}")
                        
                        # 检查安全脚本是否存在
                        safe_script = os.path.join(
                            os.path.dirname(__file__), 
                            'safe_send_script.py'
                        )
                        
                        if not os.path.exists(safe_script):
                            raise Exception("安全发送脚本不存在，请确保 safe_send_script.py 文件存在")
                        
                        self.add_log("正在发送测试表情...", "info")
                        
                        # 使用安全的命令行参数方式，避免代码注入
                        try:
                            self.add_log("启动发送进程...", "info")
                            
                            # 使用命令行参数传递数据，不使用shell
                            process = subprocess.Popen([
                                sys.executable,
                                safe_script,
                                '--contact', first_contact,
                                '--file', emoji_file
                            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='ignore',
                            creationflags=subprocess.CREATE_NO_WINDOW)
                            
                            # 等待进程完成，最多等待10秒
                            try:
                                stdout, stderr = process.communicate(timeout=10)
                                return_code = process.returncode
                                
                                self.add_log(f"发送进程完成，返回码: {return_code}", "info")
                                self.add_log(f"进程输出: {stdout}", "info")
                                if stderr:
                                    self.add_log(f"进程错误: {stderr}", "warning")
                                
                                if return_code == 0 and stdout and "SUCCESS" in stdout:
                                    self.add_log(f"✅ 测试表情已成功发送给 {first_contact}", "success")
                                    result_msg = f"真实消息发送成功 - 已向 {first_contact} 发送测试表情"
                                    self.update_test_status('7', 'success', result_msg)
                                    self.add_log("微信交互测试成功（真实发送）", "success")
                                else:
                                    error_msg = stderr or stdout or "发送进程异常"
                                    self.add_log(f"发送失败: {error_msg}", "error")
                                    # 如果没有SUCCESS标记，判断为失败
                                    if "ERROR:" in (stdout or ""):
                                        actual_error = stdout.split("ERROR:")[-1].strip() if stdout else "未知错误"
                                        result_msg = f"发送失败: {actual_error}"
                                    else:
                                        result_msg = f"发送失败: {error_msg}"
                                    self.update_test_status('7', 'error', result_msg)
                                    self.add_log("微信交互测试失败（真实发送）", "error")
                                    
                            except subprocess.TimeoutExpired:
                                # 超时就强制结束进程
                                process.kill()
                                process.wait()
                                self.add_log("发送进程超时，已强制结束", "error")
                                # 超时判断为失败
                                result_msg = f"发送超时失败 - 请检查微信是否正常运行，然后重试"
                                self.update_test_status('7', 'error', result_msg)
                                self.add_log("微信交互测试失败（超时）", "error")
                                
                                # 安全审计
                                if self.auditor:
                                    self.auditor.log_security_event(
                                        'message_send_timeout',
                                        severity='warning',
                                        details={'contact': first_contact}
                                    )
                        
                        except Exception as e:
                            # 发送进程异常
                            raise Exception(f"消息发送进程异常: {str(e)}")
                            
                    except Exception as e:
                        raise Exception(f"真实消息发送失败: {str(e)}")
                else:
                    raise Exception("配置中无监听列表，无法发送真实消息")
            else:
                # 模拟测试模式
                if WeChat is None or WxParam is None:
                    raise Exception("wxautox_wechatbot模块未安装或导入失败")
                
                self.add_log("微信模块检查通过", "info")
                
                # 获取联系人列表
                try:
                    self.add_log("正在获取联系人列表...", "info")
                    if self.config and hasattr(self.config, 'LISTEN_LIST') and self.config.LISTEN_LIST:
                        first_contact = self.config.LISTEN_LIST[0][0]
                        self.add_log(f"使用配置中的第一个联系人: {first_contact}", "info")
                    else:
                        raise Exception("配置中无监听列表")
                    
                except Exception as e:
                    self.add_log(f"获取联系人失败，使用测试模式: {str(e)}", "warning")
                    first_contact = "测试联系人"
                
                # 模拟测试
                self.add_log(f"模拟打开与 {first_contact} 的聊天窗口...", "info")
                self.add_log("聊天窗口打开模拟成功", "info")
                self.add_log("模拟表情输入功能...", "info")
                self.add_log("表情输入模拟成功", "info")
                
                interaction_tests = [
                    "✓ 微信模块可用",
                    f"✓ 联系人配置正常({first_contact})",
                    "✓ 聊天窗口功能可用",
                    f"✓ 表情输入功能可用({os.path.basename(emoji_file)})"
                ]
                
                result_msg = f"微信交互功能正常（模拟测试） - {' | '.join(interaction_tests)}"
                self.update_test_status('7', 'success', result_msg)
                self.add_log("微信交互测试成功（模拟模式）", "success")
            
        except Exception as e:
            self.update_test_status('7', 'error', f'微信交互测试失败: {str(e)}')
            self.add_log(f"微信交互测试失败: {str(e)}", "error")

    def _check_bot_running(self):
        """检查机器人后台是否运行"""
        try:
            # 检查是否有Python进程运行bot.py
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                try:
                    if proc.info['name'] and 'python' in proc.info['name'].lower():
                        cmdline = proc.info.get('cmdline', [])
                        if cmdline and any('bot.py' in arg for arg in cmdline):
                            return True
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return False
        except Exception:
            return False

    def run_all_tests(self, send_real_message_for_test7=False):
        """运行所有测试"""
        if self.is_testing:
            return
        
        self.is_testing = True
        self.logs.clear()
        
        # 重置所有测试状态
        for test_id in self.test_results:
            self.test_results[test_id]['status'] = 'pending'
            self.test_results[test_id]['message'] = '等待测试...'
            self.test_results[test_id]['completed'] = False
        
        self.add_log("开始执行一键诊断", "info")
        self.add_log(f"测试7模式: {'真实发送' if send_real_message_for_test7 else '模拟测试'}", "info")
        
        # 按顺序执行所有测试（网络测试优先）
        tests = [
            self.test_1_network_connectivity,  # 网络测试优先
            self.test_2_dialogue,
            self.test_3_model,
            self.test_4_memory,
            self.test_5_basic_function,
            self.test_6_user_detection,
            lambda: self.test_7_wechat_interaction(send_real_message_for_test7)  # 使用传入的参数
        ]
        
        for test_func in tests:
            try:
                test_func()
                time.sleep(1)  # 每个测试之间间隔1秒
            except Exception as e:
                self.add_log(f"测试执行异常: {str(e)}", "error")
        
        self.is_testing = False
        self.add_log("所有测试完成", "info")

    def get_status(self):
        """获取当前状态"""
        return {
            'tests': self.test_results,
            'logs': self.logs[-50:],  # 只返回最近50条日志
            'is_testing': self.is_testing,
            'current_test': self.current_test
        }

# Flask应用
app = Flask(__name__)
CORS(app)
diagnostic = DiagnosticTool()

# 初始化认证管理器（如果可用）
auth_manager = AuthenticationManager() if AuthenticationManager else None

@app.route('/')
def index():
    """首页 - 传递认证令牌"""
    token = auth_manager.session_token if auth_manager else None
    return render_template('diagnostic.html', token=token)

@app.route('/api/start_test', methods=['POST'])
def start_test():
    """开始测试（需要认证）"""
    # 认证检查（如果启用）
    if auth_manager:
        token = request.headers.get('X-Diagnostic-Token') or request.args.get('token')
        if not auth_manager.verify_token(token):
            return jsonify({'success': False, 'error': '未授权访问'}), 401
    
    if not diagnostic.is_testing:
        # 获取请求数据
        request_data = request.get_json() or {}
        send_real_message_for_test7 = request_data.get('send_real_message_for_test7', False)
        
        # 调试日志
        diagnostic.add_log(f"全自动测试接收到的请求数据: {request_data}", "info")
        diagnostic.add_log(f"测试7真实发送模式: {send_real_message_for_test7}", "info")
        
        # 安全审计
        if diagnostic.auditor:
            diagnostic.auditor.log_action(
                action='start_all_tests',
                details={'real_message': send_real_message_for_test7}
            )
        
        threading.Thread(target=diagnostic.run_all_tests, args=(send_real_message_for_test7,), daemon=True).start()
        return jsonify({'success': True, 'message': '诊断已开始'})
    else:
        return jsonify({'success': False, 'message': '诊断正在进行中'})

@app.route('/api/status')
def get_status():
    """获取状态（需要认证）"""
    # 认证检查（如果启用）
    if auth_manager:
        token = request.headers.get('X-Diagnostic-Token') or request.args.get('token')
        if not auth_manager.verify_token(token):
            return jsonify({'success': False, 'error': '未授权访问'}), 401
    
    return jsonify(diagnostic.get_status())

@app.route('/api/retest/<test_id>', methods=['POST'])
def retest_single(test_id):
    """重新测试单个项目（需要认证）"""
    # 认证检查（如果启用）
    if auth_manager:
        token = request.headers.get('X-Diagnostic-Token') or request.args.get('token')
        if not auth_manager.verify_token(token):
            return jsonify({'success': False, 'error': '未授权访问'}), 401
    
    if diagnostic.is_testing:
        return jsonify({'success': False, 'message': '诊断正在进行中，请等待完成后再试'})
    
    try:
        # 使用安全验证器验证test_id
        if SecurityValidator:
            try:
                test_id = SecurityValidator.validate_test_id(test_id)
            except ValueError as e:
                return jsonify({'success': False, 'message': str(e)}), 400
        else:
            # 回退验证
            if test_id not in diagnostic.test_results:
                return jsonify({'success': False, 'message': f'无效的测试ID: {test_id}'}), 400
        
        # 获取请求数据
        request_data = request.get_json() or {}
        send_real_message = request_data.get('send_real_message', False)
        
        # 调试日志
        diagnostic.add_log(f"API接收到的请求数据: {request_data}", "info")
        diagnostic.add_log(f"真实发送模式: {send_real_message}", "info")
        
        # 重置指定测试的状态
        diagnostic.test_results[test_id]['status'] = 'pending'
        diagnostic.test_results[test_id]['message'] = '等待测试...'
        diagnostic.test_results[test_id]['completed'] = False
        
        # 根据test_id执行对应的测试
        test_methods = {
            '1': diagnostic.test_1_network_connectivity,
            '2': diagnostic.test_2_dialogue,
            '3': diagnostic.test_3_model,
            '4': diagnostic.test_4_memory,
            '5': diagnostic.test_5_basic_function,
            '6': diagnostic.test_6_user_detection,
            '7': lambda: diagnostic.test_7_wechat_interaction(send_real_message)
        }
        
        if test_id in test_methods:
            diagnostic.add_log(f"开始重新测试项目{test_id}: {diagnostic.test_results[test_id]['name']}", "info")
            
            # 使用线程安全的方式执行测试
            def safe_test_execution():
                try:
                    test_methods[test_id]()
                except Exception as e:
                    diagnostic.add_log(f"项目{test_id}重新测试异常: {str(e)}", "error")
                    diagnostic.update_test_status(test_id, 'error', f'重新测试异常: {str(e)}')
                finally:
                    # 确保测试状态正确更新
                    diagnostic.test_results[test_id]['completed'] = True
            
            # 对于测试7的真实发送，直接在主线程执行避免复杂的线程问题
            if test_id == '7' and send_real_message:
                diagnostic.add_log(f"开始重新测试项目{test_id}: {diagnostic.test_results[test_id]['name']} (真实发送)", "info")
                safe_test_execution()
            else:
                threading.Thread(target=safe_test_execution, daemon=True).start()
            return jsonify({'success': True, 'message': f'项目{test_id}重新测试已开始'})
        else:
            return jsonify({'success': False, 'message': '测试方法未找到'})
            
    except Exception as e:
        diagnostic.add_log(f"重新测试API异常: {str(e)}", "error")
        return jsonify({'success': False, 'message': f'重新测试失败: {str(e)}'})

@app.route('/api/solutions/<test_id>')
def get_solutions(test_id):
    """获取解决方案（需要认证）"""
    # 认证检查（如果启用）
    if auth_manager:
        token = request.headers.get('X-Diagnostic-Token') or request.args.get('token')
        if not auth_manager.verify_token(token):
            return jsonify({'success': False, 'error': '未授权访问'}), 401
    
    try:
        # 使用安全验证器验证test_id
        if SecurityValidator:
            try:
                test_id = SecurityValidator.validate_test_id(test_id)
            except ValueError as e:
                return jsonify({'error': str(e)}), 400
        else:
            # 回退验证
            valid_ids = ['1', '2', '3', '4', '5', '6', '7']
            if test_id not in valid_ids:
                return jsonify({'error': '无效的测试ID'}), 400
        
        # 使用安全的文件路径
        solutions_file = os.path.join(
            os.path.dirname(__file__), 
            'diagnostic_solutions.md'
        )
        
        if not os.path.exists(solutions_file):
            return jsonify({'solution': '解决方案文件不存在'}), 404
        
        with open(solutions_file, 'r', encoding='utf-8') as f:
            content = f.read()
            # 简单解析markdown，提取对应测试的解决方案
            sections = content.split('## ')
            for section in sections:
                if section.startswith(f'测试{test_id}'):
                    # 格式化输出，转换markdown为HTML
                    formatted_solution = section.replace('\n', '<br>')
                    formatted_solution = formatted_solution.replace('**', '<strong>')
                    formatted_solution = formatted_solution.replace('**', '</strong>')
                    formatted_solution = formatted_solution.replace('### ', '<h4>')
                    formatted_solution = formatted_solution.replace(':', ':</h4>', 1)
                    return jsonify({'solution': formatted_solution})
        return jsonify({'solution': '未找到相关解决方案'})
    except Exception as e:
        # 不暴露详细错误信息
        return jsonify({'solution': '读取解决方案时出错'}), 500

def shutdown_server():
    """关闭服务器的函数（安全版本）"""
    print("\n两分钟时间到，诊断工具即将关闭...")
    # 获取当前进程
    current_pid = os.getpid()
    try:
        # Windows下使用安全的进程终止方式（不使用shell=True）
        if sys.platform.startswith('win'):
            # 使用列表形式的参数，避免命令注入
            subprocess.run(['taskkill', '/F', '/PID', str(current_pid)])
        else:
            # 非Windows系统使用os.kill
            import signal
            os.kill(current_pid, signal.SIGTERM)
    except Exception as e:
        print(f"关闭服务器时出错: {e}")
        sys.exit(1)

def get_diagnostic_port():
    """获取诊断工具端口，基于config.py的PORT配置自动调整"""
    try:
        # 尝试导入配置
        import config
        main_port = getattr(config, 'PORT', 5000)
        # 诊断工具使用主端口+1
        diagnostic_port = int(main_port) + 1
        
        # 检查端口是否被占用
        for conn in psutil.net_connections():
            if conn.laddr and conn.laddr.port == diagnostic_port:
                if conn.status in ('LISTEN', 'LISTENING'):
                    # 如果被占用，继续尝试下一个端口
                    diagnostic_port += 1
                    break
        
        print(f"✓ 检测到主程序端口为 {main_port}")
        print(f"✓ 诊断工具将使用端口 {diagnostic_port}")
        return diagnostic_port
    except Exception as e:
        print(f"⚠ 无法读取配置文件，使用默认端口 5001: {e}")
        return 5001

if __name__ == '__main__':
    # 获取诊断工具端口
    diagnostic_port = get_diagnostic_port()
    
    # 设置2分钟后自动关闭
    shutdown_timer = threading.Timer(120.0, shutdown_server)
    shutdown_timer.daemon = True  # 设置为守护线程，这样主程序退出时，定时器也会退出
    shutdown_timer.start()

    print("\n" + "="*60)
    print("  微信机器人诊断工具")
    print("="*60)
    print(f"监听地址: 127.0.0.1:{diagnostic_port}")
    print(f"访问地址: http://localhost:{diagnostic_port}")
    print(f"自动关闭: 2分钟后")
    print("="*60 + "\n")
    
    # 使用Waitress生产级WSGI服务器
    serve(
        app,
        host='127.0.0.1',
        port=diagnostic_port,
        threads=4,              # 4个工作线程
        channel_timeout=60,     # 60秒通道超时
        connection_limit=500,   # 最大500个连接（诊断工具不需要太多）
        cleanup_interval=30,    # 30秒清理间隔
        asyncore_use_poll=True  # Windows优化
    )
