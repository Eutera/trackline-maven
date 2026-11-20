# Trackline Maven

Public Maven repository for the Trackline Android libraries. The release index
at [Trackline Maven](https://eutera.github.io/trackline-maven/) links each
imported version to its binary, license, and complete source.

## Publish an API version

1. Publish the reviewed release in
   [trackline-provider-api](https://github.com/Eutera/trackline-provider-api/releases).
   Include the Maven ZIP, `release-manifest.json`, and the corresponding source
   archive under the names specified in the manifest.
2. In this repository, select **GitHub Actions** under **Settings → Pages → Source**.
   With the workflow on `main`, open **Actions → Import release → Run workflow**.
   Select `main` and `trackline-provider-api`, enter the version as `0.1.0` without
   the tag’s leading `v`, and run the workflow.
3. Wait for both jobs to succeed. Open the Pages URL shown by the deployment,
   then check the AAR, POM, Gradle module metadata, sources JAR, and their SHA-256
   files under the version directory. Use a Gradle build without Maven local to
   confirm the published dependency resolves.

The workflow checks the public release against its tag and validates the Maven
bundle before saving it to `main`. Pages serves the complete saved Maven tree.
If deployment fails after the import succeeds, run the same version again to
retry deployment. Release inputs are restricted to approved repositories and
stable versions of the form `MAJOR.MINOR.PATCH`. Publish any changed versioned
artifacts under a new version.

## Verify locally

```sh
python3 -m unittest discover -s tests
python3 import_release.py trackline-provider-api 0.1.0 --site-dir "$(mktemp -d)"
```

The second command reads the public release and writes a test site to a
temporary directory.
