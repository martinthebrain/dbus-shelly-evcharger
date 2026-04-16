# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


REPO_ROOT = Path("/home/martin/Schreibtisch/cerbo300126/vomCerbo/data/dbus-opendtuAndi/github/dbus-shelly-evcharger")
BOOTSTRAP_SCRIPT = REPO_ROOT / "install.sh"
UPDATER_SCRIPT = REPO_ROOT / "deploy/venus/bootstrap_updater.sh"
UPDATER_HASH = REPO_ROOT / "deploy/venus/bootstrap_updater.sh.sha256"


class TestBootstrapInstallScripts(unittest.TestCase):
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

    def test_bootstrap_updater_syncs_local_source_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_shelly_wallbox/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "shelly_wallbox").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)
            (source_dir / "tests").mkdir(parents=True, exist_ok=True)
            (source_dir / "docs").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            (source_dir / "dbus_shelly_wallbox.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "shelly_wallbox_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.shelly_wallbox.ini").write_text("fresh-config\n", encoding="utf-8")
            (source_dir / "shelly_wallbox/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "tests/should_not_ship.txt").write_text("omit\n", encoding="utf-8")
            (source_dir / "docs/should_not_ship.txt").write_text("omit\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            (target_dir / "deploy/venus/config.shelly_wallbox.ini").write_text("keep-me\n", encoding="utf-8")
            (target_dir / "tests").mkdir(parents=True, exist_ok=True)
            (target_dir / "tests/stale.txt").write_text("stale\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "SHELLY_WALLBOX_SOURCE_DIR": str(source_dir),
                },
            )

            self.assertTrue((target_dir / "dbus_shelly_wallbox.py").is_file())
            self.assertTrue((target_dir / "deploy/venus/install_shelly_wallbox.sh").is_file())
            self.assertTrue((target_dir / "shelly_wallbox/__init__.py").is_file())
            self.assertEqual(
                (target_dir / "deploy/venus/config.shelly_wallbox.ini").read_text(encoding="utf-8"),
                "keep-me\n",
            )
            self.assertFalse((target_dir / "tests").exists())
            self.assertFalse((target_dir / "docs").exists())

    def test_bootstrap_installer_refreshes_local_updater_and_runs_target_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            source_dir = root / "source"

            (source_dir / "deploy/venus/service_shelly_wallbox/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "shelly_wallbox").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            (source_dir / "dbus_shelly_wallbox.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "shelly_wallbox_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.shelly_wallbox.ini").write_text("fresh-config\n", encoding="utf-8")
            (source_dir / "shelly_wallbox/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_shelly_wallbox.sh").write_text(
                "#!/bin/bash\n"
                "printf 'installed\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                encoding="utf-8",
            )

            bootstrap_copy = bootstrap_dir / "install.sh"
            bootstrap_copy.write_text(BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            bootstrap_copy.chmod(0o755)

            signing_key, public_key = self._generate_signing_keypair(root)
            expected_hash = hashlib.sha256(UPDATER_SCRIPT.read_bytes()).hexdigest()
            manifest = {
                "format": 1,
                "channel": "stable",
                "version": "1.0.0",
                "updater_url": str(UPDATER_SCRIPT),
                "updater_sha256": expected_hash,
            }
            manifest_path = root / "bootstrap_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sig_path = root / "bootstrap_manifest.json.sig"
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(manifest_sig_path), str(manifest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            subprocess.run(
                ["bash", str(bootstrap_copy)],
                check=True,
                env={
                    **os.environ,
                    "SHELLY_WALLBOX_TARGET_DIR": str(target_dir),
                    "SHELLY_WALLBOX_SOURCE_DIR": str(source_dir),
                    "SHELLY_WALLBOX_MANIFEST_SOURCE": str(manifest_path),
                    "SHELLY_WALLBOX_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "SHELLY_WALLBOX_BOOTSTRAP_PUBKEY": str(public_key),
                    "SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((bootstrap_dir / ".wallbox-bootstrap/bootstrap_updater.sh").is_file())
            self.assertTrue((target_dir / "installed.txt").is_file())
            self.assertEqual((target_dir / "installed.txt").read_text(encoding="utf-8"), "installed\n")
            self.assertTrue((target_dir / "deploy/venus/install_shelly_wallbox.sh").is_file())

    def test_bootstrap_updater_uses_manifest_bundle_and_skips_when_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            release_dir = root / "release"

            (source_dir / "deploy/venus/service_shelly_wallbox/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "shelly_wallbox").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("9.9.9\n", encoding="utf-8")
            (source_dir / "dbus_shelly_wallbox.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "shelly_wallbox_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_shelly_wallbox.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_shelly_wallbox/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.shelly_wallbox.ini").write_text("fresh-config\n", encoding="utf-8")
            (source_dir / "shelly_wallbox/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")

            release_dir.mkdir()
            bundle_path = release_dir / "wallbox-bundle.tar.gz"
            with tarfile.open(bundle_path, "w:gz") as tar:
                for rel_path in (
                    "install.sh",
                    "LICENSE",
                    "README.md",
                    "SHELLY_PROFILES.md",
                    "version.txt",
                    "dbus_shelly_wallbox.py",
                    "shelly_wallbox_auto_input_helper.py",
                    "deploy/venus",
                    "shelly_wallbox",
                    "scripts/ops",
                ):
                    tar.add(source_dir / rel_path, arcname=rel_path)

            bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
            signing_key, public_key = self._generate_signing_keypair(root)
            manifest = {
                "format": 1,
                "channel": "stable",
                "version": "9.9.9",
                "bundle_url": str(bundle_path),
                "bundle_sha256": bundle_hash,
            }
            manifest_path = release_dir / "bootstrap_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sig_path = release_dir / "bootstrap_manifest.json.sig"
            subprocess.run(
                ["openssl", "dgst", "-sha256", "-sign", str(signing_key), "-out", str(manifest_sig_path), str(manifest_path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            (target_dir / "noUpdate").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "noUpdate").write_text("", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "SHELLY_WALLBOX_MANIFEST_SOURCE": str(manifest_path),
                    "SHELLY_WALLBOX_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "SHELLY_WALLBOX_BOOTSTRAP_PUBKEY": str(public_key),
                    "SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((target_dir / "current").is_symlink())
            self.assertTrue((target_dir / "current/dbus_shelly_wallbox.py").is_file())
            self.assertTrue((target_dir / "releases/9.9.9/deploy/venus/install_shelly_wallbox.sh").is_file())
            self.assertTrue((target_dir / "current/deploy/venus/install_shelly_wallbox.sh").is_file())
            self.assertEqual((target_dir / ".bootstrap-state/installed_bundle_sha256").read_text(encoding="utf-8"), f"{bundle_hash}\n")
            self.assertEqual((target_dir / ".bootstrap-state/installed_version").read_text(encoding="utf-8"), "9.9.9\n")
            self.assertTrue((target_dir / "noUpdate").is_file())

            sentinel = target_dir / "sentinel.txt"
            sentinel.write_text("keep\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "SHELLY_WALLBOX_MANIFEST_SOURCE": str(manifest_path),
                    "SHELLY_WALLBOX_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "SHELLY_WALLBOX_BOOTSTRAP_PUBKEY": str(public_key),
                    "SHELLY_WALLBOX_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_bootstrap_rolls_back_to_previous_release_when_current_installer_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            current_dir = target_dir / "releases/2.0.0"
            previous_dir = target_dir / "releases/1.0.0"
            (current_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            (previous_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)

            (current_dir / "deploy/venus/install_shelly_wallbox.sh").write_text(
                "#!/bin/bash\nexit 1\n",
                encoding="utf-8",
            )
            (previous_dir / "deploy/venus/install_shelly_wallbox.sh").write_text(
                "#!/bin/bash\n"
                "printf 'rolled-back\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                encoding="utf-8",
            )
            (current_dir / "deploy/venus/install_shelly_wallbox.sh").chmod(0o755)
            (previous_dir / "deploy/venus/install_shelly_wallbox.sh").chmod(0o755)

            (target_dir / "current").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "current").symlink_to(current_dir)
            (target_dir / "previous").symlink_to(previous_dir)

            bootstrap_copy = bootstrap_dir / "install.sh"
            bootstrap_copy.write_text(BOOTSTRAP_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
            bootstrap_copy.chmod(0o755)
            (bootstrap_dir / "noUpdate").write_text("", encoding="utf-8")

            subprocess.run(
                ["bash", str(bootstrap_copy)],
                check=True,
                env={
                    **os.environ,
                    "SHELLY_WALLBOX_TARGET_DIR": str(target_dir),
                },
            )

            self.assertEqual((previous_dir / "installed.txt").read_text(encoding="utf-8"), "rolled-back\n")
            self.assertTrue((target_dir / "current").is_symlink())
            self.assertEqual(os.readlink(target_dir / "current"), str(previous_dir))
