"""Offline QGIS check of all result variants, using synthetic pixels only."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace, MethodType
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qgis.core import QgsApplication, QgsProject, QgsCoordinateReferenceSystem
from qgis.PyQt.QtGui import QImage, QColor
from qgis.PyQt.QtWidgets import QListWidget
from mapstyle_ai.panel import BackgroundPanel
from mapstyle_ai.grid import build_grid
from mapstyle_ai.seams import continuation_prompt
from mapstyle_ai.live_export import blend_regions
app = QgsApplication([], False)
app.initQgis()
with TemporaryDirectory() as directory:
    root = Path(directory)
    (root/'results').mkdir()
    tile = build_grid((0, 0, 12, 12), 1, pixels=16, halo=2)[0][0]
    original = QImage(16, 16, QImage.Format_ARGB32); original.fill(QColor('red'))
    response = QImage(16, 16, QImage.Format_ARGB32); response.fill(QColor('blue'))
    original.save(str(root/'input.png')); response.save(str(root/'response.png'))
    manifest = dict(input_pixels=16, resolution=1, halo_pixels=2, bounds=(0,0,12,12),
                    clip_results=True, crs_wkt=QgsCoordinateReferenceSystem('EPSG:3857').toWkt(),
                    tiles=[dict(id=tile.name)])
    record = dict(tile=tile, manifest=manifest, directory=str(root))
    panel = SimpleNamespace(project=QgsProject(), generation_directory=root,
                            _generation_records={tile.name:record}, seam_sources={},
                            results=QListWidget(), _set_tile_status=lambda *a:None, _tell=lambda *a:None)
    for method in ('_store_result', '_add_raster'):
        setattr(panel, method, MethodType(getattr(BackgroundPanel, method), panel))
    variants = {'original':'AI original', 'copy':'Overlap restored', 'blend':'Blended overlap'}
    for mode, variant in variants.items():
        for protected in ([(0,0,4,16)], []):
            manifest['overlap_mode'] = mode
            manifest['tiles'][0]['results'] = []
            anchor = BackgroundPanel._accept_seam_result(panel, tile.name, str(root/'response.png'),
                                                         dict(png=str(root/'input.png'), protected=protected))
            entries = manifest['tiles'][0]['results']
            assert len(entries) == 1 and entries[0]['variant'] == variant
            result = QImage(str(root/entries[0]['full_image']))
            assert result.pixelColor(2,8) == QColor('red' if protected and mode != 'original' else 'blue')
            assert result.pixelColor(3,8) == QColor('red' if protected and mode == 'copy' else 'blue')
            assert result.pixelColor(8,8) == QColor('blue')
            assert QImage(str(root/entries[0]['file'].replace('.tif','-core.png'))).width() == 12
            assert anchor['png'] == str(root/entries[0]['full_image'])
            assert panel.seam_sources[tile.name] == anchor
            groups = panel.project.layerTreeRoot().children()[0].children()
            assert {g.name():g.itemVisibilityChecked() for g in groups} == {'Results · ' + variant: True}
            stored = json.loads((root/'manifest.json').read_text())
            assert stored['overlap_mode'] == mode
            assert stored['tiles'][0]['results'][0]['variant'] == variant
            # The response file remains unchanged even when copy/fade is selected.
            assert QImage(str(root/'response.png')).pixelColor(2,8) == QColor('blue')
            panel.project.clear()
    assert 'protected' not in continuation_prompt('Style', [], [])
    assert 'every pixel in the already illustrated strips exactly and unchanged' in continuation_prompt('Style', ['A1'], [(0,0,4,16)])
    # Full-width ramp: exact old at the visible edge, exact new at its end.
    for rectangles in ([(0,0,8,16)], [(0,0,16,8)], [(0,0,16,8), (0,0,8,16)]):
        faded = blend_regions(response, original, rectangles, 2)
        assert faded.pixelColor(2,2) == QColor('red')
        assert faded.pixelColor(7,7) == QColor('blue')
        assert faded.pixelColor(12,12) == QColor('blue')
        left = rectangles[-1][2] == 8
        colors = [faded.pixelColor(i,12) if left else faded.pixelColor(12,i) for i in range(2,8)]
        assert all(a.red() >= b.red() and a.blue() <= b.blue() for a,b in zip(colors,colors[1:]))
        middle = colors[2]
        assert 140 <= middle.red() <= 165 and 90 <= middle.blue() <= 115
    assert blend_regions(response, original, [], 2) == response
    panel.project.clear()
print('PASS: full-width linear fade, top/left/corner, three transition modes, preserved raw response, exact copy, unchanged start tile, cropped outputs, visibility, selected neighbor anchor and per-run mode')
