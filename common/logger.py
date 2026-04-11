import logging
import os
import sys
import time
import functools
from logging.handlers import RotatingFileHandler
from typing import Optional
from colorlog import ColoredFormatter

APP_LOGGER_NAME = 'br-korea-poc'

def init_logger(
    name: str = APP_LOGGER_NAME, 
    log_level: int = logging.INFO, 
    log_format: Optional[str] = None,
    log_file: Optional[str] = None
) -> logging.Logger:
    """
    Initializes a standardized, colored logger for the project.
    """
    if log_format is None:
        log_format = (
            '%(asctime)s - '
            '%(name)s - '
            '%(funcName)s - '
            '%(log_color)s%(levelname)s%(reset)s - '
            '%(message)s'
        )

    formatter = ColoredFormatter(
        log_format,
        reset=True,
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'bold_red',
        }
    )

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # If logger already has handlers, return it (prevents duplicate logs)
    if logger.handlers:
        return logger

    # 기존 핸들러 제거 (안전을 위해)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)

    # 콘솔 출력 설정
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(formatter)
    ch.setLevel(log_level)
    logger.addHandler(ch)

    # File Handler (Optional - 기존 프로젝트 하위 호환성 유지)
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir)
            
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(funcName)s - %(levelname)s - %(message)s"
        )
        file_handler = RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5, encoding="utf-8")
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(log_level)
        logger.addHandler(file_handler)

    return logger

def is_initialized(logger_name):
    logger = logging.getLogger(logger_name)
    return len(logger.handlers) > 0

def timefn(fn):
    """
    A decorator that logs the execution time of the decorated function.
    """
    @functools.wraps(fn)
    def measure_time(*args, **kwargs):
        # 실행 시간을 기록할 메인 로거 가져오기
        logger = logging.getLogger(APP_LOGGER_NAME)
        # 로거가 초기화되지 않았다면 기본 설정으로 초기화
        if not logger.handlers:
            init_logger(name=APP_LOGGER_NAME)
            
        start_time = time.time()
        result = fn(*args, **kwargs)
        end_time = time.time()
        execution_time = end_time - start_time
        logger.info(f"함수 {fn.__name__} 실행 시간: {execution_time:.2f}초")
        return result

    return measure_time
