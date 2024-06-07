import logging
import os
import sys

if sys.version_info.major == 3:
    from unittest import mock
else:
    import mock

modules_path = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(modules_path, 'sonic_py_common'))
from sonic_py_common import syslogger

logging.NOTICE = logging.INFO + 1


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

    @mock.patch('swsscommon.swsscommon.SonicV2Connector')
    @mock.patch('sonic_py_common.syslogger.SysLogger.log', mock.MagicMock())
    def test_runtime_config(self, mock_connector):
        syslogger.Singleton._instances = {}
        mock_db = mock.MagicMock()
        mock_db.get.return_value = None
        mock_db.hmset = mock.MagicMock()
        mock_connector.return_value = mock_db
        log = syslogger.SysLogger(log_identifier='log', enable_runtime_config=True, log_level=logging.INFO)
        mock_db.hmset.assert_called_once()

        mock_db.get.return_value = 'ERROR'
        log.refresh_config()
        assert log.logger.level == logging.ERROR

    @mock.patch('swsscommon.swsscommon.SonicV2Connector')
    @mock.patch('sonic_py_common.syslogger.SysLogger.log', mock.MagicMock())
    def test_runtime_config_negative(self, mock_connector):
        mock_db = mock.MagicMock()
        mock_db.get = mock.MagicMock(side_effect=Exception(''))
        mock_connector.return_value = mock_db
        log = syslogger.SysLogger(log_identifier='log', enable_runtime_config=True)
        log.refresh_config() # no exception here
