# -*- coding: utf-8 -*-

"""
数据库连接管理器
提供数据库连接初始化、会话管理、连接测试等功能
"""

import logging
import threading
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.exc import OperationalError
import config

logger = logging.getLogger(__name__)

class DatabaseManager:
    """数据库连接管理器"""
    
    def __init__(self):
        self.engine = None
        self.Session = None
        self.is_initialized = False
        self._lock = threading.RLock()
    
    def initialize(self):
        """初始化数据库连接"""
        with self._lock:
            if self.is_initialized:
                logger.info("数据库已经初始化，跳过重复初始化")
                return True
            
            if not config.ENABLE_DATABASE:
                logger.info("数据库功能未启用，跳过初始化")
                return False
            
            try:
                # 构建数据库连接URL
                connection_url = (
                    f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}@"
                    f"{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
                    f"?charset={config.DB_CHARSET}"
                )
                
                # 创建数据库引擎
                self.engine = create_engine(
                    connection_url,
                    pool_size=config.DB_POOL_SIZE,
                    max_overflow=config.DB_MAX_OVERFLOW,
                    pool_timeout=config.DB_POOL_TIMEOUT,
                    pool_recycle=config.DB_POOL_RECYCLE,
                    echo=False,  # 生产环境设为False
                    connect_args={'connect_timeout': 10}
                )
                
                # 创建会话工厂
                SessionFactory = sessionmaker(bind=self.engine)
                self.Session = scoped_session(SessionFactory)
                
                # 测试连接
                if self.test_connection():
                    self.is_initialized = True
                    logger.info("数据库连接初始化成功")
                    return True
                else:
                    self._cleanup()
                    return False
                    
            except Exception as e:
                logger.error(f"数据库初始化失败: {e}")
                self._cleanup()
                return False
    
    def get_session(self):
        """获取数据库会话"""
        if not self.is_initialized or self.Session is None:
            logger.warning("数据库未初始化，无法获取会话")
            return None
        
        try:
            return self.Session()
        except Exception as e:
            logger.error(f"获取数据库会话失败: {e}")
            return None
    
    def test_connection(self):
        """测试数据库连接"""
        if self.engine is None:
            logger.error("数据库引擎未初始化")
            return False
            
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            logger.info("数据库连接测试成功")
            return True
        except OperationalError as e:
            logger.error(f"数据库连接测试失败: {e}")
            return False
        except Exception as e:
            logger.error(f"数据库连接测试出现未知错误: {e}")
            return False
    
    def close(self):
        """关闭数据库连接"""
        with self._lock:
            try:
                if self.Session:
                    self.Session.remove()
                    self.Session = None
                
                if self.engine:
                    self.engine.dispose()
                    self.engine = None
                
                self.is_initialized = False
                logger.info("数据库连接已关闭")
                
            except Exception as e:
                logger.error(f"关闭数据库连接时出错: {e}")
    
    def _cleanup(self):
        """清理资源"""
        try:
            if self.Session:
                self.Session.remove()
                self.Session = None
            
            if self.engine:
                self.engine.dispose()
                self.engine = None
                
            self.is_initialized = False
        except Exception as e:
            logger.error(f"清理数据库资源时出错: {e}")
    
    def execute_with_retry(self, operation, session=None, max_retries=3):
        """执行数据库操作，支持重试机制"""
        for attempt in range(max_retries):
            session_provided = session is not None
            if not session_provided:
                session = self.get_session()
            
            if not session:
                logger.error("无法获取数据库会话")
                return None
            
            try:
                result = operation(session)
                if not session_provided:
                    session.commit()
                return result
                
            except OperationalError as e:
                logger.warning(f"数据库操作失败，尝试重试 ({attempt + 1}/{max_retries}): {e}")
                if not session_provided:
                    session.rollback()
                    session.close()
                
                if attempt == max_retries - 1:
                    logger.error(f"数据库操作最终失败: {e}")
                    return None
                    
            except Exception as e:
                logger.error(f"数据库操作出现未知错误: {e}")
                if not session_provided:
                    session.rollback()
                    session.close()
                return None
            
            finally:
                if not session_provided and session:
                    session.close()
    
    def is_available(self):
        """检查数据库是否可用"""
        return self.is_initialized and config.ENABLE_DATABASE

# 全局数据库管理器实例
db_manager = DatabaseManager()

def init_database():
    """初始化数据库连接的便捷函数"""
    return db_manager.initialize()

def get_db_session():
    """获取数据库会话的便捷函数"""
    return db_manager.get_session()

def close_database():
    """关闭数据库连接的便捷函数"""
    db_manager.close() 