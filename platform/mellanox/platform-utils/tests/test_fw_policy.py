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

"""Tests for the automatic firmware upgrade policy marker."""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_fw_manager import fw_policy


class TestFwPolicy(unittest.TestCase):
    """The marker file existing is the whole mechanism."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.marker = os.path.join(self.temp_dir, 'fw_auto_upgrade_disabled')

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_enabled_when_marker_absent(self):
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disabled_when_marker_present(self):
        with open(self.marker, 'w') as f:
            f.write('disabled by test\n')
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disabled_when_marker_is_empty(self):
        """There is no parse step, so an empty marker still disables."""
        open(self.marker, 'w').close()
        self.assertEqual(os.path.getsize(self.marker), 0)
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disabled_when_marker_has_arbitrary_content(self):
        """No 'corrupt flag' state exists - any content disables."""
        with open(self.marker, 'wb') as f:
            f.write(b'\x00\x01\xff not text at all')
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disable_creates_marker_with_note(self):
        fw_policy.disable_auto_upgrade('disabled 2026-09-17 by admin', self.marker)
        self.assertTrue(os.path.exists(self.marker))
        with open(self.marker) as f:
            self.assertEqual(f.read(), 'disabled 2026-09-17 by admin\n')
        self.assertFalse(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_disable_flushes_to_stable_storage(self):
        """
        The marker records that firmware has already been burned. Losing it to
        a power cut would let the next boot revert the operator's firmware, so
        the write is fsync-ed (file and directory) and followed by a global
        sync.
        """
        with patch('mellanox_fw_manager.fw_policy.os.fsync') as mock_fsync, \
             patch('mellanox_fw_manager.fw_policy.os.sync') as mock_sync:
            fw_policy.disable_auto_upgrade('note', self.marker)
        # one fsync for the file, one for the directory
        self.assertEqual(mock_fsync.call_count, 2)
        mock_sync.assert_called_once()

    def test_sync_happens_after_the_rename(self):
        order = []
        with patch('mellanox_fw_manager.fw_policy.os.rename',
                   side_effect=lambda a, b: order.append('rename')), \
             patch('mellanox_fw_manager.fw_policy.os.fsync',
                   side_effect=lambda fd: order.append('fsync')), \
             patch('mellanox_fw_manager.fw_policy.os.sync',
                   side_effect=lambda: order.append('sync')):
            fw_policy.disable_auto_upgrade('note', self.marker)
        self.assertEqual(order[-1], 'sync')
        self.assertLess(order.index('rename'), order.index('sync'))

    def test_disable_leaves_no_temp_file_behind(self):
        fw_policy.disable_auto_upgrade('note', self.marker)
        self.assertFalse(os.path.exists(self.marker + '.tmp'))

    def test_disable_is_idempotent_and_refreshes_note(self):
        fw_policy.disable_auto_upgrade('first', self.marker)
        fw_policy.disable_auto_upgrade('second', self.marker)
        self.assertEqual(fw_policy.get_disable_note(self.marker), 'second')

    def test_disable_logs_warning_when_logger_given(self):
        logger = MagicMock()
        fw_policy.disable_auto_upgrade('note', self.marker, logger=logger)
        logger.warning.assert_called_once()
        self.assertIn('permanently disabled', logger.warning.call_args[0][0])

    def test_disable_raises_when_marker_cannot_be_written(self):
        """
        A burn must never proceed without a durable marker, so the write
        failure has to surface rather than being swallowed.
        """
        unwritable = os.path.join(self.temp_dir, 'no_such_dir', 'marker')
        with self.assertRaises(OSError):
            fw_policy.disable_auto_upgrade('note', unwritable)

    def test_get_disable_note_returns_first_line(self):
        with open(self.marker, 'w') as f:
            f.write('audit line\nsecond line\n')
        self.assertEqual(fw_policy.get_disable_note(self.marker), 'audit line')

    def test_get_disable_note_missing_file_returns_none(self):
        self.assertIsNone(fw_policy.get_disable_note(self.marker))

    def test_get_disable_note_empty_file_returns_none(self):
        open(self.marker, 'w').close()
        self.assertIsNone(fw_policy.get_disable_note(self.marker))

    def test_get_disable_note_unreadable_returns_none(self):
        """A set policy stays set even if its audit text cannot be read."""
        with open(self.marker, 'w') as f:
            f.write('note\n')
        with patch('builtins.open', side_effect=IOError('permission denied')):
            self.assertIsNone(fw_policy.get_disable_note(self.marker))

    def test_default_marker_path_is_on_host(self):
        """/host survives SONiC-to-SONiC upgrade; /etc would not."""
        self.assertTrue(
            fw_policy.AUTO_UPGRADE_DISABLED_MARKER.startswith('/host/'))


if __name__ == '__main__':
    unittest.main()
