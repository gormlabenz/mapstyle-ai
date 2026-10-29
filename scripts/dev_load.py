"""Run from the QGIS console after defining MAPSTYLE_REPO; see README."""
from pathlib import Path
import sys
import qgis.utils

# Set by the one-line console command documented in README.
repo = str(Path(MAPSTYLE_REPO).resolve())
if repo not in sys.path:
    sys.path.insert(0, repo)
if 'mapstyle_ai' in qgis.utils.active_plugins:
    qgis.utils.unloadPlugin('mapstyle_ai')
else:
    old = qgis.utils.plugins.pop('mapstyle_ai', None)
    if old is not None:
        old.unload()
for name in list(sys.modules):
    if name == 'mapstyle_ai' or name.startswith('mapstyle_ai.'):
        del sys.modules[name]
if repo not in qgis.utils.plugin_paths:
    qgis.utils.plugin_paths.append(repo)
qgis.utils.updateAvailablePlugins()
if not qgis.utils.loadPlugin('mapstyle_ai'):
    raise RuntimeError('Could not load plugin; check the QGIS log.')
if not qgis.utils.startPlugin('mapstyle_ai'):
    raise RuntimeError('Could not start plugin; check the QGIS log.')
plugin = qgis.utils.plugins['mapstyle_ai']
plugin.show()
print('MapStyle AI loaded: ' + repo)
