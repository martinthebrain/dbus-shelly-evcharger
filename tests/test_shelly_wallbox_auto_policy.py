# SPDX-License-Identifier: GPL-3.0-or-later
import unittest

from dbus_shelly_wallbox_auto_policy import AutoPolicy, AutoStopEwmaPolicy


class TestAutoPolicy(unittest.TestCase):
    def test_ewma_policy_clamps_negative_volatility_bounds(self):
        policy = AutoStopEwmaPolicy(
            base_alpha=0.35,
            stable_alpha=0.55,
            volatile_alpha=0.15,
            volatility_low_watts=-1.0,
            volatility_high_watts=-2.0,
        )

        policy.clamp()

        self.assertEqual(policy.volatility_low_watts, 0.0)
        self.assertEqual(policy.volatility_high_watts, 0.0)

    def test_percentage_helper_clamps_out_of_range_values(self):
        self.assertEqual(AutoPolicy._clamp_percentage(120.0, "AutoMinSoc"), 100.0)
        self.assertEqual(AutoPolicy._clamp_percentage(-5.0, "AutoResumeSoc"), 0.0)
