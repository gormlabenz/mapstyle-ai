# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Publication must reject mismatched artifacts before any upload."""
from hashlib import sha256
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import types
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from scripts.publish import main


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        old = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, old)
        Path('dist').mkdir()
        self.package = Path('dist/mapstyle_ai.0.6.3.zip')
        self.write_package('0.6.3')
        self.env = patch.dict(os.environ, RELEASE_TAG='v0.6.3', QGIS_TOKEN='test-token',
                              GITHUB_REPOSITORY='example/plugin')
        self.env.start()
        self.addCleanup(self.env.stop)
        self.run = patch('scripts.publish.subprocess.run').start()
        self.output = patch('scripts.publish.subprocess.check_output', side_effect=['same\n', 'same\n']).start()
        self.addCleanup(patch.stopall)
        self.upload = Mock()
        module = types.ModuleType('qgispluginci.release')
        module.upload_plugin_to_osgeo_with_token = self.upload
        self.modules = patch.dict('sys.modules', {'qgispluginci': types.ModuleType('qgispluginci'),
                                                 'qgispluginci.release': module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def write_package(self, version):
        with ZipFile(self.package, 'w') as archive:
            archive.writestr('mapstyle_ai/metadata.txt', f'[general]\nversion={version}\n')
        self.package.with_suffix('.zip.sha256').write_text(sha256(self.package.read_bytes()).hexdigest())

    def test_verified_artifact_is_uploaded_without_rebuilding(self):
        before = self.package.read_bytes()
        main()
        self.upload.assert_called_once_with(str(self.package), 'mapstyle_ai', 'test-token')
        self.assertEqual(self.package.read_bytes(), before)
        self.assertEqual(self.run.call_args_list[-1].args[0][:3], ['gh', 'release', 'upload'])

    def test_corrupt_artifact_stops_before_external_calls(self):
        self.package.write_bytes(self.package.read_bytes() + b'corrupt')
        with self.assertRaisesRegex(SystemExit, 'checksum'):
            main()
        self.run.assert_not_called()
        self.upload.assert_not_called()

    def test_mismatched_metadata_stops_before_external_calls(self):
        self.write_package('0.6.2')
        with self.assertRaisesRegex(SystemExit, 'version'):
            main()
        self.run.assert_not_called()
        self.upload.assert_not_called()

    def test_missing_token_stops_before_external_calls(self):
        os.environ['QGIS_TOKEN'] = ''
        with self.assertRaisesRegex(SystemExit, 'QGIS_TOKEN'):
            main()
        self.run.assert_not_called()
        self.upload.assert_not_called()

    def test_wrong_commit_cannot_publish_under_other_tag(self):
        self.output.side_effect = ['new-commit\n', 'old-tag\n']
        with self.assertRaisesRegex(SystemExit, 'release tag itself'):
            main()
        self.run.assert_not_called()
        self.upload.assert_not_called()

    def test_invalid_tag_stops_before_external_calls(self):
        os.environ['RELEASE_TAG'] = 'main; echo invalid'
        with self.assertRaisesRegex(SystemExit, 'Release tag'):
            main()
        self.run.assert_not_called()
        self.upload.assert_not_called()
