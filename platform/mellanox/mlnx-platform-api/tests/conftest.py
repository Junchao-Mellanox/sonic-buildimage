import os
import pytest
import sys

test_path = os.path.dirname(os.path.abspath(__file__))
modules_path = os.path.dirname(test_path)
sys.path.insert(0, modules_path)

os.environ["PLATFORM_API_UNIT_TESTING"] = "1"

from sonic_platform import utils

@pytest.fixture(scope='function', autouse=True)
def auto_recover_mock():
    """Auto used fixture to recover some critical mocked functions
    """
    origin_os_path_exists = os.path.exists
    origin_read_int_from_file = utils.read_int_from_file
    origin_read_str_from_file = utils.read_str_from_file
    origin_read_float_from_file = utils.read_float_from_file
    origin_write_file = utils.write_file
    yield
    os.path.exists = origin_os_path_exists
    utils.read_int_from_file = origin_read_int_from_file
    utils.read_str_from_file = origin_read_str_from_file
    utils.write_file = origin_write_file
    utils.read_float_from_file = origin_read_float_from_file
