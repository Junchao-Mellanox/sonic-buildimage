import os
import redis
import sys
import threading
from sonic_platform_base.module_base import ModuleBase
from sonic_py_common.logger import Logger

from . import utils
from .device_data import DeviceDataManager
from .vpd_parser import VpdParser

# Global logger class instance
logger = Logger()


class Module(ModuleBase):
    STATE_ACTIVATED = 1
    STATE_DEACTIVATED = 0

    STATE_DB = 6
    STATE_MODULAR_CHASSIS_SLOT_TABLE = 'MODULAR_CHASSIS_SLOT|{}'
    FIELD_SEQ_NO = 'seq_no'
    redis_client = redis.Redis(db = STATE_DB)

    def __init__(self, slot_id):
        super(Module, self).__init__()
        self.slot_id = slot_id
        self.seq_no = 0
        self.current_state = Module.STATE_DEACTIVATED
        self.lock = threading.Lock()

        self.sfp_initialized_count = 0
        self.vpd_parser = VpdParser('/run/hw-management/lc{}/eeprom/vpd_parsed')


    def get_name(self):
        return 'LINE-CARD{}'.format(self.slot_id)

    def get_model(self):
        """
        Retrieves the model number (or part number) of the device

        Returns:
            string: Model/part number of device
        """
        return self.vpd_parser.get_model()

    def get_serial(self):
        """
        Retrieves the serial number of the device

        Returns:
            string: Serial number of device
        """
        return self.vpd_parser.get_serial()

    def get_revision(self):
        """
        Retrieves the hardware revision of the device

        Returns:
            string: Revision value of device
        """
        return self.vpd_parser.get_revision()

    def get_type(self):
        return ModuleBase.MODULE_TYPE_LINE

    def get_slot(self):
        return self.slot_id

    def get_presence(self):
        return utils.read_int_from_file('/run/hw-management/system/lc{}_prsnt'.format(self.slot_id)) == 1

    def get_position_in_parent(self):
        return self.slot_id

    def is_replaceable(self):
        return True

    def get_oper_status(self):
        if utils.read_int_from_file('/run/hw-management/system/lc{}_active'.format(self.slot_id)) == 1:
            return ModuleBase.MODULE_STATUS_ONLINE
        elif utils.read_int_from_file('/run/hw-management/system/lc{}_prsnt'.format(self.slot_id)) == 1:
            return ModuleBase.MODULE_STATUS_PRESENT
        elif utils.read_int_from_file('/run/hw-management/system/lc{}_prsnt'.format(self.slot_id)) == 0:
            return ModuleBase.MODULE_STATUS_EMPTY
        else:
            return ModuleBase.MODULE_STATUS_FAULT

    def _check_state(self):
        seq_no = self._get_seq_no()
        state = utils.read_int_from_file('/run/hw-management/system/lc{}_active'.format(self.slot_id))
        if state != self.current_state:
            self._re_init()
        elif seq_no != self.seq_no:
            if state == Module.STATE_ACTIVATED: # LC has been replaced, need re-initialize
                self._re_init()
        self.current_state = state
        self.seq_no = seq_no

    def _get_seq_no(self):
        try:
            seq_no = Module.redis_client.hget(Module.STATE_MODULAR_CHASSIS_SLOT_TABLE.format(self.slot_id), Module.FIELD_SEQ_NO)
            seq_no = seq_no.decode().strip()
        except Exception as e:
            seq_no = 0
        return seq_no

    def _re_init(self):
        self._thermal_list = []
        self._sfp_list = []


    ##############################################
    # THERMAL methods
    ##############################################

    def initialize_thermals(self):
        self._check_state()
        if self.current_state == Module.STATE_ACTIVATED and not self._thermal_list:
            from .thermal import initialize_linecard_thermals
            self._thermal_list = initialize_linecard_thermals(self.get_name(), self.slot_id)

    def get_num_thermals(self):
        """
        Retrieves the number of thermals available on this module

        Returns:
            An integer, the number of thermals available on this module
        """
        return DeviceDataManager.get_gearbox_count('/run/hw-management/lc{}'.format(self.slot_id))

    def get_all_thermals(self):
        """
        Retrieves all thermals available on this module

        Returns:
            A list of objects derived from ThermalBase representing all thermals
            available on this module
        """
        with self.lock:
            self.initialize_thermals()
            return self._thermal_list

    def get_thermal(self, index):
        """
        Retrieves thermal unit represented by (0-based) index <index>

        Args:
            index: An integer, the index (0-based) of the thermal to
            retrieve

        Returns:
            An object dervied from ThermalBase representing the specified thermal
        """
        with self.lock:
            self.initialize_thermals()
            thermal = None
            try:
                thermal = self._thermal_list[index]
            except IndexError:
                sys.stderr.write("THERMAL index {} out of range (0-{})\n".format(
                                index, len(self._thermal_list)-1))

            return thermal

    ##############################################
    # SFP methods
    ##############################################
    def initialize_single_sfp(self, index):
        self._check_state()
        if self.current_state == Module.STATE_ACTIVATED:
            sfp_count = self.get_num_sfps()
            if index < sfp_count:
                if not self._sfp_list:
                    self._sfp_list = [None] * sfp_count
                
                if not self._sfp_list[index]:
                    from .sfp import SFP
                    self._sfp_list[index] = SFP(index)
                    self.sfp_initialized_count += 1

    def initialize_sfps(self):
        self._check_state()
        if self.current_state == Module.STATE_ACTIVATED:
            if not self._sfp_list:
                from .sfp import SFP
                sfp_count = self.get_num_sfps()
                for index in range(sfp_count):
                    sfp_module = SFP(index)
                    self._sfp_list.append(sfp_module)
                self.sfp_initialized_count = sfp_count
            elif self.sfp_initialized_count != len(self._sfp_list):
                from .sfp import SFP
                for index in range(len(self._sfp_list)):
                    if self._sfp_list[index] is None:
                        self._sfp_list[index] = SFP(index)
                self.sfp_initialized_count = len(self._sfp_list)

    def get_num_sfps(self):
        """
        Retrieves the number of sfps available on this module

        Returns:
            An integer, the number of sfps available on this module
        """
        return DeviceDataManager.get_linecard_sfp_count(self.slot_id)

    def get_all_sfps(self):
        """
        Retrieves all sfps available on this module

        Returns:
            A list of objects derived from PsuBase representing all sfps
            available on this module
        """
        with self.lock:
            self.initialize_sfps()
            return self._sfp_list

    def get_sfp(self, index):
        """
        Retrieves sfp represented by (0-based) index <index>

        Args:
            index: An integer, the index (0-based) of the sfp to retrieve

        Returns:
            An object dervied from SfpBase representing the specified sfp
        """
        with self.lock:
            self.initialize_single_sfp(index)
            sfp = None

            try:
                sfp = self._sfp_list[index]
            except IndexError:
                sys.stderr.write("SFP index {} out of range (0-{})\n".format(
                                index, len(self._sfp_list)-1))

            return sfp
