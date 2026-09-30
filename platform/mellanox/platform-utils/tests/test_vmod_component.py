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
Tests for vModule component integration in the firmware manager.

Covers the decision that matters: a single combined MFA carries both
ASIC_FW and OE_FW, so any component differing triggers one combined burn -
while non-CPO platforms keep today's single component behaviour exactly.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from mellanox_fw_manager.firmware_base import (
    FirmwareManagerBase, FirmwareManagerError
)
from mellanox_fw_manager.spectrum_manager import SpectrumFirmwareManager
from mellanox_fw_manager.bluefield_manager import BluefieldFirmwareManager
from mellanox_fw_manager import vmod_manager
from mellanox_fw_manager import firmware_coordinator as fw_coordinator


class _Manager(FirmwareManagerBase):
    """Minimal concrete manager for exercising the base class."""

    @classmethod
    def get_asic_type_map(cls):
        return {'15b3:cf84': 'SPC6'}

    def _get_mst_device_type(self):
        return 'Spectrum'

    def run_firmware_update(self):
        return True

    def _get_available_firmware_version(self, psid):
        return '39.2021.0002'


def _make_manager(cls=_Manager, vmod_supported=True, **attrs):
    """Build a manager without touching hardware."""
    with patch.object(cls, '_initialize_asic'):
        manager = cls(asic_index=0, pci_id='0000:01:00.0', asic_type='SPC6')
    manager.logger = MagicMock()
    manager.fw_file = '/etc/mlnx/fw-SPC6.mfa'
    manager.psid = 'MT_0000001836'
    manager.current_version = '39.2021.0002'
    manager.available_version = '39.2021.0002'
    manager.vmod_supported = vmod_supported
    manager.current_vmod_version = '95.2021.0002'
    manager.available_vmod_version = '95.2021.0002'
    manager.vmod_versions_loaded = True
    manager.vmod_error = None
    manager.vmod_bank_versions = ['95.2021.0002']
    for key, value in attrs.items():
        setattr(manager, key, value)
    return manager


class TestSupportsVmod(unittest.TestCase):
    """Only ASIC families that actually carry a vModule opt in."""

    def test_base_class_does_not_support_vmod(self):
        manager = _make_manager()
        self.assertFalse(FirmwareManagerBase.supports_vmod(manager))

    def test_spc6_supports_vmod(self):
        with patch.object(SpectrumFirmwareManager, '_initialize_asic'):
            manager = SpectrumFirmwareManager(
                asic_index=0, pci_id='0000:01:00.0', asic_type='SPC6')
        self.assertTrue(manager.supports_vmod())

    def test_spc5_does_not_support_vmod(self):
        """SPC <= SPC5 regression guard."""
        with patch.object(SpectrumFirmwareManager, '_initialize_asic'):
            manager = SpectrumFirmwareManager(
                asic_index=0, pci_id='0000:01:00.0', asic_type='SPC5')
        self.assertFalse(manager.supports_vmod())

    def test_bluefield_does_not_support_vmod(self):
        with patch.object(BluefieldFirmwareManager, '_initialize_asic'):
            manager = BluefieldFirmwareManager(
                asic_index=0, pci_id='0000:01:00.0', asic_type='BF3')
        self.assertFalse(manager.supports_vmod())


class TestInitializeVmod(unittest.TestCase):

    @patch.object(vmod_manager, 'is_cpo_platform', return_value=True)
    @patch.object(vmod_manager, 'get_available_vmod_version')
    @patch.object(vmod_manager, 'get_vmod_bank_versions')
    def test_marks_applicable_without_touching_hardware(self, mock_cur, mock_avail, mock_cpo):
        """
        The version reads need MST started, which has not happened during
        coordinator construction. Touching hardware here would break --status
        and, through syncd.sh, stop syncd from starting.
        """
        manager = _make_manager(vmod_supported=False)
        with patch.object(_Manager, 'supports_vmod', return_value=True):
            manager._initialize_vmod()
        self.assertTrue(manager.vmod_supported)
        mock_cur.assert_not_called()
        mock_avail.assert_not_called()

    @patch.object(vmod_manager, 'is_cpo_platform', return_value=True)
    @patch.object(vmod_manager, 'get_available_vmod_version', return_value='95.2020.0752')
    @patch.object(vmod_manager, 'get_vmod_bank_versions',
                  return_value=['95.10.0752', '95.5.0750'])
    def test_load_versions_populates_and_caches(self, mock_banks, mock_avail, mock_cpo):
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        manager._load_vmod_versions()
        self.assertEqual(manager.current_vmod_version, '95.10.0752')
        self.assertEqual(manager.vmod_bank_versions, ['95.10.0752', '95.5.0750'])
        mock_avail.assert_called_once_with(
            '/etc/mlnx/fw-SPC6.mfa', 'MT_0000001836', logger=manager.logger)
        manager._load_vmod_versions()
        mock_avail.assert_called_once()

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value='95.2020.0752')
    @patch.object(vmod_manager, 'get_vmod_bank_versions',
                  return_value=['95.5.0750', '95.10.0752'])
    def test_staged_bank_counts_as_present(self, mock_banks, mock_avail):
        """
        After a burn the new image sits in the inactive bank until an ASIC
        reset. Re-burning it every boot would block syncd via --status.
        """
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        self.assertFalse(manager.is_vmod_upgrade_required())

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value='95.2020.0752')
    @patch.object(vmod_manager, 'get_vmod_bank_versions',
                  side_effect=RuntimeError('flint exploded'))
    def test_read_exception_sets_error_not_silent_match(self, mock_banks, mock_avail):
        """A raising read must leave an explicit error, not two Nones."""
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        manager._load_vmod_versions()
        self.assertIsNotNone(manager.vmod_error)
        self.assertFalse(manager.is_vmod_upgrade_required())

    @patch.object(vmod_manager, 'is_cpo_platform', return_value=False)
    def test_skipped_on_non_cpo_platform(self, mock_cpo):
        """Non-CPO SPC6 (e.g. Salamandra-class SKUs without CPO optics)."""
        manager = _make_manager(vmod_supported=False)
        with patch.object(_Manager, 'supports_vmod', return_value=True):
            manager._initialize_vmod()
        self.assertFalse(manager.vmod_supported)

    @patch.object(vmod_manager, 'is_cpo_platform', return_value=True)
    def test_skipped_when_manager_does_not_support_vmod(self, mock_cpo):
        manager = _make_manager(vmod_supported=False)
        with patch.object(_Manager, 'supports_vmod', return_value=False):
            manager._initialize_vmod()
        self.assertFalse(manager.vmod_supported)

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value='95.2021.0002')
    @patch.object(vmod_manager, 'get_vmod_bank_versions', return_value=[])
    def test_unreadable_current_version_does_not_raise(self, mock_banks, mock_avail):
        """
        An unreadable vModule must be reported as an error for that ASIC while
        ASIC_FW is still evaluated and burned if needed - not abort the run.
        """
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        manager._load_vmod_versions()
        self.assertIsNotNone(manager.vmod_error)
        self.assertIn('current vModule', manager.vmod_error)
        manager.logger.error.assert_called()

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value=None)
    @patch.object(vmod_manager, 'get_vmod_bank_versions', return_value=['95.10.0752'])
    def test_missing_vmod_in_mfa_does_not_raise(self, mock_banks, mock_avail):
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        manager._load_vmod_versions()
        self.assertIsNotNone(manager.vmod_error)
        self.assertIn('no VMOD component', manager.vmod_error)

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value=None)
    @patch.object(vmod_manager, 'get_vmod_bank_versions', return_value=['95.10.0752'])
    def test_asic_fw_still_burned_when_vmod_unreadable(self, mock_banks, mock_avail):
        """HLD 2.8: VMOD error must not block the ASIC_FW decision."""
        manager = _make_manager(vmod_supported=True,
                                available_version='39.2021.0010')
        manager.vmod_versions_loaded = False
        with patch.object(manager, '_has_prod_image_dev_capable_device',
                          return_value=False):
            self.assertTrue(manager.is_upgrade_required())

    @patch.object(vmod_manager, 'get_available_vmod_version', return_value=None)
    @patch.object(vmod_manager, 'get_vmod_bank_versions', return_value=[])
    def test_vmod_error_does_not_force_a_burn(self, mock_cur, mock_avail):
        manager = _make_manager(vmod_supported=True)
        manager.vmod_versions_loaded = False
        with patch.object(manager, '_has_prod_image_dev_capable_device',
                          return_value=False):
            self.assertFalse(manager.is_upgrade_required())


class TestIsVmodUpgradeRequired(unittest.TestCase):

    def test_false_when_component_not_applicable(self):
        manager = _make_manager(vmod_supported=False)
        self.assertFalse(manager.is_vmod_upgrade_required())

    def test_false_when_versions_equal(self):
        manager = _make_manager()
        self.assertFalse(manager.is_vmod_upgrade_required())

    def test_false_across_version_renderings(self):
        """Underscore vs dotted rendering must not trigger a burn."""
        manager = _make_manager(current_vmod_version='95_2021_0002',
                                available_vmod_version='95.2021.0002')
        self.assertFalse(manager.is_vmod_upgrade_required())

    def test_true_when_versions_differ(self):
        manager = _make_manager(available_vmod_version='95.2021.0010')
        self.assertTrue(manager.is_vmod_upgrade_required())


class TestIsUpgradeRequired(unittest.TestCase):
    """Any component differing triggers one combined burn."""

    def _no_dev_capable(self, manager):
        return patch.object(manager, '_has_prod_image_dev_capable_device',
                            return_value=False)

    def test_no_delta_means_no_upgrade(self):
        manager = _make_manager()
        with self._no_dev_capable(manager):
            self.assertFalse(manager.is_upgrade_required())

    def test_asic_delta_triggers_upgrade(self):
        manager = _make_manager(available_version='39.2021.0010')
        with self._no_dev_capable(manager):
            self.assertTrue(manager.is_upgrade_required())

    def test_vmod_delta_triggers_upgrade(self):
        """The gap this feature closes: vMod-only bump used to be a no-op."""
        manager = _make_manager(available_vmod_version='95.2021.0010')
        with self._no_dev_capable(manager):
            self.assertTrue(manager.is_upgrade_required())

    def test_both_delta_triggers_upgrade(self):
        manager = _make_manager(available_version='39.2021.0010',
                                available_vmod_version='95.2021.0010')
        with self._no_dev_capable(manager):
            self.assertTrue(manager.is_upgrade_required())

    def test_non_cpo_platform_unaffected_by_vmod_logic(self):
        manager = _make_manager(vmod_supported=False,
                                current_vmod_version=None,
                                available_vmod_version=None)
        with self._no_dev_capable(manager):
            self.assertFalse(manager.is_upgrade_required())

    def test_dev_capable_still_triggers_upgrade(self):
        """Pre-existing behaviour must survive the vModule addition."""
        manager = _make_manager()
        with patch.object(manager, '_has_prod_image_dev_capable_device',
                          return_value=True):
            self.assertTrue(manager.is_upgrade_required())

    def test_undeterminable_security_state_still_raises(self):
        manager = _make_manager()
        with patch.object(manager, '_has_prod_image_dev_capable_device',
                          return_value=None):
            with self.assertRaises(FirmwareManagerError):
                manager.is_upgrade_required()


class TestAsicComparisonIsByteIdentical(unittest.TestCase):
    """
    AF-7: platforms without a vModule must behave exactly as before CPO
    support. ASIC_FW therefore keeps the plain string comparison it had, with
    no normalization - a formatting difference is still treated as a version
    difference, as it always was.
    """

    def test_separator_difference_is_still_an_upgrade(self):
        manager = _make_manager(current_version='39_2021_0002',
                                available_version='39.2021.0002')
        self.assertTrue(manager.is_upgrade_required())
        components = {c['name']: c for c in manager.get_component_versions()}
        self.assertTrue(components['ASIC_FW']['upgrade_needed'])

    def test_identical_strings_are_not_an_upgrade(self):
        manager = _make_manager(current_version='39.2021.0002',
                                available_version='39.2021.0002')
        with patch.object(manager, '_has_prod_image_dev_capable_device',
                          return_value=False):
            self.assertFalse(manager.is_upgrade_required())
        components = {c['name']: c for c in manager.get_component_versions()}
        self.assertFalse(components['ASIC_FW']['upgrade_needed'])

    def test_real_version_difference_is_an_upgrade(self):
        manager = _make_manager(current_version='39.2021.0002',
                                available_version='39.2021.0004')
        self.assertTrue(manager.is_upgrade_required())


class TestGetComponentVersions(unittest.TestCase):

    def test_asic_only_on_non_cpo_platform(self):
        manager = _make_manager(vmod_supported=False)
        components = manager.get_component_versions()
        self.assertEqual([c['name'] for c in components], ['ASIC_FW'])

    def test_both_components_on_cpo_platform(self):
        manager = _make_manager()
        components = manager.get_component_versions()
        self.assertEqual([c['name'] for c in components],
                         ['ASIC_FW', 'OE_FW'])

    def test_reports_per_component_upgrade_need(self):
        manager = _make_manager(available_vmod_version='95.2021.0010')
        components = {c['name']: c for c in manager.get_component_versions()}
        self.assertFalse(components['ASIC_FW']['upgrade_needed'])
        self.assertTrue(components['OE_FW']['upgrade_needed'])
        self.assertIsNone(components['OE_FW']['error'])
        self.assertEqual(components['OE_FW']['current'], '95.2021.0002')
        self.assertEqual(components['OE_FW']['available'], '95.2021.0010')




class TestCoordinatorMstSession(unittest.TestCase):
    """
    The version reads address an mst device, so every path that inspects
    firmware versions must hold an MST session - not just the burn.
    """

    def _coordinator(self):
        from mellanox_fw_manager.firmware_coordinator import FirmwareCoordinator
        coordinator = FirmwareCoordinator.__new__(FirmwareCoordinator)
        coordinator.logger = MagicMock()
        coordinator.managers = []
        coordinator.fw_bin_path = '/etc/mlnx'
        return coordinator

    def test_mst_session_starts_the_driver(self):
        coordinator = self._coordinator()
        coordinator._start_mst = MagicMock(return_value=True)
        with coordinator.mst_session() as started:
            self.assertTrue(started)
        coordinator._start_mst.assert_called_once()

    @staticmethod
    def _mst_argvs(mock_run):
        return [c[0][0] for c in mock_run.call_args_list if c[0]]

    def test_mst_session_does_not_stop_the_driver(self):
        """
        'mst stop' unloads mst_pci/mst_pciconf unconditionally - measured on
        hardware, it succeeds even while another process holds a /dev/mst
        device open. Since syncd.sh runs --status on every syncd start, and
        once per namespace in parallel on multi-ASIC, stopping here could pull
        the driver out from under a concurrent burn or a sibling --status.

        Asserted against the commands actually issued rather than against a
        helper method, so that reintroducing a stop by any route fails here.
        """
        coordinator = self._coordinator()
        with patch.object(fw_coordinator, 'run_command',
                          return_value=MagicMock(returncode=0)) as mock_run:
            with coordinator.mst_session():
                pass
        argvs = self._mst_argvs(mock_run)
        self.assertIn(['/usr/bin/mst', 'start', '--with_i2cdev'], argvs)
        self.assertFalse([a for a in argvs if 'stop' in a], argvs)

    def test_mst_session_does_not_stop_on_exception_either(self):
        """A stop in a `finally` is the way this rule would come back."""
        coordinator = self._coordinator()
        with patch.object(fw_coordinator, 'run_command',
                          return_value=MagicMock(returncode=0)) as mock_run:
            with self.assertRaises(RuntimeError):
                with coordinator.mst_session():
                    raise RuntimeError('boom')
        argvs = self._mst_argvs(mock_run)
        self.assertFalse([a for a in argvs if 'stop' in a], argvs)

    def test_the_coordinator_has_no_stop_helper_at_all(self):
        """
        The helper was removed rather than left uncalled: an unused _stop_mst()
        is an invitation to wire it into a teardown and undo the decision.
        """
        from mellanox_fw_manager.firmware_coordinator import FirmwareCoordinator
        self.assertFalse(hasattr(FirmwareCoordinator, '_stop_mst'))

    def test_mst_session_reports_a_failed_start(self):
        """ignore-mst-start-failure yields False rather than raising."""
        coordinator = self._coordinator()
        coordinator._start_mst = MagicMock(return_value=False)
        with coordinator.mst_session() as started:
            self.assertFalse(started)

    def test_check_upgrade_required_holds_an_mst_session(self):
        """
        Regression guard: without this, 'mlnx-fw-manager --status' fails on a
        CPO platform and syncd.sh refuses to start syncd.
        """
        coordinator = self._coordinator()
        order = []
        coordinator._start_mst = MagicMock(
            side_effect=lambda: order.append('mst_start') or True)
        manager = MagicMock()
        manager.asic_index = 0
        manager.vmod_supported = True
        manager.is_upgrade_required.side_effect = \
            lambda: order.append('version_check') or False
        manager._load_vmod_versions.side_effect = \
            lambda: order.append('load_vmod')
        coordinator.managers = [manager]

        coordinator.check_upgrade_required()

        # MST is started before the reads and deliberately left running; see
        # test_mst_session_does_not_stop_the_driver.
        self.assertEqual(order, ['mst_start', 'load_vmod', 'version_check'])

    def test_non_cpo_platform_starts_no_mst_session(self):
        """
        AF-7: SPC <= SPC5 and BlueField must keep today's behaviour, including
        not paying for an MST start during a version check.
        """
        coordinator = self._coordinator()
        coordinator._start_mst = MagicMock(return_value=True)
        manager = MagicMock()
        manager.asic_index = 0
        manager.vmod_supported = False
        manager.is_upgrade_required.return_value = False
        coordinator.managers = [manager]

        coordinator.check_upgrade_required()

        coordinator._start_mst.assert_not_called()
        manager._load_vmod_versions.assert_not_called()

    def test_needs_vmod_versions_requires_explicit_opt_in(self):
        coordinator = self._coordinator()
        placeholder = MagicMock()          # every attribute is truthy
        coordinator.managers = [placeholder]
        self.assertFalse(coordinator.needs_vmod_versions())

    def test_load_vmod_versions_survives_a_failing_manager(self):
        coordinator = self._coordinator()
        bad = MagicMock()
        bad.asic_index = 0
        bad._load_vmod_versions.side_effect = RuntimeError('flint exploded')
        good = MagicMock()
        good.asic_index = 1
        coordinator.managers = [bad, good]
        coordinator.load_vmod_versions()
        good._load_vmod_versions.assert_called_once()
        coordinator.logger.error.assert_called()

if __name__ == '__main__':
    unittest.main()
