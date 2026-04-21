# SPDX-License-Identifier: GPL-3.0-or-later
"""CLI entrypoint for the local Control and State API."""

from venus_evcharger.control.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
