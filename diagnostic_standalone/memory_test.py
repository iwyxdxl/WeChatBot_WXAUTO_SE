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
记忆整理功能测试工具
独立测试记忆整理逻辑，包括读取、总结、重要性评估等步骤
只读取文件，不实际修改文件
"""

import os
import sys
import json
import re
from datetime import datetime
from openai import OpenAI

# 导入安全工具
try:
    from security_utils import SecurityValidator, sanitize_ai_prompt_input
except ImportError:
    SecurityValidator = None
    sanitize_ai_prompt_input = None

# 确保项目根目录在Python路径中
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

class MemoryTestTool:
    def __init__(self, log_func=None):
        self.config = None
        self.client = None
        self.log_func = log_func or print  # 默认使用print，也可以传入外部日志函数
        self.load_config()
        
    def load_config(self):
        """加载配置文件"""
        try:
            import config
            self.config = config
            self.log_func("✓ 配置文件加载成功")
            
            # 初始化AI客户端
            if hasattr(config, 'DEEPSEEK_API_KEY') and config.DEEPSEEK_API_KEY:
                self.client = OpenAI(
                    api_key=config.DEEPSEEK_API_KEY,
                    base_url=config.DEEPSEEK_BASE_URL
                )
                self.log_func("✓ AI客户端初始化成功")
            else:
                self.log_func("✗ AI API密钥未配置")
                
        except Exception as e:
            self.log_func(f"✗ 配置文件加载失败: {str(e)}")

    def test_memory_organization(self):
        """测试记忆整理功能"""
        self.log_func("=" * 60)
        self.log_func("开始记忆整理功能测试")
        self.log_func("=" * 60)
        
        # 步骤1: 检查记忆功能配置
        self.log_func("步骤1: 检查记忆功能配置")
        try:
            if not self.config or not hasattr(self.config, 'ENABLE_MEMORY'):
                self.log_func("✗ 记忆功能配置缺失")
                return False
            
            if not self.config.ENABLE_MEMORY:
                self.log_func("✓ 记忆功能已禁用（正常状态）")
                return True
            
            self.log_func("✓ 记忆功能已启用")
        except Exception as e:
            self.log_func(f"✗ 记忆功能配置检查失败: {str(e)}")
            return False
        
        # 步骤2: 检查记忆目录
        self.log_func("步骤2: 检查记忆目录")
        try:
            # 获取配置中的记忆目录，如果是相对路径则转换为从diagnostic_standalone目录的相对路径
            config_memory_dir = getattr(self.config, 'MEMORY_TEMP_DIR', 'Memory_Temp')
            
            # 使用安全的路径验证
            if SecurityValidator:
                try:
                    project_root_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                    memory_dir = SecurityValidator.validate_path(
                        config_memory_dir,
                        project_root_path
                    )
                except ValueError as e:
                    self.log_func(f"✗ 记忆目录路径验证失败: {str(e)}")
                    return False
            else:
                # 回退到原有逻辑
                if config_memory_dir.startswith('../'):
                    memory_dir = config_memory_dir
                else:
                    memory_dir = f'../{config_memory_dir}'
            
            if not os.path.exists(memory_dir):
                self.log_func(f"✗ 记忆目录不存在: {memory_dir}")
                return False
        except Exception as e:
            self.log_func(f"✗ 记忆目录检查失败: {str(e)}")
            return False
        
        # 步骤3: 检查prompts目录
        self.log_func("步骤3: 检查prompts目录")
        try:
            prompts_dir = '../prompts'
            if not os.path.exists(prompts_dir):
                self.log_func(f"✗ prompts目录不存在: {prompts_dir}")
                return False
            
            prompt_files = [f for f in os.listdir(prompts_dir) if f.endswith('.md')]
            if not prompt_files:
                self.log_func("✗ 未找到任何prompt文件")
                return False
        except Exception as e:
            self.log_func(f"✗ prompts目录检查失败: {str(e)}")
            return False
        
        # 步骤4: 查找记忆日志文件
        self.log_func("步骤4: 查找记忆日志文件")
        try:
            log_files = []
            for file in os.listdir(memory_dir):
                if file.endswith('_log.txt'):
                    log_files.append(file)
            
            if not log_files:
                self.log_func("✗ 未找到任何记忆日志文件")
                return False
            
            if not log_files:
                self.log_func("✗ 未找到任何记忆日志文件")
                return False
        except Exception as e:
            self.log_func(f"✗ 记忆日志文件查找失败: {str(e)}")
            return False
        
        # 步骤5: 读取并分析记忆日志内容（带大小限制）
        self.log_func("步骤5: 读取并分析记忆日志内容")
        try:
            # 选择第一个日志文件进行测试
            test_log_file = log_files[0]
            test_log_path = os.path.join(memory_dir, test_log_file)
            
            # 验证文件大小，防止资源耗尽
            MAX_LOG_SIZE = 10 * 1024 * 1024  # 10MB
            if SecurityValidator:
                try:
                    SecurityValidator.validate_file_size(test_log_path, MAX_LOG_SIZE)
                except ValueError as e:
                    self.log_func(f"✗ {str(e)}")
                    return False
            else:
                # 回退检查
                file_size = os.path.getsize(test_log_path)
                if file_size > MAX_LOG_SIZE:
                    self.log_func(f"✗ 日志文件过大: {file_size} bytes (最大允许: {MAX_LOG_SIZE} bytes)")
                    return False
            
            with open(test_log_path, 'r', encoding='utf-8') as f:
                logs = [line.strip() for line in f if line.strip()]
            
            if not logs:
                self.log_func(f"✗ 日志文件 {test_log_file} 为空")
                return False
            
        except Exception as e:
            self.log_func(f"✗ 记忆日志内容读取失败: {str(e)}")
            return False
        
        # 步骤6: 测试记忆总结功能（不实际修改文件）
        self.log_func("步骤6: 测试记忆总结功能")
        try:
            if not self.client:
                self.log_func("✗ AI客户端未初始化，无法测试总结功能")
                return False
            
            # 准备总结提示词
            user_name = test_log_file.split('_')[0]  # 从文件名提取用户名
            prompt_name = test_log_file.split('_')[1].replace('_log.txt', '')  # 提取prompt名
            
            # 使用所有日志内容进行测试（带AI提示注入防护）
            full_logs = '\n'.join(logs)
            
            # 清理输入，防止AI提示注入
            if sanitize_ai_prompt_input:
                full_logs = sanitize_ai_prompt_input(full_logs)
            
            summary_prompt = f"请以{prompt_name}的视角，用中文总结与{user_name}的对话，提取重要信息总结为一段话作为记忆片段（直接回复一段话）：\n{full_logs}"
            
            # 调用AI进行总结
            response = self.client.chat.completions.create(
                model=self.config.MODEL,
                messages=[{"role": "user", "content": summary_prompt}],
                temperature=self.config.TEMPERATURE,
                max_tokens=self.config.MAX_TOKEN
            )
            
            if response.choices and response.choices[0].message.content:
                summary = response.choices[0].message.content.strip()
                self.log_func("✓ 记忆总结生成成功")
                self.log_func(f"  - 总结内容: {summary[:100]}...")
                
                # 清洗总结内容
                cleaned_summary = re.sub(
                    r'\*{0,2}(重要度|摘要)\*{0,2}[\s:]*\d*[\.]?\d*[\s\\]*|## 记忆片段 \[\d{4}-\d{2}-\d{2}( [A-Za-z]+)? \d{2}:\d{2}(:\d{2})?\]',
                    '',
                    summary,
                    flags=re.MULTILINE
                ).strip()
                
                if cleaned_summary != summary:
                    self.log_func("  - 总结内容已清洗格式标记")
                
            else:
                error_message = "AI总结响应为空，请检查API连接或模型配置"
                self.log_func(f"✗ {error_message}")
                # 如果是从DiagnosticTool调用的，更新测试状态
                if hasattr(self, 'update_test_status') and callable(getattr(self, 'update_test_status', None)):
                    self.update_test_status('4','error', f'记忆整理测试失败: {error_message}')
                return False
                
        except Exception as e:
            error_info = str(e)
            self.log_func(f"✗ 记忆总结功能测试失败: {str(e)}")
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
                error_message = "Prompt或消息中含有敏感词，点击>>见文档1：常见问题，清理记忆"
                self.add_log(f"\033[31m错误：{error_message}\033[0m", "error")
            else:
                error_message = f"未知错误: {error_info}"
                self.add_log(f"\033[31m{error_message}\033[0m", "error")
            return False
        
        # 步骤7: 测试重要性评估功能（带AI提示注入防护）
        self.log_func("步骤7: 测试重要性评估功能")
        try:
            # 清理总结内容，防止AI提示注入
            safe_summary = cleaned_summary
            if sanitize_ai_prompt_input:
                safe_summary = sanitize_ai_prompt_input(cleaned_summary)
            
            importance_prompt = f"为以下记忆的重要性评分（1-5，直接回复数字）：\n{safe_summary}"
            
            response = self.client.chat.completions.create(
                model=self.config.MODEL,
                messages=[{"role": "user", "content": importance_prompt}],
                temperature=self.config.TEMPERATURE,
                max_tokens=100
            )
            
            if response.choices and response.choices[0].message.content:
                importance_response = response.choices[0].message.content.strip()
                
                # 提取重要性评分
                importance_match = re.search(r'[1-5]', importance_response)
                if importance_match:
                    importance = int(importance_match.group())
                    importance = min(max(importance, 1), 5)  # 确保1-5范围
                    self.log_func("✓ 重要性评估成功")
                    self.log_func(f"  - 评分: {importance}/5")
                    self.log_func(f"  - AI响应: {importance_response}")
                else:
                    self.log_func(f"✗ 无法解析重要性评分，AI响应: {importance_response}")
                    importance = 3  # 使用默认值
                    self.log_func(f"  - 使用默认评分: {importance}/5")
                
            else:
                self.log_func("✗ 重要性评估响应为空")
                return False
                
        except Exception as e:
            self.log_func(f"✗ 重要性评估功能测试失败: {str(e)}")
            return False
        
        # 步骤8: 模拟记忆片段格式化
        self.log_func("步骤8: 模拟记忆片段格式化")
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %A %H:%M")
            memory_entry = f"""## 记忆片段 [{current_time}]
**重要度**: {importance}
**摘要**: {cleaned_summary}

"""
            self.log_func("✓ 记忆片段格式化成功")
            self.log_func("  - 格式化后的记忆片段:")
            self.log_func("  " + "-"*50)
            for line in memory_entry.strip().split('\n'):
                self.log_func(f"  {line}")
            self.log_func("  " + "-"*50)
            
        except Exception as e:
            self.log_func(f"✗ 记忆片段格式化失败: {str(e)}")
            return False
        
        # 步骤9: 检查记忆容量管理配置
        self.log_func("步骤9: 检查记忆容量管理配置")
        try:
            max_memory = getattr(self.config, 'MAX_MEMORY_NUMBER', 20)
            self.log_func(f"✓ 记忆容量管理配置正常")
            self.log_func(f"  - 最大记忆数量: {max_memory}")
            
            # 检查对应的prompt文件是否存在记忆片段
            prompt_file_path = os.path.join('../prompts', f'{prompt_name}.md')
            self.log_func(f"  - 检查prompt文件: {prompt_file_path}")
            if os.path.exists(prompt_file_path):
                with open(prompt_file_path, 'r', encoding='utf-8') as f:
                    prompt_content = f.read()
                
                # 统计现有记忆片段数量
                memory_segments = re.findall(r'## 记忆片段 \[.*?\]', prompt_content)
                current_memory_count = len(memory_segments)
                
                self.log_func(f"  - 当前记忆片段数量: {current_memory_count}")
                
                if current_memory_count >= max_memory:
                    self.log_func(f"  - 记忆已达到容量上限，需要淘汰旧记忆")
                else:
                    self.log_func(f"  - 记忆容量充足，可以添加新记忆")
            else:
                self.log_func(f"  - 对应prompt文件不存在: {prompt_file_path}")
                
        except Exception as e:
            self.log_func(f"✗ 记忆容量管理检查失败: {str(e)}")
            return False
        
        self.log_func("=" * 60)
        self.log_func("记忆整理功能测试完成 - 所有步骤通过")
        self.log_func("=" * 60)
        self.log_func("测试总结:")
        self.log_func("✓ 记忆功能配置正常")
        self.log_func("✓ 目录结构完整")
        self.log_func("✓ 日志文件读取成功")
        self.log_func("✓ AI总结功能正常")
        self.log_func("✓ 重要性评估功能正常")
        self.log_func("✓ 记忆格式化功能正常")
        self.log_func("✓ 容量管理配置正常")
        self.log_func("注意: 本测试只读取文件，未实际修改任何文件")
        
        return True

def main():
    """主函数"""
    print("记忆整理功能测试工具")
    print("作用: 测试记忆整理的完整流程，包括读取、总结、评估等")
    print("特点: 只读取文件，不实际修改文件")
    
    tool = MemoryTestTool()
    
    if not tool.config:
        print("\n✗ 无法加载配置文件，测试终止")
        return
    
    if not tool.client:
        print("\n✗ AI客户端初始化失败，测试终止")
        return
    
    # 执行记忆整理测试
    success = tool.test_memory_organization()
    
    if success:
        print("\n🎉 记忆整理功能测试全部通过!")
    else:
        print("\n❌ 记忆整理功能测试存在问题，请检查上述错误信息")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n用户中断测试")
    except Exception as e:
        print(f"\n\n测试过程中发生未预期的错误: {str(e)}")
        import traceback
        traceback.print_exc()