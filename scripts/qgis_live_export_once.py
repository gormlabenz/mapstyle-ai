# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""One local export only. Does not construct a generator or send API requests.

Run inside QGIS with MAPSTYLE_REPO set. Creates synthetic neighbors in files,
then renders exactly one B2 input with top/left context. No project mutation.
"""
from pathlib import Path
import qgis.utils
from qgis.PyQt.QtCore import QSize
from qgis.PyQt.QtGui import QImage, QColor
from qgis.core import (Qgis, QgsMapSettings, QgsCoordinateReferenceSystem, QgsVectorLayer,
                       QgsFillSymbol, QgsFeature, QgsGeometry, QgsRectangle)
from mapstyle_ai.grid import build_grid
from mapstyle_ai.live_export import LiveTileExport
from mapstyle_ai.exporter import write_geotiff

folder = Path(MAPSTYLE_REPO)/'test-output'/'live-export-once'
folder.mkdir(parents=True, exist_ok=True)
tiles, _, _ = build_grid((0,0,1536,1536), 1)
base = QgsVectorLayer('Polygon', 'Local export test only', 'memory')
feature = QgsFeature()
feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(-2000,-2000,4000,4000)))
base.dataProvider().addFeatures([feature])
base.updateExtents()
base.renderer().setSymbol(QgsFillSymbol.createSimple({'color':'#66aa66','outline_style':'no'}))
crs = QgsCoordinateReferenceSystem('EPSG:3857')
base.setCrs(crs)
settings = QgsMapSettings()
settings.setLayers([base])
settings.setDestinationCrs(crs)
settings.setOutputSize(QSize(1024,1024))
settings.setOutputDpi(96)
settings.setDevicePixelRatio(1)
settings.setFlag(Qgis.MapSettingsFlag.DrawLabeling, False)
neighbors = []
for tile, color in ((tiles[1], '#3355aa'), (tiles[2], '#bb4433')):
    image = QImage(1024,1024,QImage.Format_ARGB32)
    image.fill(QColor(color))
    png, tif = folder/f'{tile.name}-neighbor.png', folder/f'{tile.name}-neighbor.tif'
    image.save(str(png), 'PNG')
    write_geotiff(png,tif,tile.source,crs.toWkt())
    neighbors.append(dict(id=tile.name,png=str(png),tif=str(tif),bounds=tile.source))
job = LiveTileExport(settings, tiles[3], neighbors, folder/'inputs')
qgis.utils._ai_live_export_test = dict(job=job, base=base, result=None, error=None)
job.completed.connect(lambda result: qgis.utils._ai_live_export_test.update(result=result))
job.failed.connect(lambda error: qgis.utils._ai_live_export_test.update(error=error))
job.start()
print({'live_exports_started':1,'api_requests_started':0,'target':'B2','neighbors':['B1','A2']})
