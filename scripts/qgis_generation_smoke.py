# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Local-only integration test. Run setup, then verify on a later QGIS event-loop turn.

Expects MAPSTYLE_REPO and a running scripts/mock_image_api.py. Uses a separate hidden
panel, dummy keys and synthetic input. The visible panel and its keys are untouched.
"""
from pathlib import Path
import qgis.utils
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.PyQt.QtWidgets import QListWidgetItem
from qgis.core import QgsProject, QgsRasterLayer
from mapstyle_ai.exporter import write_geotiff
from qgis.core import QgsNetworkAccessManager
from mapstyle_ai.panel import BackgroundPanel
from mapstyle_ai.providers import OPENAI, GOOGLE

root = Path(MAPSTYLE_REPO)
port = int((root / 'test-output/mock-port.txt').read_text())


class LocalTransport:
    def __init__(self):
        self.mode = ''
        self.sent = 0

    def post(self, request, payload):
        # Only this hidden test panel receives this adapter. No global URL override.
        rewritten = QNetworkRequest(request)
        provider = 'openai' if request.url().host() == 'api.openai.com' else 'google'
        rewritten.setUrl(QUrl(f'http://127.0.0.1:{port}/{provider}/{self.mode}'))
        self.sent += 1
        return QgsNetworkAccessManager.instance().post(rewritten, payload)


class TestPanel(BackgroundPanel):
    _suppress_notifications = True

    def refresh_layers(self):
        if not getattr(self, '_test_sources_locked', False):
            super().refresh_layers()

    def _add_raster(self, path, name, group, visible=True):
        return super()._add_raster(path, name, group, visible=False)


old_test = getattr(qgis.utils, '_ai_generation_test', None)
if old_test:
    old_test.shutdown()
    old_test.deleteLater()
canvas = iface.mapCanvas()
extent = canvas.extent()
side = max(extent.width(), extent.height())
center = extent.center()
bounds = (center.x()-side/2, center.y()-side/2, center.x()+side/2, center.y()+side/2)
fixture_path = root / 'test-output/api-synthetic.tif'
write_geotiff(root/'test-output/synthetic.png', fixture_path, bounds, canvas.mapSettings().destinationCrs().toWkt())
fixture = QgsRasterLayer(str(fixture_path), 'TEST · API-Grundlagen (synthetisch)')
fixture.setCustomProperty('mapstyle_ai/owned', True)
project = QgsProject.instance()
project.addMapLayer(fixture, False)
fixture_group = project.layerTreeRoot().addGroup('AI · API-Test (synthetisch)')
fixture_group.addLayer(fixture).setItemVisibilityChecked(False)
panel = TestPanel(iface)
qgis.utils._ai_generation_test = panel
panel._generation_network = LocalTransport()
panel.output.setText(str(root / 'test-output'))
panel.reference.setText(str(root / 'test-output/synthetic.png'))
assert Path(panel.reference.text()).is_file(), 'Run the synthetic fixture setup first'
# This panel renders only the synthetic fixture, never the user's project sources.
panel._test_sources_locked = True
panel.layers.clear()
item = QListWidgetItem(fixture.name())
item.setData(Qt.UserRole, fixture.id())
item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
item.setCheckState(Qt.Checked)
panel.layers.addItem(item)
panel.test_fixture_id = fixture.id()
panel.build()
assert panel.plan and not panel.plan['dirty'], panel.message.text()
project.layerTreeRoot().findLayer(panel.core_layer_id).parent().setItemVisibilityChecked(False)
assert len(panel.selected_tiles()) == 4
panel.concurrent.setValue(2)
panel.key.setText('dummy-test-key')
panel.generate_selected()  # Exercises automatic export -> generation pipeline.
assert panel._busy, panel.message.text()
print({'stage': 'auto_export_then_google', 'selected': len(panel.selected_tiles()), 'max_parallel': 2})
