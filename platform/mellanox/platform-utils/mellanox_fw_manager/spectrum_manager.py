#!/usr/bin/env python3
# SPDX-FileCopyrightText: NVIDIA CORPORATION & AFFILIATES
# Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
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

"""
Spectrum ASIC firmware manager implementation.

Handles firmware operations specific to Spectrum ASICs using mlxfwmanager.
"""

import re
import subprocess
from typing import Dict, Optional
from .firmware_base import FirmwareManagerBase, FW_ALREADY_UPDATED_FAILURE


class SpectrumFirmwareManager(FirmwareManagerBase):
    """Firmware manager for Spectrum ASICs."""

    # PCI ID to ASIC type mapping for Spectrum devices
    ASIC_TYPE_MAP = {
        '15b3:cb84': 'SPC',
        '15b3:cf6c': 'SPC2',
        '15b3:cf70': 'SPC3',
        '15b3:cf80': 'SPC4',
        '15b3:cf82': 'SPC5',
        '15b3:cf84': 'SPC6',
    }

    _ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[A-Za-z]|[\x08\r]')
    _MLXFW_ERR_RE = re.compile(
        r'^\s*Fail\s*:|^\s*-[EW]-|MCC\s+(?:ERROR|FAIL)|\bFAIL(?:ED|URE|S)?\b|\bERROR\b',
        re.IGNORECASE,
    )
    _MLXFW_NOISE_RE = re.compile(
        r'^\s*(user/.*\.(?:cpp|c|h):\d+|\[FLASH_ACCESS_DEBUG\]|-D-|controlFsm\s*:)',
        re.IGNORECASE,
    )

    # 'The firmware image was already updated on flash, pending reset.'
    #
    # mlxfwmanager returns rc=2 for this, but it is a success: the image is
    # staged in flash and activates on the next ASIC reset. Observed on a
    # Chameleon6S whenever the target image is burned while it is already
    # staged - including the vModule component, whose own banks keep reporting
    # the previous version until that reset propagates the new image.
    _MLXFW_ALREADY_STAGED_RE = re.compile(
        r'already\s+updated\s+on\s+flash|pending\s+reset', re.IGNORECASE)

    # ASIC families that carry a vModule (Optical Engine) firmware component
    # inside the same combined MFA as the ASIC firmware.
    VMOD_CAPABLE_ASIC_TYPES = {'SPC6'}

    def supports_vmod(self) -> bool:
        """Whether this ASIC family carries a vModule firmware component."""
        return self.asic_type in self.VMOD_CAPABLE_ASIC_TYPES

    @classmethod
    def get_asic_type_map(cls) -> Dict[str, str]:
        """
        Get the PCI ID to ASIC type mapping for Spectrum devices.

        Returns:
            Dictionary mapping PCI IDs to ASIC types
        """
        return cls.ASIC_TYPE_MAP

    def _get_mst_device_type(self) -> str:
        """Get MST device type for Spectrum ASICs."""
        return "Spectrum"

    @classmethod
    def _mlxfwmanager_detail(cls, result: subprocess.CompletedProcess, max_len: int = 1024) -> str:
        """Extract a syslog-safe error string from an mlxfwmanager result."""
        stderr = cls._ANSI_ESCAPE_RE.sub('', (result.stderr or "")).strip()
        if stderr:
            return stderr

        stdout = cls._ANSI_ESCAPE_RE.sub('', (result.stdout or "")).strip()
        if not stdout:
            return ""

        seen, picked = set(), []
        for line in stdout.splitlines():
            line = line.strip()
            if not line or line in seen or cls._MLXFW_NOISE_RE.search(line):
                continue
            if cls._MLXFW_ERR_RE.search(line):
                seen.add(line)
                picked.append(line)

        if picked:
            detail = " | ".join(picked)
            return detail if len(detail) <= max_len else detail[:max_len].rstrip() + " ...[truncated]"
        return stdout if len(stdout) <= max_len else "...[truncated]" + stdout[-max_len:]

    @classmethod
    def _is_already_staged(cls, result: subprocess.CompletedProcess) -> bool:
        """
        Whether mlxfwmanager is reporting 'already updated on flash'.

        That is a success, not a failure: the firmware is in flash and becomes
        active at the next ASIC reset. Returning False here would turn a
        completed burn into a unit failure at boot.
        """
        output = f"{result.stdout or ''}\n{result.stderr or ''}"
        return bool(cls._MLXFW_ALREADY_STAGED_RE.search(output))

    def _get_available_firmware_version(self, psid: str) -> Optional[str]:
        """Get available firmware version for Spectrum ASICs using mlxfwmanager."""
        try:
            cmd = ['mlxfwmanager', '--list-content', '-i', self.fw_file, '-d', self.pci_id]
            result = self._run_command(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.error(f"Failed to get available firmware version for Spectrum ASICs using mlxfwmanager: {result.returncode}")
                return None

            lines = result.stdout.strip().split('\n')
            for line in lines:
                if psid in line:
                    parts = line.split()
                    if len(parts) >= 4:
                        return parts[3]

            self.logger.error(f"No firmware version found for PSID {psid} in mlxfwmanager output")
            return None
        except Exception as e:
            self.logger.error(f"Failed to get available firmware version for Spectrum ASICs using mlxfwmanager: {e}")
            return None

    def run_firmware_update(self) -> bool:
        """Run the actual firmware update command for Spectrum ASICs."""
        try:
            cmd = ['mlxfwmanager', '-u', '-f', '-y', '-d', self.pci_id, '-i', self.fw_file]
            env = self._get_env()

            result = self._run_command(cmd, env=env, capture_output=True, text=True)

            if result.returncode == FW_ALREADY_UPDATED_FAILURE:
                # The reactivation attempt is kept unconditionally: a device
                # that genuinely needs its image reactivated also reports
                # rc=2, and skipping straight to "success" would leave that
                # firmware inactive while claiming the burn completed.
                self.logger.info("FW reactivation is required. Reactivating and updating FW ...")
                reactivate_cmd = ['flint', '-d', self.pci_id, 'ir']
                reactivate_result = self._run_command(reactivate_cmd, capture_output=True, text=True)
                if reactivate_result.returncode != 0:
                    self.logger.warning(f"FW reactivation failed with return code {reactivate_result.returncode}: {reactivate_result.stderr}")

                result = self._run_command(cmd, env=env, capture_output=True, text=True)

                # After reactivation the image may legitimately still be
                # reported as already staged. Treating that as a failure made
                # mlnx-fw-manager exit 1, and syncd.service has
                # Requires=mlnx-fw-manager.service - so a successful,
                # already-staged burn could stop syncd from starting.
                if (result.returncode == FW_ALREADY_UPDATED_FAILURE
                        and self._is_already_staged(result)):
                    return True

            if result.returncode != 0:
                self.logger.error(f"Failed to update firmware for Spectrum ASICs with return code {result.returncode}: {self._mlxfwmanager_detail(result)}")
                return False

            return True
        except Exception as e:
            self.logger.error(f"Failed to run firmware update for Spectrum ASICs: {e}")
            return False
