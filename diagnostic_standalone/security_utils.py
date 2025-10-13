# -*- coding: utf-8 -*-

# ***********************************************************************
# Copyright (C) 2025, iwyxdxl
# Licensed under GNU GPL-3.0 or higher, see the LICENSE file for details.
# 
# This file is part of WeChatBot.
# Security utilities module for diagnostic tool.
# ***********************************************************************

"""
安全工具模块
提供路径验证、输入验证等安全功能
"""

import os
import re
import secrets
import hashlib
from pathlib import Path
from functools import wraps
from flask import request, jsonify

class SecurityValidator:
    """安全验证工具类"""
    
    @staticmethod
    def validate_path(user_path, allowed_base, path_type="directory"):
        """
        验证路径安全性，防止路径遍历攻击
        
        Args:
            user_path: 用户提供的路径
            allowed_base: 允许的基础目录
            path_type: 路径类型 ("directory" 或 "file")
        
        Returns:
            验证后的绝对路径
        
        Raises:
            ValueError: 路径验证失败
        """
        # 处理相对路径
        if user_path.startswith('../'):
            full_path = os.path.abspath(user_path)
        else:
            full_path = os.path.abspath(f'../{user_path}')
        
        # 获取允许的基础目录的绝对路径
        abs_base = os.path.abspath(allowed_base)
        
        # 检查路径是否在允许范围内
        try:
            # 使用 Path.resolve() 解析符号链接和 ..
            resolved_path = Path(full_path).resolve()
            resolved_base = Path(abs_base).resolve()
            
            # 检查是否是允许目录的子路径
            resolved_path.relative_to(resolved_base)
        except (ValueError, OSError):
            raise ValueError(f"路径 '{user_path}' 超出允许范围 '{allowed_base}'")
        
        return str(resolved_path)
    
    @staticmethod
    def validate_test_id(test_id):
        """
        验证测试ID
        
        Args:
            test_id: 测试ID
        
        Returns:
            验证后的测试ID
        
        Raises:
            ValueError: 测试ID无效
        """
        valid_ids = ['1', '2', '3', '4', '5', '6', '7']
        if test_id not in valid_ids:
            raise ValueError(f"无效的测试ID: {test_id}")
        return test_id
    
    @staticmethod
    def sanitize_log_message(message, max_length=1000):
        """
        清理日志消息，防止日志注入
        
        Args:
            message: 日志消息
            max_length: 最大长度
        
        Returns:
            清理后的消息
        """
        if not isinstance(message, str):
            message = str(message)
        
        # 移除控制字符
        message = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', message)
        
        # 限制长度
        if len(message) > max_length:
            message = message[:max_length] + "..."
        
        return message
    
    @staticmethod
    def sanitize_file_path(file_path):
        """
        清理文件路径，防止路径注入
        
        Args:
            file_path: 文件路径
        
        Returns:
            清理后的路径
        
        Raises:
            ValueError: 路径包含危险字符
        """
        # 检查危险字符
        dangerous_chars = ['|', '&', ';', '$', '`', '\n', '\r']
        for char in dangerous_chars:
            if char in file_path:
                raise ValueError(f"文件路径包含危险字符: {char}")
        
        # 规范化路径
        normalized = os.path.normpath(file_path)
        
        return normalized
    
    @staticmethod
    def validate_file_size(file_path, max_size=10 * 1024 * 1024):
        """
        验证文件大小
        
        Args:
            file_path: 文件路径
            max_size: 最大文件大小（字节），默认10MB
        
        Raises:
            ValueError: 文件过大
        """
        if not os.path.exists(file_path):
            return
        
        file_size = os.path.getsize(file_path)
        if file_size > max_size:
            raise ValueError(f"文件过大: {file_size} bytes (最大允许: {max_size} bytes)")


class AuthenticationManager:
    """认证管理器"""
    
    def __init__(self):
        """初始化认证管理器，生成会话令牌"""
        self.session_token = secrets.token_urlsafe(32)
        self.token_hash = hashlib.sha256(self.session_token.encode()).hexdigest()
        print(f"\n{'='*60}")
        print(f"  安全认证已启用")
        print(f"{'='*60}")
        print(f"访问令牌: {self.session_token}")
        print(f"请妥善保管此令牌，诊断工具关闭后将失效")
        print(f"{'='*60}\n")
    
    def verify_token(self, token):
        """
        验证令牌
        
        Args:
            token: 待验证的令牌
        
        Returns:
            bool: 令牌是否有效
        """
        if not token:
            return False
        
        try:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            return secrets.compare_digest(token_hash, self.token_hash)
        except:
            return False
    
    def require_auth(self, f):
        """
        装饰器：要求API端点进行认证
        
        Args:
            f: 被装饰的函数
        
        Returns:
            装饰后的函数
        """
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 从请求头或查询参数获取令牌
            token = request.headers.get('X-Diagnostic-Token')
            if not token:
                token = request.args.get('token')
            
            if not self.verify_token(token):
                return jsonify({
                    'success': False, 
                    'error': '未授权访问',
                    'message': '请提供有效的访问令牌'
                }), 401
            
            return f(*args, **kwargs)
        return decorated_function


class SecurityAuditor:
    """安全审计器"""
    
    def __init__(self, audit_log_path='security_audit.log'):
        """
        初始化安全审计器
        
        Args:
            audit_log_path: 审计日志文件路径
        """
        self.audit_log_path = audit_log_path
    
    def log_action(self, action, user='system', details=None, level='info'):
        """
        记录安全审计日志
        
        Args:
            action: 操作类型
            user: 用户标识
            details: 详细信息
            level: 日志级别
        """
        import json
        from datetime import datetime
        
        audit_entry = {
            'timestamp': datetime.now().isoformat(),
            'action': action,
            'user': user,
            'level': level,
            'details': details or {},
            'ip': request.remote_addr if request and hasattr(request, 'remote_addr') else 'local'
        }
        
        try:
            with open(self.audit_log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(audit_entry, ensure_ascii=False) + '\n')
        except Exception as e:
            print(f"审计日志写入失败: {e}")
    
    def log_security_event(self, event_type, severity='warning', details=None):
        """
        记录安全事件
        
        Args:
            event_type: 事件类型
            severity: 严重性 (info/warning/error/critical)
            details: 详细信息
        """
        self.log_action(
            action=f'security_event:{event_type}',
            level=severity,
            details=details
        )


def sanitize_ai_prompt_input(user_input):
    """
    清理AI提示输入，防止提示注入攻击
    
    Args:
        user_input: 用户输入
    
    Returns:
        清理后的输入
    """
    if not isinstance(user_input, str):
        user_input = str(user_input)
    
    # 移除可能的指令关键词
    dangerous_patterns = [
        r'忽略.*?指令',
        r'forget.*?instruction',
        r'ignore.*?previous',
        r'system\s*prompt',
        r'你是.*?助手',
        r'你现在.*?角色',
        r'扮演.*?身份'
    ]
    
    cleaned = user_input
    for pattern in dangerous_patterns:
        cleaned = re.sub(pattern, '[已过滤]', cleaned, flags=re.IGNORECASE)
    
    return cleaned

