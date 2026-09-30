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

"""Tests for vModule (Optical Engine) firmware component support."""

import os
import sys
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_fw_manager import vmod_manager


def _result(returncode=0, stdout='', stderr=''):
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = stderr
    return result


# Captured verbatim from MFT 4.36.0 - the version SONiC ships - against real
# SPC6 release artifacts under /auto/mswg/release/sx_mlnx_fw/SPC6. Do not
# hand-edit these into a "tidier" shape: the point is that they are what MFT
# really emits.

# flint -d mt53124_pciconf0_vmdl_0 q full
# Captured on a Chameleon6S (SN6810-LD). The vModule reports TWO banks and has
# no plain "FW Version:" line; the running bank is the one to compare.
FLINT_FULL_OUTPUT = """Image A FW Status:                      Not Running, Uncommitted, Valid
Image B FW Status:                      Running,     Committed,   Valid
Image A FW Version:                     95.5.0750
Image A Additional Info:
Image B FW Version:                     95.10.0752
Image B Additional Info:
RunningImage LinkX FW Upgrade Major:    0x0
RunningImage MM Revision:               0x000000
PSID:                                   MT_0000001916
Boot Status:
Package Error:                          PKG_OK
Last FW Upgrade Error:                  PKG_OK
"""

# flint -i <fwpkg> --component_type VMOD q
FLINT_COMPONENT_OUTPUT = """Image type:            FSVMOD
FW Version:            95_2020_0966-002
FW Release Date:       25.3.2026
Image VSD:             95_2020_0966-002
Device VSD:            N/A
PSID:                  MT_0000001836
Security Attributes:   N/A
"""

# Every distinct version *shape* observed across the 296 SPC6 releases at
# 39.2020.0748 and above, with real samples of each.
REAL_VERSION_SAMPLES = [
    # N.N.N - 151 releases
    '95.2020.0748', '95.2020.0750', '95.2020.0932', '95.2020.2920',
    # N_N_N-N - 143 releases (respin suffix)
    '95_2020_0748-002', '95_2020_0750-006', '95_2020_0752-002',
    '39_2014_2904-100',
    # N_N_N - 2 releases
    '95_2020_1132', '95_2020_1202',
]


class TestNormalizeVersion(unittest.TestCase):
    """
    The four version reads come from different MFT code paths and render
    versions inconsistently. Without normalization the comparison would
    mismatch forever and re-burn the vModule on every boot.
    """

    def test_dotted_form(self):
        self.assertEqual(vmod_manager.normalize_version('39.2020.0176'),
                         (39, 2020, 176))

    def test_underscore_form(self):
        self.assertEqual(vmod_manager.normalize_version('39_2020_0176'),
                         (39, 2020, 176))

    def test_fw_prefixed_form(self):
        self.assertEqual(vmod_manager.normalize_version('FW 39.2020.0176'),
                         (39, 2020, 176))

    def test_surrounding_whitespace(self):
        self.assertEqual(vmod_manager.normalize_version('  39.2020.0176 '),
                         (39, 2020, 176))

    def test_two_part_version(self):
        self.assertEqual(vmod_manager.normalize_version('95.2021'), (95, 2021))

    def test_none_input(self):
        self.assertIsNone(vmod_manager.normalize_version(None))

    def test_empty_input(self):
        self.assertIsNone(vmod_manager.normalize_version(''))

    def test_non_version_text(self):
        self.assertIsNone(vmod_manager.normalize_version('N/A'))

    def test_single_number_is_not_a_version(self):
        self.assertIsNone(vmod_manager.normalize_version('39'))


class TestIsCpoPlatform(unittest.TestCase):

    def test_true_when_cpo_data_present(self):
        fake = MagicMock()
        fake.get_cpo_data.return_value = {'interfaces': {'Ethernet0': {}}}
        with patch.dict(sys.modules, {'sonic_py_common': MagicMock(device_info=fake)}):
            with patch('sonic_py_common.device_info', fake, create=True):
                self.assertTrue(vmod_manager.is_cpo_platform())

    def test_false_when_cpo_data_absent(self):
        fake = MagicMock()
        fake.get_cpo_data.return_value = None
        with patch.dict(sys.modules, {'sonic_py_common': MagicMock(device_info=fake)}):
            with patch('sonic_py_common.device_info', fake, create=True):
                self.assertFalse(vmod_manager.is_cpo_platform())

    def test_false_when_sonic_py_common_unavailable(self):
        """Non-SONiC environments must not crash the firmware manager."""
        with patch.dict(sys.modules, {'sonic_py_common': None}):
            self.assertFalse(vmod_manager.is_cpo_platform())


class TestFindVmodMstDevice(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make_device(self, name):
        open(os.path.join(self.temp_dir, name), 'w').close()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_finds_device_after_module_add(self, mock_run):
        mock_run.return_value = _result()
        self._make_device('mt53124_pciconf0_vmdl_0')
        device = vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir)
        # flint only accepts a vModule device as a bare name, never a path.
        self.assertEqual(device, 'mt53124_pciconf0_vmdl_0')
        self.assertNotIn('/', device)
        mock_run.assert_called_once()
        self.assertEqual(mock_run.call_args[0][0],
                         ['/usr/bin/mst', 'module', 'add'])

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_ignores_non_module_devices(self, mock_run):
        mock_run.return_value = _result()
        # the ASIC device and the cable device must both be ignored
        self._make_device('mt53124_pciconf0')
        self._make_device('mt53124_pciconf0_cable_16')
        device = vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir)
        self.assertIsNone(device)

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_returns_none_when_no_device(self, mock_run):
        mock_run.return_value = _result()
        logger = MagicMock()
        self.assertIsNone(
            vmod_manager.find_vmod_mst_device(logger=logger,
                                              device_dir=self.temp_dir))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_module_add_failure_still_scans(self, mock_run):
        """'mst module add' may fail because the device already exists."""
        mock_run.return_value = _result(returncode=1, stderr='already added')
        self._make_device('mt53124_pciconf0_vmdl_0')
        logger = MagicMock()
        device = vmod_manager.find_vmod_mst_device(logger=logger,
                                                   device_dir=self.temp_dir)
        self.assertIsNotNone(device)
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_picks_lowest_device_deterministically(self, mock_run):
        mock_run.return_value = _result()
        self._make_device('mt53124_pciconf0_vmdl_2')
        self._make_device('mt53124_pciconf0_vmdl_0')
        device = vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir)
        self.assertEqual(device, 'mt53124_pciconf0_vmdl_0')


class TestGetCurrentVmodVersion(unittest.TestCase):

    @patch('mellanox_fw_manager.vmod_manager.find_vmod_mst_device')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_parses_fw_version(self, mock_run, mock_find):
        mock_find.return_value = 'mt53124_pciconf0_vmdl_0'
        mock_run.return_value = _result(stdout=FLINT_FULL_OUTPUT)
        self.assertEqual(vmod_manager.get_current_vmod_version(), '95.10.0752')
        self.assertEqual(
            mock_run.call_args[0][0],
            ['/usr/bin/flint', '-d', 'mt53124_pciconf0_vmdl_0', 'q', 'full'])

    @patch('mellanox_fw_manager.vmod_manager.find_vmod_mst_device')
    def test_no_device_returns_none(self, mock_find):
        mock_find.return_value = None
        self.assertIsNone(vmod_manager.get_current_vmod_version())

    @patch('mellanox_fw_manager.vmod_manager.find_vmod_mst_device')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_flint_failure_returns_none(self, mock_run, mock_find):
        mock_find.return_value = 'mt53124_pciconf0_vmdl_0'
        mock_run.return_value = _result(returncode=1, stderr='boom')
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_current_vmod_version(logger=logger))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.find_vmod_mst_device')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_missing_version_line_returns_none(self, mock_run, mock_find):
        mock_find.return_value = 'mt53124_pciconf0_vmdl_0'
        mock_run.return_value = _result(stdout='Image type: FSCTRL\n')
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_current_vmod_version(logger=logger))
        logger.warning.assert_called()


class TestGetAvailableVmodVersion(unittest.TestCase):

    def setUp(self):
        vmod_manager.clear_caches()

    def tearDown(self):
        vmod_manager.clear_caches()

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_extract_then_query(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = [
            _result(),                                   # extract
            _result(stdout=FLINT_COMPONENT_OUTPUT),      # component query
        ]
        version = vmod_manager.get_available_vmod_version(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836')
        self.assertEqual(version, '95_2020_0966-002')

        extract_cmd = mock_run.call_args_list[0][0][0]
        self.assertEqual(extract_cmd[:6],
                         ['mlxfwmanager', '-i', '/etc/mlnx/fw-SPC6.mfa',
                          '--psid', 'MT_0000001836', '-extract'])
        query_cmd = mock_run.call_args_list[1][0][0]
        self.assertIn('--component_type', query_cmd)
        self.assertIn('VMOD', query_cmd)

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_extract_failure_returns_none(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr='no such psid')
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_available_vmod_version(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836', logger=logger))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_extract_reports_success_but_produces_nothing(self, mock_run, mock_exists):
        mock_exists.return_value = False
        mock_run.return_value = _result()
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_available_vmod_version(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836', logger=logger))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_component_query_failure_returns_none(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = [_result(), _result(returncode=1, stderr='boom')]
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_available_vmod_version(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836', logger=logger))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_no_vmod_component_in_package(self, mock_run, mock_exists):
        mock_exists.return_value = True
        mock_run.side_effect = [_result(), _result(stdout='Image type: PLDM\n')]
        logger = MagicMock()
        self.assertIsNone(vmod_manager.get_available_vmod_version(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836', logger=logger))
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.shutil.rmtree')
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_temp_dir_removed_even_on_failure(self, mock_run, mock_rmtree):
        mock_run.return_value = _result(returncode=1)
        vmod_manager.get_available_vmod_version('/etc/mlnx/fw-SPC6.mfa', 'PSID')
        mock_rmtree.assert_called_once()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_temp_dir_removed_when_command_raises(self, mock_run):
        mock_run.side_effect = RuntimeError('unexpected')
        with patch('mellanox_fw_manager.vmod_manager.shutil.rmtree') as mock_rmtree:
            with self.assertRaises(RuntimeError):
                vmod_manager.get_available_vmod_version('/mfa', 'PSID')
            mock_rmtree.assert_called_once()




class TestAvailableVersionCache(unittest.TestCase):
    """
    All ASICs compare against the same MFA, so the fwpkg extract must happen
    once per (mfa, psid) - not once per ASIC on every boot and syncd start.
    """

    def setUp(self):
        vmod_manager.clear_caches()

    def tearDown(self):
        vmod_manager.clear_caches()

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists', return_value=True)
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_extract_runs_once_for_n_asics(self, mock_run, mock_exists):
        mock_run.side_effect = [
            _result(), _result(stdout=FLINT_COMPONENT_OUTPUT),
        ]
        for _ in range(4):
            version = vmod_manager.get_available_vmod_version('/mfa', 'PSID')
            self.assertEqual(version, '95_2020_0966-002')
        # Two commands total (extract + query), not eight.
        self.assertEqual(mock_run.call_count, 2)

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_failure_is_cached_too(self, mock_run):
        mock_run.return_value = _result(returncode=1, stderr='no such psid')
        self.assertIsNone(vmod_manager.get_available_vmod_version('/mfa', 'PSID'))
        self.assertIsNone(vmod_manager.get_available_vmod_version('/mfa', 'PSID'))
        self.assertEqual(mock_run.call_count, 1)

    @patch('mellanox_fw_manager.vmod_manager.os.path.exists', return_value=True)
    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_different_mfa_is_a_different_cache_entry(self, mock_run, mock_exists):
        mock_run.side_effect = [
            _result(), _result(stdout=FLINT_COMPONENT_OUTPUT),
            _result(), _result(stdout=FLINT_COMPONENT_OUTPUT),
        ]
        vmod_manager.get_available_vmod_version('/mfa-a', 'PSID')
        vmod_manager.get_available_vmod_version('/mfa-b', 'PSID')
        self.assertEqual(mock_run.call_count, 4)


class TestRealWorldVersionFormats(unittest.TestCase):
    # These pin the OE_FW comparison. ASIC_FW is byte-identical (AF-7) and is
    # covered by TestAsicComparisonIsByteIdentical below.
    """
    Golden-corpus guard.

    These strings were captured from MFT 4.36.0 - the version SONiC ships -
    against every SPC6 release at 39.2020.0748 and above. An earlier
    normalizer, tested only against invented fixtures, silently failed on 143
    of those 296 releases (48%): the underscored '-NNN' respin form returned
    None, which disabled the re-burn guard for half the release history.
    """

    def test_every_observed_shape_parses(self):
        for version in REAL_VERSION_SAMPLES:
            with self.subTest(version=version):
                self.assertIsNotNone(
                    vmod_manager.normalize_version(version),
                    f"{version!r} is a real MFT output and must parse")

    def test_respin_suffix_is_preserved(self):
        self.assertEqual(vmod_manager.normalize_version('95_2020_0966-002'),
                         (95, 2020, 966, 2))

    def test_separator_rendering_is_insensitive(self):
        """
        The same firmware is rendered with dots by --package_info and with
        underscores by --component_type; treating those as different would
        re-burn the vModule on every boot.
        """
        self.assertFalse(vmod_manager.vmod_versions_differ(
            '95_2020_0966-002', '95.2020.0966-002'))
        self.assertFalse(vmod_manager.vmod_versions_differ(
            '95_2020_2920', '95.2020.2920'))

    def test_respin_is_preserved_by_normalization(self):
        """
        A respin is a different build, so normalize_version must keep it as a
        distinct trailing element.

        Asserted against normalize_version rather than vmod_versions_differ:
        the OE comparison deliberately discards everything except the major
        and the last field while the namespace question is open, so it cannot
        see a respin either way.
        """
        self.assertNotEqual(vmod_manager.normalize_version('95_2020_0966'),
                            vmod_manager.normalize_version('95_2020_0966-002'))
        self.assertNotEqual(vmod_manager.normalize_version('95_2020_0748-002'),
                            vmod_manager.normalize_version('95_2020_0748-006'))
        self.assertEqual(vmod_manager.normalize_version('95_2020_0966-002'),
                         (95, 2020, 966, 2))

    def test_no_spurious_upgrade_across_the_corpus(self):
        """A version compared against itself never triggers a burn."""
        for version in REAL_VERSION_SAMPLES:
            with self.subTest(version=version):
                self.assertFalse(
                    vmod_manager.vmod_versions_differ(version, version))


class TestRunningBankSelection(unittest.TestCase):
    """
    The vModule reports two firmware banks. Comparing the wrong one against
    the image MFA burns firmware that is already present, on every boot.
    """

    def test_picks_the_running_bank(self):
        self.assertEqual(
            vmod_manager.parse_running_bank_version(FLINT_FULL_OUTPUT),
            '95.10.0752')

    def test_not_running_is_not_mistaken_for_running(self):
        """'Not Running' contains 'Running' - a substring test would break."""
        flipped = FLINT_FULL_OUTPUT.replace(
            'Image A FW Status:                      Not Running, Uncommitted, Valid',
            'Image A FW Status:                      Running,     Committed,   Valid'
        ).replace(
            'Image B FW Status:                      Running,     Committed,   Valid',
            'Image B FW Status:                      Not Running, Uncommitted, Valid')
        self.assertEqual(vmod_manager.parse_running_bank_version(flipped),
                         '95.5.0750')

    def test_no_running_bank_returns_none(self):
        text = FLINT_FULL_OUTPUT.replace('Running,     Committed',
                                         'Not Running, Uncommitted')
        self.assertIsNone(vmod_manager.parse_running_bank_version(text))

    def test_empty_output(self):
        self.assertIsNone(vmod_manager.parse_running_bank_version(''))
        self.assertIsNone(vmod_manager.parse_running_bank_version(None))

    def test_file_mode_output_is_not_dual_bank(self):
        """The MFA side keeps a plain 'FW Version:' line - different parser."""
        self.assertIsNone(
            vmod_manager.parse_running_bank_version(FLINT_COMPONENT_OUTPUT))
        self.assertIsNotNone(
            vmod_manager._FW_VERSION_RE.search(FLINT_COMPONENT_OUTPUT))


class TestMiddleFieldWorkaround(unittest.TestCase):
    """
    TEMPORARY: the device and the MFA report the vModule version in different
    namespaces with a different field count. Only the major and the build
    correspond. Superseded by TestAsicComparisonIsFullPrecision and
    TestRespinAsymmetry for the edge cases; kept for the core equivalences.
    Remove with SKIP_MIDDLE_VERSION_FIELD.
    """

    def test_real_device_vs_real_mfa_compares_equal(self):
        self.assertFalse(
            vmod_manager.vmod_versions_differ('95.10.0752', '95.2020.0752'))

    def test_prior_bank_against_its_own_release(self):
        self.assertFalse(
            vmod_manager.vmod_versions_differ('95.5.0750', '95.2020.0750'))

    def test_genuine_build_difference_is_still_detected(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('95.10.0750', '95.2020.0752'))

    def test_major_difference_is_still_detected(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('94.10.0752', '95.2020.0752'))

    def test_comparable_keeps_major_and_build(self):
        self.assertEqual(vmod_manager._vmod_comparable((95, 2020, 752)), (95, 752))
        self.assertEqual(vmod_manager._vmod_comparable((95, 10, 752)), (95, 752))
        self.assertEqual(vmod_manager._vmod_comparable((95, 2020, 966, 2)), (95, 966))

    def test_two_field_versions_are_untouched(self):
        self.assertEqual(vmod_manager._vmod_comparable((95, 2020)), (95, 2020))

    def test_flag_is_documented_as_temporary(self):
        self.assertTrue(hasattr(vmod_manager, 'SKIP_MIDDLE_VERSION_FIELD'))


class TestVmodDeviceSelection(unittest.TestCase):
    """
    'mst start --with_i2cdev' exposes both PCI and I2C vModule representors.
    The I2C form sorts first but flint cannot query it (rc=1), so selection
    must prefer the PCI form explicitly rather than taking devices[0].
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make(self, *names):
        for n in names:
            open(os.path.join(self.temp_dir, n), 'w').close()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_prefers_pci_over_i2c(self, mock_run):
        mock_run.return_value = _result()
        # exactly the layout seen on sws-cham6s-4 with --with_i2cdev
        self._make('dev-i2c-7_vmdl_9', 'dev-i2c-8_vmdl_3',
                   'mt53124_pciconf0_vmdl_0', 'mt53124_pciconf0_vmdl_1')
        self.assertEqual(
            vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir),
            'mt53124_pciconf0_vmdl_0')

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_falls_back_to_i2c_when_no_pci_form(self, mock_run):
        mock_run.return_value = _result()
        self._make('dev-i2c-7_vmdl_9')
        logger = MagicMock()
        self.assertEqual(
            vmod_manager.find_vmod_mst_device(logger=logger,
                                              device_dir=self.temp_dir),
            'dev-i2c-7_vmdl_9')
        logger.warning.assert_called()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_cable_and_asic_devices_are_never_selected(self, mock_run):
        mock_run.return_value = _result()
        self._make('mt53124_pciconf0', 'mt53124_pciconf0_cable_16',
                   'mt53124_pci_cr0')
        self.assertIsNone(
            vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir))

if __name__ == '__main__':
    unittest.main()


class TestVmodComparisonScope(unittest.TestCase):
    """
    The CPO namespace workaround lives in vmod_versions_differ and applies to
    OE_FW only. ASIC_FW uses a plain byte-identical comparison (AF-7), so this
    helper must never be reachable from the ASIC path.
    """

    def test_workaround_drops_the_middle_field(self):
        """The device reports 95.10.0752 where the MFA reports 95.2020.0752."""
        self.assertFalse(
            vmod_manager.vmod_versions_differ('95.10.0752', '95.2020.0752'))

    def test_last_field_still_participates(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('95.10.0752', '95.10.0750'))

    def test_major_still_participates(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('95.10.0752', '96.10.0752'))

    def test_separator_rendering_is_normalized(self):
        self.assertFalse(
            vmod_manager.vmod_versions_differ('95_10_0752', '95.10.0752'))

    def test_asic_path_does_not_use_this_helper(self):
        """
        Guard against the regression this class exists for: if ASIC_FW were
        routed through here, 29.2010.1004 vs 29.2012.1004 would compare equal
        and a genuine ASIC upgrade would be skipped on every platform.
        """
        import inspect
        from mellanox_fw_manager import firmware_base
        src = inspect.getsource(firmware_base.FirmwareManagerBase.is_upgrade_required)
        self.assertNotIn('vmod_versions_differ', src)
        self.assertIn('self.current_version != self.available_version', src)


class TestRespinAsymmetry(unittest.TestCase):
    """
    The MFA carries a respin suffix on 143 of 296 releases; the device never
    reports one. Comparing the respin would make those releases mismatch
    forever - a burn every boot, and a non-zero --status that stops syncd.
    """

    def test_mfa_respin_vs_device_without_one(self):
        self.assertFalse(
            vmod_manager.vmod_versions_differ('95.10.0966', '95_2020_0966-002'))

    def test_real_build_difference_still_detected(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('95.10.0964', '95_2020_0966-002'))

    def test_major_difference_still_detected(self):
        self.assertTrue(
            vmod_manager.vmod_versions_differ('94.10.0966', '95_2020_0966-002'))


class TestStagedBankCounts(unittest.TestCase):
    """
    A burn writes the inactive bank; the new image is only the running one
    after an ASIC reset. Between those points the image is present but not
    running, and must not be re-burned.
    """

    def test_parse_bank_versions_returns_both_running_first(self):
        versions = vmod_manager.parse_bank_versions(FLINT_FULL_OUTPUT)
        self.assertEqual(versions[0], '95.10.0752')      # running bank
        self.assertIn('95.5.0750', versions)             # staged bank

    def test_empty_output(self):
        self.assertEqual(vmod_manager.parse_bank_versions(''), [])


class TestPerAsicDeviceSelection(unittest.TestCase):
    """
    HLD 1.7: each worker addresses its own ASIC. Reading ASIC 0's vModule for
    every manager reports one ASIC's version against another's and leaves the
    stale ASIC unconverged.
    """

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _make(self, *names):
        for n in names:
            open(os.path.join(self.temp_dir, n), 'w').close()

    @patch('mellanox_fw_manager.vmod_manager.run_command')
    def test_each_asic_reads_its_own_device(self, mock_run):
        mock_run.return_value = _result()
        self._make('mt53124_pciconf0_vmdl_0', 'mt53124_pciconf1_vmdl_0')
        self.assertEqual(
            vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir,
                                              asic_index=0),
            'mt53124_pciconf0_vmdl_0')
        self.assertEqual(
            vmod_manager.find_vmod_mst_device(device_dir=self.temp_dir,
                                              asic_index=1),
            'mt53124_pciconf1_vmdl_0')
