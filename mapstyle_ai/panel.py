"""Native QGIS dock for live, neighbor-conditioned map generation."""
from datetime import datetime
from pathlib import Path
import shutil
import tempfile
import uuid

from qgis.PyQt.QtCore import Qt, QSize, QTimer, QPoint, QLocale, QEvent
from qgis.PyQt.QtGui import QColor, QImage, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDockWidget,
    QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTableWidget, QToolButton, QWhatsThis,
    QTableWidgetItem, QVBoxLayout, QWidget,
)
from qgis.core import (
    Qgis, QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature, QgsFillSymbol, QgsGeometry,
    QgsMapLayerStyle, QgsMapSettings, QgsProject, QgsRasterLayer,
    QgsRectangle, QgsVectorLayer,
)

from qgis.gui import QgsCollapsibleGroupBox

from .exporter import write_geotiff, write_json
from .grid import build_grid, column_name, crop_window, fitted_size
from .generation import image_png, DEFAULT_TIMEOUT_MS
from .seam_run import SeamlessRun
from .seams import dependencies, continuation_prompt
from .prompts import PROMPT_VERSION, build_prompt, validate_style
from .live_export import copy_regions, blend_regions
from .providers import GOOGLE, OPENAI, MODELS, validate_options, validate_image_count, safe_error


OVERLAP_MODES = {
    'blend': ('Fade overlap',
              'Gradually blend the finished neighbor into the new image for a softer transition.',
              'Blended overlap'),
    'copy': ('Copy overlap exactly',
             'Replace the overlap with the finished neighbor pixels exactly. The inner edge may remain visible.',
             'Overlap restored'),
    'original': ('Keep AI output',
                 'Use the generated image without copying or fading neighbor pixels. Visible seams may remain.',
                 'AI original'),
}


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


def help_button(title, explanation, parent=None):
    widget = QToolButton(parent)
    widget.setText('?')
    widget.setAutoRaise(True)
    widget.setFixedSize(24, 24)
    widget.setAccessibleName('Help: ' + title)
    widget.setToolTip(explanation)
    widget.clicked.connect(lambda: QWhatsThis.showText(
        widget.mapToGlobal(QPoint(0, widget.height())), explanation, widget))
    return widget


def field_label(title, explanation):
    label = QLabel(title)
    label.setProperty('helpText', explanation)
    return label


class FieldForm(QGridLayout):
    """Label, expanding input and fixed-width help columns."""
    def __init__(self):
        super().__init__()
        self.setColumnStretch(1, 1)
        self.setColumnMinimumWidth(2, 24)
        self.setHorizontalSpacing(8)
        self.setVerticalSpacing(6)

    def addRow(self, label, field):
        row = self.rowCount()
        explanation = label.property('helpText') if isinstance(label, QLabel) else None
        if isinstance(label, QToolButton):
            # Checkbox rows use their own text as the label.
            self.addWidget(field, row, 0, 1, 2)
            self.addWidget(label, row, 2, Qt.AlignVCenter)
            return
        if isinstance(label, str):
            label = QLabel(label)
        self.addWidget(label, row, 0, Qt.AlignVCenter)
        if isinstance(field, QWidget):
            self.addWidget(field, row, 1)
        else:
            self.addLayout(field, row, 1)
        if explanation:
            label.help = help_button(label.text(), explanation)
            self.addWidget(label.help, row, 2, Qt.AlignVCenter)


class TileSizeInput(QDoubleSpinBox):
    def textFromValue(self, value):
        return self.locale().toString(value, 'f', self.decimals()).rstrip('0').rstrip(self.locale().decimalPoint())


class HelpSection(QgsCollapsibleGroupBox):
    """Native collapsible section with accessible help and optional refresh."""
    def __init__(self, title, explanation, parent=None, refresh=None):
        super().__init__(title, parent)
        self.setSaveCollapsedState(False)
        self.help = help_button(title, explanation, self)
        self.help.installEventFilter(self)
        self.refresh = None
        if refresh:
            self.refresh = QToolButton(self)
            self.refresh.setAutoRaise(True)
            self.refresh.setFixedSize(24, 24)
            self.refresh.setIcon(QgsApplication.getThemeIcon('/mActionRefresh.svg'))
            self.refresh.setToolTip('Refresh project layers')
            self.refresh.setAccessibleName('Refresh project layers')
            self.refresh.clicked.connect(refresh)
            self.refresh.installEventFilter(self)
        self.collapsedStateChanged.connect(lambda *args: QTimer.singleShot(0, self._show_header_controls))

    def eventFilter(self, watched, event):
        # QGIS also hides non-layout children when a collapsed parent changes.
        # Header controls must remain available above the collapsed content.
        if event.type() == QEvent.Hide and watched in (self.help, self.refresh) and self.isVisible():
            QTimer.singleShot(0, self._show_header_controls)
        return super().eventFilter(watched, event)

    def _show_header_controls(self, *args):
        self.help.show()
        if self.refresh:
            self.refresh.show()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_header_controls()
        self.help.move(self.width() - 30, 0)
        if self.refresh:
            self.refresh.move(self.width() - 56, 0)


class BackgroundPanel(QDockWidget):
    def __init__(self, iface):
        super().__init__('MapStyle AI', iface.mainWindow())
        self.setObjectName('MapStyleAIDock')
        self.setLocale(QLocale(QLocale.English))
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
        self._temporary_output = None
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
        self.overview = QVBoxLayout(root)
        heading = QHBoxLayout()
        title = QLabel('MapStyle AI')
        title.setStyleSheet('font-size: 20px; font-weight: 600;')
        heading.addWidget(title)
        heading.addStretch()
        heading.addWidget(help_button('MapStyle AI',
            'Create illustrated, georeferenced map backgrounds from your QGIS layers. '
            'Choose input layers, set an area, select tiles and add style references or describe your desired style. '
            'Generation sends the selected map images, reference and prompt to your provider and uses paid API credits. '
            'API keys stay in memory for this session. Generated results appear as QGIS layers.'))
        self.overview.addLayout(heading)
        self._sources_section()
        self._tiles_section()
        self._generation_section()
        self._advanced_section()
        self.overview.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(root)
        self.setWidget(scroll)
        self._area_visibility()

    def _section(self, title, explanation, collapsed=False, parent_layout=None, refresh=None):
        section = HelpSection(title, explanation, refresh=refresh)
        layout = QVBoxLayout(section)
        layout.setContentsMargins(10, 12, 10, 10)
        section.setCollapsed(collapsed)
        (parent_layout if parent_layout is not None else self.overview).addWidget(section)
        return section, layout

    def _sources_section(self):
        self.source_page, layout = self._section('Input layers',
            'Select the input layers for AI image generation. These layers are rendered into map images and sent '
            'as spatial references to your selected AI provider: OpenAI (the provider of ChatGPT) or Google Gemini. '
            'The AI uses these images to generate the output map layers, guided by your style reference and prompt. '
            'QGIS layer order and styling are preserved. '
            'Labels and selection highlights are excluded; text embedded in raster images remains visible.',
            refresh=self.refresh_layers)
        self.layers = QListWidget()
        self.layers.setMinimumHeight(90)
        self.layers.setMaximumHeight(150)
        self.layers.setToolTip('Selected layers form the map images sent to the AI provider as input for generation.')
        layout.addWidget(self.layers)
        self.area_page, layout = self._section('Area and tiles',
            'The area is stored in map coordinates. Zooming or resizing the map does not change it. '
            'Tile size is the side length of a square tile, independent of image pixels and overlap.')
        form = FieldForm()
        self.area = QComboBox()
        self.area.addItems(['Current map view', 'Layer extent'])
        form.addRow(field_label('Area', 'Capture the current map view, or use the rectangular extent of a project layer.'), self.area)
        self.build_button = button('Set area', self.capture_area)
        form.addRow('', self.build_button)
        self.area_layer = QComboBox()
        self.area_layer_label = field_label('Area layer',
            'Selecting a layer sets the area to its bounding rectangle. This layer does not have to be included in the input image.')
        form.addRow(self.area_layer_label, self.area_layer)
        self.tile_size = TileSizeInput()
        self.tile_size.setDecimals(6)
        self.tile_size.setRange(0.000001, 1000000)
        self.tile_size.setValue(1)
        self.tile_size.setKeyboardTracking(False)
        self.tile_unit = QComboBox()
        self.tile_unit.addItem('m', 1)
        self.tile_unit.addItem('km', 1000)
        self.tile_unit.setCurrentIndex(1)
        size_row = QHBoxLayout()
        size_row.addWidget(self.tile_size, 1)
        size_row.addWidget(self.tile_unit)
        form.addRow(field_label('Tile size',
            'The side length of each square tile. Fewer tiles increases the size; more tiles decreases it. '
            'Both buttons fit the grid to your area. Rectangular areas may retain some overhang.'), size_row)
        fit_row = QHBoxLayout()
        self.fewer_tiles = button('Fewer tiles', lambda: self.fit_tiles(True))
        self.more_tiles = button('More tiles', lambda: self.fit_tiles(False))
        fit_row.addWidget(self.fewer_tiles, 1)
        fit_row.addWidget(self.more_tiles, 1)
        form.addRow('', fit_row)
        self._update_fit_buttons()
        layout.addLayout(form)
        self.area.currentIndexChanged.connect(self._area_changed)
        self.area_layer.currentIndexChanged.connect(self._area_changed)
        self.tile_size.valueChanged.connect(self._size_changed)
        self.tile_unit.currentIndexChanged.connect(self._unit_changed)
        self.layers.itemChanged.connect(self._sources_changed)

    def _tiles_section(self):
        self.tiles_page, layout = self._section('Tiles',
            'Green tiles are selected for generation. Click a tile to toggle it; drag to select a range. '
            'All tiles start selected. Completed, running and failed tiles show their status.', collapsed=True)
        self.table = QTableWidget()
        self.table.setSelectionMode(QAbstractItemView.MultiSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.table.horizontalHeader().setDefaultSectionSize(78)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setMinimumHeight(160)
        self.table.setMaximumHeight(300)
        self.table.setStyleSheet('QTableWidget::item:selected { background: #ccebd8; color: #16452d; }')
        self.table.setToolTip('Click to select or deselect a tile. Green tiles will be generated.')
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table)
        self.show_frames = QCheckBox('Show tile outlines on map')
        self.show_frames.setChecked(True)
        self.show_frames.toggled.connect(self._grid_visibility)
        layout.addWidget(self.show_frames)
        self.output = QLineEdit()
        self.output.setPlaceholderText('Temporary files')
        self.output.setToolTip('Leave empty for temporary storage. Choose a folder to keep generation files permanently.')
        row = QHBoxLayout()
        row.addWidget(self.output, 1)
        row.addWidget(button('Browse…', self.choose_output))
        clear = QToolButton()
        clear.setText('↺')
        clear.setAccessibleName('Use temporary storage')
        clear.setToolTip('Use temporary storage')
        clear.clicked.connect(self.output.clear)
        row.addWidget(clear)
        form = FieldForm()
        form.addRow(field_label('Save to',
            'Temporary files are stored in the system temporary directory. They are needed for QGIS result layers '
            'and may be removed by the system later. Choose a folder to keep the files for future use.'), row)
        layout.addLayout(form)

    def _generation_section(self):
        self.generation_page, layout = self._section('Generation',
            'Each selected tile is rendered immediately before generation, using completed neighbors for continuity. '
            'Images and prompts are sent to the selected provider and charged to your API account. '
            'Results are georeferenced, but AI may still move or alter map details.')
        self.provider_page, provider_layout = self._section('Provider settings',
            'These settings apply to all tiles. API keys are kept only in memory and are never saved in the project or generation files.',
            collapsed=True, parent_layout=layout)
        form = FieldForm()
        self.provider = QComboBox()
        self.provider.addItems([GOOGLE, OPENAI])
        form.addRow('Provider', self.provider)
        self.model = QLineEdit(MODELS[GOOGLE][0])
        self.model.setToolTip('Other models: ' + ', '.join(MODELS[GOOGLE][1:]))
        form.addRow(field_label('Model ID', 'Enter an image model available to your API account.'), self.model)
        self.quality = QComboBox()
        self.quality.addItems(['auto', 'low', 'medium', 'high'])
        self.quality.setEnabled(False)
        form.addRow(field_label('Quality', 'OpenAI image quality. Higher quality can increase generation time and cost.'), self.quality)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText('API key · this session only')
        form.addRow('API key', self.key)
        self.show_key = QCheckBox('Show key')
        form.addRow('', self.show_key)
        self.concurrent = QSpinBox()
        self.concurrent.setRange(1, 16)
        self.concurrent.setValue(3)
        form.addRow(field_label('Parallel jobs', 'Maximum simultaneous requests. Tiles wait until their required neighbors are complete.'), self.concurrent)
        provider_layout.addLayout(form)
        form = FieldForm()
        self.pixels = QComboBox()
        for size in (1024, 2048):
            self.pixels.addItem(f'{size} × {size} px', size)
        form.addRow(field_label('Generated Tile Size',
            'Full generated image size in pixels, including context on all sides. '
            'The final map tile excludes the context margin.'), self.pixels)
        layout.addLayout(form)
        self.references = []
        self._next_reference_id = 1
        self.reference_button = button('Add images…', self.choose_reference)
        reference_row = QHBoxLayout()
        reference_row.addWidget(QLabel('Style references (optional)'))
        reference_row.addStretch()
        reference_row.addWidget(self.reference_button)
        layout.addLayout(reference_row)
        self.reference_list = QListWidget()
        self.reference_list.setMinimumHeight(110)
        self.reference_list.setMaximumHeight(280)
        self.reference_list.hide()
        layout.addWidget(self.reference_list)
        self.prompt = QPlainTextEdit()
        self.prompt.setPlaceholderText(
            'Describe your desired style or add image-specific instructions, e.g.: '
            'Colors like Reference 1, linework like Reference 2. You can also write Referenz 1.')
        layout.addWidget(QLabel('Your instructions (optional)'))
        self.prompt.setMinimumHeight(100)
        self.prompt.setMaximumHeight(180)
        layout.addWidget(self.prompt)
        self.generate_button = button('Generate selected tiles', self.generate_selected)
        self.generate_button.setEnabled(False)
        layout.addWidget(self.generate_button)
        self.progress = QProgressBar()
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.stop_button = button('Cancel generation', self.cancel_generation)
        self.stop_button.setEnabled(False)
        self.stop_button.hide()
        layout.addWidget(self.stop_button)
        self.provider.currentTextChanged.connect(self._provider_changed)
        self.show_key.toggled.connect(lambda visible: self.key.setEchoMode(QLineEdit.Normal if visible else QLineEdit.Password))
        self.key.textChanged.connect(self._update_generate)
        self.model.textChanged.connect(self._update_generate)
        self.prompt.textChanged.connect(self._update_generate)

    def _advanced_section(self):
        self.advanced_page, layout = self._section('Advanced settings',
            'Edge handling and context affect rendering and continuity. '
            'Geographic tile size remains independent of image resolution.', collapsed=True)
        form = FieldForm()
        self.border = QComboBox()
        self.border.addItems(['Cover entire area', 'Only fully contained tiles'])
        form.addRow(field_label('Edge tiles', 'Cover the entire area with full-size tiles, or include only tiles entirely inside the area.'), self.border)
        self.halo = QSpinBox()
        self.halo.setRange(0, 1008)
        self.halo.setSingleStep(16)
        self.halo.setValue(128)
        self.halo.setSuffix(' px per side')
        form.addRow(field_label('Context margin',
            'Extra pixels around each tile, used to connect neighboring images. The final tile excludes this margin. '
            'With 1024 px and a 128 px margin per side, the usable core is 768 px. Must be less than half the image size.'), self.halo)
        self.clip = QCheckBox('Crop results to area')
        self.clip.setChecked(True)
        form.addRow(help_button('Crop results to area',
            'Trim overhanging outer tiles to the selected area. Tile images are cropped, never stretched. '
            'Edges may extend by less than one pixel to preserve georeferencing.'), self.clip)
        self.show_overlap = QCheckBox('Show context areas on map')
        self.show_overlap.toggled.connect(self._grid_visibility)
        form.addRow(help_button('Context areas',
            'Show the full input extents including overlap. Visible only while tile outlines are enabled.'), self.show_overlap)
        self.load_inputs = QCheckBox('Load live inputs as QGIS layers')
        form.addRow(help_button('Live input layers',
            'Add the actual images sent to the provider as hidden QGIS layers for inspection.'), self.load_inputs)
        layout.addLayout(form)
        _, transition_layout = self._section('Tile transitions',
            'Neighboring images share an overlap. Choose how the generated result uses the finished neighbor pixels. '
            'All modes still send neighbor context to the AI and crop the outer context margin. '
            'This setting applies to new generations and to the image used by subsequent tiles.',
            parent_layout=layout)
        transition_layout.addWidget(text_label('Choose how neighboring tiles are joined.'))
        self.overlap_mode = QComboBox()
        for mode, (label, explanation, variant) in OVERLAP_MODES.items():
            self.overlap_mode.addItem(label, mode)
            self.overlap_mode.setItemData(self.overlap_mode.count() - 1, explanation, Qt.ToolTipRole)
        transition_layout.addWidget(self.overlap_mode)
        self.overlap_description = text_label('')
        transition_layout.addWidget(self.overlap_description)
        self.overlap_mode.currentIndexChanged.connect(self._overlap_mode_changed)
        self._overlap_mode_changed()
        self.preview_button = button('Preview latest input', self.preview_input)
        layout.addWidget(self.preview_button)
        self.import_button = button('Import result for selected tile…', self.import_result)
        self.import_button.setToolTip('Select exactly one tile with an exported input. The image must match the full input size.')
        layout.addWidget(self.import_button)
        self.log_page, log_layout = self._section('Log',
            'Rendering, generation progress and errors. API keys are excluded.', collapsed=True, parent_layout=layout)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(140)
        self.log.setMaximumBlockCount(2000)
        self.log.setPlaceholderText('Activity and errors')
        log_layout.addWidget(self.log)
        self.border.currentIndexChanged.connect(self._grid_changed)
        self.pixels.currentIndexChanged.connect(self._grid_changed)
        self.halo.valueChanged.connect(self._grid_changed)

    def _overlap_mode_changed(self, *args):
        self.overlap_description.setText(OVERLAP_MODES[self.overlap_mode.currentData()][1])

    def _tell(self, message, error=False):
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
            self._tell('Load an aerial image or other map layers into your QGIS project first.')

    def selected_layers(self):
        ids = {self.layers.item(i).data(Qt.UserRole) for i in range(self.layers.count())
               if self.layers.item(i).checkState() == Qt.Checked}
        return [layer for layer in self.project.layerTreeRoot().layerOrder() if layer.id() in ids]

    def _area_visibility(self):
        from_layer = self.area.currentIndex() == 1
        self.area_layer.setVisible(from_layer)
        self.area_layer_label.setVisible(from_layer)
        self.area_layer_label.help.setVisible(from_layer)
        self.build_button.setVisible(not from_layer)

    def _sources_changed(self, *args):
        if self._grid_area is not None:
            self._grid_changed()
        elif self.area.currentIndex() == 1 and self.area_layer.currentData() and self.selected_layers():
            self.build()
        else:
            self._invalidate()

    def _area_changed(self, *args):
        self._area_visibility()
        self._grid_area = None
        self._fitted_size = None
        self._update_fit_buttons()
        self._grid_timer.stop()
        self._invalidate()
        if self.area.currentIndex() == 1 and self.area_layer.currentData() and self.selected_layers():
            self.build()

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
        for widget, label, larger in [(self.fewer_tiles, 'Fewer tiles', True),
                                      (self.more_tiles, 'More tiles', False)]:
            proposal = fitted_size(self._grid_area[0], side, larger) if self._grid_area else None
            widget.setEnabled(proposal is not None)
            widget.setText(label)
            if proposal:
                size, rows, columns = proposal
                widget.setText(f'{label} · {columns} × {rows}')
                widget.setToolTip(f'{columns * rows} tiles with {size:g} m sides. Fit the grid to the area.')
            else:
                widget.setToolTip('Set an area first, or choose a different tile size.')

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
            self.generate_button.setToolTip('Settings changed. Update the grid before generating.')

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
        self.tiles_page.setTitle('Tiles')
        self._tell('Project changed. Set a new area.')

    def build(self, stay=False):
        self._invalidate()
        try:
            self._build()
        except Exception as error:
            self._tell(str(error), True)
        finally:
            self._update_fit_buttons()

    def _build(self):
        if self._busy:
            return
        if not self.selected_layers():
            raise ValueError('Select at least one map layer.')
        canvas = self.iface.mapCanvas()
        if abs(canvas.rotation()) > 1e-6:
            raise ValueError('Set map rotation to 0° first.')
        settings = QgsMapSettings(canvas.mapSettings())
        if self._grid_area is None:
            display_crs = settings.destinationCrs()
            if not display_crs.isValid():
                raise ValueError('Set a valid project CRS first.')
            extent = QgsRectangle(canvas.extent())
            area_crs = display_crs
            if self.area.currentIndex() == 1:
                layer = self.project.mapLayer(self.area_layer.currentData())
                if layer is None:
                    raise ValueError('Select a valid area layer.')
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
            raise ValueError('The context margin must be smaller than half the image size.')
        resolution = side / (pixels - 2 * halo)
        tiles, rows, columns = build_grid(bounds, resolution, pixels, halo,
                                         self.border.currentIndex() == 1, tile_size=side, origin=origin)
        if not tiles:
            raise ValueError('No full tile fits this area. Choose full coverage or a smaller tile size.')
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
            item = QTableWidgetItem(tile.name)
            item.setTextAlignment(Qt.AlignCenter)
            item.setData(Qt.UserRole, tile.name)
            item.setToolTip(f'Core: {tile.core}\nInput including context: {tile.source}')
            self.table.setItem(tile.row, tile.column, item)
        self._make_grid_layers()
        core = pixels - 2 * halo
        unit = 'm/pixel'
        detail = (f'{columns} columns × {rows} rows · {len(tiles)} tiles\n'
                  f'Tile size {side:g} m × {side:g} m\n'
                  f'Input {pixels} px · Core {core} px · Context {halo} px/side\n'
                  f'{crs.authid() or crs.description()} · {resolution:.8g} {unit}')
        area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
        excess = max(0, (rows * columns * side * side / area - 1) * 100)
        alignment = 'Fitted to area' if self._fitted_size else 'Fixed tile size'
        self.tile_size.setToolTip(f'{alignment} · {columns} × {rows} · {excess:.1f}% overhang')
        self._update_fit_buttons()
        self._tell(detail + f'\n{alignment} · {excess:.1f}% overhang')
        self.select_all()
        self._tell('Grid created. Area and tiles are fixed in map coordinates. All tiles selected.')

    def _make_grid_layers(self):
        for layer_id in self.grid_layers:
            if self.project.mapLayer(layer_id):
                self.project.removeMapLayer(layer_id)
        self.grid_layers = []
        self.tile_features = {}
        root = self.project.layerTreeRoot()
        group = root.insertGroup(0, 'AI · Tile grid')
        # Empty old groups are removed only if they were created by this panel.
        old_group = root.findGroup(getattr(self, '_previous_grid_group_name', '') or '')
        if old_group is not None and old_group != group and not old_group.children():
            old_group.parent().removeChildNode(old_group)
        group.setName('AI · Tile grid · ' + uuid.uuid4().hex[:6])
        self._previous_grid_group_name = group.name()
        for name, field, color in [('Tile cores', 'core', '#2879b9'), ('Inputs including context', 'source', '#df8c30')]:
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
        self._grid_visibility()

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
        self.tiles_page.setTitle(f'Tiles · {len(tiles)}/{len(self.tiles)}')
        self._update_generate()
        layer = self.project.mapLayer(getattr(self, 'core_layer_id', ''))
        if layer:
            layer.selectByIds([self.tile_features[tile.name] for tile in tiles if tile.name in self.tile_features])

    def _grid_visibility(self, *args):
        for index, layer_id in enumerate(self.grid_layers):
            node = self.project.layerTreeRoot().findLayer(layer_id)
            if node:
                node.setItemVisibilityChecked(self.show_frames.isChecked() and
                                             (index == 0 or self.show_overlap.isChecked()))

    def _output_directory(self):
        path = self.output.text().strip()
        if path:
            directory = Path(path).expanduser()
            if not directory.is_dir():
                raise ValueError('Choose an existing output folder.')
            return directory
        if self._temporary_output is None:
            # Keep files alive for loaded QGIS layers, including after panel reload.
            self._temporary_output = tempfile.mkdtemp(prefix='mapstyle-ai-')
        return Path(self._temporary_output)

    def choose_output(self):
        directory = QFileDialog.getExistingDirectory(self, 'Choose output folder', self.output.text())
        if directory:
            self.output.setText(directory)

    def choose_reference(self):
        filenames, _ = QFileDialog.getOpenFileNames(
            self, 'Choose style references', '', 'Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff)')
        self.add_references(filenames)

    def add_references(self, filenames):
        invalid = []
        for filename in filenames:
            path = str(Path(filename).expanduser().resolve())
            if any(reference['path'] == path for reference in self.references):
                continue
            if QImage(path).isNull():
                invalid.append(Path(path).name)
                continue
            self.references.append(dict(id=self._next_reference_id, path=path))
            self._next_reference_id += 1
        self._reference_preview()
        if invalid:
            self._tell('Unreadable reference images: ' + ', '.join(invalid), True)

    def remove_reference(self, reference_id):
        if self._busy:
            return
        self.references = [reference for reference in self.references if reference['id'] != reference_id]
        self._reference_preview()

    def _reference_preview(self):
        self.reference_list.clear()
        self.reference_list.setVisible(bool(self.references))
        for reference in self.references:
            card = QWidget()
            row = QHBoxLayout(card)
            preview_column = QVBoxLayout()
            preview_column.addWidget(QLabel(f'Reference {reference["id"]}'))
            preview = QLabel()
            pixmap = QPixmap(reference['path'])
            if pixmap.isNull():
                preview.setText('Image unavailable')
            else:
                preview.setPixmap(pixmap.scaled(100, 80, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            preview_column.addWidget(preview)
            row.addLayout(preview_column)
            name = text_label(Path(reference['path']).name)
            name.setToolTip(reference['path'])
            row.addWidget(name, 1)
            remove = button('Remove', lambda checked=False, number=reference['id']: self.remove_reference(number))
            remove.setAccessibleName(f'Remove Reference {reference["id"]}')
            row.addWidget(remove)
            item = QListWidgetItem(self.reference_list)
            item.setData(Qt.UserRole, reference['id'])
            item.setSizeHint(card.sizeHint())
            self.reference_list.setItemWidget(item, card)
        height = sum(self.reference_list.item(i).sizeHint().height()
                     for i in range(self.reference_list.count()))
        self.reference_list.setFixedHeight(min(280, max(110, height + 2 * self.reference_list.frameWidth())))
        self._update_generate()

    def _provider_changed(self, provider):
        self._provider_before_switch = self._provider
        self._keys[self._provider] = self.key.text()
        self._provider = provider
        self.key.setText(self._keys.get(provider, ''))
        self._models[self._provider_before_switch] = self.model.text()
        self.model.setText(self._models.get(provider, MODELS[provider][0]))
        self.model.setToolTip('Other models: ' + ', '.join(MODELS[provider][1:]))
        self.quality.setEnabled(provider == OPENAI)

    def resolved_prompt(self):
        return build_prompt(self.prompt.toPlainText(),
                            [reference['id'] for reference in self.references], self.plan['pixels'])

    def _render_settings(self):
        settings = QgsMapSettings(self.plan['settings'])
        settings.setRotation(0)
        settings.setDevicePixelRatio(1)
        settings.setOutputSize(QSize(self.plan['pixels'], self.plan['pixels']))
        settings.setBackgroundColor(QColor('white'))
        layers = self.selected_layers()
        if not layers:
            raise ValueError('No map layers available.')
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
        self.area_page.setEnabled(not busy)
        self.advanced_page.setEnabled(not busy)
        self.stop_button.setVisible(busy)
        self.table.setEnabled(not busy)
        self.provider_page.setEnabled(not busy)
        self.reference_list.setEnabled(not busy)
        self.reference_button.setEnabled(not busy)
        self.prompt.setEnabled(not busy)
        self.import_button.setEnabled(not busy)
        self.output.setEnabled(not busy)
        self._selection_changed()

    def _set_tile_status(self, tile, status):
        self.statuses[tile.name] = status
        item = self.table.item(tile.row, tile.column)
        if item:
            item.setText(f'{tile.name}\n{status}')
            item.setBackground(QColor('#fde5e2') if status == 'Failed' else
                               QColor('#e4f3ed') if status in ('Exported', 'Done', 'Result') else QColor('#e4eefb'))

    def _add_raster(self, path, name, group, visible=True):
        layer = QgsRasterLayer(str(path), name)
        if not layer.isValid():
            raise ValueError(f'Could not load raster: {path}')
        layer.setCustomProperty('mapstyle_ai/owned', True)
        self.project.addMapLayer(layer, False)
        group.addLayer(layer).setItemVisibilityChecked(visible)
        return layer

    def preview_input(self):
        selected = self.selected_tiles()
        if len(selected) != 1 or selected[0].name not in self.exported:
            self._tell('Select exactly one tile with an exported input to preview.')
            return
        path = self.exported[selected[0].name]['png']
        dialog = QDialog(self)
        dialog.setWindowTitle(f'{selected[0].name} · Actual AI input image')
        layout = QVBoxLayout(dialog)
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setPixmap(QPixmap(path).scaled(760, 760, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(image)
        layout.addWidget(text_label(path))
        layout.addWidget(button('Close', dialog.accept))
        dialog.exec_()

    def _update_generate(self, *args):
        if not hasattr(self, 'generate_button'):
            return
        ready = (self.plan and not self.plan['dirty'] and self.selected_tiles()
                 and self.key.text().strip() and self.model.text().strip()
                 and (self.references or self.prompt.toPlainText().strip()))
        self.generate_button.setEnabled(bool(ready) and not self._busy)
        self.generate_button.setToolTip('Generate selected tiles' if ready else 'Set an area, select tiles, then add an API key and reference images or your instructions.')

    def generate_selected(self):
        if self._busy:
            return
        try:
            tiles = self.selected_tiles()
            if not self.plan or self.plan['dirty'] or not tiles:
                raise ValueError('Update the grid and select tiles first.')
            provider, model = self.provider.currentText(), self.model.text().strip()
            validate_options(provider, model, self.plan['pixels'])
            key = self.key.text().strip()
            if not key or '\n' in key or '\r' in key:
                raise ValueError('Enter a valid API key.')
            validate_style(self.prompt.toPlainText(), [reference['id'] for reference in self.references])
            references = [image_png(reference['path']) for reference in self.references]
            prompt = self.resolved_prompt()
            intent = dict(provider=provider, model=model, key=key, prompt=prompt,
                          references=references, reference_records=[dict(reference) for reference in self.references],
                          user_prompt=self.prompt.toPlainText(), quality=self.quality.currentText(),
                          concurrency=self.concurrent.value(), clip=self.clip.isChecked(),
                          overlap_mode=self.overlap_mode.currentData(),
                          tile_ids=[tile.name for tile in tiles])
            self._launch_generation(intent)
        except Exception as error:
            self._tell(safe_error(error, self.key.text().strip()), True)

    def _launch_generation(self, intent):
        try:
            tiles = [tile for tile in self.tiles if tile.name in intent['tile_ids']]
            if self.plan['halo'] <= 0:
                raise ValueError('Use a context margin greater than zero for seamless connections.')
            parents = dependencies(tiles, self.seam_sources)
            validate_image_count(intent['provider'], intent['model'], len(intent['references']),
                                 max((len(ids) for ids in parents.values()), default=0))
            base = self._output_directory()
            settings = self._render_settings()
            directory = base / (datetime.now().strftime('generation-%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:6])
            for folder in ('inputs', 'responses', 'results', 'neighbors'):
                (directory / folder).mkdir(parents=True, exist_ok=True)
            reference_records = []
            for reference, png in zip(intent['reference_records'], intent['references']):
                relative = f'references/reference-{reference["id"]}.png'
                (directory / relative).parent.mkdir(exist_ok=True)
                (directory / relative).write_bytes(png)
                reference_records.append(dict(id=reference['id'], label=f'Reference {reference["id"]}',
                                              file=relative))
            # Snapshot previous anchors so future jobs remain reproducible.
            existing = {}
            for name, source in self.seam_sources.items():
                if name in intent['tile_ids'] or not any(name in ids for ids in parents.values()):
                    continue
                png, tif = directory/'neighbors'/f'{name}.png', directory/'neighbors'/f'{name}.tif'
                shutil.copyfile(source['png'], png)
                write_geotiff(png, tif, source['bounds'], self.plan['crs'].toWkt())
                existing[name] = dict(source, png=str(png), tif=str(tif))
            manifest = dict(schema_version=3, workflow='live_neighbor_continuation',
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
                            clip_results=intent['clip'], overlap_mode=intent['overlap_mode'],
                            references=reference_records, user_prompt=intent['user_prompt'],
                            prompt_version=PROMPT_VERSION, api_enabled=True,
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
                self._set_tile_status(tile, 'Queued')
            self.progress.setRange(0, len(tiles))
            self.progress.setValue(0)
            self._set_busy(True)
            self.stop_button.setEnabled(True)
            self._load_export_inputs = self.load_inputs.isChecked()
            self._tell(f'Generating {len(tiles)} tiles, up to {intent["concurrency"]} parallel jobs as neighbors become ready.')
            self.generator.start()
        except Exception as error:
            self._set_busy(False)
            self.stop_button.setEnabled(False)
            self._tell(safe_error(error, intent['key']), True)

    def _generation_preparing(self, name):
        self._set_tile_status(self._generation_records[name]['tile'], 'Rendering…')
        self._generation_state(name, 'exporting')

    def _live_input_ready(self, name, prepared):
        record = self._generation_records[name]
        record.update(prepared)
        self.exported[name] = record
        prompt = continuation_prompt(self.generation_manifest['prompt'], prepared['neighbors'], prepared['protected'],
                                     len(self.generation_manifest['references']))
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry.update(input_png=f'inputs/{name}.png', input_tif=f'inputs/{name}.tif',
                             protected_mask=f'inputs/{name}-protected.png', protected=prepared['protected'],
                             neighbors=prepared['neighbors'], prompt=prompt)
        if self._load_export_inputs:
            group = self.project.layerTreeRoot().findGroup(f'AI · {self.generation_directory.name}')
            if group is None:
                group = self.project.layerTreeRoot().insertGroup(1, f'AI · {self.generation_directory.name}')
            inputs = next((g for g in group.children() if g.name()=='Live inputs'), None)
            if inputs is None:
                inputs = group.addGroup('Live inputs')
            self._add_raster(prepared['tif'], f'{name} · Live input', inputs, visible=False)
        self._generation_state(name, 'prepared')

    def _accept_seam_result(self, name, filename, prepared):
        record = self._generation_records[name]
        mode = record['manifest'].get('overlap_mode', 'blend')
        if mode not in OVERLAP_MODES:
            raise ValueError('Unknown overlap handling mode.')
        result_path = filename
        if mode != 'original':
            image = QImage(filename)
            original = QImage(prepared['png'])
            result = (copy_regions(image, original, prepared['protected']) if mode == 'copy' else
                      blend_regions(image, original, prepared['protected'], record['manifest']['halo_pixels']))
            result_path = self.generation_directory/'results'/f'{name}-{mode}.png'
            if not result.save(str(result_path), 'PNG'):
                raise ValueError('Could not save the tile transition.')
        source = self._store_result(record['tile'], record, str(result_path), 'api_generation',
                                    variant=OVERLAP_MODES[mode][2])
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
            self._tell(f'Could not save generation log: {save_error}', True)
        done = sum(entry['state'] in ('completed', 'failed', 'cancelled') for entry in manifest['tiles'])
        self.progress.setValue(done)

    def _generation_started(self, name):
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry['request_started_at'] = datetime.now().astimezone().isoformat()
        self._set_tile_status(self._generation_records[name]['tile'], 'Generating…')
        self._generation_state(name, 'running')

    def _generation_succeeded(self, name, filename):
        record = self._generation_records[name]
        try:
            if not record.get('stored'):
                raise ValueError('Result has not been registered as a neighbor yet.')
            self.generated.add(name)
            self._set_tile_status(record['tile'], 'Done')
            self._generation_state(name, 'completed')
            mode = record['manifest'].get('overlap_mode', 'blend')
            self._tell(f'{name}: Saved result · {OVERLAP_MODES[mode][0]}.')
        except Exception as error:
            self._generation_failed(name, safe_error(error, self.key.text().strip()))

    def _generation_failed(self, name, error):
        for entry in self.generation_manifest['tiles']:
            if entry['id'] == name:
                entry['failed_at'] = datetime.now().astimezone().isoformat()
        self._set_tile_status(self._generation_records[name]['tile'], 'Failed')
        self._generation_state(name, 'failed', error)
        self._tell(f'{name}: {error}', True)

    def _generation_cancelled_tile(self, name):
        self._set_tile_status(self._generation_records[name]['tile'], 'Cancelled')
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
        self._tell(f'Generation finished: {completed} done, {failed} failed, {states.count("cancelled")} cancelled. Completed images remain loaded.')

    def cancel_generation(self):
        if self.generator and self.generator.active:
            self.generator.cancel()
            self._tell('Cancelling generation. Requests already sent may continue at the provider and incur charges.')


    def import_result(self, filename=None):
        # Qt passes False as clicked(bool); tests can pass a filename directly.
        if self._busy:
            return
        selected = self.selected_tiles()
        if len(selected) != 1 or selected[0].name not in self.exported:
            self._tell('Select exactly one tile with an exported input.', True)
            return
        if not isinstance(filename, str):
            filename, _ = QFileDialog.getOpenFileName(self, 'Choose result image', '', 'Images (*.png *.jpg *.jpeg *.webp *.tif *.tiff)')
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
            raise ValueError(f'The result must be exactly {pixels} × {pixels} pixels. Images are never stretched automatically.')
        window, extent = crop_window(tile, manifest['resolution'], manifest['halo_pixels'], pixels,
                                     manifest['bounds'] if manifest['clip_results'] else None)
        directory = Path(record['directory'])
        stem = f'{tile.name}-{uuid.uuid4().hex[:8]}'
        original = directory / 'results' / f'{stem}-full.png'
        png = directory / 'results' / f'{stem}-core.png'
        tif = directory / 'results' / f'{stem}.tif'
        if not image.save(str(original), 'PNG') or not image.copy(*window).save(str(png), 'PNG'):
            raise ValueError('Could not save result image.')
        write_geotiff(png, tif, extent, manifest['crs_wkt'])
        context_tif = directory / 'results' / f'{stem}-context.tif'
        write_geotiff(original, context_tif, tile.source, manifest['crs_wkt'])
        # Find by run path rather than a potentially removed layer tree wrapper.
        group = self.project.layerTreeRoot().findGroup(f'AI · {directory.name}')
        if group is None:
            group = self.project.layerTreeRoot().insertGroup(1, f'AI · {directory.name}')
        group_name = 'Results · ' + variant if variant else 'Results'
        result_group = next((child for child in group.children() if child.name() == group_name), None)
        if result_group is None:
            result_group = group.insertGroup(0, group_name)
            result_group.setItemVisibilityChecked(visible)
        self._add_raster(tif, f'{tile.name} · {variant or "Result"}', result_group)
        for entry in manifest['tiles']:
            if entry['id'] == tile.name:
                entry.setdefault('results', []).append(dict(
                    file=f'results/{tif.name}', full_image=f'results/{original.name}',
                    context_tif=f'results/{context_tif.name}',
                    bounds=extent, crop_window=window, origin=origin, variant=variant))
        write_json(directory / 'manifest.json', manifest)
        self._set_tile_status(tile, 'Result')
        self._tell(f'Loaded georeferenced result for {tile.name}.')
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
