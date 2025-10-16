# -*- coding: utf-8 -*-

# ***********************************************************************
# Modified based on the KouriChat project
# Copyright of this modification: Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot, which includes modifications to the KouriChat project.
# The original KouriChat project's copyright and license information are preserved in the LICENSE file.
# For any further details regarding the license, please refer to the LICENSE file.
# ***********************************************************************

import sys
import base64
import requests
import logging
from datetime import datetime
import datetime as dt
import threading
import time
from openai import OpenAI
import random
from typing import Optional
import pyautogui
import shutil
import re
import ast
from config import *
import queue
import json
from threading import Timer
from bs4 import BeautifulSoup
from urllib.parse import urlparse
import os
try:
    from wxautox_wechatbot import WeChat
    from wxautox_wechatbot.param import WxParam
    WxParam.ENABLE_FILE_LOGGER = False
    WxParam.FORCE_MESSAGE_XBIAS = True
    os.environ["PROJECT_NAME"] = 'iwyxdxl/WeChatBot_WXAUTO_SE'
except ImportError:
    try:
        from wxautox import WeChat
    except ImportError:
        from wxauto import WeChat

# 生成用户昵称列表和prompt映射字典
user_names = [entry[0] for entry in LISTEN_LIST]
prompt_mapping = {entry[0]: entry[1] for entry in LISTEN_LIST}

# 编码检测和处理辅助函数
def safe_read_file_with_encoding(file_path, fallback_content=""):
    """
    安全地读取文件，自动处理编码问题。
    
    Args:
        file_path (str): 文件路径
        fallback_content (str): 如果所有编码都失败时返回的内容
        
    Returns:
        str: 文件内容
    """
    encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp1252']
    
    for encoding in encodings_to_try:
        try:
            with open(file_path, 'r', encoding=encoding) as f:
                content = f.read()
            
            # 如果不是UTF-8编码读取成功，自动转换为UTF-8
            if encoding != 'utf-8':
                logger.info(f"文件 {file_path} 使用 {encoding} 编码读取成功，正在转换为UTF-8")
                backup_path = f"{file_path}.bak_{int(time.time())}"
                try:
                    shutil.copy(file_path, backup_path)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    logger.info(f"已将文件转换为UTF-8编码: {file_path} (备份: {backup_path})")
                except Exception as save_err:
                    logger.error(f"转换文件编码失败: {save_err}")
            
            return content
            
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.error(f"读取文件时发生错误: {file_path}, 编码: {encoding}, 错误: {e}")
            continue
    
    # 所有编码都失败，创建备份并返回备用内容
    logger.error(f"所有编码格式都无法读取文件: {file_path}")
    backup_path = f"{file_path}.corrupted_{int(time.time())}"
    try:
        shutil.copy(file_path, backup_path)
        logger.error(f"已备份损坏文件到: {backup_path}")
    except Exception as backup_err:
        logger.error(f"备份损坏文件失败: {backup_err}")
    
    return fallback_content

def safe_write_file_with_encoding(file_path, content, mode='w'):
    """
    安全地写入文件，自动处理编码问题。
    
    Args:
        file_path (str): 文件路径
        content (str): 要写入的内容
        mode (str): 写入模式，'w' 或 'a'
    """
    try:
        with open(file_path, mode, encoding='utf-8') as f:
            f.write(content)
    except UnicodeEncodeError as e:
        logger.warning(f"UTF-8编码失败，清理特殊字符: {file_path}, 错误: {e}")
        # 清理无法编码的字符
        clean_content = content.encode('utf-8', errors='ignore').decode('utf-8')
        with open(file_path, mode, encoding='utf-8') as f:
            f.write(clean_content)
        logger.info(f"已清理特殊字符并写入文件: {file_path}")

# 群聊信息缓存
group_chat_cache = {}  # {user_name: is_group_chat}
group_cache_lock = threading.Lock()

# 持续监听消息，并且收到消息后回复
wait = 1  # 设置1秒查看一次是否有新消息

# 获取程序根目录
root_dir = os.path.dirname(os.path.abspath(__file__))

# 动态配置获取函数
def get_dynamic_config(key, default_value=None):
    """动态从config.py文件获取最新配置值"""
    try:
        config_path = os.path.join(root_dir, 'config.py')
        if not os.path.exists(config_path):
            return default_value
        
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 使用正则表达式查找配置项
        pattern = rf"^{re.escape(key)}\s*=\s*(.+)$"
        match = re.search(pattern, content, re.M)
        if match:
            value_str = match.group(1).strip()
            # 处理常见的Python字面量
            if value_str.lower() in ('true', 'false'):
                return value_str.lower() == 'true'
            elif value_str.isdigit():
                return int(value_str)
            elif value_str.replace('.', '').isdigit():
                return float(value_str)
            else:
                # 使用安全的字面量解析
                try:
                    return ast.literal_eval(value_str)
                except Exception:
                    return value_str.strip("'\"")
        return default_value
    except Exception as e:
        logger.warning(f"获取动态配置 {key} 失败: {e}")
        return default_value

# 用户消息队列和聊天上下文管理
user_queues = {}  # {user_id: {'messages': [], 'last_message_time': 时间戳, ...}}
queue_lock = threading.Lock()  # 队列访问锁
chat_contexts = {}  # {user_id: [{'role': 'user', 'content': '...'}, ...]}
CHAT_CONTEXTS_FILE = "chat_contexts.json" # 存储聊天上下文的文件名
USER_TIMERS_FILE = "user_timers.json"  # 存储用户计时器状态的文件名

# 心跳相关全局变量
HEARTBEAT_INTERVAL = 5  # 秒
FLASK_SERVER_URL_BASE = f'http://localhost:{PORT}' # 使用从config导入的PORT

# --- REMINDER RELATED GLOBALS ---
RECURRING_REMINDERS_FILE = "recurring_reminders.json" # 存储重复和长期一次性提醒的文件名
# recurring_reminders 结构:
# [{'reminder_type': 'recurring', 'user_id': 'xxx', 'time_str': 'HH:MM', 'content': '...'},
#  {'reminder_type': 'one-off', 'user_id': 'xxx', 'target_datetime_str': 'YYYY-MM-DD HH:MM', 'content': '...'}]
recurring_reminders = [] # 内存中加载的提醒列表
recurring_reminder_lock = threading.RLock() # 锁，用于处理提醒文件和列表的读写

active_timers = {} # { (user_id, timer_id): Timer_object } (用于短期一次性提醒 < 10min)
timer_lock = threading.Lock()
next_timer_id = 0

# 提醒功能的资源限制（防止拒绝服务攻击）
MAX_ACTIVE_TIMERS_PER_USER = 10  # 每个用户最多10个活动的短期提醒
MAX_RECURRING_REMINDERS_PER_USER = 20  # 每个用户最多20个每日重复提醒
MAX_ONEOFF_REMINDERS_PER_USER = 30  # 每个用户最多30个长期一次性提醒
MAX_REMINDER_MESSAGE_LENGTH = 500  # 提醒消息最大长度

class AsyncHTTPHandler(logging.Handler):
    def __init__(self, url, retry_attempts=3, timeout=3, max_queue_size=1000, batch_size=20, batch_timeout=5):
        """
        初始化异步 HTTP 日志处理器。

        Args:
            url (str): 发送日志的目标 URL。
            retry_attempts (int): 发送失败时的重试次数。
            timeout (int): HTTP 请求的超时时间（秒）。
            max_queue_size (int): 内存中日志队列的最大容量。
                                  当队列满时，新的日志消息将被丢弃。
            batch_size (int): 批量处理的日志数量，达到此数量会触发发送。
            batch_timeout (int): 批处理超时时间(秒)，即使未达到batch_size，
                               经过此时间也会发送当前累积的日志。
        """
        super().__init__()
        self.url = url
        self.retry_attempts = retry_attempts
        self.timeout = timeout
        self.log_queue = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self.dropped_logs_count = 0  # 添加一个计数器来跟踪被丢弃的日志数量
        self.batch_size = batch_size  # 批处理大小
        self.batch_timeout = batch_timeout  # 批处理超时时间
        
        # 新增: 断路器相关属性
        self.consecutive_failures = 0  # 跟踪连续失败次数
        self.circuit_breaker_open = False  # 断路器状态
        self.circuit_breaker_reset_time = None  # 断路器重置时间
        self.CIRCUIT_BREAKER_THRESHOLD = 5  # 触发断路器的连续失败次数
        self.CIRCUIT_BREAKER_RESET_TIMEOUT = 60  # 断路器重置时间（秒）
        
        # 新增: HTTP请求统计
        self.total_requests = 0
        self.failed_requests = 0
        self.last_success_time = time.time()
        
        # Waitress优化: 使用Session实现连接池复用
        self.session = requests.Session()
        # 配置连接池参数（适配Waitress的连接限制）
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=2,      # 连接池数量
            pool_maxsize=5,          # 最大连接数
            max_retries=0,           # 禁用requests内置重试，使用自定义重试逻辑
            pool_block=False         # 非阻塞模式
        )
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # 设置默认请求头（优化Waitress通信）
        self.session.headers.update({
            'User-Agent': 'WeChatBot-Logger/2.0',
            'Connection': 'keep-alive',  # 启用HTTP Keep-Alive
            'Accept-Encoding': 'gzip, deflate'  # 支持压缩
        })
        
        # 后台线程用于处理日志队列
        self.worker = threading.Thread(target=self._process_queue, daemon=True)
        self.worker.start()
        
        # 测试连接到配置编辑器
        self._test_connection()

    def _test_connection(self):
        """测试到配置编辑器的连接"""
        try:
            # 使用非阻塞方式测试连接，超时时间短
            resp = self.session.get(
                f'http://localhost:{PORT}/',
                timeout=2
            )
            if resp.status_code == 200:
                print(f"\033[32m✓ 成功连接到配置编辑器服务器 (端口 {PORT})\033[0m")
                print(f"\033[32m✓ 日志将实时发送到网页端\033[0m")
            else:
                print(f"\033[33m警告: 配置编辑器服务器响应异常 (状态码 {resp.status_code})\033[0m")
        except requests.exceptions.ConnectionError:
            print(f"\033[33m警告: 无法连接到配置编辑器服务器 (端口 {PORT})\033[0m")
            print(f"\033[33m提示: 日志只会显示在控制台，不会发送到网页端\033[0m")
            print(f"\033[33m解决: 如需网页端查看日志，请先启动 config_editor.py\033[0m")
        except Exception as e:
            print(f"\033[33m警告: 连接测试失败: {str(e)[:50]}\033[0m")
    
    def emit(self, record):
        """
        格式化日志记录并尝试将其放入队列。
        如果队列已满，则放弃该日志并记录警告。
        """
        try:
            log_entry = self.format(record)
            # 使用非阻塞方式放入队列
            self.log_queue.put(log_entry, block=False)
        except queue.Full:
            # 当队列满时，捕获 queue.Full 异常
            self.dropped_logs_count += 1
            # 避免在日志处理器内部再次调用 logger (可能导致死循环)
            # 每丢弃一定数量的日志后才记录一次，避免刷屏
            if self.dropped_logs_count % 100 == 1:  # 每丢弃100条日志记录一次（第1, 101, 201...条时记录）
                logging.warning(f"日志队列已满 (容量 {self.log_queue.maxsize})，已丢弃 {self.dropped_logs_count} 条日志。请检查日志接收端或网络。")
        except Exception:
            # 处理其他可能的格式化或放入队列前的错误
            self.handleError(record)

    def _should_attempt_send(self):
        """检查断路器是否开启，决定是否尝试发送"""
        if not self.circuit_breaker_open:
            return True
        
        now = time.time()
        if self.circuit_breaker_reset_time and now >= self.circuit_breaker_reset_time:
            # 重置断路器
            logging.info("日志发送断路器重置，恢复尝试发送")
            self.circuit_breaker_open = False
            self.consecutive_failures = 0
            return True
        
        return False

    def _process_queue(self):
        """
        后台工作线程，积累一定数量的日志后批量发送到Waitress服务器。
        使用连接池复用提升性能。
        """
        headers = {
            'Content-Type': 'application/json'
        }
        batch = []  # 用于存储批处理日志
        last_batch_time = time.time()  # 上次发送批处理的时间
        
        while not self._stop_event.is_set():
            try:
                # 等待日志消息，设置超时以便能响应停止事件和批处理超时
                try:
                    # 使用较短的超时时间以便及时检查批处理超时
                    log_entry = self.log_queue.get(timeout=0.5)
                    batch.append(log_entry)
                    # 标记队列任务完成
                    self.log_queue.task_done()
                except queue.Empty:
                    # 队列为空时，检查是否应该发送当前批次（超时）
                    pass
                
                current_time = time.time()
                batch_timeout_reached = current_time - last_batch_time >= self.batch_timeout
                batch_size_reached = len(batch) >= self.batch_size
                
                # 如果达到批量大小或超时，且有日志要发送
                if (batch_size_reached or batch_timeout_reached) and batch:
                    # 新增: 检查断路器状态
                    if self._should_attempt_send():
                        success = self._send_batch(batch, headers)
                        if success:
                            self.consecutive_failures = 0  # 重置失败计数
                            self.last_success_time = time.time()
                            # 清空批次
                            batch = []
                            last_batch_time = current_time
                        else:
                            self.consecutive_failures += 1
                            self.failed_requests += 1
                            if self.consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
                                # 打开断路器
                                self.circuit_breaker_open = True
                                self.circuit_breaker_reset_time = time.time() + self.CIRCUIT_BREAKER_RESET_TIMEOUT
                                print(f"\033[33m警告: 日志发送连续失败 {self.consecutive_failures} 次，断路器开启 {self.CIRCUIT_BREAKER_RESET_TIMEOUT} 秒\033[0m")
                                print(f"\033[33m提示: 请确保配置编辑器(config_editor.py)正在运行，端口 {PORT}\033[0m")
                            # 发送失败也清空批次，避免队列堵塞
                            batch = []
                            last_batch_time = current_time
                    else:
                        # 断路器开启，只在特定时间输出警告，避免刷屏
                        if len(batch) >= self.batch_size:  # 只在批次满时才输出
                            reset_remaining = self.circuit_breaker_reset_time - time.time() if self.circuit_breaker_reset_time else 0
                            print(f"\033[33m断路器开启中，暂不发送 {len(batch)} 条日志，将在 {reset_remaining:.1f} 秒后尝试恢复\033[0m")
                        # 断路器开启时，清空批次以避免内存积累
                        batch = []
                        last_batch_time = current_time
            
            except Exception as e:
                # 出错时清空当前批次，避免卡住
                logging.error(f"日志处理队列异常: {str(e)}", exc_info=True)
                batch = []
                last_batch_time = time.time()
                time.sleep(1)  # 出错后暂停一下，避免CPU占用过高
        
        # 关闭前发送剩余的日志
        if batch:
            self._send_batch(batch, headers)

    def _send_batch(self, batch, headers):
        """
        发送一批日志记录到Waitress服务器，使用连接池和改进的重试策略
        
        返回:
            bool: 是否成功发送
        """
        data = {'logs': batch}
        
        # Waitress优化: 调整超时和重试参数
        MAX_RETRY_DELAY = 3.0  # 最大重试延迟（秒），Waitress处理能力更强
        BASE_DELAY = 0.3       # 基础延迟（秒），降低以利用Waitress快速响应
        
        self.total_requests += 1
        
        for attempt in range(self.retry_attempts):
            try:
                # 使用session发送请求，复用连接
                resp = self.session.post(
                    self.url,
                    json=data,
                    headers=headers,
                    timeout=(self.timeout, self.timeout * 2)  # (连接超时, 读取超时)
                )
                resp.raise_for_status()  # 检查 HTTP 错误状态码
                
                # 成功发送，记录日志数量
                if attempt > 0:
                    logging.info(f"在第 {attempt+1} 次尝试后成功发送 {len(batch)} 条日志 [Waitress]")
                else:
                    logging.debug(f"成功批量发送 {len(batch)} 条日志 [Waitress]")
                return True  # 成功返回
                
            except requests.exceptions.Timeout as e:
                # Waitress超时处理（通常意味着服务器负载高）
                logging.warning(f"日志发送超时 (尝试 {attempt+1}/{self.retry_attempts}) - Waitress可能繁忙")
                delay = min(BASE_DELAY * 1.2, MAX_RETRY_DELAY)  # 轻微延迟
                
            except requests.exceptions.ConnectionError as e:
                # 连接错误（可能是Waitress重启或网络问题）
                error_msg = str(e)
                if 'Connection refused' in error_msg or 'Connection aborted' in error_msg:
                    if attempt == 0:  # 只在第一次尝试时输出到控制台
                        print(f"\033[33m警告: 无法连接到配置编辑器服务器 (端口 {PORT})\033[0m")
                        print(f"\033[33m提示: 请确保 config_editor.py 正在运行\033[0m")
                    logging.warning(f"无法连接到Waitress服务器 (尝试 {attempt+1}/{self.retry_attempts})")
                else:
                    logging.warning(f"日志发送连接错误 (尝试 {attempt+1}/{self.retry_attempts}): {error_msg[:100]}")
                delay = min(BASE_DELAY * (2 ** attempt), MAX_RETRY_DELAY)  # 指数退避
                
            except requests.exceptions.HTTPError as e:
                # HTTP错误（4xx, 5xx）
                status_code = e.response.status_code if e.response else 'Unknown'
                if status_code >= 500:
                    # 服务器错误，可以重试
                    logging.warning(f"Waitress服务器错误 {status_code} (尝试 {attempt+1}/{self.retry_attempts})")
                    delay = min(BASE_DELAY * (1.5 ** attempt), MAX_RETRY_DELAY)
                else:
                    # 客户端错误，不应该重试
                    logging.error(f"日志请求被拒绝，状态码 {status_code}")
                    return False
                    
            except requests.exceptions.RequestException as e:
                # 其他请求异常
                logging.warning(f"日志发送异常 (尝试 {attempt+1}/{self.retry_attempts}): {str(e)[:100]}")
                delay = min(BASE_DELAY * (1.5 ** attempt), MAX_RETRY_DELAY)
                
            except Exception as e:
                # 未预期的异常
                logging.error(f"发送日志时发生未知错误: {str(e)[:100]}", exc_info=True)
                return False
            
            # 最后一次尝试不需要等待
            if attempt < self.retry_attempts - 1:
                time.sleep(delay)
        
        # 所有重试都失败
        downtime = time.time() - self.last_success_time
        logging.error(f"发送日志批次失败，已达到最大重试次数 ({self.retry_attempts})，丢弃 {len(batch)} 条日志 [连续失败: {self.consecutive_failures+1}, 持续时间: {downtime:.1f}秒]")
        return False  # 返回失败状态
    
    def get_stats(self):
        """返回日志处理器的统计信息"""
        return {
            'queue_size': self.log_queue.qsize(),
            'queue_capacity': self.log_queue.maxsize,
            'dropped_logs': self.dropped_logs_count,
            'total_requests': self.total_requests,
            'failed_requests': self.failed_requests,
            'circuit_breaker_status': 'open' if self.circuit_breaker_open else 'closed',
            'consecutive_failures': self.consecutive_failures
        }

    def close(self):
        """
        停止工作线程并等待队列处理完成（或超时），关闭Waitress连接。
        """
        if not self.log_queue.empty():
            logging.info(f"关闭日志处理器，还有 {self.log_queue.qsize()} 条日志待处理")
            try:
                # 尝试最多等待30秒处理剩余日志
                self.log_queue.join(timeout=30)
            except:
                pass
        
        self._stop_event.set()
        self.worker.join(timeout=self.timeout * self.retry_attempts + 5)  # 等待一个合理的时间
        
        if self.worker.is_alive():
            logging.warning("日志处理线程未能正常退出")
        else:
            logging.info("日志处理线程已正常退出")
        
        # 关闭session，释放Waitress连接池资源
        try:
            self.session.close()
            logging.debug("HTTP Session已关闭，连接池资源已释放 [Waitress]")
        except Exception as e:
            logging.warning(f"关闭HTTP Session时发生异常: {e}")
        
        super().close()

# 创建日志格式器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 初始化异步HTTP处理器
async_http_handler = AsyncHTTPHandler(
    url=f'http://localhost:{PORT}/api/log',
    batch_size=20,  # 一次发送20条日志
    batch_timeout=1  # 即使不满20条，最多等待1秒也发送
)
async_http_handler.setFormatter(formatter)

# 配置根Logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)
logger.handlers.clear()

# 添加异步HTTP日志处理器
logger.addHandler(async_http_handler)

# 同时可以保留控制台日志处理器
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# 获取微信窗口对象
try:
    wx = WeChat()
except:
    logger.error(f"\033[31m无法初始化微信接口，请确保您安装的是微信3.9版本，并且已经登录！\033[0m")
    logger.error("\033[31m微信3.9版本下载地址：https://dldir1v6.qq.com/weixin/Windows/WeChatSetup.exe \033[0m")
    exit(1)
# 获取登录用户的名字
ROBOT_WX_NAME = wx.nickname

# 存储用户的计时器和随机等待时间
user_timers = {}
user_wait_times = {}
emoji_timer = None
emoji_timer_lock = threading.Lock()
# 全局变量，控制消息发送状态
can_send_messages = True
is_sending_message = False

# 用于拍一拍功能的全局变量
user_last_msg = {}  # {user_id: msg对象} 存储每个用户最后发送的消息对象
bot_last_sent_msg = {}  # {user_id: wx.GetLastMessage()} 存储机器人发送给每个用户的最后一条消息

# --- 定时重启相关全局变量 ---
program_start_time = 0.0 # 程序启动时间戳
last_received_message_timestamp = 0.0 # 最后一次活动（收到/处理消息）的时间戳

_BLACKLIST_FETCHED = False
_BLACKLIST_STRINGS = None

def _fetch_untrusted_providers():
    global _BLACKLIST_FETCHED, _BLACKLIST_STRINGS
    if _BLACKLIST_FETCHED:
        return _BLACKLIST_STRINGS
    try:
        resp = requests.get("https://vg.v1api.cc/black", timeout=3)
        resp.raise_for_status()
        data = resp.json()
        items = data.get("data") if isinstance(data, dict) else None
        if isinstance(items, list):
            _BLACKLIST_STRINGS = [str(x).lower() for x in items if x]
        else:
            _BLACKLIST_STRINGS = []
    except Exception as e:
        _BLACKLIST_STRINGS = None
    finally:
        _BLACKLIST_FETCHED = True
    return _BLACKLIST_STRINGS

def _is_base_url_untrusted(base_url: str) -> bool:
    if not base_url:
        return False
    bl = _fetch_untrusted_providers()
    if bl is None:
        return False
    url_lower = str(base_url).lower()
    return any((s in url_lower) for s in bl)

# 初始化OpenAI客户端
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

#初始化在线 AI 客户端 (如果启用)
online_client: Optional[OpenAI] = None
if ENABLE_ONLINE_API:
    try:
        online_client = OpenAI(
            api_key=ONLINE_API_KEY,
            base_url=ONLINE_BASE_URL
        )
        logger.info("联网搜索 API 客户端已初始化。")
    except Exception as e:
        logger.error(f"初始化联网搜索 API 客户端失败: {e}", exc_info=True)
        ENABLE_ONLINE_API = False # 初始化失败则禁用该功能
        logger.warning("由于初始化失败，联网搜索功能已被禁用。")

# 初始化辅助模型客户端 (如果启用)
assistant_client: Optional[OpenAI] = None
if ENABLE_ASSISTANT_MODEL:
    try:
        assistant_client = OpenAI(
            api_key=ASSISTANT_API_KEY,
            base_url=ASSISTANT_BASE_URL
        )
        logger.info("辅助模型 API 客户端已初始化。")
    except Exception as e:
        logger.error(f"初始化辅助模型 API 客户端失败: {e}", exc_info=True)
        ENABLE_ASSISTANT_MODEL = False # 初始化失败则禁用该功能
        logger.warning("由于初始化失败，辅助模型功能已被禁用。")

def get_chat_type_info(user_name):
    """
    获取指定用户的聊天窗口类型信息（群聊或私聊）
    
    Args:
        user_name (str): 用户昵称
        
    Returns:
        bool: True表示群聊，False表示私聊，None表示未找到或出错
    """
    try:
        # 获取所有聊天窗口
        chats = wx.GetAllSubWindow()
        for chat in chats:
            chat_info = chat.ChatInfo()
            # 获取聊天窗口的名称/标识符
            chat_who = getattr(chat, 'who', None) or chat_info.get('who', None)
            
            # 只处理匹配的聊天窗口
            if chat_who == user_name:
                chat_type = chat_info.get('chat_type')
                is_group = (chat_type == 'group')
                logger.info(f"找到用户 '{user_name}' 的聊天类型: {chat_type} ({'群聊' if is_group else '私聊'})")
                return is_group
        
        logger.warning(f"未找到用户 '{user_name}' 的聊天窗口信息")
        return None
        
    except Exception as e:
        logger.error(f"获取用户 '{user_name}' 聊天类型时出错: {e}")
        return None

def update_group_chat_cache():
    """
    更新群聊缓存信息
    """
    global group_chat_cache
    
    try:
        with group_cache_lock:
            logger.info("开始更新群聊类型缓存...")
            for user_name in user_names:
                chat_type_result = get_chat_type_info(user_name)
                if chat_type_result is not None:
                    group_chat_cache[user_name] = chat_type_result
                    logger.info(f"缓存用户 '{user_name}': {'群聊' if chat_type_result else '私聊'}")
                else:
                    logger.warning(f"无法确定用户 '{user_name}' 的聊天类型，将默认处理为私聊")
                    group_chat_cache[user_name] = False
            
            logger.info(f"群聊类型缓存更新完成，共缓存 {len(group_chat_cache)} 个用户信息")
            
    except Exception as e:
        logger.error(f"更新群聊缓存时出错: {e}")

def is_user_group_chat(user_name):
    """
    检查指定用户是否为群聊
    
    Args:
        user_name (str): 用户昵称
        
    Returns:
        bool: True表示群聊，False表示私聊
    """
    with group_cache_lock:
        # 如果缓存中没有该用户信息，则实时获取
        if user_name not in group_chat_cache:
            chat_type_result = get_chat_type_info(user_name)
            if chat_type_result is not None:
                group_chat_cache[user_name] = chat_type_result
            else:
                # 如果无法获取，默认为私聊
                group_chat_cache[user_name] = False
        
        return group_chat_cache.get(user_name, False)

def parse_time(time_str):
    try:
        TimeResult = datetime.strptime(time_str, "%H:%M").time()
        return TimeResult
    except Exception as e:
        logger.error("\033[31m错误：主动消息安静时间设置有误！请填00:00-23:59 不要填24:00,并请注意中间的符号为英文冒号！\033[0m")

quiet_time_start = parse_time(QUIET_TIME_START)
quiet_time_end = parse_time(QUIET_TIME_END)

def check_user_timeouts():
    """
    检查用户是否超时未活动，并将主动消息加入队列以触发联网检查流程。
    线程持续运行，根据动态配置决定是否执行主动消息逻辑。
    """
    global last_received_message_timestamp # 引用全局变量
    
    while True:
        try:
            # 动态检查配置，如果关闭则跳过但不退出线程
            if not get_dynamic_config('ENABLE_AUTO_MESSAGE', ENABLE_AUTO_MESSAGE):
                time.sleep(5)  # 配置关闭时短暂休眠，以便快速响应配置变更
                continue
                
            current_epoch_time = time.time()

            for user in user_names:
                last_active = user_timers.get(user)
                wait_time = user_wait_times.get(user)

                if isinstance(last_active, (int, float)) and isinstance(wait_time, (int, float)):
                    if current_epoch_time - last_active >= wait_time and not is_quiet_time():
                        # 检查是否启用了忽略群聊主动消息的配置
                        if IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE and is_user_group_chat(user):
                            logger.info(f"用户 {user} 是群聊且配置为忽略群聊主动消息，跳过发送主动消息")
                            # 重置计时器以避免频繁检查
                            reset_user_timer(user)
                            continue
                        
                        # 构造主动消息（模拟用户消息格式）
                        formatted_now = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
                        auto_content = f"触发主动发消息：[{formatted_now}] {AUTO_MESSAGE}"
                        logger.info(f"为用户 {user} 生成主动消息并加入队列: {auto_content}")

                        # 将主动消息加入队列（模拟用户消息）
                        with queue_lock:
                            if user not in user_queues:
                                user_queues[user] = {
                                    'messages': [auto_content],
                                    'sender_name': user,
                                    'username': user,
                                    'last_message_time': time.time()
                                }
                            else:
                                user_queues[user]['messages'].append(auto_content)
                                user_queues[user]['last_message_time'] = time.time()

                        # 更新全局的最后消息活动时间戳，因为机器人主动发消息也算一种活动
                        last_received_message_timestamp = time.time()

                        # 重置计时器（不触发 on_user_message）
                        reset_user_timer(user)
                        
            time.sleep(10)  # 正常工作时的检查间隔
            
        except Exception as e:
            logger.error(f"主动消息检查线程异常: {e}", exc_info=True)
            time.sleep(10)  # 异常时也要休眠避免忙等

def reset_user_timer(user):
    user_timers[user] = time.time()
    user_wait_times[user] = get_random_wait_time()

def get_random_wait_time():
    return random.uniform(MIN_COUNTDOWN_HOURS, MAX_COUNTDOWN_HOURS) * 3600  # 转换为秒

# 当接收到用户的新消息时，调用此函数
def on_user_message(user):
    if user not in user_names:
        user_names.append(user)
    reset_user_timer(user)

# 修改get_user_prompt函数（增强路径验证）
def get_user_prompt(user_id):
    # 查找映射中的文件名，若不存在则使用user_id
    prompt_file = prompt_mapping.get(user_id, user_id)
    
    # 双重清理文件名
    safe_prompt_file = sanitize_user_id_for_filename(prompt_file)
    
    # 使用绝对路径并验证
    prompts_dir = os.path.abspath(os.path.join(root_dir, 'prompts'))
    prompt_path = os.path.abspath(os.path.join(prompts_dir, f'{safe_prompt_file}.md'))
    
    # 验证最终路径是否在预期目录内，防止路径遍历
    if not prompt_path.startswith(prompts_dir + os.sep):
        logger.error(f"检测到路径遍历尝试: user_id={user_id}, prompt_file={prompt_file}, path={prompt_path}")
        raise ValueError(f"非法的prompt文件路径访问尝试")
    
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt文件不存在: {prompt_path}")
        raise FileNotFoundError(f"Prompt文件 {safe_prompt_file}.md 未找到于 prompts 目录")

    # 增强编码处理的文件读取
    prompt_content = None
    try:
        with open(prompt_path, 'r', encoding='utf-8') as file:
            prompt_content = file.read()
    except UnicodeDecodeError as e:
        logger.warning(f"UTF-8解码失败，尝试其他编码格式: {prompt_path}, 错误: {e}")
        # 尝试常见的编码格式
        for encoding in ['gbk', 'gb2312', 'latin-1', 'cp1252']:
            try:
                with open(prompt_path, 'r', encoding=encoding) as file:
                    prompt_content = file.read()
                logger.info(f"成功使用 {encoding} 编码读取Prompt文件: {prompt_path}")
                # 重新以UTF-8编码保存文件
                backup_path = f"{prompt_path}.bak_{int(time.time())}"
                try:
                    shutil.copy(prompt_path, backup_path)
                    with open(prompt_path, 'w', encoding='utf-8') as file:
                        file.write(prompt_content)
                    logger.info(f"已将Prompt文件重新转换为UTF-8编码: {prompt_path} (备份: {backup_path})")
                except Exception as save_err:
                    logger.error(f"重新保存Prompt文件失败: {save_err}")
                break
            except (UnicodeDecodeError, Exception):
                continue
        else:
            # 所有编码都失败
            backup_path = f"{prompt_path}.corrupted_{int(time.time())}"
            try:
                shutil.copy(prompt_path, backup_path)
                logger.error(f"无法解码Prompt文件，已备份到: {backup_path}")
            except Exception as backup_err:
                logger.error(f"备份损坏Prompt文件失败: {backup_err}")
            raise UnicodeDecodeError(f"无法解码Prompt文件: {prompt_path}", b'', 0, 1, "所有编码格式都失败")
    
    if prompt_content is None:
        raise FileNotFoundError(f"无法读取Prompt文件内容: {prompt_path}")
    
    # 处理记忆的上传
    if not get_dynamic_config('UPLOAD_MEMORY_TO_AI', UPLOAD_MEMORY_TO_AI):
        # 如果不上传记忆到AI，则移除所有记忆片段
        memory_marker = "## 记忆片段"
        if memory_marker in prompt_content:
            prompt_content = prompt_content.split(memory_marker, 1)[0].strip()
        return prompt_content
    
    # 上传记忆到AI时，需要合并prompt文件中的记忆和JSON文件中的记忆
    json_memories = load_core_memory_from_json(user_id)
    json_memory_content = format_json_memories_for_prompt(json_memories)
    
    # 如果有JSON记忆需要添加
    if json_memory_content:
        # 找到prompt内容的结尾，添加JSON记忆
        if prompt_content.endswith('\n'):
            combined_content = prompt_content + '\n' + json_memory_content
        else:
            combined_content = prompt_content + '\n\n' + json_memory_content
        
        logger.debug(f"为用户 {user_id} 合并了 {len(json_memories)} 条JSON记忆到prompt中")
        return combined_content
    else:
        # 没有JSON记忆，直接返回原始prompt内容
        return prompt_content
             
# 加载聊天上下文
def load_chat_contexts():
    """从文件加载聊天上下文。"""
    global chat_contexts # 声明我们要修改全局变量
    try:
        if os.path.exists(CHAT_CONTEXTS_FILE):
            with open(CHAT_CONTEXTS_FILE, 'r', encoding='utf-8') as f:
                loaded_contexts = json.load(f)
                if isinstance(loaded_contexts, dict):
                    chat_contexts = loaded_contexts
                    logger.info(f"成功从 {CHAT_CONTEXTS_FILE} 加载 {len(chat_contexts)} 个用户的聊天上下文。")
                else:
                    logger.warning(f"{CHAT_CONTEXTS_FILE} 文件内容格式不正确（非字典），将使用空上下文。")
                    chat_contexts = {} # 重置为空
        else:
            logger.info(f"{CHAT_CONTEXTS_FILE} 未找到，将使用空聊天上下文启动。")
            chat_contexts = {} # 初始化为空
    except json.JSONDecodeError:
        logger.error(f"解析 {CHAT_CONTEXTS_FILE} 失败，文件可能已损坏。将使用空上下文。")
        # 可以考虑在这里备份损坏的文件
        # shutil.copy(CHAT_CONTEXTS_FILE, CHAT_CONTEXTS_FILE + ".corrupted")
        chat_contexts = {} # 重置为空
    except Exception as e:
        logger.error(f"加载聊天上下文失败: {e}", exc_info=True)
        chat_contexts = {} # 出现其他错误也重置为空，保证程序能启动

def merge_context(context_list):
    """
    合并连续相同 role 的消息，保证 user/assistant 交替。
    """
    if not context_list:
        return []
    merged = []
    last_role = None
    buffer = []
    for item in context_list:
        role = item.get('role')
        content = item.get('content', '')
        if role == last_role:
            buffer.append(content)
        else:
            if buffer:
                merged.append({'role': last_role, 'content': '\n'.join(buffer)})
            buffer = [content]
            last_role = role
    if buffer:
        merged.append({'role': last_role, 'content': '\n'.join(buffer)})
    return merged

# 保存聊天上下文
def save_chat_contexts():
    """将当前聊天上下文保存到文件。"""
    global chat_contexts
    temp_file_path = CHAT_CONTEXTS_FILE + ".tmp"
    try:
        # 创建要保存的上下文副本，以防在写入时被其他线程修改
        # 如果在 queue_lock 保护下调用，则直接使用全局 chat_contexts 即可
        contexts_to_save = dict(chat_contexts) # 创建浅拷贝
        # --- 新增：保存前合并每个用户的上下文 ---
        for user in contexts_to_save:
            contexts_to_save[user] = merge_context(contexts_to_save[user])
        # --- END ---
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            json.dump(contexts_to_save, f, ensure_ascii=False, indent=4)
        shutil.move(temp_file_path, CHAT_CONTEXTS_FILE) # 原子替换
        logger.debug(f"聊天上下文已成功保存到 {CHAT_CONTEXTS_FILE}")
    except Exception as e:
        logger.error(f"保存聊天上下文到 {CHAT_CONTEXTS_FILE} 失败: {e}", exc_info=True)
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path) # 清理临时文件
            except OSError:
                pass # 忽略清理错误

def get_deepseek_response(message, user_id, store_context=True, is_summary=False):
    """
    从 DeepSeek API 获取响应，确保正确的上下文处理，并持久化上下文。

    参数:
        message (str): 用户的消息或系统提示词（用于工具调用）。
        user_id (str): 用户或系统组件的标识符。
        store_context (bool): 是否将此交互存储到聊天上下文中。
                              对于工具调用（如解析或总结），设置为 False。
    """
    try:
        # 每次调用都重新加载聊天上下文，以应对文件被外部修改的情况
        load_chat_contexts()
        
        logger.info(f"调用 Chat API - ID: {user_id}, 是否存储上下文: {store_context}, 消息: {message[:100]}...") # 日志记录消息片段

        messages_to_send = []
        context_limit = MAX_GROUPS * 2  # 最大消息总数（不包括系统消息）

        if store_context:
            # --- 处理需要上下文的常规聊天消息 ---
            # 1. 获取该用户的系统提示词
            try:
                user_prompt = get_user_prompt(user_id)
                messages_to_send.append({"role": "system", "content": user_prompt})
            except FileNotFoundError as e:
                logger.error(f"用户 {user_id} 的提示文件错误: {e}，使用默认提示。")
                messages_to_send.append({"role": "system", "content": "你是一个乐于助人的助手。"})

            # 2. 管理并检索聊天历史记录
            with queue_lock: # 确保对 chat_contexts 的访问是线程安全的
                if user_id not in chat_contexts:
                    chat_contexts[user_id] = []

                # 在添加当前消息之前获取现有历史记录
                history = list(chat_contexts.get(user_id, []))  # 获取副本

                # 如果历史记录超过限制，则进行裁剪
                if len(history) > context_limit:
                    history = history[-context_limit:]  # 保留最近的消息

                # 将历史消息添加到 API 请求列表中
                messages_to_send.extend(history)

                # 3. 将当前用户消息添加到 API 请求列表中
                messages_to_send.append({"role": "user", "content": message})

                # 4. 在准备 API 调用后更新持久上下文
                # 将用户消息添加到持久存储中
                chat_contexts[user_id].append({"role": "user", "content": message})
                # 如果需要，裁剪持久存储（在助手回复后会再次裁剪）
                if len(chat_contexts[user_id]) > context_limit + 1:  # +1 因为刚刚添加了用户消息
                    chat_contexts[user_id] = chat_contexts[user_id][-(context_limit + 1):]
                
                # 保存上下文到文件
                save_chat_contexts() # 在用户消息添加后保存一次

        else:
            # --- 处理工具调用（如提醒解析、总结） ---
            messages_to_send.append({"role": "user", "content": message})
            logger.info(f"工具调用 (store_context=False)，ID: {user_id}。仅发送提供的消息。")

        # --- 调用 API ---
        reply = call_chat_api_with_retry(messages_to_send, user_id, is_summary=is_summary)

        # --- 如果需要，存储助手回复到上下文中 ---
        if store_context:
            with queue_lock: # 再次获取锁来更新和保存
                if user_id not in chat_contexts:
                   chat_contexts[user_id] = []  # 安全初始化 (理论上此时应已存在)

                chat_contexts[user_id].append({"role": "assistant", "content": reply})

                if len(chat_contexts[user_id]) > context_limit:
                    chat_contexts[user_id] = chat_contexts[user_id][-context_limit:]
                
                # 保存上下文到文件
                save_chat_contexts() # 在助手回复添加后再次保存
        
        return reply

    except Exception as e:
        logger.error(f"Chat 调用失败 (ID: {user_id}): {str(e)}", exc_info=True)
        return "抱歉，我现在有点忙，稍后再聊吧。"


def strip_before_thought_tags(text):
    # 匹配并截取 </thought> 或 </think> 后面的内容
    if text is None:
        return None
    match = re.search(r'(?:</thought>|</think>)([\s\S]*)', text)
    if match:
        return match.group(1)
    else:
        return text

def call_chat_api_with_retry(messages_to_send, user_id, max_retries=2, is_summary=False):
    """
    调用 Chat API 并在第一次失败或返回空结果时重试。

    参数:
        messages_to_send (list): 要发送给 API 的消息列表。
        user_id (str): 用户或系统组件的标识符。
        max_retries (int): 最大重试次数。

    返回:
        str: API 返回的文本回复。
    """
    if _is_base_url_untrusted(DEEPSEEK_BASE_URL):
        logger.error("抱歉，您所使用的API服务商不受信任，请联系网站管理员")
        raise RuntimeError("抱歉，您所使用的API服务商不受信任，请联系网站管理员")

    attempt = 0
    while attempt <= max_retries:
        try:
            logger.debug(f"发送给 API 的消息 (ID: {user_id}): {messages_to_send}")

            response = client.chat.completions.create(
                model=MODEL,
                messages=messages_to_send,
                temperature=TEMPERATURE,
                max_tokens=MAX_TOKEN,
                stream=False
            )

            if response.choices:
                # 检查API是否返回了空的消息内容
                message_content = response.choices[0].message.content
                if message_content is None:
                    logger.error(f"API返回了空的信息，可能是因为触发了安全检查机制，请修改Prompt并清空上下文再试 (ID: {user_id})")
                    logger.error(f"错误请求消息体模型: {MODEL}")
                    logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
                    logger.error(f"完整响应对象: {response}")
                else:
                    content = message_content.strip()
                    if content and "[image]" not in content and content != "ext":
                        filtered_content = strip_before_thought_tags(content)
                        if filtered_content:
                            return filtered_content
            else:
                # 记录错误日志 - 无选择项
                logger.error(f"API返回了空的选择项 (ID: {user_id})")
                logger.error(f"错误请求消息体模型: {MODEL}")
                logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
                logger.error(f"完整响应对象: {response}")

            # 如果到这里说明内容为空或过滤后为空
            if response.choices and response.choices[0].message.content is not None:
                logger.error(f"API返回了空的内容或内容被过滤 (ID: {user_id})")
            logger.error(f"错误请求消息体模型: {MODEL}")
            logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))

        except Exception as e:
            logger.error(f"错误请求消息体: {MODEL}")
            logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
            error_info = str(e)
            logger.error(f"自动重试：第 {attempt + 1} 次调用 {MODEL}失败 (ID: {user_id}) 原因: {error_info}", exc_info=False)

            # 细化错误分类
            if "real name verification" in error_info:
                logger.error("\033[31m错误：API 服务商反馈请完成实名认证后再使用！\033[0m")
                break  # 终止循环，不再重试
            elif "rate limit" in error_info:
                logger.error("\033[31m错误：API 服务商反馈当前访问 API 服务频次达到上限，请稍后再试！\033[0m")
            elif "payment required" in error_info:
                logger.error("\033[31m错误：API 服务商反馈您正在使用付费模型，请先充值再使用或使用免费额度模型！\033[0m")
                break  # 终止循环，不再重试
            elif "user quota" in error_info or "is not enough" in error_info or "UnlimitedQuota" in error_info:
                logger.error("\033[31m错误：API 服务商反馈，你的余额不足，请先充值再使用! 如有余额，请检查令牌是否为无限额度。\033[0m")
                break  # 终止循环，不再重试
            elif "Api key is invalid" in error_info:
                logger.error("\033[31m错误：API 服务商反馈 API KEY 不可用，请检查配置选项！\033[0m")
            elif "service unavailable" in error_info:
                logger.error("\033[31m错误：API 服务商反馈服务器繁忙，请稍后再试！\033[0m")
            elif "sensitive words detected" in error_info or "sensitive" in error_info:
                logger.error("\033[31m错误：Prompt或消息中含有敏感词，无法生成回复，请联系API服务商！\033[0m")
                if ENABLE_SENSITIVE_CONTENT_CLEARING:
                    logger.warning(f"已开启敏感词自动清除上下文功能，开始清除用户 {user_id} 的聊天上下文和临时记忆")
                    clear_chat_context(user_id)
                    clear_memory_temp_files(user_id)  # 清除临时记忆文件
                break  # 终止循环，不再重试
            else:
                logger.error("\033[31m未知错误：" + error_info + "\033[0m")

        attempt += 1

    raise RuntimeError("抱歉，我现在有点忙，稍后再聊吧。")

def get_assistant_response(message, user_id, is_summary=False):
    """
    从辅助模型 API 获取响应，专用于判断型任务（表情、联网、提醒解析等）。
    不存储聊天上下文，仅用于辅助判断。

    参数:
        message (str): 要发送给辅助模型的消息。
        user_id (str): 用户或系统组件的标识符。

    返回:
        str: 辅助模型返回的文本回复。
    """
    if not assistant_client:
        logger.warning(f"辅助模型客户端未初始化，回退使用主模型。用户ID: {user_id}")
        # 回退到主模型
        return get_deepseek_response(message, user_id, store_context=False, is_summary=is_summary)
    
    try:
        logger.info(f"调用辅助模型 API - ID: {user_id}, 消息: {message[:100]}...")
        
        messages_to_send = [{"role": "user", "content": message}]
        
        # 调用辅助模型 API
        reply = call_assistant_api_with_retry(messages_to_send, user_id, is_summary=is_summary)
        
        return reply

    except Exception as e:
        logger.error(f"辅助模型调用失败 (ID: {user_id}): {str(e)}", exc_info=True)
        logger.warning(f"辅助模型调用失败，回退使用主模型。用户ID: {user_id}")
        # 回退到主模型
        return get_deepseek_response(message, user_id, store_context=False, is_summary=is_summary)

def call_assistant_api_with_retry(messages_to_send, user_id, max_retries=2, is_summary=False):
    """
    调用辅助模型 API 并在第一次失败或返回空结果时重试。

    参数:
        messages_to_send (list): 要发送给辅助模型的消息列表。
        user_id (str): 用户或系统组件的标识符。
        max_retries (int): 最大重试次数。

    返回:
        str: 辅助模型返回的文本回复。
    """
    attempt = 0
    while attempt <= max_retries:
        try:
            logger.debug(f"发送给辅助模型 API 的消息 (ID: {user_id}): {messages_to_send}")

            response = assistant_client.chat.completions.create(
                model=ASSISTANT_MODEL,
                messages=messages_to_send,
                temperature=ASSISTANT_TEMPERATURE,
                max_tokens=ASSISTANT_MAX_TOKEN,
                stream=False
            )

            if response.choices:
                # 检查辅助模型API是否返回了空的消息内容
                message_content = response.choices[0].message.content
                if message_content is None:
                    logger.error(f"辅助模型API返回了空的信息，可能是因为触发了安全检查机制，请修改Prompt并清空上下文再试 (ID: {user_id})")
                    logger.error(f"辅助模型错误请求消息体: {ASSISTANT_MODEL}")
                    logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
                    logger.error(f"完整响应对象: {response}")
                else:
                    content = message_content.strip()
                    if content and "[image]" not in content:
                        filtered_content = strip_before_thought_tags(content)
                        if filtered_content:
                            return filtered_content
            else:
                # 记录错误日志 - 无选择项
                logger.error(f"辅助模型API返回了空的选择项 (ID: {user_id})")
                logger.error(f"辅助模型错误请求消息体: {ASSISTANT_MODEL}")
                logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
                logger.error(f"完整响应对象: {response}")

            # 如果到这里说明内容为空或过滤后为空
            if response.choices and response.choices[0].message.content is not None:
                logger.error(f"辅助模型API返回了空的内容或内容被过滤 (ID: {user_id})")
            logger.error(f"辅助模型错误请求消息体: {ASSISTANT_MODEL}")
            logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))

        except Exception as e:
            logger.error("辅助模型错误请求消息体:")
            logger.error(f"{ASSISTANT_MODEL}")
            logger.error(json.dumps(messages_to_send, ensure_ascii=False, indent=2))
            error_info = str(e)
            logger.error(f"辅助模型自动重试：第 {attempt + 1} 次调用失败 (ID: {user_id}) 原因: {error_info}", exc_info=False)

            # 细化错误分类
            if "real name verification" in error_info:
                logger.error("\033[31m错误：API 服务商反馈请完成实名认证后再使用！\033[0m")
                break  # 终止循环，不再重试
            elif "rate limit" in error_info:
                logger.error("\033[31m错误：API 服务商反馈当前访问 API 服务频次达到上限，请稍后再试！\033[0m")
            elif "payment required" in error_info:
                logger.error("\033[31m错误：API 服务商反馈您正在使用付费模型，请先充值再使用或使用免费额度模型！\033[0m")
                break  # 终止循环，不再重试
            elif "user quota" in error_info or "is not enough" in error_info or "UnlimitedQuota" in error_info:
                logger.error("\033[31m错误：API 服务商反馈，你的余额不足，请先充值再使用! 如有余额，请检查令牌是否为无限额度。\033[0m")
                break  # 终止循环，不再重试
            elif "Api key is invalid" in error_info:
                logger.error("\033[31m错误：API 服务商反馈 API KEY 不可用，请检查配置选项！\033[0m")
            elif "service unavailable" in error_info:
                logger.error("\033[31m错误：API 服务商反馈服务器繁忙，请稍后再试！\033[0m")
            elif "sensitive words detected" in error_info or "sensitive_words_detected" in error_info:
                logger.error("\033[31m错误：提示词中含有敏感词，无法生成回复，请联系API服务商！\033[0m")
                if ENABLE_SENSITIVE_CONTENT_CLEARING:
                    logger.warning(f"已开启敏感词自动清除上下文功能，开始清除用户 {user_id} 的聊天上下文和临时记忆")
                    clear_chat_context(user_id)
                    clear_memory_temp_files(user_id)  # 清除临时记忆文件
                break  # 终止循环，不再重试
            else:
                logger.error("\033[31m未知错误：" + error_info + "\033[0m")

        attempt += 1

    raise RuntimeError("抱歉，辅助模型现在有点忙，稍后再试吧。")

def keep_alive():
    """
    定期检查监听列表，确保所有在 user_names 中的用户都被持续监听。
    如果发现有用户从监听列表中丢失，则会尝试重新添加。
    这是一个守护线程，用于增强程序的健壮性。
    """
    check_interval = 5  # 每30秒检查一次，避免过于频繁
    logger.info(f"窗口保活/监听守护线程已启动，每 {check_interval} 秒检查一次监听状态。")
    
    while True:
        try:
            # 获取当前所有正在监听的用户昵称集合
            current_listening_users = set(wx.listen.keys())
            
            # 获取应该被监听的用户昵称集合
            expected_users_to_listen = set(user_names)
            
            # 找出配置中应该监听但当前未在监听列表中的用户
            missing_users = expected_users_to_listen - current_listening_users
            
            if missing_users:
                logger.warning(f"检测到 {len(missing_users)} 个用户从监听列表中丢失: {', '.join(missing_users)}")
                for user in missing_users:
                    try:
                        logger.info(f"正在尝试重新添加用户 '{user}' 到监听列表...")
                        # 使用与程序启动时相同的回调函数 `message_listener` 重新添加监听
                        wx.AddListenChat(nickname=user, callback=message_listener)
                        logger.info(f"已成功将用户 '{user}' 重新添加回监听列表。")
                    except Exception as e:
                        logger.error(f"重新添加用户 '{user}' 到监听列表时失败: {e}", exc_info=True)
            else:
                # 使用 debug 级别，因为正常情况下这条日志会频繁出现，避免刷屏
                logger.debug(f"监听列表状态正常，所有 {len(expected_users_to_listen)} 个目标用户都在监听中。")

        except Exception as e:
            # 捕获在检查过程中可能发生的任何意外错误，使线程能继续运行
            logger.error(f"keep_alive 线程在检查监听列表时发生未知错误: {e}", exc_info=True)
            
        # 等待指定间隔后再进行下一次检查
        time.sleep(check_interval)

def message_listener(msg, chat):
    global can_send_messages
    who = chat.who 
    msgtype = msg.type
    original_content = msg.content
    sender = msg.sender
    msgattr = msg.attr
    logger.info(f'收到来自聊天窗口 "{who}" 中用户 "{sender}" 的原始消息 (类型: {msgtype}, 属性: {msgattr}): {original_content[:100]}')

    if msgattr == 'tickle':
        if "我拍了拍" in original_content:
            logger.info("检测到自己触发的拍一拍，已忽略。")
            return
        else:
            original_content = f"[收到拍一拍消息]：{original_content}"
    elif msgattr == 'self':
        # 保存机器人自己发送的消息，用于拍一拍自己和撤回功能
        if msgtype == 'text':
            global bot_last_sent_msg
            bot_last_sent_msg[who] = msg
            logger.debug(f"已保存机器人发送给 {who} 的最后消息对象")
            return
        else:
            logger.debug(f"非文本消息，已忽略。")
            return
    elif msgattr != 'friend':
        logger.info(f"非好友消息，已忽略。")
        return

    if msgtype == 'voice':
        voicetext = msg.to_text()
        original_content = (f"[语音消息]: {voicetext}")
    
    if msgtype == 'link':
        cardurl = msg.get_url()
        original_content = (f"[卡片链接]: {cardurl}")

    if msgtype == 'quote':
        # 引用消息处理
        quoted_msg = msg.quote_content
        if quoted_msg:
            original_content = f"[引用<{quoted_msg}>消息]: {msg.content}"
        else:
            original_content = msg.content
    
    if msgtype == 'merge':
        logger.info(f"收到合并转发消息，开始处理")
        mergecontent = msg.get_messages()
        logger.info(f"收到合并转发消息，处理完成")
        # mergecontent 是一个列表，每个元素是 [发送者, 内容, 时间]
        # 转换为多行文本，每行格式: [时间] 发送者: 内容
        if isinstance(mergecontent, list):
            merged_text_lines = []
            for item in mergecontent:
                if isinstance(item, list) and len(item) == 3:
                    sender, content, timestamp = item
                    # 修改这里的判断逻辑，正确处理WindowsPath对象
                    # 检查是否为WindowsPath对象
                    if hasattr(content, 'suffix') and str(content.suffix).lower() in ('.png', '.jpg', '.jpeg', '.gif', '.bmp'):
                        # 是WindowsPath对象且是图片
                        if ENABLE_IMAGE_RECOGNITION:
                            try:
                                logger.info(f"开始识别图片: {str(content)}")
                                # 将WindowsPath对象转换为字符串
                                image_path = str(content)
                                # 保存当前状态
                                original_can_send_messages = can_send_messages
                                # 处理图片
                                content = recognize_image_with_moonshot(image_path, is_emoji=False)
                                if content:
                                    logger.info(f"图片识别成功: {content}")
                                    content = f"[图片识别结果]: {content}"
                                else:
                                    content = "[图片识别结果]: 无法识别图片内容"
                                # 确保状态恢复
                                can_send_messages = original_can_send_messages
                            except Exception as e:
                                content = "[图片识别失败]"
                                logger.error(f"图片识别失败: {e}")
                                # 确保状态恢复
                                can_send_messages = True
                        else:
                            content = "[图片]"
                    # 处理字符串路径的判断 (兼容性保留)
                    elif isinstance(content, str) and content.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                        if ENABLE_IMAGE_RECOGNITION:
                            try:
                                logger.info(f"开始识别图片: {content}")
                                # 保存当前状态
                                original_can_send_messages = can_send_messages
                                # 处理图片
                                image_content = recognize_image_with_moonshot(content, is_emoji=False)
                                if image_content:
                                    logger.info(f"图片识别成功: {image_content}")
                                    content = f"[图片识别结果]: {image_content}"
                                else:
                                    content = "[图片识别结果]: 无法识别图片内容"
                                # 确保状态恢复
                                can_send_messages = original_can_send_messages
                            except Exception as e:
                                content = "[图片识别失败]"
                                logger.error(f"图片识别失败: {e}")
                                # 确保状态恢复
                                can_send_messages = True
                        else:
                            content = "[图片]"
                    merged_text_lines.append(f"[{timestamp}] {sender}: {content}")
                else:
                    merged_text_lines.append(str(item))
            merged_text = "\n".join(merged_text_lines)
            original_content = f"[合并转发消息]:\n{merged_text}"
        else:
            original_content = f"[合并转发消息]: {mergecontent}"
    
    # 在处理完所有消息类型后检查内容是否为空
    if not original_content:
        logger.info("消息内容为空，已忽略。")
        return
        
    should_process_this_message = False
    content_for_handler = original_content 

    is_group_chat = is_user_group_chat(who)

    if not is_group_chat: 
        if who in user_names:
            should_process_this_message = True
            logger.info(f"收到来自监听列表用户 {who} 的个人私聊消息，准备处理。")
        else:
            logger.info(f"收到来自用户 {sender} (聊天窗口 {who}) 的个人私聊消息，但用户 {who} 不在监听列表或发送者与聊天窗口不符，已忽略。")
    else: 
        processed_group_content = original_content 
        at_triggered = False
        keyword_triggered = False

        if not ACCEPT_ALL_GROUP_CHAT_MESSAGES and ENABLE_GROUP_AT_REPLY and ROBOT_WX_NAME:
            temp_content_after_at_check = processed_group_content
            
            unicode_at_pattern = f'@{re.escape(ROBOT_WX_NAME)}\u2005'
            space_at_pattern = f'@{re.escape(ROBOT_WX_NAME)} '
            exact_at_string = f'@{re.escape(ROBOT_WX_NAME)}'
            
            if re.search(unicode_at_pattern, processed_group_content):
                at_triggered = True
                temp_content_after_at_check = re.sub(unicode_at_pattern, '', processed_group_content, 1).strip()
            elif re.search(space_at_pattern, processed_group_content):
                at_triggered = True
                temp_content_after_at_check = re.sub(space_at_pattern, '', processed_group_content, 1).strip()
            elif processed_group_content.strip() == exact_at_string:
                at_triggered = True
                temp_content_after_at_check = ''
                
            if at_triggered:
                logger.info(f"群聊 '{who}' 中检测到 @机器人。")
                processed_group_content = temp_content_after_at_check

        if ENABLE_GROUP_KEYWORD_REPLY:
            if any(keyword in processed_group_content for keyword in GROUP_KEYWORD_LIST):
                keyword_triggered = True
                logger.info(f"群聊 '{who}' 中检测到关键词。")
        
        basic_trigger_met = ACCEPT_ALL_GROUP_CHAT_MESSAGES or at_triggered or keyword_triggered

        if basic_trigger_met:
            if not ACCEPT_ALL_GROUP_CHAT_MESSAGES:
                if at_triggered and keyword_triggered:
                    logger.info(f"群聊 '{who}' 消息因 @机器人 和关键词触发基本处理条件。")
                elif at_triggered:
                    logger.info(f"群聊 '{who}' 消息因 @机器人 触发基本处理条件。")
                elif keyword_triggered:
                    logger.info(f"群聊 '{who}' 消息因关键词触发基本处理条件。")
            else:
                logger.info(f"群聊 '{who}' 消息符合全局接收条件，触发基本处理条件。")

            if keyword_triggered and GROUP_KEYWORD_REPLY_IGNORE_PROBABILITY:
                should_process_this_message = True
                logger.info(f"群聊 '{who}' 消息因触发关键词且配置为忽略回复概率，将进行处理。")
            elif random.randint(1, 100) <= GROUP_CHAT_RESPONSE_PROBABILITY:
                should_process_this_message = True
                logger.info(f"群聊 '{who}' 消息满足基本触发条件并通过总回复概率 {GROUP_CHAT_RESPONSE_PROBABILITY}%，将进行处理。")
            else:
                should_process_this_message = False
                logger.info(f"群聊 '{who}' 消息满足基本触发条件，但未通过总回复概率 {GROUP_CHAT_RESPONSE_PROBABILITY}%，将忽略。")
        else:
            should_process_this_message = False
            logger.info(f"群聊 '{who}' 消息 (发送者: {sender}) 未满足任何基本触发条件（全局、@、关键词），将忽略。")
        
        if should_process_this_message:
            if not msgtype == 'image':
                content_for_handler = f"[群聊消息-来自群'{who}'-发送者:{sender}]:{processed_group_content}"
            else:
                content_for_handler = processed_group_content
            
            if not content_for_handler and at_triggered and not keyword_triggered: 
                logger.info(f"群聊 '{who}' 中单独 @机器人，处理后内容为空，仍将传递给后续处理器。")
    
    if should_process_this_message:
        msg.content = content_for_handler 
        logger.info(f'最终准备处理消息 from chat "{who}" by sender "{sender}": {msg.content[:100]}')
        
        # 保存用户最后发送的消息对象，用于拍一拍功能
        global user_last_msg
        if not is_user_group_chat(who):  # 只在个人聊天中保存用户消息
            user_last_msg[who] = msg
            logger.debug(f"已保存用户 {who} 的最后消息对象")
        
        if msgtype == 'emotion':
            is_animation_emoji_in_original = True
        else:
            is_animation_emoji_in_original = False
        if is_animation_emoji_in_original and ENABLE_EMOJI_RECOGNITION:
            handle_emoji_message(msg, who)
        else:
            handle_wxauto_message(msg, who)

def recognize_image_with_moonshot(image_path, is_emoji=False):
    # 先暂停向API发送消息队列
    global can_send_messages
    can_send_messages = False

    """使用AI识别图片内容并返回文本"""
    try:

        processed_image_path = image_path
        
        # 读取图片内容并编码
        with open(processed_image_path, 'rb') as img_file:
            image_content = base64.b64encode(img_file.read()).decode('utf-8')
            
        headers = {
            'Authorization': f'Bearer {MOONSHOT_API_KEY}',
            'Content-Type': 'application/json'
        }
        text_prompt = "请用中文描述这张图片的主要内容或主题。不要使用'这是'、'这张'等开头，直接描述。如果有文字，请包含在描述中。" if not is_emoji else "请用中文简洁地描述这个聊天窗口最后一张表情包所表达的情绪、含义或内容。如果表情包含文字，请一并描述。注意：1. 只描述表情包本身，不要添加其他内容 2. 不要出现'这是'、'这个'等词语"
        data = {
            "model": MOONSHOT_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_content}"}},
                        {"type": "text", "text": text_prompt}
                    ]
                }
            ],
            "temperature": MOONSHOT_TEMPERATURE
        }
        
        response = requests.post(f"{MOONSHOT_BASE_URL}/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        recognized_text = result['choices'][0]['message']['content']
        
        if is_emoji:
            # 如果recognized_text包含"最后一张表情包是"，只保留后面的文本
            if "最后一张表情包" in recognized_text:
                recognized_text = recognized_text.split("最后一张表情包", 1)[1].strip()
            recognized_text = "发送了表情包：" + recognized_text
        else:
            recognized_text = "发送了图片：" + recognized_text
            
        logger.info(f"AI图片识别结果: {recognized_text}")
        
        # 清理临时文件
        if is_emoji and os.path.exists(processed_image_path):
            try:
                os.remove(processed_image_path)
                logger.debug(f"已清理临时表情: {processed_image_path}")
            except Exception as clean_err:
                logger.warning(f"清理临时表情图片失败: {clean_err}")
                
        # 恢复向Deepseek发送消息队列
        can_send_messages = True
        return recognized_text

    except Exception as e:
        logger.error(f"调用AI识别图片失败: {str(e)}", exc_info=True)
        # 恢复向Deepseek发送消息队列
        can_send_messages = True
        return ""

def handle_emoji_message(msg, who):
    global emoji_timer
    global can_send_messages
    can_send_messages = False

    def timer_callback():
        with emoji_timer_lock:           
            handle_wxauto_message(msg, who)   
            emoji_timer = None       

    with emoji_timer_lock:
        if emoji_timer is not None:
            emoji_timer.cancel()
        emoji_timer = threading.Timer(3.0, timer_callback)
        emoji_timer.start()

def is_safe_url(url: str) -> bool:
    """
    验证URL是否安全，防止SSRF攻击。
    
    Args:
        url: 要验证的URL
        
    Returns:
        bool: URL是否安全
    """
    try:
        import socket
        import ipaddress
        
        parsed = urlparse(url)
        
        # 1. 只允许HTTP和HTTPS协议
        if parsed.scheme not in ['http', 'https']:
            logger.warning(f"不允许的协议: {parsed.scheme}")
            return False
        
        # 2. 获取主机名
        hostname = parsed.hostname
        if not hostname:
            logger.warning("URL中没有主机名")
            return False
        
        # 3. 解析IP地址并检查是否为内网地址
        try:
            # 解析域名到IP
            ip = socket.gethostbyname(hostname)
            ip_obj = ipaddress.ip_address(ip)
            
            # 检查是否为私有地址、回环地址、链路本地地址等
            if (ip_obj.is_private or 
                ip_obj.is_loopback or 
                ip_obj.is_link_local or
                ip_obj.is_reserved or
                ip_obj.is_multicast):
                logger.warning(f"禁止访问内网/特殊地址: {hostname} -> {ip}")
                return False
            
            # 额外检查：禁止访问云元数据服务
            if ip.startswith('169.254.'):
                logger.warning(f"禁止访问云元数据服务: {hostname} -> {ip}")
                return False
                
        except socket.gaierror:
            # 域名无法解析
            logger.warning(f"域名无法解析: {hostname}")
            return False
        except ValueError as e:
            # IP地址格式错误
            logger.warning(f"IP地址格式错误: {hostname}, 错误: {e}")
            return False
        
        # 4. 端口限制（只允许常见的HTTP/HTTPS端口）
        port = parsed.port
        allowed_ports = [80, 443, 8080, 8443]
        if port and port not in allowed_ports:
            logger.warning(f"不允许的端口: {port}, 仅允许: {allowed_ports}")
            return False
        
        # 5. URL长度限制
        if len(url) > 2048:
            logger.warning(f"URL过长: {len(url)} 字符")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"URL安全验证失败: {url}, 错误: {e}")
        return False

def fetch_and_extract_text(url: str) -> Optional[str]:
    """
    获取给定 URL 的网页内容并提取主要文本。
    已增强SSRF防护。

    Args:
        url (str): 要抓取的网页链接。

    Returns:
        Optional[str]: 提取并清理后的网页文本内容（限制了最大长度），如果失败则返回 None。
    """
    try:
        # SSRF防护 - 验证URL安全性
        if not is_safe_url(url):
            logger.warning(f"URL安全检查失败，拒绝访问: {url}")
            return None
        
        # 基本 URL 格式验证
        parsed_url = urlparse(url)
        if not all([parsed_url.scheme, parsed_url.netloc]):
             logger.warning(f"无效的URL格式，跳过抓取: {url}")
             return None

        headers = {'User-Agent': REQUESTS_USER_AGENT}
        logger.info(f"开始抓取链接内容: {url}")
        
        # 使用自定义Session处理跳转，验证每个跳转目标的安全性
        session = requests.Session()
        session.headers.update(headers)
        session.max_redirects = 5  # 限制最大跳转次数
        
        # 创建钩子函数，在每次跳转前验证URL安全性
        def check_redirect_safety(response, *args, **kwargs):
            if response.is_redirect:
                redirect_url = response.headers.get('Location', '')
                # 处理相对路径
                from urllib.parse import urljoin
                absolute_redirect = urljoin(response.url, redirect_url)
                
                # 验证跳转目标的安全性
                if not is_safe_url(absolute_redirect):
                    logger.warning(f"跳转目标不安全，已阻止: {response.url} -> {absolute_redirect}")
                    raise requests.exceptions.RequestException(f"不安全的跳转目标: {absolute_redirect}")
                
                logger.debug(f"安全跳转: {response.url} -> {absolute_redirect}")
        
        session.hooks['response'].append(check_redirect_safety)
        
        # 发送请求，允许经过安全验证的跳转
        response = session.get(url, timeout=REQUESTS_TIMEOUT, allow_redirects=True, verify=True)
        response.raise_for_status()  # 检查HTTP请求是否成功 (状态码 2xx)

        # 检查内容类型，避免处理非HTML内容（如图片、PDF等）
        content_type = response.headers.get('Content-Type', '').lower()
        if 'html' not in content_type:
            logger.warning(f"链接内容类型非HTML ({content_type})，跳过文本提取: {url}")
            return None

        # 使用BeautifulSoup解析HTML
        # 指定 lxml 解析器以获得更好的性能和兼容性
        soup = BeautifulSoup(response.content, 'lxml') # 使用 response.content 获取字节流，让BS自动处理编码

        # --- 文本提取策略 ---
        # 尝试查找主要内容区域 (这部分可能需要根据常见网站结构调整优化)
        main_content_tags = ['article', 'main', '.main-content', '#content', '.post-content'] # 示例选择器
        main_text = ""
        for tag_selector in main_content_tags:
            element = soup.select_one(tag_selector)
            if element:
                main_text = element.get_text(separator='\n', strip=True)
                break # 找到一个就停止

        # 如果没有找到特定的主要内容区域，则获取整个 body 的文本作为备选
        if not main_text and soup.body:
            main_text = soup.body.get_text(separator='\n', strip=True)
        elif not main_text: # 如果连 body 都没有，则使用整个 soup
             main_text = soup.get_text(separator='\n', strip=True)

        # 清理文本：移除过多空行
        lines = [line for line in main_text.splitlines() if line.strip()]
        cleaned_text = '\n'.join(lines)

        # 限制内容长度
        if len(cleaned_text) > MAX_WEB_CONTENT_LENGTH:
            cleaned_text = cleaned_text[:MAX_WEB_CONTENT_LENGTH] + "..." # 截断并添加省略号
            logger.info(f"网页内容已提取，并截断至 {MAX_WEB_CONTENT_LENGTH} 字符。")
        elif cleaned_text:
            logger.info(f"成功提取网页文本内容 (长度 {len(cleaned_text)}).")
        else:
            logger.warning(f"未能从链接 {url} 提取到有效文本内容。")
            return None # 如果提取后为空，也视为失败

        return cleaned_text

    except requests.exceptions.Timeout:
        logger.error(f"抓取链接超时 ({REQUESTS_TIMEOUT}秒): {url}")
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"抓取链接时发生网络错误: {url}, 错误: {e}")
        return None
    except Exception as e:
        # 捕获其他可能的错误，例如 BS 解析错误
        logger.error(f"处理链接时发生未知错误: {url}, 错误: {e}", exc_info=True)
        return None

# 辅助函数：将用户消息记录到记忆日志 (如果启用)
def log_user_message_to_memory(username, original_content):
    """将用户的原始消息记录到记忆日志文件。"""
    if ENABLE_MEMORY:
        try:
            prompt_name = prompt_mapping.get(username, username)
            safe_username = sanitize_user_id_for_filename(username)
            safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
            log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{safe_username}_{safe_prompt_name}_log.txt')
            log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{username}] {original_content}\n"
            os.makedirs(os.path.dirname(log_file), exist_ok=True)
            
            # 增强编码处理的写入
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_entry)
            except UnicodeEncodeError as e:
                logger.warning(f"UTF-8编码失败，尝试清理特殊字符: {log_file}, 错误: {e}")
                # 清理无法编码的字符
                clean_content = original_content.encode('utf-8', errors='ignore').decode('utf-8')
                clean_log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{username}] {clean_content}\n"
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(clean_log_entry)
                logger.info(f"已清理特殊字符并写入记忆日志: {log_file}")
        except Exception as write_err:
             logger.error(f"写入用户 {username} 的记忆日志失败: {write_err}")

# --- 文本指令处理 ---
def _extract_command_from_text(raw_text: str) -> Optional[str]:
    try:
        if not isinstance(raw_text, str):
            return None
        text = raw_text.strip()
        # 群聊前缀形如: [群聊消息-来自群'XXX'-发送者:YYY]:实际内容
        if text.startswith("[群聊消息-"):
            sep = "]:"
            idx = text.find(sep)
            if idx != -1:
                text = text[idx + len(sep):].strip()
        # 仅识别以'/'开头的首行
        first_line = text.splitlines()[0].strip()
        if first_line.startswith('/'):
            return first_line
        return None
    except Exception:
        return None

def _update_config_boolean(key: str, value: bool) -> bool:
    """在 config.py 中更新布尔配置，同时更新内存变量。失败返回 False。"""
    try:
        config_path = os.path.join(root_dir, 'config.py')
        if not os.path.exists(config_path):
            logger.error(f"配置文件不存在: {config_path}")
            return False
        with open(config_path, 'r', encoding='utf-8') as f:
            content = f.read()
        pattern = rf"^({re.escape(key)})\s*=\s*(True|False|.+)$"
        replacement = f"{key} = {str(bool(value))}"
        new_content, count = re.subn(pattern, replacement, content, flags=re.M)
        if count == 0:
            # 若不存在该项，则追加
            new_content = content.rstrip("\n") + f"\n\n{replacement}\n"
        tmp_path = config_path + '.tmp'
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        shutil.move(tmp_path, config_path)
        # 同步到内存
        try:
            globals()[key] = bool(value)
        except Exception as e:
            logger.warning(f"更新内存配置失败 {key}: {e}")
        return True
    except Exception as e:
        logger.error(f"更新配置 {key} 失败: {e}")
        return False

def _schedule_restart(reason: str = "指令触发"):
    """延迟1.5秒执行重启，尽量保证提示消息已发送。"""
    def _do_restart():
        try:
            # 重启前清理与保存
            with queue_lock:
                save_chat_contexts()
            if get_dynamic_config('ENABLE_AUTO_MESSAGE', ENABLE_AUTO_MESSAGE):
                save_user_timers()
            if ENABLE_REMINDERS:
                with recurring_reminder_lock:
                    save_recurring_reminders()
            if 'async_http_handler' in globals() and isinstance(async_http_handler, AsyncHTTPHandler):
                try:
                    async_http_handler.close()
                except Exception:
                    pass
            clean_up_temp_files()
            logger.info(f"正在执行重启 (原因: {reason}) ...")
            
            # 使用subprocess.Popen而不是os.execv，避免命令注入风险
            import subprocess
            current_script = os.path.abspath(__file__)
            # 只传递脚本路径，不传递sys.argv中可能存在的危险参数
            subprocess.Popen(
                [sys.executable, current_script],
                cwd=os.path.dirname(current_script)
            )
            logger.info("新进程已启动，当前进程即将退出")
            sys.exit(0)
        except Exception as e:
            logger.error(f"执行重启失败: {e}", exc_info=True)
    threading.Timer(1.5, _do_restart).start()

def _handle_text_command_if_any(original_content: str, user_id: str) -> bool:
    """
    如检测到命令则执行并回复用户，返回 True 表示已处理并阻止后续流程。
    支持的命令：
    /重启 或 /re - 重启程序
    /关闭主动消息 或 /da - 关闭主动消息功能
    /开启主动消息 或 /ea - 开启主动消息功能
    /清除临时记忆 或 /cl - 清除当前聊天的临时上下文与记忆
    /允许语音通话 或 /ev - 允许使用语音通话提醒
    /禁止语音通话 或 /dv - 禁止使用语音通话提醒
    /总结 或 /ms - 立即进行一次临时记忆总结成记忆片段
    """
    try:
        # 动态检查文本命令开关
        if not get_dynamic_config('ENABLE_TEXT_COMMANDS', ENABLE_TEXT_COMMANDS):
            return False
        cmd = _extract_command_from_text(original_content)
        if not cmd:
            return False

        normalized = cmd.strip().replace('：', ':')
        reply_text = None

        if normalized == '/重启' or normalized == '/re':
            reply_text = '重启程序中，请稍后...'
            command_label = '[命令]/重启' if normalized == '/重启' else '[命令]/re'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            _schedule_restart('用户指令重启')
            return True

        if normalized == '/关闭主动消息' or normalized == '/da':
            ok = _update_config_boolean('ENABLE_AUTO_MESSAGE', False)
            reply_text = '已关闭主动消息功能。' if ok else '关闭失败，请稍后再试。'
            command_label = '[命令]/关闭主动消息' if normalized == '/关闭主动消息' else '[命令]/da'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            return True

        if normalized == '/开启主动消息' or normalized == '/ea':
            ok = _update_config_boolean('ENABLE_AUTO_MESSAGE', True)
            reply_text = '已开启主动消息功能。' if ok else '开启失败，请稍后再试。'
            command_label = '[命令]/开启主动消息' if normalized == '/开启主动消息' else '[命令]/ea'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            return True

        if normalized == '/清除临时记忆' or normalized == '/cl':
            try:
                clear_chat_context(user_id)
                clear_memory_temp_files(user_id)
                reply_text = '已清除当前聊天的临时上下文与临时记忆日志。'
            except Exception as e:
                logger.error(f"清除临时记忆失败: {e}")
                reply_text = '清除失败，请稍后再试。'
            command_label = '[命令]/清除临时记忆' if normalized == '/清除临时记忆' else '[命令]/cl'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            return True

        if normalized == '/允许语音通话' or normalized == '/ev':
            ok = _update_config_boolean('USE_VOICE_CALL_FOR_REMINDERS', True)
            reply_text = '已允许使用语音通话提醒。' if ok else '操作失败，请稍后再试。'
            command_label = '[命令]/允许语音通话' if normalized == '/允许语音通话' else '[命令]/ev'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            return True

        if normalized == '/禁止语音通话' or normalized == '/dv':
            ok = _update_config_boolean('USE_VOICE_CALL_FOR_REMINDERS', False)
            reply_text = '已禁止使用语音通话提醒。' if ok else '操作失败，请稍后再试。'
            command_label = '[命令]/禁止语音通话' if normalized == '/禁止语音通话' else '[命令]/dv'
            send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
            return True

        if normalized == '/总结' or normalized == '/ms':
            try:
                # 立即进行一次临时记忆总结，无论ENABLE_MEMORY是否启用
                reply_text = '正在进行记忆总结，请稍后...'
                command_label = '[命令]/总结' if normalized == '/总结' else '[命令]/ms'
                send_reply(user_id, user_id, user_id, command_label, reply_text, is_system_message=True)
                
                # 调用记忆总结功能，跳过记忆条目检查
                summarize_and_save(user_id, skip_check=True)
                
                # 发送完成消息
                success_text = '记忆总结已完成，记忆片段已保存。'
                send_reply(user_id, user_id, user_id, command_label, success_text, is_system_message=True)
                
            except Exception as e:
                logger.error(f"执行记忆总结失败: {e}")
                error_text = '记忆总结失败，请稍后再试。'
                send_reply(user_id, user_id, user_id, command_label, error_text, is_system_message=True)
            return True

        # 未匹配命令
        return False
    except Exception as e:
        logger.error(f"处理文本命令失败: {e}", exc_info=True)
        return False

def handle_wxauto_message(msg, who):
    """
    处理来自Wxauto的消息，包括可能的提醒、图片/表情、链接内容获取和常规聊天。
    """
    global can_send_messages # 引用全局变量以控制发送状态
    global last_received_message_timestamp # 引用全局变量以更新活动时间
    try:
        last_received_message_timestamp = time.time()
        username = who
        # 获取原始消息内容
        original_content = getattr(msg, 'content', None) or getattr(msg, 'text', None)

        # 如果消息内容为空，则直接返回
        if not original_content:
            logger.warning("收到的消息没有内容。")
            return

        # 文本指令优先处理（如/重启、/清除临时记忆等）
        try:
            if _handle_text_command_if_any(original_content, username):
                return
        except Exception as e:
            logger.error(f"指令解析失败: {e}")

        # 重置该用户的自动消息计时器
        on_user_message(username)

        # --- 1. 提醒检查 (基于原始消息内容) ---
        reminder_keywords = ["每日","每天","提醒","提醒我", "定时", "分钟后", "小时后", "计时", "闹钟", "通知我", "叫我", "提醒一下", "倒计时", "稍后提醒", "稍后通知", "提醒时间", "设置提醒", "喊我"]
        if ENABLE_REMINDERS and any(keyword in original_content for keyword in reminder_keywords):
            logger.info(f"检测到可能的提醒请求，用户 {username}: {original_content}")
            # 尝试解析并设置提醒
            reminder_set = try_parse_and_set_reminder(original_content, username)
            # 如果成功设置了提醒，则处理完毕，直接返回
            if reminder_set:
                logger.info(f"成功为用户 {username} 设置提醒，消息处理结束。")
                return # 停止进一步处理此消息

        # --- 2. 图片/表情处理 (基于原始消息内容) ---
        img_path = None         # 图片路径
        is_emoji = False        # 是否为表情包
        # processed_content 初始化为原始消息，后续步骤可能修改它
        processed_content = original_content

        # 检查是否为图片文件路径
        if msg.type in ('image'):
            if ENABLE_IMAGE_RECOGNITION:
                # 三次重试机制下载图片
                img_path = None
                for attempt in range(3):
                    try:
                        img_path = msg.download()
                        if img_path:
                            logger.info(f"图片下载成功 (第{attempt + 1}次尝试): {img_path}")
                            break
                        else:
                            logger.warning(f"图片下载失败 (第{attempt + 1}次尝试)")
                    except Exception as e:
                        logger.warning(f"图片下载异常 (第{attempt + 1}次尝试): {e}")
                    
                    if attempt < 2:  # 不是最后一次尝试
                        time.sleep(0.5)  # 等待0.5秒后重试
                
                if img_path:
                    is_emoji = False
                    processed_content = None # 标记为None，稍后会被识别结果替换
                    logger.info(f"检测到图片消息，准备识别: {img_path}")
                else:
                    logger.error("图片下载失败，已重试3次")
            else:
                logger.info("检测到图片消息，但图片识别功能已禁用。")

        # 检查是否为动画表情
        elif msg.type in ('emotion'):
            if ENABLE_EMOJI_RECOGNITION:
                # 三次重试机制截图表情
                img_path = None
                for attempt in range(3):
                    try:
                        img_path = msg.capture() # 截图
                        if img_path:
                            logger.info(f"表情截图成功 (第{attempt + 1}次尝试): {img_path}")
                            break
                        else:
                            logger.warning(f"表情截图失败 (第{attempt + 1}次尝试)")
                    except Exception as e:
                        logger.warning(f"表情截图异常 (第{attempt + 1}次尝试): {e}")
                    
                    if attempt < 2:  # 不是最后一次尝试
                        time.sleep(0.5)  # 等待0.5秒后重试
                
                if img_path:
                    is_emoji = True
                    processed_content = None # 标记为None，稍后会被识别结果替换
                    logger.info("检测到动画表情，准备截图识别...")
                else:
                    logger.error("表情截图失败，已重试3次")
            else:
                clean_up_temp_files() # 清理可能的临时文件
                logger.info("检测到动画表情，但表情识别功能已禁用。")

        # 如果需要进行图片/表情识别
        if img_path:
            logger.info(f"开始识别图片/表情 - 用户 {username}: {img_path}")
            # 调用识别函数
            recognized_text = recognize_image_with_moonshot(img_path, is_emoji=is_emoji)
            # 使用识别结果或回退占位符更新 processed_content
            processed_content = recognized_text if recognized_text else ("[图片]" if not is_emoji else "[动画表情]")
            clean_up_temp_files() # 清理临时截图文件
            can_send_messages = True # 确保识别后可以发送消息
            logger.info(f"图片/表情识别完成，结果: {processed_content}")

        # --- 3. 链接内容获取 (仅当ENABLE_URL_FETCHING为True且当前非图片/表情处理流程时) ---
        fetched_web_content = None
        # 只有在启用了URL抓取，并且当前处理的不是图片/表情（即processed_content不为None）时才进行
        if ENABLE_URL_FETCHING and processed_content is not None:
            # 使用正则表达式查找 URL
            url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
            urls_found = re.findall(url_pattern, original_content) # 仍在原始消息中查找URL

            if urls_found:
                # 优先处理第一个找到的有效链接
                url_to_fetch = urls_found[0]
                logger.info(f"检测到链接，用户 {username}，准备抓取: {url_to_fetch}")
                # 调用辅助函数抓取和提取文本
                fetched_web_content = fetch_and_extract_text(url_to_fetch)

                if fetched_web_content:
                    logger.info(f"成功获取链接内容摘要 (长度 {len(fetched_web_content)})。")
                    # 构建包含链接摘要的新消息内容，用于发送给AI
                    # 注意：这里替换了 processed_content，AI将收到包含原始消息和链接摘要的组合信息
                    processed_content = f"用户发送了消息：\"{original_content}\"\n其中包含的链接的主要内容摘要如下（可能不完整）：\n---\n{fetched_web_content}\n---\n"
                else:
                    logger.warning(f"未能从链接 {url_to_fetch} 提取有效文本内容。将按原始消息处理。")
                    # 如果抓取失败，processed_content 保持不变（可能是原始文本，或图片/表情占位符）
            # else: (如果没找到URL) 不需要操作，继续使用当前的 processed_content

        # --- 4. 记录用户消息到记忆 (如果启用) ---
        log_user_message_to_memory(username, processed_content)

        # --- 5. 将最终处理后的消息加入队列 ---
        # 只有在 processed_content 有效时才加入队列
        if processed_content:
            # 获取当前时间戳，添加到消息内容前
            current_time_str = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
            content_with_time = f"[{current_time_str}] {processed_content}" # 使用最终处理过的内容
            logger.info(f"准备将处理后的消息加入队列 - 用户 {username}: {content_with_time[:150]}...") # 日志截断防止过长

            sender_name = username # 发送者名字（对于好友聊天，who就是username）

            # 使用锁保护对共享队列的访问
            with queue_lock:
                # 如果用户队列不存在，则初始化
                if username not in user_queues:
                    user_queues[username] = {
                        'messages': [content_with_time],
                        'sender_name': sender_name,
                        'username': username,
                        'last_message_time': time.time()
                    }
                    logger.info(f"已为用户 {sender_name} 初始化消息队列并加入消息。")
                else:
                    # 用户队列已存在，追加消息并管理队列长度
                    user_queues[username]['messages'].append(content_with_time)
                    # 更新最后消息时间戳
                    user_queues[username]['last_message_time'] = time.time()
                    logger.info(f"用户 {sender_name} 的消息已加入队列（当前 {len(user_queues[username]['messages'])} 条）并更新时间。")
        else:
            # 如果经过所有处理后 processed_content 变为 None 或空字符串，则记录警告
            logger.warning(f"在处理后未找到用户 {username} 的可处理内容。原始消息: '{original_content}'")

    except Exception as e:
        can_send_messages = True # 确保发生错误时可以恢复发送消息
        logger.error(f"消息处理失败 (handle_wxauto_message): {str(e)}", exc_info=True)

def check_inactive_users():
    global can_send_messages
    while True:
        current_time = time.time()
        inactive_users = []
        with queue_lock:
            for username, user_data in user_queues.items():
                last_time = user_data.get('last_message_time', 0)
                if current_time - last_time > QUEUE_WAITING_TIME and can_send_messages and not is_sending_message: 
                    inactive_users.append(username)

        for username in inactive_users:
            process_user_messages(username)

        time.sleep(1)  # 每秒检查一次

def process_user_messages(user_id):
    """处理指定用户的消息队列，包括可能的联网搜索。"""
    global can_send_messages # 引用全局变量

    with queue_lock:
        if user_id not in user_queues:
            return
        # 从队列获取数据并移除该用户条目
        user_data = user_queues.pop(user_id)
        messages = user_data['messages']
        sender_name = user_data['sender_name']
        username = user_data['username'] # username 可能是群聊名或好友昵称

    # 合并消息
    merged_message = ' '.join(messages)
    logger.info(f"开始处理用户 '{sender_name}' (ID: {user_id}) 的合并消息: {merged_message[:100]}...")

    # 检查是否为主动消息
    is_auto_message = "触发主动发消息：" in merged_message
    
    reply = None
    online_info = None

    try:
        # --- 新增：联网搜索逻辑 ---
        if ENABLE_ONLINE_API:
            # 1. 检测是否需要联网
            search_content = needs_online_search(merged_message, user_id)
            if search_content:
                # 2. 如果需要，调用在线 API
                logger.info(f"尝试为用户 {user_id} 执行在线搜索...")
                merged_message = f"用户原始信息：\n{merged_message}\n\n需要进行联网搜索的信息：\n{search_content}"
                online_info = get_online_model_response(merged_message, user_id)

                if online_info:
                    # 3. 如果成功获取在线信息，构建新的提示给主 AI
                    logger.info(f"成功获取在线信息，为用户 {user_id} 准备最终回复...")
                    # 结合用户原始问题、在线信息，让主 AI 生成最终回复
                    # 注意：get_deepseek_response 会自动加载用户的 prompt 文件 (角色设定)
                    final_prompt = f"""
用户的原始问题是：
"{merged_message}"

根据以下联网搜索到的参考信息：
---
{online_info}
---

请结合你的角色设定，以自然的方式回答用户的原始问题。请直接给出回答内容，不要提及你是联网搜索的。
"""
                    # 调用主 AI 生成最终回复，存储上下文
                    reply = get_deepseek_response(final_prompt, user_id, store_context=True)
                    # 这里可以考虑如果在线信息是错误消息（如"在线搜索有点忙..."），是否要特殊处理
                    # 当前逻辑是：即使在线搜索返回错误信息，也会让主AI尝试基于这个错误信息来回复

                else:
                    # 在线搜索失败或未返回有效信息
                    logger.warning(f"在线搜索未能获取有效信息，用户: {user_id}。将按常规流程处理。")
                    # 这里可以选择发送一个错误提示，或者直接回退到无联网信息的回复
                    # 当前选择回退：下面会执行常规的 get_deepseek_response
                    pass # 继续执行下面的常规流程

        # --- 常规回复逻辑 (如果未启用联网、检测不需要联网、或联网失败) ---
        if reply is None: # 只有在尚未通过联网逻辑生成回复时才执行
            logger.info(f"为用户 {user_id} 执行常规回复（无联网信息）。")
            reply = get_deepseek_response(merged_message, user_id, store_context=True)

        # --- 发送最终回复 ---
        if reply:
            # 如果回复中包含思考标签（如 Deepseek R1），移除它
            if "</think>" in reply:
                reply = reply.split("</think>", 1)[1].strip()

            # 屏蔽记忆片段发送（如果包含）
            if "## 记忆片段" not in reply:
                send_reply(user_id, sender_name, username, merged_message, reply)
            else:
                logger.info(f"回复包含记忆片段标记，已屏蔽发送给用户 {user_id}。")
        else:
            logger.error(f"未能为用户 {user_id} 生成任何回复。")
            
    except Exception as e:
        if is_auto_message:
            # 如果是主动消息出错，只记录日志，不发送错误消息给用户
            logger.error(f"主动消息处理失败 (用户: {user_id}): {str(e)}")
            logger.info(f"主动消息API调用失败，已静默处理，不发送错误提示给用户 {user_id}")
        else:
            # 如果是正常用户消息出错，记录日志并重新抛出异常（保持原有的错误处理逻辑）
            logger.error(f"用户消息处理失败 (用户: {user_id}): {str(e)}")
            raise
        
def send_reply(user_id, sender_name, username, original_merged_message, reply, is_system_message=False):
    """发送回复消息，可能分段发送，并管理发送标志。
    
    Args:
        is_system_message: 如果为True，则不记录到Memory_Temp且不进行表情判断
    """
    global is_sending_message
    if not reply:
        logger.warning(f"尝试向 {user_id} 发送空回复。")
        return

    # --- 如果正在发送，等待 ---
    wait_start_time = time.time()
    MAX_WAIT_SENDING = 15.0  # 最大等待时间（秒）
    while is_sending_message:
        if time.time() - wait_start_time > MAX_WAIT_SENDING:
            logger.warning(f"等待 is_sending_message 标志超时，准备向 {user_id} 发送回复，继续执行。")
            break  # 避免无限等待
        logger.debug(f"等待向 {user_id} 发送回复，另一个发送正在进行中。")
        time.sleep(0.5)  # 短暂等待

    try:
        is_sending_message = True  # <<< 在发送前设置标志
        logger.info(f"准备向 {sender_name} (用户ID: {user_id}) 发送消息")

        # --- 表情包发送逻辑 ---
        emoji_path = None
        if ENABLE_EMOJI_SENDING and not is_system_message:
            emotion = is_emoji_request(reply)
            if emotion:
                logger.info(f"触发表情请求（概率{EMOJI_SENDING_PROBABILITY}%） 用户 {user_id}，情绪: {emotion}")
                emoji_path = send_emoji(emotion)

        # --- 文本消息处理 ---
        reply = remove_timestamps(reply)
        if REMOVE_PARENTHESES:
            reply = remove_parentheses_and_content(reply)
        parts = split_message_with_context(reply)

        if not parts:
            logger.warning(f"回复消息在分割/清理后为空，无法发送给 {user_id}。")
            is_sending_message = False
            return

        # --- 构建消息队列（文本+表情+拍一拍随机插入）---
        message_actions = []
        for part in parts:
            if part == '[tickle]':
                message_actions.append(('tickle', part))
            elif part == '[tickle_self]':
                message_actions.append(('tickle_self', part))
            elif part == '[recall]':
                message_actions.append(('recall', part))
            else:
                message_actions.append(('text', part))
        
        if emoji_path:
            # 随机选择插入位置（0到len(message_actions)之间，包含末尾）
            insert_pos = random.randint(0, len(message_actions))
            message_actions.insert(insert_pos, ('emoji', emoji_path))

        # --- 发送混合消息队列 ---
        for idx, (action_type, content) in enumerate(message_actions):
            if action_type == 'emoji':
                # 表情包发送三次重试
                success = False
                for attempt in range(3):
                    try:
                        if wx.SendFiles(filepath=content, who=user_id):
                            logger.info(f"已向 {user_id} 发送表情包")
                            success = True
                            break
                        else:
                            logger.warning(f"发送表情包失败，尝试第 {attempt + 1} 次")
                    except Exception as e:
                        logger.warning(f"发送表情包异常，尝试第 {attempt + 1} 次: {str(e)}")
                    
                    if attempt < 2:  # 不是最后一次尝试
                        time.sleep(0.5)  # 短暂等待后重试
                
                if not success:
                    logger.error(f"表情包发送失败，已重试3次")
                else:
                    time.sleep(random.uniform(0.5, 1.5))  # 表情包发送后随机延迟
            elif action_type == 'tickle':
                # 处理[tickle] - 拍一拍用户
                try:
                    global user_last_msg
                    if user_id in user_last_msg and user_last_msg[user_id]:
                        user_last_msg[user_id].tickle()
                        logger.info(f"已拍一拍用户 {user_id}")
                    else:
                        logger.warning(f"无法拍一拍用户 {user_id}，找不到用户最后发送的消息")
                except Exception as e:
                    logger.error(f"拍一拍用户失败: {str(e)}")
                time.sleep(random.uniform(2.0, 3.0))  # 拍一拍后延迟
            elif action_type == 'tickle_self':
                # 处理[tickle_self] - 拍一拍机器人自己的消息
                try:
                    global bot_last_sent_msg
                    if bot_last_sent_msg and user_id in bot_last_sent_msg and bot_last_sent_msg[user_id]:
                        bot_last_sent_msg[user_id].tickle()
                        logger.info(f"已拍一拍机器人发送给 {user_id} 的消息")
                    else:
                        logger.warning(f"无法拍一拍机器人发送给 {user_id} 的消息，找不到最后发送的消息")
                except Exception as e:
                    logger.error(f"拍一拍机器人消息失败: {str(e)}")
                time.sleep(random.uniform(2.0, 3.0))  # 拍一拍后延迟
            elif action_type == 'recall':
                # 处理[recall] - 撤回机器人上一条消息
                try:
                    if bot_last_sent_msg and user_id in bot_last_sent_msg and bot_last_sent_msg[user_id]:
                        # 延时确保撤回最新消息
                        time.sleep(random.uniform(3.0, 5.0))
                        bot_last_sent_msg[user_id].select_option('撤回')
                        logger.info(f"已撤回机器人发送给 {user_id} 的上一条消息")
                        # 撤回后清除记录的消息对象，避免重复撤回
                        bot_last_sent_msg[user_id] = None
                    else:
                        logger.warning(f"无法撤回机器人发送给 {user_id} 的消息，找不到最后发送的消息")
                except Exception as e:
                    logger.error(f"撤回机器人消息失败: {str(e)}")
                time.sleep(random.uniform(2.0, 3.0))  # 撤回后延迟
            else:
                # 文本消息发送三次重试
                success = False
                for attempt in range(3):
                    try:
                        if wx.SendMsg(msg=content, who=user_id):
                            logger.info(f"分段回复 {idx+1}/{len(message_actions)} 给 {sender_name}: {content[:50]}...")
                            if ENABLE_MEMORY and not is_system_message:
                                log_ai_reply_to_memory(username, content)
                            success = True
                            break
                        else:
                            logger.warning(f"发送文本消息失败，尝试第 {attempt + 1} 次")
                    except Exception as e:
                        logger.warning(f"发送文本消息异常，尝试第 {attempt + 1} 次: {str(e)}")
                    
                    if attempt < 2:  # 不是最后一次尝试
                        time.sleep(0.5)  # 短暂等待后重试
                
                if not success:
                    logger.error(f"文本消息发送失败，已重试3次: {content[:50]}...")

            # 处理分段延迟（仅当下一动作为文本时计算）
            if idx < len(message_actions) - 1:
                next_action = message_actions[idx + 1]
                if action_type == 'text' and next_action[0] == 'text':
                    next_part_len = len(next_action[1])
                    base_delay = next_part_len * AVERAGE_TYPING_SPEED
                    random_delay = random.uniform(RANDOM_TYPING_SPEED_MIN, RANDOM_TYPING_SPEED_MAX)
                    total_delay = max(1.0, base_delay + random_delay)
                    time.sleep(total_delay)
                else:
                    # 表情包前后使用固定随机延迟
                    time.sleep(random.uniform(0.5, 1.5))

    except Exception as e:
        logger.error(f"向 {user_id} 发送回复失败: {str(e)}", exc_info=True)
    finally:
        is_sending_message = False

def split_message_with_context(text):
    """
    将消息文本分割为多个部分，处理换行符、转义字符、$符号和[tickle]/[tickle_self]/[recall]标记。
    处理文本中的换行符和转义字符，并根据配置决定是否分割。
    无论配置如何，都会以$作为分隔符分割消息。
    特别支持[tickle]、[tickle_self]和[recall]作为独立消息分隔。
    
    特别说明：
    - 每个$都会作为独立分隔符，所以"Hello$World$Python"会分成三部分
    - 连续的$$会产生空部分，这些会被自动跳过
    - [tickle]、[tickle_self]和[recall]会被分隔成独立的消息段
    """
    result_parts = []
    
    # 首先处理[tickle]、[tickle_self]和[recall]标记，将其分隔成独立部分
    # 使用正则表达式分割，保留分隔符
    tickle_pattern = r'(\[tickle\]|\[tickle_self\]|\[recall\])'
    tickle_parts = re.split(tickle_pattern, text)
    
    # 对每个tickle分割的部分进行处理
    for tickle_part in tickle_parts:
        if not tickle_part:
            continue
            
        # 如果是tickle或recall标记，直接添加为独立部分
        if tickle_part in ['[tickle]', '[tickle_self]', '[recall]']:
            result_parts.append(tickle_part)
            continue
        
        # 对于非tickle标记的部分，继续应用原有的分隔逻辑
        # 首先用$符号分割文本（无论SEPARATE_ROW_SYMBOLS设置如何）
        dollar_parts = re.split(r'\$', tickle_part)
        
        # 对每个由$分割的部分应用原有的分隔逻辑
        for dollar_part in dollar_parts:
            # 跳过空的部分（比如连续的$$之间没有内容的情况）
            if not dollar_part.strip():
                continue
                
            # 应用原有的分隔逻辑
            if SEPARATE_ROW_SYMBOLS:
                main_parts = re.split(r'(?:\\{3,}|\n)', dollar_part)
            else:
                main_parts = re.split(r'\\{3,}', dollar_part)
                
            for part in main_parts:
                part = part.strip()
                if not part:
                    continue
                segments = []
                last_end = 0
                for match in re.finditer(r'\\', part):
                    pos = match.start()
                    should_split_at_current_pos = False
                    advance_by = 1
                    if pos + 1 < len(part) and part[pos + 1] == 'n':
                        should_split_at_current_pos = True
                        advance_by = 2
                    else:
                        prev_char = part[pos - 1] if pos > 0 else ''
                        is_last_char_in_part = (pos == len(part) - 1)
                        next_char = ''
                        if not is_last_char_in_part:
                            next_char = part[pos + 1]
                        if not is_last_char_in_part and \
                           re.match(r'[a-zA-Z0-9]', next_char) and \
                           (re.match(r'[a-zA-Z0-9]', prev_char) if prev_char else True):
                            should_split_at_current_pos = True
                        else:
                            is_in_emoticon = False
                            i = pos - 1
                            while i >= 0 and i > pos - 10:
                                if part[i] in '({[（【｛':
                                    is_in_emoticon = True
                                    break
                                if part[i].isalnum() and i < pos - 1:
                                    break
                                i -= 1
                            if not is_last_char_in_part and not is_in_emoticon:
                                _found_forward_emoticon_char = False
                                j = pos + 1
                                while j < len(part) and j < pos + 10:
                                    if part[j] in ')}]）】｝':
                                        _found_forward_emoticon_char = True
                                        break
                                    if part[j].isalnum() and j > pos + 1:
                                        break
                                    j += 1
                                if _found_forward_emoticon_char:
                                    is_in_emoticon = True
                            if not is_in_emoticon:
                                should_split_at_current_pos = True
                    if should_split_at_current_pos:
                        segment_to_add = part[last_end:pos].strip()
                        if segment_to_add:
                            segments.append(segment_to_add)
                        last_end = pos + advance_by
                if last_end < len(part):
                    final_segment = part[last_end:].strip()
                    if final_segment:
                        segments.append(final_segment)
                if segments:
                    result_parts.extend(segments)
                elif not segments and part:
                    result_parts.append(part)
                
    return [p for p in result_parts if p]

def remove_timestamps(text):
    """
    移除文本中所有[YYYY-MM-DD (Weekday) HH:MM(:SS)]格式的时间戳
    支持四种格式：
    1. [YYYY-MM-DD Weekday HH:MM:SS] - 带星期和秒
    2. [YYYY-MM-DD Weekday HH:MM] - 带星期但没有秒
    3. [YYYY-MM-DD HH:MM:SS] - 带秒但没有星期
    4. [YYYY-MM-DD HH:MM] - 基本格式
    并自动清理因去除时间戳产生的多余空格
    """
    # 定义支持多种格式的时间戳正则模式
    timestamp_pattern = r'''
        \[                # 起始方括号
        \d{4}             # 年份：4位数字
        -(?:0[1-9]|1[0-2])  # 月份：01-12 (使用非捕获组)
        -(?:0[1-9]|[12]\d|3[01]) # 日期：01-31 (使用非捕获组)
        (?:\s[A-Za-z]+)?  # 可选的星期部分
        \s                # 日期与时间之间的空格
        (?:2[0-3]|[01]\d) # 小时：00-23
        :[0-5]\d          # 分钟：00-59
        (?::[0-5]\d)?     # 可选的秒数
        \]                # 匹配结束方括号  <--- 修正点
    '''
    # 替换时间戳为空格
    text_no_timestamps = re.sub(
        pattern = timestamp_pattern,
        repl = ' ',  # 统一替换为单个空格 (lambda m: ' ' 与 ' ' 等效)
        string = text,
        flags = re.X | re.M # re.X 等同于 re.VERBOSE
    )
    # 清理可能产生的连续空格，将其合并为单个空格
    cleaned_text = re.sub(r'[^\S\r\n]+', ' ', text_no_timestamps)
    # 最后统一清理首尾空格
    return cleaned_text.strip()

def remove_parentheses_and_content(text: str) -> str:
    """
    去除文本中中文括号、英文括号及其中的内容。
    同时去除因移除括号而可能产生的多余空格（例如，连续空格变单个，每行首尾空格去除）。
    不去除其它符号和换行符。
    """
    processed_text = re.sub(r"\(.*?\)|（.*?）", "", text, flags=re.DOTALL)
    processed_text = re.sub(r" {2,}", " ", processed_text)
    lines = processed_text.split('\n')
    stripped_lines = [line.strip(" ") for line in lines]
    processed_text = "\n".join(stripped_lines)
    return processed_text

def is_emoji_request(text: str) -> Optional[str]:
    """使用AI判断消息情绪并返回对应的表情文件夹名称"""
    try:
        # 概率判断
        if ENABLE_EMOJI_SENDING and random.randint(0, 100) > EMOJI_SENDING_PROBABILITY:
            logger.info(f"未触发表情请求（概率{EMOJI_SENDING_PROBABILITY}%）")
            return None
        
        # 获取emojis目录下的所有情绪分类文件夹
        emoji_categories = [d for d in os.listdir(EMOJI_DIR) 
                            if os.path.isdir(os.path.join(EMOJI_DIR, d))]
        
        if not emoji_categories:
            logger.warning("表情包目录下未找到有效情绪分类文件夹")
            return None

        # 构造AI提示词
        prompt = f"""请判断以下消息表达的情绪，并仅回复一个词语的情绪分类：
{text}
可选的分类有：{', '.join(emoji_categories)}。请直接回复分类名称，不要包含其他内容，注意大小写。若对话未包含明显情绪，请回复None。"""

        # 根据配置选择使用辅助模型或主模型
        if ENABLE_ASSISTANT_MODEL:
            response = get_assistant_response(prompt, "emoji_detection").strip()
            logger.info(f"辅助模型情绪识别结果: {response}")
        else:
            response = get_deepseek_response(prompt, "system", store_context=False).strip()
            logger.info(f"主模型情绪识别结果: {response}")
        
        # 清洗响应内容
        response = re.sub(r"[^\w\u4e00-\u9fff]", "", response)  # 移除非文字字符

        # 验证是否为有效分类
        if response in emoji_categories:
            return response
            
        # 尝试模糊匹配
        for category in emoji_categories:
            if category in response or response in category:
                return category
                
        logger.warning(f"未匹配到有效情绪分类，AI返回: {response}")
        return None

    except Exception as e:
        logger.error(f"情绪判断失败: {str(e)}")
        return None


def send_emoji(emotion: str) -> Optional[str]:
    """根据情绪类型发送对应表情包"""
    if not emotion:
        return None
        
    emoji_folder = os.path.join(EMOJI_DIR, emotion)
    
    try:
        # 获取文件夹中的所有表情文件
        emoji_files = [
            f for f in os.listdir(emoji_folder)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif'))
        ]
        
        if not emoji_files:
            logger.warning(f"表情文件夹 {emotion} 为空")
            return None

        # 随机选择并返回表情路径
        selected_emoji = random.choice(emoji_files)
        return os.path.join(emoji_folder, selected_emoji)

    except FileNotFoundError:
        logger.error(f"表情文件夹不存在: {emoji_folder}")
    except Exception as e:
        logger.error(f"表情发送失败: {str(e)}")
    
    return None

def clean_up_temp_files ():
    if os.path.isdir("wxautox文件下载"):
        try:
            shutil.rmtree("wxautox文件下载")
        except Exception as e:
            logger.error(f"删除目录 wxautox文件下载 失败: {str(e)}")
            return
        logger.info(f"目录 wxautox文件下载 已成功删除")
    else:
        logger.info(f"目录 wxautox文件下载 不存在，无需删除")

def is_quiet_time():
    current_time = datetime.now().time()
    if quiet_time_start <= quiet_time_end:
        return quiet_time_start <= current_time <= quiet_time_end
    else:
        return current_time >= quiet_time_start or current_time <= quiet_time_end

# 记忆管理功能
def sanitize_user_id_for_filename(user_id):
    """
    将user_id转换为安全的文件名，支持中文字符。
    """
    import re
    import string
    
    # 如果输入为空或None，返回默认值
    if not user_id:
        return "default_user"
    
    # 严格拒绝路径遍历序列
    if '..' in user_id or '/' in user_id or '\\' in user_id:
        logger.warning(f"检测到可疑的user_id，包含路径遍历字符: {user_id}")
        return "suspicious_user"
    
    # 移除或替换危险字符，但保留中文字符
    # 危险字符：路径分隔符、控制字符、特殊符号等
    dangerous_chars = r'[<>:"/\\|?*\x00-\x1f\x7f]'
    safe_name = re.sub(dangerous_chars, '_', user_id)
    
    # 移除开头和结尾的空格和点
    safe_name = safe_name.strip(' .')
    
    # 确保不是Windows保留名称
    windows_reserved = {
        'CON', 'PRN', 'AUX', 'NUL',
        'COM1', 'COM2', 'COM3', 'COM4', 'COM5', 'COM6', 'COM7', 'COM8', 'COM9',
        'LPT1', 'LPT2', 'LPT3', 'LPT4', 'LPT5', 'LPT6', 'LPT7', 'LPT8', 'LPT9'
    }
    if safe_name.upper() in windows_reserved:
        safe_name = f"user_{safe_name}"
    
    # 额外验证 - 确保结果是纯文件名，不包含路径分隔符
    if os.path.sep in safe_name or (os.altsep and os.altsep in safe_name):
        logger.error(f"清理后的文件名仍包含路径分隔符: {safe_name}")
        return "error_user"
    
    # 如果结果为空，使用默认值
    if not safe_name:
        safe_name = "default_user"
    
    # 限制长度，避免文件名过长
    if len(safe_name) > 100:  # 保守的长度限制
        # 尝试保留中文字符的完整性
        safe_name = safe_name[:100]
        # 确保不在中文字符中间截断
        if len(safe_name.encode('utf-8')) > len(safe_name):
            # 有中文字符，更保守地截断
            safe_name = safe_name[:50]
    
    return safe_name

def get_core_memory_file_path(user_id):
    """获取核心记忆JSON文件的路径"""
    safe_user_id = sanitize_user_id_for_filename(user_id)
    prompt_name = prompt_mapping.get(user_id, user_id)
    safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
    core_memory_dir = os.path.join(root_dir, CORE_MEMORY_DIR)
    os.makedirs(core_memory_dir, exist_ok=True)
    return os.path.join(core_memory_dir, f'{safe_user_id}_{safe_prompt_name}_core_memory.json')

def load_core_memory_from_json(user_id):
    """从JSON文件加载核心记忆"""
    memory_file = get_core_memory_file_path(user_id)
    memories = []
    try:
        if os.path.exists(memory_file):
            with open(memory_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    memories = data
                    logger.debug(f"从JSON文件加载了 {len(memories)} 条核心记忆，用户: {user_id}")
                else:
                    logger.warning(f"核心记忆文件格式不正确，用户: {user_id}")
        else:
            logger.debug(f"核心记忆文件不存在，用户: {user_id}")
    except Exception as e:
        logger.error(f"加载核心记忆JSON文件失败，用户: {user_id}: {e}")
    return memories

def save_core_memory_to_json(user_id, memories):
    """将核心记忆保存到JSON文件"""
    memory_file = get_core_memory_file_path(user_id)
    temp_file = memory_file + '.tmp'
    try:
        with open(temp_file, 'w', encoding='utf-8') as f:
            json.dump(memories, f, ensure_ascii=False, indent=2)
        shutil.move(temp_file, memory_file)
        logger.info(f"成功保存 {len(memories)} 条核心记忆到JSON文件，用户: {user_id}")
    except Exception as e:
        logger.error(f"保存核心记忆JSON文件失败，用户: {user_id}: {e}")
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass

def add_memory_to_json(user_id, timestamp, summary, importance):
    """向JSON文件添加一条新记忆"""
    memories = load_core_memory_from_json(user_id)
    new_memory = {
        "timestamp": timestamp,
        "summary": summary,
        "importance": importance
    }
    memories.append(new_memory)
    
    # 如果超出最大数量，进行淘汰
    if len(memories) > MAX_MEMORY_NUMBER:
        memories = cleanup_json_memories(memories)
    
    save_core_memory_to_json(user_id, memories)

def cleanup_json_memories(memories):
    """对JSON格式的记忆进行淘汰处理"""
    if len(memories) <= MAX_MEMORY_NUMBER:
        return memories
    
    now = datetime.now()
    memory_scores = []
    
    for memory in memories:
        try:
            timestamp = memory.get('timestamp', '')
            importance = memory.get('importance', 3)
            
            # 尝试解析时间戳
            parsed_time = None
            formats = [
                "%Y-%m-%d %A %H:%M:%S",
                "%Y-%m-%d %A %H:%M",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M"
            ]
            
            for fmt in formats:
                try:
                    parsed_time = datetime.strptime(timestamp, fmt)
                    break
                except ValueError:
                    continue
            
            if parsed_time:
                time_diff = (now - parsed_time).total_seconds()
            else:
                logger.warning(f"无法解析JSON记忆时间戳: {timestamp}")
                time_diff = 0
            
            # 计算评分：0.6 * 重要度 - 0.4 * (时间差小时数)
            score = 0.6 * importance - 0.4 * (time_diff / 3600)
            memory_scores.append(score)
            
        except Exception as e:
            logger.warning(f"处理JSON记忆项时出错: {e}")
            memory_scores.append(0)  # 默认分数
    
    # 获取保留索引（按分数降序，时间升序）
    sorted_indices = sorted(range(len(memory_scores)), 
                          key=lambda k: (-memory_scores[k], memories[k].get('timestamp', '')))
    keep_indices = set(sorted_indices[:MAX_MEMORY_NUMBER])
    
    # 保留高分记忆
    cleaned_memories = [memories[i] for i in sorted(keep_indices)]
    logger.info(f"JSON记忆淘汰：从 {len(memories)} 条清理为 {len(cleaned_memories)} 条")
    
    return cleaned_memories

def format_json_memories_for_prompt(memories):
    """将JSON格式的记忆转换为prompt格式"""
    if not memories:
        return ""
    
    formatted_lines = []
    for memory in memories:
        timestamp = memory.get('timestamp', '')
        summary = memory.get('summary', '')
        importance = memory.get('importance', 3)
        
        formatted_lines.append(f"""## 记忆片段 [{timestamp}]
**重要度**: {importance}
**摘要**: {summary}

""")
    
    return ''.join(formatted_lines)

def append_to_memory_section(user_id, content):
    """将内容追加到用户prompt文件的记忆部分"""
    try:
        prompts_dir = os.path.join(root_dir, 'prompts')
        # 注意：这里应该使用prompt_name而不是user_id作为文件名
        prompt_name = prompt_mapping.get(user_id, user_id)
        safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
        user_file = os.path.join(prompts_dir, f'{safe_prompt_name}.md')
        
        # 确保用户文件存在
        if not os.path.exists(user_file):
            raise FileNotFoundError(f"用户文件 {safe_prompt_name}.md 不存在")

        # 读取并处理文件内容
        with open(user_file, 'r+', encoding='utf-8') as file:
            lines = file.readlines()
            
            # 查找记忆插入点
            memory_marker = "开始更新："
            insert_index = next((i for i, line in enumerate(lines) if memory_marker in line), -1)

            # 如果没有找到标记，追加到文件末尾
            if (insert_index == -1):
                insert_index = len(lines)
                lines.append(f"\n{memory_marker}\n")
                logger.info(f"在用户文件 {user_id}.md 中添加记忆标记")

            # 插入记忆内容
            current_date = datetime.now().strftime("%Y-%m-%d")
            new_content = f"\n### {current_date}\n{content}\n"

            # 写入更新内容
            lines.insert(insert_index + 1, new_content)
            file.seek(0)
            file.writelines(lines)
            file.truncate()

    except PermissionError as pe:
        logger.error(f"文件权限拒绝: {pe} (尝试访问 {user_file})")
    except IOError as ioe:
        logger.error(f"文件读写错误: {ioe} (路径: {os.path.abspath(user_file)})")
    except Exception as e:
        logger.error(f"记忆存储失败: {str(e)}", exc_info=True)
        raise  # 重新抛出异常供上层处理
    except FileNotFoundError as e:
        logger.error(f"文件未找到: {str(e)}")
        raise

def summarize_and_save(user_id, skip_check=False):
    """总结聊天记录并存储记忆
    
    Args:
        user_id: 用户ID
        skip_check: 是否跳过记忆条目数量检查，用于手动触发的总结命令
    """
    log_file = None
    temp_file = None
    backup_file = None
    try:
        # --- 前置检查 ---
        prompt_name = prompt_mapping.get(user_id, user_id)  # 获取配置的prompt名
        safe_user_id = sanitize_user_id_for_filename(user_id)
        safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
        log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{safe_user_id}_{safe_prompt_name}_log.txt')
        if not os.path.exists(log_file):
            logger.warning(f"日志文件不存在: {log_file}")
            return
        if os.path.getsize(log_file) == 0:
            logger.info(f"空日志文件: {log_file}")
            return

        # --- 读取日志 (增强编码处理) ---
        logs = []
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                logs = [line.strip() for line in f if line.strip()]
        except UnicodeDecodeError as e:
            logger.warning(f"UTF-8解码失败，尝试其他编码格式: {log_file}, 错误: {e}")
            # 尝试常见的编码格式
            for encoding in ['gbk', 'gb2312', 'latin-1', 'cp1252']:
                try:
                    with open(log_file, 'r', encoding=encoding) as f:
                        logs = [line.strip() for line in f if line.strip()]
                    logger.info(f"成功使用 {encoding} 编码读取文件: {log_file}")
                    # 重新以UTF-8编码保存文件
                    with open(log_file, 'w', encoding='utf-8') as f:
                        for log in logs:
                            f.write(log + '\n')
                    logger.info(f"已将文件重新转换为UTF-8编码: {log_file}")
                    break
                except (UnicodeDecodeError, Exception):
                    continue
            else:
                # 所有编码都失败，创建备份并重置文件
                backup_log_file = f"{log_file}.corrupted_{int(time.time())}"
                try:
                    shutil.copy(log_file, backup_log_file)
                    logger.error(f"无法解码日志文件，已备份到: {backup_log_file}")
                except Exception as backup_err:
                    logger.error(f"备份损坏文件失败: {backup_err}")
                
                # 重置为空文件
                with open(log_file, 'w', encoding='utf-8') as f:
                    f.write("")
                logger.warning(f"已重置损坏的日志文件: {log_file}")
                return
        except Exception as e:
            logger.error(f"读取日志文件时发生未知错误: {log_file}, 错误: {e}")
            return
            
        # 修改检查条件：仅检查是否达到最小处理阈值（除非跳过检查）
        if not skip_check and len(logs) < MAX_MESSAGE_LOG_ENTRIES:
            logger.info(f"日志条目不足（{len(logs)}条），未触发记忆总结。")
            return

        # --- 生成总结 ---
        # 修改为使用全部日志内容
        full_logs = '\n'.join(logs)  # 变量名改为更明确的full_logs
        summary_prompt = f"请以{prompt_name}的视角，用中文总结与{user_id}的对话，提取重要信息总结为一段话作为记忆片段（直接回复一段话）：\n{full_logs}"
        
        # 根据配置选择使用辅助模型或主模型进行记忆总结
        if USE_ASSISTANT_FOR_MEMORY_SUMMARY and ENABLE_ASSISTANT_MODEL:
            logger.info(f"使用辅助模型为用户 {user_id} 生成记忆总结")
            summary = get_assistant_response(summary_prompt, "memory_summary", is_summary=True)
        else:
            logger.info(f"使用主模型为用户 {user_id} 生成记忆总结")
            summary = get_deepseek_response(summary_prompt, "system", store_context=False, is_summary=True)

        # 添加清洗，匹配可能存在的**重要度**或**摘要**字段以及##记忆片段 [%Y-%m-%d %A %H:%M]或[%Y-%m-%d %H:%M]或[%Y-%m-%d %H:%M:%S]或[%Y-%m-%d %A %H:%M:%S]格式的时间戳
        summary = re.sub(
            r'\*{0,2}(重要度|摘要)\*{0,2}[\s:]*\d*[\.]?\d*[\s\\]*|## 记忆片段 \[\d{4}-\d{2}-\d{2}( [A-Za-z]+)? \d{2}:\d{2}(:\d{2})?\]',
            '',
            summary,
            flags=re.MULTILINE
        ).strip()

        # --- 评估重要性 ---
        importance_prompt = f"为以下记忆的重要性评分（1-5，直接回复数字）：\n{summary}"
        
        # 根据配置选择使用辅助模型或主模型进行重要性评估
        if USE_ASSISTANT_FOR_MEMORY_SUMMARY and ENABLE_ASSISTANT_MODEL:
            logger.info(f"使用辅助模型为用户 {user_id} 进行重要性评估")
            importance_response = get_assistant_response(importance_prompt, "memory_importance", is_summary=True)
        else:
            logger.info(f"使用主模型为用户 {user_id} 进行重要性评估")
            importance_response = get_deepseek_response(importance_prompt, "system", store_context=False, is_summary=True)
        
        # 强化重要性提取逻辑
        importance_match = re.search(r'[1-5]', importance_response)
        if importance_match:
            importance = min(max(int(importance_match.group()), 1), 5)  # 确保1-5范围
        else:
            importance = 3  # 默认值
            logger.warning(f"无法解析重要性评分，使用默认值3。原始响应：{importance_response}")

        # --- 存储记忆 ---
        current_time = datetime.now().strftime("%Y-%m-%d %A %H:%M")
        
        # 根据配置选择存储方式
        if get_dynamic_config('SAVE_MEMORY_TO_SEPARATE_FILE', SAVE_MEMORY_TO_SEPARATE_FILE):
            # 保存到JSON文件
            logger.info(f"将记忆保存到JSON文件，用户: {user_id}")
            add_memory_to_json(user_id, current_time, summary, importance)
        else:
            # 保存到prompt文件
            logger.info(f"将记忆保存到prompt文件，用户: {user_id}")
            # 修正1：增加末尾换行
            memory_entry = f"""## 记忆片段 [{current_time}]
**重要度**: {importance}
**摘要**: {summary}

"""  # 注意这里有两个换行

            prompt_name = prompt_mapping.get(user_id, user_id)
            prompts_dir = os.path.join(root_dir, 'prompts')
            os.makedirs(prompts_dir, exist_ok=True)

            user_prompt_file = os.path.join(prompts_dir, f'{prompt_name}.md')
            temp_file = f"{user_prompt_file}.tmp"
            backup_file = f"{user_prompt_file}.bak"

            try:
                with open(temp_file, 'w', encoding='utf-8') as f:
                    if os.path.exists(user_prompt_file):
                        with open(user_prompt_file, 'r', encoding='utf-8') as src:
                            f.write(src.read().rstrip() + '\n\n')  # 修正2：规范化原有内容结尾
                
                    # 写入预格式化的内容
                    f.write(memory_entry)  # 不再重复生成字段

                # 步骤2：备份原文件
                if os.path.exists(user_prompt_file):
                    shutil.copyfile(user_prompt_file, backup_file)

                # 步骤3：替换文件
                shutil.move(temp_file, user_prompt_file)

            except Exception as e:
                # 异常恢复流程
                if os.path.exists(backup_file):
                    shutil.move(backup_file, user_prompt_file)
                raise

        # --- 清理日志 ---
        with open(log_file, 'w', encoding='utf-8') as f:
            f.truncate()

    except Exception as e:
        logger.error(f"记忆保存失败: {str(e)}", exc_info=True)
    finally:
        # 清理临时文件
        for f in [temp_file, backup_file]:
            if f and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception as e:
                    logger.error(f"清理临时文件失败: {str(e)}")

def memory_manager():
    """记忆管理定时任务"""
    while True:
        try:
            # 检查所有监听用户
            for user in user_names:
                prompt_name = prompt_mapping.get(user, user)  # 获取配置的prompt名
                log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{user}_{prompt_name}_log.txt')
                
                try:
                    # 根据配置调用对应的记忆容量管理函数
                    manage_user_memory_capacity(user)
                except UnicodeDecodeError as ude:
                    logger.error(f"用户 {user} 的记忆文件编码异常: {str(ude)}")
                    logger.info(f"跳过用户 {user} 的内存管理，等待下一轮检查")
                    continue
                except Exception as e:
                    logger.error(f"用户 {user} 内存管理失败: {str(e)}")
                    continue

                if os.path.exists(log_file):
                    try:
                        # 增强编码处理的行数统计
                        line_count = 0
                        try:
                            with open(log_file, 'r', encoding='utf-8') as f:
                                line_count = sum(1 for _ in f)
                        except UnicodeDecodeError as ude:
                            logger.warning(f"UTF-8解码失败，尝试其他编码统计行数: {log_file}, 错误: {ude}")
                            # 尝试用其他编码统计行数
                            for encoding in ['gbk', 'gb2312', 'latin-1', 'cp1252']:
                                try:
                                    with open(log_file, 'r', encoding=encoding) as f:
                                        line_count = sum(1 for _ in f)
                                    logger.info(f"成功使用 {encoding} 编码统计行数: {log_file}")
                                    break
                                except (UnicodeDecodeError, Exception):
                                    continue
                            else:
                                # 所有编码都失败，跳过这个用户
                                logger.error(f"无法统计日志文件行数，跳过用户 {user}: {log_file}")
                                continue
                                
                        if line_count >= MAX_MESSAGE_LOG_ENTRIES:
                            summarize_and_save(user)
                    except Exception as file_err:
                        logger.error(f"处理用户 {user} 的日志文件时出错: {file_err}")
                        continue
    
        except Exception as e:
            logger.error(f"记忆管理异常: {str(e)}")
        finally:
            time.sleep(60)  # 每分钟检查一次

def manage_memory_capacity(user_file):
    """记忆淘汰机制 - 处理prompt文件中的记忆清理"""
    # 允许重要度缺失（使用可选捕获组）
    MEMORY_SEGMENT_PATTERN = r'## 记忆片段 \[(.*?)\]\n(?:\*{2}重要度\*{2}: (\d*)\n)?\*{2}摘要\*{2}:(.*?)(?=\n## 记忆片段 |\Z)'
    try:
        # 增强编码处理的文件读取
        content = None
        try:
            with open(user_file, 'r', encoding='utf-8') as f:
                content = f.read()
        except UnicodeDecodeError as e:
            logger.warning(f"UTF-8解码失败，尝试其他编码格式: {user_file}, 错误: {e}")
            # 尝试常见的编码格式
            for encoding in ['gbk', 'gb2312', 'latin-1', 'cp1252']:
                try:
                    with open(user_file, 'r', encoding=encoding) as f:
                        content = f.read()
                    logger.info(f"成功使用 {encoding} 编码读取用户文件: {user_file}")
                    # 重新以UTF-8编码保存文件
                    backup_file = f"{user_file}.bak_{int(time.time())}"
                    try:
                        shutil.copy(user_file, backup_file)
                        with open(user_file, 'w', encoding='utf-8') as f:
                            f.write(content)
                        logger.info(f"已将用户文件重新转换为UTF-8编码: {user_file} (备份: {backup_file})")
                    except Exception as save_err:
                        logger.error(f"重新保存用户文件失败: {save_err}")
                    break
                except (UnicodeDecodeError, Exception):
                    continue
            else:
                # 所有编码都失败
                backup_file = f"{user_file}.corrupted_{int(time.time())}"
                try:
                    shutil.copy(user_file, backup_file)
                    logger.error(f"无法解码用户文件，已备份到: {backup_file}")
                except Exception as backup_err:
                    logger.error(f"备份损坏用户文件失败: {backup_err}")
                logger.error(f"用户文件编码损坏，跳过记忆管理: {user_file}")
                return
        
        if content is None:
            logger.error(f"无法读取用户文件内容: {user_file}")
            return
        
        # 解析记忆片段
        segments = re.findall(MEMORY_SEGMENT_PATTERN, content, re.DOTALL)
        if len(segments) <= MAX_MEMORY_NUMBER:
            return

        # 构建评分体系
        now = datetime.now()
        memory_scores = []
        for timestamp, importance, _ in segments:
            try:
                # 尝试多种时间格式，支持新旧格式
                formats = [
                    "%Y-%m-%d %A %H:%M:%S",  # 新格式，带星期和秒
                    "%Y-%m-%d %A %H:%M",     # 新格式，带星期但没有秒
                    "%Y-%m-%d %H:%M:%S",     # 带秒但没有星期
                    "%Y-%m-%d %H:%M"         # 原始格式
                ]
                
                parsed_time = None
                for fmt in formats:
                    try:
                        parsed_time = datetime.strptime(timestamp, fmt)
                        break
                    except ValueError:
                        continue
                
                if parsed_time:
                    time_diff = (now - parsed_time).total_seconds()
                else:
                    # 如果所有格式都解析失败
                    logger.warning(f"无法解析时间戳: {timestamp}")
                    time_diff = 0
            except Exception as e:
                logger.warning(f"时间戳解析错误: {str(e)}")
                time_diff = 0
                
            # 处理重要度缺失，默认值为3
            importance_value = int(importance) if importance else 3
            score = 0.6 * importance_value - 0.4 * (time_diff / 3600)
            memory_scores.append(score)

        # 获取保留索引
        sorted_indices = sorted(range(len(memory_scores)),
                              key=lambda k: (-memory_scores[k], segments[k][0]))
        keep_indices = set(sorted_indices[:MAX_MEMORY_NUMBER])

        # 重建内容
        memory_blocks = re.split(r'(?=## 记忆片段 \[)', content)
        new_content = []
        
        # 解析时处理缺失值
        for idx, block in enumerate(memory_blocks):
            if idx == 0:
                new_content.append(block)
                continue
            try:
                # 显式关联 memory_blocks 与 segments 的索引
                segment_idx = idx - 1
                if segment_idx < len(segments) and segment_idx in keep_indices:
                    new_content.append(block)
            except Exception as e:
                logger.warning(f"跳过无效记忆块: {str(e)}")
                continue

        # 原子写入
        with open(f"{user_file}.tmp", 'w', encoding='utf-8') as f:
            f.write(''.join(new_content).strip())
        
        shutil.move(f"{user_file}.tmp", user_file)
        logger.info(f"成功清理prompt文件中的记忆")

    except Exception as e:
        logger.error(f"记忆整理失败: {str(e)}")

def manage_core_memory_capacity(user_id):
    """管理JSON文件中的核心记忆容量"""
    try:
        memories = load_core_memory_from_json(user_id)
        if len(memories) > MAX_MEMORY_NUMBER:
            logger.info(f"用户 {user_id} 的JSON记忆超过容量限制，开始清理")
            cleaned_memories = cleanup_json_memories(memories)
            save_core_memory_to_json(user_id, cleaned_memories)
            logger.info(f"用户 {user_id} 的JSON记忆清理完成")
    except Exception as e:
        logger.error(f"管理用户 {user_id} 的JSON记忆容量失败: {e}")

def manage_user_memory_capacity(user):
    """根据配置管理用户的记忆容量（prompt文件或JSON文件）"""
    try:
        if get_dynamic_config('SAVE_MEMORY_TO_SEPARATE_FILE', SAVE_MEMORY_TO_SEPARATE_FILE):
            # 清理JSON文件中的记忆
            manage_core_memory_capacity(user)
        else:
            # 清理prompt文件中的记忆
            prompt_name = prompt_mapping.get(user, user)
            user_prompt_file = os.path.join(root_dir, 'prompts', f'{prompt_name}.md')
            manage_memory_capacity(user_prompt_file)
    except Exception as e:
        logger.error(f"管理用户 {user} 记忆容量失败: {e}")

def clear_memory_temp_files(user_id):
    """清除指定用户的Memory_Temp文件"""
    try:
        logger.warning(f"已开启自动清除Memory_Temp文件功能，尝试清除用户 {user_id} 的Memory_Temp文件")
        prompt_name = prompt_mapping.get(user_id, user_id)
        safe_user_id = sanitize_user_id_for_filename(user_id)
        safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
        log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{safe_user_id}_{safe_prompt_name}_log.txt')
        if os.path.exists(log_file):
            os.remove(log_file)
            logger.warning(f"已清除用户 {user_id} 的Memory_Temp文件: {log_file}")
    except Exception as e:
        logger.error(f"清除Memory_Temp文件失败: {str(e)}")

def clear_chat_context(user_id):
    """清除指定用户的聊天上下文"""
    logger.info(f"已开启自动清除上下文功能，尝试清除用户 {user_id} 的聊天上下文")
    try:
        with queue_lock:
            if user_id in chat_contexts:
                del chat_contexts[user_id]
                save_chat_contexts()
                logger.warning(f"已清除用户 {user_id} 的聊天上下文")
    except Exception as e:
        logger.error(f"清除聊天上下文失败: {str(e)}")

def send_error_reply(user_id, error_description_for_ai, fallback_message, error_context_log=""):
    """
    生成并发送符合人设的错误回复。
    Args:
        user_id (str): 目标用户ID。
        error_description_for_ai (str): 给AI的提示，描述错误情况，要求其生成用户回复。
        fallback_message (str): 如果AI生成失败，使用的备用消息。
        error_context_log (str): 用于日志记录的错误上下文描述。
    """
    logger.warning(f"准备为用户 {user_id} 发送错误提示: {error_context_log}")
    try:
        # 调用AI生成符合人设的错误消息
        ai_error_reply = get_deepseek_response(error_description_for_ai, user_id=user_id, store_context=True)
        logger.info(f"AI生成的错误回复: {ai_error_reply[:100]}...")
        # 使用send_reply发送AI生成的回复
        send_reply(user_id, user_id, user_id, f"[错误处理: {error_context_log}]", ai_error_reply)
    except Exception as ai_err:
        logger.error(f"调用AI生成错误回复失败 ({error_context_log}): {ai_err}. 使用备用消息。")
        try:
            # AI失败，使用备用消息通过send_reply发送
            send_reply(user_id, user_id, user_id, f"[错误处理备用: {error_context_log}]", fallback_message, is_system_message=True)
        except Exception as send_fallback_err:
            # 如果连send_reply都失败了，记录严重错误
            logger.critical(f"发送备用错误消息也失败 ({error_context_log}): {send_fallback_err}")

def try_parse_and_set_reminder(message_content, user_id):
    """
    尝试解析消息内容，区分短期一次性、长期一次性、重复提醒。
    使用 AI 进行分类和信息提取，然后设置短期定时器或保存到文件。
    如果成功设置了任一类型的提醒，返回 True，否则返回 False。
    """
    global next_timer_id # 引用全局变量，用于生成短期一次性提醒的ID
    logger.debug(f"尝试为用户 {user_id} 解析提醒请求 (需要识别类型和时长): '{message_content}'")
    
    # 长度限制
    if len(message_content) > 1000:
        logger.warning(f"提醒请求消息过长: {len(message_content)} 字符，用户: {user_id}")
        error_msg = "提醒内容太长啦，请简短一些再试试~"
        send_reply(user_id, user_id, user_id, "[提醒限制]", error_msg, is_system_message=True)
        return False
    
    # 检查用户活动短期定时器数量
    with timer_lock:
        user_timer_count = sum(1 for (uid, _) in active_timers.keys() if uid == user_id)
        if user_timer_count >= MAX_ACTIVE_TIMERS_PER_USER:
            logger.warning(f"用户 {user_id} 的活动短期提醒已达上限: {user_timer_count}/{MAX_ACTIVE_TIMERS_PER_USER}")
            error_msg = f"你的活动提醒数量已经达到上限啦（{MAX_ACTIVE_TIMERS_PER_USER}个），等之前的提醒完成后再设置新的吧~"
            send_reply(user_id, user_id, user_id, "[提醒限制]", error_msg, is_system_message=True)
            return False
    
    # 检查用户的重复提醒和一次性提醒数量
    with recurring_reminder_lock:
        user_recurring_count = sum(1 for r in recurring_reminders 
                                  if r.get('user_id') == user_id 
                                  and r.get('reminder_type') == 'recurring')
        user_oneoff_count = sum(1 for r in recurring_reminders 
                               if r.get('user_id') == user_id 
                               and r.get('reminder_type') == 'one-off')
        
        # 如果已经达到任一限制，先提示用户（具体类型限制在后面再次检查）
        if user_recurring_count >= MAX_RECURRING_REMINDERS_PER_USER and user_oneoff_count >= MAX_ONEOFF_REMINDERS_PER_USER:
            logger.warning(f"用户 {user_id} 的所有类型提醒都已达上限")
            error_msg = f"你的提醒数量已经很多啦，可以先删除一些再添加新的哦~"
            send_reply(user_id, user_id, user_id, "[提醒限制]", error_msg, is_system_message=True)
            return False

    try:
        # --- 1. 获取当前时间，准备给 AI 的上下文信息 ---
        now = dt.datetime.now()
        # AI 需要知道当前完整日期时间来计算目标时间
        current_datetime_str_for_ai = now.strftime("%Y-%m-%d %A %H:%M:%S")
        logger.debug(f"当前时间: {current_datetime_str_for_ai} (用于AI分析)")

        # --- 2. 构建新的 AI 提示，要求 AI 分类并提取信息 ---
        # --- 更新: 增加短期/长期一次性提醒的区分 ---
        parsing_prompt = f"""
请分析用户的提醒或定时请求。
当前时间是: {current_datetime_str_for_ai}.
用户的请求是: "{message_content}"

请判断这个请求属于以下哪种类型，并计算相关时间：
A) **重复性每日提醒**：例如 "每天早上8点叫我起床", "提醒我每天晚上10点睡觉"。
B) **一次性提醒 (延迟 > 10分钟 / 600秒)**：例如 "1小时后提醒我", "今天下午3点开会", "明天早上叫我"。
C) **一次性提醒 (延迟 <= 10分钟 / 600秒)**：例如 "5分钟后提醒我", "提醒我600秒后喝水"。
D) **非提醒请求**：例如 "今天天气怎么样?", "取消提醒"。

根据判断结果，请严格按照以下格式输出：
- 如果是 A (重复每日提醒): 返回 JSON 对象 `{{"type": "recurring", "time_str": "HH:MM", "message": "提醒的具体内容"}}`。 `time_str` 必须是 24 小时制的 HH:MM 格式。
- 如果是 B (长期一次性提醒): 返回 JSON 对象 `{{"type": "one-off-long", "target_datetime_str": "YYYY-MM-DD HH:MM", "message": "提醒的具体内容"}}`。 `target_datetime_str` 必须是计算出的未来目标时间的 YYYY-MM-DD HH:MM 格式。
- 如果是 C (短期一次性提醒): 返回 JSON 对象 `{{"type": "one-off-short", "delay_seconds": number, "message": "提醒的具体内容"}}`。 `delay_seconds` 必须是从现在开始计算的、小于等于 600 的正整数总秒数。
- 如果是 D (非提醒): 请直接返回字面单词 `null`。

请看以下例子 (假设当前时间是 2024-05-29 星期三 10:00:00):
1. "每天早上8点叫我起床" -> `{{"type": "recurring", "time_str": "08:00", "message": "叫我起床"}}`
2. "提醒我30分钟后喝水" -> `{{"type": "one-off-long", "target_datetime_str": "2024-05-29 10:30", "message": "喝水"}}` (超过10分钟)
3. "下午2点提醒我开会" -> `{{"type": "one-off-long", "target_datetime_str": "2024-05-29 14:00", "message": "开会"}}`
4. "明天早上7点叫我起床" -> `{{"type": "one-off-long", "target_datetime_str": "2024-05-30 07:00", "message": "叫我起床"}}`
5. "提醒我5分钟后站起来活动" -> `{{"type": "one-off-short", "delay_seconds": 300, "message": "站起来活动"}}` (小于等于10分钟)
6. "10分钟后叫我" -> `{{"type": "one-off-short", "delay_seconds": 600, "message": "叫我"}}` (等于10分钟)
7. "今天怎么样?" -> `null`

请务必严格遵守输出格式，只返回指定的 JSON 对象或 `null`，不要添加任何解释性文字。
"""
        # --- 3. 调用 AI 进行解析和分类 ---
        # 根据配置选择使用辅助模型或主模型
        if ENABLE_ASSISTANT_MODEL:
            logger.info(f"向辅助模型发送提醒解析请求（区分时长），用户: {user_id}，内容: '{message_content}'")
            ai_raw_response = get_assistant_response(parsing_prompt, "reminder_parser_classifier_v2_" + user_id)
            logger.debug(f"辅助模型提醒解析原始响应 (分类器 v2): {ai_raw_response}")
        else:
            logger.info(f"向主模型发送提醒解析请求（区分时长），用户: {user_id}，内容: '{message_content}'")
            ai_raw_response = get_deepseek_response(parsing_prompt, user_id="reminder_parser_classifier_v2_" + user_id, store_context=False)
            logger.debug(f"主模型提醒解析原始响应 (分类器 v2): {ai_raw_response}")

        # 使用新的清理函数处理AI的原始响应
        cleaned_ai_output_str = extract_last_json_or_null(ai_raw_response)
        logger.debug(f"AI响应清理并提取后内容: '{cleaned_ai_output_str}'")
        response = cleaned_ai_output_str

        # --- 4. 解析 AI 的响应 ---
        # 修改判断条件，使用清理后的结果
        if cleaned_ai_output_str is None or cleaned_ai_output_str == "null": # "null" 是AI明确表示非提醒的方式
            logger.info(f"AI 未在用户 '{user_id}' 的消息中检测到有效的提醒请求 (清理后结果为 None 或 'null')。原始AI响应: '{ai_raw_response}'")
            return False
        
        try:
            response_cleaned = re.sub(r"```json\n?|\n?```", "", response).strip()
            reminder_data = json.loads(response_cleaned)
            logger.debug(f"解析后的JSON数据 (分类器 v2): {reminder_data}")

            reminder_type = reminder_data.get("type")
            reminder_msg = str(reminder_data.get("message", "")).strip()

            # --- 5. 验证共享数据（提醒内容不能为空）---
            if not reminder_msg:
                logger.warning(f"从AI解析得到的提醒消息为空。用户: {user_id}, 数据: {reminder_data}")
                error_prompt = f"用户尝试设置提醒，但似乎没有说明要提醒的具体内容（用户的原始请求可能是 '{message_content}'）。请用你的语气向用户解释需要提供提醒内容，并鼓励他们再说一次。"
                fallback = "嗯... 光设置时间还不行哦，得告诉我你要我提醒你做什么事呀？"
                send_error_reply(user_id, error_prompt, fallback, "提醒内容为空")
                return False
            
            # 限制提醒消息长度
            if len(reminder_msg) > MAX_REMINDER_MESSAGE_LENGTH:
                logger.warning(f"提醒消息过长: {len(reminder_msg)} 字符，已截断，用户: {user_id}")
                reminder_msg = reminder_msg[:MAX_REMINDER_MESSAGE_LENGTH] + "..."

            # --- 6. 根据 AI 判断的类型分别处理 ---

            # --- 6a. 短期一次性提醒 (<= 10分钟) ---
            if reminder_type == "one-off-short":
                try:
                    delay_seconds = int(reminder_data['delay_seconds'])
                    if not (0 < delay_seconds <= 600): # 验证延迟在 (0, 600] 秒之间
                         logger.warning(f"AI 返回的 'one-off-short' 延迟时间无效: {delay_seconds} 秒 (应 > 0 且 <= 600)。用户: {user_id}, 数据: {reminder_data}")
                         error_prompt = f"用户想设置一个短期提醒（原始请求 '{message_content}'），但我计算出的时间 ({delay_seconds}秒) 不在10分钟内或已过去。请用你的语气告诉用户这个时间有点问题，建议他们检查一下或换个说法。"
                         fallback = "哎呀，这个短期提醒的时间好像有点不对劲（要么超过10分钟，要么已经过去了），能麻烦你再说一次吗？"
                         send_error_reply(user_id, error_prompt, fallback, "短期延迟时间无效")
                         return False
                except (KeyError, ValueError, TypeError) as val_e:
                     logger.error(f"解析AI返回的 'one-off-short' 提醒数据失败。用户: {user_id}, 数据: {reminder_data}, 错误: {val_e}")
                     error_prompt = f"用户想设置短期提醒（原始请求 '{message_content}'），但我没理解好时间({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们换种方式说，比如'5分钟后提醒我...'"
                     fallback = "抱歉呀，我好像没太明白你的时间意思，设置短期提醒失败了。能麻烦你换种方式再说一遍吗？比如 '5分钟后提醒我...'"
                     send_error_reply(user_id, error_prompt, fallback, f"One-off-short数据解析失败 ({type(val_e).__name__})")
                     return False

                # 设置 threading.Timer 定时器
                target_dt = now + dt.timedelta(seconds=delay_seconds)
                confirmation_time_str = target_dt.strftime('%Y-%m-%d %H:%M:%S')
                delay_str_approx = format_delay_approx(delay_seconds, target_dt)

                logger.info(f"准备为用户 {user_id} 设置【短期一次性】提醒 (<=10min)，计划触发时间: {confirmation_time_str} (延迟 {delay_seconds:.2f} 秒)，内容: '{reminder_msg}'")

                with timer_lock:
                    timer_id = next_timer_id
                    next_timer_id += 1
                    timer_key = (user_id, timer_id)
                    timer = Timer(float(delay_seconds), trigger_reminder, args=[user_id, timer_id, reminder_msg])
                    active_timers[timer_key] = timer
                    timer.start()
                    logger.info(f"【短期一次性】提醒定时器 (ID: {timer_id}) 已为用户 {user_id} 成功启动。")

                log_original_message_to_memory(user_id, message_content) # 记录原始请求

                confirmation_prompt = f"""用户刚才的请求是："{message_content}"。
根据这个请求，你已经成功将一个【短期一次性】提醒（10分钟内）安排在 {confirmation_time_str} (也就是 {delay_str_approx}) 触发。
提醒的核心内容是：'{reminder_msg}'。
请你用自然、友好的语气回复用户，告诉他这个【短期】提醒已经设置好了，确认时间和提醒内容。"""
                send_confirmation_reply(user_id, confirmation_prompt, f"[短期一次性提醒已设置: {reminder_msg}]", f"收到！【短期提醒】设置好啦，我会在 {delay_str_approx} ({target_dt.strftime('%H:%M')}) 提醒你：{reminder_msg}")
                return True

            # --- 6b. 长期一次性提醒 (> 10分钟) ---
            elif reminder_type == "one-off-long":
                try:
                    target_datetime_str = reminder_data['target_datetime_str']
                    # 在本地再次验证时间格式是否为 YYYY-MM-DD HH:MM
                    target_dt = datetime.strptime(target_datetime_str, '%Y-%m-%d %H:%M')
                    # 验证时间是否在未来
                    if target_dt <= now:
                        logger.warning(f"AI 返回的 'one-off-long' 目标时间无效: {target_datetime_str} (已过去或就是现在)。用户: {user_id}, 数据: {reminder_data}")
                        error_prompt = f"用户想设置一个提醒（原始请求 '{message_content}'），但我计算出的目标时间 ({target_datetime_str}) 好像是过去或就是现在了。请用你的语气告诉用户这个时间点无法设置，建议他们指定一个未来的时间。"
                        fallback = "哎呀，这个时间点 ({target_dt.strftime('%m月%d日 %H:%M')}) 好像已经过去了或就是现在啦，没办法设置过去的提醒哦。要不试试说一个未来的时间？"
                        send_error_reply(user_id, error_prompt, fallback, "长期目标时间无效")
                        return False
                except (KeyError, ValueError, TypeError) as val_e:
                    logger.error(f"解析AI返回的 'one-off-long' 提醒数据失败。用户: {user_id}, 数据: {reminder_data}, 错误: {val_e}")
                    error_prompt = f"用户想设置一个较远时间的提醒（原始请求 '{message_content}'），但我没理解好目标时间 ({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们用明确的日期和时间再说，比如'明天下午3点'或'2024-06-15 10:00'。"
                    fallback = "抱歉呀，我好像没太明白你说的那个未来的时间点，设置提醒失败了。能麻烦你说得更清楚一点吗？比如 '明天下午3点' 或者 '6月15号上午10点' 这样。"
                    send_error_reply(user_id, error_prompt, fallback, f"One-off-long数据解析失败 ({type(val_e).__name__})")
                    return False

                logger.info(f"准备为用户 {user_id} 添加【长期一次性】提醒 (>10min)，目标时间: {target_datetime_str}，内容: '{reminder_msg}'")

                # 检查用户长期一次性提醒数量限制
                with recurring_reminder_lock:
                    user_oneoff_count = sum(1 for r in recurring_reminders 
                                           if r.get('user_id') == user_id 
                                           and r.get('reminder_type') == 'one-off')
                    if user_oneoff_count >= MAX_ONEOFF_REMINDERS_PER_USER:
                        logger.warning(f"用户 {user_id} 的长期一次性提醒已达上限: {user_oneoff_count}/{MAX_ONEOFF_REMINDERS_PER_USER}")
                        error_msg = f"你的一次性提醒数量已经达到上限啦（{MAX_ONEOFF_REMINDERS_PER_USER}个），可以先删除一些再添加新的哦~"
                        send_reply(user_id, user_id, user_id, "[提醒限制]", error_msg, is_system_message=True)
                        return False
                    
                    # 创建要存储的提醒信息字典 (包含类型)
                    new_reminder = {
                        "reminder_type": "one-off", # 在存储时统一用 'one-off'
                        "user_id": user_id,
                        "target_datetime_str": target_datetime_str, # 存储目标时间
                        "content": reminder_msg
                    }

                    # 添加到内存列表并保存到文件
                    recurring_reminders.append(new_reminder)
                    save_recurring_reminders() # 保存更新后的列表

                logger.info(f"【长期一次性】提醒已添加并保存到文件。用户: {user_id}, 时间: {target_datetime_str}, 内容: '{reminder_msg}'")

                log_original_message_to_memory(user_id, message_content)

                # 发送确认消息
                confirmation_prompt = f"""用户刚才的请求是："{message_content}"。
根据这个请求，你已经成功为他设置了一个【一次性】提醒。
这个提醒将在【指定时间】 {target_datetime_str} 触发。
提醒的核心内容是：'{reminder_msg}'。
请你用自然、友好的语气回复用户，告诉他这个【一次性】提醒已经设置好了，确认好具体的日期时间和提醒内容。"""
                # 使用格式化后的时间发送给用户
                friendly_time = target_dt.strftime('%Y年%m月%d日 %H:%M')
                send_confirmation_reply(user_id, confirmation_prompt, f"[长期一次性提醒已设置: {reminder_msg}]", f"好嘞！【一次性提醒】设置好啦，我会在 {friendly_time} 提醒你：{reminder_msg}")
                return True

            # --- 6c. 重复性每日提醒 ---
            elif reminder_type == "recurring":
                try:
                    time_str = reminder_data['time_str']
                    datetime.strptime(time_str, '%H:%M') # 验证 HH:MM 格式
                except (KeyError, ValueError, TypeError) as val_e:
                    logger.error(f"解析AI返回的 'recurring' 提醒数据失败。用户: {user_id}, 数据: {reminder_data}, 错误: {val_e}")
                    error_prompt = f"用户想设置每日提醒（原始请求 '{message_content}'），但我没理解好时间 ({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们用明确的'每天几点几分'格式再说，比如'每天早上8点'或'每天22:30'。"
                    fallback = "抱歉呀，我好像没太明白你说的每日提醒时间，设置失败了。能麻烦你说清楚是'每天几点几分'吗？比如 '每天早上8点' 或者 '每天22:30' 这样。"
                    send_error_reply(user_id, error_prompt, fallback, f"Recurring数据解析失败 ({type(val_e).__name__})")
                    return False

                logger.info(f"准备为用户 {user_id} 添加【每日重复】提醒，时间: {time_str}，内容: '{reminder_msg}'")

                # 添加到内存列表并保存到文件
                with recurring_reminder_lock:
                    # 检查用户每日重复提醒数量限制
                    user_recurring_count = sum(1 for r in recurring_reminders 
                                              if r.get('user_id') == user_id 
                                              and r.get('reminder_type') == 'recurring')
                    if user_recurring_count >= MAX_RECURRING_REMINDERS_PER_USER:
                        logger.warning(f"用户 {user_id} 的每日重复提醒已达上限: {user_recurring_count}/{MAX_RECURRING_REMINDERS_PER_USER}")
                        error_msg = f"你的每日提醒数量已经达到上限啦（{MAX_RECURRING_REMINDERS_PER_USER}个），可以先删除一些再添加新的哦~"
                        send_reply(user_id, user_id, user_id, "[提醒限制]", error_msg, is_system_message=True)
                        return False
                    
                    # 检查是否已存在完全相同的重复提醒
                    exists = any(
                        r.get('reminder_type') == 'recurring' and
                        r.get('user_id') == user_id and
                        r.get('time_str') == time_str and
                        r.get('content') == reminder_msg
                        for r in recurring_reminders
                    )
                    if not exists:
                        # 创建要存储的提醒信息字典 (包含类型)
                        new_reminder = {
                            "reminder_type": "recurring", # 明确类型
                            "user_id": user_id,
                            "time_str": time_str, # 存储 HH:MM
                            "content": reminder_msg
                        }
                        recurring_reminders.append(new_reminder)
                        save_recurring_reminders()
                        logger.info(f"【每日重复】提醒已添加并保存。用户: {user_id}, 时间: {time_str}, 内容: '{reminder_msg}'")
                    else:
                        logger.info(f"相同的【每日重复】提醒已存在，未重复添加。用户: {user_id}, 时间: {time_str}")
                        # 可以选择告知用户提醒已存在
                        # send_reply(user_id, user_id, user_id, "[重复提醒已存在]", f"嗯嗯，这个 '{reminder_msg}' 的每日 {time_str} 提醒我已经记下啦，不用重复设置哦。")
                        # return True # 即使未添加，也认为设置意图已满足

                log_original_message_to_memory(user_id, message_content)

                # 向用户发送确认消息
                confirmation_prompt = f"""用户刚才的请求是："{message_content}"。
根据这个请求，你已经成功为他设置了一个【每日重复】提醒。
这个提醒将在【每天】的 {time_str} 触发。
提醒的核心内容是：'{reminder_msg}'。
请你用自然、友好的语气回复用户，告诉他【每日】提醒已经设置好了，确认时间和提醒内容。强调这是每天都会提醒的。"""
                send_confirmation_reply(user_id, confirmation_prompt, f"[每日提醒已设置: {reminder_msg}]", f"好嘞！【每日提醒】设置好啦，以后我【每天】 {time_str} 都会提醒你：{reminder_msg}")
                return True

            # --- 6d. 未知类型 ---
            else:
                 logger.error(f"AI 返回了未知的提醒类型: '{reminder_type}'。用户: {user_id}, 数据: {reminder_data}")
                 error_prompt = f"用户想设置提醒（原始请求 '{message_content}'），但我有点糊涂了，没搞清楚时间或者类型。请用你的语气抱歉地告诉用户，请他们说得更清楚一点，比如是几分钟后、明天几点、还是每天提醒。"
                 fallback = "哎呀，我有点没搞懂你的提醒要求，是几分钟后提醒，还是指定某个时间点，或者是每天都提醒呀？麻烦说清楚点我才能帮你设置哦。"
                 send_error_reply(user_id, error_prompt, fallback, f"未知提醒类型 '{reminder_type}'")
                 return False

        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as json_e:
            # 处理 JSON 解析本身或后续访问键值对的错误
            response_cleaned_str = response_cleaned if 'response_cleaned' in locals() else 'N/A'
            logger.error(f"解析AI返回的提醒JSON失败 (分类器 v2)。用户: {user_id}, 原始响应: '{response}', 清理后: '{response_cleaned_str}', 错误: {json_e}")
            error_prompt = f"用户想设置提醒（原始请求可能是 '{message_content}'），但我好像没完全理解时间或者内容，解析的时候出错了 ({type(json_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们换种方式说，比如'30分钟后提醒我...'或'每天下午3点叫我...'。"
            fallback = "抱歉呀，我好像没太明白你的意思，设置提醒失败了。能麻烦你换种方式再说一遍吗？比如 '30分钟后提醒我...' 或者 '每天下午3点叫我...' 这种。"
            send_error_reply(user_id, error_prompt, fallback, f"JSON解析失败 ({type(json_e).__name__})")
            return False

    except Exception as e:
        # 捕获此函数中其他所有未预料的错误
        logger.error(f"处理用户 {user_id} 的提醒请求 '{message_content}' 时发生未预料的错误 (分类器 v2): {str(e)}", exc_info=True)
        error_prompt = f"在处理用户设置提醒的请求（可能是 '{message_content}'）时，发生了一个我没预料到的内部错误（{type(e).__name__}）。请用你的语气向用户表达歉意，说明暂时无法完成设置，并建议他们稍后再试。"
        fallback = "哎呀，好像内部出了点小问题，暂时没法帮你设置提醒了，非常抱歉！要不稍等一下再试试看？"
        send_error_reply(user_id, error_prompt, fallback, f"通用处理错误 ({type(e).__name__})")
        return False

def extract_last_json_or_null(ai_response_text: str) -> Optional[str]:
    """
    从AI的原始响应文本中清理并提取最后一个有效的JSON对象字符串或字面量 "null"。

    Args:
        ai_response_text: AI返回的原始文本。

    Returns:
        如果找到有效的JSON对象，则返回其字符串形式。
        如果AI明确返回 "null" (清理后)，则返回字符串 "null"。
        如果没有找到有效的JSON或 "null"，则返回 None。
    """
    if ai_response_text is None:
        return None

    # 步骤 1: 移除常见的Markdown代码块标记，并去除首尾空格
    # 这个正则表达式会移除 ```json\n, ```json, \n```, ```
    processed_text = re.sub(r"```json\n?|\n?```", "", ai_response_text).strip()

    # 步骤 2: 检查清理后的文本是否完全是 "null" (不区分大小写)
    # 这是AI指示非提醒请求的明确信号
    if processed_text.lower() == 'null':
        return "null" # 返回字面量字符串 "null"

    # 步骤 3: 查找所有看起来像JSON对象的子字符串
    # re.DOTALL 使得 '.' 可以匹配换行符
    # 这个正则表达式会找到所有以 '{' 开头并以 '}' 结尾的非重叠子串
    json_candidates = re.findall(r'\{.*?\}', processed_text, re.DOTALL)

    if not json_candidates:
        # 没有找到任何类似JSON的结构，并且它也不是 "null"
        return None

    # 步骤 4: 从后往前尝试解析每个候选JSON字符串
    for candidate_str in reversed(json_candidates):
        try:
            # 尝试解析以验证它是否是有效的JSON
            json.loads(candidate_str)
            # 如果成功解析，说明这是最后一个有效的JSON对象字符串
            return candidate_str
        except json.JSONDecodeError:
            # 解析失败，继续尝试前一个候选者
            continue

    # 如果所有候选者都解析失败
    return None

def format_delay_approx(delay_seconds, target_dt):
    """将延迟秒数格式化为用户友好的大致时间描述。"""
    if delay_seconds < 60:
        # 少于1分钟，显示秒
        return f"大约 {int(delay_seconds)} 秒后"
    elif delay_seconds < 3600:
        # 少于1小时，显示分钟
        return f"大约 {int(delay_seconds / 60)} 分钟后"
    elif delay_seconds < 86400:
        # 少于1天，显示小时和分钟
        hours = int(delay_seconds / 3600)
        minutes = int((delay_seconds % 3600) / 60)
        # 如果分钟数为0，则只显示小时
        return f"大约 {hours} 小时" + (f" {minutes} 分钟后" if minutes > 0 else "后")
    else:
        # 超过1天，显示天数和目标日期时间
        days = int(delay_seconds / 86400)
        # 使用中文日期时间格式
        return f"大约 {days} 天后 ({target_dt.strftime('%Y年%m月%d日 %H:%M')}左右)"

def log_original_message_to_memory(user_id, message_content):
    """将设置提醒的原始用户消息记录到记忆日志文件（如果启用了记忆功能）。"""
    if ENABLE_MEMORY: # 检查是否启用了记忆功能
        try:
            # 获取用户对应的 prompt 文件名（或用户昵称）
            prompt_name = prompt_mapping.get(user_id, user_id)
            safe_user_id = sanitize_user_id_for_filename(user_id)
            safe_prompt_name = sanitize_user_id_for_filename(prompt_name)
            # 构建日志文件路径
            log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{safe_user_id}_{safe_prompt_name}_log.txt')
            # 准备日志条目，记录原始用户消息
            log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{user_id}] {message_content}\n"
            # 确保目录存在
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # 增强编码处理的写入
            try:
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_entry)
            except UnicodeEncodeError as e:
                logger.warning(f"UTF-8编码失败，尝试清理特殊字符: {log_file}, 错误: {e}")
                # 清理无法编码的字符
                clean_content = message_content.encode('utf-8', errors='ignore').decode('utf-8')
                clean_log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{user_id}] {clean_content}\n"
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(clean_log_entry)
                logger.info(f"已清理特殊字符并写入提醒记忆日志: {log_file}")
        except Exception as write_err:
            logger.error(f"写入用户 {user_id} 的提醒设置记忆日志失败: {write_err}")

def send_confirmation_reply(user_id, confirmation_prompt, log_context, fallback_message):
    """使用 AI 生成并发送提醒设置成功的确认消息，包含备用消息逻辑。"""
    logger.debug(f"准备发送给 AI 用于生成确认消息的提示词（部分）: {confirmation_prompt[:250]}...")
    try:
        # 调用 AI 生成确认回复，存储上下文
        confirmation_msg = get_deepseek_response(confirmation_prompt, user_id=user_id, store_context=True)
        logger.info(f"已为用户 {user_id} 生成提醒确认消息: {confirmation_msg[:100]}...")
        # 使用 send_reply 发送 AI 生成的确认消息
        send_reply(user_id, user_id, user_id, log_context, confirmation_msg)
        logger.info(f"已通过 send_reply 向用户 {user_id} 发送提醒确认消息。")
    except Exception as api_err:
        # 如果 AI 调用失败
        logger.error(f"调用API为用户 {user_id} 生成提醒确认消息失败: {api_err}. 将使用备用消息。")
        try:
             # 尝试使用 send_reply 发送预设的备用确认消息
             send_reply(user_id, user_id, user_id, f"{log_context} [备用确认]", fallback_message, is_system_message=True)
        except Exception as send_fallback_err:
             # 如果连发送备用消息都失败了，记录严重错误
             logger.critical(f"发送备用确认消息也失败 ({log_context}): {send_fallback_err}")
    
def trigger_reminder(user_id, timer_id, reminder_message):
    """当短期提醒到期时由 threading.Timer 调用的函数。"""
    global is_sending_message

    timer_key = (user_id, timer_id)
    logger.info(f"触发【短期】提醒 (ID: {timer_id})，用户 {user_id}，内容: {reminder_message}")

    # 从活动计时器列表中移除 (短期提醒)
    with timer_lock:
        if timer_key in active_timers:
            del active_timers[timer_key]
        else:
             logger.warning(f"触发时未在 active_timers 中找到短期计时器键 {timer_key}。")

    if is_quiet_time() and not ALLOW_REMINDERS_IN_QUIET_TIME:
        logger.info(f"当前为安静时间：抑制【短期】提醒 (ID: {timer_id})，用户 {user_id}。")
        return

    try:
        # 创建提醒前缀，让AI知道这是一个提醒触发
        reminder_prefix = f"提醒触发：{reminder_message}"
        
        # 将提醒消息添加到用户的消息队列，而不是直接调用API
        current_time_str = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
        formatted_message = f"[{current_time_str}] {reminder_prefix}"
        
        with queue_lock:
            if user_id not in user_queues:
                user_queues[user_id] = {
                    'messages': [formatted_message],
                    'sender_name': user_id,
                    'username': user_id,
                    'last_message_time': time.time()
                }
            else:
                user_queues[user_id]['messages'].append(formatted_message)
                user_queues[user_id]['last_message_time'] = time.time()
        
        logger.info(f"已将提醒消息 '{reminder_message}' 添加到用户 {user_id} 的消息队列，用以执行联网检查流程")

        # 可选：如果仍需语音通话功能，保留这部分
        if get_dynamic_config('USE_VOICE_CALL_FOR_REMINDERS', USE_VOICE_CALL_FOR_REMINDERS):
            try:
                wx.VoiceCall(user_id)
                logger.info(f"通过语音通话提醒用户 {user_id} (短期提醒)。")
            except Exception as voice_err:
                logger.error(f"语音通话提醒失败 (短期提醒)，用户 {user_id}: {voice_err}")

    except Exception as e:
        logger.error(f"处理【短期】提醒失败 (ID: {timer_id})，用户 {user_id}: {str(e)}", exc_info=True)
        # 即使出错，也不再使用原来的直接发送备用消息方法
        # 而是尽可能添加到队列
        try:
            fallback_msg = f"[{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')}] 提醒时间到：{reminder_message}"
            with queue_lock:
                if user_id in user_queues:
                    user_queues[user_id]['messages'].append(fallback_msg)
                    user_queues[user_id]['last_message_time'] = time.time()
                else:
                    user_queues[user_id] = {
                        'messages': [fallback_msg],
                        'sender_name': user_id,
                        'username': user_id,
                        'last_message_time': time.time()
                    }
            logger.info(f"已将备用提醒消息添加到用户 {user_id} 的消息队列")
        except Exception as fallback_e:
            logger.error(f"添加提醒备用消息到队列失败，用户 {user_id}: {fallback_e}")


def log_ai_reply_to_memory(username, reply_part):
    """将 AI 的回复部分记录到用户的记忆日志文件中。"""
    if not ENABLE_MEMORY:  # 双重检查是否意外调用
         return
    try:
        prompt_name = prompt_mapping.get(username, username)  # 使用配置的提示名作为 AI 身份
        log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_{prompt_name}_log.txt')
        log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{prompt_name}] {reply_part}\n"

        # 确保日志目录存在
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        # 增强编码处理的写入
        try:
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
        except UnicodeEncodeError as e:
            logger.warning(f"UTF-8编码失败，尝试清理特殊字符: {log_file}, 错误: {e}")
            # 清理无法编码的字符
            clean_reply = reply_part.encode('utf-8', errors='ignore').decode('utf-8')
            clean_log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{prompt_name}] {clean_reply}\n"
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(clean_log_entry)
            logger.info(f"已清理特殊字符并写入AI回复记忆日志: {log_file}")
    except Exception as log_err:
        logger.error(f"记录 AI 回复到记忆日志失败，用户 {username}: {log_err}")

def load_recurring_reminders():
    """从 JSON 文件加载重复和长期一次性提醒到内存中。"""
    global recurring_reminders
    reminders_loaded = []
    try:
        if os.path.exists(RECURRING_REMINDERS_FILE):
            with open(RECURRING_REMINDERS_FILE, 'r', encoding='utf-8') as f:
                loaded_data = json.load(f)
                if isinstance(loaded_data, list):
                    valid_reminders_count = 0
                    now = datetime.now() # 获取当前时间用于检查一次性提醒是否已过期
                    for item in loaded_data:
                        # 基本结构验证
                        if not (isinstance(item, dict) and
                                'reminder_type' in item and
                                'user_id' in item and
                                'content' in item):
                            logger.warning(f"跳过无效格式的提醒项: {item}")
                            continue

                        user_id = item.get('user_id')
                        reminder_type = item.get('reminder_type')
                        content = item.get('content')

                        # 用户有效性检查
                        if user_id not in user_names:
                             logger.warning(f"跳过未在监听列表中的用户提醒: {user_id}")
                             continue

                        # 类型特定验证
                        is_valid = False
                        if reminder_type == 'recurring':
                            time_str = item.get('time_str')
                            if time_str:
                                try:
                                    datetime.strptime(time_str, '%H:%M')
                                    is_valid = True
                                except ValueError:
                                    logger.warning(f"跳过无效时间格式的重复提醒: {item}")
                            else:
                                logger.warning(f"跳过缺少 time_str 的重复提醒: {item}")
                        elif reminder_type == 'one-off':
                            target_datetime_str = item.get('target_datetime_str')
                            if target_datetime_str:
                                try:
                                    target_dt = datetime.strptime(target_datetime_str, '%Y-%m-%d %H:%M')
                                    # 只加载未过期的一次性提醒
                                    if target_dt > now:
                                        is_valid = True
                                    else:
                                        logger.info(f"跳过已过期的一次性提醒: {item}")
                                except ValueError:
                                    logger.warning(f"跳过无效日期时间格式的一次性提醒: {item}")
                            else:
                                logger.warning(f"跳过缺少 target_datetime_str 的一次性提醒: {item}")
                        else:
                            logger.warning(f"跳过未知 reminder_type 的提醒: {item}")

                        if is_valid:
                            reminders_loaded.append(item)
                            valid_reminders_count += 1

                    # 使用锁安全地更新全局列表
                    with recurring_reminder_lock:
                        recurring_reminders = reminders_loaded
                    logger.info(f"成功从 {RECURRING_REMINDERS_FILE} 加载 {valid_reminders_count} 条有效提醒。")
                else:
                    logger.error(f"{RECURRING_REMINDERS_FILE} 文件内容不是有效的列表格式。将初始化为空列表。")
                    with recurring_reminder_lock:
                        recurring_reminders = []
        else:
            logger.info(f"{RECURRING_REMINDERS_FILE} 文件未找到。将以无提醒状态启动。")
            with recurring_reminder_lock:
                recurring_reminders = []
    except json.JSONDecodeError:
        logger.error(f"解析 {RECURRING_REMINDERS_FILE} 文件 JSON 失败。将初始化为空列表。")
        with recurring_reminder_lock:
            recurring_reminders = []
    except Exception as e:
        logger.error(f"加载提醒失败: {str(e)}", exc_info=True)
        with recurring_reminder_lock:
            recurring_reminders = [] # 确保出错时列表也被初始化

def save_recurring_reminders():
    """将内存中的当前提醒列表（重复和长期一次性）保存到 JSON 文件。"""
    global recurring_reminders
    with recurring_reminder_lock: # 获取锁保证线程安全
        temp_file_path = RECURRING_REMINDERS_FILE + ".tmp"
        # 创建要保存的列表副本，以防在写入时列表被其他线程修改
        reminders_to_save = list(recurring_reminders)
        try:
            with open(temp_file_path, 'w', encoding='utf-8') as f:
                json.dump(reminders_to_save, f, ensure_ascii=False, indent=4)
            shutil.move(temp_file_path, RECURRING_REMINDERS_FILE)
            logger.info(f"成功将 {len(reminders_to_save)} 条提醒保存到 {RECURRING_REMINDERS_FILE}")
        except Exception as e:
            logger.error(f"保存提醒失败: {str(e)}", exc_info=True)
            if os.path.exists(temp_file_path):
                try:
                    os.remove(temp_file_path)
                except OSError:
                    pass

def recurring_reminder_checker():
    """后台线程函数，每分钟检查是否有到期的重复或长期一次性提醒。"""
    last_checked_minute_str = None # 记录上次检查的 YYYY-MM-DD HH:MM
    while True:
        try:
            now = datetime.now()
            # 需要精确到分钟进行匹配
            current_datetime_minute_str = now.strftime("%Y-%m-%d %H:%M")
            current_time_minute_str = now.strftime("%H:%M") # 仅用于匹配每日重复

            # 仅当分钟数变化时才执行检查
            if current_datetime_minute_str != last_checked_minute_str:
                reminders_to_trigger_now = []
                reminders_to_remove_indices = [] # 记录需要删除的一次性提醒的索引

                # 在锁保护下读取当前的提醒列表副本
                with recurring_reminder_lock:
                    current_reminders_copy = list(recurring_reminders) # 创建副本

                for index, reminder in enumerate(current_reminders_copy):
                    reminder_type = reminder.get('reminder_type')
                    user_id = reminder.get('user_id')
                    content = reminder.get('content')
                    should_trigger = False

                    if reminder_type == 'recurring':
                        # 检查每日重复提醒 (HH:MM)
                        if reminder.get('time_str') == current_time_minute_str:
                            should_trigger = True
                            logger.info(f"匹配到每日重复提醒: 用户 {user_id}, 时间 {current_time_minute_str}, 内容: {content}")
                    elif reminder_type == 'one-off':
                        # 检查长期一次性提醒 (YYYY-MM-DD HH:MM)
                        if reminder.get('target_datetime_str') == current_datetime_minute_str:
                            should_trigger = True
                            # 标记此一次性提醒以便稍后删除
                            reminders_to_remove_indices.append(index)
                            logger.info(f"匹配到长期一次性提醒: 用户 {user_id}, 时间 {current_datetime_minute_str}, 内容: {content}")

                    if should_trigger:
                        reminders_to_trigger_now.append(reminder.copy()) # 添加副本到触发列表

                # --- 触发提醒 ---
                if reminders_to_trigger_now:
                    logger.info(f"当前时间 {current_datetime_minute_str}，发现 {len(reminders_to_trigger_now)} 条到期的提醒。")
                    if is_quiet_time() and not ALLOW_REMINDERS_IN_QUIET_TIME:
                        logger.info(f"处于安静时间，将抑制 {len(reminders_to_trigger_now)} 条提醒。")
                    else:
                        for reminder in reminders_to_trigger_now:
                            user_id = reminder['user_id']
                            content = reminder['content']
                            reminder_type = reminder['reminder_type'] # 获取类型用于日志和提示
                            logger.info(f"正在为用户 {user_id} 触发【{reminder_type}】提醒：{content}")

                            # 修改：不再直接调用API，而是将提醒添加到消息队列
                            try:
                                # 构造提醒消息前缀
                                if reminder_type == 'recurring':
                                    prefix = f"每日提醒：{content}"
                                else: # one-off
                                    prefix = f"一次性提醒：{content}"

                                # 将提醒添加到用户的消息队列
                                formatted_message = f"[{now.strftime('%Y-%m-%d %A %H:%M:%S')}] {prefix}"
                                
                                with queue_lock:
                                    if user_id not in user_queues:
                                        user_queues[user_id] = {
                                            'messages': [formatted_message],
                                            'sender_name': user_id,
                                            'username': user_id,
                                            'last_message_time': time.time()
                                        }
                                    else:
                                        user_queues[user_id]['messages'].append(formatted_message)
                                        user_queues[user_id]['last_message_time'] = time.time()
                                
                                logger.info(f"已将{reminder_type}提醒 '{content}' 添加到用户 {user_id} 的消息队列，用以执行联网检查流程")

                                # 保留语音通话功能（如果启用）
                                if get_dynamic_config('USE_VOICE_CALL_FOR_REMINDERS', USE_VOICE_CALL_FOR_REMINDERS):
                                    try:
                                        wx.VoiceCall(user_id)
                                        logger.info(f"通过语音通话提醒用户 {user_id} ({reminder_type}提醒)。")
                                    except Exception as voice_err:
                                        logger.error(f"语音通话提醒失败 ({reminder_type}提醒)，用户 {user_id}: {voice_err}")

                            except Exception as trigger_err:
                                logger.error(f"将提醒添加到消息队列失败，用户 {user_id}，提醒：{content}：{trigger_err}")

                # --- 删除已触发的一次性提醒 ---
                if reminders_to_remove_indices:
                    logger.info(f"准备从列表中删除 {len(reminders_to_remove_indices)} 条已触发的一次性提醒。")
                    something_removed = False
                    with recurring_reminder_lock:
                        # 从后往前删除，避免索引错乱
                        indices_to_delete_sorted = sorted(reminders_to_remove_indices, reverse=True)
                        original_length = len(recurring_reminders)
                        for index in indices_to_delete_sorted:
                            # 再次检查索引是否有效（理论上应该总是有效）
                            if 0 <= index < len(recurring_reminders):
                                removed_item = recurring_reminders.pop(index)
                                logger.debug(f"已从内存列表中删除索引 {index} 的一次性提醒: {removed_item.get('content')}")
                                something_removed = True
                            else:
                                logger.warning(f"尝试删除索引 {index} 时发现其无效（当前列表长度 {len(recurring_reminders)}）。")

                        if something_removed:
                            # 只有实际删除了内容才保存文件
                            logger.info(f"已从内存中删除 {original_length - len(recurring_reminders)} 条一次性提醒，正在保存更新后的列表...")
                            save_recurring_reminders() # 保存更新后的列表
                        else:
                            logger.info("没有实际删除任何一次性提醒（可能索引无效或列表已空）。")

                # 更新上次检查的分钟数
                last_checked_minute_str = current_datetime_minute_str

            # 休眠，接近一分钟检查一次
            time.sleep(58)

        except Exception as e:
            logger.error(f"提醒检查器循环出错: {str(e)}", exc_info=True)
            time.sleep(60) # 出错后等待时间稍长

# --- 检测是否需要联网搜索的函数 ---
def needs_online_search(message: str, user_id: str) -> Optional[str]:
    """
    使用主 AI 判断用户消息是否需要联网搜索，并返回需要搜索的内容。

    参数:
        message (str): 用户的消息。
        user_id (str): 用户标识符 (用于日志)。

    返回:
        Optional[str]: 如果需要联网搜索，返回需要搜索的内容；否则返回 None。
    """
    if not ENABLE_ONLINE_API:  # 如果全局禁用，直接返回 None
        return None

    # 构建用于检测的提示词
    detection_prompt = f"""
请判断以下用户消息是否明确需要查询当前、实时或非常具体的外部信息（例如：{SEARCH_DETECTION_PROMPT}）。
用户消息："{message}"

如果需要联网搜索，请回答 "需要联网"，并在下一行提供你认为需要搜索的内容。
如果不需要联网搜索（例如：常规聊天、询问一般知识、历史信息、角色扮演对话等），请只回答 "不需要联网"。
请不要添加任何其他解释。
"""
    try:
        # 根据配置选择使用辅助模型或主模型
        if ENABLE_ASSISTANT_MODEL:
            logger.info(f"向辅助模型发送联网检测请求，用户: {user_id}，消息: '{message[:50]}...'")
            response = get_assistant_response(detection_prompt, f"online_detection_{user_id}")
        else:
            logger.info(f"向主 AI 发送联网检测请求，用户: {user_id}，消息: '{message[:50]}...'")
            response = get_deepseek_response(detection_prompt, user_id=f"online_detection_{user_id}", store_context=False)

        # 清理并判断响应
        cleaned_response = response.strip()
        if "</think>" in cleaned_response:
            cleaned_response = cleaned_response.split("</think>", 1)[1].strip()
        
        if ENABLE_ASSISTANT_MODEL:
            logger.info(f"辅助模型联网检测响应: '{cleaned_response}'")
        else:
            logger.info(f"主模型联网检测响应: '{cleaned_response}'")

        if "不需要联网" in cleaned_response:
            logger.info(f"用户 {user_id} 的消息不需要联网。")
            return None
        elif "需要联网" in cleaned_response:
            # 提取需要搜索的内容
            search_content = cleaned_response.split("\n", 1)[1].strip() if "\n" in cleaned_response else ""
            logger.info(f"检测到用户 {user_id} 的消息需要联网，搜索内容: '{search_content}'")
            return search_content
        else:
            logger.warning(f"无法解析联网检测响应，用户: {user_id}，响应: '{cleaned_response}'")
            return None

    except Exception as e:
        logger.error(f"联网检测失败，用户: {user_id}，错误: {e}", exc_info=True)
        return None  # 出错时默认不需要联网

# --- 调用在线模型的函数 ---
def get_online_model_response(query: str, user_id: str) -> Optional[str]:
    """
    使用配置的在线 API 获取搜索结果。

    参数:
        query (str): 要发送给在线模型的查询（通常是用户消息）。
        user_id (str): 用户标识符 (用于日志)。

    返回:
        Optional[str]: 在线 API 的回复内容，如果失败则返回 None。
    """
    if not online_client: # 检查在线客户端是否已成功初始化
        logger.error(f"在线 API 客户端未初始化，无法为用户 {user_id} 执行在线搜索。")
        return None

    # 获取当前时间并格式化为字符串
    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 结合固定的提示词、当前时间和用户查询
    online_query_prompt = f"请在互联网上查找相关信息，忽略过时信息，并给出简要的回答。\n{ONLINE_FIXED_PROMPT}\n当前时间：{current_time_str}\n\n{query}"

    try:
        logger.info(f"调用在线 API - 用户: {user_id}, 查询: '{query[:100]}...'")
        # 使用 online_client 调用在线模型
        response = online_client.chat.completions.create(
            model=ONLINE_MODEL,
            messages=[{"role": "user", "content": online_query_prompt}],
            temperature=ONLINE_API_TEMPERATURE,
            max_tokens=ONLINE_API_MAX_TOKEN,
            stream=False
        )

        if not response.choices:
            logger.error(f"在线 API 返回了空的选择项，用户: {user_id}")
            return None

        # 检查在线API是否返回了空的消息内容
        message_content = response.choices[0].message.content
        if message_content is None:
            logger.error(f"在线API返回了空的信息，可能是因为触发了安全检查机制，请修改Prompt并清空上下文再试 (用户: {user_id})")
            return None

        reply = message_content.strip()
        # 清理回复，去除思考过程
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()
        logger.info(f"在线 API 响应 (用户 {user_id}): {reply}")
        return reply

    except Exception as e:
        logger.error(f"调用在线 API 失败，用户: {user_id}: {e}", exc_info=True)
        return "抱歉，在线搜索功能暂时出错了。"

def monitor_memory_usage():
    import psutil
    MEMORY_THRESHOLD = 328  # 内存使用阈值328MB
    while True:
        process = psutil.Process(os.getpid())
        memory_usage = process.memory_info().rss / 1024 / 1024  # MB
        logger.info(f"当前内存使用: {memory_usage:.2f} MB")
        if memory_usage > MEMORY_THRESHOLD:
            logger.warning(f"内存使用超过阈值 ({MEMORY_THRESHOLD} MB)，执行垃圾回收")
            import gc
            gc.collect()
        time.sleep(600)

def scheduled_restart_checker():
    """
    定时检查是否需要重启程序。
    重启条件：
    1. 已达到RESTART_INTERVAL_HOURS的运行时间
    2. 在RESTART_INACTIVITY_MINUTES内没有活动，或活动结束后又等待了RESTART_INACTIVITY_MINUTES
    3. 没有正在进行的短期提醒事件
    4. 没有即将到来（5分钟内）的长期提醒或每日重复提醒事件
    """
    global program_start_time, last_received_message_timestamp # 引用全局变量

    if not ENABLE_SCHEDULED_RESTART:
        logger.info("定时重启功能已禁用。")
        return

    logger.info(f"定时重启功能已启用。重启间隔: {RESTART_INTERVAL_HOURS} 小时，不活跃期: {RESTART_INACTIVITY_MINUTES} 分钟。")

    restart_interval_seconds = RESTART_INTERVAL_HOURS * 3600
    inactivity_seconds = RESTART_INACTIVITY_MINUTES * 60

    if restart_interval_seconds <= 0:
        logger.error("重启间隔时间必须大于0，定时重启功能将不会启动。")
        return
    
    # 初始化下一次检查重启的时间点
    next_restart_time = program_start_time + restart_interval_seconds
    restart_pending = False  # 标记是否处于待重启状态（已达到间隔时间但在等待不活跃期）

    while True:
        current_time = time.time()
        time_since_last_activity = current_time - last_received_message_timestamp
        
        # 准备重启的三个条件检查
        interval_reached = current_time >= next_restart_time or restart_pending
        inactive_enough = time_since_last_activity >= inactivity_seconds
        
        # 只有在准备重启时才检查提醒事件，避免不必要的检查
        if interval_reached and inactive_enough:
            # 检查是否有正在进行的短期提醒
            has_active_short_reminders = False
            with timer_lock:
                if active_timers:
                    logger.info(f"当前有 {len(active_timers)} 个短期提醒进行中，等待它们完成后再重启。")
                    has_active_short_reminders = True
            
            # 检查是否有即将到来的提醒（5分钟内）
            has_upcoming_reminders = False
            now = datetime.now()
            five_min_later = now + dt.timedelta(minutes=5)
            
            with recurring_reminder_lock:
                for reminder in recurring_reminders:
                    target_dt = None
                    
                    # 处理长期一次性提醒
                    if reminder.get('reminder_type') == 'one-off':
                        try:
                            target_dt = datetime.strptime(reminder.get('target_datetime_str'), '%Y-%m-%d %H:%M')
                        except (ValueError, TypeError):
                            continue
                    
                    # 处理每日重复提醒 - 需要结合当前日期计算今天的触发时间
                    elif reminder.get('reminder_type') == 'recurring':
                        try:
                            time_str = reminder.get('time_str')
                            if time_str:
                                # 解析时间字符串获取小时和分钟
                                reminder_time = datetime.strptime(time_str, '%H:%M').time()
                                # 结合当前日期构建完整的目标时间
                                target_dt = datetime.combine(now.date(), reminder_time)
                                
                                # 如果今天的触发时间已过，检查明天的触发时间是否在5分钟内
                                # (极少情况：如果定时检查恰好在23:55-00:00之间，且有0:00-0:05的提醒)
                                if target_dt < now:
                                    target_dt = datetime.combine(now.date() + dt.timedelta(days=1), reminder_time)
                        except (ValueError, TypeError):
                            continue
                    
                    # 检查目标时间是否在5分钟内
                    if target_dt and now <= target_dt <= five_min_later:
                        reminder_type = "长期一次性" if reminder.get('reminder_type') == 'one-off' else "每日重复"
                        display_time = target_dt.strftime('%Y-%m-%d %H:%M') if reminder.get('reminder_type') == 'one-off' else target_dt.strftime('%H:%M')
                        logger.info(f"检测到5分钟内即将执行的{reminder_type}提醒，延迟重启。提醒时间: {display_time}")
                        has_upcoming_reminders = True
                        break
            
            # 如果没有提醒阻碍，则可以重启
            if not has_active_short_reminders and not has_upcoming_reminders:
                logger.warning(f"满足重启条件：已运行约 {(current_time - program_start_time)/3600:.2f} 小时，已持续 {time_since_last_activity/60:.1f} 分钟无活动，且没有即将执行的提醒。准备重启程序...")
                try:
                    # --- 执行重启前的清理操作 ---
                    logger.info("定时重启前：保存聊天上下文...")
                    with queue_lock:
                        save_chat_contexts()
                    
                    # 保存用户计时器状态
                    if get_dynamic_config('ENABLE_AUTO_MESSAGE', ENABLE_AUTO_MESSAGE):
                        logger.info("定时重启前：保存用户计时器状态...")
                        save_user_timers()
                    
                    if ENABLE_REMINDERS:
                        logger.info("定时重启前：保存提醒列表...")
                        with recurring_reminder_lock:
                            save_recurring_reminders()
                    
                    # 关闭异步HTTP日志处理器
                    if 'async_http_handler' in globals() and isinstance(async_http_handler, AsyncHTTPHandler):
                        logger.info("定时重启前：关闭异步HTTP日志处理器...")
                        async_http_handler.close()
                    
                    logger.info("定时重启前：执行最终临时文件清理...")
                    clean_up_temp_files()
                    
                    logger.info("正在执行重启...")
                    # 使用subprocess.Popen而不是os.execv，避免命令注入风险
                    import subprocess
                    current_script = os.path.abspath(__file__)
                    # 只传递脚本路径，不传递sys.argv中可能存在的危险参数
                    subprocess.Popen(
                        [sys.executable, current_script],
                        cwd=os.path.dirname(current_script)
                    )
                    logger.info("新进程已启动，当前进程即将退出")
                    sys.exit(0)
                except Exception as e:
                    logger.error(f"执行重启操作时发生错误: {e}", exc_info=True)
                    # 如果重启失败，推迟下一次检查，避免短时间内连续尝试
                    restart_pending = False
                    next_restart_time = current_time + restart_interval_seconds 
                    logger.info(f"重启失败，下一次重启检查时间推迟到: {datetime.fromtimestamp(next_restart_time).strftime('%Y-%m-%d %H:%M:%S')}")
            elif has_upcoming_reminders:
                # 有提醒即将执行，延长10分钟后再检查
                logger.info(f"由于5分钟内有提醒将执行，延长重启时间10分钟。")
                next_restart_time = current_time + 600  # 延长10分钟
                restart_pending = True  # 保持待重启状态
            else:
                # 有短期提醒正在进行，稍后再检查
                logger.info(f"由于有短期提醒正在进行，将在下一轮检查是否可以重启。")
                restart_pending = True  # 保持待重启状态
        elif interval_reached and not inactive_enough:
            # 已达到间隔时间但最近有活动，设置待重启状态
            if not restart_pending:
                logger.info(f"已达到重启间隔({RESTART_INTERVAL_HOURS}小时)，但最近 {time_since_last_activity/60:.1f} 分钟内有活动，将在 {RESTART_INACTIVITY_MINUTES} 分钟无活动后重启。")
                restart_pending = True
            # 不更新next_restart_time，因为我们现在是等待不活跃期
        elif current_time >= next_restart_time and not restart_pending:
            # 第一次达到重启时间点
            logger.info(f"已达到计划重启检查点 ({RESTART_INTERVAL_HOURS}小时)。距离上次活动: {time_since_last_activity/60:.1f}分钟 (不活跃阈值: {RESTART_INACTIVITY_MINUTES}分钟)。")
            restart_pending = True  # 进入待重启状态
        
        # 每分钟检查一次条件
        time.sleep(60)

# 发送心跳的函数
# Waitress优化：创建心跳专用session，复用连接
_heartbeat_session = None

def _init_heartbeat_session():
    """初始化心跳专用Session（连接池复用）"""
    global _heartbeat_session
    if _heartbeat_session is None:
        _heartbeat_session = requests.Session()
        # 配置连接池参数
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=1,
            pool_maxsize=2,
            max_retries=0,
            pool_block=False
        )
        _heartbeat_session.mount('http://', adapter)
        _heartbeat_session.mount('https://', adapter)
        # 设置请求头
        _heartbeat_session.headers.update({
            'User-Agent': 'WeChatBot-Heartbeat/2.0',
            'Connection': 'keep-alive',
            'Content-Type': 'application/json'
        })
    return _heartbeat_session

def send_heartbeat():
    """向Waitress服务器发送心跳信号（使用连接池复用）"""
    heartbeat_url = f"{FLASK_SERVER_URL_BASE}/bot_heartbeat"
    payload = {
        'status': 'alive',
        'pid': os.getpid()  # 发送当前进程PID，方便调试
    }
    
    try:
        session = _init_heartbeat_session()
        # 使用更精确的超时设置（连接超时, 读取超时）
        response = session.post(heartbeat_url, json=payload, timeout=(3, 5))
        
        if response.status_code == 200:
            logger.debug(f"心跳发送成功至Waitress服务器 (PID: {os.getpid()})")
        else:
            logger.warning(f"心跳响应异常，状态码: {response.status_code} (PID: {os.getpid()})")
            
    except requests.exceptions.Timeout:
        logger.warning(f"心跳发送超时 - Waitress服务器可能繁忙 (PID: {os.getpid()})")
        
    except requests.exceptions.ConnectionError as e:
        error_msg = str(e)
        if 'Connection refused' in error_msg:
            logger.error(f"无法连接到Waitress服务器 - 服务器可能未启动 (PID: {os.getpid()})")
        else:
            logger.error(f"心跳发送连接错误: {error_msg[:100]} (PID: {os.getpid()})")
            
    except requests.exceptions.RequestException as e:
        logger.error(f"心跳发送网络错误: {str(e)[:100]} (PID: {os.getpid()})")
        
    except Exception as e:
        logger.error(f"心跳发送未知错误: {str(e)[:100]} (PID: {os.getpid()})", exc_info=True)


# 心跳线程函数
def heartbeat_thread_func():
    """心跳线程，定期发送心跳"""
    logger.info(f"机器人心跳线程启动 (PID: {os.getpid()})，每 {HEARTBEAT_INTERVAL} 秒发送一次心跳。")
    while True:
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)

# 保存用户计时器状态的函数
def save_user_timers():
    """将用户计时器状态保存到文件"""
    temp_file_path = USER_TIMERS_FILE + ".tmp"
    try:
        timer_data = {
            'user_timers': dict(user_timers),
            'user_wait_times': dict(user_wait_times)
        }
        with open(temp_file_path, 'w', encoding='utf-8') as f:
            json.dump(timer_data, f, ensure_ascii=False, indent=4)
        shutil.move(temp_file_path, USER_TIMERS_FILE)
        logger.info(f"用户计时器状态已保存到 {USER_TIMERS_FILE}")
    except Exception as e:
        logger.error(f"保存用户计时器状态失败: {e}", exc_info=True)
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except OSError:
                pass

# 加载用户计时器状态的函数
def load_user_timers():
    """从文件加载用户计时器状态"""
    global user_timers, user_wait_times
    try:
        if os.path.exists(USER_TIMERS_FILE):
            with open(USER_TIMERS_FILE, 'r', encoding='utf-8') as f:
                timer_data = json.load(f)
                if isinstance(timer_data, dict):
                    loaded_user_timers = timer_data.get('user_timers', {})
                    loaded_user_wait_times = timer_data.get('user_wait_times', {})
                    
                    # 验证并恢复有效的计时器状态
                    restored_count = 0
                    for user in user_names:
                        if (user in loaded_user_timers and user in loaded_user_wait_times and
                            isinstance(loaded_user_timers[user], (int, float)) and
                            isinstance(loaded_user_wait_times[user], (int, float))):
                            user_timers[user] = loaded_user_timers[user]
                            user_wait_times[user] = loaded_user_wait_times[user]
                            restored_count += 1
                            logger.debug(f"已恢复用户 {user} 的计时器状态")
                        else:
                            # 如果没有保存的状态或状态无效，则初始化
                            reset_user_timer(user)
                            logger.debug(f"为用户 {user} 重新初始化计时器状态")
                    
                    logger.info(f"成功从 {USER_TIMERS_FILE} 恢复 {restored_count} 个用户的计时器状态")
                else:
                    logger.warning(f"{USER_TIMERS_FILE} 文件格式不正确，将重新初始化所有计时器")
                    initialize_all_user_timers()
        else:
            logger.info(f"{USER_TIMERS_FILE} 未找到，将初始化所有用户计时器")
            initialize_all_user_timers()
    except json.JSONDecodeError:
        logger.error(f"解析 {USER_TIMERS_FILE} 失败，将重新初始化所有计时器")
        initialize_all_user_timers()
    except Exception as e:
        logger.error(f"加载用户计时器状态失败: {e}", exc_info=True)
        initialize_all_user_timers()

def initialize_all_user_timers():
    """初始化所有用户的计时器"""
    for user in user_names:
        reset_user_timer(user)
    logger.info("所有用户计时器已重新初始化")


def main():
    try:
        # --- 启动前检查 ---
        logger.info("\033[32m进行启动前检查...\033[0m")

        # 预检查所有用户prompt文件
        for user in user_names:
            prompt_file = prompt_mapping.get(user, user)
            prompt_path = os.path.join(root_dir, 'prompts', f'{prompt_file}.md')
            if not os.path.exists(prompt_path):
                raise FileNotFoundError(f"用户 {user} 的prompt文件 {prompt_file}.md 不存在")

        # 确保临时目录存在
        memory_temp_dir = os.path.join(root_dir, MEMORY_TEMP_DIR)
        os.makedirs(memory_temp_dir, exist_ok=True)
        
        # 确保核心记忆目录存在（当启用单独文件存储时使用）
        core_memory_dir = os.path.join(root_dir, CORE_MEMORY_DIR)
        os.makedirs(core_memory_dir, exist_ok=True)

        # 加载聊天上下文
        logger.info("正在加载聊天上下文...")
        load_chat_contexts() # 调用加载函数

        if ENABLE_REMINDERS:
             logger.info("提醒功能已启用。")
             # 加载已保存的提醒 (包括重复和长期一次性)
             load_recurring_reminders()
             if not isinstance(ALLOW_REMINDERS_IN_QUIET_TIME, bool):
                  logger.warning("配置项 ALLOW_REMINDERS_IN_QUIET_TIME 的值不是布尔类型 (True/False)，可能导致意外行为。")
        else:
            logger.info("提醒功能已禁用 (所有类型提醒将无法使用)。")

        # --- 初始化 ---
        logger.info("\033[32m初始化微信接口和清理临时文件...\033[0m")
        clean_up_temp_files()
        global wx
        try:
            wx = WeChat()
            wx.Show()
        except:
            logger.error(f"\033[31m无法初始化微信接口，请确保您安装的是微信3.9版本，并且已经登录！\033[0m")
            exit(1)

        for user_name in user_names:
            if user_name == ROBOT_WX_NAME:
                logger.error(f"\033[31m您填写的用户列表中包含自己登录的微信昵称，请删除后再试！\033[0m")
                exit(1)
            ListenChat = wx.AddListenChat(nickname=user_name, callback=message_listener)
            if ListenChat:
                logger.info(f"成功添加监听用户{ListenChat}")
            else:
                logger.error(f"\033[31m添加监听用户{user_name}失败，请确保您在用户列表填写的微信昵称/备注与实际完全匹配，并且不要包含表情符号和特殊符号，注意填写的不是自己登录的微信昵称!\033[0m")
                exit(1)
        logger.info("监听用户添加完成")
        
        # 初始化所有用户的自动消息计时器 - 总是初始化，以便功能开启时立即可用
        logger.info("正在加载用户自动消息计时器状态...")
        load_user_timers()  # 替换原来的初始化代码
        logger.info("用户自动消息计时器状态加载完成。")
        
        # 初始化群聊类型缓存
        if IGNORE_GROUP_CHAT_FOR_AUTO_MESSAGE:
            logger.info("主动消息群聊忽略功能已启用，正在初始化群聊类型缓存...")
            update_group_chat_cache()
            logger.info("群聊类型缓存初始化完成。")
        else:
            logger.info("主动消息群聊忽略功能已禁用。")

        # --- 启动窗口保活线程 ---
        logger.info("\033[32m启动窗口保活线程...\033[0m")
        listener_thread = threading.Thread(target=keep_alive, name="keep_alive")
        listener_thread.daemon = True
        listener_thread.start()
        logger.info("消息窗口保活已启动。")

        checker_thread = threading.Thread(target=check_inactive_users, name="InactiveUserChecker")
        checker_thread.daemon = True
        checker_thread.start()
        logger.info("非活跃用户检查与消息处理线程已启动。")

         # 启动定时重启检查线程 (如果启用)
        global program_start_time, last_received_message_timestamp
        program_start_time = time.time()
        last_received_message_timestamp = time.time()
        if ENABLE_SCHEDULED_RESTART:
            restart_checker_thread = threading.Thread(target=scheduled_restart_checker, name="ScheduledRestartChecker")
            restart_checker_thread.daemon = True # 设置为守护线程，主程序退出时它也会退出
            restart_checker_thread.start()
            logger.info("定时重启检查线程已启动。")

        if ENABLE_MEMORY:
            memory_thread = threading.Thread(target=memory_manager, name="MemoryManager")
            memory_thread.daemon = True
            memory_thread.start()
            logger.info("记忆管理线程已启动。")
        else:
             logger.info("记忆功能已禁用。")

        # 检查重复和长期一次性提醒
        if ENABLE_REMINDERS:
            reminder_checker_thread = threading.Thread(target=recurring_reminder_checker, name="ReminderChecker")
            reminder_checker_thread.daemon = True
            reminder_checker_thread.start()
            logger.info("提醒检查线程（重复和长期一次性）已启动。")

        # 自动消息 - 线程总是启动，但根据动态配置决定是否工作
        auto_message_thread = threading.Thread(target=check_user_timeouts, name="AutoMessageChecker")
        auto_message_thread.daemon = True
        auto_message_thread.start()
        current_auto_msg_status = get_dynamic_config('ENABLE_AUTO_MESSAGE', ENABLE_AUTO_MESSAGE)
        logger.info(f"主动消息检查线程已启动 (当前状态: {'启用' if current_auto_msg_status else '禁用'})。")
        
        # 启动心跳线程
        heartbeat_th = threading.Thread(target=heartbeat_thread_func, name="BotHeartbeatThread", daemon=True)
        heartbeat_th.start()

        logger.info("\033[32mBOT已成功启动并运行中...\033[0m")

        # 启动内存使用监控线程
        monitor_memory_usage_thread = threading.Thread(target=monitor_memory_usage, name="MemoryUsageMonitor")
        monitor_memory_usage_thread.daemon = True
        monitor_memory_usage_thread.start()
        logger.info("内存使用监控线程已启动。")

        wx.KeepRunning()

        while True:
            time.sleep(60)

    except FileNotFoundError as e:
        logger.critical(f"初始化失败: 缺少必要的文件或目录 - {str(e)}")
        logger.error(f"\033[31m错误：{str(e)}\033[0m")
    except Exception as e:
        logger.critical(f"主程序发生严重错误: {str(e)}", exc_info=True)
    finally:
        logger.info("程序准备退出，执行清理操作...")

        # 保存用户计时器状态（如果启用了自动消息）
        if get_dynamic_config('ENABLE_AUTO_MESSAGE', ENABLE_AUTO_MESSAGE):
            logger.info("程序退出前：保存用户计时器状态...")
            save_user_timers()

        # 取消活动的短期一次性提醒定时器
        with timer_lock:
            if active_timers:
                 logger.info(f"正在取消 {len(active_timers)} 个活动的短期一次性提醒定时器...")
                 cancelled_count = 0
                 # 使用 list(active_timers.items()) 创建副本进行迭代
                 for timer_key, timer in list(active_timers.items()):
                     try:
                         timer.cancel()
                         cancelled_count += 1
                     except Exception as cancel_err:
                         logger.warning(f"取消短期定时器 {timer_key} 时出错: {cancel_err}")
                 active_timers.clear()
                 logger.info(f"已取消 {cancelled_count} 个短期一次性定时器。")
            else:
                 logger.info("没有活动的短期一次性提醒定时器需要取消。")

        if 'async_http_handler' in globals() and isinstance(async_http_handler, AsyncHTTPHandler):
            logger.info("正在关闭异步HTTP日志处理器...")
            try:
                 async_http_handler.close()
                 logger.info("异步HTTP日志处理器已关闭。")
            except Exception as log_close_err:
                 logger.error(f"关闭异步日志处理器时出错: {log_close_err}")
        
        # 关闭心跳Session，释放Waitress连接
        global _heartbeat_session
        if _heartbeat_session is not None:
            logger.info("正在关闭心跳连接池...")
            try:
                _heartbeat_session.close()
                logger.info("心跳连接池已关闭 [Waitress]")
            except Exception as hb_close_err:
                logger.error(f"关闭心跳连接池时出错: {hb_close_err}")

        logger.info("执行最终临时文件清理...")
        clean_up_temp_files()
        logger.info("程序退出。")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("接收到用户中断信号 (Ctrl+C)，程序将退出。")
    except Exception as e:
        logger.error(f"程序启动或运行期间发生未捕获的顶层异常: {str(e)}", exc_info=True)
        print(f"FALLBACK LOG: {datetime.now()} - CRITICAL ERROR - {str(e)}")
