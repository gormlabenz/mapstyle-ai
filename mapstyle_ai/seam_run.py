"""Dependency-aware orchestration: live export -> API -> insertion -> next tile."""
from pathlib import Path

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from .generation import GenerationQueue
from .live_export import LiveTileExport
from .seams import dependencies, ready_tiles, blocked_tiles, continuation_prompt


class SeamlessRun(QObject):
    preparing = pyqtSignal(str)
    input_ready = pyqtSignal(str, object)
    started = pyqtSignal(str)
    succeeded = pyqtSignal(str, str)
    failed = pyqtSignal(str, str)
    cancelled_tile = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, settings, tiles, existing, directory, options, accept, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tiles = {tile.name: tile for tile in tiles}
        self.available = {name: source for name, source in existing.items() if name not in self.tiles}
        self.parents = dependencies(tiles, self.available)
        self.pending = list(self.tiles)
        self.running = {}
        self.completed = set(self.available)
        self.failed_ids = set()
        self.directory = Path(directory)
        self.options = dict(options)
        self.accept = accept
        self.active = False
        self.cancelled = False
        self.max_active = 0
        self.prepared = {}
        self._key = self.options.pop('key')

    def start(self):
        self.active = True
        QTimer.singleShot(0, self._pump)

    def _pump(self):
        if not self.active:
            return
        blocked = blocked_tiles(self.pending, self.parents, self.failed_ids)
        for name in list(self.pending):
            if name in blocked:
                self.pending.remove(name)
                self.failed_ids.add(name)
                self.failed.emit(name, 'Nicht gestartet: Ein benötigter Nachbar ist fehlgeschlagen.')
        ready = ready_tiles(self.pending, self.parents, self.completed)
        while ready and len(self.running) < self.options['concurrency'] and not self.cancelled:
            name = ready.pop(0)
            self.pending.remove(name)
            neighbors = [self.available[parent] for parent in self.parents[name]]
            exporter = LiveTileExport(self.settings, self.tiles[name], neighbors, self.directory/'inputs', self)
            self.running[name] = exporter
            exporter.completed.connect(lambda result, n=name: self._exported(n, result))
            exporter.failed.connect(lambda error, n=name: self._failed(n, error))
            self.max_active = max(self.max_active, len(self.running))
            self.preparing.emit(name)
            exporter.start()
        if not self.pending and not self.running:
            self.active = False
            self._key = ''
            self.options['reference'] = b''
            self.finished.emit()

    def _exported(self, name, prepared):
        if not self.active or self.cancelled:
            return
        try:
            self._start_request(name, prepared)
        except Exception as error:
            self._failed(name, str(error))

    def _start_request(self, name, prepared):
        exporter = self.running.pop(name)
        exporter.deleteLater()
        self.prepared[name] = prepared
        self.input_ready.emit(name, prepared)
        prompt = continuation_prompt(self.options['prompt'], prepared['neighbors'], prepared['protected'])
        neighbor_bytes = [Path(self.available[parent]['png']).read_bytes() for parent in prepared['neighbors']]
        job = dict(id=name, input=prepared['png'], pixels=self.options['pixels'], prompt=prompt,
                   neighbors=neighbor_bytes, output=str(self.directory/'responses'/f'{name}.png'))
        queue = GenerationQueue([job], self.options['provider'], self.options['model'], self._key,
                                prompt, self.options['reference'], 1, self.options['quality'], parent=self)
        self.running[name] = queue
        queue.started.connect(self.started)
        queue.succeeded.connect(self._succeeded)
        queue.failed.connect(self._failed)
        # A finished reply is kept alive until its own callbacks unwind.
        queue.finished.connect(lambda q=queue: q.deleteLater())
        queue.start()

    def _succeeded(self, name, filename):
        try:
            # The callback restores protected pixels and inserts the result synchronously.
            # Dependents become eligible only after that work has succeeded.
            source = self.accept(name, filename, self.prepared[name])
            self.available[name] = source
            self.completed.add(name)
            self.running.pop(name, None)
            self.succeeded.emit(name, filename)
            QTimer.singleShot(0, self._pump)
        except Exception as error:
            self._failed(name, str(error))

    def _failed(self, name, error):
        child = self.running.pop(name, None)
        if isinstance(child, LiveTileExport):
            child.deleteLater()
        self.failed_ids.add(name)
        self.failed.emit(name, error)
        if error.startswith(('HTTP 401', 'HTTP 402', 'HTTP 403', 'HTTP 429')):
            for queued in self.pending:
                self.cancelled_tile.emit(queued)
            self.pending.clear()
            for queued, work in list(self.running.items()):
                if isinstance(work, LiveTileExport) or (isinstance(work, GenerationQueue) and not work.running):
                    work.shutdown()
                    work.deleteLater()
                    self.running.pop(queued, None)
                    self.cancelled_tile.emit(queued)
        QTimer.singleShot(0, self._pump)

    def cancel(self):
        self.cancelled = True
        for name in self.pending:
            self.cancelled_tile.emit(name)
        self.pending.clear()
        for name, child in list(self.running.items()):
            child.shutdown()
            child.deleteLater()
            self.running.pop(name, None)
            self.cancelled_tile.emit(name)
        QTimer.singleShot(0, self._pump)

    def shutdown(self):
        self.active = False
        for child in self.running.values():
            child.shutdown()
            child.deleteLater()
        self.running.clear()
        self.pending.clear()
        self._key = ''
        self.options['reference'] = b''
