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
Tests for `mlnx-fw-manager --burn` (OF-7, OF-8, OF-9) and the MFA shape
classifier it relies on.

The central idea under test: `--component` is an **assertion** the tool
verifies against the supplied image, not a filter over what gets written. MFT
has no component-selective burn, so a burn writes the whole matched image; the
tool's job is to refuse when that does not match the operator's stated intent.
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

PSID = 'MT_0000001916'


def _result(rc=0, stdout='', stderr=''):
    return MagicMock(returncode=rc, stdout=stdout, stderr=stderr)


class TestClassifyMfa(unittest.TestCase):
    """OF-9: two probes decide what a burn of this archive will write."""

    def test_combined_when_psid_present_and_oe_version_found(self):
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, f'PN {PSID} FW 39.2020.0752')), \
             patch.object(vmod_manager, '_primary_image_type', return_value='FS6'), \
             patch.object(vmod_manager, 'get_available_vmod_version',
                          return_value='95.2020.0752'):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/c.mfa', PSID),
                             vmod_manager.SHAPE_COMBINED)

    def test_asic_only_when_no_oe_component(self):
        """`flint --component_type VMOD q` answers 'not supported' (rc 1)."""
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, f'PN {PSID} FW 39.2020.0752')), \
             patch.object(vmod_manager, '_primary_image_type', return_value='FS6'), \
             patch.object(vmod_manager, 'get_available_vmod_version',
                          return_value=None):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/a.mfa', PSID),
                             vmod_manager.SHAPE_ASIC_ONLY)

    def test_oe_only_when_primary_image_is_fsvmod(self):
        """
        Found on hardware: a vModule-only archive can carry the ASIC's PSID,
        and answers the VMOD probe with a version. Only the primary image type
        separates it from a combined archive - without that check it is
        misclassified as combined and `--component asic` is accepted against
        an archive with no ASIC firmware in it.
        """
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, f'PN {PSID} FW 95.2020.0752')), \
             patch.object(vmod_manager, '_primary_image_type',
                          return_value='FSVMOD'):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/v.mfa', PSID),
                             vmod_manager.SHAPE_OE_ONLY)

    def test_combined_is_not_confused_with_oe_only(self):
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, f'PN {PSID} FW 39.2020.0752')), \
             patch.object(vmod_manager, '_primary_image_type',
                          return_value='FS6'), \
             patch.object(vmod_manager, 'get_available_vmod_version',
                          return_value='95.2020.0752'):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/c.mfa', PSID),
                             vmod_manager.SHAPE_COMBINED)

    def test_unreadable_package_falls_through_to_the_normal_path(self):
        """A failed probe must not be read as 'vModule-only'."""
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, f'PN {PSID} FW 39.2020.0752')), \
             patch.object(vmod_manager, '_primary_image_type', return_value=None), \
             patch.object(vmod_manager, 'get_available_vmod_version',
                          return_value=None):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/x.mfa', PSID),
                             vmod_manager.SHAPE_ASIC_ONLY)

    def test_no_match_when_psid_absent(self):
        """
        The vModule-only archive is keyed by its own PSIDs and carries no
        entry for the ASIC, so it cannot be burned to this device at all.
        """
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, 'PN WRN0000000004 FW 95.2020.2924')):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/v.mfa', PSID),
                             vmod_manager.SHAPE_NO_MATCH)

    def test_no_match_when_list_content_fails(self):
        with patch.object(vmod_manager, 'run_command', return_value=_result(1, '')):
            self.assertEqual(vmod_manager.classify_mfa('/tmp/x.mfa', PSID),
                             vmod_manager.SHAPE_NO_MATCH)

    def test_psid_presence_is_decided_on_list_content_not_query(self):
        """
        `mlxfwmanager --query` prints 'No matching image found' and still
        exits 0, so a caller checking only the return code reads an
        inapplicable archive as 'up to date'. The classifier must not use it.
        """
        with patch.object(vmod_manager, 'run_command',
                          return_value=_result(0, 'no psid here')) as mock_run:
            vmod_manager.classify_mfa('/tmp/x.mfa', PSID)
        argv = mock_run.call_args[0][0]
        self.assertIn('--list-content', argv)
        self.assertNotIn('--query', argv)


class BurnCliBase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.marker = os.path.join(self.temp_dir, 'fw_auto_upgrade_disabled')
        self.image = os.path.join(self.temp_dir, 'fw.mfa')
        with open(self.image, 'wb') as f:
            f.write(b'x' * 64)

        self._marker = patch.object(fw_policy, 'AUTO_UPGRADE_DISABLED_MARKER',
                                    self.marker)
        self._marker.start()
        self._cpo = patch.object(vmod_manager, 'is_cpo_platform', return_value=True)
        self._cpo.start()
        self._tty = patch('mellanox_fw_manager.main.sys.stdin.isatty',
                          return_value=False)
        self._tty.start()

        self.manager = MagicMock()
        self.manager.asic_index = 0
        self.manager.pci_id = '0000:02:00.0'
        self.manager.get_psid.return_value = PSID
        self.manager.burn_image.return_value = True
        # By default a delta exists, so burns proceed.
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0756', 'upgrade_needed': True, 'error': None},
            {'name': 'OE_FW', 'current': '95.10.0752',
             'available': '95.2020.0756', 'upgrade_needed': True, 'error': None},
        ]

        self.coordinator = MagicMock()
        self.coordinator.get_default_mfa_path.return_value = self.image
        self.coordinator.select_managers.return_value = [self.manager]
        self.coordinator.get_asic_count.return_value = 1
        self._fwctl = patch('sonic_platform.device_data.DeviceDataManager.'
                            'is_module_host_management_mode', return_value=True)
        self._fwctl.start()
        self._coord = patch('mellanox_fw_manager.main.FirmwareCoordinator',
                            return_value=self.coordinator)
        self._coord.start()

    def tearDown(self):
        self._coord.stop()
        self._fwctl.stop()
        self._tty.stop()
        self._cpo.stop()
        self._marker.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _shape(self, shape):
        return patch.object(vmod_manager, 'classify_mfa', return_value=shape)


class TestBurnComponentAssertion(BurnCliBase):
    """OF-9: --component is verified against the image, then honoured."""

    def test_component_all_accepts_a_combined_image(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            self.assertEqual(
                fw_main.handle_burn('all', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once_with(self.image)

    def test_component_asic_accepts_an_asic_only_image(self):
        with self._shape(vmod_manager.SHAPE_ASIC_ONLY):
            self.assertEqual(
                fw_main.handle_burn('asic', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()

    def test_component_asic_refuses_a_combined_image(self):
        """
        A burn writes the whole matched image, so honouring 'asic' against a
        combined archive would silently rewrite OE_FW too.
        """
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('asic', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_component_oe_accepts_a_combined_image(self):
        """The combined archive is the only one carrying OE firmware."""
        with self._shape(vmod_manager.SHAPE_COMBINED):
            self.assertEqual(
                fw_main.handle_burn('oe', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()

    def test_component_oe_refuses_an_asic_only_image(self):
        with self._shape(vmod_manager.SHAPE_ASIC_ONLY):
            rc = fw_main.handle_burn('oe', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_component_asic_refuses_an_oe_only_image(self):
        with self._shape(vmod_manager.SHAPE_OE_ONLY):
            rc = fw_main.handle_burn('asic', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_component_oe_accepts_an_oe_only_image(self):
        with self._shape(vmod_manager.SHAPE_OE_ONLY):
            self.assertEqual(
                fw_main.handle_burn('oe', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()

    def test_component_all_on_an_oe_only_image_says_asic_is_not_written(self):
        with self._shape(vmod_manager.SHAPE_OE_ONLY), \
             patch('builtins.print') as mock_print:
            self.assertEqual(
                fw_main.handle_burn('all', self.image, 'all', False, True, False), 0)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('ASIC_FW is not', out)

    def test_image_without_this_psid_is_refused(self):
        with self._shape(vmod_manager.SHAPE_NO_MATCH):
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_refusal_names_the_alternative(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('asic', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('ASIC-only', out)
        self.assertIn('--component all', out)


class TestBurnBehaviour(BurnCliBase):
    """OF-7, OF-8."""

    def test_dry_run_returns_ten_when_a_delta_exists(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', self.image, 'all', True, True, False)
        self.assertEqual(rc, 10)
        self.manager.burn_image.assert_not_called()

    def test_dry_run_returns_zero_when_up_to_date(self):
        """
        10 means 'an upgrade is required'. Returning it for an image already
        on the box tells the operator to burn firmware they already have.
        """
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0752', 'upgrade_needed': False, 'error': None},
        ]
        with self._shape(vmod_manager.SHAPE_ASIC_ONLY):
            rc = fw_main.handle_burn('asic', self.image, 'all', True, True, False)
        self.assertEqual(rc, 0)
        self.manager.burn_image.assert_not_called()

    def test_dry_run_reports_current_and_available_per_component(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', True, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('ASIC_FW', out)
        self.assertIn('39.2020.0756', out)
        self.assertIn('differs -> burn', out)

    def test_up_to_date_device_is_not_burned(self):
        """
        Without this every --burn is a real multi-minute flash write plus an
        ASIC reset requirement, even when it changes nothing.
        """
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0752', 'upgrade_needed': False, 'error': None},
        ]
        with self._shape(vmod_manager.SHAPE_ASIC_ONLY), \
             patch('builtins.print') as mock_print:
            rc = fw_main.handle_burn('asic', self.image, 'all', False, True, False)
        self.assertEqual(rc, 0)
        self.manager.burn_image.assert_not_called()
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('nothing to do', out)
        self.assertIn('0 burned, 1 skipped', out)

    def test_summary_line_reports_what_happened(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('1 burned, 0 skipped, 0 errors', out)

    def test_a_real_burn_never_returns_ten(self):
        """
        10 is the --dry-run answer to 'is a write needed?'. A real burn answers
        'did the write succeed?' instead: it finds the same delta, writes it,
        and returns 0. A caller that treated 10 as 'work remaining' would loop
        forever if a real burn returned it.
        """
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 0)
        self.manager.burn_image.assert_called_once()

    def test_zero_does_not_distinguish_burned_from_skipped(self):
        """
        Documented in 2.10.3: a scripting caller cannot tell from the exit code
        alone whether flash was written. Both cases exit 0 and are separated
        only by the summary line. Pinned so the contract is not narrowed by
        accident - and so that changing it is a deliberate act.
        """
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            burned_rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        burned_out = ' '.join(str(c) for c in mock_print.call_args_list)

        self.manager.reset_mock()
        self.manager.get_psid.return_value = PSID
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0752', 'upgrade_needed': False, 'error': None},
        ]
        with self._shape(vmod_manager.SHAPE_ASIC_ONLY), \
             patch('builtins.print') as mock_print:
            skipped_rc = fw_main.handle_burn('asic', self.image, 'all', False, True, False)
        skipped_out = ' '.join(str(c) for c in mock_print.call_args_list)

        self.assertEqual(burned_rc, skipped_rc, 0)
        self.assertIn('1 burned, 0 skipped', burned_out)
        self.assertIn('0 burned, 1 skipped', skipped_out)

    UNREADABLE_OE = [
        {'name': 'OE_FW', 'current': None, 'available': '95.2020.0756',
         'upgrade_needed': False, 'error': 'unable to read the version'},
    ]

    def test_an_unreadable_component_is_an_error_not_a_success(self):
        """
        "Nothing to do" and "I could not tell" must not share an exit code. A
        component whose version cannot be read leaves its state unknown, and
        reporting 0 would tell a convergence script the switch is converged
        when nothing was written and nothing is known.
        """
        self.manager.get_component_versions.return_value = self.UNREADABLE_OE
        with self._shape(vmod_manager.SHAPE_OE_ONLY), \
             patch('builtins.print') as mock_print:
            rc = fw_main.handle_burn('oe', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('1 errors', out)
        self.assertIn('state is unknown', out)

    def test_dry_run_does_not_report_unknown_as_up_to_date(self):
        """0 from --dry-run means "up to date"; an unreadable component is not."""
        self.manager.get_component_versions.return_value = self.UNREADABLE_OE
        with self._shape(vmod_manager.SHAPE_OE_ONLY):
            rc = fw_main.handle_burn('oe', self.image, 'all', True, True, False)
        self.assertEqual(rc, 1)

    def test_a_real_delta_still_outranks_an_unreadable_sibling_on_dry_run(self):
        """
        10 is actionable and 1 is not, so a found delta wins. The errored
        component is still named in the per-row output.
        """
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0756', 'upgrade_needed': True, 'error': None},
            {'name': 'OE_FW', 'current': None, 'available': '95.2020.0756',
             'upgrade_needed': False, 'error': 'unable to read the version'},
        ]
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            rc = fw_main.handle_burn('all', self.image, 'all', True, True, False)
        self.assertEqual(rc, 10)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('error', out)

    def test_a_successful_burn_alongside_an_unreadable_component_is_an_error(self):
        """
        The write succeeded, but one component's state is still unknown
        afterwards, so the run cannot claim success. "Reboot to activate" is
        suppressed for the same reason.
        """
        self.manager.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0756', 'upgrade_needed': True, 'error': None},
            {'name': 'OE_FW', 'current': None, 'available': '95.2020.0756',
             'upgrade_needed': False, 'error': 'unable to read the version'},
        ]
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_called_once()
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('1 burned, 0 skipped, 1 errors', out)
        self.assertNotIn('Reboot to activate', out)

    def test_missing_image_is_an_error(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', '/nonexistent.mfa', 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_defaults_to_the_installed_image_mfa(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', None, 'all', False, True, False)
        self.coordinator.get_default_mfa_path.assert_called_once()
        self.manager.burn_image.assert_called_once_with(self.image)

    def test_burn_failure_returns_one(self):
        self.manager.burn_image.return_value = False
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)

    def test_works_while_the_policy_is_disabled(self):
        """
        OF-8. Refusing here would leave the operator with no supported way to
        upgrade at all - the policy being off is exactly when they need this.
        """
        fw_policy.disable_auto_upgrade('owner took over', self.marker)
        with self._shape(vmod_manager.SHAPE_COMBINED):
            self.assertEqual(
                fw_main.handle_burn('all', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()

    def test_burn_does_not_change_the_policy(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertTrue(fw_policy.is_auto_upgrade_enabled(self.marker))

    def test_reminds_operator_the_auto_flow_will_revert_it(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('--auto-upgrade disable', out)

    def test_interactive_caller_must_confirm(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('mellanox_fw_manager.main.sys.stdin.isatty', return_value=True), \
             patch('builtins.input', return_value='n'):
            rc = fw_main.handle_burn('all', self.image, 'all', False, False, False)
        self.assertEqual(rc, 2)
        self.manager.burn_image.assert_not_called()

    def test_non_interactive_caller_is_not_prompted(self):
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.input') as mock_input:
            fw_main.handle_burn('all', self.image, 'all', False, False, False)
        mock_input.assert_not_called()


class TestBurnUsesMstSession(BurnCliBase):
    """
    Found on hardware: without an MST session mlxfwmanager cannot reach the
    device over DMA, falls back to a register-access burn, and the burn took
    695 s and then failed. The automatic flow already burns inside
    mst_session(); the manual path must too.
    """

    def test_burn_runs_inside_an_mst_session(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.coordinator.mst_session.assert_called_once()

    def test_dry_run_also_holds_the_session(self):
        """Classification extracts from the MFA; keep one session for both."""
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', True, True, False)
        self.coordinator.mst_session.assert_called_once()

    def test_stale_semaphore_is_cleared_before_writing(self):
        """
        An aborted earlier burn leaves the flash semaphore locked; the next
        burn then fails in ~2 s with 'Failed to lock flash semaphore' having
        written nothing.
        """
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.manager.clear_semaphore.assert_called_once()

    def test_semaphore_is_not_cleared_on_a_dry_run(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', True, True, False)
        self.manager.clear_semaphore.assert_not_called()


class TestBurnDeviceHealthGate(BurnCliBase):
    """
    An ASIC left unreadable by an earlier failed burn answers
    `mlxfwmanager --query` with 'Failed to open device' and an empty PSID.
    Burning into that state took a switch down during testing and needed a
    reboot to recover, so the burn must refuse before writing.
    """

    def test_unreadable_device_is_refused(self):
        self.manager.get_psid.return_value = None
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_empty_psid_is_treated_as_unreadable(self):
        self.manager.get_psid.return_value = ''
        with self._shape(vmod_manager.SHAPE_COMBINED):
            rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_gate_runs_before_classification(self):
        """
        Ordering is the point: classify_mfa extracts from the MFA and costs
        seconds, and its failure message blames the image rather than the
        device. Neither is wanted for a device we will not write to.
        """
        self.manager.get_psid.return_value = None
        with patch.object(vmod_manager, 'classify_mfa') as mock_classify:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        mock_classify.assert_not_called()

    def test_message_names_the_device_not_the_image(self):
        self.manager.get_psid.return_value = None
        with patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('not responding', out)
        self.assertIn('Reboot', out)
        self.assertNotIn('does not carry firmware', out)

    def test_dry_run_is_gated_too(self):
        """A dry run on a wedged device is still a pointless MFA extract."""
        self.manager.get_psid.return_value = None
        with patch.object(vmod_manager, 'classify_mfa') as mock_classify:
            rc = fw_main.handle_burn('all', self.image, 'all', True, True, False)
        self.assertEqual(rc, 1)
        mock_classify.assert_not_called()

    def test_healthy_device_proceeds(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            self.assertEqual(
                fw_main.handle_burn('all', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()


class TestBurnPreconditions(BurnCliBase):
    """2.8: checks that must happen before anything is written."""

    def test_rejected_when_system_under_firmware_control(self):
        self._fwctl.stop()
        try:
            with patch('sonic_platform.device_data.DeviceDataManager.'
                       'is_module_host_management_mode', return_value=False), \
                 self._shape(vmod_manager.SHAPE_COMBINED):
                rc = fw_main.handle_burn('all', self.image, 'all', False, True, False)
        finally:
            self._fwctl.start()
        self.assertEqual(rc, 1)
        self.manager.burn_image.assert_not_called()

    def test_fw_control_check_runs_before_classification(self):
        self._fwctl.stop()
        try:
            with patch('sonic_platform.device_data.DeviceDataManager.'
                       'is_module_host_management_mode', return_value=False), \
                 patch.object(vmod_manager, 'classify_mfa') as mock_classify:
                fw_main.handle_burn('all', self.image, 'all', False, True, False)
        finally:
            self._fwctl.start()
        mock_classify.assert_not_called()

    def test_dry_run_is_not_blocked_by_firmware_control(self):
        """A dry run writes nothing, so it stays useful for inspection."""
        self._fwctl.stop()
        try:
            with patch('sonic_platform.device_data.DeviceDataManager.'
                       'is_module_host_management_mode', return_value=False), \
                 self._shape(vmod_manager.SHAPE_COMBINED):
                rc = fw_main.handle_burn('all', self.image, 'all', True, True, False)
        finally:
            self._fwctl.start()
        self.assertEqual(rc, 10)

    def test_flash_warning_is_printed_even_without_a_tty(self):
        """A non-interactive caller is not prompted, but its log must record it."""
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('writes firmware to ASIC flash', out)

    def test_subset_of_asics_warns_about_non_uniformity(self):
        """1.7: OE firmware must not be left skewed across ASICs."""
        self.coordinator.get_asic_count.return_value = 2
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, '0', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('uniform', out)

    def test_no_warning_when_every_asic_is_burned(self):
        self.coordinator.get_asic_count.return_value = 1
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertNotIn('uniform', out)

    def test_invalid_asic_selector_is_reported_cleanly(self):
        """Not as a traceback."""
        from mellanox_fw_manager.fw_manager import FirmwareUpgradeError
        self.coordinator.select_managers.side_effect = FirmwareUpgradeError(
            'ASIC 9 not found; this system has 1 ASIC(s)')
        with self._shape(vmod_manager.SHAPE_COMBINED), \
             patch('builtins.print') as mock_print:
            rc = fw_main.handle_burn('all', self.image, '9', False, True, False)
        self.assertEqual(rc, 1)
        out = ' '.join(str(c) for c in mock_print.call_args_list)
        self.assertIn('ASIC 9 not found', out)


class TestBurnPerAsic(BurnCliBase):
    """AF-12: the unit of work is the ASIC, never the port."""

    def test_each_asic_is_burned_exactly_once(self):
        second = MagicMock()
        second.asic_index = 1
        second.pci_id = '0000:07:00.0'
        second.get_psid.return_value = PSID
        second.burn_image.return_value = True
        second.get_component_versions.return_value = [
            {'name': 'ASIC_FW', 'current': '39.2020.0752',
             'available': '39.2020.0756', 'upgrade_needed': True, 'error': None},
        ]
        self.coordinator.select_managers.return_value = [self.manager, second]
        self.coordinator.get_asic_count.return_value = 2

        with self._shape(vmod_manager.SHAPE_COMBINED):
            self.assertEqual(
                fw_main.handle_burn('all', self.image, 'all', False, True, False), 0)
        self.manager.burn_image.assert_called_once()
        second.burn_image.assert_called_once()

    def test_asic_selector_is_passed_through(self):
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, '1', False, True, False)
        self.coordinator.select_managers.assert_called_once_with('1')

    def test_psid_is_read_per_asic(self):
        """
        Classification is per device: two ASICs could in principle carry
        different PSIDs, and burning an archive that matches only one of them
        must not be decided from the other.
        """
        with self._shape(vmod_manager.SHAPE_COMBINED):
            fw_main.handle_burn('all', self.image, 'all', False, True, False)
        self.manager.get_psid.assert_called_once()


if __name__ == '__main__':
    unittest.main()
