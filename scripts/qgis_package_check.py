# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Load the built ZIP in an isolated QGIS runtime, without provider requests."""
from pathlib import Path
import os
import sys
import tempfile
from zipfile import ZipFile

with tempfile.TemporaryDirectory(prefix='mapstyle-package-') as directory:
    root = Path(directory)
    os.environ['QGIS_AUTH_DB_DIR_PATH'] = directory
    os.environ['QGIS_CUSTOM_CONFIG_PATH'] = directory
    package = Path(sys.argv[1]).resolve()
    with ZipFile(package) as archive:
        assert archive.testzip() is None
        assert all(name.startswith('mapstyle_ai/') for name in archive.namelist())
        archive.extractall(root)
    sys.path.insert(0, str(root))
    from qgis.core import QgsApplication, QgsProject
    from qgis.gui import QgsMapCanvas
    from qgis.PyQt.QtWidgets import QMainWindow
    from qgis.PyQt.QtGui import QImage
    app = QgsApplication([], False)
    app.initQgis()
    import mapstyle_ai
    assert Path(mapstyle_ai.__file__).is_relative_to(root)
    window = QMainWindow()
    canvas = QgsMapCanvas()
    class Interface:
        def mainWindow(self): return window
        def mapCanvas(self): return canvas
        def addPluginToMenu(self, menu, action): window.addAction(action)
        def addToolBarIcon(self, action): pass
        def removePluginMenu(self, menu, action): window.removeAction(action)
        def removeToolBarIcon(self, action): pass
        def addDockWidget(self, area, dock): window.addDockWidget(area, dock)
        def removeDockWidget(self, dock): window.removeDockWidget(dock)
    plugin = mapstyle_ai.classFactory(Interface())
    plugin.initGui()
    assert not plugin.action.icon().isNull()
    image = QImage(str(root / 'mapstyle_ai/icon.png'))
    assert image.width() == image.height() == 128
    plugin.show()
    assert plugin.dock is not None
    plugin.unload()
    assert plugin.action is None and plugin.dock is None
    app.processEvents()
    QgsProject.instance().clear()
    print('PASS: ZIP integrity, isolated import, icon, initGui, panel and unload; API requests: 0')
