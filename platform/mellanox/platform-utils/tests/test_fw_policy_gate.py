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
Tests for the automatic firmware upgrade policy gate in main().

The gate must be evaluated before anything touches hardware, and it must cover
every subcommand - including --status, which syncd.sh depends on.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_fw_manager.main import (
    main, _exit_if_auto_upgrade_disabled, EXIT_SUCCESS, FW_UPGRADE_IS_REQUIRED
)
from mellanox_fw_manager import main as main_module


class TestExitIfAutoUpgradeDisabled(unittest.TestCase):

    def setUp(self):
        main_module.logger = MagicMock()

    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=True)
    def test_returns_when_enabled(self, mock_enabled):
        """No exit, so the normal flow continues."""
        self.assertIsNone(_exit_if_auto_upgrade_disabled(None))

    @patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
           return_value='disabled 2026-09-17 by admin')
    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=False)
    def test_exits_zero_when_disabled(self, mock_enabled, mock_note):
        with self.assertRaises(SystemExit) as ctx:
            _exit_if_auto_upgrade_disabled(None)
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)
        main_module.logger.warning.assert_called_once()
        self.assertIn('disabled 2026-09-17 by admin',
                      main_module.logger.warning.call_args[0][0])

    @patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
           return_value=None)
    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=False)
    def test_exits_zero_when_note_unreadable(self, mock_enabled, mock_note):
        """An unreadable audit line must not change the policy decision."""
        with self.assertRaises(SystemExit) as ctx:
            _exit_if_auto_upgrade_disabled(None)
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)

    @patch('mellanox_fw_manager.main.handle_status')
    @patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
           return_value='note')
    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=False)
    def test_status_request_reports_then_exits_zero(self, mock_enabled,
                                                    mock_note, mock_status):
        """
        A latched switch must still be able to show firmware versions - it is
        the only way an operator can see them from this tool - but the exit
        code must be forced to 0 regardless of any mismatch.
        """
        mock_status.return_value = FW_UPGRADE_IS_REQUIRED
        with self.assertRaises(SystemExit) as ctx:
            _exit_if_auto_upgrade_disabled('all')
        mock_status.assert_called_once()
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)

    @patch('mellanox_fw_manager.main.handle_status',
           side_effect=RuntimeError('hardware gone'))
    @patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
           return_value='note')
    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=False)
    def test_status_failure_still_exits_zero(self, mock_enabled, mock_note,
                                             mock_status):
        """Even a broken status report must not stop syncd from starting."""
        with self.assertRaises(SystemExit) as ctx:
            _exit_if_auto_upgrade_disabled('all')
        self.assertEqual(ctx.exception.code, EXIT_SUCCESS)

    @patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
           return_value=False)
    def test_reset_is_not_gated(self, mock_enabled):
        """Resetting BlueField firmware config is not a firmware burn."""
        self.assertIsNone(_exit_if_auto_upgrade_disabled(None, reset=True))


class TestMainPolicyGate(unittest.TestCase):
    """End-to-end through the click entry point."""

    def setUp(self):
        self.runner = CliRunner()

    def _invoke(self, args, enabled):
        with patch('mellanox_fw_manager.main.setup_logging'), \
             patch('mellanox_fw_manager.main._exit_if_qemu'), \
             patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
                   return_value=enabled), \
             patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
                   return_value='disabled by operator'), \
             patch('mellanox_fw_manager.main.FirmwareCoordinator') as coordinator, \
             patch('mellanox_fw_manager.main._lock_state_change'):
            main_module.logger = MagicMock()
            result = self.runner.invoke(main, args)
        return result, coordinator

    def test_status_returns_zero_when_disabled(self):
        """
        The syncd regression guard.

        syncd.sh runs 'mlnx-fw-manager --status=all' and treats any non-zero
        return as fatal, while handle_status() returns 10 on any mismatch. On
        an operator-managed switch the vModule version differs from the image
        MFA by design, so a non-zero return here would stop syncd from
        starting on every boot, forever.
        """
        result, coordinator = self._invoke(['--status', 'all'], enabled=False)
        self.assertEqual(result.exit_code, EXIT_SUCCESS)

    def test_status_single_asic_returns_zero_when_disabled(self):
        result, coordinator = self._invoke(['--status', '0'], enabled=False)
        self.assertEqual(result.exit_code, EXIT_SUCCESS)

    def test_upgrade_skipped_when_disabled(self):
        result, coordinator = self._invoke([], enabled=False)
        self.assertEqual(result.exit_code, EXIT_SUCCESS)
        coordinator.assert_not_called()

    def test_dry_run_returns_zero_when_disabled(self):
        result, coordinator = self._invoke(['--dry-run'], enabled=False)
        self.assertEqual(result.exit_code, EXIT_SUCCESS)
        coordinator.assert_not_called()

    def test_no_hardware_touched_when_disabled(self):
        """The gate runs before MST start, so nothing is probed."""
        with patch('mellanox_fw_manager.main.setup_logging'), \
             patch('mellanox_fw_manager.main._exit_if_qemu'), \
             patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
                   return_value=False), \
             patch('mellanox_fw_manager.main.fw_policy.get_disable_note',
                   return_value=None), \
             patch('mellanox_fw_manager.main.run_command') as mock_run, \
             patch('mellanox_fw_manager.main.FirmwareCoordinator') as coordinator:
            main_module.logger = MagicMock()
            result = self.runner.invoke(main, [])
        self.assertEqual(result.exit_code, EXIT_SUCCESS)
        coordinator.assert_not_called()
        mock_run.assert_not_called()

    def test_dry_run_still_reports_upgrade_required_when_enabled(self):
        """
        Guards against an inverted condition: with the policy enabled,
        --dry-run must keep its contract of returning 10.
        """
        with patch('mellanox_fw_manager.main.setup_logging'), \
             patch('mellanox_fw_manager.main._exit_if_qemu'), \
             patch('mellanox_fw_manager.main.fw_policy.is_auto_upgrade_enabled',
                   return_value=True), \
             patch('mellanox_fw_manager.main._lock_state_change'), \
             patch('mellanox_fw_manager.main.FirmwareCoordinator') as coordinator:
            coordinator.return_value.check_upgrade_required.return_value = True
            main_module.logger = MagicMock()
            result = self.runner.invoke(main, ['--dry-run'])
        self.assertEqual(result.exit_code, FW_UPGRADE_IS_REQUIRED)

    def test_normal_flow_runs_when_enabled(self):
        result, coordinator = self._invoke([], enabled=True)
        coordinator.assert_called()


class TestPerComponentStatusOutput(unittest.TestCase):

    def setUp(self):
        main_module.logger = MagicMock()

    def _manager(self, components, upgrade_needed):
        manager = MagicMock()
        manager.asic_index = 0
        manager.is_upgrade_required.return_value = upgrade_needed
        manager.get_component_versions.return_value = components
        return manager

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_status_prints_both_components(self, mock_coordinator):
        components = [
            {'name': 'ASIC_FW', 'current': '39.2021.0002',
             'available': '39.2021.0002', 'upgrade_needed': False},
            {'name': 'OE_FW', 'current': '95.2021.0002',
             'available': '95.2021.0010', 'upgrade_needed': True},
        ]
        manager = self._manager(components, upgrade_needed=True)
        mock_coordinator.return_value.get_asic_count.return_value = 1
        mock_coordinator.return_value.managers = [manager]

        from mellanox_fw_manager.main import handle_status
        with patch('builtins.print') as mock_print:
            rc = handle_status('all', verbose=False, upgrade=False)

        printed = ' '.join(str(c[0][0]) for c in mock_print.call_args_list)
        self.assertEqual(rc, FW_UPGRADE_IS_REQUIRED)
        self.assertIn('ASIC_FW', printed)
        self.assertIn('OE_FW', printed)
        self.assertIn('95.2021.0010', printed)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_status_returns_zero_when_all_up_to_date(self, mock_coordinator):
        components = [
            {'name': 'ASIC_FW', 'current': '39.2021.0002',
             'available': '39.2021.0002', 'upgrade_needed': False},
        ]
        manager = self._manager(components, upgrade_needed=False)
        mock_coordinator.return_value.get_asic_count.return_value = 1
        mock_coordinator.return_value.managers = [manager]

        from mellanox_fw_manager.main import handle_status
        with patch('builtins.print'):
            rc = handle_status('all', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_SUCCESS)


class TestHandleStatusSelection(unittest.TestCase):
    """ASIC selection branches of --status, exercised with the policy enabled."""

    def setUp(self):
        main_module.logger = MagicMock()
        self.manager = MagicMock()
        self.manager.asic_index = 0
        self.manager.is_upgrade_required.return_value = False
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2021.0002',
             'available': '39.2021.0002', 'upgrade_needed': False},
        ]

    def _coordinator(self, mock_coordinator, num_asics):
        mock_coordinator.return_value.get_asic_count.return_value = num_asics
        mock_coordinator.return_value.managers = [self.manager] * num_asics

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_flag_mode_on_single_asic_resolves_to_asic_zero(self, mock_coordinator):
        self._coordinator(mock_coordinator, 1)
        from mellanox_fw_manager.main import handle_status
        with patch('builtins.print'):
            rc = handle_status('__flag__', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_SUCCESS)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_flag_mode_on_multi_asic_is_rejected(self, mock_coordinator):
        self._coordinator(mock_coordinator, 4)
        from mellanox_fw_manager.main import handle_status, EXIT_FAILURE
        with patch('builtins.print'):
            rc = handle_status('__flag__', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_FAILURE)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_specific_asic_index(self, mock_coordinator):
        self._coordinator(mock_coordinator, 2)
        from mellanox_fw_manager.main import handle_status
        with patch('builtins.print'):
            rc = handle_status('1', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_SUCCESS)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_out_of_range_asic_index(self, mock_coordinator):
        self._coordinator(mock_coordinator, 1)
        from mellanox_fw_manager.main import handle_status, EXIT_FAILURE
        with patch('builtins.print'):
            rc = handle_status('7', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_FAILURE)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_invalid_asic_id(self, mock_coordinator):
        self._coordinator(mock_coordinator, 1)
        from mellanox_fw_manager.main import handle_status, EXIT_FAILURE
        with patch('builtins.print'):
            rc = handle_status('bogus', verbose=False, upgrade=False)
        self.assertEqual(rc, EXIT_FAILURE)

    @patch('mellanox_fw_manager.main.FirmwareCoordinator')
    def test_per_asic_error_is_reported_as_upgrade_required(self, mock_coordinator):
        self.manager.is_upgrade_required.side_effect = RuntimeError('ASIC not ready')
        self._coordinator(mock_coordinator, 1)
        from mellanox_fw_manager.main import handle_status
        with patch('builtins.print'):
            rc = handle_status('all', verbose=False, upgrade=False)
        self.assertEqual(rc, FW_UPGRADE_IS_REQUIRED)


class TestNonCpoStatusOutputUnchanged(unittest.TestCase):
    """
    AF-7: SPC1-SPC5 and BlueField must keep today's behaviour. They report a
    single component, so no per-component rows are printed for them.
    """

    def setUp(self):
        main_module.logger = MagicMock()

    def _run(self, components):
        manager = MagicMock()
        manager.asic_index = 0
        manager.is_upgrade_required.return_value = False
        manager.get_component_versions.return_value = components
        with patch('mellanox_fw_manager.main.FirmwareCoordinator') as coordinator:
            coordinator.return_value.get_asic_count.return_value = 1
            coordinator.return_value.managers = [manager]
            from mellanox_fw_manager.main import handle_status
            with patch('builtins.print') as mock_print:
                handle_status('all', verbose=False, upgrade=False)
        return ' '.join(str(c[0][0]) for c in mock_print.call_args_list)

    def test_single_component_platform_prints_no_component_rows(self):
        printed = self._run([
            {'name': 'ASIC_FW', 'current': '13.2016.3430',
             'available': '13.2016.3430', 'upgrade_needed': False,
             'error': None},
        ])
        self.assertNotIn('ASIC_FW', printed)
        self.assertIn('ASIC 0: Up to date', printed)

    def test_cpo_platform_prints_component_rows(self):
        printed = self._run([
            {'name': 'ASIC_FW', 'current': '39.2021.0002',
             'available': '39.2021.0002', 'upgrade_needed': False,
             'error': None},
            {'name': 'OE_FW', 'current': '95.2021.0002',
             'available': '95.2021.0010', 'upgrade_needed': True,
             'error': None},
        ])
        self.assertIn('ASIC_FW', printed)
        self.assertIn('OE_FW', printed)

    def test_vmod_error_is_surfaced_in_the_table(self):
        printed = self._run([
            {'name': 'ASIC_FW', 'current': '39.2021.0002',
             'available': '39.2021.0002', 'upgrade_needed': False,
             'error': None},
            {'name': 'OE_FW', 'current': None, 'available': None,
             'upgrade_needed': False, 'error': 'no VMOD component found'},
        ])
        self.assertIn('no VMOD component found', printed)

if __name__ == '__main__':
    unittest.main()
