from pathlib import Path
from PIL import Image
import json

root = Path(__file__).parent
geography = json.loads((root / 'geography.json').read_text())
tiles = geography['tiles']
assert len(tiles) == 20
assert {t['tile'] for t in tiles} == {f'{c}{r}' for c in 'ABCDE' for r in range(1, 5)}
canvas = Image.new('RGB', (5120, 4096))
for tile in tiles:
    path = root / 'tiles' / (tile['tile'] + '.png')
    with Image.open(path) as image:
        assert image.width == image.height, path
        image = image.convert('RGB').resize((1024, 1024), Image.Resampling.LANCZOS)
        canvas.paste(image, ((tile['column'] - 1) * 1024, (tile['row'] - 1) * 1024))
canvas.save(root / 'mapstyle-ai-hero.png', optimize=True)
canvas.resize((2560, 2048), Image.Resampling.LANCZOS).save(root / 'mapstyle-ai-hero-web.png', optimize=True)
canvas.resize((1600, 1280), Image.Resampling.LANCZOS).save(root / 'preview.jpg', quality=92)
for name in ['mapstyle-ai-hero.png', 'mapstyle-ai-hero-web.png']:
    path = root / name
    with Image.open(path) as check:
        check.verify()
    print(name, path.stat().st_size)
