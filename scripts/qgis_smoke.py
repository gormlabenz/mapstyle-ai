"""Execute in the live QGIS process via MCP. Never clears or saves the project.

Creates clearly named synthetic local data; leaves the demo available for testing.
The second stage in qgis_verify.py runs after asynchronous export has finished.
"""
from pathlib import Path
import qgis.utils
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QImage, QPainter, QColor, QPen, QFont
from qgis.core import (
    QgsProject, QgsCoordinateReferenceSystem, QgsRasterLayer, QgsRectangle,
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling, QgsTextFormat, QgsFillSymbol,
)
from mapstyle_ai.exporter import write_geotiff

root = Path(MAPSTYLE_REPO)
out = root / 'test-output'
out.mkdir(exist_ok=True)
image = QImage(1600, 1600, QImage.Format_ARGB32)
image.fill(QColor('#b8d58d'))
painter = QPainter(image)
painter.fillRect(100, 100, 400, 600, QColor('#629e6e'))
painter.fillRect(1000, 800, 450, 650, QColor('#629e6e'))
painter.fillRect(650, 400, 200, 300, QColor('#c89a7a'))
painter.setPen(QPen(QColor('#f7f1df'), 60))
painter.drawLine(0, 1400, 1500, 0)
painter.drawLine(0, 500, 1600, 1200)
painter.end()
image.save(str(out / 'synthetic.png'))
crs = QgsCoordinateReferenceSystem('EPSG:3857')
write_geotiff(out / 'synthetic.png', out / 'synthetic.tif', (0, 0, 1600, 1600), crs.toWkt())
project = QgsProject.instance()
demo = project.layerTreeRoot().addGroup('DEMO · Synthetische Testdaten')
raster = QgsRasterLayer(str(out / 'synthetic.tif'), 'DEMO · Grundlagen (keine echten Geodaten)')
assert raster.isValid()
project.addMapLayer(raster, False)
demo.addLayer(raster)
vector = QgsVectorLayer('Polygon?crs=EPSG:3857&field=name:string', 'DEMO · Beschriftungstest', 'memory')
feature = QgsFeature(vector.fields())
feature.setAttributes(['DIESER TEXT DARF NICHT EXPORTIERT WERDEN'])
feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(200, 200, 1400, 1400)))
vector.dataProvider().addFeatures([feature])
vector.updateExtents()
vector.renderer().setSymbol(QgsFillSymbol.createSimple({'color': '0,0,0,0', 'outline_style': 'no'}))
label = QgsPalLayerSettings()
label.fieldName = 'name'
format = QgsTextFormat()
format.setFont(QFont('Arial'))
format.setSize(24)
format.setColor(QColor('#ff0000'))
label.setFormat(format)
vector.setLabeling(QgsVectorLayerSimpleLabeling(label))
vector.setLabelsEnabled(True)
project.addMapLayer(vector, False)
demo.insertLayer(0, vector)
project.setCrs(crs)
canvas = iface.mapCanvas()
canvas.setDestinationCrs(crs)
canvas.setRotation(0)
canvas.setLayers([vector, raster])
canvas.setExtent(QgsRectangle(0, 0, 1600, 1600))
canvas.refresh()
panel = qgis.utils.plugins['mapstyle_ai'].dock
panel.refresh_layers()
panel.output.setText(str(out))
# Small test tiles exercise several neighbors even in a narrow docked canvas.
panel.pixels.addItem('256 × 256 Pixel (nur Test)', 256)
panel.pixels.setCurrentIndex(panel.pixels.count() - 1)
panel.halo.setValue(32)
panel.build()
assert panel.plan is not None and not panel.plan['dirty'], panel.message.text()
assert len(panel.selected_tiles()) >= 1
assert not panel.generate_button.isEnabled()
panel.provider.setCurrentIndex(1)
panel.key.setText('TEST-SECRET-MUST-NOT-BE-EXPORTED')
panel.provider.setCurrentIndex(0)
panel.key.setText('SECOND-TEST-SECRET-MUST-NOT-BE-EXPORTED')
panel.export_selected()
assert panel.exporter is not None, panel.message.text()
print({'stage': 'export_started', 'grid_tiles': len(panel.tiles),
       'selected': [tile.name for tile in panel.selected_tiles()],
       'directory': str(panel.last_directory), 'canvas': [canvas.width(), canvas.height()]})
