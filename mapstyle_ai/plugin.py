from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction


class MapStyleAIPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.dock = None

    def initGui(self):
        self.action = QAction('MapStyle AI', self.iface.mainWindow())
        self.action.setObjectName('MapStyleAIAction')
        self.action.triggered.connect(self.show)
        self.iface.addPluginToMenu('&MapStyle AI', self.action)
        self.iface.addToolBarIcon(self.action)

    def show(self):
        if self.dock is None:
            from .panel import BackgroundPanel
            self.dock = BackgroundPanel(self.iface)
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.show()
        self.dock.raise_()

    def unload(self):
        if self.dock is not None:
            self.dock.shutdown()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removePluginMenu('&MapStyle AI', self.action)
            self.iface.removeToolBarIcon(self.action)
            self.action.deleteLater()
            self.action = None
