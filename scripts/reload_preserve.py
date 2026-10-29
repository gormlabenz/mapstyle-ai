"""Reload idle plugin, preserving UI selections and credentials in memory only.

Does not render map images or start generations. API keys are never printed/written.
"""
from pathlib import Path
import qgis.utils
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem

old_plugin = qgis.utils.plugins.get('mapstyle_ai')
old = old_plugin.dock if old_plugin else None
if old and old._busy:
    raise RuntimeError('Laufender Auftrag: erst abschließen oder selbst abbrechen, dann neu laden.')
state = None
if old:
    state = dict(
        provider=old.provider.currentText(), model=old.model.text(), models=dict(old._models),
        keys=dict(old._keys), reference=old.reference.text(), output=old.output.text(),
        quality=old.quality.currentText(), concurrency=old.concurrent.value(),
        pixels=old.pixels.currentData(), halo=old.halo.value(), border=old.border.currentIndex(),
        area=old.area.currentIndex(), area_layer=old.area_layer.currentData(), clip=old.clip.isChecked(),
        load_inputs=old.load_inputs.isChecked(), show_overlap=old.show_overlap.isChecked(),
        checked={old.layers.item(i).data(Qt.UserRole):old.layers.item(i).checkState() for i in range(old.layers.count())},
        tile_size=old.tile_size.value() if hasattr(old, 'tile_size') else 1,
        tile_unit=old.tile_unit.currentData() if hasattr(old, 'tile_unit') else 1000,
        grid_area=getattr(old, '_grid_area', None), fitted_size=getattr(old, '_fitted_size', None),
        fit_hint=old.fit_hint.text() if hasattr(old, 'fit_hint') else 'Raster übernommen. Mit den Buttons an das Gebiet anpassen.',
        plan=old.plan, tiles=list(old.tiles), selected={tile.name for tile in old.selected_tiles()},
        grid_layers=list(old.grid_layers), grid_group=getattr(old,'_previous_grid_group_name',None),
        summary=old.summary.text(), hint=old.plan_hint.text(),
        seams=dict(getattr(old,'seam_sources',{})),
        prompt=old.prompt.toPlainText(), exported=dict(old.exported), statuses=dict(old.statuses),
        results=[old.results.item(i).text() for i in range(old.results.count())],
    )
    state['keys'][state['provider']]=old.key.text()
exec((Path(MAPSTYLE_REPO)/'scripts/dev_load.py').read_text())
new = qgis.utils.plugins['mapstyle_ai'].dock
if state:
    new.provider.setCurrentText(state['provider'])
    new._models=state['models']
    new.model.setText(state['model'])
    new._keys=state['keys']
    new.key.setText(state['keys'].get(state['provider'],''))
    new.prompt.setPlainText(state['prompt'])
    new.reference.setText(state['reference'])
    new._reference_preview()
    new.output.setText(state['output'])
    new.quality.setCurrentText(state['quality'])
    new.concurrent.setValue(state['concurrency'])
    new.pixels.setCurrentIndex(new.pixels.findData(state['pixels']))
    new.halo.setValue(state['halo'])
    new.tile_unit.setCurrentIndex(new.tile_unit.findData(state['tile_unit']))
    new.tile_size.setValue(state['tile_size'])
    new.border.setCurrentIndex(state['border'])
    new.area.setCurrentIndex(state['area'])
    new.area_layer.setCurrentIndex(new.area_layer.findData(state['area_layer']))
    new.clip.setChecked(state['clip'])
    new.load_inputs.setChecked(state['load_inputs'])
    new.show_overlap.setChecked(state['show_overlap'])
    for i in range(new.layers.count()):
        item=new.layers.item(i)
        item.setCheckState(state['checked'].get(item.data(Qt.UserRole),Qt.Unchecked))
    if state['plan'] and state['tiles']:
        from mapstyle_ai.grid import column_name
        new.plan=state['plan']
        new._grid_area=state['grid_area']
        new._fitted_size=state['fitted_size']
        new.fit_hint.setText(state['fit_hint'])
        new.plan.setdefault('fitted', False)
        if 'tile_size_m' not in new.plan:
            new.plan['dirty'] = True
        new.tiles=state['tiles']
        new.seam_sources=state['seams']
        new.exported=state['exported']
        new.generated=set(new.seam_sources)
        rows=max(t.row for t in new.tiles)+1
        columns=max(t.column for t in new.tiles)+1
        new.table.setRowCount(rows)
        new.table.setColumnCount(columns)
        new.table.setHorizontalHeaderLabels([column_name(i) for i in range(columns)])
        new.table.setVerticalHeaderLabels([str(i+1) for i in range(rows)])
        for tile in new.tiles:
            item=QTableWidgetItem(f'{tile.name}\nOffen')
            item.setData(Qt.UserRole,tile.name)
            item.setTextAlignment(Qt.AlignCenter)
            new.table.setItem(tile.row,tile.column,item)
            if tile.name in state['statuses']:
                new._set_tile_status(tile,state['statuses'][tile.name])
        new.grid_layers=state['grid_layers']
        new._previous_grid_group_name=state['grid_group']
        new._make_grid_layers()
        new.summary.setText(state['summary'])
        new.plan_hint.setText(state['hint'])
        for tile in new.tiles:
            if tile.name in state['selected']:
                new.table.item(tile.row,tile.column).setSelected(True)
        new._selection_changed()
    new._update_fit_buttons()
    new._grid_timer.stop()
    if new.plan and 'tile_size_m' not in new.plan:
        new.plan_hint.setText('Neues metrisches Raster: bitte Gebiet übernehmen und Kachelgröße festlegen.')
    new.results.addItems(state['results'])
    new.tabs.setCurrentIndex(0 if new.plan and 'tile_size_m' not in new.plan else 1)
    new._tell('Version 0.4.1 bereit. Kacheln nur auswählen; Live-Export und Generierung starten erst auf deinen Klick.')
# Avoid keeping an extra snapshot of credentials in the console namespace.
state=None
print('Interface aktualisiert; keine Bildexporte oder KI-Aufrufe gestartet.')
