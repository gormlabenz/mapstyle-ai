"""Reload an idle plugin, preserving selections and credentials in memory only.

No rendering, provider calls or credentials written to disk.
"""
from pathlib import Path
import qgis.utils
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QTableWidgetItem

old_plugin = qgis.utils.plugins.get('mapstyle_ai')
old = old_plugin.dock if old_plugin else None
if old and old._busy:
    raise RuntimeError('Finish or cancel the active job before reloading.')
state = None
if old:
    from mapstyle_ai.seams import DEFAULT_PROMPT as old_default
    state = dict(
        provider=old.provider.currentText(), model=old.model.text(), models=dict(old._models),
        keys={provider: field.text() for provider, field in old.key_inputs.items()} if hasattr(old, 'key_inputs') else dict(old._keys),
        references=([dict(reference) for reference in old.references] if hasattr(old, 'references')
                    else ([dict(id=1, path=old.reference.text())] if old.reference.text().strip() else [])),
        next_reference_id=getattr(old, '_next_reference_id', 1), output=old.output.text(),
        temporary_output=getattr(old, '_temporary_output', None),
        overlap_mode=old.overlap_mode.currentData() if hasattr(old, 'overlap_mode') else 'blend',
        quality=old.quality.currentText(), concurrency=old.concurrent.value(),
        pixels=old.pixels.currentData(), halo=old.halo.value(), border=old.border.currentIndex(),
        area=old.area.currentIndex(), area_layer=old.area_layer.currentData(), clip=old.clip.isChecked(),
        load_inputs=old.load_inputs.isChecked(), show_overlap=old.show_overlap.isChecked(),
        show_frames=old.show_frames.isChecked() if hasattr(old, 'show_frames') else True,
        checked={old.layers.item(i).data(Qt.UserRole):old.layers.item(i).checkState() for i in range(old.layers.count())},
        tile_size=old.tile_size.value() if hasattr(old, 'tile_size') else 1,
        tile_unit=old.tile_unit.currentData() if hasattr(old, 'tile_unit') else 1000,
        grid_area=getattr(old, '_grid_area', None), fitted_size=getattr(old, '_fitted_size', None),
        plan=old.plan, tiles=list(old.tiles), selected={tile.name for tile in old.selected_tiles()},
        grid_layers=list(old.grid_layers), grid_group=getattr(old,'_previous_grid_group_name',None),
        run_group=old.run_group,
        seams=dict(getattr(old,'seam_sources',{})),
        prompt=None if not hasattr(old, 'references') and old.prompt.toPlainText() == old_default else old.prompt.toPlainText(),
        exported=dict(old.exported), statuses=dict(old.statuses),
        generation_records=getattr(old, '_generation_records', {}),
    )
    state['next_reference_id'] = max(state['next_reference_id'],
                                     max((r['id'] for r in state['references']), default=0) + 1)
    if not hasattr(old, 'key_inputs'):
        state['keys'][state['provider']] = old.key.text()
exec((Path(MAPSTYLE_REPO)/'scripts/dev_load.py').read_text())
new = qgis.utils.plugins['mapstyle_ai'].dock
if state:
    new.provider.setCurrentText(state['provider'])
    new._models = state['models']
    new.model.setText(state['model'])
    for provider, field in new.key_inputs.items():
        field.setText(state['keys'].get(provider, ''))
    if state['prompt'] is not None:
        new.prompt.setPlainText(state['prompt'])
    new.references = state['references']
    new._next_reference_id = state['next_reference_id']
    new._reference_preview()
    new.output.setText(state['output'])
    new._temporary_output = state['temporary_output']
    new.overlap_mode.setCurrentIndex(new.overlap_mode.findData(state['overlap_mode']))
    new.quality.setCurrentText(state['quality'])
    new.concurrent.setValue(state['concurrency'])
    new.pixels.setCurrentIndex(new.pixels.findData(state['pixels']))
    new.halo.setValue(state['halo'])
    new.tile_unit.setCurrentIndex(new.tile_unit.findData(state['tile_unit']))
    new.tile_size.setValue(state['tile_size'])
    new.border.setCurrentIndex(state['border'])
    new.area.blockSignals(True)
    new.area_layer.blockSignals(True)
    new.area.setCurrentIndex(state['area'])
    new.area_layer.setCurrentIndex(new.area_layer.findData(state['area_layer']))
    new.area.blockSignals(False)
    new.area_layer.blockSignals(False)
    new._area_visibility()
    new.clip.setChecked(state['clip'])
    new.load_inputs.setChecked(state['load_inputs'])
    new.show_overlap.setChecked(state['show_overlap'])
    new.show_frames.setChecked(state['show_frames'])
    new.layers.blockSignals(True)
    for i in range(new.layers.count()):
        item = new.layers.item(i)
        item.setCheckState(state['checked'].get(item.data(Qt.UserRole),Qt.Unchecked))
    new.layers.blockSignals(False)
    if state['plan'] and state['tiles']:
        from mapstyle_ai.grid import column_name
        new.plan = state['plan']
        new._grid_area = state['grid_area']
        new._fitted_size = state['fitted_size']
        new.plan.setdefault('fitted', False)
        if 'tile_size_m' not in new.plan:
            new.plan['dirty'] = True
        new.tiles = state['tiles']
        new.seam_sources = state['seams']
        new.exported = state['exported']
        new.generated = set(new.seam_sources)
        new._generation_records = state['generation_records']
        rows = max(t.row for t in new.tiles) + 1
        columns = max(t.column for t in new.tiles) + 1
        new.table.setRowCount(rows)
        new.table.setColumnCount(columns)
        new.table.setHorizontalHeaderLabels([column_name(i) for i in range(columns)])
        new.table.setVerticalHeaderLabels([str(i+1) for i in range(rows)])
        status_names = {'Offen':'Ready', 'Fehler':'Failed', 'Fertig':'Done', 'Ergebnis':'Result',
                        'Exportiert':'Exported', 'Wartet':'Queued', 'Abgebrochen':'Cancelled'}
        for tile in new.tiles:
            item = QTableWidgetItem(tile.name)
            item.setData(Qt.UserRole, tile.name)
            item.setTextAlignment(Qt.AlignCenter)
            item.setToolTip(f'Core: {tile.core}\nInput including context: {tile.source}')
            new.table.setItem(tile.row, tile.column, item)
            if tile.name in state['statuses']:
                status = state['statuses'][tile.name]
                new._set_tile_status(tile, status_names.get(status, status))
        new.grid_layers = state['grid_layers']
        new.run_group = state['run_group']
        new._make_grid_layers()
        legacy_grid = new.project.layerTreeRoot().findGroup(state['grid_group'] or '')
        if legacy_grid is not None and not legacy_grid.children():
            legacy_grid.parent().removeChildNode(legacy_grid)
        for tile in new.tiles:
            if tile.name in state['selected']:
                new.table.item(tile.row,tile.column).setSelected(True)
        new._selection_changed()
    new._update_fit_buttons()
    new._grid_timer.stop()
    new._tell('MapStyle AI 0.6.1 ready. No image exports or API requests started.')
state = None
print('Interface updated. No image exports or API requests started.')
