"""Compatibility wrapper for ``shelly_wallbox.backend.probe``."""

from shelly_wallbox.backend.probe import *  # noqa: F401,F403
from shelly_wallbox.backend.probe import main as _main


if __name__ == "__main__":
    raise SystemExit(_main())
