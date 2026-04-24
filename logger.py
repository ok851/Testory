import logging
import os
from datetime import datetime
import json
import traceback
from typing import Optional, Dict, Any

class UATLogger:
    """UI自动化测试平台日志记录器"""
    
    def __init__(self, name: str = "UATPlatform", log_dir: str = "logs"):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.DEBUG)
        
        # 创建日志目录
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)
        
        # 设置日志格式
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # 文件处理器 - 记录所有日志
        today = datetime.now().strftime("%Y%m%d")
        file_handler = logging.FileHandler(
            os.path.join(log_dir, f"uat_platform_{today}.log"),
            encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)
        
        # 错误文件处理器 - 只记录错误
        error_handler = logging.FileHandler(
            os.path.join(log_dir, f"errors_{today}.log"),
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        self.logger.addHandler(error_handler)
        
        # 控制台处理器 - 只记录INFO及以上级别
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)
    
    def debug(self, message: str):
        """记录调试信息"""
        self.logger.debug(message)
    
    def info(self, message: str):
        """记录一般信息"""
        self.logger.info(message)
    
    def warning(self, message: str):
        """记录警告信息"""
        self.logger.warning(message)
    
    def error(self, message: str, exc_info: bool = True):
        """记录错误信息"""
        if exc_info:
            self.logger.error(message, exc_info=True)
        else:
            self.logger.error(message)
    
    def critical(self, message: str, exc_info: bool = True):
        """记录严重错误"""
        if exc_info:
            self.logger.critical(message, exc_info=True)
        else:
            self.logger.critical(message)
    
    def log_api_request(self, endpoint: str, method: str, request_data: Optional[Dict] = None):
        """记录API请求"""
        message = f"API请求: {method} {endpoint}"
        if request_data:
            # 避免记录敏感信息
            filtered_data = self._filter_sensitive_data(request_data.copy())
            message += f" | 数据: {json.dumps(filtered_data, ensure_ascii=False)}"
        self.info(message)
    
    def log_api_response(self, endpoint: str, status_code: int, response_data: Optional[Dict] = None):
        """记录API响应"""
        message = f"API响应: {endpoint} | 状态码: {status_code}"
        if response_data:
            # 避免记录敏感信息
            filtered_data = self._filter_sensitive_data(response_data.copy())
            message += f" | 数据: {json.dumps(filtered_data, ensure_ascii=False)}"
        self.info(message)
    
    def log_automation_step(self, action: str, selector: Optional[str] = None, details: Optional[str] = None):
        """记录自动化步骤"""
        message = f"自动化操作: {action}"
        if selector:
            message += f" | 选择器: {selector}"
        if details:
            message += f" | 详情: {details}"
        self.info(message)
    
    def log_browser_event(self, event_type: str, event_data: Dict[str, Any]):
        """记录浏览器事件"""
        filtered_data = self._filter_sensitive_data(event_data.copy())
        message = f"浏览器事件: {event_type} | 数据: {json.dumps(filtered_data, ensure_ascii=False)}"
        self.debug(message)
    
    def log_recording_session(self, start_time: datetime, end_time: datetime, step_count: int, url: str):
        """记录录制会话"""
        duration = end_time - start_time
        message = f"录制会话: URL={url} | 时长={duration.total_seconds():.2f}秒 | 步骤数={step_count}"
        self.info(message)
    
    def log_exception(self, func_name: str, exception: Exception, additional_info: Optional[str] = None):
        """记录异常"""
        message = f"异常捕获: 函数={func_name} | 异常类型={type(exception).__name__} | 异常消息={str(exception)}"
        if additional_info:
            message += f" | 附加信息={additional_info}"
        
        # 记录详细的堆栈跟踪
        self.error(message)
    
    def _filter_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """过滤敏感数据，避免在日志中记录密码等信息"""
        sensitive_keys = (
            'password',
            'pwd',
            'token',
            'secret',
            'api_key',
            'authorization',
        )
        
        if not isinstance(data, dict):
            return data
        
        filtered = data.copy()
        
        for key, value in filtered.items():
            key_lower = str(key).lower()
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                filtered[key] = '***'
            elif key_lower.endswith('_key') or key_lower.endswith('_secret'):
                filtered[key] = '***'
        
        return filtered

# 创建全局日志记录器实例
uat_logger = UATLogger()