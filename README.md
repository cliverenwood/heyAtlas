# heyAtlas

Persönlicher Sprachassistent für macOS (Apple Silicon) mit Claude. Läuft lokal bis auf die Claude-Anfragen; später als Menüleisten-App in einer DMG.

„Hey Atlas“ (bis das eigene Modell trainiert ist: „Hey Jarvis“) → Ton → sprechen → Pause → mlx-whisper (lokal) → Claude mit Werkzeugen → AppleScript → Piper

## Einrichten

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
security add-generic-password -s anthropic-api-key -a "$USER" -w   # fragt den Schlüssel verdeckt ab
```

Der Assistent liest den Schlüssel aus dem macOS-Schlüsselbund. Ist `ANTHROPIC_API_KEY` gesetzt, hat diese Variable Vorrang.

## Starten

```sh
.venv/bin/python assistent.py --text   # erst tippen: testet Claude und die Werkzeuge
.venv/bin/python assistent.py          # Wake-Word: „Hey Jarvis“, Ton abwarten, sprechen
.venv/bin/python assistent.py --ptt    # Push-to-Talk: Enter startet und beendet die Aufnahme
```

Beim ersten Start fragt macOS nach Zugriff auf Mikrofon, Kalender und Erinnerungen (für das Terminal). Das Whisper-Modell (~500 MB) wird beim ersten Start geladen. Die Piper-Stimme lädst du einmalig mit:

```sh
mkdir -p stimmen && cd stimmen && ../.venv/bin/python -m piper.download_voices de_DE-thorsten-medium
```

## Einstellungen (Umgebungsvariablen)

| Variable | Standard | Zweck |
|---|---|---|
| `CLAUDE_MODELL` | `claude-sonnet-5` | `claude-opus-5` ist gründlicher, aber etwa doppelt so langsam |
| `STIMME` | `stimmen/de_DE-thorsten-medium.onnx` | Piper-Stimme; weitere laden mit `cd stimmen && ../.venv/bin/python -m piper.download_voices de_DE-kerstin-low` |
| `KALENDER` | erster beschreibbarer | Kalender für neue Termine |
| `HEIMATORT` | leer | Ort für Wetterfragen ohne Ortsangabe, z. B. `Hamburg` |
| `ANTHROPIC_WORKSPACE_ID` | leer | nur für API-Schlüssel ohne Workspace (Console → Settings → Workspaces) |
| `NACHFRAGE_SEK` | `4` | so lange hört er nach einer Antwort ohne Wake-Word weiter, `0` = aus |
| `KURZBEFEHL_ORDNER` | `Sprachassistent` | nur Kurzbefehle aus diesem Ordner sind erlaubt |
| `WAKEWORD` | `hey_jarvis` | auch `alexa`, `hey_mycroft`, `hey_rhasspy` oder Pfad zu eigener `.onnx` |
| `WAKEWORD_SCHWELLE` | `0.5` | höher = weniger Fehlauslösungen, niedriger = reagiert leichter |
| `WHISPER_MODELL` | `mlx-community/whisper-small-mlx` | `mlx-community/whisper-large-v3-turbo` = genauer, ~1 s langsamer |

## Werkzeuge

- `erinnerung_anlegen`: legt die Erinnerung sofort an
- `termine_lesen`: liest Termine aus allen Kalendern über EventKit, inklusive Serienterminen (braucht „Vollzugriff“ auf Kalender für Terminal)
- `termin_anlegen`, `termin_verschieben`, `termin_loeschen`: fragen vorher per Sprache nach; bei Serienterminen betrifft es nur das eine Vorkommen
- `spotify_abspielen`: sucht Titel, Künstler oder Alben über die Spotify Web API und spielt sie in der Spotify-App
- `spotify_steuern`: Play, Pause, weiter, zurück, lauter, leiser, Zufallswiedergabe
- `spotify_aktueller_titel`: was gerade läuft
- `notiz_anlegen`, `notizen_suchen`: Notizen-App
- `nachricht_senden`: iMessage an einen Kontakt, mit Rückfrage vor dem Senden
- `kurzbefehle_auflisten`, `kurzbefehl_ausfuehren`: Kurzbefehle aus dem Ordner „Sprachassistent“, z. B. Home-Szenen
- `timer_stellen`, `timer_abfragen`, `timer_abbrechen`: mehrere benannte Timer gleichzeitig
- `wecker_stellen`, `wecker_auflisten`, `wecker_loeschen`: einmalig oder an Wochentagen, gespeichert in `~/Library/Application Support/heyAtlas/wecker.json`
- `klingeln_stoppen`: schaltet Wecker und Timer aus; im Sprachmodus reicht auch einfach „Hey Atlas“
- `wetter`: aktuelles Wetter und bis zu 7 Tage Vorhersage von Open-Meteo (kostenlos, ohne Schlüssel)

Spotify braucht Client ID und Secret einer App von developer.spotify.com im Schlüsselbund:

```sh
security add-generic-password -s spotify-client-id -a "$USER" -w
security add-generic-password -s spotify-client-secret -a "$USER" -w
```

## Autostart

`start.command` startet den Assistenten im Terminal und nach einem Absturz neu. Eintragen unter
Systemeinstellungen → Allgemein → Anmeldeobjekte → „+“ → `start.command` auswählen.

## Eigenes Aktivierungswort „Hey Atlas“

Training mit dem Colab-Notizbuch aus dem README von [openWakeWord](https://github.com/dscripka/openWakeWord)
(Phrase `hey atlas`). Die fertige Datei nach `stimmen/hey_atlas.onnx` legen und eintragen:

```sh
echo 'export WAKEWORD="$HOME/voiceAgent/stimmen/hey_atlas.onnx"' >> ~/.zshrc
```

## Mac vor Weckern aufwecken (pmset)

```sh
.venv/bin/python assistent.py --pmset-einrichten   # fragt einmal nach dem Admin-Passwort
.venv/bin/python assistent.py --pmset-entfernen
```

Legt `/etc/sudoers.d/heyatlas` an, das nur `pmset schedule wake …` und `pmset schedule cancel wake …` ohne Passwort erlaubt.
Atlas weckt den Mac dann eine Minute vor jedem Wecker. Aus dem Ruhezustand klappt das, aus dem ausgeschalteten Zustand nicht.
