"""Link this checkout into an existing QGIS profile; never replaces a directory."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--profile', required=True, type=Path, help='QGIS profile directory (contains python/plugins)')
args = parser.parse_args()
source = Path(__file__).resolve().parents[1] / 'mapstyle_ai'
profile = args.profile.expanduser().resolve()
if not profile.is_dir():
    parser.error(f'Profile does not exist: {profile}')
plugins = profile / 'python' / 'plugins'
plugins.mkdir(parents=True, exist_ok=True)
target = plugins / source.name
if target.is_symlink() and target.resolve() == source:
    print(f'Already linked: {target}')
elif target.exists() or target.is_symlink():
    parser.error(f'Will not replace existing plugin: {target}')
else:
    target.symlink_to(source, target_is_directory=True)
    print(f'Linked: {target} -> {source}')
