"""Pure dependency and pixel geometry for north/left tile continuation."""
import math


DEFAULT_PROMPT = """Übernimm die räumliche Anordnung aus Bild 1. Erhalte Positionen, Grundformen und Verbindungen von Gebäuden, Straßen, Wegen, Gewässern und Flächen. Gestalte die Details passend zum Illustrationsstil, ohne zusätzliche Gebäude oder Wege zu erfinden. Behalte Kartenorientierung, Maßstab und Bildausschnitt bei; die Darstellung der Objekte folgt dem Illustrationsstil. Bild 2 dient nur als Stilreferenz, nicht als geografische Vorlage. Ohne Beschriftungen, Legende oder Rahmen.
Gib das vollständige Bild mit genau {tile_px} × {tile_px} Pixeln zurück."""


def dependencies(tiles, existing=()):
    positions = {(t.row, t.column): t.name for t in tiles}
    positions.update({(t['row'], t['column']): name for name, t in dict(existing).items()
                      if (t['row'], t['column']) not in positions})
    result = {}
    for tile in tiles:
        result[tile.name] = [positions[pos] for pos in ((tile.row - 1, tile.column),
                                                       (tile.row, tile.column - 1)) if pos in positions]
    if sum(not parents for parents in result.values()) > 1:
        raise ValueError('Die Auswahl hat mehrere getrennte Startpunkte. Bitte ein zusammenhängendes Gebiet mit Anschlüssen von oben oder links wählen.')
    return result


def ready_tiles(pending, parents, completed):
    return [name for name in pending if all(parent in completed for parent in parents[name])]


def blocked_tiles(pending, parents, failed):
    blocked = set(failed)
    while True:
        children = {name for name in pending if any(parent in blocked for parent in parents[name])}
        if children <= blocked:
            return children
        blocked |= children


def overlap_windows(target, neighbor, resolution):
    """Two equal-resolution, pixel-aligned extents -> target/source integer rectangles."""
    left, bottom = max(target[0], neighbor[0]), max(target[1], neighbor[1])
    right, top = min(target[2], neighbor[2]), min(target[3], neighbor[3])
    if right <= left or top <= bottom:
        return None
    def pixel(value):
        exact = value / resolution
        rounded = round(exact)
        if not math.isclose(exact, rounded, rel_tol=0, abs_tol=1e-5):
            raise ValueError('Nachbarkachel passt nicht exakt auf das Pixelraster.')
        return rounded
    width, height = pixel(right-left), pixel(top-bottom)
    return ((pixel(left-target[0]), pixel(target[3]-top), width, height),
            (pixel(left-neighbor[0]), pixel(neighbor[3]-top), width, height))


def continuation_prompt(prompt, neighbors, windows):
    if not neighbors:
        return 'Gestalte die gesamte Kartengrundlage in Bild 1 im Illustrationsstil von Bild 2.\n' + prompt
    sides = ['links' if h > w else 'oben' for x, y, w, h in windows]
    location = ' und '.join(dict.fromkeys(sides))
    lines = [f'Führe die vorhandene Illustration {location} in Bild 1 über die gesamte übrige Kartengrundlage fort.',
             'Übernimm alle Pixel in den bereits illustrierten Streifen exakt und unverändert aus Bild 1. '
             'Bearbeite nur den Bereich außerhalb dieser Streifen. '
             'Die Streifen geben den fortzusetzenden Stil vor und haben Vorrang vor Bild 2. '
             'Führe Linien ohne Unterbrechung oder Versatz weiter und schließe Farben und Texturen '
             'ohne sichtbaren Wechsel an, sodass das Ergebnis wie eine durchgehende Illustration wirkt.',
             prompt,
             'Die weiteren Nachbarbilder liefern zusätzlichen Kontext; die genaue Anschlussvorgabe '
             'sind die illustrierten Streifen in Bild 1.']
    for index, (name, rectangle, side) in enumerate(zip(neighbors, windows, sides), 3):
        x, y, w, h = rectangle
        lines.append(f'Bild {index}: Nachbarkachel {name}, Anschluss {side}. '
                     f'Unveränderter Streifen in Bild 1: x={x}, y={y}, Breite={w}, Höhe={h} Pixel '
                     '(Ursprung oben links).')
    return '\n'.join(lines)
