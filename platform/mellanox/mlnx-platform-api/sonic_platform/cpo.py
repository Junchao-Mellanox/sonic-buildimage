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

import os
from collections.abc import Iterator

from sonic_py_common import multi_asic
from sonic_py_common.logger import Logger

from sonic_platform_base.sfp_base import SfpBase
from sonic_platform_base.sonic_xcvr.fields import consts, cdb_consts
from sonic_platform_base.sonic_xcvr.cpo.cpo_base import (
    CpoBase,
    CpoFwOpsEntry,
    CpoHardwareInfo,
    OeId,
)

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

CPO_TYPE = "CPO"

logger = Logger()


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

    CDB_TARGET_DEFAULT = 0
    CDB_TARGET_ELS = 1

    # Filled in by Chassis.get_all_cpos(); resolve_oe_fw_ops_target() walks it
    # to find the even-vModule sibling that owns the pair's firmware.
    global_cpo_list = None

    # The image most recently downloaded to each of the OE's two banks, so a
    # repeated request for the same file is recognised and skipped.
    oe_fw_bank = [None, None]

    def __init__(self, sfp_index, bank_id, oe_id, els_id, asic_id='asic0'):
        hardware_id = CpoHardwareInfo(oe_id=OeId.NVIDIA_SPC6_CPO, elsfp_id=None)
        asic_index = multi_asic.get_asic_index_from_namespace(asic_id)
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
        if oe_result is None:
            # Nothing to enrich. A port whose engine cannot be read reports
            # nothing at all, rather than a laser-source-only answer that would
            # look like a healthy port which had lost most of its fields.
            return None
        try:
            els_result = getattr(self.elsfp.get_api(), getter_name)()
        except Exception as e:
            # The engine's half still stands on its own, and this runs on every
            # DOM cycle, so stay quiet about an unreachable laser source.
            logger.log_debug(f'{self}: no laser source half for {getter_name}: {e}')
            return oe_result
        if not els_result:
            return oe_result
        merged = dict(els_result)
        merged.update(oe_result)
        return merged

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

    def set_cdb_target(self, cdb_target):
        """
        Set CDB target to perform CDB commands on a ELS target and not on default module target which is 0.
        """
        api = self.get_xcvr_api()
        if not api:
            logger.log_error(f'Failed to get api object for SFP {self.sdk_index}, probably module EEPROM is not ready')
            return False

        # CMIS Module Level Control (Lower page 00h, byte 26h). Bit0 selects target (0 = default).
        module_ctrl = api.xcvr_eeprom.read(consts.MODULE_LEVEL_CONTROL)
        if module_ctrl is None:
            logger.log_error(f'Failed to read {consts.MODULE_LEVEL_CONTROL} for SFP {self.sdk_index}')
            return False

        new_module_ctrl = (module_ctrl & 0xFE) | (int(cdb_target) & 0x1)
        if new_module_ctrl == module_ctrl:
            return True

        if not api.xcvr_eeprom.write(consts.MODULE_LEVEL_CONTROL, new_module_ctrl):
            logger.log_error(
                f'Failed to write {consts.MODULE_LEVEL_CONTROL} for SFP {self.sdk_index}: '
                f'old=0x{module_ctrl:02x} new=0x{new_module_ctrl:02x}'
            )
            return False

        return True

    def _get_fw_component_api(self, fw_op_entry: CpoFwOpsEntry):
        if fw_op_entry.component_class == 'OpticalEngine':
            cpo = self.resolve_oe_fw_ops_target()
            cpo.set_cdb_target(0)
            return cpo.oe.get_api()
        if fw_op_entry.component_class == 'ExternalLaserSource':
            self.set_cdb_target(1)
            return self.elsfp.get_api()
        return None

    def resolve_oe_fw_ops_target(self):
        """The port whose optical engine serves this one's firmware operations.

        vModules come in pairs and the even one of a pair owns the firmware for
        both, so an odd port has to defer to its sibling. The sibling is found
        by its vModule index: the CPO list is indexed by nothing in particular
        once a platform mixes CPO and pluggable ports.
        """
        vmod_index = self.sdk_index
        if vmod_index % 2 == 0:
            return self
        for cpo in self.global_cpo_list or []:
            if cpo.sdk_index == vmod_index - 1:
                return cpo
        raise RuntimeError(f"Failed to find sibling for CPO {vmod_index}")

    def detect_duplicate_download_request(self, api, fw_op_entry):
        image_bank_index = 0
        if fw_op_entry.component_class == 'OpticalEngine':
            firmware_info = api.get_module_fw_info()
            if not firmware_info or firmware_info.get("status") is not True:
                reason = firmware_info.get("info") if firmware_info else None
                raise RuntimeError(reason or "failed to get firmware information")
            firmware_info = firmware_info.get("result")
            if not firmware_info:
                raise RuntimeError("failed to get firmware information")
            inactive_firmware = firmware_info[9]
            firmare_a = firmware_info[0]

            if inactive_firmware == firmare_a:
                image_bank_index = 0
                if fw_op_entry.file_path == self.oe_fw_bank[0]:
                    return True, image_bank_index
            else:
                image_bank_index = 1
                if fw_op_entry.file_path == self.oe_fw_bank[1]:
                    return True, image_bank_index
        return False, image_bank_index

    def update_oe_fw_bank(self, image_bank_index, fw_op_entry):
        if fw_op_entry.component_class == 'OpticalEngine':
            if image_bank_index == 0:
                self.oe_fw_bank[0] = fw_op_entry.file_path
            else:
                self.oe_fw_bank[1] = fw_op_entry.file_path

    @staticmethod
    def _report_fw_failure(
            operation: str,
            fw_op_entry: CpoFwOpsEntry,
            reason: str) -> None:
        """Print and log a CPO firmware operation failure."""
        message = (
            f"CPO firmware {operation} failed: "
            f"module={fw_op_entry.cpo_module_index}, "
            f"component={fw_op_entry.component_class}, "
            f"index={fw_op_entry.component_index}: {reason}"
        )
        print(message)
        logger.log_error(message)

    def download_firmware(
            self, fw_op_entry: CpoFwOpsEntry) -> Iterator[int]:
        """
        Download firmware to a CPO component using the standard CMIS CDB flow.

        Args:
            fw_op_entry: A CPO firmware operation entry containing the image
                path selected by the package matching stage.

        Returns:
            An iterator yielding the number of bytes written for each
            successfully downloaded block.

        Raises:
            RuntimeError: If validation or a CMIS CDB operation fails.
        """
        if not fw_op_entry.file_path:
            reason = "firmware image path is missing"
            self._report_fw_failure(
                "download", fw_op_entry, reason
            )
            raise RuntimeError(reason)

        if not os.path.isfile(fw_op_entry.file_path):
            reason = (
                f"firmware image file does not exist: {fw_op_entry.file_path}"
            )
            self._report_fw_failure(
                "download", fw_op_entry, reason
            )
            raise RuntimeError(reason)

        api = self._get_fw_component_api(fw_op_entry)
        if api is None:
            reason = "unsupported component class"
            self._report_fw_failure(
                "download", fw_op_entry, reason
            )
            raise RuntimeError(reason)

        detect_duplicate, image_bank_index = self.detect_duplicate_download_request(api, fw_op_entry)
        if detect_duplicate:
            return

        fw_features = api.get_module_fw_mgmt_feature()
        if not fw_features or fw_features.get("status") is not True:
            info = fw_features.get("info") if fw_features else None
            reason = info or "failed to get firmware management features"
            self._report_fw_failure(
                "download", fw_op_entry, reason
            )
            raise RuntimeError(reason)

        feature = fw_features.get("feature")
        if not isinstance(feature, (list, tuple)) or len(feature) != 5:
            reason = "invalid firmware management features"
            self._report_fw_failure(
                "download", fw_op_entry, reason
            )
            raise RuntimeError(reason)

        start_lpl_size, max_block_size, lpl_only, _, _ = feature
        result, info = api.module_fw_start_download(fw_op_entry.file_path)
        if result is not True:
            reason = info or "CDB start download command failed"
            self._report_fw_failure("download", fw_op_entry, reason)
            raise RuntimeError(reason)

        block_size = (
            cdb_consts.LPL_MAX_PAYLOAD_SIZE
            if lpl_only
            else max_block_size
        )
        with open(fw_op_entry.file_path, "rb") as firmware:
            firmware.seek(start_lpl_size)
            address = 0
            while chunk := firmware.read(block_size):
                if lpl_only:
                    status = api.cdb_lpl_block_write(address, chunk)
                else:
                    status = api.cdb_epl_block_write(address, chunk)
                if status != 1:
                    reason = f"CDB block write failed at offset {address}"
                    self._report_fw_failure(
                        "download", fw_op_entry, reason
                    )
                    raise RuntimeError(reason)
                address += len(chunk)
                yield len(chunk)

        result, info = api.module_fw_complete_download()
        if result is not True:
            reason = info or "CDB complete download command failed"
            self._report_fw_failure("download", fw_op_entry, reason)
            raise RuntimeError(reason)
        else:
            self.update_oe_fw_bank(image_bank_index, fw_op_entry)

    def run_firmware(
            self, fw_op_entry: CpoFwOpsEntry, mode: int = 0x01) -> bool:
        """
        Run staged firmware for a CPO component.

        Args:
            fw_op_entry: A CPO firmware operation entry.
            mode: CMIS CDB firmware run mode. Defaults to an attempted hitless
                reset to the inactive image.

        Returns:
            True if the operation succeeds, otherwise False.
        """
        api = self._get_fw_component_api(fw_op_entry)
        if api is None:
            self._report_fw_failure(
                "run", fw_op_entry, "unsupported component class"
            )
            return False

        result, info = api.module_fw_run(mode)
        if result is not True:
            self._report_fw_failure(
                "run", fw_op_entry, info or "CDB run command failed"
            )
            return False
        return True

    def commit_firmware(self, fw_op_entry: CpoFwOpsEntry) -> bool:
        """
        Commit running firmware for a CPO component.

        Args:
            fw_op_entry: A CPO firmware operation entry.

        Returns:
            True if the operation succeeds, otherwise False.
        """
        api = self._get_fw_component_api(fw_op_entry)
        if api is None:
            self._report_fw_failure(
                "commit", fw_op_entry, "unsupported component class"
            )
            return False

        result, info = api.module_fw_commit()
        if result is not True:
            self._report_fw_failure(
                "commit", fw_op_entry, info or "CDB commit command failed"
            )
            return False
        return True

    def upgrade_firmware(
            self, fw_op_entry: CpoFwOpsEntry) -> Iterator[int]:
        """
        Download, run, and commit firmware for a CPO component.

        Args:
            fw_op_entry: A CPO firmware operation entry.

        Returns:
            An iterator yielding the number of bytes written for each
            successfully downloaded block.
        """
        yield from self.download_firmware(fw_op_entry)
        if not self.run_firmware(fw_op_entry):
            raise RuntimeError("firmware run failed")
        if not self.commit_firmware(fw_op_entry):
            raise RuntimeError("firmware commit failed")
