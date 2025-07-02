# -*- coding: utf-8 -*-

"""
数据库模型定义
定义了用户聊天记录、群聊记录和群聊总结的数据模型
"""

from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer, Enum, TIMESTAMP
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from datetime import datetime

Base = declarative_base()

class UserChatMessage(Base):
    """用户聊天消息模型"""
    __tablename__ = 'user_chat_messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    user_id = Column(String(100), nullable=False, comment='用户ID')
    speaker = Column(String(100), nullable=False, comment='发言者')
    message_content = Column(Text, nullable=False, comment='消息内容')
    message_time = Column(DateTime, nullable=False, comment='消息时间')
    prompt_name = Column(String(100), comment='角色名称')
    message_type = Column(Enum('user', 'ai', 'system', name='message_type'), default='user', comment='消息类型')
    created_at = Column(TIMESTAMP, default=func.current_timestamp(), comment='创建时间')
    
    def __repr__(self):
        return f"<UserChatMessage(id={self.id}, user_id='{self.user_id}', speaker='{self.speaker}')>"

class GroupChatMessage(Base):
    """群聊消息模型"""
    __tablename__ = 'group_chat_messages'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    group_id = Column(String(100), nullable=False, comment='群聊ID')
    speaker = Column(String(100), nullable=False, comment='发言者')
    message_content = Column(Text, nullable=False, comment='消息内容')
    message_time = Column(DateTime, nullable=False, comment='消息时间')
    prompt_name = Column(String(100), comment='角色名称')
    message_type = Column(Enum('user', 'ai', 'system', name='group_message_type'), default='user', comment='消息类型')
    created_at = Column(TIMESTAMP, default=func.current_timestamp(), comment='创建时间')
    
    def __repr__(self):
        return f"<GroupChatMessage(id={self.id}, group_id='{self.group_id}', speaker='{self.speaker}')>"

class GroupSummary(Base):
    """群聊总结模型"""
    __tablename__ = 'group_summaries'
    
    id = Column(Integer, primary_key=True, autoincrement=True, comment='主键ID')
    group_name = Column(String(100), nullable=False, comment='群聊名称')
    summary_content = Column(Text, nullable=False, comment='总结内容')
    summary_date = Column(DateTime, nullable=False, comment='总结日期')
    time_range = Column(String(20), nullable=False, comment='时间范围')
    message_count = Column(Integer, default=0, comment='消息数量')
    prompt_name = Column(String(100), comment='角色名称')
    created_at = Column(TIMESTAMP, default=func.current_timestamp(), comment='创建时间')
    
    def __repr__(self):
        return f"<GroupSummary(id={self.id}, group_name='{self.group_name}', date='{self.summary_date}')>" 