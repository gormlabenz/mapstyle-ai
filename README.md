# MapStyle AI · QGIS 3.44 · Version 0.4.1

Python-Plugin für KI-Kartenhintergründe aus vorhandenen QGIS-Layern und einem austauschbaren Stilreferenzbild. Nutzt QGIS, Qt und GDAL; keine zusätzlichen Python-Pakete oder Webserver nötig.

## Projektordner und Beispieldaten

- `mapstyle_ai/`: QGIS-Plugin.
- `scripts/` und `tests/`: Hilfsskripte und automatisierte Tests.
- `docs/`: Platz für die Projektdokumentation.
- `examples/`: Platz für kleine, teilbare Beispiele.
- `local/`: persönliche Versuche und Arbeitsdaten; durch Git ausgeschlossen.
- `dist/`: gebaute Plugin-Pakete; durch Git ausgeschlossen.
- `ahrensburg/`: vorhandene lokale Kartengrundlagen und QGIS-Projekte für Ahrensburg; durch Git ausgeschlossen. Der Ordner bleibt zur Erhaltung vorhandener Dateipfade an seinem bisherigen Ort.
- `examples/mapstyle-ai-test/`: lokales, durch Git ausgeschlossenes manuelles Testprojekt mit Referenzbildern, Generierungsläufen und Screencast. Einstieg: [QGIS-Projekt](examples/mapstyle-ai-test/mapstyle-ai-test.qgz), [Ordnerübersicht](examples/mapstyle-ai-test/README.md).
- `test-output/`: Ergebnisse der automatisierten Prüfungen; durch Git ausgeschlossen.

## Ablauf

1. Grundlagenlayer wählen, auf das Gebiet zoomen und unter **1 · Grundlagen** das Raster erstellen. Gebiet: sichtbarer Ausschnitt oder rechteckige Ausdehnung eines Layers. Kartenauflösung wird eingefroren, Kartendrehung muss 0° sein.
2. Unter **2 · Kacheln** die gewünschten Zellen auswählen, zum Beispiel **2 × 2 links oben**, und einen existierenden Ausgabeordner wählen. **Es werden noch keine Eingabebilder exportiert.**
3. Unter **3 · Generierung** Anbieter, Modell-ID, API-Key, Referenzbild und Parallelität einstellen. Den bearbeitbaren Prompt prüfen und **Ausgewählte Kacheln generieren** starten.
4. Die erste Kachel, beispielsweise A1, wird unmittelbar aus den Grundlagen gerendert und generiert. Das fertige Bild wird räumlich zugeordnet und in QGIS eingesetzt.
5. Erst danach werden B1 und A2 freigegeben. Ihre Eingabebilder werden jetzt frisch aus QGIS gerendert, einschließlich der vollständigen fertigen Nachbarbilder in den Überlappungsbereichen. Diese Nachbarn werden zusätzlich als Bildreferenzen übertragen.
6. B2 wartet auf B1 und A2. Das setzt sich durch das Raster fort. Die Parallelität ist ein Maximum; tatsächlich gleichzeitig starten nur Kacheln, deren Nachbarn fertig sind.

Der Standardprompt besteht aus einem gemeinsamen, bearbeitbaren Grundteil und einer automatisch vorangestellten Aufgabe: Die Startkachel wird vollständig im Stil von Bild 2 gestaltet; Folgekacheln führen die illustrierten Streifen links, oben oder an beiden Seiten fort. Deren Stil hat Vorrang vor der allgemeinen Referenz. Bild 1 bestimmt die räumliche Anordnung; stilistische Vereinfachungen sind erlaubt, Orientierung, Maßstab und Ausschnitt bleiben erhalten. Nur bei vorhandenen Nachbarn werden Erhalt und Anschluss der Streifen beschrieben und ihre Pixelrechtecke genannt. Bild 1 ist das zusammengesetzte Live-Eingabebild, Bild 2 die Stilreferenz, weitere Bilder liefern Nachbarkontext. Interne Kern- und Kontextsaumgrößen stehen nicht mehr im Standardprompt.

**Drei Ergebnisvarianten:** Jede generierte Kachel wird als „KI-Original“, „Überlappung zurückkopiert“ und „Überlappung weich überblendet“ georeferenziert gespeichert. Die getrennten Ergebnisgruppen in QGIS erlauben den Vergleich; standardmäßig ist nur die weiche Variante sichtbar. Dabei bleibt der abgeschnittene äußere Kontextsaum vollständig vom alten Bild erhalten. Ab dem sichtbaren Kachelrand nimmt dessen Anteil linear von 100 % bis auf 0 % am inneren Ende der Überlappung ab. Bei Anschlüssen von oben und links werden die Deckkräfte in der Ecke kombiniert. Weitere Kacheln verwenden die weich überblendete Variante als Anschluss. Bei der Startkachel sind alle drei Varianten identisch.

**Zusätzlicher technischer Schutz:** Nach der Antwort werden die bereits fertigen Überlappungsbereiche exakt aus dem Eingabebild zurückkopiert. Die unveränderte Übernahme dieser Pixel hängt damit nicht vom Modell ab. Das neue Bild einschließlich Kontext wird für weitere Nachbarn aufbewahrt. Sichtbar wird der zugeschnittene Kern.

Die gesamte Kartenkomposition wird über QGIS gerendert; die Nachbarbilder werden zusätzlich pixelgenau eingesetzt, um Resamplingunterschiede im geschützten Bereich auszuschließen. Dabei sind ausschließlich die gewählten Grundlagen und die vorgesehenen Nachbarn beteiligt. Alte Ergebnisgruppen, Rasterhilfen, Beschriftungen und Auswahlmarkierungen gelangen nicht versehentlich in die Eingaben.

## Auswahl und Wiederholungen

- „Alle“, „Keine“, „2 × 2 links oben“ und „Offene“ unterstützen die Auswahl im Tabellenraster. Ziehen/Umschalt-Klick markiert Bereiche, Cmd/Strg-Klick ergänzt einzelne Zellen.
- Im ersten Durchlauf ist eine Auswahl mit einem gemeinsamen Startpunkt nötig. Getrennte Inseln ohne Anschlüsse von oben/links werden vor dem Start abgewiesen.
- Im selben Panel können nach einem kleinen Test weitere Kacheln ausgewählt werden; fertige Nachbarn aus vorigen Durchläufen werden weitergereicht. „Offene“ wählt noch nicht erfolgreich erzeugte Kacheln aus.
- Eine erneut ausgewählte Kachel wird neu generiert. Ihre alte Version dient in diesem Durchlauf nicht als fertiger Anschluss; abhängige Kacheln warten auf die neue Version.
- Bei Fehlern eines benötigten Nachbarn werden abhängige Kacheln ohne API-Aufruf als nicht gestartet markiert. Unabhängige bereits freigegebene Zweige dürfen abschließen.
- Ein neu erstelltes Raster setzt die Zuordnung der Anschlussbilder im Panel zurück. Alte Dateien und geladene Ergebnislayer bleiben erhalten. Das gilt ebenfalls beim Wechsel von Version 0.2 zu 0.3: frühere unabhängig erzeugte Kacheln werden nicht automatisch als neue Stilanker verwendet.

## Grundlagen und Überlappung

Vorhandene lokale Raster/GeoTIFFs, Shapefiles, GeoPackages oder über QGIS erreichbare Netzquellen können gemeinsam gewählt werden. Reihenfolge und Darstellung stammen aus QGIS. Der Gebiets-Layer legt nur die rechteckige Gesamtausdehnung fest und muss nicht selbst in den Eingaben vorkommen.

Eingabegröße: 1024 oder 2048 Pixel. Kontextsaum: einstellbar, für die Weitergabe größer als null. Bei 1024 Pixeln und 128 Pixeln Kontext pro Seite ist der Kern 768 Pixel breit. Nachbareingaben überlappen um 256 Pixel. Es wird das vollständige vorherige Bild benutzt, sodass die geschützte Anschlusszone auch in den neuen Kern hineinreicht.

Randbehandlung: vollständige Abdeckung oder nur vollständig enthaltene Kerne. Der Kontext darf über das gewählte Gebiet hinausgehen. Ergebniszuschnitt am Gebiet ist optional; nach außen wird auf Pixelgrenzen gerundet, ohne die Bilder zu strecken. Der Rand kann dadurch um weniger als einen Pixel überschritten werden.

Kern- und Kontextflächen lassen sich als Hilfslayer anzeigen; die Auswahl ist transparent hervorgehoben. **Letztes Live-Eingabebild ansehen** zeigt nach dem ersten Export die tatsächliche Eingabe der gewählten Kachel. Eingabebilder können einzeln als ausgeblendete QGIS-Layer geladen werden.

**Kachelgröße** gibt die Seitenlänge des quadratischen Kerns in Metern oder Kilometern vor, standardmäßig 1 km. **Gebiet übernehmen und Raster erstellen** übernimmt den Kartenausschnitt oder die Layer-Ausdehnung einmalig. Das Plugin wählt die lokale UTM-Zone (UPS in Polargebieten) und verankert die Kanten an Vielfachen der Seitenlänge ab KBS-Ursprung (0, 0). Vollständige Randkacheln können über das Gebiet hinausragen. Bei gleichem KBS und gleicher Größe bleiben Kanten auch bei einer Gebietserweiterung deckungsgleich; bei Wechsel der UTM-Zone entsteht ein neues Raster. Das Verfahren ist für regionale Gebiete gedacht.

**Weniger Kacheln** und **Mehr Kacheln** unter der Größenangabe schlagen die nächstgrößere beziehungsweise nächstkleinere passende Seitenlänge vor. Die Buttons zeigen Spalten × Zeilen; der Tooltip zeigt Anzahl und Größe. Für ein Gebiet von 600 × 600 m und eine Eingabe von 400 m ergibt das entweder 1 × 1 mit 600 m oder 2 × 2 mit 300 m. Die Größen werden aus Gebietsbreite/n und Gebietshöhe/n gewählt, sodass mindestens eine Richtung genau passt. Bei Rechtecken kann in der anderen Richtung Überstand bleiben, da die Kacheln quadratisch bleiben. Die Anzeige nennt diesen Überstand in Prozent (Kernflächen, ohne Kontext). Der Klick richtet das Raster an der linken oberen Gebietsecke aus und aktiviert vollständige Abdeckung. Optionaler Ergebniszuschnitt bleibt erhalten. Weitere Klicks wechseln zur jeweils nächsten Größe; die Grenze von 2500 Kacheln gilt weiterhin.

Angepasste Raster bleiben bei Navigation, Einheitenwechsel, Bildgröße und Kontext unverändert positioniert. Eine manuell geänderte Kachelgröße kehrt zum festen metrischen Rasterursprung zurück. Neues Übernehmen eines Gebiets setzt die Anpassung zurück. Rasteränderungen wählen wie bisher alle Kacheln aus und setzen Anschlusszuordnungen zurück. Beim Anpassen bleibt der Tab Grundlagen offen, damit beide Vorschläge direkt verglichen werden können.

Änderungen an Kachelgröße, Bildgröße, Kontext oder Randbehandlung aktualisieren die Vorschau nach kurzer Pause für das gespeicherte Gebiet. Neu berechnete Raster wählen alle Kacheln aus und setzen die bisherigen Anschlusszuordnungen zurück. Der Wechsel zwischen m und km erhält die physische Länge. Zoomen und Fenstergröße beeinflussen weder Kachelgrenzen noch Exportauflösung. Ein anderes Gebiet ausdrücklich erneut übernehmen. Bei 2 km Kernkante und 1024 Pixeln mit 128 Pixeln Kontext pro Seite ist die Bodenauflösung 2000 / 768 ≈ 2,604 m/Pixel. Mehr Pixel verbessern die rechnerische Auflösung bei unveränderten Kernflächen. Das ist keine garantierte KI-Detailgenauigkeit. Render-DPI sind auf 96 festgelegt. Das Gebiet bleibt während der Panel-Sitzung gespeichert; nach einem Neustart erneut übernehmen. Das Raster ist auf 2500 Kacheln begrenzt. In Rasterquellen bereits eingebrannte Beschriftungen lassen sich nicht durch Abschalten der QGIS-Beschriftung entfernen.

## Anbieter und Zugang

Generierungen senden Eingabebild, Stilreferenz, fertige Nachbarbilder und Prompt an den gewählten Anbieter und werden über dessen API abgerechnet. ChatGPT-/Gemini-Web-Abos ersetzen kein API-Konto mit entsprechender Abrechnung.

| Anbieter | Standard | Weitere bearbeitbare Modell-IDs |
| --- | --- | --- |
| Google | `gemini-nano-banana-2.1` | `gemini-3-pro-image`, `gemini-3.1-flash-image` |
| OpenAI | `gpt-image-2.5-sunburst` | `gpt-image-2.5-flare`, `gpt-image-2` |

Verfügbarkeit hängt vom Konto ab. Die Modell-ID ist bearbeitbar. Gemini wird mit 1:1 und 1K/2K aufgerufen; OpenAI über `/v1/images/edits` mit genauer Pixelgröße. OpenAI-Qualität ist einstellbar. Größere OpenAI-Auflösungen sind teilweise experimentell.

API-Keys bleiben nur im Arbeitsspeicher, getrennt nach Anbieter. Sie werden nicht in Manifest, Projekt, Einstellungen oder Logs geschrieben. Feste offizielle HTTPS-Endpunkte, keine automatisch verfolgten Weiterleitungen. QGIS übernimmt Netzwerk-, Proxy- und TLS-Einstellungen. Zertifikatsfehler werden nicht automatisch übergangen.

## Dateien, Abbruch und Grenzen

Jeder Durchlauf legt einen eigenen Ordner an:

```text
generation-…/
  reference.png                  # verwendete Stilreferenz
  neighbors/                     # kopierte Anschlussanker aus vorherigen Durchläufen
  inputs/B2.png                  # unmittelbar vor B2 exportiertes zusammengesetztes Bild
  inputs/B2.tif                  # dessen Georeferenzierung
  inputs/B2-protected.png        # Weiß: geschützte Pixel; Schwarz: neuer Bereich
  responses/B2.png               # unveränderte Modellantwort
  results/B2-stitched.png        # Modellantwort mit exakt zurückkopierten Anschlüssen
  results/…-full.png              # vollständiges korrigiertes Ergebnis
  results/…-context.tif           # vollständiges Ergebnis als georeferenzierter Anschluss
  results/…-core.png              # zugeschnittener sichtbarer Kern
  results/….tif                  # sichtbarer georeferenzierter Ergebnislayer
  manifest.json                  # Status, Abhängigkeiten, Nachbarn, Prompt, Modell; keine Keys
```

Das Manifest wird fortlaufend aktualisiert. Enthalten sind sowohl der allgemeine Prompt als auch die tatsächlich pro Kachel versandten Anweisungen und geschützten Pixelrechtecke. GeoTIFFs lassen sich unabhängig vom Plugin laden. Eine Wiederaufnahme alter Manifeste nach einem QGIS-Neustart ist bisher nicht eingebaut. Geladene Rasterlayer durch Speichern des QGIS-Projekts behalten; temporäre Hilfsvektoren bei Bedarf separat speichern.

Falsche Ausgabegrößen werden angezeigt und ohne Verzerren zurückgewiesen; die vollständige Modellantwort bleibt erhalten. API-/Netzwerkfehler erscheinen pro Kachel. Es gibt keine automatischen kostenpflichtigen Wiederholungen. Bei HTTP 401/402/403/429 werden weitere noch nicht versandte Anfragen gestoppt; bereits laufende Anfragen dürfen abschließen.

**Generierung abbrechen** stoppt Warteschlange, lokale Exporte und laufende Downloads. Bereits beim Anbieter eingegangene Anfragen können trotzdem berechnet werden. Eine einzelne API-Anfrage wird nach 30 Minuten lokal abgebrochen.

Die geschützten Pixel sind identisch; eine optisch perfekte Fortsetzung der neu erzeugten Bereiche ist dennoch eine Modellleistung. Die Georeferenzierung korrigiert keine von der KI verschobenen Objekte. Die visuelle Qualität dieses neuen Ablaufs wird durch den Nutzer getestet.

Externe Ergebnisbilder können weiterhin einer Kachel mit bereits vorhandener Live-Eingabe manuell zugeordnet werden; sie müssen dieselbe volle Pixelgröße haben.

## Entwicklung und Installation

Das Plugin ist auf diesem Rechner mit dem Repository verknüpft. In QGIS unter **Erweiterungen → MapStyle AI → MapStyle AI** öffnen.

```sh
python3 scripts/install.py --profile "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default"
python3 scripts/package.py
```

Die ZIP liegt unter `dist/mapstyle_ai-0.4.1.zip` und lässt sich im Erweiterungsmanager unter **Aus ZIP installieren** laden. Das Installationsskript überschreibt keine vorhandene Installation.

Zum Neuladen ohne laufenden Auftrag, unter Beibehaltung von Einstellungen und Schlüsseln im Arbeitsspeicher:

```python
from pathlib import Path; MAPSTYLE_REPO = '/Users/gormlabenz/Documents/labenz-io/000_Repositorys/mapstyle-ai'; exec((Path(MAPSTYLE_REPO) / 'scripts/reload_preserve.py').read_text())
```

`dev_load.py` lädt alternativ ohne diese Übernahme neu. Wiederhergestellte Einstellungen starten keine Exporte oder Generierungen.

## Prüfungen dieser Version

```sh
python3 -m unittest discover -s tests -v
```

Für das metrische Raster prüft ein zusätzlicher isolierter QGIS-Lauf Gebiet, Zoom, Fenstergröße, Pixelzahl, Kontext, Einheitenwechsel, verzögerte Rastervorschau und einen georeferenzierten Export mit synthetischen Daten. Keine Anbieteraufrufe und keine Änderung am geöffneten QGIS-Projekt:

```sh
QT_QPA_PLATFORM=offscreen PYTHONPATH=/Applications/QGIS.app/Contents/Resources/python /Applications/QGIS.app/Contents/MacOS/python scripts/qgis_metric_grid_check.py
```

Die lokalen Tests prüfen Geometrie, Pixelüberlappung, diagonale Freigabe, Abhängigkeiten, getrennte Auswahlen, Fehlerfortpflanzung und die Bildreihenfolge beider API-Formate. Dabei werden keine API-Anfragen versandt.

**In QGIS wurde genau ein zusammengesetztes Eingabebild exportiert, ohne Generierungsaufruf.** `scripts/qgis_live_export_once.py` verwendet rein synthetische Nachbarn und verändert das Projekt nicht. Der Test prüft Georeferenzierung, Eckpriorität, Schutzmaske und pixelgenaues Zurückkopieren. Bericht: `test-output/live-export-once/validation.json`.

Die älteren Smoke-Skripte für die frühere unabhängige Generierung sind kein Testablauf für Version 0.3 und wurden bei dieser Änderung nicht ausgeführt. Der eigentliche KI-Test wird vom Nutzer gestartet.

## Quellen

- [QGIS 3.44: Kartenrendering](https://docs.qgis.org/3.44/en/docs/pyqgis_developer_cookbook/composer.html)
- [QGIS 3.44: QgsMapSettings](https://api.qgis.org/api/3.44/classQgsMapSettings.html)
- [OpenAI: Bildbearbeitung mit Referenzen](https://developers.openai.com/api/docs/guides/image-generation)
- [Google: Bildgenerierung](https://ai.google.dev/gemini-api/docs/generate-content/image-generation)

### Timeout-Korrektur in 0.3.1

Qt-Transferlimit, QGIS-Inaktivitätstimer der einzelnen Bildanfrage und Plugin-Gesamtlimit betragen 30 Minuten. Das globale QGIS-Netzwerk-Timeout bleibt unverändert. QGIS 3.44 verwendet zusätzlich zu Qt einen eigenen Timer namens `timeoutTimer`; dieser wird nur an der jeweiligen API-Antwort angepasst. Start-/Fehlerzeitpunkte und das Zeitlimit werden im Manifest protokolliert; Netzwerkfehler enthalten die verstrichene Dauer.
