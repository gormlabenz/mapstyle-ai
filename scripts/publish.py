# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Publish the exact QGIS-tested artifact to GitHub and plugins.qgis.org."""
from configparser import ConfigParser
from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess
from zipfile import ZipFile


def main():
    tag = os.environ['RELEASE_TAG']
    if not re.fullmatch(r'v\d+\.\d+\.\d+', tag):
        raise SystemExit('Release tag must use vMAJOR.MINOR.PATCH.')
    version = tag[1:]
    package = Path('dist') / f'mapstyle_ai.{version}.zip'
    checksum = package.with_suffix('.zip.sha256')
    expected = checksum.read_text().split()[0]
    if sha256(package.read_bytes()).hexdigest() != expected:
        raise SystemExit('Package checksum does not match.')
    with ZipFile(package) as archive:
        metadata = ConfigParser(interpolation=None)
        metadata.read_string(archive.read('mapstyle_ai/metadata.txt').decode())
        if metadata['general']['version'] != version:
            raise SystemExit('Package version does not match the release tag.')
    if not os.environ.get('QGIS_TOKEN'):
        raise SystemExit('Set the QGIS_TOKEN GitHub Actions secret before publishing.')
    # A manual retry must use the tag's own workflow/code, never publish a
    # package built from a different default-branch commit under an older tag.
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip()
    remote_tag = subprocess.check_output(
        ['gh', 'api', f'repos/{os.environ["GITHUB_REPOSITORY"]}/commits/{tag}', '--jq', '.sha'],
        text=True).strip()
    if head != remote_tag:
        raise SystemExit('Run the release workflow on the release tag itself.')
    subprocess.run(['gh', 'release', 'view', tag], check=True)
    subprocess.run(['gh', 'release', 'upload', tag, str(package), str(checksum), '--clobber'], check=True)
    # Use QGIS Plugin CI's token uploader on the artifact verified by QGIS.
    # No second build can change the package after verification.
    from qgispluginci.release import upload_plugin_to_osgeo_with_token
    upload_plugin_to_osgeo_with_token(str(package), 'mapstyle_ai', os.environ['QGIS_TOKEN'])


if __name__ == '__main__':
    main()
