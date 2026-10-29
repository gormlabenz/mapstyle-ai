# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Standalone QGIS runtime check. Uses synthetic data and no provider calls.

Run with QGIS Python and QT_QPA_PLATFORM=offscreen; see README.
"""
import sys
import tempfile
import os
from pathlib import Path
auth_directory = tempfile.TemporaryDirectory()
os.environ['QGIS_AUTH_DB_DIR_PATH'] = auth_directory.name
os.environ['QGIS_CUSTOM_CONFIG_PATH'] = auth_directory.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qgis.core import QgsApplication, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsRectangle, QgsCoordinateReferenceSystem
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtWidgets import QMainWindow
from qgis.PyQt.QtCore import Qt, QEventLoop, QTimer
from mapstyle_ai.panel import BackgroundPanel
from mapstyle_ai.live_export import LiveTileExport
from osgeo import gdal
app = QgsApplication([], False)
app.initQgis()
window = QMainWindow()
canvas = QgsMapCanvas()
canvas.resize(800, 600)
canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:32632'))
class Interface:
    def mainWindow(self): return window
    def mapCanvas(self): return canvas
iface = Interface()
project = QgsProject.instance()
base = QgsVectorLayer('Polygon?crs=EPSG:32632&field=name:string', 'Test area', 'memory')
f = QgsFeature(base.fields())
f.setAttributes(['Test'])
f.setGeometry(QgsGeometry.fromRect(QgsRectangle(500100, 5300200, 511100, 5307200)))
base.dataProvider().addFeatures([f]); base.updateExtents()
project.addMapLayer(base)
canvas.setLayers([base]); canvas.setExtent(base.extent())
panel = BackgroundPanel(iface)
panel._suppress_notifications = True
panel.layers.item(0).setCheckState(Qt.Checked)
panel.area.setCurrentIndex(1)
panel.tile_size.setValue(2)
panel._build()
assert panel.plan and not panel.plan['dirty'], panel.log.toPlainText()
assert panel.plan['crs'].authid() == 'EPSG:32632'
assert len(panel.selected_tiles()) == len(panel.tiles)
cores = [t.core for t in panel.tiles]
bounds = panel.plan['bounds']
canvas.zoomByFactor(0.1)
canvas.resize(1200, 900)
panel._build()
assert cores == [t.core for t in panel.tiles]
assert bounds == panel.plan['bounds']
panel.pixels.setCurrentIndex(1)
panel.halo.setValue(256)
panel._build()
assert cores == [t.core for t in panel.tiles]
panel.tile_unit.setCurrentIndex(0)
assert panel.tile_size.value() == 2000
panel._build()
assert cores == [t.core for t in panel.tiles]
panel.tile_unit.setCurrentIndex(1)
assert panel.tile_size.value() == 2
panel.tile_size.setValue(1)
loop = QEventLoop(); QTimer.singleShot(700, loop.quit); loop.exec()
assert panel.plan['tile_size_m'] == 1000, panel.log.toPlainText()
assert bounds == panel.plan['bounds']
# Fit buttons reproduce the 600 m example, including a non-zero metric origin.
fit_bounds = (500100, 5300200, 500700, 5300800)
panel._grid_area = (fit_bounds, panel.plan['crs'])
panel.tile_unit.setCurrentIndex(0)
panel.tile_size.setValue(400)
panel._build()
assert '2 × 2' in panel.more_tiles.text()
panel.more_tiles.click()
assert len(panel.tiles) == 4 and panel.plan['tile_size_m'] == 300
assert panel.plan['grid_origin'] == (fit_bounds[0], fit_bounds[3])
assert not hasattr(panel, 'tabs')
assert '0.0% overhang' in panel.tile_size.toolTip()
panel.fewer_tiles.click()
assert len(panel.tiles) == 1 and panel.plan['tile_size_m'] == 600
assert panel.tiles[0].core == fit_bounds
assert not panel.fewer_tiles.isEnabled()
assert not panel._grid_timer.isActive()
# Image settings, units and navigation preserve the optimized position and size.
panel.tile_unit.setCurrentIndex(1)
panel.pixels.setCurrentIndex(0)
panel.halo.setValue(128)
canvas.zoomByFactor(2)
panel._build()
assert panel.tiles[0].core == fit_bounds
panel.tile_unit.setCurrentIndex(0)
panel.tile_size.setValue(400)
assert panel._fitted_size is None  # Manual input returns to fixed metric sizing.
panel._build()
panel.more_tiles.click()
panel.pixels.setCurrentIndex(1)
panel.halo.setValue(256)
panel._build()
assert len(panel.tiles) == 4 and panel.plan['bounds'] == fit_bounds
# One real QGIS export, without a provider or any external requests.
temporary = tempfile.TemporaryDirectory(prefix='mapstyle-metric-')
job = LiveTileExport(panel._render_settings(), panel.tiles[0], [], temporary.name)
result = {}; loop = QEventLoop()
def done(value): result.update(value); loop.quit()
def failed(error): result['error'] = error; loop.quit()
job.completed.connect(done); job.failed.connect(failed)
job.start(); QTimer.singleShot(15000, loop.quit); loop.exec()
assert 'tif' in result, result
raster = gdal.Open(result['tif'])
assert raster.RasterXSize == 2048 and raster.RasterYSize == 2048
assert abs(raster.GetGeoTransform()[1] - panel.plan['resolution']) < 1e-8
assert '32632' in raster.GetProjection()
print({'checks': 'metric area, zoom, resize, image size, halo, units, live preview, fitting buttons, GeoTIFF', 'tile_size_m': panel.plan['tile_size_m'], 'tiles': len(panel.tiles), 'crs': panel.plan['crs'].authid(), 'api_requests': 0})
panel.shutdown()
raster = None
temporary.cleanup()
