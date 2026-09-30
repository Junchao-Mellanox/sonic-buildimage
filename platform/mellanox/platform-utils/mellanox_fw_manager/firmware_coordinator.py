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
Firmware coordinator for managing multiple ASIC firmware operations.

Handles coordination of firmware upgrades across multiple ASICs,
including process management, error handling, and SONiC image integration.
"""
import os
import logging
import subprocess
from contextlib import contextmanager
from typing import List
from multiprocessing import Queue

from .firmware_base import (
    FirmwareManagerError, FirmwareUpgradeError, FirmwareUpgradePartialError,
    UpgradeStatus, UpgradeStatusType
)
from .platform_utils import _detect_platform, run_command
from .asic_manager import AsicManager
from .bluefield_manager import BluefieldFirmwareManager


class FirmwareCoordinator:
    """Main coordinator class that manages multiple ASIC firmware processes."""

    def __init__(self, verbose: bool = False, from_image: bool = False, clear_semaphore: bool = False,
                 ignore_mst_start_failure: bool = False):
        """
        Initialize the firmware coordinator.

        Args:
            verbose: Enable verbose logging
            from_image: If True, upgrade from next SONiC image (matches shell script --upgrade)
            clear_semaphore: Clear hardware semaphore before upgrade
        """
        self.verbose = verbose
        self.from_image = from_image
        self.clear_semaphore = clear_semaphore
        self.ignore_mst_start_failure = ignore_mst_start_failure
        self.logger = logging.getLogger()

        try:
            platform = _detect_platform()
        except Exception as e:
            self.logger.error(f"Failed to detect platform: {e}")
            raise

        self.asic_manager = AsicManager(platform)

        from .fw_manager import create_firmware_manager
        num_asics = self.get_asic_count()
        pci_ids = self.get_asic_pci_ids()
        asic_type = self.asic_manager.get_asic_type()
        self.managers = []

        if self.from_image:
            try:
                self.fw_bin_path = self._get_image_firmware_path()
            except Exception as e:
                self.logger.error(f"Failed to get firmware path from image: {e}")
                raise
        else:
            self.fw_bin_path = "/etc/mlnx"

        for asic_index in range(num_asics):
            pci_id = pci_ids[asic_index] if asic_index < len(pci_ids) else None

            try:
                manager = create_firmware_manager(
                    asic_index=asic_index,
                    pci_id=pci_id,
                    asic_type=asic_type,
                    fw_bin_path=self.fw_bin_path,
                    verbose=self.verbose,
                    clear_semaphore=self.clear_semaphore
                )

                self.managers.append(manager)

            except FirmwareManagerError as e:
                self.logger.error(f"Failed to initialize ASIC {asic_index}: {e}")
                raise

        self.logger.info(f"Initialized firmware coordinator with {len(self.managers)} ASIC(s) and image from {self.fw_bin_path}")

    def _start_mst(self) -> bool:
        """
        Start the MST driver.

        Returns:
            True if MST was successfully started, False if startup failed and
            ignore_mst_start_failure is set.

        Raises:
            FirmwareManagerError: If MST start fails and ignore_mst_start_failure is False.
        """
        self.logger.info("Starting MST with i2cdev")
        result = run_command(['/usr/bin/mst', 'start', '--with_i2cdev'],
                             logger=self.logger, capture_output=True, text=True)
        if result.returncode != 0:
            msg = f"MST start failed (rc={result.returncode}): {result.stderr}"
            if self.ignore_mst_start_failure:
                self.logger.warning(f"{msg} - continuing (ignore-mst-start-failure set)")
                return False
            raise FirmwareManagerError(msg)
        return True

    @contextmanager
    def mst_session(self):
        """
        Run a block with the MST driver started.

        The vModule firmware version reads address an mst device rather than a
        PCI BDF, so every path that inspects firmware versions - not just the
        burn - has to hold an MST session. Without this, 'mlnx-fw-manager
        --status' would fail on a CPO platform and syncd.sh would refuse to
        start syncd.

        **The session is deliberately not stopped on exit.** 'mst stop' unloads
        mst_pci/mst_pciconf unconditionally - measured on hardware, it succeeds
        even while another process holds a /dev/mst device open, taking every
        mst device away from it. Since syncd.sh runs 'mlnx-fw-manager --status'
        on every syncd start (including a crash-restart, and once per namespace
        in parallel on multi-ASIC), a stop here could pull the driver out from
        under a concurrent firmware burn, or out from under a sibling
        --status which would then fail and block syncd from starting.

        Leaving the driver loaded is safe and self-limiting: 'mst start' is
        idempotent, syncd restarts cleanly with the modules resident (verified
        on hardware), and the syncd start path tears them down on its own when
        it rebinds the PCI device - so nothing accumulates across a restart.

        There is deliberately no counterpart that stops it: a helper sitting
        here uncalled is an invitation to wire it into a 'finally' and undo
        all of the above.
        """
        yield self._start_mst()

    def needs_vmod_versions(self) -> bool:
        """
        Whether any ASIC carries a vModule firmware component.

        Used to avoid starting MST on platforms that have no vModule -
        SPC <= SPC5 and BlueField must keep behaving exactly as they do today,
        including not paying for an MST start during a version check.
        """
        # Compared against True explicitly: a manager that has not opted in
        # must read as "no vModule" even when its attribute is a placeholder.
        return any(getattr(manager, 'vmod_supported', False) is True
                   for manager in self.managers)

    def load_vmod_versions(self) -> None:
        """
        Populate the vModule versions for every ASIC.

        Must be called inside an mst_session(). The underlying available
        version read is memoized per (MFA, PSID), so the fwpkg extract happens
        once for the whole system rather than once per ASIC.
        """
        for manager in self.managers:
            try:
                manager._load_vmod_versions()
            except Exception as e:
                self.logger.error(
                    f"Failed to load vModule versions for ASIC "
                    f"{manager.asic_index}: {e}"
                )

    def upgrade_firmware(self) -> None:
        """
        Upgrade firmware for all ASICs using separate processes.

        Starts the MST driver before upgrading and stops it on exit (whether
        successful or not). MST start failure aborts the upgrade unless
        ignore_mst_start_failure is set.

        Raises:
            FirmwareManagerError: If MST start fails and ignore_mst_start_failure is False.
            FirmwareUpgradeError: If all ASIC upgrades fail
            FirmwareUpgradePartialError: If some ASIC upgrades fail
        """
        with self.mst_session():
            if self.needs_vmod_versions():
                self.load_vmod_versions()
            self._upgrade_firmware_impl()

    def _upgrade_firmware_impl(self) -> None:
        """Internal upgrade implementation, called after MST has been started."""
        num_asics = len(self.managers)

        self.logger.info(f"Starting firmware upgrade for {num_asics} ASIC(s) from {self.fw_bin_path}")

        processes = self.managers.copy()

        status_queue = Queue()

        for manager in processes:
            manager.status_queue = status_queue
            manager.start()

        results = {}
        success_count = 0
        failure_count = 0
        timeout_failures = set()

        # Wait for all processes with timeout (10 minutes per ASIC)
        # Firmware upgrades typically take 1-3 minutes, so 10 minutes provides a safe margin
        timeout_per_asic = 600  # seconds
        for process in processes:
            process.join(timeout=timeout_per_asic)
            if process.is_alive():
                self.logger.error(f"ASIC {process.asic_index} firmware upgrade timed out after {timeout_per_asic} seconds")
                process.terminate()
                process.join(timeout=10)  # Give it 10 seconds to terminate
                if process.is_alive():
                    self.logger.error(f"Force killing stuck process for ASIC {process.asic_index}")
                    process.kill()
                    process.join()
                timeout_failures.add(process.asic_index)
                failure_count += 1

        while not status_queue.empty():
            try:
                status_data = status_queue.get_nowait()
                status = UpgradeStatus.from_dict(status_data)
                results[status.asic_index] = status

                if status.status == UpgradeStatusType.SUCCESS:
                    success_count += 1
                elif status.status == UpgradeStatusType.FAILED or status.status == UpgradeStatusType.ERROR:
                    failure_count += 1
            except Exception as e:
                self.logger.error(f"Error processing status queue: {e}")
                break

        # If a process completed but didn't report status (not in results and not timeout), treat as failure
        for process in processes:
            if process.asic_index not in results and process.asic_index not in timeout_failures:
                self.logger.error(f"ASIC {process.asic_index} completed but did not report status, treating as failure")
                failure_count += 1

        total_failures = failure_count
        total_asics = num_asics

        self.logger.info(f"Upgrade results: {success_count} successful, {total_failures} failed")

        if total_failures == total_asics:
            self.logger.error("All ASIC upgrades failed")
            raise FirmwareUpgradeError("All ASIC upgrades failed")
        elif total_failures > 0:
            self.logger.warning(f"Some ASIC upgrades failed ({total_failures}/{total_asics})")
            raise FirmwareUpgradePartialError(f"Some ASIC upgrades failed ({total_failures}/{total_asics})")
        else:
            self.logger.info("All ASIC upgrades completed successfully")

    def check_upgrade_required(self) -> bool:
        """
        Check if firmware upgrade is required for any ASIC (dry-run functionality).

        Returns:
            True if upgrade is required, False otherwise
        """
        self.logger.info(f"Checking if firmware upgrade is required from {self.fw_bin_path}")

        if not self.needs_vmod_versions():
            # No vModule on this platform: no MST session, no behaviour change.
            return self._check_upgrade_required_impl()

        with self.mst_session():
            self.load_vmod_versions()
            return self._check_upgrade_required_impl()

    def _check_upgrade_required_impl(self) -> bool:
        """Version comparison, assuming an MST session is already held."""
        upgrade_needed = False
        for manager in self.managers:
            try:
                if manager.is_upgrade_required():
                    self.logger.info(f"Firmware upgrade is required for ASIC {manager.asic_index}")
                    upgrade_needed = True
            except Exception as e:
                self.logger.error(f"Error checking upgrade status for ASIC {manager.asic_index}: {e}")
                upgrade_needed = True

        if upgrade_needed:
            self.logger.info("Firmware upgrade is required")
        else:
            self.logger.info("Firmware is up to date")

        return upgrade_needed

    def _get_image_firmware_path(self) -> str:
        """
        Get firmware path from next SONiC image (matches shell script UpgradeFWFromImage).

        Returns:
            Path to firmware binaries from the next SONiC image
        """
        cmd = ['sonic-installer', 'list']
        result = run_command(cmd, logger=self.logger, capture_output=True, text=True)
        if result.returncode != 0:
            error_msg = f"Failed to get SONiC image list: {result.stderr if result.stderr else 'Unknown error'}"
            self.logger.error(error_msg)
            raise FirmwareUpgradeError(error_msg)

        next_image = None
        current_image = None
        for line in result.stdout.split('\n'):
            if line.startswith('Next: '):
                next_image = line.split('Next: ')[1].strip()
            elif line.startswith('Current: '):
                current_image = line.split('Current: ')[1].strip()

        if not next_image:
            raise FirmwareUpgradeError("No next SONiC image found")

        platform_fw_path = f"/host/image-{next_image.replace('SONiC-OS-', '')}/platform/fw/asic/"

        if os.path.exists(platform_fw_path):
            self.logger.info(f"Using FW binaries from {platform_fw_path}")
            return platform_fw_path
        else:
            fs_path = f"/host/image-{next_image.replace('SONiC-OS-', '')}/fs.squashfs"
            fs_mountpoint = f"/tmp/image-{next_image.replace('SONiC-OS-', '')}-fs"
            fw_bin_path = f"{fs_mountpoint}/etc/mlnx/"

            self.logger.info(f"Using FW binaries from {fw_bin_path}")

            try:
                os.makedirs(fs_mountpoint, exist_ok=True)
            except Exception as e:
                error_msg = f"Failed to create mount point directory {fs_mountpoint}: {e}"
                self.logger.error(error_msg)
                raise FirmwareUpgradeError(error_msg)

            cmd = ['mount', '-t', 'squashfs', fs_path, fs_mountpoint]
            mount_result = run_command(cmd, logger=self.logger)
            if mount_result.returncode != 0:
                error_msg = f"Failed to mount {fs_path}: {mount_result.stderr if mount_result.stderr else 'Unknown error'}"
                self.logger.error(error_msg)
                raise FirmwareUpgradeError(error_msg)

            return fw_bin_path

    def get_default_mfa_path(self) -> str:
        """Path of the MFA shipped in the installed image (--burn default)."""
        return os.path.join(self.fw_bin_path, f"fw-{self.asic_manager.get_asic_type()}.mfa")

    def select_managers(self, asic: str = 'all') -> List:
        """
        The per-ASIC managers a manual burn should act on (--asic).

        Returns every manager for 'all'. AF-12 is satisfied by construction:
        the unit is the ASIC, and there is no port-level selector.

        Raises:
            FirmwareUpgradeError: If the selector names an ASIC that does not
                exist - silently burning a different one would be worse.
        """
        if asic in (None, 'all', '__flag__'):
            return list(self.managers)
        try:
            index = int(asic)
        except (TypeError, ValueError):
            raise FirmwareUpgradeError(f"Invalid ASIC selector: {asic}")
        for manager in self.managers:
            if manager.asic_index == index:
                return [manager]
        raise FirmwareUpgradeError(
            f"ASIC {index} not found; this system has {len(self.managers)} ASIC(s)")

    def get_asic_count(self) -> int:
        """Get the number of ASICs in the system."""
        return self.asic_manager.get_asic_count()

    def get_asic_pci_ids(self) -> List[str]:
        """Get PCI IDs for all ASICs."""
        return self.asic_manager.get_asic_pci_ids()

    def reset_firmware_config(self) -> None:
        """
        Reset firmware configuration for all BlueField ASICs.

        Raises:
            FirmwareManagerError: If reset fails or no BlueField ASICs found
        """
        bluefield_managers = [m for m in self.managers if isinstance(m, BluefieldFirmwareManager)]

        if not bluefield_managers:
            raise FirmwareManagerError("No BlueField ASICs found for reset operation")

        self.logger.info(f"Resetting firmware configuration for {len(bluefield_managers)} BlueField ASIC(s)")

        success_count = 0
        failure_count = 0

        for manager in bluefield_managers:
            try:
                if manager.reset_firmware_config():
                    success_count += 1
                    self.logger.info(f"Reset successful for ASIC {manager.asic_index}")
                else:
                    failure_count += 1
                    self.logger.error(f"Reset failed for ASIC {manager.asic_index}")
            except Exception as e:
                failure_count += 1
                self.logger.error(f"Reset failed for ASIC {manager.asic_index}: {e}")

        if failure_count == len(bluefield_managers):
            raise FirmwareManagerError("All BlueField ASIC resets failed")
        elif failure_count > 0:
            self.logger.warning(f"Some BlueField ASIC resets failed ({failure_count}/{len(bluefield_managers)})")

        self.logger.info(f"Reset completed: {success_count} successful, {failure_count} failed")
