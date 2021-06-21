import os
import sys
if sys.version_info.major == 3:
    from unittest import mock
else:
    import mock

test_path = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.dirname(test_path)
sys.path.insert(0, modules_path)

from sonic_platform.chassis import Chassis
from sonic_platform.device_data import DeviceDataManager


class TestChassis:
    """Test class to test chassis.py. The test cases covers:
        1. PSU related API
        2. Fan drawer related API
        3. SFP related API (Except modular chassis SFP related API)
        4. Reboot cause related API
    
    Thermal, Eeprom, Watchdog, Component, System LED related API will be tested in seperate class
    """
    @classmethod
    def setup_class(cls):
        os.environ["MLNX_PLATFORM_API_UNIT_TESTING"] = "1"

    def test_psu(self):
        from sonic_platform.psu import Psu, FixedPsu
        # Test creating hot swapable PSU
        DeviceDataManager.get_psu_count = mock.MagicMock(return_value=2)
        DeviceDataManager.is_psu_hotswapable = mock.MagicMock(return_value=True)
        chassis = Chassis()
        chassis.initialize_psu()
        assert len(chassis._psu_list) == 2
        assert len(list(filter(lambda x: isinstance(x, Psu) ,chassis._psu_list))) == 2

        # Test creating fixed PSU
        DeviceDataManager.get_psu_count = mock.MagicMock(return_value=3)
        DeviceDataManager.is_psu_hotswapable = mock.MagicMock(return_value=False)
        chassis._psu_list = []
        chassis.initialize_psu()
        assert len(chassis._psu_list) == 3
        assert len(list(filter(lambda x: isinstance(x, FixedPsu) ,chassis._psu_list))) == 3

        # Test chassis.get_all_psus
        chassis._psu_list = []
        psu_list = chassis.get_all_psus()
        assert len(psu_list) == 3

        # Test chassis.get_psu
        chassis._psu_list = []
        psu = chassis.get_psu(0)
        assert psu and isinstance(psu, FixedPsu)
        psu = chassis.get_psu(3)
        assert psu is None

        # Test chassis.get_num_psus
        chassis._psu_list = []
        assert chassis.get_num_psus() == 3

    def test_fan(self):
        from sonic_platform.fan_drawer import RealDrawer, VirtualDrawer

        # Test creating fixed fan
        DeviceDataManager.is_fan_hotswapable = mock.MagicMock(return_value=False)
        assert DeviceDataManager.get_fan_drawer_count() == 1
        DeviceDataManager.get_fan_count = mock.MagicMock(return_value=4)
        chassis = Chassis()
        chassis.initialize_fan()
        assert len(chassis._fan_drawer_list) == 1
        assert len(list(filter(lambda x: isinstance(x, VirtualDrawer) ,chassis._fan_drawer_list))) == 1
        assert chassis.get_fan_drawer(0).get_num_fans() == 4

        # Test creating hot swapable fan
        DeviceDataManager.get_fan_drawer_count = mock.MagicMock(return_value=2)
        DeviceDataManager.get_fan_count = mock.MagicMock(return_value=4)
        DeviceDataManager.is_fan_hotswapable = mock.MagicMock(return_value=True)
        chassis._fan_drawer_list = []
        chassis.initialize_fan()
        assert len(chassis._fan_drawer_list) == 2
        assert len(list(filter(lambda x: isinstance(x, RealDrawer) ,chassis._fan_drawer_list))) == 2
        assert chassis.get_fan_drawer(0).get_num_fans() == 2
        assert chassis.get_fan_drawer(1).get_num_fans() == 2

        # Test chassis.get_all_fan_drawers
        chassis._fan_drawer_list = []
        assert len(chassis.get_all_fan_drawers()) == 2

        # Test chassis.get_fan_drawer
        chassis._fan_drawer_list = []
        fan_drawer = chassis.get_fan_drawer(0)
        assert fan_drawer and isinstance(fan_drawer, RealDrawer)
        fan_drawer = chassis.get_fan_drawer(2)
        assert fan_drawer is None

        # Test chassis.get_num_fan_drawers
        chassis._fan_drawer_list = []
        assert chassis.get_num_fan_drawers() == 2

    def test_sfp(self):
        # Test get_num_sfps, it should not create any SFP objects
        DeviceDataManager.get_sfp_count = mock.MagicMock(return_value=3)
        chassis = Chassis()
        assert chassis.get_num_sfps() == 3
        assert len(chassis._sfp_list) == 0

        # Index out of bound, return None
        sfp = chassis.get_sfp(4)
        assert sfp is None
        assert len(chassis._sfp_list) == 0

        # Get one SFP, other SFP list should be initialized to None
        sfp = chassis.get_sfp(1)
        assert sfp is not None
        assert len(chassis._sfp_list) == 3
        assert chassis._sfp_list[1] is None
        assert chassis._sfp_list[2] is None
        assert chassis.sfp_initialized_count == 1

        # Get the SFP again, no new SFP created
        sfp1 = chassis.get_sfp(1)
        assert id(sfp) == id(sfp1)

        # Get another SFP, sfp_initialized_count increase
        sfp2 = chassis.get_sfp(2)
        assert sfp2 is not None
        assert chassis._sfp_list[2] is None
        assert chassis.sfp_initialized_count == 2

        # Get all SFPs, but there are SFP already created, only None SFP created
        sfp_list = chassis.get_all_sfps()
        assert len(sfp_list) == 3
        assert chassis.sfp_initialized_count == 3
        assert list(filter(lambda x: x is not None, sfp_list))
        assert id(sfp1) == id(sfp_list[0])
        assert id(sfp2) == id(sfp_list[1])

        # Get all SFPs, no SFP yet, all SFP created
        chassis._sfp_list = []
        chassis.sfp_initialized_count = 0
        sfp_list = chassis.get_all_sfps()
        assert len(sfp_list) == 3
        assert chassis.sfp_initialized_count == 3

    def test_change_event(self):
        from sonic_platform.sfp_event import sfp_event
        from sonic_platform.sfp import SFP
        DeviceDataManager.get_sfp_count = mock.MagicMock(return_value=3)
        sfp_event.__init__ = mock.MagicMock(return_value=None)
        sfp_event.initialize = mock.MagicMock()
        SFP.reinit = mock.MagicMock()

        return_port_dict = {1: '1'}
        def mock_check_sfp_status(self, port_dict, timeout):
            port_dict.update(return_port_dict)
            return True if port_dict else False

        sfp_event.check_sfp_status = mock_check_sfp_status
        chassis = Chassis()

        # Call get_change_event with timeout=0, wait until an event is detected
        status, event_dict = chassis.get_change_event()
        assert status is True
        assert 'sfp' in event_dict and event_dict['sfp'][1] == '1'
        assert len(chassis._sfp_list) == 3
        assert SFP.reinit.call_count == 1

        # Call get_change_event with timeout=1.0
        return_port_dict = {}
        status, event_dict = chassis.get_change_event(timeout=1.0)
        assert status is True
        assert 'sfp' in event_dict and not event_dict['sfp']

    def test_reboot_cause(self):
        from sonic_platform import utils
        from sonic_platform.chassis import REBOOT_CAUSE_ROOT
        chassis = Chassis()
        major, minor = chassis.get_reboot_cause()
        assert major == chassis.REBOOT_CAUSE_NON_HARDWARE
        assert minor == ''

        mock_file_content = {}
        def read_int_from_file(file_path, default=0, raise_exception=False):
            return mock_file_content[file_path]

        utils.read_int_from_file = read_int_from_file

        for key, value in chassis.reboot_major_cause_dict.items():
            file_path = os.path.join(REBOOT_CAUSE_ROOT, key)
            mock_file_content[file_path] = 1
            major, minor = chassis.get_reboot_cause()
            assert major == value
            assert minor == ''
            mock_file_content[file_path] = 0

        for key, value in chassis.reboot_minor_cause_dict.items():
            file_path = os.path.join(REBOOT_CAUSE_ROOT, key)
            mock_file_content[file_path] = 1
            major, minor = chassis.get_reboot_cause()
            assert major == chassis.REBOOT_CAUSE_HARDWARE_OTHER
            assert minor == value
            mock_file_content[file_path] = 0

    def test_module(self):
        from sonic_platform.chassis import ModularChassis
        # Test get_num_modules, it should not create any SFP objects
        DeviceDataManager.get_linecard_count = mock.MagicMock(return_value=3)
        chassis = ModularChassis()
        assert chassis.get_num_modules() == 3
        assert len(chassis._module_list) == 0

        # Index out of bound, return None
        m = chassis.get_module(3)
        assert m is None
        assert len(chassis._module_list) == 0

        # Get one Module, other Module in list should be initialized to None
        m = chassis.get_module(0)
        assert m is not None
        assert len(chassis._module_list) == 3
        assert chassis._module_list[1] is None
        assert chassis._module_list[2] is None
        assert chassis.module_initialized_count == 1

        # Get the Module again, no new Module created
        m1 = chassis.get_module(0)
        assert id(m) == id(m1)

        # Get another Module, module_initialized_count increase
        m2 = chassis.get_module(1)
        assert m2 is not None
        assert chassis._module_list[2] is None
        assert chassis.module_initialized_count == 2

        # Get all SFPs, but there are SFP already created, only None SFP created
        module_list = chassis.get_all_modules()
        assert len(module_list) == 3
        assert chassis.module_initialized_count == 3
        assert list(filter(lambda x: x is not None, module_list))
        assert id(m1) == id(module_list[0])
        assert id(m2) == id(module_list[1])

        # Get all SFPs, no SFP yet, all SFP created
        chassis._module_list = []
        chassis.module_initialized_count = 0
        module_list = chassis.get_all_modules()
        assert len(module_list) == 3
        assert chassis.module_initialized_count == 3
