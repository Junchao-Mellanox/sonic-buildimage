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
vModule (Optical Engine) firmware component support.

On SPC6 the optical engine firmware does not live in a pluggable module - it
lives in the ASIC flash as the VMOD component, shipped alongside ASIC_FW inside
one combined MFA. This module provides the two version reads that the ASIC
firmware path cannot supply:

  * current version, from the live vModule mst device
  * available version, from the VMOD component inside the combined MFA

Both reads return a raw version string. Comparison must go through
normalize_version() - the two reads come from different MFT code paths and
render versions inconsistently ("39.2020.0176" vs "39_2020_0176" vs
"FW 39.2020.0176"). A naive string compare would report a permanent mismatch
and re-burn the vModule on every single boot.
"""

import os
import re
import glob
import shutil
import logging
import tempfile
from typing import Optional, Tuple

from .platform_utils import run_command

# PLDM component identifier of the vModule firmware inside the MFA.
VMOD_COMPONENT_TYPE = 'VMOD'

# Component names used in status reporting.
COMPONENT_ASIC_FW = 'ASIC_FW'
COMPONENT_OE_FW = 'OE_FW'

# mst device naming for the vModule.
#
# Verified on a Chameleon6S (SN6810-LD): 'mst module add' reports
# "Added 16 virtual modules / Added 1 cable devices" and creates
#   /dev/mst/mt53124_pciconf0_vmdl_0 .. _vmdl_15   (the vModules)
#   /dev/mst/mt53124_pciconf0_cable_16             (the cable device)
MST_DEVICE_DIR = '/dev/mst'
VMOD_MST_DEVICE_GLOB = '*_vmdl_*'

# 'mst start --with_i2cdev' - which the firmware coordinator uses - additionally
# exposes I2C-attached vModule representors ('dev-i2c-7_vmdl_9'), which flint
# cannot query (rc=1). The PCI-attached representors are the usable ones, and
# they sort *after* the I2C ones, so the PCI form must be preferred explicitly
# rather than by taking the first match.
VMOD_MST_PCI_DEVICE_GLOB = '*pciconf*_vmdl_*'

# File-mode query output, e.g. 'flint -i <fwpkg> --component_type VMOD q':
#     FW Version:            95.2020.0752
_FW_VERSION_RE = re.compile(r'^\s*FW\s+version\s*:\s*(\S+)\s*$',
                            re.IGNORECASE | re.MULTILINE)

# Device-mode query output is dual-bank and has no plain "FW Version:" line,
# e.g. 'flint -d mt53124_pciconf0_vmdl_0 q full':
#     Image A FW Status:   Not Running, Uncommitted, Valid
#     Image B FW Status:   Running,     Committed,   Valid
#     Image A FW Version:  95.5.0750
#     Image B FW Version:  95.10.0752
# The running bank is the one to compare against the image MFA.
_IMAGE_STATUS_RE = re.compile(
    r'^\s*Image\s+(\S+)\s+FW\s+Status\s*:\s*(.+?)\s*$',
    re.IGNORECASE | re.MULTILINE)
_IMAGE_VERSION_RE = re.compile(
    r'^\s*Image\s+(\S+)\s+FW\s+Version\s*:\s*(\S+)\s*$',
    re.IGNORECASE | re.MULTILINE)

# Matches a firmware version as MFT actually renders it. Verified against all
# 296 SPC6 releases at 39.2020.0748 and above:
#
#   95.2020.2920        151 releases   dotted, no respin
#   95_2020_0966-002    143 releases   underscored, with a respin suffix
#   95_2020_2920          2 releases   underscored, no respin
#
# Separators are interchangeable and carry no meaning. The trailing "-NNN" is
# the release respin number and IS significant - '95_2020_0966' and
# '95_2020_0966-002' are different builds and must not compare equal.
_VERSION_TOKEN_RE = re.compile(
    r'^(?:FW\s+)?(\d+(?:[._]\d+)+)(?:-(\d+))?$', re.IGNORECASE)


# Memo for the available-version read. All ASICs are compared against the same
# MFA, so the fwpkg extract must happen once per (mfa, psid) rather than once
# per ASIC - on an N-ASIC box the per-ASIC cost would otherwise be paid on
# every boot and every syncd start.
_AVAILABLE_VERSION_CACHE = {}


def clear_caches() -> None:
    """Drop memoized version reads. Intended for tests."""
    _AVAILABLE_VERSION_CACHE.clear()


def normalize_version(version: Optional[str]) -> Optional[Tuple[int, ...]]:
    """
    Normalize a firmware version string into a comparable tuple.

    Used by the OE_FW comparison only. Separator rendering is normalized
    away, because the same vModule firmware is reported with dots by some MFT
    code paths and underscores by others - a raw string compare would report a
    permanent mismatch and re-burn the vModule on every boot.

    ASIC_FW deliberately does NOT use this: it keeps the byte-identical
    comparison it had before CPO support, so upgrade decisions on the existing
    NVIDIA platforms are unchanged (AF-7).

    The respin suffix is preserved as a trailing element, because it denotes a
    genuinely different build:

        "95.2020.2920"      -> (95, 2020, 2920)
        "95_2020_2920"      -> (95, 2020, 2920)      same firmware
        "95_2020_0966-002"  -> (95, 2020, 966, 2)
        "95.2020.0966-002"  -> (95, 2020, 966, 2)    same firmware
        "95_2020_0966"      -> (95, 2020, 966)       DIFFERENT build

    Args:
        version: Raw version string from MFT output

    Returns:
        Tuple of ints, or None if the string is not a recognizable version.
    """
    if not version:
        return None
    match = _VERSION_TOKEN_RE.match(version.strip())
    if not match:
        return None
    # The regex only matches digit groups, so int() cannot fail here.
    parts = [int(part) for part in re.split(r'[._]', match.group(1))]
    if match.group(2) is not None:
        parts.append(int(match.group(2)))
    return tuple(parts)


def parse_bank_versions(output: str) -> list:
    """
    Every firmware version the vModule reports, across both banks.

    A burn writes the inactive bank; the new image only becomes the running
    one after an ASIC reset. Between those two points the running bank still
    reports the previous version, so comparing only the running bank would
    report "upgrade needed" for firmware that is already in flash - burning it
    again on every boot and, through syncd.sh's fatal treatment of a non-zero
    --status, blocking syncd.

    Args:
        output: stdout of 'flint -d <vmdl_dev> q full'

    Returns:
        List of version strings, running bank first where identifiable.
    """
    versions = dict(_IMAGE_VERSION_RE.findall(output or ''))
    running = parse_running_bank_version(output)
    ordered = [running] if running else []
    ordered += [v for b, v in sorted(versions.items()) if v != running]
    return ordered


def parse_running_bank_version(output: str) -> Optional[str]:
    """
    Extract the *running* vModule firmware version from a device query.

    The vModule carries two firmware banks and reports both. Only the bank
    whose status begins with "Running" is live; the other is the staged or
    previous image. Comparing the wrong bank against the image MFA would burn
    firmware that is already present, on every boot.

    The status field reads e.g. "Not Running, Uncommitted, Valid" or
    "Running,     Committed,   Valid", so the first comma-separated token is
    matched exactly - a substring test would treat "Not Running" as running.

    Args:
        output: stdout of 'flint -d <vmdl_dev> q full'

    Returns:
        The running bank's version string, or None if no bank reports itself
        as running.
    """
    versions = {bank: version
                for bank, version in _IMAGE_VERSION_RE.findall(output or '')}
    for bank, status in _IMAGE_STATUS_RE.findall(output or ''):
        state = status.split(',')[0].strip().lower()
        if state == 'running':
            return versions.get(bank)
    return None


# ---------------------------------------------------------------------------
# TEMPORARY WORKAROUND - pending an answer from the firmware team.
#
# The device and the MFA report the vModule version in different namespaces,
# with a different number of fields. Measured on sws-cham6s-4 (SN6810-LD,
# MFT 4.36.0) and across 296 SPC6 releases:
#
#     MFA    : 95.2020.0752   or  95_2020_0966-002   (respin on 143/296)
#     Device : 95.10.0752                            (never a respin)
#
# Only the first field (major) and the third (build) correspond. The second
# field differs systematically (2020 vs 10) and the MFA's respin suffix has no
# device-side counterpart, so neither can participate in the comparison.
#
# Comparing anything else never matches and would re-burn the vModule on every
# boot - which, via syncd.sh's fatal treatment of a non-zero --status, would
# stop syncd from starting.
#
# This applies to OE_FW ONLY. Remove once a definitive answer lands - see
# ch6s-fw-upgrade-hld/vmod-version-mismatch-email.md.
SKIP_MIDDLE_VERSION_FIELD = True


def _vmod_comparable(version: Optional[Tuple[int, ...]]) -> Optional[Tuple[int, ...]]:
    """
    Reduce a normalized vModule version to the fields both sides expose.

        (95, 2020, 752)    -> (95, 752)     MFA
        (95, 10,   752)    -> (95, 752)     device      -> equal
        (95, 2020, 966, 2) -> (95, 966)     MFA w/ respin
        (95, 12,   966)    -> (95, 966)     device      -> equal
    """
    if version is None:
        return None
    if SKIP_MIDDLE_VERSION_FIELD and len(version) >= 3:
        return (version[0], version[2])
    return version


def vmod_versions_differ(current: Optional[str], available: Optional[str]) -> bool:
    """
    Compare two vModule version strings.

    Applies the namespace workaround documented above. OE_FW only.

    Args:
        current: Version read from the running vModule bank
        available: Version read from the firmware archive

    Returns:
        True if the versions differ.
    """
    normalized_current = normalize_version(current)
    normalized_available = normalize_version(available)
    if normalized_current is not None and normalized_available is not None:
        return _vmod_comparable(normalized_current) != _vmod_comparable(normalized_available)
    return current != available


def is_cpo_platform(logger: logging.Logger = None) -> bool:
    """
    Detect whether this platform carries CPO optics.

    Uses the CPO topology data (optical_devices.json / cpo.json) that the
    platform API already keys off, so SONiC cannot disagree with itself about
    what the box is. The file is read from the platform directory, which is
    available at every stage of boot - no MST, no SDK and no database are
    required.

    Returns:
        True if the platform declares CPO topology data, False otherwise
        (including when sonic_py_common is unavailable).
    """
    try:
        from sonic_py_common import device_info
        return bool(device_info.get_cpo_data())
    except ImportError:
        # Not a SONiC environment (unit tests, offline tooling).
        return False
    except Exception as e:
        if logger:
            logger.error(
                f"Failed to read CPO topology data: {e}. Treating the platform "
                f"as non-CPO; vModule firmware will not be managed."
            )
        return False


def find_vmod_mst_device(logger: logging.Logger = None,
                         device_dir: str = MST_DEVICE_DIR,
                         asic_index: int = 0) -> Optional[str]:
    """
    Locate the vModule mst device, creating it if necessary.

    'mst module add' exposes the vModule representors under /dev/mst. The call
    is idempotent, so it is safe to issue on every query.

    Args:
        logger: Optional logger
        device_dir: mst device directory (overridable for testing)

    Returns:
        The **bare** mst device name (e.g. 'mt53124_pciconf0_vmdl_0'), or None
        if none was found.

        flint's two device classes take opposite argument forms: a vModule
        device is only accepted as a bare name, while a pciconf device is only
        accepted as a full /dev/mst path. Passing the full path here yields
        "-E- Failed to detect connected cable".
    """
    result = run_command(['/usr/bin/mst', 'module', 'add'], logger=logger,
                         capture_output=True, text=True)
    if result.returncode != 0:
        if logger:
            logger.warning(
                f"'mst module add' failed (rc={result.returncode}): {result.stderr}"
            )

    # Select this ASIC's own representor. The pciconf index in the device name
    # identifies the ASIC, so ASIC 1 must read *pciconf1_vmdl_*, not the first
    # device on the box - otherwise every worker reads ASIC 0 and a stale ASIC
    # is reported with another ASIC's version and never converged.
    devices = sorted(glob.glob(os.path.join(
        device_dir, f'*pciconf{asic_index}_vmdl_*')))
    if not devices:
        devices = sorted(glob.glob(os.path.join(device_dir,
                                                VMOD_MST_PCI_DEVICE_GLOB)))
        if devices and logger:
            logger.warning(
                f"No vModule device for ASIC {asic_index} under {device_dir}; "
                f"falling back to {os.path.basename(devices[0])}"
            )
    if not devices:
        devices = sorted(glob.glob(os.path.join(device_dir,
                                                VMOD_MST_DEVICE_GLOB)))
        if devices and logger:
            logger.warning(
                f"No PCI-attached vModule device under {device_dir}; "
                f"falling back to {os.path.basename(devices[0])}"
            )
    if not devices:
        if logger:
            logger.warning(f"No vModule mst device found under {device_dir}")
        return None
    return os.path.basename(devices[0])


def get_vmod_bank_versions(logger: logging.Logger = None,
                           device_dir: str = MST_DEVICE_DIR,
                           asic_index: int = 0) -> list:
    """
    All vModule bank versions for an ASIC (see parse_bank_versions).

    Returns:
        List of version strings; empty if they could not be read.
    """
    output = _query_vmod_device(logger=logger, device_dir=device_dir,
                               asic_index=asic_index)
    return parse_bank_versions(output) if output else []


def _query_vmod_device(logger: logging.Logger = None,
                       device_dir: str = MST_DEVICE_DIR,
                       asic_index: int = 0) -> Optional[str]:
    """Run the vModule device query and return its stdout, or None."""
    vmod_device = find_vmod_mst_device(logger=logger, device_dir=device_dir,
                                       asic_index=asic_index)
    if not vmod_device:
        return None

    result = run_command(['/usr/bin/flint', '-d', vmod_device, 'q', 'full'],
                         logger=logger, capture_output=True, text=True)
    if result.returncode != 0:
        if logger:
            logger.warning(
                f"flint query failed for vModule device {vmod_device} "
                f"(rc={result.returncode}): {result.stderr}"
            )
        return None
    return result.stdout or ''


def get_current_vmod_version(logger: logging.Logger = None,
                             device_dir: str = MST_DEVICE_DIR,
                             asic_index: int = 0) -> Optional[str]:
    """
    Read the vModule firmware version currently on the device.

    Args:
        logger: Optional logger
        device_dir: mst device directory (overridable for testing)

    Returns:
        Raw version string, or None if it could not be determined.
    """
    output = _query_vmod_device(logger=logger, device_dir=device_dir,
                                asic_index=asic_index)
    if output is None:
        return None

    version = parse_running_bank_version(output)
    if not version:
        if logger:
            logger.warning("No running firmware bank found in flint output")
        return None
    return version


def get_available_vmod_version(mfa_file: str, psid: str,
                               logger: logging.Logger = None) -> Optional[str]:
    """
    Read the vModule firmware version carried by the combined MFA.

    The vModule ships as a PLDM component inside the device PSID's package, so
    the package is extracted to a temporary directory and then queried for the
    VMOD component.

    This is a per-MFA operation, not a per-ASIC one - all ASICs are compared
    against the same file, so the caller should perform it once and share the
    result rather than paying for one extract per ASIC.

    Args:
        mfa_file: Path of the combined MFA
        psid: Device PSID, used to select the package to extract
        logger: Optional logger

    Returns:
        Raw version string, or None if it could not be determined.
    """
    cache_key = (mfa_file, psid)
    if cache_key in _AVAILABLE_VERSION_CACHE:
        return _AVAILABLE_VERSION_CACHE[cache_key]

    version = _extract_and_query_vmod_version(mfa_file, psid, logger)
    _AVAILABLE_VERSION_CACHE[cache_key] = version
    return version


def _extract_and_query_vmod_version(mfa_file: str, psid: str,
                                    logger: logging.Logger = None) -> Optional[str]:
    """Uncached implementation of get_available_vmod_version()."""
    tmp_dir = tempfile.mkdtemp(prefix='vmod-fwpkg-')
    try:
        fwpkg_path = os.path.join(tmp_dir, f'{psid}.fwpkg')
        extract_cmd = ['mlxfwmanager', '-i', mfa_file, '--psid', psid,
                       '-extract', '--extract-file', fwpkg_path]
        result = run_command(extract_cmd, logger=logger,
                             capture_output=True, text=True)
        if result.returncode != 0:
            if logger:
                logger.warning(
                    f"Failed to extract fwpkg for PSID {psid} from {mfa_file} "
                    f"(rc={result.returncode}): {result.stderr}"
                )
            return None

        if not os.path.exists(fwpkg_path):
            if logger:
                logger.warning(
                    f"Extract reported success but {fwpkg_path} does not exist"
                )
            return None

        query_cmd = ['/usr/bin/flint', '-i', fwpkg_path,
                     '--component_type', VMOD_COMPONENT_TYPE, 'q']
        result = run_command(query_cmd, logger=logger,
                             capture_output=True, text=True)
        if result.returncode != 0:
            if logger:
                logger.warning(
                    f"flint query of VMOD component failed for {fwpkg_path} "
                    f"(rc={result.returncode}): {result.stderr}"
                )
            return None

        match = _FW_VERSION_RE.search(result.stdout or '')
        if not match:
            if logger:
                logger.warning(
                    f"No VMOD version found in flint output for {fwpkg_path}"
                )
            return None
        return match.group(1)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# MFA package shapes (AF-11). Which components an archive carries for a given
# device decides what a burn of it will write, and therefore whether it matches
# what the operator asked for (OF-9).
SHAPE_COMBINED = 'combined'      # ASIC_FW + OE_FW
SHAPE_ASIC_ONLY = 'asic-only'    # ASIC_FW only
SHAPE_OE_ONLY = 'oe-only'        # OE_FW only
SHAPE_NO_MATCH = 'no-match'      # does not carry this device's PSID at all

# The primary image of an extracted package. A vModule-only package reports
# FSVMOD here; anything carrying ASIC firmware reports an FS<n> type.
_OE_PRIMARY_IMAGE_TYPE = 'FSVMOD'
_IMAGE_TYPE_RE = re.compile(r'^Image type:\s*(\S+)', re.MULTILINE)


def classify_mfa(mfa_file: str, psid: str, logger=None) -> str:
    """
    Determine which components an MFA carries for a given device (OF-9).

    Three probes:

      1. ``mlxfwmanager --list-content`` must list the device's PSID. If it
         does not, the archive holds no image for this device - burning it is
         impossible. This is checked with ``--list-content`` rather than
         ``--query`` because ``--query`` reports 'No matching image found' and
         still exits 0, which a caller checking only the return code would
         read as 'up to date'.
      2. The extracted package's **primary** image type. ``FSVMOD`` means the
         archive carries vModule firmware only - there is no ASIC image in it,
         so a burn cannot satisfy ``--component asic`` however the PSID
         matched. Measured on the release share: the vModule-only package for
         a Chameleon6S PSID reports ``Image type: FSVMOD``.
      3. ``flint --component_type VMOD q``. A present OE version alongside an
         ASIC primary image means the archive is combined; its absence means
         ASIC-only.

    Probe 2 exists because probe 3 alone cannot tell a combined archive from a
    vModule-only one - both answer with an OE version.

    Args:
        mfa_file: Path to the MFA
        psid: The device's PSID
        logger: Optional logger

    Returns:
        One of SHAPE_COMBINED, SHAPE_ASIC_ONLY, SHAPE_OE_ONLY, SHAPE_NO_MATCH.
    """
    list_cmd = ['/usr/bin/mlxfwmanager', '--list-content', '-i', mfa_file]
    result = run_command(list_cmd, logger=logger, capture_output=True, text=True)
    if result.returncode != 0 or psid not in (result.stdout or ''):
        if logger:
            logger.info(f"{mfa_file} carries no image for PSID {psid}")
        return SHAPE_NO_MATCH

    if _primary_image_type(mfa_file, psid, logger) == _OE_PRIMARY_IMAGE_TYPE:
        return SHAPE_OE_ONLY

    return (SHAPE_COMBINED if get_available_vmod_version(mfa_file, psid, logger)
            else SHAPE_ASIC_ONLY)


def _primary_image_type(mfa_file: str, psid: str, logger=None) -> Optional[str]:
    """
    The ``Image type`` of the package extracted for this PSID.

    Returns None if the package cannot be extracted or queried; callers treat
    that as 'not vModule-only' rather than failing, so an unreadable archive
    still reaches the ordinary ASIC/combined path and its errors surface there.
    """
    tmp_dir = tempfile.mkdtemp(prefix='mfa_shape_')
    fwpkg_path = os.path.join(tmp_dir, 'probe.fwpkg')
    try:
        extract_cmd = ['/usr/bin/mlxfwmanager', '-i', mfa_file, '--psid', psid,
                       '-extract', '--extract-file', fwpkg_path]
        result = run_command(extract_cmd, logger=logger,
                             capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(fwpkg_path):
            return None

        query = run_command(['/usr/bin/flint', '-i', fwpkg_path, 'q'],
                            logger=logger, capture_output=True, text=True)
        if query.returncode != 0:
            return None
        match = _IMAGE_TYPE_RE.search(query.stdout or '')
        return match.group(1) if match else None
    except Exception as e:
        if logger:
            logger.warning(f"Failed to probe image type of {mfa_file}: {e}")
        return None
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
