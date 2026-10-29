"""Asynchronous QGIS rendering and local, georeferenced image export."""
import json
from pathlib import Path

from osgeo import gdal
from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import QgsMapRendererSequentialJob, QgsMapSettings, QgsRectangle


def write_json(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def write_geotiff(png, target, bounds, crs_wkt):
    xmin, ymin, xmax, ymax = bounds
    dataset = gdal.Translate(
        str(target), str(png), format='GTiff',
        outputBounds=[xmin, ymax, xmax, ymin], outputSRS=crs_wkt,
        creationOptions=['COMPRESS=DEFLATE', 'TILED=YES'],
    )
    if dataset is None:
        raise RuntimeError(f'Could not write GeoTIFF: {target}')
    dataset.FlushCache()
    dataset = None


class TileExporter(QObject):
    tile_done = pyqtSignal(str, str, str)
    progress = pyqtSignal(int, int)
    completed = pyqtSignal(bool, str)

    def __init__(self, settings, tiles, directory, manifest, parent=None):
        super().__init__(parent)
        self.settings = QgsMapSettings(settings)
        self.tiles = list(tiles)
        self.directory = Path(directory)
        self.manifest = manifest
        self.index = 0
        self.job = None
        self.cancelled = False
        self.active = False

    def start(self):
        self.active = True
        self.manifest['state'] = 'exporting'
        self._save()
        QTimer.singleShot(0, self._next)

    def _save(self):
        write_json(self.directory / 'manifest.json', self.manifest)

    def _next(self):
        if not self.active:
            return
        if self.cancelled or self.index >= len(self.tiles):
            self._finish('cancelled' if self.cancelled else 'exported')
            return
        tile = self.tiles[self.index]
        settings = QgsMapSettings(self.settings)
        settings.setExtent(QgsRectangle(*tile.source))
        actual = settings.visibleExtent()
        if any(abs(a - b) > max(settings.mapUnitsPerPixel() * 1e-5, 1e-10)
               for a, b in zip(tile.source, (
                   actual.xMinimum(), actual.yMinimum(), actual.xMaximum(), actual.yMaximum()))):
            self._finish('failed', 'Render extent does not match the tile grid.')
            return
        self.job = QgsMapRendererSequentialJob(settings)
        self.job.finished.connect(self._rendered)
        self.job.start()

    def _rendered(self):
        if not self.active:
            return
        tile = self.tiles[self.index]
        job = self.job
        self.job = None
        try:
            if self.cancelled:
                self._finish('cancelled')
                return
            errors = job.errors()
            if errors:
                raise RuntimeError('; '.join(error.message for error in errors))
            image = job.renderedImage()
            png = self.directory / 'inputs' / f'{tile.name}.png'
            tif = self.directory / 'inputs' / f'{tile.name}.tif'
            if image.isNull() or not image.save(str(png), 'PNG'):
                raise RuntimeError(f'Image export failed: {tile.name}')
            write_geotiff(png, tif, tile.source, self.manifest['crs_wkt'])
            record = self.manifest['tiles'][self.index]
            record.update(state='exported', input_png=f'inputs/{png.name}',
                          input_tif=f'inputs/{tif.name}')
            self._save()
            self.index += 1
            self.tile_done.emit(tile.name, str(png), str(tif))
            self.progress.emit(self.index, len(self.tiles))
            QTimer.singleShot(0, self._next)
        except Exception as error:
            self.manifest['tiles'][self.index]['state'] = 'failed'
            self._finish('failed', str(error))

    def _finish(self, state, error=''):
        self.active = False
        self.manifest['state'] = state
        if error:
            self.manifest['error'] = error
        try:
            self._save()
        except OSError as save_error:
            state, error = 'failed', str(save_error)
        self.completed.emit(state == 'exported', error or state)

    def cancel(self):
        self.cancelled = True
        if self.job is not None:
            self.job.cancelWithoutBlocking()

    def shutdown(self):
        """Disconnect first: never let a job call into a deleted dock on reload."""
        self.active = False
        self.cancelled = True
        if self.job is not None:
            self.job.finished.disconnect(self._rendered)
            self.job.cancel()
            self.job = None
        self.manifest['state'] = 'cancelled'
        self._save()
