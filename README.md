# MapStyle AI · QGIS 3.44 · Version 0.6.1

A QGIS plugin for illustrated, georeferenced map backgrounds from existing map layers, style references and/or a style description. Uses QGIS, Qt and GDAL without additional Python packages.

## Interface

One scrollable English overview replaces the tabs. Input layers, Area and tiles, and Generation start open; Tiles, **Provider settings**, **Advanced settings** and **Log** start collapsed. Question marks provide hover hints and clickable help, including in collapsed headers. Form fields use separate label, input and fixed-width help columns, with all help icons on the right. The input-layer header also contains an icon to refresh project layers.

1. **Input layers:** select the raster and vector layers used as input for AI image generation. Their rendered map images are sent as spatial references to the selected provider (OpenAI or Google Gemini), which generates the output map layers using the style reference and prompt. QGIS drawing order and styling are preserved. Labels and selection highlights are excluded; embedded raster text remains visible.
2. **Area and tiles:** capture the **Current map view** with **Set area**, or select **Layer extent** to use a layer's bounding rectangle immediately. The area layer does not have to be an input layer. Set square tile size in m or km. The fitting buttons sit directly below the size input in the same form column.
3. **Tiles:** all tiles start selected in green. Click to select or deselect; drag for a range. Running and completed tiles show status. **Show tile outlines on map** controls both core outlines and any enabled context overlay.
4. **Save to:** empty means temporary storage. Optionally choose a folder for permanent generation files; the reset icon returns to temporary storage.
5. **Generation:** expand provider settings to set provider, model, quality, separate OpenAI/Gemini API keys and parallel jobs. Use **Save API keys** to retain entered keys securely across restarts. Add one or more style references and/or describe the desired style in the initially empty optional instructions field, then generate selected tiles. The cancel button appears while running.
6. **Advanced settings:** image pixels, edge treatment, context margin, crop-to-area, context overlay, optional live-input layers, input preview, manual result import and a collapsible activity log.

Temporary images are real files in the system temporary directory because QGIS layers need backing files. They remain available across panel reloads but may be removed by the system later. Choose a permanent output folder to retain results for future projects. Unsaved keys stay in panel memory only. Saved keys use QGIS encrypted authentication storage; normal settings contain only their authentication configuration IDs. Keys are excluded from project files, generation files and plugin logs.

## API keys and secure storage

**Provider settings** always shows separate **OpenAI API key** and **Google Gemini API key** password fields. Enter either or both, then click **Save API keys**. Empty fields leave saved keys unchanged. Saving a replacement updates the same provider configuration. Each provider shows whether its key is saved or is an unsaved session entry; successful saves clear the input field. **Show keys** reveals only entered text, never stored credentials.

An entered key takes precedence for this session. Without an entered key, generation uses that provider's saved QGIS configuration. Switching providers preserves both inputs independently. Closing the plugin clears session inputs and retains saved configurations. The Google field uses the existing Gemini API, not Vertex AI.

QGIS handles master-password creation and unlocking through its standard dialog. The plugin does not create its own master password or change QGIS wallet settings. To avoid entering the master password after every restart, use QGIS' optional **Wallet/Keyring** integration in **Settings → Options → Authentication**. Otherwise QGIS can ask for the master password the first time saved credentials are used in a session. No password prompt is opened just by opening the panel.

Saved entries are named **MapStyle AI - OpenAI** and **MapStyle AI - Google Gemini** and can be managed or removed in QGIS' Authentication settings. Cancelling unlocking or a missing configuration prevents requests using that saved key; there is no plaintext fallback. Details and verification: [QGIS credential integration](docs/credentials.md).

## Geographic grid

Tile size is the square **core** side length in meters, independent of map zoom, canvas dimensions, image pixels and context. A local UTM CRS is chosen from the area center; polar regions use UPS. The grid is intended for regional areas. Manual sizes align to multiples of their side length from CRS origin (0, 0). Identical sizes in the same CRS remain aligned when an area expands; changing UTM zones creates a new grid.

**Fewer tiles** and **More tiles** choose the nearest larger or smaller side length from area width/n and height/n, aligning the grid to the saved area's top-left corner. At least one dimension fits exactly; rectangular areas may retain overhang in the other dimension to preserve square tiles. For 600 × 600 m with an initial 400 m size, the proposals are 1 × 1 at 600 m or 2 × 2 at 300 m. Button labels show the proposed columns × rows; tooltips show size and count. Detailed overhang and grid information are written to the log.

Fitting enables full coverage. Image size, context, unit switching and navigation preserve the fitted size and origin. Manually editing tile size or capturing a new area resets fitting. A newly computed grid selects all tiles and resets neighbor associations; existing result files and project layers remain. The saved area lasts for the panel session. Grid size is limited to 2500 tiles.

Input images are 1024 or 2048 square pixels, including context. At 1024 pixels and 128 context pixels per side, the visible core is 768 pixels wide. With a 2 km core, resolution is 2000 / 768 ≈ 2.604 m/pixel. This describes georeferencing, not guaranteed AI detail. Rendering uses 96 DPI and north-up orientation. Optional cropping trims edge tiles to the area, rounding outward by less than one pixel without stretching images.

## Neighbor continuation and results

The first tile is rendered from selected map layers immediately before generation. Right and lower neighbors wait for their required predecessors, then render a fresh input that includes their completed neighbors. For a 2 × 2 grid, A1 precedes B1/A2, then B2. Parallel jobs are a maximum; dependency readiness determines actual concurrency. Disconnected selections with multiple starting points are rejected before provider requests.

The internal English base prompt preserves map arrangement, scale, orientation and crop; it is not shown in the interface and imposes no particular colors or illustration medium. At least one style reference or non-whitespace user instruction is required. References alone, instructions alone, and both together are supported.

**Add images…** accepts multiple files and can be used repeatedly. Every preview has a stable **Reference 1**, **Reference 2**, etc. label and a Remove button. Deleting a reference never renumbers the remaining images; adding an image allocates a fresh number. Duplicate file paths are ignored. Instructions may refer to either `Reference 1` or `Referenz 1`, e.g. “Colors like Reference 1, linework like Reference 3.” Explicit mentions of absent reference numbers are rejected before generation. Without specific instructions, references contribute their shared visual characteristics with no automatic first-image priority.

The prompt explicitly maps each reference label to its transmitted image position. Image 1 is always the geographic input, followed by all style references in display order, then completed neighbors. Neighbor image numbers are computed from the actual reference count. Starting tiles also work without any style image. Finished connection strips take priority over style requests for continuity; the selected overlap treatment still controls local result processing.

There is no fixed reference selection cap in the UI. Provider input-image limits include the map input and the maximum number of required neighbors in the selected run and are checked before starting. OpenAI allows 16 total images; supported current Gemini models allow 14, with Gemini 3 Pro Image allowing up to 3 style references. The existing 20 MB inline Gemini request-size guard runs before each HTTP request. Unknown custom model limits remain subject to the provider response. No images are silently omitted. See [OpenAI image edits](https://developers.openai.com/api/reference/resources/images/methods/edit) and [Google image generation](https://ai.google.dev/gemini-api/docs/image-generation).

**Advanced settings → Tile transitions** selects how each new tile joins its finished neighbors:

- **Fade overlap** (default): gradually blends existing neighbor pixels into the new image.
- **Copy overlap exactly**: restores neighbor pixels exactly in the overlap; the inner edge may remain visible.
- **Keep AI output**: uses the generated image without copying or fading pixels; visible seams may remain.

A short general explanation and an explanation for the selected option appear in the settings, with additional question-mark help. All modes still supply neighbor context to the AI and crop the outer context margin. The selected mode is recorded per run in the manifest and controls both the visible result and the anchor for subsequent tiles. One result variant is saved and loaded per tile; the untouched provider response is retained separately. Existing results are not reprocessed when the setting changes. Copying protects old pixels exactly; blending softens the transition. Visual continuity in newly generated areas still depends on the provider. Georeferencing cannot correct features moved by AI.

Each generation creates a separate folder containing `references/reference-N.png`, copied neighbor anchors, live input PNGs/GeoTIFFs, protection masks, original responses, result variants and `manifest.json`. The manifest records grid geometry, dependencies, user instructions, reference labels and files, base prompt version, resolved per-tile prompts, image settings, request timing and per-tile states without credentials. Setting a new area creates a new **MapStyle · YYYY-MM-DD-HH:MM:SS.ffffff** project group, with three subgroups: **Generated Tiles**, **Tile Grid** and **Input Layers**. Generated layers use only tile names such as A1 and B2; a new result replaces the previous layer for that tile within the active group. Generating selected tiles fills the group created for that area. Setting another area preserves all previous groups, grids, inputs and results, even for the same extent. Adjusting tile size or fitting rebuilds only the active grid. Loading generated results is automatic. **Show Input Layers** is disabled by default. When enabled, they are added to **Input Layers**, which starts hidden and collapsed. External full-size results can be imported for a single selected tile that already has an exported input.

There are no automatic paid retries. Authentication, quota and rate-limit errors stop further queued requests. Cancel stops local work and pending requests; requests already received by the provider may still incur charges. Each image request has a 30-minute timeout. Resuming old manifests after restarting QGIS is not implemented.

## Providers

Google Gemini and OpenAI image editing are supported. Model IDs are editable and availability depends on the API account. Web subscriptions do not replace API billing. Official HTTPS endpoints use QGIS networking with manual redirects; TLS checks remain enabled. OpenAI quality is selectable. Requests contain the map input, all selected style references, neighbor images where applicable and the prompt.

## Development and installation

- `mapstyle_ai/`: plugin source.
- `scripts/`, `tests/`: development helpers and offline checks.
- `docs/`: project documentation.
- `local/`, `ahrensburg/`, `examples/mapstyle-ai-test/`: ignored personal maps and manual experiments.
- `dist/`, `test-output/`: ignored packages and test artifacts.

```sh
python3 scripts/install.py --profile "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default"
python3 scripts/package.py
python3 -m unittest discover -s tests -v
```

The install script refuses to overwrite an existing installation. The installable ZIP is `dist/mapstyle_ai-0.6.1.zip`.

Offline QGIS checks on this Mac:

```sh
/Applications/QGIS.app/Contents/MacOS/python scripts/qgis_credentials_check.py
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python scripts/qgis_metric_grid_check.py
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python scripts/qgis_compact_ui_check.py
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python scripts/qgis_reference_check.py
QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python scripts/qgis_result_variants_check.py
```

These isolated checks use synthetic data and make no provider calls. They cover metric geometry and export, actual overview interactions, area modes, fitting, selection, overlay visibility, help, collapsed defaults, temporary/custom output, simulated generation launch/cancel and all three transition modes. A real paid generation is left to the user. Legacy smoke scripts for the original workflow are not current verification paths.

To reload an idle plugin in the QGIS console while retaining selections and credentials in memory:

```python
from pathlib import Path
MAPSTYLE_REPO = '/Users/gormlabenz/Documents/labenz-io/000_Repositorys/mapstyle-ai'
exec((Path(MAPSTYLE_REPO) / 'scripts/reload_preserve.py').read_text())
```

Reloading starts no rendering or provider requests. It preserves temporary storage paths, tile selection, source choices, existing result associations and user-edited prompts. The previous single reference migrates to Reference 1; an unchanged previous default prompt migrates to an empty user field. Existing custom text is retained. Reloading preserves reference numbers and the next available number.
