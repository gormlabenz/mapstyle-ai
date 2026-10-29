"""QGIS-independent, north-up tile geometry. All bounds use one map CRS."""
from dataclasses import asdict, dataclass
import math


def column_name(index):
    result = ""
    while index >= 0:
        index, remainder = divmod(index, 26)
        result = chr(65 + remainder) + result
        index -= 1
    return result


@dataclass(frozen=True)
class Tile:
    row: int
    column: int
    core: tuple
    source: tuple

    @property
    def name(self):
        return f"{column_name(self.column)}{self.row + 1}"

    def record(self):
        return {"id": self.name, **asdict(self)}


def build_grid(bounds, resolution, pixels=1024, halo=128, contained=False, limit=2500, tile_size=None, origin=(0, 0)):
    xmin, ymin, xmax, ymax = bounds
    if not all(math.isfinite(v) for v in (*bounds, resolution)):
        raise ValueError("Ausdehnung und Auflösung müssen endlich sein.")
    if xmax <= xmin or ymax <= ymin or resolution <= 0:
        raise ValueError("Gebiet und Kartenauflösung müssen größer als null sein.")
    if pixels <= 0 or halo < 0 or 2 * halo >= pixels:
        raise ValueError("Der Kontextsaum muss kleiner als die halbe Bildkante sein.")
    side = (pixels - 2 * halo) * resolution if tile_size is None else tile_size
    if not math.isfinite(side) or side <= 0:
        raise ValueError("Die Kachelgröße muss endlich und größer als null sein.")
    if tile_size is not None:
        # A fitted grid uses the saved AOI corner; manual grids use CRS origin.
        if not all(math.isfinite(value) for value in origin):
            raise ValueError("Der Rasterursprung muss endlich sein.")
        ox, oy = origin
        def snap(value):
            ratio = value / side
            nearest = round(ratio)
            return nearest if math.isclose(ratio, nearest, rel_tol=0, abs_tol=1e-9) else ratio
        lower = math.ceil if contained else math.floor
        upper = math.floor if contained else math.ceil
        xmin, ymin = ox + lower(snap(xmin - ox)) * side, oy + lower(snap(ymin - oy)) * side
        xmax, ymax = ox + upper(snap(xmax - ox)) * side, oy + upper(snap(ymax - oy)) * side
        resolution = side / (pixels - 2 * halo)
    def count(length):
        ratio = length / side
        # Avoid losing exact boundary tiles to floating-point CRS arithmetic.
        rounded = round(ratio)
        if math.isclose(ratio, rounded, rel_tol=1e-10, abs_tol=1e-10):
            ratio = rounded
        return math.floor(ratio) if contained else math.ceil(ratio)
    columns, rows = max(0, count(xmax - xmin)), max(0, count(ymax - ymin))
    if rows * columns > limit:
        raise ValueError(f"Mehr als {limit} Kacheln. Kleineres Gebiet oder größere Kachelgröße wählen.")
    margin = halo * resolution
    tiles = []
    for row in range(rows):
        top = ymax - row * side
        for column in range(columns):
            left = xmin + column * side
            core = (left, top - side, left + side, top)
            source = (core[0] - margin, core[1] - margin,
                      core[2] + margin, core[3] + margin)
            tiles.append(Tile(row, column, core, source))
    return tiles, rows, columns


def fitted_size(bounds, side, larger, limit=2500):
    """Nearest larger/smaller square size fitting at least one AOI dimension.

    Snap to width/n or height/n, then cover the other dimension without stretching.
    The caller anchors the result at the saved AOI corner.
    """
    xmin, ymin, xmax, ymax = bounds
    if not all(math.isfinite(v) for v in (*bounds, side)) or side <= 0 or xmax <= xmin or ymax <= ymin:
        raise ValueError("Gebiet und Kachelgröße müssen endlich und größer als null sein.")
    width, height = xmax - xmin, ymax - ymin
    candidates = sorted({length / n for length in (width, height)
                         for n in range(1, limit + 1)}, reverse=not larger)
    def count(length, size):
        ratio = length / size
        nearest = round(ratio)
        if math.isclose(ratio, nearest, rel_tol=0, abs_tol=1e-9):
            ratio = nearest
        return math.ceil(ratio)
    for size in candidates:
        if math.isclose(size, side, rel_tol=1e-9) or (size > side) != larger:
            continue
        columns, rows = count(width, size), count(height, size)
        if rows * columns <= limit:
            return size, rows, columns
    return None


def crop_window(tile, resolution, halo, pixels, bounds=None):
    """Return integer source-image crop and its exact pixel-aligned map bounds.

    Clip outwards to pixel boundaries; never distort the image to fit an AOI.
    """
    core = tile.core
    target = core if bounds is None else (
        max(core[0], bounds[0]), max(core[1], bounds[1]),
        min(core[2], bounds[2]), min(core[3], bounds[3]))
    if target[0] >= target[2] or target[1] >= target[3]:
        raise ValueError("Kachel liegt außerhalb des Gebiets.")
    size = pixels - 2 * halo
    def snap(v):
        return round(v) if math.isclose(v, round(v), abs_tol=1e-8) else v
    left = max(0, math.floor(snap((target[0] - core[0]) / resolution)))
    top = max(0, math.floor(snap((core[3] - target[3]) / resolution)))
    right = min(size, math.ceil(snap((target[2] - core[0]) / resolution)))
    bottom = min(size, math.ceil(snap((core[3] - target[1]) / resolution)))
    extent = (core[0] + left * resolution, core[3] - bottom * resolution,
              core[0] + right * resolution, core[3] - top * resolution)
    return (halo + left, halo + top, right - left, bottom - top), extent
