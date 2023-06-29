import os
import pytest
import subprocess
import sys
if sys.version_info.major == 3:
    from unittest import mock
else:
    import mock

test_path = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.dirname(test_path)
sys.path.insert(0, modules_path)

from sonic_platform.device_data import DeviceDataManager


class TestDeviceData:
    @mock.patch('sonic_platform.device_data.utils.read_int_from_file', mock.MagicMock(return_value=1))
    def test_is_fan_hotswapable(self):
        assert DeviceDataManager.is_fan_hotswapable()

    @mock.patch('sonic_platform.device_data.utils.read_int_from_file', mock.MagicMock(return_value=1))
    def test_get_linecard_sfp_count(self):
        assert DeviceDataManager.get_linecard_sfp_count(1) == 1

    @mock.patch('sonic_platform.device_data.utils.read_int_from_file', mock.MagicMock(return_value=1))
    def test_get_gearbox_count(self):
        assert DeviceDataManager.get_gearbox_count('') == 1

    @mock.patch('sonic_platform.device_data.DeviceDataManager.get_platform_name', mock.MagicMock(return_value='x86_64-mlnx_msn3420-r0'))
    def test_get_linecard_max_port_count(self):
        assert DeviceDataManager.get_linecard_max_port_count() == 0

    @mock.patch('sonic_platform.device_data.DeviceDataManager.get_platform_name', mock.MagicMock(return_value='x86_64-nvidia_sn2201-r0'))
    def test_get_bios_component(self):
        assert DeviceDataManager.get_bios_component() is not None



