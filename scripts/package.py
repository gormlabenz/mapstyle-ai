# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Validate metadata and build a reproducible QGIS plugin ZIP."""
from configparser import ConfigParser
from hashlib import sha256
from pathlib import Path
import re
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
source = root / 'mapstyle_ai'
metadata = ConfigParser(interpolation=None)
metadata.read(source / 'metadata.txt', encoding='utf-8')
general = metadata['general']
for field in ('name', 'description', 'about', 'version', 'qgisMinimumVersion',
              'author', 'email', 'homepage', 'repository', 'tracker', 'icon'):
    if not general.get(field, '').strip():
        raise SystemExit(f'Missing metadata: {field}')
version = general['version']
if not re.fullmatch(r'\d+(?:\.\d+)+', version):
    raise SystemExit('Version must contain dotted numbers.')
for filename in ('__init__.py', 'LICENSE', 'README.md', general['icon']):
    if not (source / filename).is_file():
        raise SystemExit(f'Missing package file: {filename}')
output = root / 'dist' / f'mapstyle_ai-{version}.zip'
output.parent.mkdir(exist_ok=True)
with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
    for file in sorted(source.rglob('*')):
        relative = file.relative_to(root)
        if (not file.is_file() or any(part.startswith('.') or part == '__pycache__'
                                    for part in relative.parts)
                or file.suffix in ('.pyc', '.pyo')):
            continue
        info = ZipInfo(relative.as_posix(), date_time=(2026, 1, 1, 0, 0, 0))
        info.compress_type = ZIP_DEFLATED
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, file.read_bytes())
if output.stat().st_size > 25 * 1024 * 1024:
    output.unlink()
    raise SystemExit('Package exceeds the QGIS repository 25 MB limit.')
checksum = sha256(output.read_bytes()).hexdigest()
output.with_suffix('.zip.sha256').write_text(f'{checksum}  {output.name}\n')
print(f'{output}\nSHA256: {checksum}')
