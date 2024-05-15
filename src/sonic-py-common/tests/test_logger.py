import logging
import os
import pytest
import sys
import time

if sys.version_info.major == 3:
    from unittest import mock
else:
    import mock

modules_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(modules_path, 'sonic_py_common'))
from sonic_py_common import logger, syslogger


@pytest.fixture()
def auto_stop():
    logger.Logger.config_handler.stop()


def wait_until(predict, timeout, interval=1, *args, **kwargs):
    """Wait until a condition become true

    Args:
        predict (object): a callable such as function, lambda
        timeout (int): wait time in seconds
        interval (int, optional): interval to check the predict. Defaults to 1.

    Returns:
        _type_: _description_
    """
    if predict(*args, **kwargs):
        return True
    while timeout > 0:
        time.sleep(interval)
        timeout -= interval
        if predict(*args, **kwargs):
            return True
    return False


class TestLogger:
    def test_basic(self):
        log = logger.Logger()
        log.log_error('error message')
        log.log_warning('warning message')
        log.log_notice('notice message')
        log.log_info('info message')
        log.log_debug('debug message')
        log.log(log.LOG_PRIORITY_ERROR, 'error msg', also_print_to_console=True)
        
    def test_log_priority(self):
        log = logger.Logger()
        log.set_min_log_priority_error()
        assert log._min_log_priority == log.LOG_PRIORITY_ERROR
        log.set_min_log_priority_warning()
        assert log._min_log_priority == log.LOG_PRIORITY_WARNING
        log.set_min_log_priority_notice()
        assert log._min_log_priority == log.LOG_PRIORITY_NOTICE
        log.set_min_log_priority_info()
        assert log._min_log_priority == log.LOG_PRIORITY_INFO
        log.set_min_log_priority_debug()
        assert log._min_log_priority == log.LOG_PRIORITY_DEBUG
    
    def test_log_priority_from_str(self):
        log = logger.Logger()
        assert log.log_priority_from_str('ERROR') == log.LOG_PRIORITY_ERROR
        assert log.log_priority_from_str('INFO') == log.LOG_PRIORITY_INFO
        assert log.log_priority_from_str('NOTICE') == log.LOG_PRIORITY_NOTICE
        assert log.log_priority_from_str('WARN') == log.LOG_PRIORITY_WARNING
        assert log.log_priority_from_str('DEBUG') == log.LOG_PRIORITY_DEBUG
        assert log.log_priority_from_str('invalid') == log.LOG_PRIORITY_NOTICE
        
    def test_log_priority_to_str(self):
        log = logger.Logger()
        assert log.log_priority_to_str(log.LOG_PRIORITY_NOTICE) == 'NOTICE'
        assert log.log_priority_to_str(log.LOG_PRIORITY_INFO) == 'INFO'
        assert log.log_priority_to_str(log.LOG_PRIORITY_DEBUG) == 'DEBUG'
        assert log.log_priority_to_str(log.LOG_PRIORITY_WARNING) == 'WARN'
        assert log.log_priority_to_str(log.LOG_PRIORITY_ERROR) == 'ERROR'
        assert log.log_priority_to_str(-1) == 'NOTICE'


class TestSysLogger:
    def test_basic(self):
        log = syslogger.SysLogger()
        log.logger.log = mock.MagicMock()
        log.log_error('error message')
        log.log_warning('warning message')
        log.log_notice('notice message')
        log.log_info('info message')
        log.log_debug('debug message')
        log.log(logging.ERROR, 'error msg', also_print_to_console=True)
        
    def test_log_priority(self):
        log = syslogger.SysLogger()
        log.set_min_log_priority(logging.ERROR)
        assert log.logger.level == logging.ERROR
        
    def test_log_priority_from_str(self):
        log = syslogger.SysLogger()
        assert log.log_priority_from_str('ERROR') == logging.ERROR
        assert log.log_priority_from_str('INFO') == logging.INFO
        assert log.log_priority_from_str('NOTICE') == logging.NOTICE
        assert log.log_priority_from_str('WARN') == logging.WARN
        assert log.log_priority_from_str('DEBUG') == logging.DEBUG
        assert log.log_priority_from_str('invalid') == logging.NOTICE
        
    def test_log_priority_to_str(self):
        log = syslogger.SysLogger()
        assert log.log_priority_to_str(logging.NOTICE) == 'NOTICE'
        assert log.log_priority_to_str(logging.INFO) == 'INFO'
        assert log.log_priority_to_str(logging.DEBUG) == 'DEBUG'
        assert log.log_priority_to_str(logging.WARN) == 'WARN'
        assert log.log_priority_to_str(logging.ERROR) == 'ERROR'
        assert log.log_priority_to_str(-1) == 'NOTICE'


class TestLoggerConfigHandler:
    @mock.patch('sonic_py_common.logger_config_handler.redis.Redis', mock.MagicMock())
    def test_start_stop(self):
        log = logger.Logger()
        log.config_handler.start()
        log.config_handler.stop()
  
    @mock.patch('sonic_py_common.logger_config_handler.redis.Redis')
    def test_reset(self, mock_redis, auto_stop):
        mock_db = mock.MagicMock()
        mock_redis.return_value = mock_db
        mock_pubsub = mock.MagicMock()
        mock_pubsub.get_message = mock.MagicMock(return_value=None)
        mock_db.pubsub = mock.MagicMock(return_value=mock_pubsub)
        log = logger.Logger(enable_config_thread=True)
        old_thread = log.config_handler.log_config_thread
        log.config_handler.reset()
        assert old_thread != log.config_handler.log_config_thread
        assert not log.config_handler.log_thread_started
       
    @mock.patch('sonic_py_common.logger_config_handler.redis.Redis') 
    def test_run(self, mock_redis, auto_stop):
        mock_db = mock.MagicMock()
        mock_redis.return_value = mock_db
        mock_db.hget = mock.MagicMock(return_value='ERROR')
        mock_pubsub = mock.MagicMock()
        mock_pubsub.get_message = mock.MagicMock(return_value=None)
        mock_db.pubsub = mock.MagicMock(return_value=mock_pubsub)
        log = logger.Logger(log_identifier='test', enable_config_thread=True)
        slog = syslogger.SysLogger(log_identifier='test', enable_config_thread=True)
        assert wait_until(lambda :log._min_log_priority == log.LOG_PRIORITY_ERROR,
                          timeout=5,
                          interval=1)
        assert wait_until(lambda :slog.logger.level == logging.ERROR,
                          timeout=5,
                          interval=1)
        mock_db.hget = mock.MagicMock(return_value='NOTICE')
        mock_pubsub.get_message.return_value = {
            'channel': '__keyspace@4__:LOGGER|test',
            'data': 'hset'
        }
        assert wait_until(lambda :log._min_log_priority == log.LOG_PRIORITY_NOTICE,
                          timeout=5,
                          interval=1)
        assert wait_until(lambda :slog.logger.level == logging.NOTICE,
                          timeout=5,
                          interval=1)
        mock_pubsub.get_message.return_value = None
   