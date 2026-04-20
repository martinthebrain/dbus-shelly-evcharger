# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from venus_evcharger.core.contracts import normalized_bootstrap_update_status_fields


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "install.sh"
UPDATER_SCRIPT = REPO_ROOT / "deploy/venus/bootstrap_updater.sh"
UPDATER_HASH = REPO_ROOT / "deploy/venus/bootstrap_updater.sh.sha256"


class TestBootstrapInstallScripts(unittest.TestCase):
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

    def test_bootstrap_updater_syncs_local_source_and_preserves_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)
            (source_dir / "tests").mkdir(parents=True, exist_ok=True)
            (source_dir / "docs").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("Version: 1.2.3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\n"
                "ConfigSchemaVersion=1\n"
                "Host=template-host\n"
                "Mode=0\n"
                "NewToggle=1\n"
                "\n"
                "[Backend]\n"
                "Type=shelly\n"
                "ExtraSetting=42\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "tests/should_not_ship.txt").write_text("omit\n", encoding="utf-8")
            (source_dir / "docs/should_not_ship.txt").write_text("omit\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            original_config = (
                "[DEFAULT]\n"
                "# keep this local host comment\n"
                "Host=keep-me\n"
                "Mode=2\n"
                "\n"
                "[Backend]\n"
                "# keep this local backend comment\n"
                "Type=custom\n"
            )
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                original_config,
                encoding="utf-8",
            )
            (target_dir / "tests").mkdir(parents=True, exist_ok=True)
            (target_dir / "tests/stale.txt").write_text("stale\n", encoding="utf-8")

            subprocess.run(
                ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                check=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                },
            )

            self.assertTrue((target_dir / "venus_evcharger_service.py").is_file())
            self.assertTrue((target_dir / "deploy/venus/install_venus_evcharger_service.sh").is_file())
            self.assertTrue((target_dir / "venus_evcharger/__init__.py").is_file())
            merged_config = (target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8")
            self.assertIn("# keep this local host comment\n", merged_config)
            self.assertIn("Host=keep-me\n", merged_config)
            self.assertIn("Mode=2\n", merged_config)
            self.assertIn("ConfigSchemaVersion=1\n", merged_config)
            self.assertIn("NewToggle=1\n", merged_config)
            self.assertIn("[Backend]\n", merged_config)
            self.assertIn("# keep this local backend comment\n", merged_config)
            self.assertIn("Type=custom\n", merged_config)
            self.assertIn("ExtraSetting=42\n", merged_config)
            self.assertFalse((target_dir / "tests").exists())
            self.assertFalse((target_dir / "docs").exists())
            backup_candidates = sorted((target_dir / "deploy/venus").glob("config.venus_evcharger.ini.bak-*"))
            self.assertEqual(len(backup_candidates), 1)
            self.assertEqual(backup_candidates[0].read_text(encoding="utf-8"), original_config)
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "success")
            self.assertTrue(status["config_merge_changed"])
            self.assertTrue(status["config_merge_comment_preserved"])
            self.assertTrue(status["config_validation_passed"])
            self.assertEqual(status["config_schema_before"], "0")
            self.assertEqual(status["config_schema_target"], "1")
            self.assertEqual(status["new_version"], "1.2.3")
            self.assertIn("DEFAULT.ConfigSchemaVersion", status["config_merge_added_keys"])
            self.assertIn("DEFAULT.NewToggle", status["config_merge_added_keys"])
            self.assertIn("Backend.ExtraSetting", status["config_merge_added_keys"])
            self.assertEqual(status["config_merge_backup_path"], str(backup_candidates[0]))
            self.assertTrue((target_dir / ".bootstrap-state/update_audit.log").is_file())
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)
            self.assertEqual((target_dir / ".bootstrap-state/installed_version").read_text(encoding="utf-8"), "1.2.3\n")

    def test_bootstrap_updater_rejects_invalid_preserved_config_when_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nHost=template-host\nNewToggle=1\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            preserved_text = "this is not a valid ini file\nwithout = separators\n"
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(preserved_text, encoding="utf-8")

            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                    check=True,
                    env={
                        **os.environ,
                        "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                    },
                )

            self.assertEqual(
                (target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8"),
                preserved_text,
            )
            self.assertFalse((target_dir / "venus_evcharger_service.py").exists())
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "failed")
            self.assertEqual(status["failure_reason"], "config-validation-failed")
            self.assertEqual(status["config_merge_skipped_reason"], "malformed-local-config")
            self.assertFalse(status["config_validation_passed"])
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)

    def test_bootstrap_updater_keeps_current_release_when_staged_manifest_config_fails_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            release_dir = root / "release"
            current_release = target_dir / "releases/1.0.0"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("9.9.9\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nHost=template-host\nNewToggle=1\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
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
                    "venus_evcharger_service.py",
                    "venus_evcharger_auto_input_helper.py",
                    "deploy/venus",
                    "venus_evcharger",
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

            (current_release / "deploy/venus").mkdir(parents=True, exist_ok=True)
            preserved_text = "this is not a valid ini file\nwithout = separators\n"
            (current_release / "deploy/venus/config.venus_evcharger.ini").write_text(preserved_text, encoding="utf-8")
            (target_dir / "current").parent.mkdir(parents=True, exist_ok=True)
            (target_dir / "current").symlink_to(current_release)

            with self.assertRaises(subprocess.CalledProcessError):
                subprocess.run(
                    ["bash", str(UPDATER_SCRIPT), str(target_dir)],
                    check=True,
                    env={
                        **os.environ,
                        "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                        "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                        "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                        "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                    },
                )

            self.assertTrue((target_dir / "current").is_symlink())
            self.assertEqual(os.readlink(target_dir / "current"), str(current_release))
            self.assertFalse((target_dir / "releases/9.9.9").exists())
            status = self._read_normalized_status(target_dir)
            self.assertEqual(status["result"], "failed")
            self.assertEqual(status["promotion_aborted_reason"], "config-validation-failed")
            self.assertTrue(status["current_preserved"])
            self.assertEqual(self._read_normalized_latest_audit(target_dir), status)

    def test_bootstrap_installer_refreshes_local_updater_and_runs_target_installer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bootstrap_dir = root / "bootstrap"
            bootstrap_dir.mkdir()
            target_dir = root / "target"
            source_dir = root / "source"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("1.2.3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nHost=template-host\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text(
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
                    "VENUS_EVCHARGER_TARGET_DIR": str(target_dir),
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((bootstrap_dir / ".venus-evcharger-bootstrap/bootstrap_updater.sh").is_file())
            self.assertTrue((target_dir / "installed.txt").is_file())
            self.assertEqual((target_dir / "installed.txt").read_text(encoding="utf-8"), "installed\n")
            self.assertTrue((target_dir / "deploy/venus/install_venus_evcharger_service.sh").is_file())

    def test_bootstrap_updater_uses_manifest_bundle_and_skips_when_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"
            release_dir = root / "release"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("9.9.9\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\nHost=template-host\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
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
                    "venus_evcharger_service.py",
                    "venus_evcharger_auto_input_helper.py",
                    "deploy/venus",
                    "venus_evcharger",
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
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue((target_dir / "current").is_symlink())
            self.assertTrue((target_dir / "current/venus_evcharger_service.py").is_file())
            self.assertTrue((target_dir / "releases/9.9.9/deploy/venus/install_venus_evcharger_service.sh").is_file())
            self.assertTrue((target_dir / "current/deploy/venus/install_venus_evcharger_service.sh").is_file())
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
                    "VENUS_EVCHARGER_MANIFEST_SOURCE": str(manifest_path),
                    "VENUS_EVCHARGER_MANIFEST_SIG_SOURCE": str(manifest_sig_path),
                    "VENUS_EVCHARGER_BOOTSTRAP_PUBKEY": str(public_key),
                    "VENUS_EVCHARGER_REQUIRE_SIGNED_MANIFEST": "1",
                },
            )

            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_bootstrap_updater_dry_run_reports_preview_without_modifying_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_dir = root / "source"
            target_dir = root / "target"

            (source_dir / "deploy/venus/service_venus_evcharger/log").mkdir(parents=True, exist_ok=True)
            (source_dir / "venus_evcharger").mkdir(parents=True, exist_ok=True)
            (source_dir / "scripts/ops").mkdir(parents=True, exist_ok=True)

            (source_dir / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "LICENSE").write_text("license\n", encoding="utf-8")
            (source_dir / "README.md").write_text("readme\n", encoding="utf-8")
            (source_dir / "SHELLY_PROFILES.md").write_text("profiles\n", encoding="utf-8")
            (source_dir / "version.txt").write_text("Version: 2.0.0\n", encoding="utf-8")
            (source_dir / "venus_evcharger_service.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "venus_evcharger_auto_input_helper.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            (source_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/boot_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/restart_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/uninstall_venus_evcharger_service.sh").write_text("#!/bin/bash\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/service_venus_evcharger/log/run").write_text("#!/bin/sh\n", encoding="utf-8")
            (source_dir / "deploy/venus/config.venus_evcharger.ini").write_text(
                "[DEFAULT]\n"
                "ConfigSchemaVersion=1\n"
                "Host=template-host\n"
                "NewToggle=1\n",
                encoding="utf-8",
            )
            (source_dir / "venus_evcharger/__init__.py").write_text("# pkg\n", encoding="utf-8")
            (source_dir / "scripts/ops/example.sh").write_text("#!/bin/bash\n", encoding="utf-8")

            (target_dir / "deploy/venus").mkdir(parents=True, exist_ok=True)
            original_config = "[DEFAULT]\nHost=keep-me\n"
            (target_dir / "deploy/venus/config.venus_evcharger.ini").write_text(original_config, encoding="utf-8")

            completed = subprocess.run(
                ["bash", str(UPDATER_SCRIPT), "--dry-run", str(target_dir)],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "VENUS_EVCHARGER_SOURCE_DIR": str(source_dir),
                },
            )

            preview = normalized_bootstrap_update_status_fields(json.loads(completed.stdout.strip()))
            self.assertEqual(preview["mode"], "dry-run")
            self.assertEqual(preview["result"], "preview")
            self.assertEqual(preview["new_version"], "2.0.0")
            self.assertTrue(preview["config_merge_changed"])
            self.assertTrue(preview["config_merge_backup_required"])
            self.assertTrue(preview["config_validation_passed"])
            self.assertIn("DEFAULT.ConfigSchemaVersion", preview["config_merge_added_keys"])
            self.assertIn("DEFAULT.NewToggle", preview["config_merge_added_keys"])
            self.assertEqual((target_dir / "deploy/venus/config.venus_evcharger.ini").read_text(encoding="utf-8"), original_config)
            self.assertFalse((target_dir / "venus_evcharger_service.py").exists())
            self.assertFalse((target_dir / ".bootstrap-state/update_status.json").exists())

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

            (current_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text(
                "#!/bin/bash\nexit 1\n",
                encoding="utf-8",
            )
            (previous_dir / "deploy/venus/install_venus_evcharger_service.sh").write_text(
                "#!/bin/bash\n"
                "printf 'rolled-back\\n' > \"$(dirname \"$0\")/../../installed.txt\"\n",
                encoding="utf-8",
            )
            (current_dir / "deploy/venus/install_venus_evcharger_service.sh").chmod(0o755)
            (previous_dir / "deploy/venus/install_venus_evcharger_service.sh").chmod(0o755)

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
                    "VENUS_EVCHARGER_TARGET_DIR": str(target_dir),
                },
            )

            self.assertEqual((previous_dir / "installed.txt").read_text(encoding="utf-8"), "rolled-back\n")
            self.assertTrue((target_dir / "current").is_symlink())
            self.assertEqual(os.readlink(target_dir / "current"), str(previous_dir))
