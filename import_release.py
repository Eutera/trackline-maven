"""Import a verified Trackline release into the Maven site."""

import argparse
import hashlib
import html
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen


OWNER = "Eutera"
GROUP = "io.github.eutera.trackline"
GROUP_PATH = Path(*GROUP.split("."))
APPROVED_REPOSITORIES = ("trackline-provider-api",)
VERSION_PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
CHECKSUMS = ("md5", "sha1", "sha256", "sha512")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version_key(version):
    require(re.fullmatch(VERSION_PATTERN, version), f"Invalid stable version: {version}")
    return tuple(map(int, version.split(".")))


def download(url):
    request = Request(url, headers={"User-Agent": "Trackline-Maven"})
    with urlopen(request, timeout=60) as response:
        return response.read()


def read_release(repository, version):
    api_url = f"https://api.github.com/repos/{OWNER}/{repository}"
    tag = f"v{version}"
    release = json.loads(download(f"{api_url}/releases/tags/{tag}"))
    require(
        release["tag_name"] == tag and not release["draft"] and not release["prerelease"],
        f"Expected a published stable release for {tag}",
    )
    commit = json.loads(download(f"{api_url}/commits/{tag}"))["sha"]
    assets = {asset["name"] for asset in release["assets"]}
    prefix = f"{repository}-{version}"
    bundle_name = f"{prefix}-maven.zip"
    required_assets = {bundle_name, "release-manifest.json", f"{prefix}-source.zip"}
    require(required_assets <= assets, f"Missing release assets: {sorted(required_assets - assets)}")
    download_url = f"https://github.com/{OWNER}/{repository}/releases/download/{tag}"
    manifest = json.loads(download(f"{download_url}/release-manifest.json"))
    source = manifest["source"]
    require(
        (manifest["groupId"], manifest["artifactId"], manifest["version"]) == (GROUP, repository, version),
        "Manifest coordinates do not match the requested release",
    )
    require(
        (source["repository"], source["commit"], source["tag"])
        == (f"https://github.com/{OWNER}/{repository}", commit, tag),
        "Manifest source does not match the public release tag",
    )
    require(re.fullmatch(r"[0-9a-f]{40}", commit), "Invalid source commit")
    require(
        manifest["publicationBundle"] == bundle_name and source["archive"] == f"{prefix}-source.zip",
        "Manifest asset names do not match the requested release",
    )
    bundle = download(f"{download_url}/{bundle_name}")
    require(
        hashlib.sha256(bundle).hexdigest() == manifest["assets"][bundle_name]["sha256"],
        "Maven bundle SHA-256 does not match the manifest",
    )
    record = {
        "artifact": repository,
        "version": version,
        "commit": commit,
        "release": f"https://github.com/{OWNER}/{repository}/releases/tag/{tag}",
        "source": f"{download_url}/{prefix}-source.zip",
        "license": manifest["license"]["spdx"],
    }
    return bundle, manifest["mavenFiles"], record


def verify_publication(bundle, manifest_files, artifact, version):
    artifact_path = GROUP_PATH / artifact
    prefix = f"{artifact}-{version}"
    version_dir = artifact_path / version
    version_files = {
        version_dir / f"{prefix}{suffix}" for suffix in (".aar", ".pom", ".module", "-sources.jar")
    }
    primary_files = version_files | {artifact_path / "maven-metadata.xml"}
    required = {path.as_posix() for path in primary_files}
    allowed = required | {f"{path}.{algorithm}" for path in required for algorithm in CHECKSUMS}
    require(set(manifest_files) == required, "Unexpected or missing Maven files in the manifest")
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate file in the Maven bundle")
        require(set(names) <= allowed, "Unexpected path in the Maven bundle")
        require(required | {f"{name}.sha256" for name in required} <= set(names), "Incomplete Maven bundle")
        contents = {name: archive.read(name) for name in names}

    for name in required:
        data = contents[name]
        require(
            hashlib.sha256(data).hexdigest() == manifest_files[name]["sha256"],
            f"Manifest SHA-256 mismatch for {name}",
        )
        for algorithm in CHECKSUMS:
            checksum = contents.get(f"{name}.{algorithm}")
            if checksum is not None:
                require(
                    checksum.strip() == hashlib.new(algorithm, data).hexdigest().encode(),
                    f"Invalid {algorithm} checksum for {name}",
                )
    return {Path(name): data for name, data in contents.items() if Path(name).parent == version_dir}


def write_maven_metadata(site, artifact, versions):
    versions = sorted(versions, key=version_key)
    metadata = ET.Element("metadata")
    ET.SubElement(metadata, "groupId").text = GROUP
    ET.SubElement(metadata, "artifactId").text = artifact
    versioning = ET.SubElement(metadata, "versioning")
    ET.SubElement(versioning, "latest").text = versions[-1]
    ET.SubElement(versioning, "release").text = versions[-1]
    version_list = ET.SubElement(versioning, "versions")
    for version in versions:
        ET.SubElement(version_list, "version").text = version
    ET.SubElement(versioning, "lastUpdated").text = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ET.indent(metadata, space="  ")
    data = ET.tostring(metadata, encoding="utf-8", xml_declaration=True) + b"\n"
    path = site / GROUP_PATH / artifact / "maven-metadata.xml"
    path.write_bytes(data)
    for algorithm in CHECKSUMS:
        path.with_suffix(f".xml.{algorithm}").write_text(hashlib.new(algorithm, data).hexdigest())


def write_release_index(site, releases):
    entries = []
    for record in releases:
        artifact, version = record["artifact"], record["version"]
        coordinate = f"{GROUP}:{artifact}:{version}"
        aar = GROUP_PATH / artifact / version / f"{artifact}-{version}.aar"
        license_url = f"https://github.com/{OWNER}/{artifact}/blob/{record['commit']}/LICENSE"
        entries.append(
            f"<li><code>{html.escape(coordinate)}</code> · "
            f'<a href="{aar.as_posix()}">AAR</a> · '
            f'<a href="{record["release"]}">Release</a> · '
            f'<a href="{record["source"]}">Full source</a> · '
            f'<a href="{license_url}">{html.escape(record["license"])}</a> · '
            f"<code>{record['commit']}</code></li>"
        )
    (site / "index.html").write_text(
        '<!doctype html>\n<html lang="en"><meta charset="utf-8">\n'
        "<title>Trackline Maven</title>\n<h1>Trackline Maven</h1>\n<ul>\n"
        + "\n".join(entries) + "\n</ul>\n</html>\n",
        encoding="utf-8",
    )


def save_publication(site, files, record):
    artifact, version = record["artifact"], record["version"]
    ledger = site / "releases.json"
    releases = json.loads(ledger.read_text()) if ledger.exists() else []
    existing_record = next(
        (item for item in releases if (item["artifact"], item["version"]) == (artifact, version)), None
    )
    version_dir = site / GROUP_PATH / artifact / version
    if existing_record is not None or version_dir.exists():
        require(
            existing_record == record
            and {path.relative_to(site) for path in version_dir.iterdir()} == set(files)
            and all((site / path).read_bytes() == data for path, data in files.items()),
            f"{artifact}:{version} already exists with different contents or provenance",
        )
        return False

    version_dir.mkdir(parents=True)
    for path, data in files.items():
        (site / path).write_bytes(data)
    releases.append(record)
    releases.sort(key=lambda item: (item["artifact"], version_key(item["version"])))
    ledger.write_text(json.dumps(releases, indent=2) + "\n")
    write_maven_metadata(site, artifact, [item["version"] for item in releases if item["artifact"] == artifact])
    write_release_index(site, releases)
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", choices=APPROVED_REPOSITORIES)
    parser.add_argument("version")
    parser.add_argument("--site-dir", type=Path, default=Path("site"))
    args = parser.parse_args()
    try:
        version_key(args.version)
        bundle, manifest_files, record = read_release(args.repository, args.version)
        files = verify_publication(bundle, manifest_files, args.repository, args.version)
        imported = save_publication(args.site_dir, files, record)
    except (OSError, KeyError, TypeError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(1, f"Import failed: {error}\n")
    print(f"{args.repository}:{args.version} {'imported' if imported else 'already imported'}")


if __name__ == "__main__":
    main()
