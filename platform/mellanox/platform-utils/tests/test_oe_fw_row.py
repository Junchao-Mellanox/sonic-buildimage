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
Tests for the OE_FW row in get_component_versions.py (AF-10).

This is a reporting command: an unreadable vModule must cost the operator that
one cell, never the whole table.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_component_versions import main as cv_main


class TestOeFwRow(unittest.TestCase):

    def test_row_is_registered(self):
        self.assertIn('OE_FW', cv_main.COMPONENT_RULES_SW)

    def test_row_sits_next_to_the_asic_fw_row(self):
        """Operators read the two firmware versions together."""
        keys = list(cv_main.COMPONENT_RULES_SW.keys())
        self.assertEqual(keys.index('OE_FW'), keys.index('FW') + 1)

    def test_compilation_column_is_a_dash(self):
        """
        AF-10: only ACTUAL is populated. The build records no OE_FW line, and
        deriving the image's version at runtime costs a multi-second MFA
        extract on what is meant to be a cheap reporting command.
        """
        with patch.object(cv_main.os.path, 'exists', return_value=False):
            compiled = cv_main.parse_compiled_components_file()
        self.assertEqual(compiled['OE_FW'], '-')

    def test_compilation_is_a_dash_even_when_the_file_lacks_the_row(self):
        import tempfile, textwrap
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write(textwrap.dedent("""\
                SDK 4.10.0724-002
                FW 2020.0752
                """))
            path = f.name
        try:
            with patch.object(cv_main, 'COMPONENT_VERSIONS_FILE', path):
                compiled = cv_main.parse_compiled_components_file()
            self.assertEqual(compiled['OE_FW'], '-')
            self.assertEqual(compiled['FW'], '2020.0752')
        finally:
            os.unlink(path)

    def test_a_recorded_compilation_value_is_used_if_the_build_adds_one(self):
        """The fallback is absent, not the ability to read a recorded value."""
        import tempfile, textwrap
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write(textwrap.dedent("""\
                FW 2020.0752
                OE_FW 2020.0752
                """))
            path = f.name
        try:
            with patch.object(cv_main, 'COMPONENT_VERSIONS_FILE', path):
                compiled = cv_main.parse_compiled_components_file()
            self.assertEqual(compiled['OE_FW'], '2020.0752')
        finally:
            os.unlink(path)

    def test_reads_the_running_version_per_asic(self):
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = True
        vmod.get_current_vmod_version.side_effect = ['95.10.0752', '95.10.0752']
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}), \
             patch.object(cv_main, 'DeviceDataManager') as ddm, \
             patch.object(cv_main.subprocess, 'run',
                          return_value=MagicMock(returncode=0)):
            ddm.get_asic_count.return_value = 2
            self.assertEqual(cv_main.parse_oe_fw_version(''),
                             ['95.10.0752', '95.10.0752'])

    def test_non_cpo_platform_reports_na(self):
        """AF-7: platforms without a vModule are unchanged."""
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = False
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}):
            self.assertEqual(cv_main.parse_oe_fw_version(''), ['N/A'])

    def test_non_cpo_platform_starts_no_mst_session(self):
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = False
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}), \
             patch.object(cv_main.subprocess, 'run') as mock_run:
            cv_main.parse_oe_fw_version('')
        mock_run.assert_not_called()

    def test_unreadable_vmodule_reports_na_without_raising(self):
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = True
        vmod.get_current_vmod_version.return_value = None
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}), \
             patch.object(cv_main, 'DeviceDataManager') as ddm, \
             patch.object(cv_main.subprocess, 'run',
                          return_value=MagicMock(returncode=0)):
            ddm.get_asic_count.return_value = 1
            self.assertEqual(cv_main.parse_oe_fw_version(''), ['N/A'])

    def test_a_raising_read_is_not_fatal(self):
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = True
        vmod.get_current_vmod_version.side_effect = RuntimeError('mst gone')
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}), \
             patch.object(cv_main, 'DeviceDataManager') as ddm, \
             patch.object(cv_main.subprocess, 'run',
                          return_value=MagicMock(returncode=0)):
            ddm.get_asic_count.return_value = 1
            self.assertEqual(cv_main.parse_oe_fw_version(''), ['N/A'])

    def _run_and_collect_argvs(self, version, returncode=0):
        vmod = MagicMock()
        vmod.is_cpo_platform.return_value = True
        if isinstance(version, Exception):
            vmod.get_current_vmod_version.side_effect = version
        else:
            vmod.get_current_vmod_version.return_value = version
        with patch.dict(sys.modules, {'mellanox_fw_manager.vmod_manager': vmod,
                                      'mellanox_fw_manager': MagicMock(vmod_manager=vmod)}), \
             patch.object(cv_main, 'DeviceDataManager') as ddm, \
             patch.object(cv_main.subprocess, 'run',
                          return_value=MagicMock(returncode=returncode)) as mock_run:
            ddm.get_asic_count.return_value = 1
            cv_main.parse_oe_fw_version('')
        return [c[0][0] for c in mock_run.call_args_list]

    def test_the_session_is_started(self):
        self.assertIn(['mst', 'start'], self._run_and_collect_argvs('95.10.0752'))

    def test_the_session_is_never_stopped(self):
        """
        The same rule as FirmwareCoordinator.mst_session: 'mst stop' unloads
        mst_pci/mst_pciconf even while another process holds a /dev/mst device
        open, so a reporting command must never issue it - it could pull the
        driver out from under a concurrent burn or a syncd start.
        """
        self.assertNotIn(['mst', 'stop'], self._run_and_collect_argvs('95.10.0752'))

    def test_the_session_is_not_stopped_on_the_failure_paths_either(self):
        """A stop hidden in a `finally` is exactly how this rule would regress."""
        for version in (None, RuntimeError('mst gone')):
            with self.subTest(version=version):
                self.assertNotIn(['mst', 'stop'],
                                 self._run_and_collect_argvs(version))

    def test_a_failed_mst_start_does_not_provoke_a_stop(self):
        self.assertNotIn(['mst', 'stop'],
                         self._run_and_collect_argvs(None, returncode=1))


if __name__ == '__main__':
    unittest.main()
