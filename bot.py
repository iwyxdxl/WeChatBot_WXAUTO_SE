# -*- coding: utf-8 -*-

# ***********************************************************************
# Modified based on the My-Dream-Moments project
# Copyright of the original project: Copyright (C) 2025, umaru
# Copyright of this modification: Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# ***********************************************************************

import base64
import requests
import logging
from datetime import datetime, time as dt_time
import threading
import time
import os
from wxauto import WeChat
from openai import OpenAI
import random
from typing import Optional
import pyautogui
import shutil
import re  
from config import (
    DEEPSEEK_API_KEY, MAX_TOKEN, TEMPERATURE, MODEL, DEEPSEEK_BASE_URL, LISTEN_LIST, 
    MOONSHOT_API_KEY, MOONSHOT_BASE_URL, MOONSHOT_TEMPERATURE, EMOJI_DIR,
    AUTO_MESSAGE, MIN_COUNTDOWN_HOURS, MAX_COUNTDOWN_HOURS, MOONSHOT_MODEL,
    QUIET_TIME_START, QUIET_TIME_END, QUEUE_WAITING_TIME, 
    AVERAGE_TYPING_SPEED, RANDOM_TYPING_SPEED_MIN, RANDOM_TYPING_SPEED_MAX,
    ENABLE_IMAGE_RECOGNITION, ENABLE_EMOJI_RECOGNITION, 
    ENABLE_EMOJI_SENDING, ENABLE_AUTO_MESSAGE, ENABLE_MEMORY, 
    MEMORY_TEMP_DIR, MAX_MESSAGE_LOG_ENTRIES, MAX_MEMORY_NUMBER,
    Accept_All_Group_Chat_Messages
    )

# 生成用户昵称列表和prompt映射字典
user_names = [entry[0] for entry in LISTEN_LIST]
prompt_mapping = {entry[0]: entry[1] for entry in LISTEN_LIST}

# 获取微信窗口对象
wx = WeChat()
ROBOT_WX_NAME = wx.A_MyIcon.Name
# 设置监听列表
for user_name in user_names:
    wx.AddListenChat(who=user_name, savepic=True)
# 持续监听消息，并且收到消息后回复
wait = 1  # 设置1秒查看一次是否有新消息

# 初始化OpenAI客户端
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL
)

# 获取程序根目录
root_dir = os.path.dirname(os.path.abspath(__file__))

# 用户消息队列和聊天上下文管理
user_queues = {}  # {user_id: {'messages': [], 'last_message_time': 时间戳, ...}}
queue_lock = threading.Lock()  # 队列访问锁
chat_contexts = {}  # {user_id: [{'role': 'user', 'content': '...'}, ...]}

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 存储用户的计时器和随机等待时间
user_timers = {}
user_wait_times = {}
emoji_timer = None
emoji_timer_lock = threading.Lock()
# 全局变量，控制消息发送状态
can_send_messages = True
is_sending_message = False

def parse_time(time_str):
    try:
        TimeResult = datetime.strptime(time_str, "%H:%M").time()
        return TimeResult
    except Exception as e:
        logger.error("主动消息安静时间设置有误！请填00:00-23:59 不要填24:00,并请注意中间的符号为英文冒号！")
        print("\033[31m错误：主动消息安静时间设置有误！请填00:00-23:59 不要填24:00,并请注意中间的符号为英文冒号！\033[0m")

quiet_time_start = parse_time(QUIET_TIME_START)
quiet_time_end = parse_time(QUIET_TIME_END)

def check_user_timeouts():
    if ENABLE_AUTO_MESSAGE:
        while True:
            current_time = time.time()
            for user in user_names:
                last_active = user_timers.get(user)
                wait_time = user_wait_times.get(user)
                if last_active and wait_time:
                    if current_time - last_active >= wait_time:
                        if not is_quiet_time():
                            reply = get_deepseek_response(AUTO_MESSAGE, user)
                            send_reply(user, user, user, AUTO_MESSAGE, reply)
                        # 重置计时器和等待时间
                        reset_user_timer(user)
            time.sleep(10)  # 每10秒检查一次

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
        return file.read()


def get_deepseek_response(message, user_id):
    try:
        logger.info(f"调用 DeepSeek API - 用户ID: {user_id}, 消息: {message}")
        user_prompt = get_user_prompt(user_id)
        with queue_lock:
            if user_id not in chat_contexts:
                chat_contexts[user_id] = []
            chat_contexts[user_id].append({"role": "user", "content": message})

        MAX_GROUPS = 5
        while len(chat_contexts[user_id]) > MAX_GROUPS * 2:
            chat_contexts[user_id].pop(0)

        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": user_prompt},
                *chat_contexts[user_id][-MAX_GROUPS * 2:]
            ],
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKEN,
            stream=False
        )

        if not response.choices:
            logger.error("API返回空choices")
            return "服务响应异常，请稍后再试"

        reply = response.choices[0].message.content.strip()
        with queue_lock:
            chat_contexts[user_id].append({"role": "assistant", "content": reply})

        logger.info(f"API回复: {reply}")

        return reply
    except Exception as e:
        ErrorImformation = str(e)
        logger.error(f"DeepSeek调用失败: {str(e)}", exc_info=True)
        if "real name verification" in ErrorImformation :
            print("\033[31m错误：请在硅基流动网站实名后再使用！ \033[0m")
        elif "rate" in ErrorImformation :
            print("\033[31m错误：硅基流动速率限制，请稍后再试！ \033[0m")
        elif "paid requires" in ErrorImformation :
            print("\033[31m错误：您正在使用付费模型，请先充值再使用或使用免费额度模型！ \033[0m")
        elif "Api key is invalid" in ErrorImformation :
            print("\033[31m错误：API KEY不可用，请检查配置选项！ \033[0m")
        else :
            print("\033[31m错误：硅基流动服务器繁忙，请稍后再试！ \033[0m")
        return "抱歉，我现在有点忙，稍后再聊吧。"

def message_listener():
    while True:
        try:
            if wx is None:
                wx = WeChat()
                for user_name in user_names:
                    wx.AddListenChat(who=user_name, savepic=True)
                if not wx.GetSessionList():
                    time.sleep(5)
                    

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
                    elif Accept_All_Group_Chat_Messages:
                        if not msg.content.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                            msg.content = "群聊消息[" + msg.sender + "]:" + msg.content
                        if '[动画表情]' in content and ENABLE_EMOJI_RECOGNITION:
                            handle_emoji_message(msg, who)
                        else:
                            handle_wxauto_message(msg, who)
                    elif ROBOT_WX_NAME != '' and (bool(re.search(f'@{ROBOT_WX_NAME}\u2005', msg.content))):  
                        # 处理群聊信息，只有@当前机器人才会处理
                        msg.content = re.sub(f'@{ROBOT_WX_NAME}\u2005', '', content).strip()
                        msg.content = "群聊消息[" + msg.sender + "]:" + msg.content
                        handle_wxauto_message(msg, who)
                    else:
                        logger.debug(f"非需要处理消息: {content}")   
                        
        except Exception as e:
            logger.error(f"消息监听出错: {str(e)}")
            print("\033[31m重要提示：请不要关闭程序打开的微信聊天框！若关闭之后收不到消息，请重启程序！ \033[0m")
            wx = None
        time.sleep(wait)

def recognize_image_with_moonshot(image_path, is_emoji=False):
    # 先暂停向DeepSeek API发送消息队列
    global can_send_messages
    can_send_messages = False

    """使用Moonshot AI识别图片内容并返回文本"""
    with open(image_path, 'rb') as img_file:
        image_content = base64.b64encode(img_file.read()).decode('utf-8')
    headers = {
        'Authorization': f'Bearer {MOONSHOT_API_KEY}',
        'Content-Type': 'application/json'
    }
    text_prompt = "请描述这个图片" if not is_emoji else "请描述这个聊天窗口的最后一张表情包"
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
        logger.info(f"Moonshot AI图片识别结果: {recognized_text}")
        # 恢复向Deepseek发送消息队列
        can_send_messages = True
        return recognized_text

    except Exception as e:
        logger.error(f"调用Moonshot AI识别图片失败: {str(e)}")
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
    try:
        username = who  # 获取发送者
        content = getattr(msg, 'content', None) or getattr(msg, 'text', None)  # 获取消息内容
        img_path = None  # 初始化图片路径
        is_emoji = False  # 初始化是否为动画表情标志
        global can_send_messages

        # 重置定时器
        on_user_message(username)

        # 检查是否是图片消息
        if content and content.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
            if ENABLE_IMAGE_RECOGNITION:
                img_path = content  # 如果消息内容是图片路径，则赋值给img_path
                is_emoji = False
                content = None  # 将内容置为空，因为我们只处理图片
            else:
                content = "[图片]"

        # 检查是否是"[动画表情]"
        if content and "[动画表情]" in content:
            if ENABLE_EMOJI_RECOGNITION:
                # 对聊天对象的窗口进行截图，并保存到指定目录           
                img_path = capture_and_save_screenshot(username)
                is_emoji = True  # 设置为动画表情
                content = None  # 将内容置为空，不再处理该消息
            else:
                content = "[动画表情]"
                clean_up_temp_files()

        if img_path:
            logger.info(f"处理图片消息 - {username}: {img_path}")
            recognized_text = recognize_image_with_moonshot(img_path, is_emoji=is_emoji)
            content = recognized_text if content is None else f"{content} {recognized_text}"
            # 清理临时文件
            clean_up_temp_files()
            can_send_messages = True

        if content:
                
            if ENABLE_MEMORY:
                # 记录到用户专属日志文件（添加[User]标记）
                log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log.txt')
                log_entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | [User] {content}\n" 
                
                # 检查文件大小并轮转
                if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:  # 1MB
                    archive_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log_archive_{int(time.time())}.txt')
                    shutil.move(log_file, archive_file)
                    
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_entry)
                
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            content = f"[{current_time}] {content}"
            logger.info(f"处理消息 - {username}: {content}")
            sender_name = username  # 使用昵称作为发送者名称    

            with queue_lock:
                if username not in user_queues:
                    # 初始化用户的消息队列
                    user_queues[username] = {
                        'messages': [content],  # 初始化消息列表
                        'sender_name': sender_name,
                        'username': username,
                        'last_message_time': time.time()  # 设置最后消息时间
                    }
                    logger.info(f"已为 {sender_name} 初始化消息队列")
                else:
                    # 添加新消息到消息列表
                    if len(user_queues[username]['messages']) >= 5:
                        # 如果消息数量超过5条，移除最早的消息
                        user_queues[username]['messages'].pop(0)
                    user_queues[username]['messages'].append(content)
                    user_queues[username]['last_message_time'] = time.time()  # 更新最后消息时间

                    logger.info(f"{sender_name} 的消息已加入队列并更新最后消息时间")
        else:
            logger.warning("无法获取消息内容")
    except Exception as e:
        logger.error(f"消息处理失败: {str(e)}")

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
    # 是否可以向Deepseek发消息队列
    global can_send_messages

    with queue_lock:
        if user_id not in user_queues:
            return
        user_data = user_queues.pop(user_id)  # 从用户队列中移除用户数据
        messages = user_data['messages']      # 获取消息列表
        sender_name = user_data['sender_name']
        username = user_data['username']

    # 合并消息为一句
    merged_message = ' '.join(messages)  # 使用空格或其他分隔符合并消息
    logger.info(f"处理合并消息 ({sender_name}): {merged_message}")

    # 获取 API 回复
    reply = get_deepseek_response(merged_message, user_id)

    # 如果使用Deepseek R1，则只保留思考结果
    if "</think>" in reply:
        reply = reply.split("</think>", 1)[1].strip()
    
    # 发送回复，屏蔽记忆片段发送
    if "## 记忆片段" not in reply:
        send_reply(user_id, sender_name, username, merged_message, reply)

def send_reply(user_id, sender_name, username, merged_message, reply):
    global is_sending_message
    try:
        # 发送分段消息过程中停止向deepseek发送新请求
        is_sending_message = True
        # 首先检查是否需要发送表情包
        if ENABLE_EMOJI_SENDING:
            emotion = is_emoji_request(reply)
            if emotion:
                emoji_path = send_emoji(emotion)
                if emoji_path:
                    try:
                        # 先发送表情包
                        wx.SendFiles(filepath=emoji_path, who=user_id)
                    except Exception as e:
                        logger.error(f"发送表情包失败: {str(e)}")

        if '\\' in reply:
            parts = [p.strip() for p in reply.split('\\') if p.strip()]
            for i, part in enumerate(parts):
                wx.SendMsg(part, user_id)
                logger.info(f"分段回复 {sender_name}: {part}")

                if ENABLE_MEMORY:
                    # 记录到用户专属日志文件（添加[AI]标记）
                    log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log.txt')
                    log_entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | [AI] {part}\n"  
                    
                    # 检查文件大小并轮转
                    if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:  # 1MB
                        archive_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log_archive_{int(time.time())}.txt')
                        shutil.move(log_file, archive_file)
                        
                    with open(log_file, 'a', encoding='utf-8') as f:
                        f.write(log_entry)
            
                if i < len(parts) - 1:
                    next_part = parts[i + 1]
                    # 计算延时时间，模拟打字速度
                    delay = len(next_part) * (AVERAGE_TYPING_SPEED + random.uniform(RANDOM_TYPING_SPEED_MIN, RANDOM_TYPING_SPEED_MAX))
                    if delay < 2:
                        delay = 2
                    time.sleep(delay)
        else:
            wx.SendMsg(reply, user_id)
            logger.info(f"回复 {sender_name}: {reply}")

            if ENABLE_MEMORY:
                # 记录到用户专属日志文件（添加[AI]标记）
                log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log.txt')
                log_entry = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | [AI] {reply}\n"  
                
                # 检查文件大小并轮转
                if os.path.exists(log_file) and os.path.getsize(log_file) > 1 * 1024 * 1024:  # 1MB
                    archive_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{username}_log_archive_{int(time.time())}.txt')
                    shutil.move(log_file, archive_file)
                    
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(log_entry)

        # 解除发送限制
        is_sending_message = False

    except Exception as e:
        logger.error(f"发送回复失败: {str(e)}")
        # 解除发送限制
        is_sending_message = False

def is_emoji_request(text: str) -> bool:
    # 定义情绪关键词字典
    emotion_keywords =  {
        'angry': ['生气', '愤怒', '怒', '哼', '鄙视', '厌恶', '烦恼', '厌倦', '啊啊'],
        'happy': ['开心', '高兴', '笑', '喜', '哈哈', '嘻嘻', '嘿嘿', '愉快', '兴奋', '满足', '乐', '激动', '惊喜'],
        'sad': ['难过', '伤心', '委屈', '哭', '悲', '泪', '呜呜', '心酸', '愧疚', '懊悔', '失落', '孤独', '寂寞', '恐惧', '害怕', '忧虑'],
        'neutral': ['惊讶', '紧张', '放松', '失望', '羞愧', '惊恐', '惊魂未定', '震惊', '疑惑', '困惑', '怀疑', '安慰', '安宁', '放心']
    }

    # 将文本转换为小写（如果需要）
    text = text.lower()
    
    # 初始化情绪计数器
    emotion_score = {'angry': 0, 'happy': 0, 'neutral': 0, 'sad': 0}
    
    # 遍历情绪关键词，统计匹配次数
    for emotion, keywords in emotion_keywords.items():
        for keyword in keywords:
            if keyword in text:
                emotion_score[emotion] += 1
                
    # 找出最高分的情绪
    detected_emotion = max(emotion_score, key=emotion_score.get)
    
    # 如果所有情绪分数都为0，默认设为'neutral'
    if emotion_score[detected_emotion] == 0:
        detected_emotion = 'False'
    
    return detected_emotion
    
def send_emoji(emotion):
    # 构建对应情绪的表情包路径
    emoji_folder = os.path.join(EMOJI_DIR, emotion)
    
    # 获取文件夹中的所有文件名
    try:
        emoji_files = os.listdir(emoji_folder)
    except FileNotFoundError:
        print(f"表情包文件夹 {emoji_folder} 不存在。")
        return
    
    # 如果文件夹为空
    if not emoji_files:
        print(f"表情包文件夹 {emoji_folder} 中没有表情包。")
        return
    
    # 随机选择一个表情包
    selected_emoji = random.choice(emoji_files)
    emoji_path = os.path.join(emoji_folder, selected_emoji)
    
    # 发送表情包，这里以打印路径代替实际发送
    print(f"发送表情包：{emoji_path}")
    return emoji_path


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
        
        # 确保窗口被前置和激活
        if not chat_window.isActive:
            chat_window.activate()
        if not chat_window.isMaximized:
            chat_window.maximize()
        
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
        shutil.rmtree("screenshot")
        print(f"目录 screenshot 已成功删除")
    else:
        print(f"目录 screenshot 不存在，无需删除")

    if os.path.isdir("wxauto文件"):
        shutil.rmtree("wxauto文件")
        print(f"目录 wxauto文件 已成功删除")
    else:
        print(f"目录 wxauto文件 不存在，无需删除")

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
            if insert_index == -1:
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
        log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{user_id}_log.txt')
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
                logger.info(f"日志条目不足（{len(logs)}条），无需处理")
                return

        # --- 生成总结 ---
        # 修改为使用全部日志内容
        full_logs = '\n'.join(logs)  # 变量名改为更明确的full_logs
        summary_prompt = f"请用中文总结以下对话，提取重要信息形成记忆片段：\n{full_logs}"
        summary = get_deepseek_response(summary_prompt, user_id)
        
        # --- 评估重要性 ---
        importance_prompt = f"为以下内容的重要性评分（1-5，直接回复数字）：\n{summary}"
        importance_response = get_deepseek_response(importance_prompt, user_id)
        
        # 强化重要性提取逻辑
        importance_match = re.search(r'[1-5]', importance_response)
        if importance_match:
            importance = min(max(int(importance_match.group()), 1), 5)  # 确保1-5范围
        else:
            importance = 3  # 默认值
            logger.warning(f"无法解析重要性评分，使用默认值3。原始响应：{importance_response}")

        # --- 存储记忆 ---
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M")
        memory_entry = f"""## 记忆片段 [{current_time}]
**重要度**: {importance}
**摘要**: {summary}
"""

        prompt_name = prompt_mapping.get(user_id, user_id)
        prompts_dir = os.path.join(root_dir, 'prompts')
        os.makedirs(prompts_dir, exist_ok=True)  # 确保目录存在
        
        user_prompt_file = os.path.join(prompts_dir, f'{prompt_name}.md')
        temp_file = f"{user_prompt_file}.tmp"
        backup_file = f"{user_prompt_file}.bak"

        # 原子写入流程
        try:
            # 步骤1：创建新文件
            with open(temp_file, 'w', encoding='utf-8') as f:
                # 保留原有内容
                if os.path.exists(user_prompt_file):
                    with open(user_prompt_file, 'r', encoding='utf-8') as src:
                        f.write(src.read())
                # 追加新内容
                f.write(f"\n## 记忆片段 [{datetime.now().strftime('%Y-%m-%d %H:%M')}]\n")
                f.write(f"**摘要**: {summary}\n\n")

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
                log_file = os.path.join(root_dir, MEMORY_TEMP_DIR, f'{user}_log.txt')
                
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
    """内存淘汰机制"""
    MEMORY_SEGMENT_PATTERN = r'## 记忆片段 \[(.*?)\]\n\*{2}重要度\*{2}: (\d+)\n\*{2}摘要\*{2}:(.*?)(?=\n## 记忆片段 |\Z)'
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
                time_diff = (now - datetime.strptime(timestamp, "%Y-%m-%d %H:%M")).total_seconds()
            except ValueError:
                time_diff = 0
            score = 0.6 * int(importance) - 0.4 * (time_diff / 3600)
            memory_scores.append(score)

        # 获取保留索引
        sorted_indices = sorted(range(len(memory_scores)),
                              key=lambda k: (-memory_scores[k], segments[k][0]))
        keep_indices = set(sorted_indices[:MAX_MEMORY_NUMBER])

        # 重建内容
        memory_blocks = re.split(r'(?=## 记忆片段 \[)', content)
        new_content = []
        
        for idx, block in enumerate(memory_blocks):
            if idx == 0:  # 文件头
                new_content.append(block)
                continue
            if (idx-1) in keep_indices:  # 对齐索引
                new_content.append(block)

        # 原子写入
        with open(f"{user_file}.tmp", 'w', encoding='utf-8') as f:
            f.write(''.join(new_content).strip())
        
        shutil.move(f"{user_file}.tmp", user_file)
        logger.info(f"成功清理记忆")

    except Exception as e:
        logger.error(f"记忆整理失败: {str(e)}")

def main():
    
    try:
        # 预检查所有用户prompt文件
        for user in user_names:
            prompt_file = prompt_mapping.get(user, user)
            prompt_path = os.path.join(root_dir, 'prompts', f'{prompt_file}.md')
            if not os.path.exists(prompt_path):
                raise FileNotFoundError(f"用户 {user} 的prompt文件 {prompt_file}.md 不存在")
            
        # 确保临时目录存在
        memory_temp_dir = os.path.join(root_dir, MEMORY_TEMP_DIR)
        os.makedirs(memory_temp_dir, exist_ok=True)

        clean_up_temp_files()

        global wx
        wx = WeChat()

        listener_thread = threading.Thread(target=message_listener)
        listener_thread.daemon = True
        listener_thread.start()

        checker_thread = threading.Thread(target=check_inactive_users)
        checker_thread.daemon = True
        checker_thread.start()

        if ENABLE_MEMORY:
            # 启动记忆管理线程
            memory_thread = threading.Thread(target=memory_manager)
            memory_thread.daemon = True
            memory_thread.start()
        
        # 启动后台线程来检查用户超时
        if ENABLE_AUTO_MESSAGE:
            threading.Thread(target=check_user_timeouts, daemon=True).start()

        logger.info("开始运行BOT...")

        while True:
            time.sleep(wait)
    except Exception as e:
        logger.error(f"发生异常: {str(e)}")
    except FileNotFoundError as e:
        logger.error(f"初始化失败: {str(e)}")
        print(f"\033[31m错误：{str(e)}\033[0m")
        exit(1)
    finally:
        logger.info("程序退出")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        logger.info("用户终止程序")
    except Exception as e:
        logger.error(f"发生异常: {str(e)}", exc_info=True)