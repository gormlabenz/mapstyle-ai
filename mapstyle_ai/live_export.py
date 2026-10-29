# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Render one current tile, including full generated neighbors, without API calls."""
from pathlib import Path

from qgis.PyQt.QtCore import QObject, QRect, Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor, QImage, QLinearGradient, QPainter
from qgis.core import QgsMapSettings, QgsMapRendererSequentialJob, QgsRasterLayer, QgsRectangle

from .exporter import write_geotiff
from .seams import overlap_windows


def copy_regions(image, original, rectangles):
    result = image.convertToFormat(QImage.Format_ARGB32)
    painter = QPainter(result)
    painter.setCompositionMode(QPainter.CompositionMode_Source)
    for rectangle in rectangles:
        rect = QRect(*rectangle)
        painter.drawImage(rect, original, rect)
    painter.end()
    return result


def blend_regions(image, original, rectangles, halo):
    """Fade old pixels from 100% at the visible core edge to 0% inward.

    The cropped outer halo remains fully old. At top/left intersections the
    two opacity ramps combine with source-over, without painting the old image
    twice. With no neighbors the provider response is unchanged.
    """
    result = image.convertToFormat(QImage.Format_ARGB32)
    if not rectangles:
        return result
    mask = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
    mask.fill(Qt.transparent)
    painter = QPainter(mask)
    for x, y, width, height in rectangles:
        if width < image.width():  # Left neighbor: fade towards the right.
            gradient = QLinearGradient(x + min(halo, width - 1) + 0.5, 0,
                                       x + width - 0.5, 0)
        else:  # Top neighbor: fade downwards.
            gradient = QLinearGradient(0, y + min(halo, height - 1) + 0.5,
                                       0, y + height - 0.5)
        gradient.setColorAt(0, QColor(255, 255, 255, 255))
        gradient.setColorAt(1, QColor(255, 255, 255, 0))
        painter.fillRect(QRect(x, y, width, height), gradient)
    painter.end()
    overlay = original.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    painter = QPainter(overlay)
    painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
    painter.drawImage(0, 0, mask)
    painter.end()
    painter = QPainter(result)
    painter.drawImage(0, 0, overlay)
    painter.end()
    return result


class LiveTileExport(QObject):
    completed = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, settings, tile, neighbors, directory, parent=None):
        super().__init__(parent)
        self.settings = QgsMapSettings(settings)
        self.tile, self.neighbors = tile, list(neighbors)
        self.directory = Path(directory)
        self.job = None
        self.layers = []
        self.active = False

    def start(self):
        try:
            for neighbor in self.neighbors:
                layer = QgsRasterLayer(neighbor['tif'], 'AI · Anschluss ' + neighbor['id'])
                if not layer.isValid():
                    raise ValueError('Neighbor tile is no longer readable: ' + neighbor['id'])
                self.layers.append(layer)
            self.settings.setLayers(self.layers + self.settings.layers())
            self.settings.setExtent(QgsRectangle(*self.tile.source))
            actual = self.settings.visibleExtent()
            expected = self.tile.source
            values = (actual.xMinimum(), actual.yMinimum(), actual.xMaximum(), actual.yMaximum())
            if any(abs(a-b) > max(self.settings.mapUnitsPerPixel()*1e-5, 1e-10) for a,b in zip(expected, values)):
                raise ValueError('Render extent does not match the tile grid.')
            self.job = QgsMapRendererSequentialJob(self.settings)
            self.job.finished.connect(self._finished)
            self.active = True
            self.job.start()
        except Exception as error:
            self.active = False
            self.failed.emit(str(error))

    def _finished(self):
        self.active = False
        try:
            errors = self.job.errors()
            if errors:
                raise ValueError('; '.join(error.message for error in errors))
            image = self.job.renderedImage().convertToFormat(QImage.Format_ARGB32)
            if image.isNull():
                raise ValueError('Could not render the tile.')
            resolution = self.settings.mapUnitsPerPixel()
            rectangles = []
            # Match QGIS draw order: the first/top neighbor has priority in corners.
            for neighbor in reversed(self.neighbors):
                windows = overlap_windows(self.tile.source, neighbor['bounds'], resolution)
                if windows is None:
                    raise ValueError('Neighbor tile has no overlapping context.')
                target, source = windows
                previous = QImage(neighbor['png'])
                if previous.isNull() or previous.size() != image.size():
                    raise ValueError('Neighbor tile has an unexpected image size.')
                painter = QPainter(image)
                painter.setCompositionMode(QPainter.CompositionMode_Source)
                painter.drawImage(QRect(*target), previous, QRect(*source))
                painter.end()
            # Record the rectangles in reference-image order (top, left).
            for neighbor in self.neighbors:
                rectangles.append(overlap_windows(self.tile.source, neighbor['bounds'], resolution)[0])
            mask = QImage(image.size(), QImage.Format_RGB32)
            mask.fill(Qt.black)
            painter = QPainter(mask)
            for rectangle in rectangles:
                painter.fillRect(QRect(*rectangle), Qt.white)
            painter.end()
            self.directory.mkdir(parents=True, exist_ok=True)
            png = self.directory / f'{self.tile.name}.png'
            tif = self.directory / f'{self.tile.name}.tif'
            mask_path = self.directory / f'{self.tile.name}-protected.png'
            if not image.save(str(png), 'PNG') or not mask.save(str(mask_path), 'PNG'):
                raise ValueError('Could not save live input image.')
            write_geotiff(png, tif, self.tile.source, self.settings.destinationCrs().toWkt())
            self.completed.emit(dict(png=str(png), tif=str(tif), mask=str(mask_path),
                                     protected=rectangles, neighbors=[n['id'] for n in self.neighbors]))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            self.job = None
            self.layers.clear()

    def shutdown(self):
        self.active = False
        if self.job:
            self.job.finished.disconnect(self._finished)
            self.job.cancel()
            self.job = None
        self.layers.clear()
