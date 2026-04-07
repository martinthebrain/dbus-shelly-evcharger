#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -eu

: "${SHELLY_STRESS_ITERS:=2000}"
: "${SHELLY_STRESS_THREADS:=6}"

export SHELLY_STRESS_ITERS
export SHELLY_STRESS_THREADS

echo "Running Shelly wallbox stress tests with SHELLY_STRESS_ITERS=${SHELLY_STRESS_ITERS} SHELLY_STRESS_THREADS=${SHELLY_STRESS_THREADS}"
python3 -m unittest tests.test_shelly_wallbox_stress
