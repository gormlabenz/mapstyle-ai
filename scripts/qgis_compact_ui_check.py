"""Offline interaction checks for the English overview, with zero provider calls."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if not os.environ.get('MAPSTYLE_COMPACT_CHECK_PROFILE'):
    with tempfile.TemporaryDirectory(prefix='mapstyle-compact-check-') as directory:
        env = dict(os.environ, QT_QPA_PLATFORM='offscreen', MAPSTYLE_COMPACT_CHECK_PROFILE=directory,
                   QGIS_AUTH_DB_DIR_PATH=directory, QGIS_CUSTOM_CONFIG_PATH=directory)
        result = subprocess.run([sys.executable, str(Path(__file__).resolve())], env=env, timeout=60)
        raise SystemExit(result.returncode)
from qgis.core import QgsApplication, QgsProject, QgsCoordinateReferenceSystem, QgsVectorLayer, QgsFeature, QgsGeometry, QgsRectangle, QgsSettings
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import Qt, QObject, pyqtSignal, QEventLoop, QTimer
from qgis.PyQt.QtGui import QImage, QColor
from qgis.PyQt.QtTest import QTest
from qgis.PyQt.QtWidgets import QMainWindow, QTabWidget, QWhatsThis
import mapstyle_ai.panel as panel_module

app = QgsApplication([], False)
app.initQgis()
assert os.environ['MAPSTYLE_COMPACT_CHECK_PROFILE'] in app.authManager().authenticationDatabaseUri()
assert os.environ['MAPSTYLE_COMPACT_CHECK_PROFILE'] in QgsSettings().fileName()
window = QMainWindow()
canvas = QgsMapCanvas()
canvas.resize(800, 600)
crs = QgsCoordinateReferenceSystem('EPSG:32632')
canvas.setDestinationCrs(crs)
project = QgsProject.instance()
layer = QgsVectorLayer('Polygon?crs=EPSG:32632&field=name:string', 'Map input', 'memory')
feature = QgsFeature(layer.fields())
feature.setAttributes(['Map input'])
feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(500100, 5300200, 500700, 5300800)))
layer.dataProvider().addFeatures([feature])
layer.updateExtents()
project.addMapLayer(layer)
canvas.setLayers([layer])
canvas.setExtent(layer.extent())
class Interface:
    def mainWindow(self): return window
    def mapCanvas(self): return canvas
panel = panel_module.BackgroundPanel(Interface())
panel._suppress_notifications = True
window.addDockWidget(Qt.RightDockWidgetArea, panel)
window.resize(580, 1050)
window.show()
app.processEvents()
assert not panel.findChildren(QTabWidget)
assert panel.tiles_page.isCollapsed()
assert panel.overlap_mode.currentData() == 'blend'
assert panel.provider_page.isCollapsed()
assert panel.provider_page.parent() == panel.generation_page
assert panel.advanced_page.isCollapsed()
assert panel.log_page.isCollapsed()
assert panel.log_page.parent() == panel.advanced_page
assert not panel.build_button.isHidden() and panel.area_layer.isHidden()
assert panel.area_layer_label.help.isHidden()
assert not panel.output.text() and panel.output.placeholderText() == 'Temporary files'
assert not panel.load_inputs.isChecked()
# Selecting a layer captures its extent immediately, with no separate button.
panel.layers.item(0).setCheckState(Qt.Unchecked)
panel.area.setCurrentIndex(1)
assert panel.plan is None
panel.layers.item(0).setCheckState(Qt.Checked)
assert panel.build_button.isHidden() and not panel.area_layer.isHidden()
assert panel.plan and not panel.plan['dirty'], panel.log.toPlainText()
panel.tile_size.setValue(0.4)
panel._build()
panel.more_tiles.click()
assert len(panel.tiles) == len(panel.selected_tiles()) == 4
assert panel.plan['tile_size_m'] == 300
# Field inputs and fitting buttons share the same form column.
app.processEvents()
assert panel.fewer_tiles.mapTo(panel.area_page, panel.fewer_tiles.rect().topLeft()).x() == panel.tile_size.mapTo(panel.area_page, panel.tile_size.rect().topLeft()).x()
assert abs(panel.fewer_tiles.width() - panel.more_tiles.width()) <= 1
# Field help occupies its own aligned column to the right of inputs.
field_labels = [label for label in panel.area_page.findChildren(panel_module.QLabel)
                if hasattr(label, 'help') and label.isVisible()]
help_positions = {label.help.mapTo(panel.area_page, label.help.rect().topLeft()).x()
                  for label in field_labels}
assert len(help_positions) == 1
assert next(iter(help_positions)) > panel.tile_unit.mapTo(panel.area_page, panel.tile_unit.rect().topRight()).x()
# Every tile can be toggled with a plain click.
panel.tiles_page.setCollapsed(False)
app.processEvents()
item = panel.table.item(0, 0)
QTest.mouseClick(panel.table.viewport(), Qt.LeftButton, pos=panel.table.visualItemRect(item).center())
assert len(panel.selected_tiles()) == 3
QTest.mouseClick(panel.table.viewport(), Qt.LeftButton, pos=panel.table.visualItemRect(item).center())
assert len(panel.selected_tiles()) == 4
# Outlines belong to Area and tiles; toggling preserves the layers and their IDs.
assert panel.show_frames.parent() == panel.area_page
panel.show_overlap.setChecked(True)
old_ids = list(panel.grid_layers)
root_group = project.layerTreeRoot().findGroup(panel.run_group)
assert panel.run_group.startswith('MapStyle · ')
assert [g.name() for g in root_group.children()] == ['Generated Tiles', 'Tile Grid', 'Input Layers']
grid_group = root_group.findGroup('Tile Grid')
assert not root_group.findGroup('Input Layers').itemVisibilityChecked()
assert not root_group.findGroup('Input Layers').isExpanded()
selected_names = {tile.name for tile in panel.selected_tiles()}
panel.show_frames.setChecked(False)
assert panel.grid_layers == old_ids
assert all(project.mapLayer(i) is not None for i in old_ids)
assert not grid_group.itemVisibilityChecked()
assert all(not project.layerTreeRoot().findLayer(i).isVisible() for i in old_ids)
panel.select_none()
panel.show_frames.setChecked(True)
assert panel.grid_layers == old_ids
assert project.mapLayer(panel.core_layer_id).selectedFeatureCount() == 0
panel.select_all()
assert {tile.name for tile in panel.selected_tiles()} == selected_names
panel.show_frames.setChecked(False)
panel._build()
assert len(panel.grid_layers) == 2
assert not grid_group.itemVisibilityChecked()
assert [g.name() for g in root_group.children()] == ['Generated Tiles', 'Tile Grid', 'Input Layers']
panel.show_frames.setChecked(True)
assert project.mapLayer(panel.core_layer_id).selectedFeatureCount() == len(panel.selected_tiles())
assert all(project.layerTreeRoot().findLayer(i).isVisible() for i in panel.grid_layers)
panel.show_overlap.setChecked(False)
assert project.layerTreeRoot().findLayer(panel.grid_layers[0]).isVisible()
assert not project.layerTreeRoot().findLayer(panel.grid_layers[1]).isVisible()
# Collapsible headers retain clickable help, even when closed.
assert panel.advanced_page.help.isVisible()
QTest.mouseClick(panel.source_page.help, Qt.LeftButton)
app.processEvents()
QWhatsThis.hideText()
panel.advanced_page.setCollapsed(False)
panel.log_page.setCollapsed(False)
panel._tell('Test activity')
assert 'Test activity' in panel.log.toPlainText()
panel.log_page.setCollapsed(True)
panel.advanced_page.setCollapsed(True)
# Temporary storage works without choosing a folder; custom folders remain optional.
temporary_output = panel._output_directory()
assert temporary_output.is_dir()
assert temporary_output == panel._output_directory()
with tempfile.TemporaryDirectory() as custom:
    panel.output.setText(custom)
    assert panel._output_directory() == Path(custom)
panel.output.clear()
assert panel._output_directory() == temporary_output
# A provider-free run exercises launch, manifest, busy state and cancellation.
class OfflineRun(QObject):
    preparing = pyqtSignal(str)
    input_ready = pyqtSignal(str, object)
    started = pyqtSignal(str)
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    cancelled_tile = pyqtSignal(str)
    finished = pyqtSignal()
    def __init__(self, settings, tiles, existing, directory, options, accept, parent):
        super().__init__(parent)
        self.tiles = tiles
        self.options = dict(options)
        self.active = False
        self.cancelled = False
    def start(self): self.active = True
    def cancel(self):
        self.active = False
        self.cancelled = True
        for tile in self.tiles: self.cancelled_tile.emit(tile.name)
        self.finished.emit()
    def shutdown(self): self.active = False
panel_module.SeamlessRun = OfflineRun
reference = QImage(64, 64, QImage.Format_ARGB32)
reference.fill(QColor('#acc7a2'))
reference_path = temporary_output / 'reference-test.png'
reference.save(str(reference_path))
panel.add_references([str(reference_path)])
panel._reference_preview()
panel.key_inputs[panel._provider].setText('offline-test-key')
panel.overlap_mode.setCurrentIndex(panel.overlap_mode.findData('copy'))
assert 'exactly' in panel.overlap_description.text()
assert panel.generate_button.isEnabled()
panel.generate_selected()
assert panel._busy and panel.stop_button.isEnabled() and panel.stop_button.isVisible()
assert not panel.area_page.isEnabled() and not panel.provider_page.isEnabled()
assert not panel.prompt.isEnabled()
manifest = panel.generation_directory / 'manifest.json'
assert 'offline-test-key' not in manifest.read_text()
assert json.loads(manifest.read_text())['overlap_mode'] == 'copy'
assert not panel.overlap_mode.isEnabled()
QTest.mouseClick(panel.stop_button, Qt.LeftButton)
assert not panel._busy and panel.generator.cancelled
assert not panel.stop_button.isVisible()
assert json.loads(manifest.read_text())['state'] == 'cancelled'
assert panel.provider_page.isCollapsed() and panel.advanced_page.isCollapsed()
# A new area owns a fresh group, even for the same extent. Old results survive.
first_group_name = panel.run_group
first_group = project.layerTreeRoot().findGroup(first_group_name)
first_grid_ids = list(panel.grid_layers)
tile = panel.selected_tiles()[0]
image = QImage(panel.plan['pixels'], panel.plan['pixels'], QImage.Format_ARGB32)
image.fill(QColor('blue'))
response_path = temporary_output / 'synthetic-result.png'
assert image.save(str(response_path))
panel._store_result(tile, panel._generation_records[tile.name], str(response_path), 'offline_test')
first_result_id = first_group.findGroup('Generated Tiles').findLayers()[0].layerId()
first_input = panel._add_raster(project.mapLayer(first_result_id).source(), 'Synthetic input',
                                first_group.findGroup('Input Layers'), False)
first_ids = first_grid_ids + [first_result_id, first_input.id()]
first_sources = {i: project.mapLayer(i).source() for i in first_ids}
panel.seam_sources[tile.name] = {'previous_run': True}
panel.capture_area()
second_group_name = panel.run_group
assert second_group_name != first_group_name
second_group = project.layerTreeRoot().findGroup(second_group_name)
assert not second_group.findGroup('Generated Tiles').findLayers()
assert not second_group.findGroup('Input Layers').findLayers()
assert len(second_group.findGroup('Tile Grid').findLayers()) == 2
assert not panel.seam_sources and not panel.exported and not panel.generated
assert {i: project.mapLayer(i).source() for i in first_ids} == first_sources
panel.generate_selected()
assert panel._busy and panel.run_group == second_group_name
panel._store_result(tile, panel._generation_records[tile.name], str(response_path), 'offline_test')
assert len(second_group.findGroup('Generated Tiles').findLayers()) == 1
assert first_group.findGroup('Generated Tiles').findLayers()[0].layerId() == first_result_id
assert {i: project.mapLayer(i).source() for i in first_ids} == first_sources
QTest.mouseClick(panel.stop_button, Qt.LeftButton)
panel.more_tiles.click()
assert panel.run_group == second_group_name
assert len(second_group.findGroup('Generated Tiles').findLayers()) == 1
assert all(project.mapLayer(i) is not None for i in first_ids)
# The saved-key path launches with an authcfg only and still exports no secrets.
# This check is run with a disposable profile/auth database by the documented runner.
auth = QgsApplication.authManager()
auth.setPasswordHelperEnabled(False)
assert auth.setMasterPassword('disposable-compact-ui-password', True)
panel.save_keys_button.click()
saved_id = panel.credentials.config_id(panel._provider)
assert saved_id and not panel.key_inputs[panel._provider].text()
assert panel.generate_button.isEnabled()
panel.generate_selected()
assert panel.generator.options['authcfg'] == saved_id
assert panel.generator.options['key'] == ''
manifest = panel.generation_directory / 'manifest.json'
assert 'offline-test-key' not in manifest.read_text() and 'authcfg' not in manifest.read_text()
QTest.mouseClick(panel.stop_button, Qt.LeftButton)
assert not panel._busy
assert auth.removeAuthenticationConfig(saved_id)
# View mode has its own explicit capture action and remains fixed while zooming.
panel.area.setCurrentIndex(0)
assert not panel.build_button.isHidden() and panel.area_layer.isHidden()
assert panel.area_layer_label.help.isHidden()
panel.build_button.click()
assert panel.plan and not panel.plan['dirty']
bounds = panel.plan['bounds']
canvas.zoomByFactor(2)
assert panel.plan['bounds'] == bounds
# Preview for manual layout inspection, without showing credentials.
panel.key_inputs[panel._provider].clear()
panel.provider_page.setCollapsed(True)
app.processEvents()
assert panel.provider_page.help.isVisible()
assert panel.advanced_page.help.isVisible()
panel.grab().save('/private/tmp/mapstyle-ui-preview.png')
print('PASS: English overview, area modes, aligned fitting buttons, click selection, overlays, help, collapsible settings, temporary/custom storage, separate area groups with preserved results/inputs/grids, offline generation and cancellation; API requests: 0')
panel.shutdown()
project.clear()
shutil.rmtree(temporary_output)
