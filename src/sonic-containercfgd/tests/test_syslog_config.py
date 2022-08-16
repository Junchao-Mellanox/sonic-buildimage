import os
import sys
from unittest import mock

test_path = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.dirname(test_path)
sys.path.insert(0, modules_path)

from containercfgd import containercfgd

containercfgd.container_name = 'swss'


def test_handle_config():
    handler = containercfgd.SyslogHandler()
    handler.update_syslog_config = mock.MagicMock()

    handler.handle_config(containercfgd.SYSLOG_CONFIG_FEATURE_TABLE,
                          'bgp',
                          None)
    handler.update_syslog_config.assert_not_called()

    handler.handle_config(containercfgd.SYSLOG_CONFIG_FEATURE_TABLE,
                          'swss',
                          None)
    handler.update_syslog_config.assert_called_once()

    handler.update_syslog_config.side_effect = Exception('')
    handler.handle_config(containercfgd.SYSLOG_CONFIG_FEATURE_TABLE,
                          'swss',
                          None)


def test_handle_init_data():
    handler = containercfgd.SyslogHandler()
    handler.update_syslog_config = mock.MagicMock()

    init_data = {}
    handler.handle_init_data(init_data)
    handler.update_syslog_config.assert_not_called()

    init_data = {containercfgd.SYSLOG_CONFIG_FEATURE_TABLE: {}}
    handler.handle_init_data(init_data)
    handler.update_syslog_config.assert_not_called()

    init_data = {containercfgd.SYSLOG_CONFIG_FEATURE_TABLE: {'swss': {}}}
    handler.handle_init_data(init_data)
    handler.update_syslog_config.assert_called_once()


@mock.patch('containercfgd.containercfgd.run_command')
def test_update_syslog_config(mock_run_cmd):
    handler = containercfgd.SyslogHandler()
    handler.parse_syslog_conf = mock.MagicMock(return_value=('100', '200', '127.0.0.1'))

    data = {containercfgd.SYSLOG_RATE_LIMIT_INTERVAL: '100',
            containercfgd.SYSLOG_RATE_LIMIT_BURST: '200'}
    handler.update_syslog_config(data)
    mock_run_cmd.assert_not_called()

    data = {containercfgd.SYSLOG_RATE_LIMIT_INTERVAL: '200',
            containercfgd.SYSLOG_RATE_LIMIT_BURST: '200'}

    handler.update_syslog_config(data)
    mock_run_cmd.assert_called()


def test_parse_syslog_conf():
    handler = containercfgd.SyslogHandler()
    handler.SYSLOG_CONF_PATH = os.path.join(test_path, 'mock_rsyslog.conf')
    interval, burst, target_ip = handler.parse_syslog_conf()
    assert interval == '50'
    assert burst == '10002'
    assert target_ip == '127.0.0.1'

    handler.SYSLOG_CONF_PATH = os.path.join(test_path, 'mock_empty_rsyslog.conf')
    interval, burst, target_ip = handler.parse_syslog_conf()
    assert interval is '0'
    assert burst is '0'
    assert target_ip is None
