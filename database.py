from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# 创建基类
Base = declarative_base()

# 创建数据库连接
engine = create_engine('sqlite:///chat_history.db')

# 创建会话工厂
Session = sessionmaker(bind=engine)

class ChatMessage(Base):
    __tablename__ = 'chat_messages'
    
    id = Column(Integer, primary_key=True)
    sender_id = Column(String(100))  # 发送者微信ID
    sender_name = Column(String(100))  # 发送者昵称
    message = Column(Text)  # 发送的消息
    reply = Column(Text)  # 机器人的回复
    created_at = Column(DateTime, default=datetime.now)

# 创建数据库表
Base.metadata.create_all(engine) 