# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

from venus_evcharger.core.contracts import normalized_bootstrap_update_status_fields


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "install.sh"
UPDATER_SCRIPT = REPO_ROOT / "deploy/venus/bootstrap_updater.sh"
UPDATER_HASH = REPO_ROOT / "deploy/venus/bootstrap_updater.sh.sha256"


class _BootstrapInstallScriptsBase(unittest.TestCase):
    def _read_normalized_status(self, target_dir: Path) -> dict[str, object]:
        raw_status = json.loads((target_dir / ".bootstrap-state/update_status.json").read_text(encoding="utf-8"))
        return normalized_bootstrap_update_status_fields(raw_status)

    def _read_normalized_latest_audit(self, target_dir: Path) -> dict[str, object]:
        audit_lines = (target_dir / ".bootstrap-state/update_audit.log").read_text(encoding="utf-8").splitlines()
        self.assertTrue(audit_lines)
        return normalized_bootstrap_update_status_fields(json.loads(audit_lines[-1]))

    def _generate_signing_keypair(self, root: Path) -> tuple[Path, Path]:
        private_key = root / "bootstrap_signing.key"
        public_key = root / "bootstrap_signing.pub"
        subprocess.run(
            ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:2048", "-out", str(private_key)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ["openssl", "rsa", "-pubout", "-in", str(private_key), "-out", str(public_key)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return private_key, public_key


__all__ = [name for name in globals() if not name.startswith("__")]
