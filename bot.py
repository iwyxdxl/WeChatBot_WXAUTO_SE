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

import base64
import requests
import logging
from datetime import datetime
import datetime as dt
import threading
import time
import os
from Mwxauto.wxauto import WeChat
from openai import OpenAI
import random
from typing import Optional
import pyautogui
import shutil
import re
from config import *
import queue
import json
from threading import Timer
import sys

# 生成用户昵称列表和prompt映射字典
user_names = [entry[0] for entry in LISTEN_LIST]
prompt_mapping = {entry[0]: entry[1] for entry in LISTEN_LIST}

# 获取微信窗口对象
wx = WeChat()
ROBOT_WX_NAME = wx.A_MyIcon.Name
# 设置监听列表
for user_name in user_names:
    wx.AddListenChat(who=user_name, savepic=True, savevoice=True)
# 持续监听消息，并且收到消息后回复
wait = 1  # 设置1秒查看一次是否有新消息

# 获取程序根目录
root_dir = os.path.dirname(os.path.abspath(__file__))

# 用户消息队列和聊天上下文管理
user_queues = {}  # {user_id: {'messages': [], 'last_message_time': 时间戳, ...}}
queue_lock = threading.Lock()  # 队列访问锁
chat_contexts = {}  # {user_id: [{'role': 'user', 'content': '...'}, ...]}

# 用户消息队列和聊天上下文管理
user_queues = {}
queue_lock = threading.Lock()
chat_contexts = {}

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

class AsyncHTTPHandler(logging.Handler):
    def __init__(self, url, retry_attempts=3, timeout=3, max_queue_size=1000, batch_size=10, batch_timeout=5):
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
        # 后台线程用于处理日志队列
        self.worker = threading.Thread(target=self._process_queue, daemon=True)
        self.worker.start()

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

    def _process_queue(self):
        """
        后台工作线程，积累一定数量的日志后批量发送到目标 URL。
        """
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': 'WeChatBot/1.0'
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
                    self._send_batch(batch, headers)
                    batch = []  # 清空批处理列表
                    last_batch_time = current_time  # 重置超时计时器
            
            except Exception as e:
                # 出错时清空当前批次，避免卡住
                batch = []
                last_batch_time = time.time()
                time.sleep(1)  # 出错后暂停一下，避免CPU占用过高
        
        # 关闭前发送剩余的日志
        if batch:
            self._send_batch(batch, headers)

    def _send_batch(self, batch, headers):
        """发送一批日志记录"""
        data = {'logs': batch}  # 发送格式改为包含多条日志的列表
        
        for attempt in range(self.retry_attempts):
            try:
                resp = requests.post(
                    self.url,
                    json=data,
                    headers=headers,
                    timeout=self.timeout
                )
                resp.raise_for_status()  # 检查 HTTP 错误状态码
                # 发送成功，记录日志数量
                logger.debug(f"成功批量发送 {len(batch)} 条日志")
                break
            except requests.exceptions.RequestException as e:
                # 如果不是最后一次尝试，稍作等待后重试
                if attempt < self.retry_attempts - 1:
                    time.sleep(1 * (attempt + 1))  # 等待时间随重试次数增加
    
    def close(self):
        """
        停止工作线程并等待队列处理完成（或超时）。
        """
        if not self.log_queue.empty():
            pass  # 等待队列任务完成
        self._stop_event.set()
        self.worker.join(timeout=self.timeout * self.retry_attempts + 5)  # 等待一个合理的时间
        super().close()

# 创建日志格式器
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

# 初始化异步HTTP处理器
async_http_handler = AsyncHTTPHandler(
    url=f'http://localhost:{PORT}/api/log',
    batch_size=20,  # 一次发送20条日志
    batch_timeout=3  # 即使不满20条，最多等待3秒也发送
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

# 存储用户的计时器和随机等待时间
user_timers = {}
user_wait_times = {}
emoji_timer = None
emoji_timer_lock = threading.Lock()
# 全局变量，控制消息发送状态
can_send_messages = True
is_sending_message = False

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
    """
    if ENABLE_AUTO_MESSAGE:
        while True:
            current_epoch_time = time.time()

            for user in user_names:
                last_active = user_timers.get(user)
                wait_time = user_wait_times.get(user)

                if isinstance(last_active, (int, float)) and isinstance(wait_time, (int, float)):
                    if current_epoch_time - last_active >= wait_time and not is_quiet_time():
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
                                if len(user_queues[user]['messages']) > 5:
                                    user_queues[user]['messages'].pop(0)
                                user_queues[user]['last_message_time'] = time.time()

                        # 重置计时器（不触发 on_user_message）
                        reset_user_timer(user)
            time.sleep(10)

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

# 修改get_user_prompt函数
def get_user_prompt(user_id):
    # 查找映射中的文件名，若不存在则使用user_id
    prompt_file = prompt_mapping.get(user_id, user_id)
    prompt_path = os.path.join(root_dir, 'prompts', f'{prompt_file}.md')
    
    if not os.path.exists(prompt_path):
        logger.error(f"Prompt文件不存在: {prompt_path}")
        raise FileNotFoundError(f"Prompt文件 {prompt_file}.md 未找到于 prompts 目录")

    with open(prompt_path, 'r', encoding='utf-8') as file:
        prompt_content = file.read()
        if UPLOAD_MEMORY_TO_AI:
            return prompt_content
        else:
            memory_marker = "## 记忆片段"
            if memory_marker in prompt_content:
                prompt_content = prompt_content.split(memory_marker, 1)[0].strip()
            return prompt_content
             

def get_deepseek_response(message, user_id, store_context=True):
    """
    从 DeepSeek API 获取响应，确保正确的上下文处理。

    参数:
        message (str): 用户的消息或系统提示词（用于工具调用）。
        user_id (str): 用户或系统组件的标识符。
        store_context (bool): 是否将此交互存储到聊天上下文中。
                              对于工具调用（如解析或总结），设置为 False。
    """
    try:
        logger.info(f"调用 Chat API - ID: {user_id}, 是否存储上下文: {store_context}, 消息: {message}")  # 日志记录消息片段

        messages_to_send = []
        MAX_GROUPS = 5  # 最大消息对数（用户+助手）
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
            with queue_lock:
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

        else:
            # --- 处理工具调用（如提醒解析、总结） ---
            # 仅发送提供的消息，通常作为用户提示词发送给 AI
            # 不包括系统提示或用户聊天上下文中的历史记录
            messages_to_send.append({"role": "user", "content": message})
            logger.info(f"工具调用 (store_context=False)，ID: {user_id}。仅发送提供的消息。")

        # --- 调用 API ---
        # logger.debug(f"发送给 API 的消息 (ID: {user_id}): {messages_to_send}")  # 可选：调试日志
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages_to_send,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKEN,
            stream=False
        )

        if not response.choices:
            logger.error("API 返回了空的选择项。")
            return "服务响应异常，请稍后再试"  # 服务响应异常

        reply = response.choices[0].message.content.strip()

        # --- 如果需要，存储助手回复到上下文中 ---
        if store_context:
            with queue_lock:
                # 确保上下文存在（如果 store_context 为 True，这里应该总是存在）
                if user_id not in chat_contexts:
                   chat_contexts[user_id] = []  # 安全初始化

                # 将助手回复添加到持久存储中
                chat_contexts[user_id].append({"role": "assistant", "content": reply})

                # 在添加助手回复后对持久上下文进行最终裁剪
                if len(chat_contexts[user_id]) > context_limit:  # 检查实际限制
                    # 从开头裁剪以保持限制
                    chat_contexts[user_id] = chat_contexts[user_id][-context_limit:]

        logger.info(f"API 回复 (ID: {user_id}): {reply[:100]}...")
        return reply

    except Exception as e:
        ErrorImformation = str(e)
        logger.error(f"Chat 调用失败 (ID: {user_id}): {str(e)}", exc_info=True)
        if "real name verification" in ErrorImformation:
            logger.error("\033[31m错误：API 服务商反馈请完成实名认证后再使用！\033[0m")
        elif "rate" in ErrorImformation:
            logger.error("\033[31m错误：API 服务商反馈当前访问 API 服务频次达到上限，请稍后再试！\033[0m")
        elif "paid" in ErrorImformation:
            logger.error("\033[31m错误：API 服务商反馈您正在使用付费模型，请先充值再使用或使用免费额度模型！\033[0m")
        elif "Api key is invalid" in ErrorImformation:
            logger.error("\033[31m错误：API 服务商反馈 API KEY 不可用，请检查配置选项！\033[0m")
        elif "busy" in ErrorImformation:
            logger.error("\033[31m错误：API 服务商反馈服务器繁忙，请稍后再试！\033[0m")
        else:
            logger.error("\033[31m错误：" + str(e) + "\033[0m")
        return "抱歉，我现在有点忙，稍后再聊吧。"

def message_listener():
    while True:
        try:
            if wx is None:
                wx = WeChat()
                for user_name in user_names:
                    wx.AddListenChat(who=user_name, savepic=True, savevoice=True)
                    
            msgs = wx.GetListenMessage()
            for chat in msgs:
                who = chat.who
                one_msgs = msgs.get(chat)
                for msg in one_msgs:
                    msgtype = msg.type
                    content = msg.content
                    logger.info(f'【{who}】：{content}')
                    if not content:
                        continue
                    if msgtype != 'friend':
                        logger.debug(f"非好友消息，忽略! 消息类型: {msgtype}")
                        continue
                    if who == msg.sender:
                        if '[动画表情]' in content and ENABLE_EMOJI_RECOGNITION:
                            handle_emoji_message(msg, who)
                        else:
                            handle_wxauto_message(msg, who)
                    elif ACCEPT_ALL_GROUP_CHAT_MESSAGES:
                        if not msg.content.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                            msg.content = "[群聊消息-" +"发送者:"+ msg.sender + "]:" + msg.content
                        if '[动画表情]' in content and ENABLE_EMOJI_RECOGNITION:
                            handle_emoji_message(msg, who)
                        else:
                            handle_wxauto_message(msg, who)
                    elif ROBOT_WX_NAME != '' and (bool(re.search(f'@{ROBOT_WX_NAME}\u2005', msg.content))):  
                        # 处理群聊信息，只有@当前机器人才会处理
                        msg.content = re.sub(f'@{ROBOT_WX_NAME}\u2005', '', content).strip()
                        msg.content = "[群聊消息-" +"发送者:"+ msg.sender + "]:" + msg.content
                        handle_wxauto_message(msg, who)
                    else:
                        logger.debug(f"非需要处理消息: {content}")   
                        
        except Exception as e:
            logger.error(f"Message: {str(e)}")
            logger.error("\033[31m重要提示：请不要关闭程序打开的微信聊天框！若命令窗口收不到消息，请将微信聊天框置于最前台！ \033[0m")
            wx = None
        time.sleep(wait)

def recognize_image_with_moonshot(image_path, is_emoji=False):
    # 先暂停向DeepSeek API发送消息队列
    global can_send_messages
    can_send_messages = False

    """使用AI识别图片内容并返回文本"""
    with open(image_path, 'rb') as img_file:
        image_content = base64.b64encode(img_file.read()).decode('utf-8')
    headers = {
        'Authorization': f'Bearer {MOONSHOT_API_KEY}',
        'Content-Type': 'application/json'
    }
    text_prompt = "请用中文描述这个图片" if not is_emoji else "请用中文描述这个聊天窗口的最后一张表情包"
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
    try:
        response = requests.post(f"{MOONSHOT_BASE_URL}/chat/completions", headers=headers, json=data)
        response.raise_for_status()
        result = response.json()
        recognized_text = result['choices'][0]['message']['content']
        if is_emoji:
            # 如果recognized_text包含“最后一张表情包是”，只保留后面的文本
            if "最后一张表情包是" in recognized_text:
                recognized_text = recognized_text.split("最后一张表情包是", 1)[1].strip()
            recognized_text = "发送了表情包：" + recognized_text
        else :
            recognized_text = "发送了图片：" + recognized_text
        logger.info(f"AI图片识别结果: {recognized_text}")
        # 恢复向Deepseek发送消息队列
        can_send_messages = True
        return recognized_text

    except Exception as e:
        logger.error(f"调用AI识别图片失败: {str(e)}")
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

def handle_wxauto_message(msg, who):
    """处理来自Wxauto的消息，包括可能的提醒功能。"""
    try:
        username = who
        content = getattr(msg, 'content', None) or getattr(msg, 'text', None)
        img_path = None
        is_emoji = False
        global can_send_messages

        if not content:
            logger.warning("收到的消息没有内容。")
            return

        on_user_message(username) # 重置自动消息计时器

        # --- 提醒检查 ---
        # 如果启用了提醒功能，在图片/表情处理之前检查关键字
        reminder_keywords = ["每日","每天","提醒","提醒我", "定时", "分钟后", "小时后", "计时", "闹钟", "通知我", "叫我", "提醒一下", "倒计时", "稍后提醒", "稍后通知", "提醒时间", "设置提醒", "喊我"]
        if ENABLE_REMINDERS and any(keyword in content for keyword in reminder_keywords):
            logger.info(f"检测到可能的提醒请求，用户 {username}: {content}")
            reminder_set = try_parse_and_set_reminder(content, username)
            if reminder_set:
                logger.info(f"成功为用户 {username} 设置提醒。")
                
                # 如果设置了提醒，可以选择是否继续作为普通聊天消息处理。
                # 目前选择仅确认提醒并停止进一步处理此消息。
                return # 停止进一步处理此消息

        # --- 图片/表情处理 ---
        if content.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
             if ENABLE_IMAGE_RECOGNITION:
                img_path = content
                is_emoji = False
                content = None # 稍后将被识别结果替换
             else:
                content = "[图片]" # 如果未启用识别功能，用占位符替换路径

        if content and "[动画表情]" in content:
            if ENABLE_EMOJI_RECOGNITION:
                img_path = capture_and_save_screenshot(username)
                is_emoji = True
                content = None # 稍后将被识别结果替换
            else:
                content = "[动画表情]" # 如果未启用识别功能，用占位符替换
                clean_up_temp_files() # 清理可能已拍摄但未使用的截图

        if img_path:
            # 尝试图片识别
            logger.info(f"处理图片/表情消息 - 用户 {username}: {img_path}")
            recognized_text = recognize_image_with_moonshot(img_path, is_emoji=is_emoji)
            content = recognized_text if recognized_text else ("[图片]" if not is_emoji else "[动画表情]") # 使用识别文本或回退占位符
            clean_up_temp_files() # 清理截图文件
            can_send_messages = True # 确保在识别过程中暂停后可以发送消息

        # --- 普通消息处理 ---
        # 仅在内容存在时继续处理（可能是原始文本、占位符或识别文本）
        if content:
            if ENABLE_MEMORY:
                # 将用户消息记录到记忆日志文件
                prompt_name = prompt_mapping.get(username, username)
                log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_{prompt_name}_log.txt')
                log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{username}] {content}\n"
                # 确保目录存在
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                # 写入日志条目
                try:
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(log_entry)
                except Exception as write_err:
                     logger.error(f"写入用户 {username} 的记忆日志失败: {write_err}")


            current_time_str = datetime.now().strftime("%Y-%m-%d %A %H:%M:%S")
            content_with_time = f"[{current_time_str}] {content}"
            logger.info(f"处理消息 - 用户 {username}: {content_with_time}")
            sender_name = username

            # 添加到用户消息队列
            with queue_lock:
                if username not in user_queues:
                    user_queues[username] = {
                        'messages': [content_with_time],
                        'sender_name': sender_name,
                        'username': username,
                        'last_message_time': time.time()
                    }
                    logger.info(f"已为用户 {sender_name} 初始化消息队列")
                else:
                    # 添加新消息，保持队列大小限制（例如，5条）
                    user_queues[username]['messages'].append(content_with_time)
                    MAX_QUEUE_LEN = 5 # 定义最大队列长度
                    if len(user_queues[username]['messages']) > MAX_QUEUE_LEN:
                         user_queues[username]['messages'].pop(0)
                    user_queues[username]['last_message_time'] = time.time()
                    logger.info(f"用户 {sender_name} 的消息已加入队列（当前 {len(user_queues[username]['messages'])} 条）并更新最后消息时间")
        else:
            # 如果启用了图片/表情识别但未能生成文本，且原始内容为None（因为是图片路径或"[动画表情]"），可能会发生这种情况
            logger.warning(f"在处理后未找到用户 {username} 的可处理内容。")

    except Exception as e:
        can_send_messages = True # 确保在发生错误时可以恢复发送消息
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

    reply = None
    online_info = None

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
        

def send_reply(user_id, sender_name, username, original_merged_message, reply):
    """发送回复消息，可能分段发送，并管理发送标志。"""
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

        # --- 表情包发送 ---
        if ENABLE_EMOJI_SENDING:
            emotion = is_emoji_request(reply)
            if emotion:
                logger.info(f"触发表情请求（概率{EMOJI_SENDING_PROBABILITY}%） 用户 {user_id}，情绪: {emotion}")
                emoji_path = send_emoji(emotion)
                if emoji_path:
                    try:
                        wx.SendFiles(filepath=emoji_path, who=user_id)
                        logger.info(f"已向 {user_id} 发送表情包 {emotion}")
                        time.sleep(random.uniform(0.5, 1.5))  # 表情发送后稍作延迟
                    except Exception as e:
                        logger.error(f"向 {user_id} 发送表情包失败: {str(e)}")

        # --- 文本消息发送 ---
        reply = remove_timestamps(reply)  # 移除时间戳（如果有）

        # 处理可能的分段消息（例如 '\\' 或者 '\n' 分隔符）
        parts = [p.strip() for p in re.split(r'\\|\n', reply) if p.strip()]
        if not parts:  # 如果分割后消息为空
            logger.warning(f"回复消息在分割/清理后为空，无法发送给 {user_id}。")
            is_sending_message = False  # 确保标志被重置，即使提前返回
            return

        if len(parts) > 1:  # 分段消息
            logger.info(f"向 {user_id} 发送分段消息（共 {len(parts)} 段）")
            for i, part in enumerate(parts):
                wx.SendMsg(msg=part, who=user_id)
                logger.info(f"分段回复 {i+1}/{len(parts)} 给 {sender_name}: {part}")
                # 如果启用记忆功能，记录到记忆日志
                if ENABLE_MEMORY:
                    log_ai_reply_to_memory(username, part)

                if i < len(parts) - 1:
                    # 根据下一段的长度计算延迟时间
                    next_part_len = len(parts[i+1])
                    base_delay = next_part_len * AVERAGE_TYPING_SPEED
                    random_delay = random.uniform(RANDOM_TYPING_SPEED_MIN, RANDOM_TYPING_SPEED_MAX)
                    total_delay = base_delay + random_delay
                    # 确保最小延迟（例如 1 秒），避免消息发送过快
                    final_delay = max(1.0, total_delay)
                    logger.debug(f"分段延迟时间 for {user_id}: {final_delay:.2f} 秒")
                    time.sleep(final_delay)
        else:  # 单条消息（未分段或只有一段）
            single_reply = parts[0]  # 获取单条消息内容
            wx.SendMsg(msg=single_reply, who=user_id)
            logger.info(f"回复 {sender_name}: {single_reply}")
            # 如果启用记忆功能，记录到记忆日志
            if ENABLE_MEMORY:
                log_ai_reply_to_memory(username, single_reply)

    except Exception as e:
        logger.error(f"向 {user_id} 发送回复失败: {str(e)}", exc_info=True)
    finally:
        is_sending_message = False  # <<< 在发送完成或出错后重置标志

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
        -(0[1-9]|1[0-2])  # 月份：01-12
        -(0[1-9]|[12]\d|3[01]) # 日期：01-31
        (?:\s[A-Za-z]+)?  # 可选的星期部分
        \s                # 日期与时间之间的空格
        (?:2[0-3]|[01]\d) # 小时：00-23
        :[0-5]\d          # 分钟：00-59
        (?::[0-5]\d)?     # 可选的秒数：00-59括号
    '''
    
    # 使用正则标志：
    # 1. re.VERBOSE 允许模式中的注释和空格
    # 2. re.MULTILINE 跨行匹配
    # 3. 替换时自动处理前后空格
    return re.sub(
        pattern = timestamp_pattern,
        repl = lambda m: ' ',  # 统一替换为单个空格
        string = text,
        flags = re.X | re.M
    ).strip()  # 最后统一清理首尾空格

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

        # 获取AI判断结果
        response = get_deepseek_response(prompt, "system",  store_context=False).strip()
        
        # 清洗响应内容
        response = re.sub(r"[^\w\u4e00-\u9fff]", "", response)  # 移除非文字字符
        logger.info(f"AI情绪识别结果: {response}")

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

def capture_and_save_screenshot(who):
    screenshot_folder = os.path.join(root_dir, 'screenshot')
    if not os.path.exists(screenshot_folder):
        os.makedirs(screenshot_folder)
    screenshot_path = os.path.join(screenshot_folder, f'{who}_{datetime.now().strftime("%Y%m%d%H%M%S")}.png')
    
    try:
        # 激活并定位微信聊天窗口
        wx_chat = WeChat()
        wx_chat.ChatWith(who)
        chat_window = pyautogui.getWindowsWithTitle(who)[0]
        
        # # 确保窗口被前置和激活
        # if not chat_window.isActive:
        #     chat_window.activate()
        # if not chat_window.isMaximized:
        #     chat_window.maximize()
        
        # 获取窗口的坐标和大小
        x, y, width, height = chat_window.left, chat_window.top, chat_window.width, chat_window.height

        time.sleep(wait)

        # 截取指定窗口区域的屏幕
        screenshot = pyautogui.screenshot(region=(x, y, width, height))
        screenshot.save(screenshot_path)
        logger.info(f'已保存截图: {screenshot_path}')
        return screenshot_path
    except Exception as e:
        logger.error(f'保存截图失败: {str(e)}')

def clean_up_temp_files ():
    # 检查是否存在该目录
    if os.path.isdir("screenshot"):
        try:
            shutil.rmtree("screenshot")
        except Exception as e:
            logger.error(f"删除目录 screenshot 失败: {str(e)}")
            return
        logger.info(f"目录 screenshot 已成功删除")
    else:
        logger.info(f"目录 screenshot 不存在，无需删除")

    if os.path.isdir("wxauto文件"):
        try:
            shutil.rmtree("wxauto文件")
        except Exception as e:
            logger.error(f"删除目录 wxauto文件 失败: {str(e)}")
            return
        logger.info(f"目录 wxauto文件 已成功删除")
    else:
        logger.info(f"目录 wxauto文件 不存在，无需删除")

def is_quiet_time():
    current_time = datetime.now().time()
    if quiet_time_start <= quiet_time_end:
        return quiet_time_start <= current_time <= quiet_time_end
    else:
        return current_time >= quiet_time_start or current_time <= quiet_time_end

# 记忆管理功能
def append_to_memory_section(user_id, content):
    """将内容追加到用户prompt文件的记忆部分"""
    try:
        prompts_dir = os.path.join(root_dir, 'prompts')
        user_file = os.path.join(prompts_dir, f'{user_id}.md')
        
        # 确保用户文件存在
        if not os.path.exists(user_file):
            raise FileNotFoundError(f"用户文件 {user_id}.md 不存在")

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

def summarize_and_save(user_id):
    """总结聊天记录并存储记忆"""
    log_file = None
    temp_file = None
    backup_file = None
    try:
        # --- 前置检查 ---
        prompt_name = prompt_mapping.get(user_id, user_id)  # 获取配置的prompt名
        log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{user_id}_{prompt_name}_log.txt')
        if not os.path.exists(log_file):
            logger.warning(f"日志文件不存在: {log_file}")
            return
        if os.path.getsize(log_file) == 0:
            logger.info(f"空日志文件: {log_file}")
            return

        # --- 读取日志 ---
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = [line.strip() for line in f if line.strip()]
            # 修改检查条件：仅检查是否达到最小处理阈值
            if len(logs) < MAX_MESSAGE_LOG_ENTRIES:
                logger.info(f"日志条目不足（{len(logs)}条），未触发记忆总结。")
                return

        # --- 生成总结 ---
        # 修改为使用全部日志内容
        full_logs = '\n'.join(logs)  # 变量名改为更明确的full_logs
        summary_prompt = f"请以{prompt_name}的视角，用中文总结与{user_id}的对话，提取重要信息总结为一段话作为记忆片段（直接回复一段话）：\n{full_logs}"
        summary = get_deepseek_response(summary_prompt, "system", store_context=False)
        # 添加清洗，匹配可能存在的**重要度**或**摘要**字段以及##记忆片段 [%Y-%m-%d %A %H:%M]或[%Y-%m-%d %H:%M]或[%Y-%m-%d %H:%M:%S]或[%Y-%m-%d %A %H:%M:%S]格式的时间戳
        summary = re.sub(
            r'\*{0,2}(重要度|摘要)\*{0,2}[\s:]*\d*[\.]?\d*[\s\\]*|## 记忆片段 \[\d{4}-\d{2}-\d{2}( [A-Za-z]+)? \d{2}:\d{2}(:\d{2})?\]',
            '',
            summary,
            flags=re.MULTILINE
        ).strip()

        # --- 评估重要性 ---
        importance_prompt = f"为以下记忆的重要性评分（1-5，直接回复数字）：\n{summary}"
        importance_response = get_deepseek_response(importance_prompt, "system", store_context=False)
        
        # 强化重要性提取逻辑
        importance_match = re.search(r'[1-5]', importance_response)
        if importance_match:
            importance = min(max(int(importance_match.group()), 1), 5)  # 确保1-5范围
        else:
            importance = 3  # 默认值
            logger.warning(f"无法解析重要性评分，使用默认值3。原始响应：{importance_response}")

        # --- 存储记忆 ---
        current_time = datetime.now().strftime("%Y-%m-%d %A %H:%M")
        
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
                    prompt_name = prompt_mapping.get(user, user)  # 获取配置的文件名，没有则用昵称
                    user_prompt_file = os.path.join(root_dir, 'prompts', f'{prompt_name}.md')
                    manage_memory_capacity(user_prompt_file)
                except Exception as e:
                    logger.error(f"内存管理失败: {str(e)}")

                if os.path.exists(log_file):
                    with open(log_file, 'r', encoding='utf-8') as f:
                        line_count = sum(1 for _ in f)
                        
                    if line_count >= MAX_MESSAGE_LOG_ENTRIES:
                        summarize_and_save(user)
    
        except Exception as e:
            logger.error(f"记忆管理异常: {str(e)}")
        finally:
            time.sleep(60)  # 每分钟检查一次

def manage_memory_capacity(user_file):
    """记忆淘汰机制"""
    # 允许重要度缺失（使用可选捕获组）
    MEMORY_SEGMENT_PATTERN = r'## 记忆片段 \[(.*?)\]\n(?:\*{2}重要度\*{2}: (\d*)\n)?\*{2}摘要\*{2}:(.*?)(?=\n## 记忆片段 |\Z)'
    try:
        with open(user_file, 'r', encoding='utf-8') as f:
            content = f.read()
        
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
        logger.info(f"成功清理记忆")

    except Exception as e:
        logger.error(f"记忆整理失败: {str(e)}")

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
            send_reply(user_id, user_id, user_id, f"[错误处理备用: {error_context_log}]", fallback_message)
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
        logger.info(f"向AI发送提醒解析请求（区分时长），用户: {user_id}，内容: '{message_content}'")
        # 使用特定的 user_id 前缀区分此 AI 调用
        response = get_deepseek_response(parsing_prompt, user_id="reminder_parser_classifier_v2_" + user_id, store_context=False) # 解析过程不存储上下文
        logger.debug(f"AI提醒解析原始响应 (分类器 v2): {response}")

        # --- 4. 解析 AI 的响应 ---
        if response is None or response.strip().lower() == 'null':
            logger.info(f"AI 未在用户 '{user_id}' 的消息中检测到有效的提醒请求 (分类器 v2): '{message_content}' (AI响应: {response})")
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
                     error_prompt = f"用户想设置短期提醒（原始请求 '{message_content}'），但我没理解好时间({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们换种方式说，比如‘5分钟后提醒我...’。"
                     fallback = "抱歉呀，我好像没太明白你的时间意思，设置短期提醒失败了。能麻烦你换种方式再说一遍吗？比如 ‘5分钟后提醒我...’。"
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

                confirmation_prompt = f"""用户刚才的请求是：“{message_content}”。
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
                    error_prompt = f"用户想设置一个较远时间的提醒（原始请求 '{message_content}'），但我没理解好目标时间 ({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们用明确的日期和时间再说，比如‘明天下午3点’或‘2024-06-15 10:00’。"
                    fallback = "抱歉呀，我好像没太明白你说的那个未来的时间点，设置提醒失败了。能麻烦你说得更清楚一点吗？比如 ‘明天下午3点’ 或者 ‘6月15号上午10点’ 这样。"
                    send_error_reply(user_id, error_prompt, fallback, f"One-off-long数据解析失败 ({type(val_e).__name__})")
                    return False

                logger.info(f"准备为用户 {user_id} 添加【长期一次性】提醒 (>10min)，目标时间: {target_datetime_str}，内容: '{reminder_msg}'")

                # 创建要存储的提醒信息字典 (包含类型)
                new_reminder = {
                    "reminder_type": "one-off", # 在存储时统一用 'one-off'
                    "user_id": user_id,
                    "target_datetime_str": target_datetime_str, # 存储目标时间
                    "content": reminder_msg
                }

                # 添加到内存列表并保存到文件
                with recurring_reminder_lock:
                    recurring_reminders.append(new_reminder)
                    save_recurring_reminders() # 保存更新后的列表

                logger.info(f"【长期一次性】提醒已添加并保存到文件。用户: {user_id}, 时间: {target_datetime_str}, 内容: '{reminder_msg}'")

                log_original_message_to_memory(user_id, message_content)

                # 发送确认消息
                confirmation_prompt = f"""用户刚才的请求是：“{message_content}”。
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
                    error_prompt = f"用户想设置每日提醒（原始请求 '{message_content}'），但我没理解好时间 ({type(val_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们用明确的‘每天几点几分’格式再说，比如‘每天早上8点’或‘每天22:30’。"
                    fallback = "抱歉呀，我好像没太明白你说的每日提醒时间，设置失败了。能麻烦你说清楚是‘每天几点几分’吗？比如 ‘每天早上8点’ 或者 ‘每天22:30’ 这样。"
                    send_error_reply(user_id, error_prompt, fallback, f"Recurring数据解析失败 ({type(val_e).__name__})")
                    return False

                logger.info(f"准备为用户 {user_id} 添加【每日重复】提醒，时间: {time_str}，内容: '{reminder_msg}'")

                # 创建要存储的提醒信息字典 (包含类型)
                new_reminder = {
                    "reminder_type": "recurring", # 明确类型
                    "user_id": user_id,
                    "time_str": time_str, # 存储 HH:MM
                    "content": reminder_msg
                }

                # 添加到内存列表并保存到文件
                with recurring_reminder_lock:
                    # 检查是否已存在完全相同的重复提醒
                    exists = any(
                        r.get('reminder_type') == 'recurring' and
                        r.get('user_id') == user_id and
                        r.get('time_str') == time_str and
                        r.get('content') == reminder_msg
                        for r in recurring_reminders
                    )
                    if not exists:
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
                confirmation_prompt = f"""用户刚才的请求是：“{message_content}”。
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
            error_prompt = f"用户想设置提醒（原始请求可能是 '{message_content}'），但我好像没完全理解时间或者内容，解析的时候出错了 ({type(json_e).__name__})。请用你的语气抱歉地告诉用户没听懂，并请他们换种方式说，比如‘30分钟后提醒我...’或‘每天下午3点叫我...’。"
            fallback = "抱歉呀，我好像没太明白你的意思，设置提醒失败了。能麻烦你换种方式再说一遍吗？比如 ‘30分钟后提醒我...’ 或者 ‘每天下午3点叫我...’ 这种。"
            send_error_reply(user_id, error_prompt, fallback, f"JSON解析失败 ({type(json_e).__name__})")
            return False

    except Exception as e:
        # 捕获此函数中其他所有未预料的错误
        logger.error(f"处理用户 {user_id} 的提醒请求 '{message_content}' 时发生未预料的错误 (分类器 v2): {str(e)}", exc_info=True)
        error_prompt = f"在处理用户设置提醒的请求（可能是 '{message_content}'）时，发生了一个我没预料到的内部错误（{type(e).__name__}）。请用你的语气向用户表达歉意，说明暂时无法完成设置，并建议他们稍后再试。"
        fallback = "哎呀，好像内部出了点小问题，暂时没法帮你设置提醒了，非常抱歉！要不稍等一下再试试看？"
        send_error_reply(user_id, error_prompt, fallback, f"通用处理错误 ({type(e).__name__})")
        return False

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
            # 构建日志文件路径
            log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{user_id}_{prompt_name}_log.txt')
            # 准备日志条目，记录原始用户消息
            log_entry = f"{datetime.now().strftime('%Y-%m-%d %A %H:%M:%S')} | [{user_id}] {message_content}\n"
            # 确保目录存在
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            # 以追加模式写入日志条目
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(log_entry)
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
             send_reply(user_id, user_id, user_id, f"{log_context} [备用确认]", fallback_message)
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
                if len(user_queues[user_id]['messages']) > 5:
                    user_queues[user_id]['messages'].pop(0)
                user_queues[user_id]['last_message_time'] = time.time()
        
        logger.info(f"已将提醒消息 '{reminder_message}' 添加到用户 {user_id} 的消息队列，用以执行联网检查流程")

        # 可选：如果仍需语音通话功能，保留这部分
        if USE_VOICE_CALL_FOR_REMINDERS:
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

        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(log_entry)
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
    logger.info("提醒检查线程（重复和长期一次性）已启动。")
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
                                        if len(user_queues[user_id]['messages']) > 5:
                                            user_queues[user_id]['messages'].pop(0)
                                        user_queues[user_id]['last_message_time'] = time.time()
                                
                                logger.info(f"已将{reminder_type}提醒 '{content}' 添加到用户 {user_id} 的消息队列，用以执行联网检查流程")

                                # 保留语音通话功能（如果启用）
                                if USE_VOICE_CALL_FOR_REMINDERS:
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
用户消息：“{message}”

如果需要联网搜索，请回答 "需要联网"，并在下一行提供你认为需要搜索的内容。
如果不需要联网搜索（例如：常规聊天、询问一般知识、历史信息、角色扮演对话等），请只回答 "不需要联网"。
请不要添加任何其他解释。
"""
    try:
        logger.info(f"向主 AI 发送联网检测请求，用户: {user_id}，消息: '{message[:50]}...'")
        # 调用主 AI 进行判断，不存储上下文
        response = get_deepseek_response(detection_prompt, user_id=f"online_detection_{user_id}", store_context=False)

        # 清理并判断响应
        cleaned_response = response.strip()
        if "</think>" in cleaned_response:
            cleaned_response = cleaned_response.split("</think>", 1)[1].strip()
        logger.info(f"联网检测 AI 响应: '{cleaned_response}'")

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

        reply = response.choices[0].message.content.strip()
        # 清理回复，去除思考过程
        if "</think>" in reply:
            reply = reply.split("</think>", 1)[1].strip()
        logger.info(f"在线 API 响应 (用户 {user_id}): {reply}")
        return reply

    except Exception as e:
        logger.error(f"调用在线 API 失败，用户: {user_id}: {e}", exc_info=True)
        return "抱歉，在线搜索功能暂时出错了。"

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
        wx = WeChat()
        for user_name in user_names:
            logger.info(f"添加监听用户: {user_name}")
            wx.AddListenChat(who=user_name, savepic=True, savevoice=True)

        # 初始化所有用户的自动消息计时器
        if ENABLE_AUTO_MESSAGE: # 仅在启用自动消息时才初始化
            logger.info("为所有监听用户初始化自动消息计时器...")
            for user in user_names:
                reset_user_timer(user) # 为每个用户设置初始时间和随机等待时间
                # 可以选择性地记录初始化的等待时间，方便调试
                if user in user_wait_times:
                     initial_wait_hours = user_wait_times[user] / 3600
                     logger.debug(f"用户 {user} 的初始自动消息等待时间设置为: {initial_wait_hours:.2f} 小时")
                else:
                     logger.warning(f"未能为用户 {user} 获取初始化后的等待时间。")
            logger.info("所有用户自动消息计时器初始化完成。")
        else:
            logger.info("自动消息功能已禁用，跳过计时器初始化。")
        # ==============================================================
        # ====================== 初始化代码结束 ======================
        # ==============================================================

        # --- 启动后台线程 ---
        logger.info("\033[32m启动后台线程...\033[0m")
        listener_thread = threading.Thread(target=message_listener, name="MessageListener")
        listener_thread.daemon = True
        listener_thread.start()
        logger.info("消息监听线程已启动。")

        checker_thread = threading.Thread(target=check_inactive_users, name="InactiveUserChecker")
        checker_thread.daemon = True
        checker_thread.start()
        logger.info("非活跃用户检查与消息处理线程已启动。")

        if ENABLE_MEMORY:
            memory_thread = threading.Thread(target=memory_manager, name="MemoryManager")
            memory_thread.daemon = True
            memory_thread.start()
            logger.info("记忆管理线程已启动。")
        else:
             logger.info("记忆功能已禁用。")

        # --- 修改: 线程名称更新 ---
        if ENABLE_REMINDERS:
            # 这个线程现在检查重复和长期一次性提醒
            reminder_checker_thread = threading.Thread(target=recurring_reminder_checker, name="ReminderChecker")
            reminder_checker_thread.daemon = True
            reminder_checker_thread.start()
            logger.info("提醒检查线程（重复和长期一次性）已启动。")

        # ... (其他线程启动，如自动消息) ...
        if ENABLE_AUTO_MESSAGE:
            auto_message_thread = threading.Thread(target=check_user_timeouts, name="AutoMessageChecker")
            auto_message_thread.daemon = True
            auto_message_thread.start()
            logger.info("主动消息检查线程已启动。")
        # 注意：上面已经根据 ENABLE_AUTO_MESSAGE 决定是否初始化计时器和启动线程了，这里无需重复判断
        # else:
        #      logger.info("主动消息功能已禁用。") # 这句日志在初始化部分已经有了


        logger.info("\033[32mBOT已成功启动并运行中...\033[0m")

        while True:
            time.sleep(60)

    except FileNotFoundError as e:
        logger.critical(f"初始化失败: 缺少必要的文件或目录 - {str(e)}")
        logger.error(f"\033[31m错误：{str(e)}\033[0m")
    except Exception as e:
        logger.critical(f"主程序发生严重错误: {str(e)}", exc_info=True)
    finally:
        # ... (原有的 finally 清理代码) ...
        logger.info("程序准备退出，执行清理操作...")

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

        # --- 注意：不需要在这里显式保存 recurring_reminders ---
        # --- 保存操作已在添加/删除时完成 ---

        if 'async_http_handler' in globals() and isinstance(async_http_handler, AsyncHTTPHandler):
            logger.info("正在关闭异步HTTP日志处理器...")
            try:
                 async_http_handler.close()
                 logger.info("异步HTTP日志处理器已关闭。")
            except Exception as log_close_err:
                 logger.error(f"关闭异步日志处理器时出错: {log_close_err}")

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