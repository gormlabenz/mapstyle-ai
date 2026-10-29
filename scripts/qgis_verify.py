# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Second smoke stage: run after the QGIS event loop finished exporting."""
import json
from pathlib import Path
import qgis.utils
from osgeo import gdal
from qgis.PyQt.QtGui import QImage
from qgis.core import QgsCoordinateReferenceSystem, QgsRectangle

panel = qgis.utils.plugins['mapstyle_ai'].dock
assert panel.exporter is not None and not panel.exporter.active, 'Export läuft noch'
assert panel.exporter.manifest['state'] == 'exported', panel.exporter.manifest
assert len(panel.exported) == len(panel.exporter.tiles)
manifest_text = (panel.last_directory / 'manifest.json').read_text()
assert 'TEST-SECRET' not in manifest_text
assert not panel.generate_button.isEnabled()
for record in panel.exported.values():
    image = QImage(record['png'])
    assert image.width() == 256 and image.height() == 256
    # Labeling is red in the project; there must be no red text in inputs.
    assert not any(image.pixelColor(x, y).red() > 230 and
                   image.pixelColor(x, y).green() < 60 and
                   image.pixelColor(x, y).blue() < 60
                   for x in range(0, 256, 2) for y in range(0, 256, 2))
    ds = gdal.Open(record['tif'])
    assert ds.RasterXSize == 256 and ds.RasterYSize == 256
    assert QgsCoordinateReferenceSystem.fromWkt(ds.GetProjection()).authid() == 'EPSG:3857'
    gt = ds.GetGeoTransform()
    source = record['tile'].source
    assert abs(gt[0] - source[0]) < 1e-7
    assert abs(gt[3] - source[3]) < 1e-7
    assert abs(gt[1] - panel.plan['resolution']) < 1e-7
    assert abs(gt[5] + panel.plan['resolution']) < 1e-7
# Exercise the result import with a deliberately unmodified input, not AI output.
first = next(iter(panel.exported.values()))
panel.select_none()
panel.table.item(first['tile'].row, first['tile'].column).setSelected(True)
panel.import_result(first['png'])
manifest = json.loads((panel.last_directory / 'manifest.json').read_text())
result = next(entry['results'][-1] for entry in manifest['tiles'] if entry['id'] == first['tile'].name)
ds = gdal.Open(str(panel.last_directory / result['file']))
assert ds.RasterXSize == 192 and ds.RasterYSize == 192
assert abs(ds.GetGeoTransform()[0] - first['tile'].core[0]) < 1e-7
assert abs(ds.GetGeoTransform()[3] - first['tile'].core[3]) < 1e-7
assert panel.results.count() == 1
# Mismatched dimensions must not create an output.
wrong = QImage(64, 64, QImage.Format_ARGB32)
wrong.fill(0)
wrong_path = panel.last_directory / 'wrong-size.png'
wrong.save(str(wrong_path))
panel.import_result(str(wrong_path))
assert panel.results.count() == 1
assert 'genau 256' in panel.message.text()
# Provider keys are isolated and are never used for network requests.
panel.provider.setCurrentIndex(1)
assert panel.key.text() == 'TEST-SECRET-MUST-NOT-BE-EXPORTED'
panel.key.clear()
panel._keys.clear()
panel.provider.setCurrentIndex(0)
panel.key.clear()
panel._keys.clear()
print({'stage': 'passed', 'exported': len(panel.exported), 'input_pixels': 256,
       'result_pixels': ds.RasterXSize, 'label_suppression': True,
       'georeferencing': True, 'secret_exclusion': True, 'dimension_rejection': True})
