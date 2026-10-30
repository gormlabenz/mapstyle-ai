# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

"""Pure dependency and pixel geometry for north/left tile continuation."""
import math


from .prompts import DEFAULT_PROMPT


def dependencies(tiles, existing=()):
    positions = {(t.row, t.column): t.name for t in tiles}
    positions.update({(t['row'], t['column']): name for name, t in dict(existing).items()
                      if (t['row'], t['column']) not in positions})
    result = {}
    for tile in tiles:
        result[tile.name] = [positions[pos] for pos in ((tile.row - 1, tile.column),
                                                       (tile.row, tile.column - 1)) if pos in positions]
    if sum(not parents for parents in result.values()) > 1:
        raise ValueError('Selection has multiple disconnected starting points. Select a connected area with neighbors above or to the left.')
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
            raise ValueError('Neighbor tile does not align exactly with the pixel grid.')
        return rounded
    width, height = pixel(right-left), pixel(top-bottom)
    return ((pixel(left-target[0]), pixel(target[3]-top), width, height),
            (pixel(left-neighbor[0]), pixel(neighbor[3]-top), width, height))


def continuation_prompt(prompt, neighbors, windows):
    if not neighbors:
        return 'Illustrate the entire base map in Image 1 using the style instructions below.\n' + prompt
    sides = ['left' if h > w else 'top' for x, y, w, h in windows]
    location = ' and '.join(dict.fromkeys(sides))
    lines = [f'Continue the existing illustration at the {location} of Image 1 across the remaining base map.',
             'Copy every pixel in the already illustrated strips exactly and unchanged from Image 1. '
             'Edit only the area outside these strips. '
             'The strips define the style to continue and take priority over style references and user styling instructions. '
             'Continue lines without gaps or offsets and match colors and textures '
             'without visible transitions, so the result reads as one continuous illustration.',
             'Image 1 is the complete target tile. Its illustrated strips are already positioned '
             'at the correct coordinates. Illustrate the remaining base map in place; '
             'do not repeat, shift or expand the neighboring geography into that area.',
             prompt]
    for name, rectangle, side in zip(neighbors, windows, sides):
        x, y, w, h = rectangle
        lines.append(f'Connection from neighbor tile {name} at {side}, already embedded in Image 1. '
                     f'Unchanged strip in Image 1: x={x}, y={y}, width={w}, height={h} pixels '
                     '(origin at top left).')
    return '\n'.join(lines)
