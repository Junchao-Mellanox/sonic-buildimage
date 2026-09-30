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
Base classes, exceptions, and enums for firmware management.

Contains the abstract base class and common data structures used across
all firmware manager implementations.
"""
from __future__ import annotations

import os
import logging
import re
import subprocess
import time
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Dict, Optional, Tuple, Any
from multiprocessing import Process, Queue
from .platform_utils import run_command
from . import vmod_manager

EXIT_SUCCESS = 0
EXIT_FAILURE = 1
FW_ALREADY_UPDATED_FAILURE = 2
FW_UPGRADE_IS_REQUIRED = 10


class FirmwareManagerError(Exception):
    """Base exception for firmware manager errors."""
    pass


class FirmwareUpgradeError(Exception):
    """Exception raised when firmware upgrade fails."""
    pass


class FirmwareUpgradePartialError(Exception):
    """Exception raised when some ASIC upgrades fail."""
    pass


class UpgradeStatusType(Enum):
    """Enum for firmware upgrade status types."""
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


class UpgradeStatus:
    """Status information for firmware upgrade operations."""

    def __init__(self, asic_index: int, status: UpgradeStatusType, message: str = "",
                 current_version: str = "", available_version: str = "", pci_id: str = None):
        self.asic_index = asic_index
        self.status = status
        self.message = message
        self.current_version = current_version
        self.available_version = available_version
        self.pci_id = pci_id
        self.timestamp = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for inter-process communication."""
        return {
            'asic_index': self.asic_index,
            'status': self.status.value,
            'message': self.message,
            'current_version': self.current_version,
            'available_version': self.available_version,
            'pci_id': self.pci_id,
            'timestamp': self.timestamp
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'UpgradeStatus':
        """Create from dictionary for inter-process communication."""
        status = cls(
            asic_index=data['asic_index'],
            status=UpgradeStatusType(data['status']),
            message=data['message'],
            current_version=data['current_version'],
            available_version=data['available_version'],
            pci_id=data.get('pci_id')
        )
        status.timestamp = data.get('timestamp')
        return status


class FirmwareManagerBase(Process):
    """Base firmware manager class for a single ASIC, running in its own process."""

    def __init__(self, asic_index: int, pci_id: str,
                 fw_bin_path: str = None, verbose: bool = False, clear_semaphore: bool = False,
                 asic_type: str = None, status_queue: Queue = None):
        """
        Initialize the firmware manager for a single ASIC.

        Args:
            asic_index: Index of the ASIC this manager handles
            pci_id: PCI ID of the ASIC
            fw_bin_path: Path to firmware binaries
            verbose: Enable verbose logging
            clear_semaphore: Clear hardware semaphore before upgrade
            asic_type: Type of ASIC (e.g., 'spc1', 'bf3')
            status_queue: Queue for reporting status to parent process
        """
        super().__init__()
        self.asic_index = asic_index
        self.pci_id = pci_id
        self.fw_bin_path = fw_bin_path
        self.verbose = verbose
        self.should_clear_semaphore = clear_semaphore
        self.status_queue = status_queue

        self.asic_type = asic_type
        self.fw_file = None
        self.current_version = None
        self.available_version = None
        self.psid = None

        # vModule (Optical Engine) firmware component. Only populated on
        # CPO capable platforms whose manager supports it; stays None
        # everywhere else so non-CPO platforms behave exactly as before.
        self.vmod_supported = False
        self.current_vmod_version = None
        self.available_vmod_version = None
        self.vmod_versions_loaded = False
        self.vmod_error = None
        self.vmod_bank_versions = []

        self.logger = logging.getLogger()

        self._initialize_asic()

    def _initialize_asic(self) -> None:
        """Initialize ASIC-specific information during object creation."""
        try:
            if not self.asic_type:
                raise FirmwareManagerError(f"ASIC type not provided for ASIC {self.asic_index}")

            # Validate ASIC type is supported by this manager
            if self.asic_type not in self.get_asic_type_map().values():
                raise FirmwareManagerError(f"Unsupported ASIC type '{self.asic_type}' for ASIC {self.asic_index}")

            self.fw_file = self._get_firmware_file_path(self.asic_type, self.fw_bin_path)
            if not self.fw_file or not os.path.exists(self.fw_file):
                raise FirmwareManagerError(f"Firmware file not found: {self.fw_file}")

            self.current_version, self.available_version = self._get_firmware_versions()
            if not self.current_version or not self.available_version:
                raise FirmwareManagerError(f"Could not retrieve firmware versions for ASIC {self.asic_index}")

            self.logger.info(f"ASIC {self.asic_index} initialized: {self.asic_type}, "
                             f"current: {self.current_version}, available: {self.available_version}")

            self._initialize_vmod()

        except Exception as e:
            self.logger.error(f"Initialization failed: {str(e)}")
            raise

    def run(self):
        """Main process entry point."""
        try:
            if not self.is_upgrade_required():
                self._report_status(UpgradeStatusType.SUCCESS, f"ASIC {self.asic_index} firmware is up to date")
                return

            if self.should_clear_semaphore:
                self.logger.info(f"Clearing semaphore before upgrade for ASIC {self.asic_index}")
                if not self.clear_semaphore():
                    self.logger.warning(f"Failed to clear semaphore for ASIC {self.asic_index}, continuing with upgrade")

            if self.run_firmware_update():
                self._report_status(UpgradeStatusType.SUCCESS, f"ASIC {self.asic_index} upgrade completed")
            else:
                self._report_status(UpgradeStatusType.FAILED, f"ASIC {self.asic_index} upgrade failed")

        except Exception as e:
            self._report_status(UpgradeStatusType.ERROR, f"Unexpected error: {str(e)}")

    def supports_vmod(self) -> bool:
        """
        Whether this manager handles the vModule firmware component.

        Overridden by ASIC families that carry a vModule. The base class
        returns False so that BlueField and any future manager keep today's
        single component behaviour without opting in.
        """
        return False

    def _initialize_vmod(self) -> None:
        """
        Decide whether the vModule firmware component applies to this ASIC.

        Deliberately touches no hardware. The version reads need MST started
        and are therefore deferred to _load_vmod_versions(), which the caller
        performs inside an MST session. Doing them here would run them during
        FirmwareCoordinator.__init__ - before _start_mst() - and break both
        --status and --upgrade.

        Applicability is skipped entirely unless the manager supports vModule
        *and* the platform declares CPO topology, which is what keeps
        SPC <= SPC5, BlueField and non-CPO SPC6 platforms unchanged.
        """
        if not self.supports_vmod() or not vmod_manager.is_cpo_platform(self.logger):
            self.logger.info(
                f"ASIC {self.asic_index}: vModule firmware component not applicable"
            )
            return

        self.vmod_supported = True

    def _load_vmod_versions(self) -> None:
        """
        Read the vModule firmware versions, once, caching the result.

        Requires MST to be started. Failure is recorded and logged as an ERROR
        but never raised: per the design, an unreadable or absent vModule
        component must still leave ASIC_FW evaluated and burned if needed,
        rather than aborting the whole run.
        """
        if not self.vmod_supported or self.vmod_versions_loaded:
            return

        try:
            self.vmod_bank_versions = vmod_manager.get_vmod_bank_versions(
                logger=self.logger, asic_index=self.asic_index)
            self.current_vmod_version = (self.vmod_bank_versions[0]
                                         if self.vmod_bank_versions else None)
            self.available_vmod_version = vmod_manager.get_available_vmod_version(
                self.fw_file, self.psid, logger=self.logger)
        except Exception as e:
            # Mark loaded only after the reads, and always leave an explicit
            # error - otherwise a raising read would leave both versions None
            # with no error, which compares 'equal' and silently reports the
            # component as up to date.
            self.vmod_versions_loaded = True
            self.vmod_error = f"vModule firmware query failed: {e}"
            self.logger.error(f"ASIC {self.asic_index}: {self.vmod_error}")
            return

        self.vmod_versions_loaded = True

        if not self.current_vmod_version:
            self.vmod_error = (
                f"unable to read the current vModule firmware version on a "
                f"CPO capable platform"
            )
        elif not self.available_vmod_version:
            self.vmod_error = (
                f"no VMOD component found in {self.fw_file} on a CPO capable "
                f"platform"
            )

        if self.vmod_error:
            self.logger.error(
                f"ASIC {self.asic_index}: {self.vmod_error}; "
                f"ASIC firmware is still evaluated."
            )
            return

        self.logger.info(
            f"ASIC {self.asic_index} vModule firmware: "
            f"current: {self.current_vmod_version}, "
            f"available: {self.available_vmod_version}"
        )

    def is_vmod_upgrade_required(self) -> bool:
        """
        Check whether the vModule firmware differs from the image MFA.

        Returns:
            False when the component is not applicable to this ASIC.
        """
        if not self.vmod_supported:
            return False
        self._load_vmod_versions()
        if self.vmod_error:
            # The component state is unknown; do not let that force a burn,
            # and do not let it block the ASIC firmware decision either.
            return False

        # A burn writes the inactive bank and only becomes the running image
        # at the next ASIC reset. Treating the image as missing during that
        # window would re-burn it on every boot, so a match in *either* bank
        # counts as present.
        for version in (self.vmod_bank_versions or [self.current_vmod_version]):
            if not vmod_manager.vmod_versions_differ(
                    version, self.available_vmod_version):
                return False
        return True

    def get_component_versions(self) -> List[Dict[str, Any]]:
        """
        Per-component version view, used by --status and --dry-run reporting.

        Returns:
            One dict per applicable component with keys 'name', 'current',
            'available' and 'upgrade_needed'.
        """
        components = [{
            'name': vmod_manager.COMPONENT_ASIC_FW,
            'current': self.current_version,
            'available': self.available_version,
            # Byte-identical comparison, unchanged from the pre-CPO release
            # (AF-7). Deliberately NOT normalized: OE_FW needs normalization
            # because MFT renders vModule versions inconsistently, but applying
            # it to ASIC_FW would change upgrade decisions on every existing
            # NVIDIA platform.
            'upgrade_needed': self.current_version != self.available_version,
            'error': None,
        }]
        if self.vmod_supported:
            upgrade_needed = self.is_vmod_upgrade_required()
            components.append({
                'name': vmod_manager.COMPONENT_OE_FW,
                'current': self.current_vmod_version,
                'available': self.available_vmod_version,
                'upgrade_needed': upgrade_needed,
                'error': self.vmod_error,
            })
        return components

    def is_upgrade_required(self) -> bool:
        """
        Check if firmware upgrade is required for this ASIC.

        A single combined MFA carries both ASIC_FW and OE_FW, so any
        component differing from the image triggers one combined burn.

        Returns:
            True if upgrade is required, False otherwise

        Raises:
            FirmwareManagerError: If the device's security state cannot be
                determined (so the decision is surfaced to the caller rather
                than silently defaulting to "no upgrade").
        """
        # Byte-identical, as before CPO support (AF-7). See get_component_versions.
        if self.current_version != self.available_version:
            return True
        if self.is_vmod_upgrade_required():
            self.logger.info(
                f"ASIC {self.asic_index}: vModule firmware differs "
                f"(current: {self.current_vmod_version}, "
                f"available: {self.available_vmod_version}); "
                f"firmware upgrade is required."
            )
            return True
        is_prod_image_dev_capable = self._has_prod_image_dev_capable_device()
        if is_prod_image_dev_capable is None:
            raise FirmwareManagerError(
                f"Unable to determine security attributes for {self.pci_id}; "
                f"cannot decide whether firmware upgrade is required."
            )
        return is_prod_image_dev_capable

    def _has_prod_image_dev_capable_device(self) -> Optional[bool]:
        """
        Check whether the device exposes the 'prod_image_dev_capable_device'
        security attribute via 'flint -d <pci_id> q full'. When this attribute
        is set, the firmware must be re-burned to transition the device out of
        the dev-capable state, even if versions already match.

        Devices that do not advertise security attributes at all (no
        'Security Attributes:' line in flint output) are treated as normal
        and return False.

        Returns:
            True if the attribute is present, False if it is not present
            (including devices that do not expose security attributes at all),
            None if the security state could not be determined due to a
            flint query failure or unexpected exception.
        """
        try:
            cmd = ['/usr/bin/flint', '-d', self.pci_id, 'q', 'full']
            result = self._run_command(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                self.logger.warning(
                    f"flint query full failed for {self.pci_id} "
                    f"(rc={result.returncode}): {result.stderr}"
                )
                return None

            match = re.search(r'^Security Attributes:\s*(.+)$', result.stdout, re.MULTILINE)
            if not match:
                return False
            tokens = {t.strip() for t in match.group(1).split(',')}
            if 'prod_image_dev_capable_device' in tokens:
                self.logger.info(
                    f"ASIC {self.asic_index} ({self.pci_id}) has "
                    f"'prod_image_dev_capable_device' security attribute; "
                    f"firmware upgrade is required."
                )
                return True
            return False
        except Exception as e:
            self.logger.warning(
                f"Failed to query security attributes for {self.pci_id}: {e}"
            )
            return None

    def _report_status(self, status: UpgradeStatusType, message: str = ""):
        """Report status to parent process."""
        if self.status_queue:
            upgrade_status = UpgradeStatus(
                asic_index=self.asic_index,
                status=status,
                message=message,
                current_version=self.current_version or "",
                available_version=self.available_version or "",
                pci_id=self.pci_id
            )
            self.status_queue.put(upgrade_status.to_dict())

    def get_asic_type(self) -> Optional[str]:
        """Get the ASIC type for this platform."""
        return self.asic_type

    @classmethod
    @abstractmethod
    def get_asic_type_map(cls) -> Dict[str, str]:
        """
        Get the PCI ID to ASIC type mapping for this manager.

        Returns:
            Dictionary mapping PCI IDs (format 'vendor:device') to ASIC types
        """
        pass

    def get_firmware_filename(self) -> Optional[str]:
        """
        Get the firmware filename for this manager's ASIC type.

        Returns:
            Firmware filename or None if not found
        """
        if not self.asic_type:
            return None
        return f'fw-{self.asic_type}.mfa'

    @abstractmethod
    def _get_mst_device_type(self) -> str:
        """Get MST device type based on ASIC type (matches shell script)."""
        pass

    @abstractmethod
    def run_firmware_update(self) -> bool:
        """Run the actual firmware update command."""
        pass

    def _get_firmware_file_path(self, asic_type: str, fw_bin_path: str = None) -> Optional[str]:
        """Get the firmware file path for the given ASIC type."""
        fw_filename = self.get_firmware_filename()
        if not fw_filename:
            return None

        if fw_bin_path:
            return os.path.join(fw_bin_path, fw_filename)
        else:
            return os.path.join('/etc/mlnx', fw_filename)

    def _query_firmware_versions(self) -> Tuple[str, str]:
        """
        Query firmware versions without retry logic.

        Returns:
            Tuple of (current_version, available_version)

        Raises:
            FirmwareManagerError: If query fails or versions cannot be retrieved
        """
        cmd = ['mlxfwmanager', '--query-format', 'XML', '-d', self.pci_id]
        env = self._get_env()
        result = self._run_command(cmd, env=env, capture_output=True, text=True)

        output = result.stdout or ''
        error_output = result.stderr or ''
        stderr_suffix = f", stderr={error_output}" if error_output else ""

        if result.returncode != 0:
            raise FirmwareManagerError(
                f"Query returned non-zero exit code: stdout={output}{stderr_suffix}")

        # Extract XML block from potentially verbose debug output
        xml_start = output.find('<Devices>')
        xml_end = output.find('</Devices>')
        if xml_start < 0 or xml_end < 0:
            raise FirmwareManagerError(
                f"No XML device data found in query output: stdout={output}{stderr_suffix}")

        xml_str = output[xml_start:xml_end + len('</Devices>')]
        root = ET.fromstring(xml_str)
        current_version = root.find('.//Device/Versions/FW').get('current')
        psid = root.find('.//Device').get('psid')
        self.psid = psid

        if not current_version or current_version == '--' or not psid:
            raise FirmwareManagerError(
                f"Version or PSID not found in response: stdout={output}{stderr_suffix}")

        available_version = self._get_available_firmware_version(psid)
        return current_version, available_version

    def _get_firmware_versions(self) -> Tuple[Optional[str], Optional[str]]:
        """Get current and available firmware versions with exponential back off retry."""
        max_wait = 60
        delay = 1
        max_delay = 16
        start_time = time.monotonic()
        attempt = 0

        while True:
            attempt += 1
            try:
                return self._query_firmware_versions()
            except Exception as e:
                elapsed = time.monotonic() - start_time
                remaining = max_wait - elapsed

                if remaining <= 0:
                    self.logger.error(
                        f"Failed to get firmware versions for {self.pci_id} "
                        f"after {attempt} attempts ({elapsed:.0f}s elapsed): {str(e)}"
                    )
                    return None, None

                sleep_time = min(delay, remaining)
                error_str = str(e)

                if 'rc = 523' in error_str:
                    prefix = f"ASIC not ready for {self.pci_id} (rc=523)"
                else:
                    prefix = f"Unable to get firmware versions for {self.pci_id} (attempt {attempt})"

                self.logger.info(
                    f"{prefix}: {error_str} "
                    f"(retrying in {sleep_time:.0f}s, {remaining:.0f}s remaining)"
                )

                time.sleep(sleep_time)
                delay = min(delay * 2, max_delay)

    def get_psid(self) -> Optional[str]:
        """
        The device's PSID, read from hardware and cached.

        Needed by the manual burn to classify the supplied MFA (OF-9) before
        writing anything.
        """
        if self.psid:
            return self.psid
        try:
            self.get_current_firmware_version()
        except Exception as e:
            self.logger.error(f"Failed to read PSID for ASIC {self.asic_index}: {e}")
        return self.psid

    def burn_image(self, mfa_file: str) -> bool:
        """
        Burn an operator-supplied MFA to this ASIC (OF-7).

        Runs the same code path as the automatic flow - including the
        'already staged' handling - differing only in which image is written.
        The caller is responsible for having an MST session active; without
        one mlxfwmanager falls back to a register-access burn that is several
        times slower.
        """
        previous = self.fw_file
        self.fw_file = mfa_file
        try:
            return self.run_firmware_update()
        finally:
            self.fw_file = previous

    @abstractmethod
    def _get_available_firmware_version(self, psid: str) -> Optional[str]:
        """Get available firmware version using platform-specific method."""
        pass

    def _get_env(self) -> dict:
        """
        Get environment variables including MFT diagnosis flags if verbose mode is enabled.

        Returns:
            Dictionary of environment variables
        """
        env = os.environ.copy()
        if self.verbose:
            env['FLASH_ACCESS_DEBUG'] = '1'
            env['FW_COMPS_DEBUG'] = '1'
        return env

    def _run_command(self, cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
        """
        Execute a subprocess command with automatic logging.

        Args:
            cmd: Command and arguments as a list
            **kwargs: Additional arguments to pass to subprocess.run

        Returns:
            CompletedProcess instance from subprocess.run
        """
        return run_command(cmd, logger=self.logger, **kwargs)

    def clear_semaphore(self) -> bool:
        """
        Clear hardware semaphore for this ASIC.

        Returns:
            True if successful, False otherwise
        """
        try:
            self.logger.info(f"Clearing semaphore for device {self.pci_id}")
            cmd = ['/usr/bin/flint', '-d', self.pci_id, '--clear_semaphore']
            result = self._run_command(cmd, capture_output=True, text=True, check=True)
            self.logger.info(f"Successfully cleared semaphore for {self.pci_id}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to clear semaphore for {self.pci_id}: {e.stderr}")
            return False
        except Exception as e:
            self.logger.error(f"Failed to clear semaphore for ASIC {self.asic_index}: {e}")
            return False
