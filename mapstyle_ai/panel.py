"""Native QGIS dock for live, neighbor-conditioned map generation."""
from datetime import datetime
from pathlib import Path
import shutil
import uuid

from qgis.PyQt.QtCore import Qt, QSize, QTimer
from qgis.PyQt.QtGui import QColor, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDockWidget,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFillSymbol, QgsGeometry,
    QgsMapLayerStyle, QgsMapSettings, QgsProject, QgsRasterLayer,
    QgsRectangle, QgsVectorLayer,
)

from .exporter import write_geotiff, write_json
from .grid import build_grid, column_name, crop_window, fitted_size
from .generation import image_png, DEFAULT_TIMEOUT_MS
from .seam_run import SeamlessRun
from .seams import DEFAULT_PROMPT, dependencies, continuation_prompt
from .live_export import copy_regions, blend_regions
from .providers import GOOGLE, OPENAI, MODELS, validate_options, safe_error


def rect_tuple(rect):
    return (rect.xMinimum(), rect.yMinimum(), rect.xMaximum(), rect.yMaximum())


def text_label(text):
    label = QLabel(text)
    label.setWordWrap(True)
    return label


def button(text, callback):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    return widget


class BackgroundPanel(QDockWidget):
    def __init__(self, iface):
        super().__init__('MapStyle AI', iface.mainWindow())
        self.setObjectName('MapStyleAIDock')
        self.setMinimumWidth(420)
        self.iface = iface
        self.project = QgsProject.instance()
        self.tiles = []
        self.plan = None
        self._grid_area = None
        self._fitted_size = None
        self.grid_layers = []
        self.tile_features = {}
        self.exported = {}
        self.statuses = {}
        self.generator = None
        self.generated = set()
        self.seam_sources = {}
        self.generation_manifest = None
        self._models = {}
        self.last_directory = None
        self.run_group = None
        self._keys = {}
        self._provider = GOOGLE
        self._busy = False
        self._closed = False
        self._grid_timer = QTimer(self)
        self._grid_timer.setSingleShot(True)
        self._grid_timer.setInterval(350)
        self._grid_timer.timeout.connect(lambda: self.build(stay=True))
        self._build_ui()
        self.refresh_layers()
        self.project.layersAdded.connect(self._schedule_refresh)
        self.project.layersRemoved.connect(self._schedule_refresh)
        self.project.cleared.connect(self._project_cleared)

    def _build_ui(self):
        root = QWidget()
        layout = QVBoxLayout(root)
        title = QLabel('MapStyle AI')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        layout.addWidget(title)
        layout.addWidget(text_label('Kartenhintergründe aus deinen Grundlagen und einem austauschbaren Referenzbild erzeugen.'))
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._sources_tab()
        self._grid_tab()
        self._generation_tab()
        self._results_tab()
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.stop_button = button('Generierung abbrechen', self.cancel_generation)
        self.stop_button.setEnabled(False)
        layout.addWidget(self.stop_button)
        self.message = text_label('1. Grundlagen wählen → 2. Kacheln auswählen → 3. Live-Generierung starten')
        layout.addWidget(self.message)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(80)
        self.log.setPlaceholderText('Export- und Fehlermeldungen')
        layout.addWidget(self.log)
        self.setWidget(root)

    def _tab(self, title):
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.tabs.addTab(scroll, title)
        return content, layout

    def _sources_tab(self):
        self.source_page, layout = self._tab('1 · Grundlagen')
        layout.addWidget(text_label('Bereits im Projekt geladene Raster- und Vektorlayer auswählen. Reihenfolge und Gestaltung werden aus QGIS übernommen.'))
        layout.addWidget(button('Projektlayer aktualisieren', self.refresh_layers))
        self.layers = QListWidget()
        self.layers.setMinimumHeight(160)
        self.layers.setToolTip('Oben liegende Layer werden auch im Eingabebild oben gezeichnet. Verborgene Layer können hier ebenfalls ausgewählt werden.')
        layout.addWidget(self.layers)
        form = QFormLayout()
        self.area = QComboBox()
        self.area.addItems(['Sichtbarer Kartenausschnitt', 'Ausdehnung eines Layers'])
        form.addRow('Gebiet', self.area)
        self.area_layer = QComboBox()
        self.area_layer.setEnabled(False)
        form.addRow('Gebiets-Layer', self.area_layer)
        self.tile_size = QDoubleSpinBox()
        self.tile_size.setDecimals(6)
        self.tile_size.setRange(0.000001, 1000000)
        self.tile_size.setValue(1)
        self.tile_size.setKeyboardTracking(False)
        self.tile_unit = QComboBox()
        self.tile_unit.addItem('m', 1)
        self.tile_unit.addItem('km', 1000)
        self.tile_unit.setCurrentIndex(1)
        size_row = QHBoxLayout()
        size_row.addWidget(self.tile_size)
        size_row.addWidget(self.tile_unit)
        form.addRow('Kachelgröße', size_row)
        self.tile_size.setToolTip('Seitenlänge des quadratischen Kerns. Bildgröße und Kontext ändern diese Länge nicht.')
        fit_row = QHBoxLayout()
        self.fewer_tiles = button('Weniger Kacheln', lambda: self.fit_tiles(True))
        self.more_tiles = button('Mehr Kacheln', lambda: self.fit_tiles(False))
        fit_row.addWidget(self.fewer_tiles)
        fit_row.addWidget(self.more_tiles)
        form.addRow(fit_row)
        self.fit_hint = text_label('Zuerst Gebiet übernehmen, dann Kacheln passend verteilen.')
        form.addRow(self.fit_hint)
        self._update_fit_buttons()
        self.border = QComboBox()
        self.border.addItems(['Vollständig abdecken', 'Nur vollständig enthaltene Kacheln'])
        form.addRow('Randbehandlung', self.border)
        self.pixels = QComboBox()
        for size in (1024, 2048):
            self.pixels.addItem(f'{size} × {size} Pixel', size)
        form.addRow('Bildgröße inkl. Kontext', self.pixels)
        self.halo = QSpinBox()
        self.halo.setRange(0, 1008)
        self.halo.setSingleStep(16)
        self.halo.setValue(128)
        self.halo.setSuffix(' px pro Seite')
        form.addRow('Kontextsaum', self.halo)
        self.clip = QCheckBox('Ergebnis am gewählten Gebiet zuschneiden')
        self.clip.setChecked(True)
        form.addRow(self.clip)
        layout.addLayout(form)
        layout.addWidget(text_label('Das Gebiet wird einmal übernommen. Kachelgröße in Metern oder Kilometern, Bildgröße und Kontext sind unabhängig vom Bildschirmzoom. „Weniger Kacheln“ und „Mehr Kacheln“ passen Größe und Rasterposition an das Gebiet an. Quadratische Kacheln bleiben erhalten; bei länglichen Gebieten kann ein Restüberstand bleiben. Das Raster verwendet ein lokales UTM-KBS (in Polargebieten UPS). Randkacheln behalten ihre volle Größe. Nordausrichtung erforderlich. Native QGIS-Beschriftungen und Auswahlmarkierungen werden nicht exportiert; eingebrannte Texte bleiben sichtbar.'))
        self.build_button = button('Gebiet übernehmen und Raster erstellen', self.capture_area)
        layout.addWidget(self.build_button)
        self.plan_hint = text_label('Noch kein Raster erstellt.')
        layout.addWidget(self.plan_hint)
        layout.addStretch()
        self.area.currentIndexChanged.connect(self._area_changed)
        self.area_layer.currentIndexChanged.connect(self._area_changed)
        self.border.currentIndexChanged.connect(self._grid_changed)
        self.pixels.currentIndexChanged.connect(self._grid_changed)
        self.halo.valueChanged.connect(self._grid_changed)
        self.tile_size.valueChanged.connect(self._size_changed)
        self.tile_unit.currentIndexChanged.connect(self._unit_changed)
        self.layers.itemChanged.connect(self._invalidate)

    def _grid_tab(self):
        _, layout = self._tab('2 · Kacheln')
        self.summary = text_label('Erstelle zunächst ein Raster im Tab Grundlagen.')
        layout.addWidget(self.summary)
        row = QHBoxLayout()
        row.addWidget(button('Alle', self.select_all))
        row.addWidget(button('Keine', self.select_none))
        row.addWidget(button('Offene', self.select_pending))
        row.addWidget(button('2 × 2 links oben', self.select_first_four))
        layout.addLayout(row)
        self.table = QTableWidget()
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(94)
        self.table.verticalHeader().setDefaultSectionSize(64)
        self.table.setMinimumHeight(250)
        self.table.setToolTip('Ziehen oder Umschalt-Klick für einen Bereich. Cmd/Strg-Klick für einzelne weitere Kacheln.')
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        self.selection_label = text_label('0 Kacheln ausgewählt')
        layout.addWidget(self.selection_label)
        self.show_overlap = QCheckBox('Kontextflächen auf der Karte anzeigen')
        self.show_overlap.toggled.connect(self._overlap_visibility)
        layout.addWidget(self.show_overlap)
        layout.addWidget(text_label('Blau: verwendeter Kern · Orange: Eingabe inklusive Kontext. Kachelauswahl wird auf der Karte hervorgehoben.'))
        self.output = QLineEdit()
        self.output.setPlaceholderText('Ordner für Exporte wählen …')
        row = QHBoxLayout()
        row.addWidget(self.output, 1)
        row.addWidget(button('Ordner …', self.choose_output))
        layout.addLayout(row)
        self.load_inputs = QCheckBox('Live-Eingabebilder als QGIS-Layer laden')
        self.load_inputs.setChecked(True)
        layout.addWidget(self.load_inputs)
        layout.addWidget(text_label('Hier werden nur Kacheln ausgewählt. Eingabebilder werden erst während der Generierung exportiert, sobald die benötigten Nachbarn fertig sind.'))
        self.preview_button = button('Letztes Live-Eingabebild ansehen', self.preview_input)
        layout.addWidget(self.preview_button)

    def _generation_tab(self):
        self.generation_page, layout = self._tab('3 · Generierung')
        notice = text_label('Beim Start werden die ausgewählten Grundlagenkacheln, das Referenzbild und der Prompt an den gewählten Anbieter übertragen. Generierungen werden über dessen API abgerechnet. Jede Eingabe wird unmittelbar vor der Generierung mit fertigen Nachbarn exportiert. Nach A1 folgen B1 und A2, danach B2.')
        notice.setStyleSheet('background: #fff1d6; color: #593c00; padding: 10px; border-radius: 4px;')
        layout.addWidget(notice)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems([GOOGLE, OPENAI])
        form.addRow('Anbieter', self.provider)
        self.model = QLineEdit()
        self.model.setText(MODELS[GOOGLE][0])
        self.model.setToolTip('Weitere Modelle: ' + ', '.join(MODELS[GOOGLE][1:]))
        form.addRow('Modell-ID', self.model)
        self.quality = QComboBox()
        self.quality.addItems(['auto', 'low', 'medium', 'high'])
        self.quality.setEnabled(False)
        form.addRow('Qualität (OpenAI)', self.quality)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('API-Key · nur für diese Sitzung')
        form.addRow('API-Key', self.key)
        self.show_key = QCheckBox('Schlüssel anzeigen')
        form.addRow(self.show_key)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 16)
        self.concurrent.setValue(3)
        form.addRow('Gleichzeitige Generierungen', self.concurrent)
        self.reference = QLineEdit()
        self.reference.setPlaceholderText('Stilreferenz auswählen …')
        ref_row = QHBoxLayout()
        ref_row.addWidget(self.reference)
        ref_row.addWidget(button('Bild …', self.choose_reference))
        form.addRow('Referenzbild', ref_row)
        layout.addLayout(form)
        layout.addWidget(text_label('API-Keys bleiben nur im Arbeitsspeicher des Panels. Sie werden weder im QGIS-Projekt noch in Exporten oder Einstellungen gespeichert.'))
        self.reference_preview = QLabel('Noch kein Referenzbild gewählt')
        self.reference_preview.setAlignment(Qt.AlignCenter)
        self.reference_preview.setMinimumHeight(100)
        self.reference_preview.setMaximumHeight(160)
        layout.addWidget(self.reference_preview)
        layout.addWidget(QLabel('Prompt · vor jedem Auftrag bearbeitbar'))
        self.prompt = QPlainTextEdit(DEFAULT_PROMPT)
        self.prompt.setMinimumHeight(230)
        layout.addWidget(self.prompt, 1)
        layout.addWidget(text_label('Platzhalter: {tile_px}, {core_px}, {halo_px}. Nur diese werden beim Export ersetzt.'))
        layout.addWidget(button('Standardprompt wiederherstellen', self.reset_prompt))
        self.generate_button = button('Ausgewählte Kacheln generieren', self.generate_selected)
        self.generate_button.setEnabled(False)
        layout.addWidget(self.generate_button)
        self.provider.currentTextChanged.connect(self._provider_changed)
        self.show_key.toggled.connect(lambda visible: self.key.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password))
        self.reference.editingFinished.connect(self._reference_preview)
        self.reference.textChanged.connect(self._update_generate)
        self.key.textChanged.connect(self._update_generate)
        self.model.textChanged.connect(self._update_generate)
        self.prompt.textChanged.connect(self._update_generate)

    def _results_tab(self):
        _, layout = self._tab('4 · Ergebnisse')
        layout.addWidget(text_label('Generierte Bilder erscheinen automatisch als georeferenzierte Layer. Extern erzeugte Bilder kannst du weiterhin manuell einer exportierten Kachel zuordnen.'))
        layout.addWidget(text_label('Wähle im Kachelraster genau eine exportierte Kachel aus. Das Ergebnisbild muss dieselbe Pixelgröße wie deren Eingabebild haben. Kontextsaum und optional der Gebietsrand werden abgeschnitten.'))
        self.import_button = button('Ergebnisbild für ausgewählte Kachel laden …', self.import_result)
        layout.addWidget(self.import_button)
        layout.addWidget(text_label('Das Ergebnis wird als GeoTIFF gespeichert und in einer eigenen Layergruppe geladen. Die räumliche Zuordnung korrigiert keine durch die KI verschobenen Gebäude oder Straßen.'))
        self.results = QListWidget()
        layout.addWidget(self.results, 1)
        layout.addWidget(text_label('Pro Generierung entsteht ein eigener Laufordner mit PNG-Eingaben, GeoTIFFs und manifest.json. Referenz, Prompt, Raster und Auswahl werden darin dokumentiert. API-Keys sind ausgeschlossen.'))

    def _tell(self, message, error=False):
        self.message.setText(message)
        self.log.appendPlainText(message)
        if error and not getattr(self, '_suppress_notifications', False):
            self.iface.messageBar().pushWarning('MapStyle AI', message)

    def _schedule_refresh(self, *args):
        if not self._closed:
            QTimer.singleShot(0, self.refresh_layers)

    def refresh_layers(self):
        if self._closed or self._busy:
            return
        selected = {self.layers.item(i).data(Qt.UserRole): self.layers.item(i).checkState()
                    for i in range(self.layers.count())}
        area_id = self.area_layer.currentData()
        self.layers.blockSignals(True)
        self.area_layer.blockSignals(True)
        self.layers.clear()
        self.area_layer.clear()
        for layer in self.project.layerTreeRoot().layerOrder():
            if not layer.isValid() or not layer.isSpatial() or layer.customProperty('mapstyle_ai/owned', False):
                continue
            node = self.project.layerTreeRoot().findLayer(layer.id())
            item = QListWidgetItem(layer.name())
            item.setData(Qt.UserRole, layer.id())
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(selected.get(layer.id(), Qt.Checked if node and node.isVisible() else Qt.Unchecked))
            self.layers.addItem(item)
            self.area_layer.addItem(layer.name(), layer.id())
        if area_id:
            self.area_layer.setCurrentIndex(self.area_layer.findData(area_id))
        self.layers.blockSignals(False)
        self.area_layer.blockSignals(False)
        if self.plan and [layer.id() for layer in self.selected_layers()] != self.plan['source_ids']:
            self._invalidate()
        if not self.layers.count():
            self._tell('Lade zunächst ein Luftbild oder andere Grundlagen in dein QGIS-Projekt.')

    def selected_layers(self):
        ids = {self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count())
               if self.layers.item(i).checkState() == Qt.Checked}
        return [layer for layer in self.project.layerTreeRoot().layerOrder() if layer.id() in ids]

    def _area_changed(self, *args):
        self.area_layer.setEnabled(self.area.currentIndex() == 1)
        self._grid_area = None
        self._fitted_size = None
        self._update_fit_buttons()
        self._grid_timer.stop()
        self._invalidate()

    def capture_area(self):
        self._grid_timer.stop()
        self._grid_area = None
        self._fitted_size = None
        self.build()

    def _size_changed(self, *args):
        self._fitted_size = None
        self._grid_changed()

    def _update_fit_buttons(self):
        side = self._fitted_size or self.tile_size.value() * self.tile_unit.currentData()
        for widget, label, larger in [(self.fewer_tiles, 'Weniger Kacheln', True),
                                      (self.more_tiles, 'Mehr Kacheln', False)]:
            proposal = fitted_size(self._grid_area[0], side, larger) if self._grid_area else None
            widget.setEnabled(proposal is not None)
            widget.setText(label)
            if proposal:
                size, rows, columns = proposal
                widget.setText(f'{label} · {columns} × {rows}')
                widget.setToolTip(f'{columns * rows} Kacheln mit je {size:g} m Seitenlänge. Raster am Gebiet ausrichten.')
            else:
                widget.setToolTip('Zuerst Gebiet übernehmen oder andere Kachelgröße wählen.')

    def fit_tiles(self, larger):
        if self._busy or self._grid_area is None:
            return
        side = self._fitted_size or self.tile_size.value() * self.tile_unit.currentData()
        proposal = fitted_size(self._grid_area[0], side, larger)
        if proposal is None:
            return
        self._grid_timer.stop()
        self._fitted_size = proposal[0]
        self.tile_size.blockSignals(True)
        self.tile_size.setValue(self._fitted_size / self.tile_unit.currentData())
        self.tile_size.blockSignals(False)
        # Fitting always covers the entire area; partial edge cells can be clipped.
        self.border.blockSignals(True)
        self.border.setCurrentIndex(0)
        self.border.blockSignals(False)
        self.build(stay=True)

    def _unit_changed(self):
        # Switching units changes the representation, not the physical length.
        self.tile_size.blockSignals(True)
        meters = self.tile_unit.currentData() == 1
        value = self.tile_size.value() * (1000 if meters else 0.001)
        self.tile_size.setDecimals(3 if meters else 6)
        self.tile_size.setRange(0.001 if meters else 0.000001, 1000000000 if meters else 1000000)
        self.tile_size.setValue(value)
        self.tile_size.blockSignals(False)
        self._grid_changed()

    def _grid_changed(self, *args):
        self._update_fit_buttons()
        self._invalidate()
        if self._grid_area is not None and not self._busy:
            self._grid_timer.start()

    def _invalidate(self, *args):
        if self.plan:
            self.plan['dirty'] = True
            self._update_generate()
            self.plan_hint.setText('Einstellungen geändert. Raster neu erstellen, bevor du generierst.')

    def _project_cleared(self):
        self.cancel_generation()
        self._grid_timer.stop()
        self._grid_area = None
        self._fitted_size = None
        self._update_fit_buttons()
        self.plan = None
        self.tiles = []
        self.exported.clear()
        self.generated.clear()
        self.seam_sources.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self.grid_layers = []
        self.tile_features = {}
        self._previous_grid_group_name = None
        self.plan_hint.setText('Projekt gewechselt. Bitte ein neues Raster erstellen.')

    def build(self, stay=False):
        previous_tab = self.tabs.currentIndex()
        self._invalidate()
        try:
            self._build()
        except Exception as error:
            self._tell(str(error), True)
        finally:
            self._update_fit_buttons()
            if stay:
                self.tabs.setCurrentIndex(previous_tab)

    def _build(self):
        if self._busy:
            return
        if not self.selected_layers():
            raise ValueError('Wähle mindestens einen Grundlagen-Layer aus.')
        canvas = self.iface.mapCanvas()
        if abs(canvas.rotation()) > 1e-6:
            raise ValueError('Bitte die Kartendrehung zuerst auf 0° setzen.')
        settings = QgsMapSettings(canvas.mapSettings())
        if self._grid_area is None:
            display_crs = settings.destinationCrs()
            if not display_crs.isValid():
                raise ValueError('Bitte zuerst ein gültiges Projekt-KBS setzen.')
            extent = QgsRectangle(canvas.extent())
            area_crs = display_crs
            if self.area.currentIndex() == 1:
                layer = self.project.mapLayer(self.area_layer.currentData())
                if layer is None:
                    raise ValueError('Wähle einen gültigen Gebiets-Layer aus.')
                extent, area_crs = layer.extent(), layer.crs()
            wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
            center = QgsCoordinateTransform(area_crs, wgs84, self.project).transform(extent.center())
            lon, lat = center.x(), center.y()
            if lat > 84 or lat < -80:
                epsg = 32661 if lat > 0 else 32761
            else:
                zone = min(60, max(1, int((lon + 180) // 6) + 1))
                epsg = (32600 if lat >= 0 else 32700) + zone
            crs = QgsCoordinateReferenceSystem(f'EPSG:{epsg}')
            extent = QgsCoordinateTransform(area_crs, crs, self.project).transformBoundingBox(extent)
            self._grid_area = (rect_tuple(extent), crs)
        bounds, crs = self._grid_area
        settings.setDestinationCrs(crs)
        settings.setOutputDpi(96)
        pixels = self.pixels.currentData()
        halo = self.halo.value()
        side = self._fitted_size or self.tile_size.value() * self.tile_unit.currentData()
        origin = (bounds[0], bounds[3]) if self._fitted_size else (0, 0)
        if 2 * halo >= pixels:
            raise ValueError('Der Kontextsaum muss kleiner als die halbe Bildkante sein.')
        resolution = side / (pixels - 2 * halo)
        tiles, rows, columns = build_grid(bounds, resolution, pixels, halo,
                                         self.border.currentIndex() == 1, tile_size=side, origin=origin)
        if not tiles:
            raise ValueError('Keine vollständige Kachel passt in dieses Gebiet. Vollständige Abdeckung oder kleinere Kachelgröße wählen.')
        self.tiles = tiles
        self.plan = dict(bounds=bounds, resolution=resolution, tile_size_m=side, grid_origin=origin, fitted=self._fitted_size is not None,
                         pixels=pixels, halo=halo, crs=crs, settings=settings, dirty=False,
                         source_ids=[layer.id() for layer in self.selected_layers()])
        self.exported.clear()
        self.generated.clear()
        self.seam_sources.clear()
        self.statuses.clear()
        self.table.clear()
        self.table.setRowCount(rows)
        self.table.setColumnCount(columns)
        self.table.setHorizontalHeaderLabels([column_name(i) for i in range(columns)])
        self.table.setVerticalHeaderLabels([str(i + 1) for i in range(rows)])
        for tile in tiles:
            item = QTableWidgetItem(f'{tile.name}\nOffen')
            item.setTextAlignment(Qt.AlignCenter)
            item.setData(Qt.UserRole, tile.name)
            item.setToolTip(f'Kern: {tile.core}\nEingabe inkl. Kontext: {tile.source}')
            self.table.setItem(tile.row, tile.column, item)
        self._make_grid_layers()
        core = pixels - 2 * halo
        unit = 'm/Pixel'
        detail = (f'{columns} Spalten × {rows} Zeilen · {len(tiles)} Kacheln\n'
                  f'Kachelgröße {side:g} m × {side:g} m\n'
                  f'Eingabe {pixels} px · Kern {core} px · Kontext {halo} px/Seite\n'
                  f'{crs.authid() or crs.description()} · {resolution:.8g} {unit}')
        area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        excess = max(0, (rows * columns * side * side / area - 1) * 100)
        alignment = 'An Gebiet angepasst' if self._fitted_size else 'Feste Kachelgröße'
        self.fit_hint.setText(f'{alignment} · {columns} × {rows} = {len(tiles)} Kacheln · {excess:.1f} % Überstand')
        self._update_fit_buttons()
        self.summary.setText(detail + f'\n{alignment} · {excess:.1f} % Überstand')
        self.plan_hint.setText(detail)
        self.select_all()
        self.tabs.setCurrentIndex(1)
        self._tell('Raster erstellt. Gebiet und Raster sind geografisch fixiert; Kartennavigation ändert dieses Raster nicht. Alle Kacheln sind ausgewählt.')

    def _make_grid_layers(self):
        for layer_id in self.grid_layers:
            if self.project.mapLayer(layer_id):
                self.project.removeMapLayer(layer_id)
        self.grid_layers = []
        self.tile_features = {}
        root = self.project.layerTreeRoot()
        group = root.insertGroup(0, 'AI · Kachelraster')
        # Empty old groups are removed only if they were created by this panel.
        old_group = root.findGroup(getattr(self, '_previous_grid_group_name', '') or '')
        if old_group is not None and old_group != group and not old_group.children():
            old_group.parent().removeChildNode(old_group)
        group.setName('AI · Kachelraster · ' + uuid.uuid4().hex[:6])
        self._previous_grid_group_name = group.name()
        for name, field, color in [('Kernflächen', 'core', '#2879b9'), ('Eingaben inklusive Kontext', 'source', '#df8c30')]:
            layer = QgsVectorLayer('Polygon?field=tile:string&field=row:integer&field=column:integer', name, 'memory')
            layer.setCrs(self.plan['crs'])
            layer.setCustomProperty('mapstyle_ai/owned', True)
            features = []
            for tile in self.tiles:
                feature = QgsFeature(layer.fields())
                feature.setAttributes([tile.name, tile.row + 1, tile.column + 1])
                feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(*getattr(tile, field))))
                features.append(feature)
            layer.dataProvider().addFeatures(features)
            layer.updateExtents()
            symbol = QgsFillSymbol.createSimple({'color': '0,0,0,0', 'outline_color': color,
                                                  'outline_width': '0.35', 'outline_style': 'dash' if field == 'source' else 'solid'})
            layer.renderer().setSymbol(symbol)
            self.project.addMapLayer(layer, False)
            node = group.addLayer(layer)
            if field == 'source':
                node.setItemVisibilityChecked(self.show_overlap.isChecked())
            else:
                selection = layer.selectionProperties()
                selection.setSelectionRenderingMode(Qgis.SelectionRenderingMode.CustomSymbol)
                selection.setSelectionSymbol(QgsFillSymbol.createSimple({
                    'color': '40,121,185,25', 'outline_color': '#1469a8',
                    'outline_width': '0.8',
                }))
                self.tile_features = {feature['tile']: feature.id() for feature in layer.getFeatures()}
                self.core_layer_id = layer.id()
            self.grid_layers.append(layer.id())

    def selected_tiles(self):
        names = {item.data(Qt.UserRole) for item in self.table.selectedItems()}
        return [tile for tile in self.tiles if tile.name in names]

    def select_all(self):
        self.table.selectAll()

    def select_none(self):
        self.table.clearSelection()

    def select_pending(self):
        self.table.clearSelection()
        for tile in self.tiles:
            if tile.name not in self.generated:
                self.table.item(tile.row, tile.column).setSelected(True)

    def select_first_four(self):
        self.table.clearSelection()
        for tile in self.tiles:
            if tile.row < 2 and tile.column < 2:
                self.table.item(tile.row, tile.column).setSelected(True)

    def _selection_changed(self):
        tiles = self.selected_tiles()
        self.selection_label.setText(f'{len(tiles)} von {len(self.tiles)} Kacheln ausgewählt')
        self._update_generate()
        layer = self.project.mapLayer(getattr(self, 'core_layer_id', ''))
        if layer:
            layer.selectByIds([self.tile_features[tile.name] for tile in tiles if tile.name in self.tile_features])

    def _overlap_visibility(self, checked):
        if len(self.grid_layers) > 1:
            node = self.project.layerTreeRoot().findLayer(self.grid_layers[1])
            if node:
                node.setItemVisibilityChecked(checked)

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, 'Exportordner wählen', self.output.text())
        if directory:
            self.output.setText(directory)

    def choose_reference(self):
        filename, _ = QFileDialog.getOpenFileName(self, 'Stilreferenz wählen', '', 'Bilder (*.png *.jpg *.jpeg *.webp *.tif *.tiff)')
        if filename:
            self.reference.setText(filename)
            self._reference_preview()

    def _reference_preview(self):
        pixmap = QPixmap(self.reference.text()) if self.reference.text().strip() else QPixmap()
        if pixmap.isNull():
            self.reference_preview.clear()
            self.reference_preview.setText('Kein gültiges Referenzbild gewählt')
        else:
            self.reference_preview.setPixmap(pixmap.scaled(300, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def _provider_changed(self, provider):
        self._provider_before_switch = self._provider
        self._keys[self._provider] = self.key.text()
        self._provider = provider
        self.key.setText(self._keys.get(provider, ''))
        self._models[self._provider_before_switch] = self.model.text()
        self.model.setText(self._models.get(provider, MODELS[provider][0]))
        self.model.setToolTip('Weitere Modelle: ' + ', '.join(MODELS[provider][1:]))
        self.quality.setEnabled(provider == OPENAI)

    def reset_prompt(self):
        answer = QMessageBox.question(self, 'Prompt zurücksetzen', 'Den bearbeiteten Prompt durch den Standardprompt ersetzen?')
        if answer == QMessageBox.Yes:
            self.prompt.setPlainText(DEFAULT_PROMPT)

    def resolved_prompt(self):
        result = self.prompt.toPlainText()
        for key, value in {'tile_px': self.plan['pixels'], 'core_px': self.plan['pixels'] - 2 * self.plan['halo'],
                           'halo_px': self.plan['halo']}.items():
            result = result.replace('{' + key + '}', str(value))
        return result

    def _render_settings(self):
        settings = QgsMapSettings(self.plan['settings'])
        settings.setRotation(0)
        settings.setDevicePixelRatio(1)
        settings.setOutputSize(QSize(self.plan['pixels'], self.plan['pixels']))
        settings.setBackgroundColor(QColor('white'))
        layers = self.selected_layers()
        if not layers:
            raise ValueError('Keine Grundlagen-Layer verfügbar.')
        settings.setLayers(layers)
        settings.setFlag(Qgis.MapSettingsFlag.DrawLabeling, False)
        settings.setFlag(Qgis.MapSettingsFlag.DrawSelection, False)
        styles = {}
        for layer in layers:
            style = QgsMapLayerStyle()
            style.readFromLayer(layer)
            styles[layer.id()] = style.xmlData()
        settings.setLayerStyleOverrides(styles)
        return settings

    def _set_busy(self, busy):
        self._busy = busy
        self.source_page.setEnabled(not busy)
        self.table.setEnabled(not busy)
        self.generation_page.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.output.setEnabled(not busy)
        self._selection_changed()

    def _set_tile_status(self, tile, status):
        self.statuses[tile.name] = status
        item = self.table.item(tile.row, tile.column)
        if item:
            item.setText(f'{tile.name}\n{status}')
            item.setBackground(QColor('#fde5e2') if status == 'Fehler' else
                               QColor('#e4f3ed') if status in ('Exportiert', 'Fertig', 'Ergebnis') else QColor('#e4eefb'))

    def _add_raster(self, path, name, group, visible=True):
        layer = QgsRasterLayer(str(path), name)
        if not layer.isValid():
            raise ValueError(f'Raster konnte nicht geladen werden: {path}')
        layer.setCustomProperty('mapstyle_ai/owned', True)
        self.project.addMapLayer(layer, False)
        group.addLayer(layer).setItemVisibilityChecked(visible)
        return layer

    def preview_input(self):
        selected = self.selected_tiles()
        if len(selected) != 1 or selected[0].name not in self.exported:
            self._tell('Für die Vorschau genau eine bereits exportierte Kachel auswählen.')
            return
        path = self.exported[selected[0].name]['png']
        dialog = QDialog(self)
        dialog.setWindowTitle(f'{selected[0].name} · Tatsächliches KI-Eingabebild')
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setPixmap(QPixmap(path).scaled(760, 760, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(image)
        layout.addWidget(text_label(path))
        layout.addWidget(button('Schließen', dialog.accept))
        dialog.exec_()

    def _update_generate(self, *args):
        if not hasattr(self, 'generate_button'):
            return
        ready = (self.plan and not self.plan['dirty'] and self.selected_tiles()
                 and self.key.text().strip() and self.reference.text().strip()
                 and self.model.text().strip() and self.prompt.toPlainText().strip())
        self.generate_button.setEnabled(bool(ready) and not self._busy)

    def generate_selected(self):
        if self._busy:
            return
        try:
            tiles = self.selected_tiles()
            if not self.plan or self.plan['dirty'] or not tiles:
                raise ValueError('Bitte ein aktuelles Raster erstellen und Kacheln auswählen.')
            provider, model = self.provider.currentText(), self.model.text().strip()
            validate_options(provider, model, self.plan['pixels'])
            key = self.key.text().strip()
            if not key or '\n' in key or '\r' in key:
                raise ValueError('Bitte einen gültigen API-Key eingeben.')
            reference = image_png(self.reference.text().strip())
            prompt = self.resolved_prompt().strip()
            if not prompt:
                raise ValueError('Bitte einen Prompt eingeben.')
            intent = dict(provider=provider, model=model, key=key, prompt=prompt,
                          reference=reference, quality=self.quality.currentText(),
                          concurrency=self.concurrent.value(), clip=self.clip.isChecked(),
                          tile_ids=[tile.name for tile in tiles])
            self._launch_generation(intent)
        except Exception as error:
            self._tell(safe_error(error, self.key.text().strip()), True)

    def _launch_generation(self, intent):
        try:
            tiles = [tile for tile in self.tiles if tile.name in intent['tile_ids']]
            if self.plan['halo'] <= 0:
                raise ValueError('Für nahtlose Anschlüsse einen Kontextsaum größer als 0 wählen.')
            parents = dependencies(tiles, self.seam_sources)
            base = Path(self.output.text().strip()).expanduser()
            if not self.output.text().strip() or not base.is_dir():
                raise ValueError('Bitte einen existierenden Ausgabeordner wählen.')
            settings = self._render_settings()
            directory = base / (datetime.now().strftime('generation-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6])
            for folder in ('inputs', 'responses', 'results', 'neighbors'):
                (directory / folder).mkdir(parents=True, exist_ok=True)
            (directory / 'reference.png').write_bytes(intent['reference'])
            # Snapshot previous anchors so future jobs remain reproducible.
            existing = {}
            for name, source in self.seam_sources.items():
                if name in intent['tile_ids'] or not any(name in ids for ids in parents.values()):
                    continue
                png, tif = directory/'neighbors'/f'{name}.png', directory/'neighbors'/f'{name}.tif'
                shutil.copyfile(source['png'], png)
                write_geotiff(png, tif, source['bounds'], self.plan['crs'].toWkt())
                existing[name] = dict(source, png=str(png), tif=str(tif))
            manifest = dict(schema_version=2, workflow='live_neighbor_continuation',
                            state='generating', created_at=datetime.now().astimezone().isoformat(),
                            crs_wkt=self.plan['crs'].toWkt(), crs_authid=self.plan['crs'].authid(),
                            bounds=self.plan['bounds'], resolution=self.plan['resolution'],
                            tile_size_m=self.plan['tile_size_m'], grid_origin=self.plan['grid_origin'],
                            fitted=self.plan['fitted'],
                            input_pixels=self.plan['pixels'], halo_pixels=self.plan['halo'],
                            border_mode=self.border.currentText(),
                            layers=[{'id':layer.id(), 'name':layer.name()} for layer in self.selected_layers()],
                            provider=intent['provider'], model=intent['model'], prompt=intent['prompt'],
                            quality=intent['quality'], concurrency=intent['concurrency'],
                            request_timeout_seconds=DEFAULT_TIMEOUT_MS // 1000,
                            clip_results=intent['clip'], reference='reference.png', api_enabled=True,
                            tiles=[dict(tile.record(), state='queued', dependencies=parents[tile.name]) for tile in tiles])
            self._generation_records = {tile.name:dict(directory=str(directory), manifest=manifest, tile=tile)
                                        for tile in tiles}
            write_json(directory/'manifest.json', manifest)
            self.generation_manifest, self.generation_directory = manifest, directory
            options = dict(intent, pixels=self.plan['pixels'])
            self.generator = SeamlessRun(settings, tiles, existing, directory, options,
                                         self._accept_seam_result, self)
            self.generator.preparing.connect(self._generation_preparing)
            self.generator.input_ready.connect(self._live_input_ready)
            self.generator.started.connect(self._generation_started)
            self.generator.succeeded.connect(self._generation_succeeded)
            self.generator.failed.connect(self._generation_failed)
            self.generator.cancelled_tile.connect(self._generation_cancelled_tile)
            self.generator.finished.connect(self._generation_finished)
            for tile in tiles:
                self.seam_sources.pop(tile.name, None)
                self.generated.discard(tile.name)
                self._set_tile_status(tile, 'Wartet')
            self.progress.setRange(0, len(tiles))
            self.progress.setValue(0)
            self._set_busy(True)
            self.stop_button.setEnabled(True)
            self._load_export_inputs = self.load_inputs.isChecked()
            self._tell(f'Live-Generierung: {len(tiles)} Kacheln, bis zu {intent["concurrency"]} gleichzeitig nach Nachbarfreigabe.')
            self.generator.start()
        except Exception as error:
            self._set_busy(False)
            self.stop_button.setEnabled(False)
            self._tell(safe_error(error, intent['key']), True)

    def _generation_preparing(self, name):
        self._set_tile_status(self._generation_records[name]['tile'], 'Live-Export …')
        self._generation_state(name, 'exporting')

    def _live_input_ready(self, name, prepared):
        record = self._generation_records[name]
        record.update(prepared)
        self.exported[name] = record
        prompt = continuation_prompt(self.generation_manifest['prompt'], prepared['neighbors'], prepared['protected'])
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry.update(input_png=f'inputs/{name}.png', input_tif=f'inputs/{name}.tif',
                             protected_mask=f'inputs/{name}-protected.png', protected=prepared['protected'],
                             neighbors=prepared['neighbors'], prompt=prompt)
        if self._load_export_inputs:
            group = self.project.layerTreeRoot().findGroup(f'AI · {self.generation_directory.name}')
            if group is None:
                group = self.project.layerTreeRoot().insertGroup(1, f'AI · {self.generation_directory.name}')
            inputs = next((g for g in group.children() if g.name()=='Live-Eingaben'), None)
            if inputs is None:
                inputs = group.addGroup('Live-Eingaben')
            self._add_raster(prepared['tif'], f'{name} · Live-Eingabe', inputs, visible=False)
        self._generation_state(name, 'prepared')

    def _accept_seam_result(self, name, filename, prepared):
        record = self._generation_records[name]
        image = QImage(filename)
        original = QImage(prepared['png'])
        corrected = copy_regions(image, original, prepared['protected'])
        stitched = self.generation_directory/'results'/f'{name}-stitched.png'
        if not corrected.save(str(stitched), 'PNG'):
            raise ValueError('Geschützte Anschlussbereiche konnten nicht gespeichert werden.')
        self._store_result(record['tile'], record, filename, 'api_generation',
                           variant='KI-Original', visible=False)
        self._store_result(record['tile'], record, str(stitched), 'api_generation',
                           variant='Überlappung zurückkopiert', visible=False)
        blended = blend_regions(image, original, prepared['protected'], record['manifest']['halo_pixels'])
        blended_path = self.generation_directory/'results'/f'{name}-blended.png'
        if not blended.save(str(blended_path), 'PNG'):
            raise ValueError('Überblendete Kachel konnte nicht gespeichert werden.')
        source = self._store_result(record['tile'], record, str(blended_path), 'api_generation',
                                    variant='Überlappung weich überblendet')
        record['stored'] = True
        self.seam_sources[name] = source
        return source

    def _generation_state(self, name, state, error=None):
        manifest = self.generation_manifest
        for entry in manifest['tiles']:
            if entry['id'] == name:
                entry['state'] = state
                if error:
                    entry['error'] = error
        try:
            write_json(self.generation_directory / 'manifest.json', manifest)
        except OSError as save_error:
            self._tell(f'Generierungsprotokoll konnte nicht gespeichert werden: {save_error}', True)
        done = sum(entry['state'] in ('completed', 'failed', 'cancelled') for entry in manifest['tiles'])
        self.progress.setValue(done)

    def _generation_started(self, name):
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry['request_started_at'] = datetime.now().astimezone().isoformat()
        self._set_tile_status(self._generation_records[name]['tile'], 'Generiert …')
        self._generation_state(name, 'running')

    def _generation_succeeded(self, name, filename):
        record = self._generation_records[name]
        try:
            if not record.get('stored'):
                raise ValueError('Ergebnis wurde noch nicht als Anschluss übernommen.')
            self.generated.add(name)
            self._set_tile_status(record['tile'], 'Fertig')
            self._generation_state(name, 'completed')
            self._tell(f'{name}: Drei Varianten gespeichert: KI-Original, zurückkopiert und weich überblendet.')
        except Exception as error:
            self._generation_failed(name, safe_error(error, self.key.text().strip()))

    def _generation_failed(self, name, error):
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry['failed_at'] = datetime.now().astimezone().isoformat()
        self._set_tile_status(self._generation_records[name]['tile'], 'Fehler')
        self._generation_state(name, 'failed', error)
        self._tell(f'{name}: {error}', True)

    def _generation_cancelled_tile(self, name):
        self._set_tile_status(self._generation_records[name]['tile'], 'Abgebrochen')
        self._generation_state(name, 'cancelled')

    def _generation_finished(self):
        states = [entry['state'] for entry in self.generation_manifest['tiles']]
        completed, failed = states.count('completed'), states.count('failed')
        self.generation_manifest['state'] = ('cancelled' if self.generator.cancelled else
                                             'completed' if completed == len(states) else
                                             'partial' if completed else 'failed')
        try:
            write_json(self.generation_directory / 'manifest.json', self.generation_manifest)
        except OSError as error:
            self._tell(str(error), True)
        self.stop_button.setEnabled(False)
        self._set_busy(False)
        self.refresh_layers()
        self._tell(f'Generierung beendet: {completed} fertig, {failed} fehlgeschlagen, {states.count("cancelled")} abgebrochen. Fertige Bilder bleiben geladen.')

    def cancel_generation(self):
        if self.generator and self.generator.active:
            self.generator.cancel()
            self._tell('Generierung wird abgebrochen. Bereits gesendete Anfragen können beim Anbieter weiterlaufen und berechnet werden.')


    def import_result(self, filename=None):
        # Qt passes False as clicked(bool); tests can pass a filename directly.
        if self._busy:
            return
        selected = self.selected_tiles()
        if len(selected) != 1 or selected[0].name not in self.exported:
            self._tell('Bitte genau eine bereits exportierte Kachel auswählen.', True)
            return
        if not isinstance(filename, str):
            filename, _ = QFileDialog.getOpenFileName(self, 'Ergebnisbild wählen', '', 'Bilder (*.png *.jpg *.jpeg *.webp *.tif *.tiff)')
        if not filename:
            return
        try:
            tile = selected[0]
            self._store_result(tile, self.exported[tile.name], filename, 'manual_import')
        except Exception as error:
            self._tell(str(error), True)

    def _store_result(self, tile, record, filename, origin, variant=None, visible=True):
        manifest = record['manifest']
        image = QImage(filename)
        pixels = manifest['input_pixels']
        if image.isNull() or image.width() != pixels or image.height() != pixels:
            raise ValueError(f'Das Ergebnis muss genau {pixels} × {pixels} Pixel haben. Es wird nicht automatisch verzerrt.')
        window, extent = crop_window(tile, manifest['resolution'], manifest['halo_pixels'], pixels,
                                     manifest['bounds'] if manifest['clip_results'] else None)
        directory = Path(record['directory'])
        stem = f'{tile.name}-{uuid.uuid4().hex[:8]}'
        original = directory / 'results' / f'{stem}-full.png'
        png = directory / 'results' / f'{stem}-core.png'
        tif = directory / 'results' / f'{stem}.tif'
        if not image.save(str(original), 'PNG') or not image.copy(*window).save(str(png), 'PNG'):
            raise ValueError('Ergebnisbild konnte nicht gespeichert werden.')
        write_geotiff(png, tif, extent, manifest['crs_wkt'])
        context_tif = directory / 'results' / f'{stem}-context.tif'
        write_geotiff(original, context_tif, tile.source, manifest['crs_wkt'])
        # Find by run path rather than a potentially removed layer tree wrapper.
        group = self.project.layerTreeRoot().findGroup(f'AI · {directory.name}')
        if group is None:
            group = self.project.layerTreeRoot().insertGroup(1, f'AI · {directory.name}')
        group_name = 'Ergebnisse · ' + variant if variant else 'Ergebnisse'
        result_group = next((child for child in group.children() if child.name() == group_name), None)
        if result_group is None:
            result_group = group.insertGroup(0, group_name)
            result_group.setItemVisibilityChecked(visible)
        self._add_raster(tif, f'{tile.name} · {variant or "Ergebnis"}', result_group)
        for entry in manifest['tiles']:
            if entry['id'] == tile.name:
                entry.setdefault('results', []).append(dict(
                    file=f'results/{tif.name}', full_image=f'results/{original.name}',
                    context_tif=f'results/{context_tif.name}',
                    bounds=extent, crop_window=window, origin=origin, variant=variant))
        write_json(directory / 'manifest.json', manifest)
        self._set_tile_status(tile, 'Ergebnis')
        self.results.addItem(f'{tile.name} · {variant or "Ergebnis"} · {tif.name}')
        self._tell(f'Ergebnis für {tile.name} georeferenziert geladen.')
        return dict(id=tile.name, row=tile.row, column=tile.column, bounds=tile.source,
                    png=str(original), tif=str(context_tif))

    def shutdown(self):
        self._closed = True
        self._grid_timer.stop()
        for signal, callback in [(self.project.layersAdded, self._schedule_refresh),
                                 (self.project.layersRemoved, self._schedule_refresh),
                                 (self.project.cleared, self._project_cleared)]:
            try:
                signal.disconnect(callback)
            except (TypeError, RuntimeError):
                pass
        if self.generator and self.generator.active:
            self.generator.shutdown()
            if self.generation_manifest:
                for entry in self.generation_manifest['tiles']:
                    if entry['state'] in ('queued', 'exporting', 'prepared', 'running'):
                        entry['state'] = 'cancelled'
                self.generation_manifest['state'] = 'cancelled'
                write_json(self.generation_directory / 'manifest.json', self.generation_manifest)
        self._keys.clear()
        self.key.clear()
