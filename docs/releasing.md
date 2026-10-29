# Releasing MapStyle AI

## Release 0.6.1

This is the first public experimental release. Minimum QGIS: 3.44; maximum: 3.99. QGIS 4 compatibility is not claimed. The plugin uses QGIS/Qt/GDAL and Python's standard library without pip dependencies. Paid OpenAI or Google Gemini API access is required only for generation.

## Rebuild and verify

1. Update `mapstyle_ai/metadata.txt` and `CHANGELOG.md` for each new release. The package filename is derived from metadata.
2. Run `python3 -m unittest discover -s tests -v`.
3. Run the six current isolated QGIS checks listed in README. These use synthetic data, disposable authentication databases and simulated API responses; no paid generation is performed.
4. Run `python3 scripts/package.py`. It checks required metadata/files, omits caches/hidden files, enforces the 25 MB limit and writes a reproducible ZIP and SHA-256 checksum under `dist/`.
5. Install the ZIP into a disposable QGIS profile and check load, panel, icon and unload. Desktop checks on Windows and Linux remain outstanding. Native credential dialogs and a real provider generation also require manual verification.
6. Review staged files for credentials and personal data. Keep local maps, `.env`, generation outputs, authentication databases and test artifacts out of Git.
7. Commit the verified source, tag `vVERSION`, push to the public repository and attach the ZIP plus checksum to a GitHub prerelease while experimental.

The ZIP contains one `mapstyle_ai/` folder, including source, metadata, icon, quick-start guide and LICENSE. Source files must match the release tag. Development scripts and private example projects do not belong in the ZIP.

## OSGeo account and QGIS submission

1. Open https://id.osgeo.org/ldap/create and select **Publish a QGIS Plugin**.
2. Source code URL: https://github.com/gormlabenz/mapstyle-ai
3. Affiliation: https://www.linkedin.com/in/gorm-labenz-426530156/
4. Send the generated mantra request from `gorm@labenz.io` to `mantra-request@osgeo.org`. The mantra is a verification code supplied by OSGeo; the publication reason is not itself the code.
5. After receiving the code, complete OSGeo registration. Keep passwords and verification codes out of this repository.
6. Sign in at https://plugins.qgis.org/ using the OSGeo account. Choose **Upload a plugin** and upload `dist/mapstyle_ai-0.6.1.zip`. Keep the experimental flag enabled.
7. Record the returned plugin/version URL. An upload is not approval: wait for the security scan and volunteer review. Resolve review findings and increment the version for a replacement upload.

The account depends on OSGeo's reply; the QGIS upload cannot precede account activation. Do not label the plugin as available in QGIS until approval is confirmed.

## References

- [QGIS publishing requirements](https://plugins.qgis.org/docs/publish)
- [Approval and security scans](https://plugins.qgis.org/docs/approval)
- [Plugin metadata](https://docs.qgis.org/3.44/en/docs/pyqgis_developer_cookbook/plugins/plugins.html)
- [Release structure](https://docs.qgis.org/3.44/en/docs/pyqgis_developer_cookbook/plugins/releasing.html)

## Verification record — 2026-10-29

- 48 standard-library unit tests passed.
- All six QGIS integration scripts listed in README passed: credential persistence, metric grid/export, compact interface, multiple references, model capabilities and result variants.
- `qgis_package_check.py dist/mapstyle_ai-0.6.1.zip` passed against the extracted archive: ZIP integrity, isolated module import, icon, plugin initialization, panel opening and unload.
- Rebuilding the package produces the same SHA-256 checksum.
- Common provider-token/private-key patterns were checked in release sources and Git history with no matches. This limited check is not a security audit.
- No paid provider requests were made. Windows/Linux desktop behavior, native keyring dialogs and paid API generation remain unverified.

The GitHub workflow repeats unit tests and package validation on Linux. It does not install or exercise QGIS.
