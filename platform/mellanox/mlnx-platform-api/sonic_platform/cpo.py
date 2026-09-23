#
# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
"""The port class for co-packaged optics.

A CPO port is not a pluggable one, and this module is a sibling of `sfp.py`
rather than a part of it: what the two technologies share is stated as the
mixins both import, so neither has to know the other exists.
"""

from sonic_py_common import multi_asic
from sonic_py_common.logger import Logger

from sonic_platform_base.sfp_base import SfpBase
from sonic_platform_base.sonic_xcvr.cpo.cpo_base import CpoBase, CpoHardwareInfo, OeId

from .cpo_device import NvidiaSysfsElsfp, NvidiaSysfsOe
from .device_data import DeviceDataManager
from .module_detection_flow import (
    ModuleDetectionFlow,
    STATE_FW_CONTROL,
    STATE_NOT_PRESENT,
    STATE_POWER_BAD,
    STATE_POWER_LIMIT_ERROR,
    STATE_SW_CONTROL,
)
from .module_sysfs import ModuleSysfsMixin, SFP_STATUS_INSERTED, SFP_STATUS_REMOVED
from .module_xcvr import ModuleXcvrMixin

logger = Logger()

CPO_TYPE = "CPO"


class CpoPort(ModuleXcvrMixin, ModuleSysfsMixin, CpoBase):
    """One logical port of a co-packaged optics module.

    A CPO port is not a pluggable one: there is no cage and nothing to insert,
    and its media lives in two devices - the optical engine and the external
    laser source - rather than in one module. So it takes the shared behaviour
    as mixins and its media from `CpoBase`, instead of inheriting `SFP`.

    Four logical ports share one OE, one per bank. Identity therefore lives on
    the OE and not on the port: `module_access` points there, and the three
    accessors below are views onto it rather than copies.
    """

    NUMBER_OF_BANKS = 4

    CPO_STATUS_MAP = {
        'els_control_mode_APCACC': 'ELS control mode APC/ACC',
        'els_vcc': 'ELS Vcc',
        'els_fault_code1': 'ELS Fault code (lane 1)',
        'els_warning_code1': 'ELS Warning code (lane 1)',
        'els_lane_state1': 'ELS Lane state (lane 1)',
        'els_output_fiber_checked_flag_lane1': 'ELS Output fiber checked flag (lane 1)',
        'els_lane_enable1': 'ELS Lane enable (lane 1)',
        'els_fault_code2': 'ELS Fault code (lane 2)',
        'els_warning_code2': 'ELS Warning code (lane 2)',
        'els_lane_state2': 'ELS Lane state (lane 2)',
        'els_output_fiber_checked_flag_lane2': 'ELS Output fiber checked flag (lane 2)',
        'els_lane_enable2': 'ELS Lane enable (lane 2)',
        'els_fault_code3': 'ELS Fault code (lane 3)',
        'els_warning_code3': 'ELS Warning code (lane 3)',
        'els_lane_state3': 'ELS Lane state (lane 3)',
        'els_output_fiber_checked_flag_lane3': 'ELS Output fiber checked flag (lane 3)',
        'els_lane_enable3': 'ELS Lane enable (lane 3)',
        'els_fault_code4': 'ELS Fault code (lane 4)',
        'els_warning_code4': 'ELS Warning code (lane 4)',
        'els_lane_state4': 'ELS Lane state (lane 4)',
        'els_output_fiber_checked_flag_lane4': 'ELS Output fiber checked flag (lane 4)',
        'els_lane_enable4': 'ELS Lane enable (lane 4)',
        'els_fault_code5': 'ELS Fault code (lane 5)',
        'els_warning_code5': 'ELS Warning code (lane 5)',
        'els_lane_state5': 'ELS Lane state (lane 5)',
        'els_output_fiber_checked_flag_lane5': 'ELS Output fiber checked flag (lane 5)',
        'els_lane_enable5': 'ELS Lane enable (lane 5)',
        'els_fault_code6': 'ELS Fault code (lane 6)',
        'els_warning_code6': 'ELS Warning code (lane 6)',
        'els_lane_state6': 'ELS Lane state (lane 6)',
        'els_output_fiber_checked_flag_lane6': 'ELS Output fiber checked flag (lane 6)',
        'els_lane_enable6': 'ELS Lane enable (lane 6)',
        'els_fault_code7': 'ELS Fault code (lane 7)',
        'els_warning_code7': 'ELS Warning code (lane 7)',
        'els_lane_state7': 'ELS Lane state (lane 7)',
        'els_output_fiber_checked_flag_lane7': 'ELS Output fiber checked flag (lane 7)',
        'els_lane_enable7': 'ELS Lane enable (lane 7)',
        'els_fault_code8': 'ELS Fault code (lane 8)',
        'els_warning_code8': 'ELS Warning code (lane 8)',
        'els_lane_state8': 'ELS Lane state (lane 8)',
        'els_output_fiber_checked_flag_lane8': 'ELS Output fiber checked flag (lane 8)',
        'els_lane_enable8': 'ELS Lane enable (lane 8)',
        'els_custom_mon_high_alarm': 'ELS Custom mon high alarm',
        'els_custom_mon_low_alarm': 'ELS Custom mon low alarm',
        'els_custom_mon_high_warning': 'ELS Custom mon high warning',
        'els_custom_mon_low_warning': 'ELS Custom mon low warning'
    }

    CPO_DOM_CHANNEL_FORMAT_MAP = {
        'els_laser_mpd1': 'ELS Lane1LaserMPD',
        'els_tec_voltage_laser1': 'ELS Lane1TecVoltage',
        'els_tec_health_value_laser1': 'ELS Lane1TecHealth',
        'els_health_value_laser1': 'ELS Lane1LaserHealth',
        'els_bias_current_setpoint1': 'ELS Lane1BiasCurrentSetpoint',
        'els_opt_power_setpoint1': 'ELS Lane1OptPowerSetpoint',
        'els_laser_mpd2': 'ELS Lane2LaserMPD',
        'els_tec_voltage_laser2': 'ELS Lane2TecVoltage',
        'els_tec_health_value_laser2': 'ELS Lane2TecHealth',
        'els_health_value_laser2': 'ELS Lane2LaserHealth',
        'els_bias_current_setpoint2': 'ELS Lane2BiasCurrentSetpoint',
        'els_opt_power_setpoint2': 'ELS Lane2OptPowerSetpoint',
        'els_laser_mpd3': 'ELS Lane3LaserMPD',
        'els_tec_voltage_laser3': 'ELS Lane3TecVoltage',
        'els_tec_health_value_laser3': 'ELS Lane3TecHealth',
        'els_health_value_laser3': 'ELS Lane3LaserHealth',
        'els_bias_current_setpoint3': 'ELS Lane3BiasCurrentSetpoint',
        'els_opt_power_setpoint3': 'ELS Lane3OptPowerSetpoint',
        'els_laser_mpd4': 'ELS Lane4LaserMPD',
        'els_tec_voltage_laser4': 'ELS Lane4TecVoltage',
        'els_tec_health_value_laser4': 'ELS Lane4TecHealth',
        'els_health_value_laser4': 'ELS Lane4LaserHealth',
        'els_bias_current_setpoint4': 'ELS Lane4BiasCurrentSetpoint',
        'els_opt_power_setpoint4': 'ELS Lane4OptPowerSetpoint',
        'els_laser_mpd5': 'ELS Lane5LaserMPD',
        'els_tec_voltage_laser5': 'ELS Lane5TecVoltage',
        'els_tec_health_value_laser5': 'ELS Lane5TecHealth',
        'els_health_value_laser5': 'ELS Lane5LaserHealth',
        'els_bias_current_setpoint5': 'ELS Lane5BiasCurrentSetpoint',
        'els_opt_power_setpoint5': 'ELS Lane5OptPowerSetpoint',
        'els_laser_mpd6': 'ELS Lane6LaserMPD',
        'els_tec_voltage_laser6': 'ELS Lane6TecVoltage',
        'els_tec_health_value_laser6': 'ELS Lane6TecHealth',
        'els_health_value_laser6': 'ELS Lane6LaserHealth',
        'els_bias_current_setpoint6': 'ELS Lane6BiasCurrentSetpoint',
        'els_opt_power_setpoint6': 'ELS Lane6OptPowerSetpoint',
        'els_laser_mpd7': 'ELS Lane7LaserMPD',
        'els_tec_voltage_laser7': 'ELS Lane7TecVoltage',
        'els_tec_health_value_laser7': 'ELS Lane7TecHealth',
        'els_health_value_laser7': 'ELS Lane7LaserHealth',
        'els_bias_current_setpoint7': 'ELS Lane7BiasCurrentSetpoint',
        'els_opt_power_setpoint7': 'ELS Lane7OptPowerSetpoint',
        'els_laser_mpd8': 'ELS Lane8LaserMPD',
        'els_tec_voltage_laser8': 'ELS Lane8TecVoltage',
        'els_tec_health_value_laser8': 'ELS Lane8TecHealth',
        'els_health_value_laser8': 'ELS Lane8LaserHealth',
        'els_bias_current_setpoint8': 'ELS Lane8BiasCurrentSetpoint',
        'els_opt_power_setpoint8': 'ELS Lane8OptPowerSetpoint',
    }

    CPO_DOM_MODULE_MAP = {
        'els_power_consumption': 'ELS power_consumption'
    }

    CPO_DOM_FORMAT_MAP = {**CPO_DOM_CHANNEL_FORMAT_MAP, **CPO_DOM_MODULE_MAP}

    CPO_DOM_UNIT_MAP = {
        'els_power_consumption': 'W',
        'els_laser_mpd1': 'mA',
        'els_tec_voltage_laser1': 'Volts',
        'els_tec_health_value_laser1': 'Volts',
        'els_health_value_laser1': 'Volts',
        'els_bias_current_setpoint1': 'mA',
        'els_opt_power_setpoint1': 'dBm',
        'els_laser_mpd2': 'mA',
        'els_tec_voltage_laser2': 'Volts',
        'els_tec_health_value_laser2': 'Volts',
        'els_health_value_laser2': 'Volts',
        'els_bias_current_setpoint2': 'mA',
        'els_opt_power_setpoint2': 'dBm',
        'els_laser_mpd3': 'mA',
        'els_tec_voltage_laser3': 'Volts',
        'els_tec_health_value_laser3': 'Volts',
        'els_health_value_laser3': 'Volts',
        'els_bias_current_setpoint3': 'mA',
        'els_opt_power_setpoint3': 'dBm',
        'els_laser_mpd4': 'mA',
        'els_tec_voltage_laser4': 'Volts',
        'els_tec_health_value_laser4': 'Volts',
        'els_health_value_laser4': 'Volts',
        'els_bias_current_setpoint4': 'mA',
        'els_opt_power_setpoint4': 'dBm',
        'els_laser_mpd5': 'mA',
        'els_tec_voltage_laser5': 'Volts',
        'els_tec_health_value_laser5': 'Volts',
        'els_health_value_laser5': 'Volts',
        'els_bias_current_setpoint5': 'mA',
        'els_opt_power_setpoint5': 'dBm',
        'els_laser_mpd6': 'mA',
        'els_tec_voltage_laser6': 'Volts',
        'els_tec_health_value_laser6': 'Volts',
        'els_health_value_laser6': 'Volts',
        'els_bias_current_setpoint6': 'mA',
        'els_opt_power_setpoint6': 'dBm',
        'els_laser_mpd7': 'mA',
        'els_tec_voltage_laser7': 'Volts',
        'els_tec_health_value_laser7': 'Volts',
        'els_health_value_laser7': 'Volts',
        'els_bias_current_setpoint7': 'mA',
        'els_opt_power_setpoint7': 'dBm',
        'els_laser_mpd8': 'mA',
        'els_tec_voltage_laser8': 'Volts',
        'els_tec_health_value_laser8': 'Volts',
        'els_health_value_laser8': 'Volts',
        'els_bias_current_setpoint8': 'mA',
        'els_opt_power_setpoint8': 'dBm',
    }

    def __init__(self, sfp_index, bank_id, oe_id, els_id, asic_id='asic0'):
        hardware_id = CpoHardwareInfo(oe_id=OeId.NVIDIA_SPC6_CPO, elsfp_id=None)
        asic_index = multi_asic.get_asic_index_from_namespace(asic_id)
        # Instantiating the devices is what wires their api factories, so
        # there is no api code here: get_xcvr_api() builds lazily off these.
        oe = NvidiaSysfsOe(hardware_id, bank=bank_id, sdk_index=oe_id, asic_index=asic_index)
        elsfp = NvidiaSysfsElsfp(hardware_id, bank=bank_id, sdk_index=oe_id, asic_index=asic_index)
        CpoBase.__init__(self, hardware_id, oe, elsfp)

        self.index = sfp_index + 1
        self.asic_id = asic_id
        self.els_id = els_id
        self.sfp_type = CPO_TYPE
        self._sfp_type_str = None
        self.sn = None
        from .thermal import initialize_sfp_thermal
        self._thermal_list = initialize_sfp_thermal(self)
        if DeviceDataManager.is_multi_asic_platform():
            self.namespace = asic_id
        else:
            self.namespace = multi_asic.DEFAULT_NAMESPACE
        ModuleDetectionFlow.init_detection_flow(self, oe_id)

    @property
    def module_access(self):
        """The OE owns this port's sysfs coordinates; all four banks share it."""
        return self.oe

    @property
    def sdk_index(self):
        return self.oe.sdk_index

    @property
    def oe_id(self):
        return self.oe.sdk_index

    @property
    def bank_id(self):
        return self.oe.bank

    def read_eeprom(self, offset, num_bytes):
        return self.oe.read_eeprom(offset, num_bytes)

    def _read_eeprom(self, offset, num_bytes, log_on_error=True):
        return self.oe._read_eeprom(offset, num_bytes, log_on_error)

    def write_eeprom(self, offset, num_bytes, write_buffer):
        return self.oe.write_eeprom(offset, num_bytes, write_buffer)

    def get_asic_id(self):
        return self.asic_id

    def __str__(self):
        return f'CPO port {self.index} (OE {self.oe.sdk_index} bank {self.oe.bank})'

    # The three accessors StateMachine drives an entity through. Everything
    # else about the detection flow lives in ModuleDetectionFlow.
    def get_state(self):
        """Return the current state.

        Returns:
            str: current state
        """
        return self.state

    def change_state(self, new_state):
        """Change from old state to new state

        Args:
            new_state (str): new state
        """
        self.state = new_state

    def on_action(self, action_name):
        """Called when a state machine action is executing

        Args:
            action_name (str): action name
        """
        ModuleDetectionFlow.action_table[action_name](self)

    def fill_change_event(self, port_dict):
        """One physical OE fans out to NUMBER_OF_BANKS contiguous logical
        ports starting at self.index (= bank 0 of this vModule).
        """
        if self.state == STATE_NOT_PRESENT:
            value = SFP_STATUS_REMOVED
        elif self.state == STATE_SW_CONTROL or self.state == STATE_FW_CONTROL:
            value = SFP_STATUS_INSERTED
        elif self.state == STATE_POWER_BAD or self.state == STATE_POWER_LIMIT_ERROR:
            value = str(SfpBase.SFP_ERROR_BIT_POWER_BUDGET_EXCEEDED
                        | SfpBase.SFP_STATUS_BIT_INSERTED)
        else:
            return
        for i in range(self.NUMBER_OF_BANKS):
            port_dict[self.index + i] = value

    def get_platform_specific_transceiver_status_format_map(self):
        """Retrieves the platform-specific transceiver status format map."""
        return self.CPO_STATUS_MAP.copy()

    def get_platform_specific_dom_format_map(self):
        """Retrieves platform-specific DOM format and unit maps."""
        return self.CPO_DOM_FORMAT_MAP.copy(), self.CPO_DOM_UNIT_MAP.copy()

    # This part of merging the two halves is the same as in the STATE DB Table is temporary
    # and going to be removed once the STATE DB Table is updated to support new ELS Tables.

    def _merged_halves(self, getter_name):
        """The engine's answer to `getter_name`, enriched with the laser source's.

        The getter is named once and resolved on both halves, so the two can
        never be paired up wrongly. The laser source answers with els_-prefixed
        keys only, so the two are a disjoint union in practice; the engine is
        merged last anyway, so its own fields cannot be shadowed by the second
        device even if that ever stops holding.
        """
        oe_result = getattr(super(), getter_name)()
        if not oe_result:
            logger.log_error(f'no engine half for {getter_name}')
            return None
        try:
            els_result = getattr(self.elsfp.get_api(), getter_name)()
        except Exception as e:
            logger.log_debug(f'no laser source half for {getter_name}: {e}')
            return oe_result
        if not els_result:
            return oe_result
        return {**els_result, **oe_result}

    def get_transceiver_info(self):
        return self._merged_halves('get_transceiver_info')

    def get_transceiver_status(self):
        return self._merged_halves('get_transceiver_status')

    def get_transceiver_status_flags(self):
        return self._merged_halves('get_transceiver_status_flags')

    def get_transceiver_dom_real_value(self):
        return self._merged_halves('get_transceiver_dom_real_value')

    def get_transceiver_dom_flags(self):
        return self._merged_halves('get_transceiver_dom_flags')

    def get_transceiver_threshold_info(self):
        return self._merged_halves('get_transceiver_threshold_info')
