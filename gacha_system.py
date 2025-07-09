# -*- coding: utf-8 -*-
import random
import json
import os
import re
import threading

# --- 常量定义 ---
# 配置文件路径
CARD_DATA_FILE = '卡面文档.txt'  # 卡面数据源文件
GACHA_STATE_FILE = 'gacha_state.json'  # 存储用户抽卡记录（保底计数）的文件

# 抽卡概率
BASE_5_STAR_PROB = 0.01  # 5星基础概率
BASE_4_STAR_PROB = 0.07  # 4星基础概率

# 5星保底规则
PITY_5_STAR_THRESHOLD = 60  # 5星软保底触发次数（第61次开始提升概率）
PITY_5_STAR_INCREASE = 0.10  # 每次概率提升值（10%）

# --- 全局变量 ---
# 卡池数据 (为了效率，只在程序启动时加载一次)
five_star_cards = []
four_star_cards = []
three_star_cards = []

# 文件读写锁，防止多线程同时操作状态文件导致数据错乱
state_lock = threading.Lock()

def _parse_card_line(line: str) -> list:
    """从单行文本中解析出所有卡面名称"""
    # 使用正则表达式寻找所有被单引号和方括号包裹的内容
    # 例如：'[✨雾海神临]' -> '✨雾海神临'
    matches = re.findall(r"'\[(.*?)\]'", line)
    return matches

def load_card_data():
    """
    从卡面文档.txt加载所有卡牌数据到全局变量中。
    """
    global five_star_cards, four_star_cards, three_star_cards
    
    if five_star_cards and four_star_cards and three_star_cards:
        return

    print("正在初始化卡池数据...")
    try:
        # 确保我们使用的是根目录下的文件
        root_dir = os.path.dirname(os.path.abspath(__file__))
        card_file_path = os.path.join(root_dir, CARD_DATA_FILE)

        with open(card_file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith("五星卡有:"):
                    five_star_cards = _parse_card_line(line)
                elif line.startswith("四星卡有:"):
                    four_star_cards = _parse_card_line(line)
                elif line.startswith("三星卡有:"):
                    three_star_cards = _parse_card_line(line)
        
        if not all([five_star_cards, four_star_cards, three_star_cards]):
            raise ValueError("卡面数据文件格式不正确或内容不全，请检查。")
            
        print(f"卡池数据加载成功！5星卡: {len(five_star_cards)}张, 4星卡: {len(four_star_cards)}张, 3星卡: {len(three_star_cards)}张。")

    except FileNotFoundError:
        print(f"\n\033[91m错误：找不到卡面数据文件 '{CARD_DATA_FILE}'！\033[0m")
        print(f"\033[93m请确保 '{CARD_DATA_FILE}' 和 'gacha_system.py' 在同一个目录下。\033[0m\n")
        exit()
    except Exception as e:
        print(f"加载卡池数据时发生错误: {e}")
        exit()

def _load_gacha_state() -> dict:
    """加载用户的保底状态"""
    with state_lock:
        if not os.path.exists(GACHA_STATE_FILE):
            return {}
        try:
            with open(GACHA_STATE_FILE, 'r', encoding='utf-8') as f:
                # 检查文件是否为空
                if os.path.getsize(GACHA_STATE_FILE) > 0:
                    return json.load(f)
                return {} # 文件为空
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

def _save_gacha_state(state: dict):
    """保存用户的保底状态"""
    with state_lock:
        with open(GACHA_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=4)

def perform_ten_pull(user_id: str) -> str:
    """
    为指定用户执行一次十连抽，并返回格式化的结果字符串。

    Args:
        user_id (str): 用户的唯一标识符，用于追踪保底。

    Returns:
        str: 格式化后的十连抽结果。
    """
    if not all([five_star_cards, four_star_cards, three_star_cards]):
        return "抽卡系统正在维护中，卡池数据未加载成功，请联系管理员。"

    gacha_states = _load_gacha_state()
    user_state = gacha_states.get(user_id, {"pity_5_star": 0})
    pity_5_star_counter = user_state.get("pity_5_star", 0)

    results = []
    has_high_rarity_in_pull = False

    for pull_index in range(10):
        if pity_5_star_counter < PITY_5_STAR_THRESHOLD:
            current_5_star_prob = BASE_5_STAR_PROB
        else:
            increase_times = pity_5_star_counter - (PITY_5_STAR_THRESHOLD - 1)
            current_5_star_prob = BASE_5_STAR_PROB + increase_times * PITY_5_STAR_INCREASE
        current_5_star_prob = min(current_5_star_prob, 1.0)

        rand_num = random.random()
        card_drawn = None

        if rand_num < current_5_star_prob:
            card_drawn = random.choice(five_star_cards)
            pity_5_star_counter = 0
            has_high_rarity_in_pull = True
        else:
            pity_5_star_counter += 1
            # 综合概率12%含保底，基础4星概率7%
            # 为了简化，我们直接使用7%作为非5星情况下的判断
            # P(4星 | 非5星) = P(4星) / P(非5星)
            if (1.0 - current_5_star_prob) > 0:
                 adjusted_4_star_prob = BASE_4_STAR_PROB / (1 - current_5_star_prob)
            else: # 如果5星概率为100%，则4星概率为0
                 adjusted_4_star_prob = 0
            
            if random.random() < adjusted_4_star_prob:
                card_drawn = random.choice(four_star_cards)
                has_high_rarity_in_pull = True
            else:
                card_drawn = random.choice(three_star_cards)
        
        results.append(card_drawn)

    if not has_high_rarity_in_pull:
        index_to_replace = random.randint(0, 9)
        # 简单保底给4星，不引入5星（因为5星有自己的保底机制）
        results[index_to_replace] = random.choice(four_star_cards)
    
    gacha_states[user_id] = {"pity_5_star": pity_5_star_counter}
    _save_gacha_state(gacha_states)

    # Format the result string
    result_lines = ["本次十连抽的结果为："]
    for card in results:
        result_lines.append(card)

    return "\n".join(result_lines)

load_card_data()

if __name__ == '__main__':
    print("\n--- 开始抽卡测试 ---")
    
    test_user = "test_player_001"
    
    states = _load_gacha_state()
    if test_user in states:
        del states[test_user]
        _save_gacha_state(states)
        print(f"已重置用户 '{test_user}' 的抽卡记录。")

    for i in range(1, 11): 
        print(f"\n--- 第 {i} 次十连 ---")
        result_text = perform_ten_pull(test_user)
        print(result_text)
        current_state = _load_gacha_state().get(test_user, {})
        print(f"当前5星保底计数: {current_state.get('pity_5_star', 0)}")
