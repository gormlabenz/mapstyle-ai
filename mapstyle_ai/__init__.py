# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Gorm Labenz

def classFactory(iface):
    from .plugin import MapStyleAIPlugin
    return MapStyleAIPlugin(iface)
