# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot.
# Safe message sending script for diagnostic tool.
# ***********************************************************************

"""
安全的消息发送脚本
通过命令行参数接收联系人和文件路径，避免代码注入风险
"""

import os
import sys
import time
import argparse
import json

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='安全的微信消息发送脚本')
    parser.add_argument('--contact', required=True, help='联系人名称')
    parser.add_argument('--file', required=True, help='要发送的文件路径')
    parser.add_argument('--config', default='send_config.json', help='配置文件路径')
    
    args = parser.parse_args()
    
    try:
        # 验证文件路径
        if not os.path.exists(args.file):
            print(f"ERROR: 文件不存在: {args.file}")
            sys.exit(1)
        
        # 检查文件路径是否包含危险字符
        dangerous_chars = ['|', '&', ';', '$', '`', '\n', '\r']
        for char in dangerous_chars:
            if char in args.file or char in args.contact:
                print(f"ERROR: 参数包含危险字符")
                sys.exit(1)
        
        # 导入微信模块
        try:
            from wxautox_wechatbot import WeChat
            try:
                from wxautox_wechatbot.param import WxParam
            except ImportError:
                try:
                    from wxautox_wechatbot import WxParam
                except ImportError:
                    WxParam = None
        except ImportError as e:
            print(f"ERROR: 无法导入wxautox_wechatbot模块: {e}")
            sys.exit(1)
        
        # 初始化
        os.environ["PROJECT_NAME"] = 'iwyxdxl/WeChatBot_WXAUTO_SE'
        if WxParam:
            WxParam.ENABLE_FILE_LOGGER = False
        
        wx = WeChat()
        
        # 确保微信窗口在前台
        try:
            import win32gui
            import win32con
            
            def find_wechat_window():
                """查找微信窗口"""
                def enum_windows_proc(hwnd, param):
                    if win32gui.IsWindowVisible(hwnd):
                        window_text = win32gui.GetWindowText(hwnd)
                        if "微信" in window_text or "WeChat" in window_text:
                            param.append(hwnd)
                    return True
                
                windows = []
                win32gui.EnumWindows(enum_windows_proc, windows)
                return windows[0] if windows else None
            
            wechat_hwnd = find_wechat_window()
            if wechat_hwnd:
                # 激活微信窗口
                win32gui.ShowWindow(wechat_hwnd, win32con.SW_RESTORE)
                win32gui.SetForegroundWindow(wechat_hwnd)
                time.sleep(1)
                print("INFO: 微信窗口已激活")
            else:
                print("WARNING: 未找到微信窗口，继续尝试发送")
        except ImportError:
            print("INFO: win32gui未安装，跳过窗口激活")
        except Exception as e:
            print(f"WARNING: 窗口激活失败: {e}，继续尝试发送")
        
        # 发送消息
        print(f"INFO: 打开与 {args.contact} 的聊天窗口")
        wx.ChatWith(args.contact)
        time.sleep(0.5)
        
        print(f"INFO: 发送文件 {args.file}")
        wx.SendFiles(args.file)
        
        print("SUCCESS: 测试表情发送成功")
        sys.exit(0)
        
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()

