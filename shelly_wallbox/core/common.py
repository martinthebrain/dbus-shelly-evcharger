# SPDX-License-Identifier: GPL-3.0-or-later
"""Package facade for the legacy common wallbox helpers."""

from dbus_shelly_wallbox_common import *  # noqa: F401,F403
from dbus_shelly_wallbox_common import (
    _auto_state_code,
    _confirmed_relay_state_max_age_seconds,
    _derive_auto_state,
    _fresh_confirmed_relay_output,
)

auto_state_code = _auto_state_code
confirmed_relay_state_max_age_seconds = _confirmed_relay_state_max_age_seconds
derive_auto_state = _derive_auto_state
fresh_confirmed_relay_output = _fresh_confirmed_relay_output
