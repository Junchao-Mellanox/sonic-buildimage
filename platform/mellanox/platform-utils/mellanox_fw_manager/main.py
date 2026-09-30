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
Command Line Interface for Mellanox Firmware Manager

Provides a command-line interface for firmware upgrade operations.
"""

import os
import sys
import time
import logging
import logging.handlers
import fcntl
import subprocess
from contextlib import contextmanager
import click
from .fw_manager import create_firmware_manager, EXIT_SUCCESS, EXIT_FAILURE, FW_UPGRADE_IS_REQUIRED, FirmwareManagerError, FirmwareUpgradeError, FirmwareUpgradePartialError
from .firmware_coordinator import FirmwareCoordinator
from .platform_utils import run_command
from . import fw_policy
from . import vmod_manager

logger = None


def setup_logging(verbose: bool = False, nosyslog: bool = False):
    """Setup global logging configuration with syslog."""
    global logger

    level = logging.DEBUG if verbose else logging.INFO

    logger = logging.getLogger()
    logger.setLevel(level)

    logger.handlers.clear()

    if not nosyslog:
        syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
        syslog_handler.setLevel(level)

        formatter = logging.Formatter('mellanox-fw-manager[%(process)d]: %(levelname)s - %(message)s')
        syslog_handler.setFormatter(formatter)

        logger.addHandler(syslog_handler)

    if verbose or nosyslog:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)


def _exit_if_qemu() -> None:
    """
    Exit if running on QEMU/SimX platform (matches shell script ExitIfQEMU).

    Raises:
        SystemExit: If QEMU platform is detected
    """
    try:
        cmd = ['lspci', '-vvv']
        result = run_command(cmd, logger=logger, capture_output=True, text=True)
        if result.returncode == 0 and 'SimX' in result.stdout:
            print("No FW upgrade for SimX platform")
            sys.exit(EXIT_SUCCESS)
    except Exception as e:
        logger.warning(f"Failed to check for QEMU platform: {e}")


def _exit_if_auto_upgrade_disabled(status: str, reset: bool = False) -> None:
    """
    Honour the automatic firmware upgrade policy.

    Once an operator has taken ownership of ASIC and vModule firmware, the
    automatic flow must get out of the way rather than fighting them once per
    boot. Nothing is burned and, for the upgrade paths, nothing is queried.

    --status is deliberately *not* short-circuited: it still reports the
    per-component versions and the policy state, because that is the only way
    an operator can see firmware versions on a latched switch. What it must
    never do is return non-zero - syncd.sh runs 'mlnx-fw-manager --status=all'
    at container start and treats any non-zero return as fatal, while
    handle_status() returns FW_UPGRADE_IS_REQUIRED (10) on any mismatch. On an
    operator-managed switch the vModule version differs from the image MFA by
    design, so a non-zero return here would stop syncd from starting on every
    boot, forever.

    Args:
        status: The --status argument, or None when not requested
        reset: True when --reset was requested. Resetting BlueField firmware
            configuration is not a firmware burn, so the policy does not gate
            it.

    Raises:
        SystemExit: When the policy is disabled and this is not a --status run.
    """
    if fw_policy.is_auto_upgrade_enabled():
        return
    if reset:
        return

    note = fw_policy.get_disable_note()
    detail = f" ({note})" if note else ""
    message = (
        f"Automatic firmware upgrade is disabled{detail}. "
        f"ASIC and vModule firmware are managed by the operator."
    )
    logger.warning(message)
    print(message)

    if status is not None:
        # Report versions, then force success regardless of any mismatch.
        try:
            handle_status(status, verbose=False, upgrade=False)
        except Exception as e:
            logger.warning(f"Status report failed while policy is disabled: {e}")
        sys.exit(EXIT_SUCCESS)

    sys.exit(EXIT_SUCCESS)


@contextmanager
def _lock_state_change():
    """
    Context manager for file locking (matches shell script LockStateChange/UnlockStateChange).
    """
    lock_file = "/tmp/mlxfwmanager-lock"
    lock_fd = None

    try:
        logger.info(f"Locking {lock_file} from CLI")
        lock_fd = open(lock_file, 'w')
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        logger.info(f"Locked {lock_file} from CLI")
        yield lock_fd
    except Exception as e:
        logger.error(f"Failed to acquire lock: {e}")
        raise FirmwareManagerError(f"Failed to acquire lock: {e}")
    finally:
        if lock_fd:
            try:
                logger.info(f"Unlocking {lock_file} from CLI")
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                lock_fd.close()
            except Exception as e:
                logger.warning(f"Failed to unlock: {e}")


def handle_status(asic_id_arg: str, verbose: bool, upgrade: bool) -> int:
    """
    Handle firmware status check - reports if firmware version matches.

    Args:
        asic_id_arg: ASIC ID, "all", or "__flag__" (when used as flag)
        verbose: Enable verbose logging
        upgrade: Use firmware from next SONiC image

    Returns:
        Exit code
    """
    try:
        fw_coordinator = FirmwareCoordinator(verbose=verbose, from_image=upgrade)
        num_asics = fw_coordinator.get_asic_count()

        # Handle flag mode for single-ASIC systems
        if asic_id_arg == '__flag__':
            if num_asics == 1:
                asic_id_arg = '0'
            else:
                print(f"Error: Multi-ASIC system detected ({num_asics} ASICs).")
                print("Usage: mlnx-fw-manager --status <ASIC_ID|all>")
                print("Example: mlnx-fw-manager --status all")
                return EXIT_FAILURE

        # Determine which ASICs to check
        if asic_id_arg.lower() == 'all':
            managers_to_check = fw_coordinator.managers
            print(f"Firmware Status Report ({num_asics} ASIC(s)):")
        else:
            try:
                asic_id = int(asic_id_arg)
                if asic_id >= num_asics:
                    print(f"Error: ASIC {asic_id} not found. System has {num_asics} ASIC(s).")
                    return EXIT_FAILURE
                managers_to_check = [fw_coordinator.managers[asic_id]]
                print(f"Firmware Status Report (ASIC {asic_id}):")
            except ValueError:
                print(f"Error: Invalid ASIC ID '{asic_id_arg}'. Use a number or 'all'.")
                return EXIT_FAILURE

        upgrade_needed_asics = []
        up_to_date_asics = []
        error_asics = []

        # The vModule version read addresses an mst device, so the status path
        # needs an MST session just like the burn path does - but only on
        # platforms that actually have a vModule.
        if fw_coordinator.needs_vmod_versions():
            with fw_coordinator.mst_session():
                fw_coordinator.load_vmod_versions()
                _report_components(managers_to_check, upgrade_needed_asics,
                                   up_to_date_asics, error_asics)
        else:
            _report_components(managers_to_check, upgrade_needed_asics,
                               up_to_date_asics, error_asics)

        print(f"Summary: {len(up_to_date_asics)} up to date, "
              f"{len(upgrade_needed_asics)} need upgrade, "
              f"{len(error_asics)} errors")
        print(f"Auto firmware upgrade policy: "
              f"{'ENABLED' if fw_policy.is_auto_upgrade_enabled() else 'DISABLED'}")

        if upgrade_needed_asics or error_asics:
            return FW_UPGRADE_IS_REQUIRED
        return EXIT_SUCCESS

    except Exception as e:
        print(f"Status check failed: {e}")
        return EXIT_FAILURE


def _report_components(managers_to_check, upgrade_needed_asics,
                       up_to_date_asics, error_asics):
    """Print the per-ASIC, per-component status lines."""
    for manager in managers_to_check:
        try:
            is_needed = manager.is_upgrade_required()
            if is_needed:
                upgrade_needed_asics.append(manager.asic_index)
                print(f"ASIC {manager.asic_index}: Upgrade needed")
            else:
                up_to_date_asics.append(manager.asic_index)
                print(f"ASIC {manager.asic_index}: Up to date")
            components = manager.get_component_versions()
            # Single-component platforms (SPC1-SPC5, BlueField) keep their
            # existing one-line output byte for byte.
            if len(components) > 1:
                for component in components:
                    if component.get('error'):
                        state = f"error: {component['error']}"
                    elif component['upgrade_needed']:
                        state = 'upgrade needed'
                    else:
                        state = 'up to date'
                    print(f"  {component['name']:<10} "
                          f"current {component['current']}  "
                          f"available {component['available']}  "
                          f"{state}")
        except Exception as e:
            error_asics.append(manager.asic_index)
            print(f"ASIC {manager.asic_index}: Error checking status - {e}")


def handle_reset(verbose: bool) -> int:
    """
    Handle firmware configuration reset.

    Args:
        verbose: Enable verbose logging

    Returns:
        Exit code
    """
    try:
        fw_coordinator = FirmwareCoordinator(verbose=verbose)
        fw_coordinator.reset_firmware_config()
        print("Firmware configuration reset completed successfully.")
        return EXIT_SUCCESS
    except Exception as e:
        print(f"Firmware reset failed: {e}")
        return EXIT_FAILURE


def handle_dry_run(verbose: bool, upgrade: bool) -> int:
    """
    Handle dry-run (check if firmware upgrade is required).

    Args:
        verbose: Enable verbose logging
        upgrade: Use firmware from next SONiC image

    Returns:
        Exit code
    """
    try:
        fw_coordinator = FirmwareCoordinator(verbose=verbose, from_image=upgrade)
        required = fw_coordinator.check_upgrade_required()
        for manager in fw_coordinator.managers:
            for component in manager.get_component_versions():
                state = 'upgrade needed' if component['upgrade_needed'] else 'up to date'
                if component.get('error'):
                    state = f"error: {component['error']}"
                print(f"ASIC {manager.asic_index} {component['name']:<10} "
                      f"current {component['current']}  "
                      f"available {component['available']}  {state}")
        if required:
            print("Firmware upgrade is required.")
            logger.info(f"Dry-run check: upgrade required, exiting with {FW_UPGRADE_IS_REQUIRED}")
            return FW_UPGRADE_IS_REQUIRED
        else:
            print("Firmware is up to date.")
            logger.info(f"Dry-run check: up to date, exiting with {EXIT_SUCCESS}")
            return EXIT_SUCCESS
    except Exception as e:
        print(f"Firmware check failed: {e}")
        logger.error(f"Dry-run check failed, exiting with {EXIT_FAILURE}")
        return EXIT_FAILURE


def handle_upgrade(verbose: bool, upgrade: bool, clear_semaphore: bool,
                   ignore_mst_start_failure: bool = False) -> int:
    """
    Handle firmware upgrade operation.

    Args:
        verbose: Enable verbose logging
        upgrade: Use firmware from next SONiC image
        clear_semaphore: Clear hardware semaphore before upgrade
        ignore_mst_start_failure: Continue upgrade even if MST driver fails to start

    Returns:
        Exit code
    """
    try:
        fw_coordinator = FirmwareCoordinator(
            verbose=verbose,
            from_image=upgrade,
            clear_semaphore=clear_semaphore,
            ignore_mst_start_failure=ignore_mst_start_failure
        )

        if not fw_coordinator.check_upgrade_required():
            print("Firmware is up to date.")
            logger.info(f"Upgrade check: up to date, exiting with {EXIT_SUCCESS}")
            return EXIT_SUCCESS
        else:
            fw_coordinator.upgrade_firmware()
            print("Firmware upgrade completed successfully.")
            logger.info(f"Upgrade completed successfully, exiting with {EXIT_SUCCESS}")
            return EXIT_SUCCESS

    except FirmwareUpgradeError as e:
        print(f"Firmware upgrade failed: {e}")
        return EXIT_FAILURE
    except FirmwareUpgradePartialError as e:
        print(f"Firmware upgrade partially failed: {e}")
        return EXIT_SUCCESS
    except Exception as e:
        print(f"Firmware upgrade failed: {e}")
        return EXIT_FAILURE


EXIT_DECLINED = 2


def _require_cpo_platform() -> None:
    """
    Reject --auto-upgrade / --burn on platforms without CPO (OF-1a).

    Operator-owned firmware management exists because OE firmware is new and
    per-ASIC. Nothing about that applies to a platform carrying only ASIC_FW,
    and exposing a way to switch off automatic ASIC firmware upgrade across the
    whole existing NVIDIA portfolio is a far larger change than this feature
    asks for. AF-7 requires those platforms to behave exactly as today.

    Uses the same CPO predicate the automatic flow uses to decide whether
    OE_FW is a component at all - there is no second definition.

    Raises:
        SystemExit: with EXIT_FAILURE on a non-CPO platform.
    """
    if vmod_manager.is_cpo_platform(logger):
        return
    print("--auto-upgrade and --burn are supported only on CPO-capable platforms.")
    print("This platform has no Optical Engine firmware, and its automatic "
          "firmware upgrade behaviour is unchanged.")
    sys.exit(EXIT_FAILURE)


def handle_auto_upgrade(action: str, note: str, assume_yes: bool) -> int:
    """
    --auto-upgrade {status|disable|enable} (OF-1, OF-3, OF-4, OF-6).

    'status' never encodes the policy in its exit code (OF-4). Doing so would
    tempt callers to treat 'disabled' as a failure, which is the mistake AF-9
    exists to prevent.
    """
    _require_cpo_platform()

    if action == 'status':
        if fw_policy.is_auto_upgrade_enabled():
            print("Automatic firmware upgrade: ENABLED")
        else:
            print("Automatic firmware upgrade: DISABLED")
            disable_note = fw_policy.get_disable_note()
            if disable_note:
                print(f"  {disable_note}")
        return EXIT_SUCCESS

    if action == 'enable':
        try:
            changed = fw_policy.enable_auto_upgrade(logger=logger)
        except OSError as err:
            print(f"Failed to re-enable automatic firmware upgrade: {err}")
            return EXIT_FAILURE
        print("Automatic firmware upgrade: ENABLED")
        if changed:
            print("Note: firmware is not burned now. The next automatic run will "
                  "converge this switch to the firmware shipped in the installed "
                  "SONiC image.")
        return EXIT_SUCCESS

    # disable
    if not fw_policy.is_auto_upgrade_enabled():
        print("Automatic firmware upgrade: DISABLED (already)")
        return EXIT_SUCCESS

    print("WARNING: Disabling automatic firmware upgrade hands you ownership of\n"
          "         ASIC and Optical Engine firmware on this switch. SONiC will no\n"
          "         longer burn firmware, including after a SONiC-to-SONiC upgrade.\n"
          "         You can re-enable it later with:\n"
          "           mlnx-fw-manager --auto-upgrade enable")
    if sys.stdin.isatty() and not assume_yes:
        if input("Continue? [y/N]: ").strip().lower() not in ('y', 'yes'):
            print("Aborted. Nothing changed.")
            return EXIT_DECLINED

    audit = (f"disabled {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
             f"by mlnx-fw-manager --auto-upgrade disable")
    if note:
        audit = f"{audit}: {note}"
    try:
        fw_policy.disable_auto_upgrade(audit, logger=logger)
    except OSError as err:
        print(f"Failed to disable automatic firmware upgrade: {err}")
        return EXIT_FAILURE
    print("Automatic firmware upgrade: DISABLED")
    return EXIT_SUCCESS


def handle_burn(component: str, image: str, asic: str,
                dry_run: bool, assume_yes: bool, verbose: bool) -> int:
    """
    --burn: manual firmware upgrade (OF-7, OF-8, OF-9).

    Ignores the policy flag: this is the manual path, and refusing to run while
    the automatic flow is disabled would leave the operator with no supported
    way to upgrade. It does not change the policy either.
    """
    _require_cpo_platform()

    coordinator = FirmwareCoordinator(verbose=verbose)
    mfa = image or coordinator.get_default_mfa_path()

    if not os.path.isfile(mfa):
        print(f"Firmware image not found: {mfa}")
        return EXIT_FAILURE

    # 2.8: under system firmware control the ASIC firmware owns the module
    # state machine and a SONiC-driven burn would race it. Checked before any
    # write, and before the MFA classification that costs seconds.
    if not dry_run:
        try:
            _reject_if_system_under_fw_control()
        except FirmwareUpgradeError as e:
            print(str(e))
            return EXIT_FAILURE

        # Printed unconditionally, not only on a TTY: a non-interactive caller
        # is not prompted but its log must still record that flash was written.
        print("WARNING: This writes firmware to ASIC flash. Do not power off "
              "the switch.")

    # The burn must run inside an MST session. Without one mlxfwmanager cannot
    # reach the device over DMA and falls back to a register-access burn that
    # is several times slower and, measured on hardware, fails outright after
    # ~700 s. The automatic flow already burns inside mst_session(); the manual
    # path has to do the same.
    try:
        with coordinator.mst_session():
            rc = _burn_all(coordinator, component, mfa, asic, dry_run, assume_yes)
    except FirmwareUpgradeError as e:
        print(f"Firmware burn failed: {e}")
        return EXIT_FAILURE

    if rc == EXIT_SUCCESS and not dry_run:
        print("Reboot to activate.")
        if fw_policy.is_auto_upgrade_enabled() and image:
            print("Note: automatic firmware upgrade is still ENABLED, so the next "
                  "boot will converge this switch back to the image's firmware. "
                  "Run 'mlnx-fw-manager --auto-upgrade disable' to keep this "
                  "firmware.")
    if dry_run:
        print("Dry run: nothing was written.")
    return rc


def _reject_if_system_under_fw_control() -> None:
    """
    Reject a manual burn unless the system is under software control (2.8).

    This is a system property, not a per-module one: under system firmware
    control the ASIC firmware owns the module state machine and a SONiC-driven
    burn would race it.

    Raises:
        FirmwareUpgradeError: If the system is under firmware control.
    """
    from sonic_platform.device_data import DeviceDataManager
    if not DeviceDataManager.is_module_host_management_mode():
        raise FirmwareUpgradeError(
            "Manual firmware burn is only supported when the system is under "
            "software control; this system is firmware controlled "
            "(SAI_INDEPENDENT_MODULE_MODE is not 1). No firmware was written.")


def _components_to_report(manager, component):
    """The component rows a burn of `component` is accountable for."""
    rows = manager.get_component_versions()
    if component == 'asic':
        return [r for r in rows if r['name'] == vmod_manager.COMPONENT_ASIC_FW]
    if component == 'oe':
        return [r for r in rows if r['name'] == vmod_manager.COMPONENT_OE_FW]
    return rows


def _burn_all(coordinator, component, mfa, asic, dry_run, assume_yes) -> int:
    """Per-ASIC burn loop. Must be called inside an MST session."""
    burned = skipped = errors = 0
    delta_found = False

    managers = coordinator.select_managers(asic)

    # A subset burn leaves OE firmware non-uniform across ASICs, which the
    # architecture forbids in steady state (1.7). Allowed for bring-up and
    # debug, but the operator has to be told.
    if len(managers) < coordinator.get_asic_count():
        print(f"WARNING: burning {len(managers)} of "
              f"{coordinator.get_asic_count()} ASICs. OE firmware must be made "
              f"uniform across all of them, or the system is left in an "
              f"unsupported state.")

    for manager in managers:
        # Device health gate. get_psid() goes through 'mlxfwmanager --query',
        # which is also what reports 'Failed to open device' on an ASIC left
        # unreadable by an earlier failed burn - and the query is already
        # retried with backoff, so an empty PSID here means the device is
        # genuinely not responding rather than momentarily busy.
        #
        # Without this the caller still refuses, but via classify_mfa, whose
        # message blames the *image*. An operator reads that as 'wrong MFA',
        # picks another one and retries straight back into the burn that wedged
        # the switch. Measured once in testing: burning into an unreadable
        # device took the box down and needed a reboot to recover.
        psid = manager.get_psid()
        if not psid:
            print(f"ASIC {manager.asic_index}: the device is not responding "
                  f"(mlxfwmanager cannot read its PSID). Refusing to burn.")
            print("  Reboot to recover, then retry. Burning into an unreadable "
                  "device can leave the switch needing a power cycle.")
            return EXIT_FAILURE

        shape = vmod_manager.classify_mfa(mfa, psid, logger)

        if shape == vmod_manager.SHAPE_NO_MATCH:
            print(f"ASIC {manager.asic_index}: {mfa} does not carry firmware for "
                  f"this device (PSID {psid}). Nothing was written.")
            return EXIT_FAILURE

        print(f"Image {mfa}: {shape}, PSID {psid} present.")

        # --component is an assertion, verified against the image (OF-9).
        if component == 'asic' and shape == vmod_manager.SHAPE_COMBINED:
            print("-E- --component asic was requested, but this image also carries "
                  "OE_FW, and a burn writes the whole matched image. Either:\n"
                  "      - use the ASIC-only image, or\n"
                  "      - re-run with --component all to accept that OE_FW is "
                  "written too.\n"
                  "Nothing was written.")
            return EXIT_FAILURE
        if component == 'oe' and shape == vmod_manager.SHAPE_ASIC_ONLY:
            print("-E- --component oe was requested, but this image carries no "
                  "OE_FW component. Nothing was written.")
            return EXIT_FAILURE
        if component == 'asic' and shape == vmod_manager.SHAPE_OE_ONLY:
            print("-E- --component asic was requested, but this image carries "
                  "OE_FW only - it has no ASIC firmware to write. Use the "
                  "ASIC-only or combined image. Nothing was written.")
            return EXIT_FAILURE
        if component == 'all' and shape == vmod_manager.SHAPE_OE_ONLY:
            print("Note: this image carries OE_FW only, so ASIC_FW is not "
                  "written by this burn.")

        # Report what differs before deciding, so a dry run is informative and
        # a no-op burn is visibly a no-op rather than a six-minute flash write.
        rows = _components_to_report(manager, component)
        needed = False
        print(f"ASIC {manager.asic_index} ({manager.pci_id}):")
        for row in rows:
            verdict = ('error' if row.get('error')
                       else 'differs -> burn' if row['upgrade_needed']
                       else 'up to date')
            print(f"  {row['name']:<9} current {row['current']}  "
                  f"available {row['available']}  {verdict}")
            if row.get('error'):
                errors += 1
            elif row['upgrade_needed']:
                needed = True

        if not needed:
            print(f"ASIC {manager.asic_index}: nothing to do.")
            skipped += 1
            continue

        delta_found = True
        if dry_run:
            continue

        if sys.stdin.isatty() and not assume_yes:
            if input("Continue? [y/N]: ").strip().lower() not in ('y', 'yes'):
                print("Aborted. Nothing was written.")
                return EXIT_DECLINED

        manager.clear_semaphore()
        if not manager.burn_image(mfa):
            print(f"ASIC {manager.asic_index}: burn failed.")
            return EXIT_FAILURE
        burned += 1
        print(f"ASIC {manager.asic_index}: burn completed. Firmware is staged and "
              f"activates on the next ASIC reset.")

    print(f"Summary: {burned} burned, {skipped} skipped, {errors} errors")

    # A component whose version could not be read leaves its state unknown.
    # Reporting that as 0 ("up to date") or as 10 ("an upgrade is required")
    # would both be claims this run cannot support, so it is an error - the
    # operator has to be able to tell "nothing to do" from "I could not tell".
    # Today only OE_FW can reach this: the ASIC_FW row never carries an error,
    # and an unreadable ASIC is already refused by the pre-burn health gate.
    #
    # A found delta still outranks it on the dry-run path: an operator who has
    # been told an upgrade is required has an answer they can act on, and the
    # errored component is named in the per-row output either way.
    if dry_run and delta_found:
        return FW_UPGRADE_IS_REQUIRED
    if errors:
        print(f"{errors} component(s) could not be read; their firmware state "
              f"is unknown. Nothing was written for them.")
        return EXIT_FAILURE
    return EXIT_SUCCESS


@click.command(context_settings={'help_option_names': ['-h', '--help'], 'max_content_width': 120})
@click.option('-u', '--upgrade', is_flag=True,
              help='Upgrade ASIC firmware using next boot image (useful after SONiC-To-SONiC update)')
@click.option('-v', '--verbose', is_flag=True,
              help='Verbose mode (enabled when -u|--upgrade)')
@click.option('-d', '--dry-run', is_flag=True,
              help='Compare the FW versions without installation. Return code "0" means the FW is up-to-date, return code "10" means an upgrade is required, otherwise an error is detected.')
@click.option('-c', '--clear-semaphore', is_flag=True,
              help='Clear hw semaphore before firmware upgrade')
@click.option('-r', '--reset', is_flag=True,
              help='Reset firmware configuration (NVIDIA BlueField platform only)')
@click.option('-m', '--ignore-mst-start-failure', is_flag=True,
              help='Continue firmware upgrade even if MST driver fails to start '
                   '(useful on SmartSwitch when DPUs are powered off)')
@click.option('--nosyslog', is_flag=True,
              help='Disable syslog and log to console only')
@click.option('--status', 'status', type=str, default=None, is_flag=False, flag_value='__flag__', metavar='[ASIC_ID|all]',
              help='Show firmware version status. Single-ASIC: use as flag. Multi-ASIC: specify ASIC ID or "all".')
@click.option('--auto-upgrade', 'auto_upgrade', type=click.Choice(['status', 'disable', 'enable']), default=None,
              help='Show, disable or enable the automatic firmware upgrade policy (CPO platforms only)')
@click.option('--burn', 'burn', is_flag=True,
              help='Manually burn firmware from an MFA (CPO platforms only)')
@click.option('--component', 'component', type=click.Choice(['asic', 'oe', 'all']), default='all',
              help='Components the burn is intended to write; verified against the image')
@click.option('--image', 'image', type=str, default=None, metavar='<mfa>',
              help="MFA to burn. Defaults to the installed image's MFA.")
@click.option('--asic', 'asic', type=str, default='all', metavar='[ASIC_ID|all]',
              help='Which ASIC to act on (default: all)')
@click.option('--note', 'note', type=str, default=None, metavar='<text>',
              help='Audit text recorded when disabling the automatic flow')
@click.option('-y', '--yes', 'assume_yes', is_flag=True,
              help='Skip the confirmation prompt (not required non-interactively)')
def main(upgrade, verbose, dry_run, clear_semaphore, reset, ignore_mst_start_failure, nosyslog, status,
         auto_upgrade, burn, component, image, asic, note, assume_yes):
    """
    Mellanox Firmware Manager

    Usage: ./mlnx-fw-upgrade [OPTIONS]

    OPTIONS:
      -u, --upgrade  Upgrade ASIC firmware using next boot image (useful after SONiC-To-SONiC update)
      -v, --verbose  Verbose mode (enabled when -u|--upgrade)
      -d, --dry-run  Compare the FW versions without installation. Return code "0" means the FW is up-to-date, return code "10" means an upgrade is required, otherwise an error is detected.
      -c, --clear-semaphore   Clear hw semaphore before firmware upgrade
      -r, --reset    Reset firmware configuration (NVIDIA BlueField platform only)
      --status [ASIC_ID|all]  Show firmware version status
                              Single-ASIC: use as flag (no argument)
                              Multi-ASIC: specify ASIC ID or "all"
      --nosyslog     Disable syslog and log to console only
      -h, --help     Print help

    Examples:
      ./mlnx-fw-upgrade --verbose
      ./mlnx-fw-upgrade --upgrade
      ./mlnx-fw-upgrade --reset
      ./mlnx-fw-upgrade --clear-semaphore --upgrade

      # Single-ASIC system:
      ./mlnx-fw-upgrade --status              # Check firmware status

      # Multi-ASIC system:
      ./mlnx-fw-upgrade --status all          # Check all ASICs
      ./mlnx-fw-upgrade --status 0            # Check ASIC 0
      ./mlnx-fw-upgrade --status 0 --upgrade  # Check ASIC 0 against next image

      ./mlnx-fw-upgrade --help
    """
    setup_logging(verbose, nosyslog)

    logger.info("Mellanox Firmware Manager started")

    _exit_if_qemu()

    # Operator-driven sub-commands run before the policy gate: --auto-upgrade
    # manages the gate itself, and --burn is the manual path that must stay
    # usable precisely when the automatic flow is disabled (OF-8).
    if auto_upgrade is not None:
        sys.exit(handle_auto_upgrade(auto_upgrade, note, assume_yes))

    if burn:
        sys.exit(handle_burn(component, image, asic, dry_run, assume_yes, verbose))

    _exit_if_auto_upgrade_disabled(status, reset)

    if status is not None:
        exit_code = handle_status(status, verbose, upgrade)
        logger.info(f"Mellanox Firmware Manager finished with exit code {exit_code}")
        sys.exit(exit_code)

    with _lock_state_change():
        if reset:
            exit_code = handle_reset(verbose)
        elif dry_run:
            exit_code = handle_dry_run(verbose, upgrade)
        else:
            exit_code = handle_upgrade(verbose, upgrade, clear_semaphore, ignore_mst_start_failure)

    logger.info(f"Mellanox Firmware Manager finished with exit code {exit_code}")
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
