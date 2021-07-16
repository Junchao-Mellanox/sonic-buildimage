import os
import sys
if sys.version_info.major == 3:
    from unittest import mock
else:
    import mock

test_path = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.dirname(test_path)
sys.path.insert(0, modules_path)

from sonic_platform.sfp import SFP, SX_PORT_MODULE_STATUS_INITIALIZING, SX_PORT_MODULE_STATUS_PLUGGED, SX_PORT_MODULE_STATUS_UNPLUGGED, SX_PORT_MODULE_STATUS_PLUGGED_WITH_ERROR, SX_PORT_MODULE_STATUS_PLUGGED_DISABLED
from sonic_platform.chassis import Chassis


@mock.patch('sonic_platform.sfp.SFP._read_eeprom_specific_bytes', mock.MagicMock(return_value=None))
@mock.patch('sonic_platform.sfp.SFP._get_error_code')
@mock.patch('sonic_platform.chassis.Chassis.get_num_sfps', mock.MagicMock(return_value=2))
def test_sfp_get_error_status(mock_get_error_code):
    chassis = Chassis()

    # Fetch an SFP module to test
    sfp = chassis.get_sfp(1)

    description_dict = sfp._get_error_description_dict()
    for error in description_dict.keys():
        mock_get_error_code.return_value = (SX_PORT_MODULE_STATUS_PLUGGED_WITH_ERROR, error)
        description = sfp.get_error_description()

        assert description == description_dict[error]

    mock_get_error_code.return_value = (SX_PORT_MODULE_STATUS_PLUGGED_WITH_ERROR, -1)
    description = sfp.get_error_description()
    assert description == "Unknown error (-1)"

    expected_description_list = [
        (SX_PORT_MODULE_STATUS_INITIALIZING, "Initializing"),
        (SX_PORT_MODULE_STATUS_PLUGGED, "OK"),
        (SX_PORT_MODULE_STATUS_UNPLUGGED, "Unplugged"),
        (SX_PORT_MODULE_STATUS_PLUGGED_DISABLED, "Disabled")
    ]
    for oper_code, expected_description in expected_description_list:
        mock_get_error_code.return_value = (oper_code, -1)
        description = sfp.get_error_description()

        assert description == expected_description
