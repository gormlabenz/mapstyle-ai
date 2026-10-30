# MapStyle AI

MapStyle AI transforms your QGIS layers into map backgrounds in a style of your choice by generating new tiles with AI. Describe the look you want or provide reference images. The generated tiles are georeferenced and loaded directly into your project.

## Requirements and installation

- QGIS 3.44 or later in the QGIS 3 series. QGIS 4 is not yet verified.
- Internet access, your own OpenAI or Google Gemini API key, and separately billed API access for generation. Chat subscriptions do not include API billing.
- No additional Python packages. QGIS, Qt and GDAL supply the runtime.

Download the plugin ZIP from the project's GitHub Releases. In QGIS, open **Plugins → Manage and Install Plugins → Install from ZIP**, select the ZIP, then enable **MapStyle AI**. Open it from **Plugins → MapStyle AI** or its toolbar icon.

This release is experimental. Once approved in the official QGIS repository, it can also be installed through the plugin manager with experimental plugins enabled.

## First map

1. Open a QGIS project and select the map layers under **Input layers**.
2. Choose **Current map view → Set area**, or **Layer extent**. Set the square tile size in metres or kilometres. All tiles initially start selected.
3. Choose a permanent **Save to** folder to keep results. Leaving it empty uses temporary storage that the operating system can delete.
4. Under **Provider settings**, choose a provider and model and enter its API key. **Save API keys** optionally stores the key in QGIS' encrypted authentication database.
5. Add style reference images and/or enter a style description, for example: “A simple watercolor map, cream paper, muted blue water, green parks, no labels.” At least one reference or instruction is required.
6. Generate the selected tiles. Result GeoTIFFs are loaded into the **Generated Tiles** subgroup. Save your QGIS project and retain the output folder.

Neighboring tiles are generated in dependency order. **Advanced settings → Tile transitions** offers fade overlap, exact pixel copying or untouched AI output. Even with neighbor context, seams and altered map features can occur.

## Data and costs

Generation sends rendered map inputs, your selected style references, spatially aligned overlap strips from completed neighbors and prompts to the selected provider. Only submit data you are authorized to share. There is no plugin-operated server or telemetry. Requests go to `api.openai.com` or `generativelanguage.googleapis.com` through QGIS networking.

Generation uses paid provider APIs. There are no automatic paid retries. Cancellation stops local work and queued requests; a request already received by a provider may still be charged. Provider prices, model access and retention policies are controlled by that provider.

API keys remain in panel memory unless saved through QGIS authentication storage. Generation folders contain input/reference images, prompts, manifests and results, but no credentials. Anyone receiving these folders can read their contents. Remove stored keys in QGIS Authentication settings when needed.

## Limitations and help

AI images are illustrations. Georeferencing positions their pixels; it does not guarantee geographic accuracy. Do not use generated features for surveying, navigation or measurements. The grid is intended for regional areas and is limited to 2,500 tiles. Resuming saved runs after restarting QGIS is not implemented. The interface is English. macOS/QGIS 3.44 has been checked; Windows and Linux need desktop verification.

If generation fails, check the API key, API billing and model access, then inspect the plugin **Log**. Try one tile first. Never post keys or confidential map inputs in a bug report.

- Documentation: https://github.com/gormlabenz/mapstyle-ai#readme
- Source: https://github.com/gormlabenz/mapstyle-ai
- Issues: https://github.com/gormlabenz/mapstyle-ai/issues
- Author: Gorm Labenz, gorm@labenz.io

Copyright (C) 2026 Gorm Labenz. Licensed under GNU GPL version 2 or, at your option, any later version. See LICENSE. No warranty. Provider services and generated-image rights are subject to their own terms.
