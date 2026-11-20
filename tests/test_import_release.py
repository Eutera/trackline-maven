import hashlib
import io
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from import_release import GROUP_PATH, save_publication, verify_publication


ARTIFACT = "trackline-provider-api"
ARTIFACT_PATH = GROUP_PATH / ARTIFACT


def publication(version):
    prefix = ARTIFACT_PATH / version / f"{ARTIFACT}-{version}"
    files = {
        f"{prefix}{suffix}": f"{version}{suffix}".encode()
        for suffix in (".aar", ".pom", ".module", "-sources.jar")
    }
    files[f"{ARTIFACT_PATH}/maven-metadata.xml"] = b"<metadata />"
    manifest = {name: {"sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()}
    files.update({f"{name}.sha256": digest["sha256"].encode() for name, digest in manifest.items()})
    return files, manifest


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def release(version):
    return {
        "artifact": ARTIFACT,
        "version": version,
        "commit": "a" * 40,
        "release": f"https://github.com/Eutera/{ARTIFACT}/releases/tag/v{version}",
        "source": f"https://github.com/Eutera/{ARTIFACT}/releases/download/v{version}/{ARTIFACT}-{version}-source.zip",
        "license": "Apache-2.0",
    }


class ImportReleaseTest(unittest.TestCase):
    def test_rejects_unexpected_archive_path(self):
        files, manifest = publication("0.1.0")
        files["../outside.txt"] = b"unexpected"

        with self.assertRaisesRegex(ValueError, "Unexpected path"):
            verify_publication(archive(files), manifest, ARTIFACT, "0.1.0")

    def test_rejects_changed_artifact(self):
        files, manifest = publication("0.1.0")
        files[f"{ARTIFACT_PATH}/0.1.0/{ARTIFACT}-0.1.0.aar"] = b"changed"

        with self.assertRaisesRegex(ValueError, "Manifest SHA-256 mismatch"):
            verify_publication(archive(files), manifest, ARTIFACT, "0.1.0")

    def test_rejects_invalid_checksum_file(self):
        files, manifest = publication("0.1.0")
        files[f"{ARTIFACT_PATH}/0.1.0/{ARTIFACT}-0.1.0.pom.sha256"] = b"wrong"

        with self.assertRaisesRegex(ValueError, "Invalid sha256 checksum"):
            verify_publication(archive(files), manifest, ARTIFACT, "0.1.0")

    def test_retains_versions_and_uses_highest_version_for_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            for version in ("0.10.0", "0.9.0"):
                files, manifest = publication(version)
                verified = verify_publication(archive(files), manifest, ARTIFACT, version)
                self.assertTrue(save_publication(site, verified, release(version)))
            metadata_path = site / ARTIFACT_PATH / "maven-metadata.xml"
            metadata = ET.parse(metadata_path)
            self.assertEqual(metadata.findtext("versioning/latest"), "0.10.0")
            self.assertEqual(
                [element.text for element in metadata.findall("versioning/versions/version")],
                ["0.9.0", "0.10.0"],
            )
            self.assertEqual(
                metadata_path.with_suffix(".xml.sha256").read_text(),
                hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
            )
            for version in ("0.9.0", "0.10.0"):
                aar = site / ARTIFACT_PATH / version / f"{ARTIFACT}-{version}.aar"
                self.assertEqual(aar.read_bytes(), f"{version}.aar".encode())
                self.assertIn(release(version)["source"], (site / "index.html").read_text())

    def test_retry_is_idempotent_and_cannot_replace_version(self):
        files, manifest = publication("0.1.0")
        verified = verify_publication(archive(files), manifest, ARTIFACT, "0.1.0")
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            record = release("0.1.0")
            self.assertTrue(save_publication(site, verified, record))
            self.assertFalse(save_publication(site, verified, record))

            with self.assertRaisesRegex(ValueError, "already exists with different contents"):
                save_publication(site, verified, dict(record, commit="b" * 40))
            aar = ARTIFACT_PATH / "0.1.0" / f"{ARTIFACT}-0.1.0.aar"
            with self.assertRaisesRegex(ValueError, "already exists with different contents"):
                save_publication(site, dict(verified) | {aar: b"changed"}, record)
            self.assertEqual((site / aar).read_bytes(), verified[aar])
