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
Automatic firmware upgrade policy.

The policy is a single bit of durable state: has this switch been handed over
to operator-managed firmware?

The mechanism is deliberately minimal - **the marker file existing means the
automatic firmware upgrade flow is disabled**. The contents are never parsed;
the file holds one line of human readable audit text so that whoever finds it
later knows where it came from.

The marker lives on /host rather than /etc because /etc lives inside the image
squashfs and would be reset by a SONiC-to-SONiC upgrade, which is exactly the
boundary across which the flag must survive.
"""

import os
import logging
from typing import Optional

# The marker file. Existence == automatic firmware upgrade disabled.
#
# Every function below resolves `marker_path` from this constant at call time
# rather than binding it as a default argument, so that the path stays a single
# source of truth and can be redirected in tests.
AUTO_UPGRADE_DISABLED_MARKER = '/host/fw_auto_upgrade_disabled'


def is_auto_upgrade_enabled(marker_path: str = None) -> bool:
    """
    Check whether the automatic firmware upgrade flow is still enabled.

    There is no parse step, so there is no "corrupt flag" state: an empty
    marker, a zero length marker and a marker with arbitrary content all
    disable the flow identically.

    Args:
        marker_path: Path of the marker file (overridable for testing)

    Returns:
        True if automatic firmware upgrade is enabled, False if it has been
        permanently disabled.
    """
    marker_path = marker_path or AUTO_UPGRADE_DISABLED_MARKER
    return not os.path.exists(marker_path)


def get_disable_note(marker_path: str = None) -> Optional[str]:
    """
    Read the audit line recorded when the flow was disabled.

    Purely informational - used to make the skip message in the log useful.
    Any read failure yields None rather than raising, because a policy that is
    set must stay set even if its audit text cannot be read.

    Args:
        marker_path: Path of the marker file (overridable for testing)

    Returns:
        The first line of the marker file, or None if it cannot be read.
    """
    marker_path = marker_path or AUTO_UPGRADE_DISABLED_MARKER
    try:
        with open(marker_path, 'r') as f:
            note = f.readline().strip()
        return note or None
    except (IOError, OSError):
        return None


def disable_auto_upgrade(note: str,
                         marker_path: str = None,
                         logger: logging.Logger = None) -> None:
    """
    Disable the automatic firmware upgrade flow.

    The write is atomic - a temporary file is written, flushed, fsync-ed and
    then renamed over the target, so a crash can never leave a half written
    marker. The containing directory is fsync-ed as well so that the rename
    itself survives a power cut.

    Set explicitly by the operator through
    `mlnx-fw-manager --auto-upgrade disable`, and reversed by
    `--auto-upgrade enable`. It is deliberately NOT a side effect of any
    firmware burn: a second, implicit writer of this state would let automation
    take a switch out of SONiC-managed firmware without anyone asking for it.

    Calling this on an already disabled switch refreshes the audit line and is
    not an error.

    Args:
        note: One line of human readable audit text
        marker_path: Path of the marker file (overridable for testing)
        logger: Optional logger

    Raises:
        OSError: If the marker cannot be persisted. The caller must treat this
            as fatal and abort the upgrade - burning without a durable marker
            is the one state this design must never produce.
    """
    marker_path = marker_path or AUTO_UPGRADE_DISABLED_MARKER
    tmp_path = marker_path + '.tmp'
    directory = os.path.dirname(marker_path) or '.'

    try:
        with open(tmp_path, 'w') as f:
            f.write(note.rstrip('\n') + '\n')
            f.flush()
            os.fsync(f.fileno())
    except OSError:
        # A partial write (disk full, for example) would otherwise leave
        # <marker>.tmp on /host forever.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    os.rename(tmp_path, marker_path)

    # fsync the directory so the rename is durable, not just the file content.
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)

    # The fsync pair above is the POSIX durability guarantee. os.sync() is
    # belt-and-braces on top: losing this marker to a power cut would silently
    # hand firmware management back to SONiC, and the next boot would revert
    # the operator's firmware.
    os.sync()

    if logger:
        logger.warning(
            f"Automatic firmware upgrade has been permanently disabled: {note}"
        )


def enable_auto_upgrade(marker_path: str = None,
                        logger: logging.Logger = None) -> bool:
    """
    Re-enable the automatic firmware upgrade flow (OF-1, OF-6).

    Removing the marker does **not** burn anything. The next automatic run
    converges the switch to the image's MFA in the normal way, so an operator
    who re-enables the flow on a switch carrying their own firmware should
    expect that firmware to be replaced at the next boot, not immediately.

    Calling this on a switch that is already enabled is not an error.

    Args:
        marker_path: Path of the marker file (overridable for testing)
        logger: Optional logger

    Returns:
        True if the marker was removed, False if it was already absent.

    Raises:
        OSError: If the marker exists but cannot be removed. The caller must
            not report success - the policy is still in force.
    """
    marker_path = marker_path or AUTO_UPGRADE_DISABLED_MARKER
    if not os.path.exists(marker_path):
        return False

    os.remove(marker_path)

    # Durability matters in the same way it does for disable: a marker that
    # reappears after a power cut would silently re-disable the flow.
    directory = os.path.dirname(marker_path) or '.'
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    os.sync()

    if logger:
        logger.warning(
            "Automatic firmware upgrade has been re-enabled. No firmware was "
            "burned; the next automatic run will converge this switch to the "
            "firmware shipped in the installed SONiC image."
        )
    return True
