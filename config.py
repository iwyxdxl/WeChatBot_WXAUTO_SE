# 用户列表(请配置要和bot说话的账号的昵称，不要写备注！)
# 例如：LISTEN_LIST = ['用户1','用户2']
LISTEN_LIST = ['用户1']
# DeepSeek API 配置
DEEPSEEK_API_KEY = 'sk-'
# 硅基流动API注册地址，免费15元额度 https://cloud.siliconflow.cn/
DEEPSEEK_BASE_URL = 'https://api.siliconflow.cn/v1/'
# 如果要使用官方的API
# DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
# 硅基流动API的模型
MODEL = 'Pro/deepseek-ai/DeepSeek-V3'
# 回复最大token
MAX_TOKEN = 2000
#温度
TEMPERATURE = 1.3
# Moonshot AI配置（用于图片和表情包识别）
# API申请https://platform.moonshot.cn/
MOONSHOT_API_KEY = 'sk-'
MOONSHOT_BASE_URL = "https://api.moonshot.cn/v1"
MOONSHOT_MODEL = "moonshot-v1-128k-vision-preview"
MOONSHOT_TEMPERATURE = 0.3
#表情包存放目录
EMOJI_DIR = 'emojis'
# 自动消息配置
AUTO_MESSAGE = "请你模拟系统设置的角色，在微信上找对方发消息想知道对方在做什么"
MIN_COUNTDOWN_HOURS = 0.2  # 最小倒计时时间（小时）
MAX_COUNTDOWN_HOURS = 0.5  # 最大倒计时时间（小时）
# 消息发送时间限制
QUIET_TIME_START = "22:00"  # 安静时间开始
QUIET_TIME_END = "8:00"    # 安静时间结束
