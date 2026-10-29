"""Offline QGIS integration for numbered references and all input modes; no HTTP."""
import base64
from email import policy
from email.parser import BytesParser
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qgis.core import QgsApplication, QgsProject, QgsCoordinateReferenceSystem, QgsVectorLayer, QgsFeature, QgsGeometry, QgsRectangle
from qgis.gui import QgsMapCanvas
from qgis.PyQt.QtCore import Qt, QObject, pyqtSignal, QTimer, QEventLoop
from qgis.PyQt.QtGui import QImage, QColor
from qgis.PyQt.QtWidgets import QMainWindow, QPushButton
from mapstyle_ai.panel import BackgroundPanel
from mapstyle_ai.providers import GOOGLE, OPENAI, make_request
import mapstyle_ai.seam_run as run_module

app = QgsApplication([], False)
app.initQgis()
project = QgsProject.instance()
window = QMainWindow()
canvas = QgsMapCanvas()
canvas.resize(800, 600)
canvas.setDestinationCrs(QgsCoordinateReferenceSystem('EPSG:32632'))
layer = QgsVectorLayer('Polygon?crs=EPSG:32632&field=name:string', 'Synthetic map', 'memory')
feature = QgsFeature(layer.fields())
feature.setGeometry(QgsGeometry.fromRect(QgsRectangle(500100, 5300200, 500700, 5300800)))
layer.dataProvider().addFeatures([feature])
layer.updateExtents()
project.addMapLayer(layer)
canvas.setLayers([layer])
canvas.setExtent(layer.extent())
class Interface:
    def mainWindow(self): return window
    def mapCanvas(self): return canvas
panel = BackgroundPanel(Interface())
panel._suppress_notifications = True
window.addDockWidget(Qt.RightDockWidgetArea, panel)
window.resize(580, 1050)
window.show()
app.processEvents()
panel.area.setCurrentIndex(1)
panel._grid_area = ((500100, 5300200, 500700, 5300800), canvas.mapSettings().destinationCrs())
panel.tile_unit.setCurrentIndex(panel.tile_unit.findData(1))
panel.tile_size.setValue(400)
panel._build()
panel.more_tiles.click()
assert len(panel.tiles) == 4, len(panel.tiles)
assert panel.prompt.toPlainText() == ''
assert 'Reference 1' in panel.prompt.placeholderText()
assert not hasattr(panel, 'reset_prompt_button')
panel.key.setText('offline-test-key')
assert not panel.generate_button.isEnabled()

# Fake only the HTTP queue: real live exports, scheduling, image processing and manifests run.
captured = []
class OfflineQueue(QObject):
    started = pyqtSignal(str)
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    finished = pyqtSignal()
    def __init__(self, jobs, provider, model, key, prompt, references, concurrency, quality, parent):
        super().__init__(parent)
        self.jobs = jobs
        self.references = references
        self.prompt = prompt
        self.provider, self.model = provider, model
    def start(self): QTimer.singleShot(0, self.complete)
    def complete(self):
        for job in self.jobs:
            source = Path(job['input']).read_bytes()
            _, headers, body = make_request(self.provider, self.model, 'dummy', self.prompt,
                                           job['pixels'], source, self.references, neighbors=job['neighbors'])
            if self.provider == OPENAI:
                msg = BytesParser(policy=policy.default).parsebytes(
                    ('Content-Type: ' + headers['Content-Type'] + '\r\n\r\n').encode() + body)
                images = [part.get_payload(decode=True) for part in msg.iter_parts() if part.get_filename()]
            else:
                images = [base64.b64decode(part['inlineData']['data'])
                          for part in json.loads(body)['contents'][0]['parts'] if 'inlineData' in part]
            assert images == [source, *self.references, *job['neighbors']]
            captured.append((job['id'], self.prompt, len(self.references), len(job['neighbors'])))
            self.started.emit(job['id'])
            Path(job['output']).write_bytes(source)
            self.succeeded.emit(job['id'], job['output'])
        self.finished.emit()
    def shutdown(self): pass
run_module.GenerationQueue = OfflineQueue

with tempfile.TemporaryDirectory(prefix='mapstyle-references-') as temp:
    panel.output.setText(temp)
    paths = []
    for number, color in enumerate(('#ff0000', '#00ff00', '#0000ff', '#aaaaaa'), 1):
        image = QImage(48, 48, QImage.Format_ARGB32)
        image.fill(QColor(color))
        path = str(Path(temp) / f'style-{number}.png')
        image.save(path)
        paths.append(path)
    panel.add_references(paths[:3])
    assert panel.generate_button.isEnabled()
    assert panel.reference_list.count() == 3
    panel.prompt.setPlainText('Colors like Reference 1; lines like Referenz 3.')
    removes = [b for b in panel.reference_list.findChildren(QPushButton)
               if b.accessibleName() == 'Remove Reference 2']
    assert len(removes) == 1
    removes[0].click()
    panel.add_references([paths[3], paths[0]])  # Duplicate does not allocate a new number.
    assert [r['id'] for r in panel.references] == [1, 3, 4]
    assert panel._next_reference_id == 5
    assert 'Image 3 = Reference 3' in panel.resolved_prompt()
    panel.prompt.setPlainText('Reference 2 colors')
    panel.generate_selected()
    assert not panel._busy and 'Reference 2 is not attached' in panel.log.toPlainText()
    # Empty, image-only, mixed and text-only behavior with actual dependency scheduling.
    for provider in (OPENAI, GOOGLE):
        for mode in ('images', 'mixed', 'text'):
            panel.provider.setCurrentText(provider)
            panel.key.setText('offline-test-key')
            panel.references = ([] if mode == 'text' else
                                [dict(id=1, path=paths[0]), dict(id=3, path=paths[2])])
            panel._reference_preview()
            panel.prompt.setPlainText('' if mode == 'images' else
                                     ('Colors like Referenz 3.' if mode == 'mixed' else 'Watercolor, light colors.'))
            panel._build()  # Reset old anchors and select all four tiles.
            assert panel.generate_button.isEnabled()
            captured.clear()
            panel.generate_selected()
            assert panel._busy, panel.log.toPlainText()
            assert not panel.reference_list.isEnabled() and not panel.prompt.isEnabled()
            loop = QEventLoop()
            timed_out = []
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: (timed_out.append(True), loop.quit()))
            panel.generator.finished.connect(loop.quit)
            timer.start(15000)
            loop.exec()
            timer.stop()
            assert not timed_out, panel.log.toPlainText()
            assert not panel._busy, panel.log.toPlainText()
            manifest = json.loads((panel.generation_directory / 'manifest.json').read_text())
            assert len(captured) == 4, (captured, panel.log.toPlainText())
            assert all(tile['state'] == 'completed' for tile in manifest['tiles']), manifest['tiles']
            assert manifest['user_prompt'] == panel.prompt.toPlainText()
            assert manifest['schema_version'] == 3 and manifest['prompt_version'] == 1
            assert [r['id'] for r in manifest['references']] == ([] if mode == 'text' else [1, 3])
            for reference in manifest['references']:
                assert (panel.generation_directory / reference['file']).is_file()
            for name, prompt, refs, neighbors in captured:
                tile = next(t for t in manifest['tiles'] if t['id'] == name)
                assert tile['prompt'] == prompt
                if neighbors:
                    assert f'Image {2 + refs}: neighbor tile' in prompt
                elif not refs:
                    assert 'Image 2' not in prompt
            assert 'offline-test-key' not in json.dumps(manifest)
    panel.references = []
    panel.prompt.clear()
    panel._reference_preview()
    assert not panel.generate_button.isEnabled()
    # No UI selection cap; reject a provider overflow before any tile exports/requests.
    panel.references = [dict(id=i, path=paths[0]) for i in range(1, 15)]
    panel._reference_preview()
    panel.provider.setCurrentText(OPENAI)
    panel.key.setText('dummy')
    panel._build()
    captured.clear()
    panel.generate_selected()
    assert not panel._busy and not captured
    assert '17' in panel.log.toPlainText() and 'at most 16' in panel.log.toPlainText()
    panel.references = [dict(id=1, path=paths[0]), dict(id=3, path=paths[2])]
    panel._reference_preview()
    panel.prompt.setPlainText('Colors like Reference 1, linework like Reference 3.')
    panel.key.clear()
    app.processEvents()
    panel.grab().save('/private/tmp/mapstyle-references-preview.png')
    # Exercise state migration in an isolated runtime; only plugin construction is stubbed.
    from types import SimpleNamespace
    import qgis.utils
    import mapstyle_ai.seams as seam_module
    from qgis.PyQt.QtWidgets import QLineEdit
    reload_source = (Path(__file__).parent / 'reload_preserve.py').read_text()
    reload_source = reload_source.replace(
        "exec((Path(MAPSTYLE_REPO)/'scripts/dev_load.py').read_text())", 'reload_test()')
    qgis.utils.plugins['mapstyle_ai'] = SimpleNamespace(dock=panel)
    def reload_test():
        previous = qgis.utils.plugins['mapstyle_ai'].dock
        previous.shutdown()
        window.removeDockWidget(previous)
        previous.deleteLater()
        replacement = BackgroundPanel(Interface())
        replacement._suppress_notifications = True
        window.addDockWidget(Qt.RightDockWidgetArea, replacement)
        qgis.utils.plugins['mapstyle_ai'] = SimpleNamespace(dock=replacement)
    next_id = panel._next_reference_id
    selected = {tile.name for tile in panel.selected_tiles()}
    exec(reload_source, dict(reload_test=reload_test))
    panel = qgis.utils.plugins['mapstyle_ai'].dock
    assert [r['id'] for r in panel.references] == [1, 3]
    assert panel._next_reference_id == next_id
    assert panel.prompt.toPlainText() == 'Colors like Reference 1, linework like Reference 3.'
    assert {tile.name for tile in panel.selected_tiles()} == selected
    # Simulate the previous single-image interface with its unchanged default prompt.
    del panel.references
    del panel._next_reference_id
    panel.reference = QLineEdit(paths[0])
    legacy_default = 'Previous default map instructions'
    panel.prompt.setPlainText(legacy_default)
    original_default = seam_module.DEFAULT_PROMPT
    seam_module.DEFAULT_PROMPT = legacy_default
    exec(reload_source, dict(reload_test=reload_test))
    seam_module.DEFAULT_PROMPT = original_default
    panel = qgis.utils.plugins['mapstyle_ai'].dock
    assert panel.references == [dict(id=1, path=paths[0])]
    assert panel._next_reference_id == 2
    assert panel.prompt.toPlainText() == ''
    panel.shutdown()
    qgis.utils.plugins.pop('mapstyle_ai', None)
    project.clear()
print('PASS: numbered references, removal, text-only/images-only/mixed, both wire formats, six 2x2 dependency runs, manifests, busy state, provider preflight, state reload and legacy migration; external API calls: 0')
