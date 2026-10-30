# Changelog

## 0.6.2 — 2026-10-30

- Fix repeated upper/left neighbor geography in generated tiles: completed neighbors now provide only their spatially aligned overlap strips in Image 1, without sending full neighbor images alongside the target.
- Clarify target geography and connection strips in continuation prompts; record prompt version 2 and aligned-input context in manifests.
- Check provider image limits against the images actually sent.
- Add offline regression coverage for distinct 2 × 2 tile geography through rendering, requests and cropped results for both providers.

## 0.6.1 — 2026-10-29

Initial public experimental release.

- Metric regional tile grids, area capture and tile fitting.
- OpenAI and Google Gemini generation with model-specific output sizes.
- Multiple numbered style references and optional instructions.
- Neighbor-aware scheduling and fade, exact-copy or AI-only transitions.
- Georeferenced GeoTIFF output, input previews and generation manifests.
- Optional encrypted API-key storage through QGIS authentication.
- English interface with contextual help, cancellation and activity log.

Prepared for QGIS 3.44–3.99. Paid provider generation and native Windows/Linux behavior are not covered by offline release checks.
