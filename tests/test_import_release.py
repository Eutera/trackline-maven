import argparse
import hashlib
import io
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

from import_release import GROUP_PATH, provider_repository, read_release, save_publication, verify_publication


ARTIFACT = "trackline-provider-api"
ARTIFACT_PATH = GROUP_PATH / ARTIFACT


def publication(version, artifact=ARTIFACT):
    artifact_path = GROUP_PATH / artifact
    prefix = artifact_path / version / f"{artifact}-{version}"
    files = {
        f"{prefix}{suffix}": f"{version}{suffix}".encode()
        for suffix in (".aar", ".pom", ".module", "-sources.jar")
    }
    files[f"{artifact_path}/maven-metadata.xml"] = b"<metadata />"
    manifest = {name: {"sha256": hashlib.sha256(data).hexdigest()} for name, data in files.items()}
    files.update({f"{name}.sha256": digest["sha256"].encode() for name, digest in manifest.items()})
    return files, manifest


def archive(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in files.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def release(version, artifact=ARTIFACT):
    return {
        "artifact": artifact,
        "version": version,
        "commit": "a" * 40,
        "release": f"https://github.com/Eutera/{artifact}/releases/tag/v{version}",
        "source": f"https://github.com/Eutera/{artifact}/releases/download/v{version}/{artifact}-{version}-source.zip",
        "license": "Apache-2.0",
    }


class ImportReleaseTest(unittest.TestCase):
    def test_provider_names_accept_new_providers_and_reject_other_paths(self):
        for name in ("trackline-provider-api", "trackline-provider-demo", "trackline-provider-future-service"):
            with self.subTest(name=name):
                self.assertEqual(provider_repository(name), name)
        for name in ("trackline-app", "other-owner/trackline-provider-demo", "../trackline-provider-demo",
                     "trackline-provider-", "https://github.com/Eutera/trackline-provider-demo"):
            with self.subTest(name=name):
                with self.assertRaises(argparse.ArgumentTypeError):
                    provider_repository(name)

    def test_public_release_pins_notice_links_and_rejects_invalid_source_paths(self):
        artifact = "trackline-provider-demo"
        version = "0.1.0"
        commit = "a" * 40
        bundle = b"reviewed publication bundle"
        prefix = f"{artifact}-{version}"
        manifest = {
            "groupId": "io.github.eutera.trackline", "artifactId": artifact, "version": version,
            "source": {
                "repository": f"https://github.com/Eutera/{artifact}", "commit": commit,
                "tag": "v0.1.0", "archive": f"{prefix}-source.zip",
            },
            "publicationBundle": f"{prefix}-maven.zip",
            "assets": {f"{prefix}-maven.zip": {"sha256": hashlib.sha256(bundle).hexdigest()}},
            "license": {"spdx": "Apache-2.0"}, "mavenFiles": {},
            "notices": [{"title": "Recording credits and licenses", "path": "src/main/assets/licenses/DEMO-MUSIC-CREDITS.md"}],
        }
        public_release = {
            "tag_name": "v0.1.0", "draft": False, "prerelease": False,
            "assets": [{"name": name} for name in (f"{prefix}-maven.zip", "release-manifest.json", f"{prefix}-source.zip")],
        }

        def responses():
            return [json.dumps(public_release).encode(), json.dumps({"sha": commit}).encode(),
                    json.dumps(manifest).encode(), bundle]

        with patch("import_release.download", side_effect=responses()):
            record = read_release(artifact, version)[2]
            self.assertEqual(record["notices"], [{
                "title": "Recording credits and licenses",
                "url": f"https://github.com/Eutera/{artifact}/blob/{commit}/src/main/assets/licenses/DEMO-MUSIC-CREDITS.md",
            }])

        for path in ("../LICENSE", "/LICENSE", "https://other.example/LICENSE", "src/./LICENSE", "src/../LICENSE"):
            manifest["notices"][0]["path"] = path
            with self.subTest(path=path), patch("import_release.download", side_effect=responses()):
                with self.assertRaisesRegex(ValueError, "relative source file path"):
                    read_release(artifact, version)

    def test_demo_import_retains_api_and_adds_recording_credits(self):
        with tempfile.TemporaryDirectory() as directory:
            site = Path(directory)
            api_files, api_manifest = publication("0.1.0")
            verified_api = verify_publication(archive(api_files), api_manifest, ARTIFACT, "0.1.0")
            save_publication(site, verified_api, release("0.1.0"))
            previous_api = {path.relative_to(site): path.read_bytes() for path in (site / ARTIFACT_PATH).rglob("*") if path.is_file()}
            demo = "trackline-provider-demo"
            files, manifest = publication("0.1.0", demo)
            verified = verify_publication(archive(files), manifest, demo, "0.1.0")
            record = release("0.1.0", demo)
            record["notices"] = [{
                "title": "Recording credits and licenses",
                "url": f"https://github.com/Eutera/{demo}/blob/{record['commit']}/src/main/assets/licenses/DEMO-MUSIC-CREDITS.md",
            }]

            self.assertTrue(save_publication(site, verified, record))
            self.assertFalse(save_publication(site, verified, record))

            for path, data in previous_api.items():
                self.assertEqual((site / path).read_bytes(), data)
            index = (site / "index.html").read_text()
            self.assertIn(release("0.1.0")["source"], index)
            self.assertIn(record["source"], index)
            self.assertIn(record["notices"][0]["url"], index)
            self.assertIn("Recording credits and licenses", index)
            demo_metadata = ET.parse(site / GROUP_PATH / demo / "maven-metadata.xml")
            self.assertEqual(demo_metadata.findtext("artifactId"), demo)
            self.assertEqual(demo_metadata.findtext("versioning/latest"), "0.1.0")

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
