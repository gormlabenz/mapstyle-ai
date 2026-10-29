# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Offline model/size UI and grid checks. No HTTP or generation."""
from pathlib import Path
import sys
import os
import tempfile
auth_directory = tempfile.TemporaryDirectory()
os.environ['QGIS_AUTH_DB_DIR_PATH'] = auth_directory.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qgis.core import QgsApplication, QgsCoordinateReferenceSystem, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsRectangle
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtWidgets import QMainWindow
from mapstyle_ai.panel import BackgroundPanel
from mapstyle_ai.providers import GOOGLE, OPENAI

app = QgsApplication([], False)
app.initQgis()
window = QMainWindow()
canvas = QgsMapCanvas()
canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:32632'))
layer = QgsVectorLayer('Polygon?crs=EPSG:32632', 'Synthetic map', 'memory')
feature = QgsFeature()
feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(500000, 5300000, 501000, 5301000)))
layer.dataProvider().addFeatures([feature])
layer.updateExtents()
QgsProject.instance().addMapLayer(layer)
canvas.setLayers([layer])
canvas.setExtent(layer.extent())
class Interface:
    def mainWindow(self): return window
    def mapCanvas(self): return canvas
panel = BackgroundPanel(Interface())
panel._suppress_notifications = True
def sizes(): return [panel.pixels.itemData(i) for i in range(panel.pixels.count())]
assert not panel.model.isEditable()
assert sizes() == [1024, 2048, 4096]
panel.pixels.setCurrentIndex(panel.pixels.findData(4096))
panel.model.setCurrentText('gemini-3-pro-image')
assert panel.pixels.currentData() == 4096
panel.model.setCurrentText('gemini-2.5-flash-image')
assert sizes() == [1024] and panel._effective_options()[1] == 1024
panel.model.setCurrentText('gemini-3.1-flash-image')
assert sizes() == [512, 1024, 2048, 4096]
panel.pixels.setCurrentIndex(panel.pixels.findData(512))
panel._grid_area = ((500000, 5300000, 501000, 5301000), canvas.mapSettings().destinationCrs())
panel._build()
assert panel.plan['pixels'] == 512 and not panel.plan['dirty']
panel.provider.setCurrentText(OPENAI)
assert sizes() == [1024, 2048] and panel.plan['dirty']
assert 'experimental' in panel.pixels.itemText(1)
panel.model_override.setText('future-image')
assert not sizes() and not panel.generate_button.isEnabled()
assert 'require both' in panel.capability_status.text()
panel.size_override.setText('1536')
assert panel._effective_options() == ('future-image', 1536)
panel._build()
assert panel.plan['pixels'] == 1536 and not panel.plan['dirty']
panel.size_override.setText('1001')
assert not panel.generate_button.isEnabled()
assert 'multiple of 16' in panel.capability_status.text()
panel.provider.setCurrentText(GOOGLE)
assert panel.model.currentText() == 'gemini-3.1-flash-image'
assert not panel.model_override.text() and not panel.size_override.text()
panel.provider.setCurrentText(OPENAI)
assert panel.model_override.text() == 'future-image' and panel.size_override.text() == '1001'
panel.model_override.clear()
panel.size_override.clear()
panel._build()
assert panel.plan['pixels'] == 1024
panel.size_override.setText('2048')
assert panel.plan['dirty']
panel._build()
assert panel.plan['pixels'] == 2048
panel._set_busy(True)
assert not panel.pixels.isEnabled()
panel._set_busy(False)
assert not panel.pixels.isEnabled()  # The active size override still owns this value.
# Reload preserves custom IDs, including invalid drafts, and migrates legacy text IDs.
from types import SimpleNamespace
import qgis.utils
from qgis.PyQt.QtWidgets import QLineEdit
reload_source = (Path(__file__).parent / 'reload_preserve.py').read_text().replace(
    "exec((Path(MAPSTYLE_REPO)/'scripts/dev_load.py').read_text())", 'reload_test()')
qgis.utils.plugins['mapstyle_ai'] = SimpleNamespace(dock=panel)
def reload_test():
    previous = qgis.utils.plugins['mapstyle_ai'].dock
    previous.shutdown()
    previous.deleteLater()
    qgis.utils.plugins['mapstyle_ai'] = SimpleNamespace(dock=BackgroundPanel(Interface()))
panel.model_override.setText('future-image')
panel.size_override.setText('1536')
panel._build()
exec(reload_source, dict(reload_test=reload_test))
panel = qgis.utils.plugins['mapstyle_ai'].dock
assert panel._effective_options() == ('future-image', 1536)
assert not panel.plan['dirty']
panel.size_override.setText('bad')
exec(reload_source, dict(reload_test=reload_test))
panel = qgis.utils.plugins['mapstyle_ai'].dock
assert panel.size_override.text() == 'bad' and panel.plan['dirty']
panel.pixels.blockSignals(True)
panel.pixels.clear()
panel.pixels.addItem('2048 × 2048 px', 2048)
panel.pixels.blockSignals(False)
panel.model = QLineEdit('legacy-custom-image')
del panel.model_override
del panel.size_override
exec(reload_source, dict(reload_test=reload_test))
panel = qgis.utils.plugins['mapstyle_ai'].dock
assert panel._effective_options() == ('legacy-custom-image', 2048)
panel._grid_timer.stop()
panel.close()
qgis.utils.plugins.pop('mapstyle_ai', None)
print('PASS: model choices, native sizes, resets, overrides, validation, provider isolation and grid dimensions; no API calls.')
