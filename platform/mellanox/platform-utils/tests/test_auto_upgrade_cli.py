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
Tests for `mlnx-fw-manager --auto-upgrade` and `--burn` (OF-1..OF-9).

These two options are halves of one workflow: the operator turns the automatic
flow off, then drives firmware themselves. Both are restricted to CPO-capable
platforms (OF-1a).
"""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_fw_manager import main as fw_main
from mellanox_fw_manager import fw_policy, vmod_manager


class AutoUpgradeCliBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.marker = os.path.join(self.temp_dir, 'fw_auto_upgrade_disabled')
        self._marker_patch = patch.object(
            fw_policy, 'AUTO_UPGRADE_DISABLED_MARKER', self.marker)
        self._marker_patch.start()
        # Default: CPO platform, non-interactive caller.
        self._cpo = patch.object(vmod_manager, 'is_cpo_platform', return_value=True)
        self._cpo.start()
        self._tty = patch('mellanox_fw_manager.main.sys.stdin.isatty',
                          return_value=False)
        self._tty.start()

    def tearDown(self):
        self._tty.stop()
        self._cpo.stop()
        self._marker_patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _disable(self, note='test'):
        fw_policy.disable_auto_upgrade(note, self.marker)


class TestAutoUpgradeStatus(AutoUpgradeCliBase):
    """OF-4: showing the policy works in either state and never fails."""

    def test_status_enabled_returns_zero(self):
        self.assertEqual(fw_main.handle_auto_upgrade('status', None, False), 0)

    def test_status_disabled_also_returns_zero(self):
        """
        The state lives on stdout, not in the exit code. Encoding it would
        tempt callers to treat 'disabled' as failure - the mistake AF-9 exists
        to prevent.
        """
        self._disable()
        self.assertEqual(fw_main.handle_auto_upgrade('status', None, False), 0)

    def test_status_prints_state_and_audit_line(self):
        self._disable('disabled by netops ticket 4471')
        with patch('builtins.print') as mock_print:
            fw_main.handle_auto_upgrade('status', None, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('DISABLED', out)
        self.assertIn('ticket 4471', out)

    def test_status_does_not_change_the_policy(self):
        fw_main.handle_auto_upgrade('status', None, False)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))
        self._disable()
        fw_main.handle_auto_upgrade('status', None, False)
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))


class TestAutoUpgradeDisable(AutoUpgradeCliBase):
    """OF-3."""

    def test_disable_creates_the_flag(self):
        rc = fw_main.handle_auto_upgrade('disable', None, False)
        self.assertEqual(rc, 0)
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disable_warns_about_ownership(self):
        with patch('builtins.print') as mock_print:
            fw_main.handle_auto_upgrade('disable', None, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('WARNING', out)
        self.assertIn('ownership', out)

    def test_note_is_attached_to_the_audit_line(self):
        """
        The note supplements provenance, it does not replace it: a marker
        holding only the operator's words loses when and by what it was set.
        """
        fw_main.handle_auto_upgrade('disable', 'owned by netops', False)
        line = fw_policy.get_disable_note(self.marker)
        self.assertIn('owned by netops', line)
        self.assertIn('--auto-upgrade disable', line)
        self.assertRegex(line, r'\d{4}-\d{2}-\d{2}T')

    def test_interactive_caller_must_confirm(self):
        with patch('mellanox_fw_manager.main.sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='y'):
            self.assertEqual(fw_main.handle_auto_upgrade('disable', None, False), 0)
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_decline_returns_two_and_changes_nothing(self):
        with patch('mellanox_fw_manager.main.sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='n'):
            self.assertEqual(fw_main.handle_auto_upgrade('disable', None, False), 2)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_empty_answer_declines(self):
        with patch('mellanox_fw_manager.main.sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value=''):
            self.assertEqual(fw_main.handle_auto_upgrade('disable', None, False), 2)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_yes_skips_the_prompt(self):
        with patch('mellanox_fw_manager.main.sys.stdin.isatty', return_value=True), \
             patch('builtins.input') as mock_input:
            self.assertEqual(fw_main.handle_auto_upgrade('disable', None, True), 0)
        mock_input.assert_not_called()

    def test_non_interactive_caller_is_never_prompted(self):
        with patch('builtins.input') as mock_input:
            fw_main.handle_auto_upgrade('disable', None, False)
        mock_input.assert_not_called()

    def test_already_disabled_is_not_an_error(self):
        self._disable('first')
        self.assertEqual(fw_main.handle_auto_upgrade('disable', None, False), 0)
        self.assertEqual(fw_policy.get_disable_note(self.marker), 'first')

    def test_write_failure_returns_one(self):
        with patch.object(fw_policy, 'disable_auto_upgrade',
                          side_effect=OSError('read-only file system')):
            self.assertEqual(fw_main.handle_auto_upgrade('disable', None, False), 1)


class TestAutoUpgradeEnable(AutoUpgradeCliBase):
    """OF-6: enabling burns nothing by itself."""

    def test_enable_removes_the_flag(self):
        self._disable()
        self.assertEqual(fw_main.handle_auto_upgrade('enable', None, False), 0)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_enable_when_already_enabled_is_not_an_error(self):
        self.assertEqual(fw_main.handle_auto_upgrade('enable', None, False), 0)

    def test_enable_does_not_burn(self):
        self._disable()
        with patch('mellanox_fw_manager.main.FirmwareCoordinator') as mock_coord:
            fw_main.handle_auto_upgrade('enable', None, False)
        mock_coord.assert_not_called()

    def test_enable_says_no_firmware_was_burned(self):
        self._disable()
        with patch('builtins.print') as mock_print:
            fw_main.handle_auto_upgrade('enable', None, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('not burned', out)

    def test_remove_failure_returns_one(self):
        self._disable()
        with patch.object(fw_policy, 'enable_auto_upgrade',
                          side_effect=OSError('permission denied')):
            self.assertEqual(fw_main.handle_auto_upgrade('enable', None, False), 1)

    def test_round_trip(self):
        fw_main.handle_auto_upgrade('disable', None, False)
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))
        fw_main.handle_auto_upgrade('enable', None, False)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))


class TestPlatformScoping(AutoUpgradeCliBase):
    """OF-1a: both options exist only on CPO-capable platforms."""

    def test_auto_upgrade_rejected_on_non_cpo(self):
        self._cpo.stop()
        try:
            with patch.object(vmod_manager, 'is_cpo_platform', return_value=False):
                with self.assertRaises(SystemExit) as ctx:
                    fw_main.handle_auto_upgrade('status', None, False)
            self.assertEqual(ctx.exception.code, 1)
        finally:
            self._cpo.start()

    def test_burn_rejected_on_non_cpo(self):
        self._cpo.stop()
        try:
            with patch.object(vmod_manager, 'is_cpo_platform', return_value=False):
                with self.assertRaises(SystemExit) as ctx:
                    fw_main.handle_burn('all', None, 'all', False, True, False)
            self.assertEqual(ctx.exception.code, 1)
        finally:
            self._cpo.start()

    def test_rejection_writes_no_flag(self):
        self._cpo.stop()
        try:
            with patch.object(vmod_manager, 'is_cpo_platform', return_value=False):
                with self.assertRaises(SystemExit):
                    fw_main.handle_auto_upgrade('disable', None, True)
        finally:
            self._cpo.start()
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_uses_the_same_cpo_predicate_as_the_auto_flow(self):
        """
        One definition, not two. If this ever diverges, a platform could get
        the CLI without the OE component, or the reverse.
        """
        self._cpo.stop()
        try:
            with patch.object(vmod_manager, 'is_cpo_platform',
                              return_value=True) as mock_cpo:
                fw_main._require_cpo_platform()
            mock_cpo.assert_called_once()
        finally:
            self._cpo.start()


if __name__ == '__main__':
    unittest.main()
