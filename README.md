# heyAtlas

Persönlicher Sprachassistent für macOS (Apple Silicon) mit Claude. Läuft lokal bis auf die Claude-Anfragen; später als Menüleisten-App in einer DMG.

„Hey Atlas“ (bis das eigene Modell trainiert ist: „Hey Jarvis“) → Ton → sprechen → Pause → mlx-whisper (lokal) → Claude mit Werkzeugen → AppleScript → Piper

## Einrichten

```sh
brew install python@3.12
/opt/homebrew/opt/python@3.12/bin/python3.12 -m venv .venv312
.venv312/bin/pip install -r requirements.txt rumps pyobjc-framework-ServiceManagement
security add-generic-password -s anthropic-api-key -a "$USER" -w   # fragt den Schlüssel verdeckt ab
```

Der Assistent liest den Schlüssel aus dem macOS-Schlüsselbund. Ist `ANTHROPIC_API_KEY` gesetzt, hat diese Variable Vorrang.

## Starten

```sh
.venv312/bin/python assistent.py --text   # erst tippen: testet Claude und die Werkzeuge
.venv312/bin/python assistent.py          # Wake-Word: „Hey Jarvis“, Ton abwarten, sprechen
.venv312/bin/python assistent.py --ptt    # Push-to-Talk: Enter startet und beendet die Aufnahme
```

Beim ersten Start fragt macOS nach Zugriff auf Mikrofon, Kalender und Erinnerungen (für das Terminal). Das Whisper-Modell (~500 MB) wird beim ersten Start geladen. Die Piper-Stimme lädst du einmalig mit:

```sh
mkdir -p stimmen && cd stimmen && ../.venv312/bin/python -m piper.download_voices de_DE-thorsten-medium
```

## Einstellungen (Umgebungsvariablen)

| Variable | Standard | Zweck |
|---|---|---|
| `CLAUDE_MODELL` | `claude-sonnet-5` | `claude-opus-5` ist gründlicher, aber etwa doppelt so langsam |
| `STIMME` | `de_DE-thorsten-medium` | Piper-Stimme (Name, wird geladen) oder macOS-Systemstimme als `macos:Anna (Premium)`; Premium-Stimmen unter Bedienungshilfen → Gesprochene Inhalte → Stimmen verwalten |
| `KALENDER` | erster beschreibbarer | Kalender für neue Termine |
| `BROWSER` | `Safari` | Browser für YouTube, alternativ `Google Chrome` |
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
- `erinnerungen_lesen`, `erinnerung_abhaken`: offene Erinnerungen (z. B. Einkaufsliste) lesen und abhaken; `erinnerung_anlegen` kann eine Liste wählen
- `mails_lesen`, `mails_suchen`, `mail_vorlesen`: ungelesene Mails, Suche nach Absender, Betreff und Zeitraum (auch gelesene), ganze Mail vorlesen ohne zitierten Verlauf (markiert sie als gelesen); nur Posteingang
- `mail_antworten`, `mail_schreiben`: Antworten und neue Mails als offener Entwurf; Senden nur auf ausdrücklichen Wunsch mit Rückfrage
- `lautstaerke`, `app_oeffnen`, `app_beenden`, `dunkelmodus`, `bildschirm_sperren`: Mac-Steuerung
- `apple_music_abspielen`, `apple_music_steuern`: eigene Mediathek in der Musik-App (Streaming-Katalog ist per AppleScript nicht erreichbar)
- `youtube_abspielen`, `youtube_steuern`: Suche über die YouTube Data API, Wiedergabe im Browser
- `nachrichten`: Schlagzeilen der Tagesschau; „Guten Morgen“ liefert ein Briefing aus Wetter, Terminen, Weckern, Nachrichten und ungelesenen Mails (nur Anzahl und Betreffs)
- Websuche: Claudes eingebaute Websuche für aktuelle Fragen
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
(Phrase `hey atlas`). Das Modell liegt in `assets/wakeword/hey_atlas.onnx` und ist Standard (zweite Version; die erste erkannte die echte Stimme kaum). Fehlt es, gilt „Hey Jarvis“. Bekannte Schwäche: „Hey Alice“ löst im Test mit künstlichen Stimmen oft aus; dann in der App die Empfindlichkeit auf „Zurückhaltend“ stellen.
Mit der eigenen Stimme testen:

```sh
.venv312/bin/python assistent.py --wakeword-test
```

## Mac vor Weckern aufwecken (pmset)

```sh
.venv312/bin/python assistent.py --pmset-einrichten   # fragt einmal nach dem Admin-Passwort
.venv312/bin/python assistent.py --pmset-entfernen
```

Legt `/etc/sudoers.d/heyatlas` an, das nur `pmset schedule wake …` und `pmset schedule cancel wake …` ohne Passwort erlaubt.
Atlas weckt den Mac dann eine Minute vor jedem Wecker. Aus dem Ruhezustand klappt das, aus dem ausgeschalteten Zustand nicht.

## YouTube

API-Schlüssel in der Google Cloud Console anlegen (YouTube Data API v3 aktivieren) und speichern:

```sh
security add-generic-password -s youtube-api-key -a "$USER" -w
```

Zum Steuern (Pause, vor, zurück) in Safari einmalig: Einstellungen → Erweitert → „Funktionen für Webentwickler zeigen“,
dann Menü Entwickler → „JavaScript von Apple Events erlauben“.

## Schnellbefehle ohne Claude

Kurze Befehle erledigt Atlas sofort selbst, ohne Anfrage an Claude (unter 1 ms statt 2–4 s). Sie greifen nur, wenn der
ganze Satz passt; alles andere geht an Claude.

- „Stopp“: schaltet Wecker/Timer aus, sonst pausiert es die Musik
- „Lauter“, „Leiser“, „Ton aus“, „Ton an“
- „Pause“, „Spiel weiter“, „Nächstes Lied“, „Vorheriges Lied“, „Was läuft gerade?“ (Spotify oder Musik, nur wenn sie spielt)
- „Wie spät ist es?“
- „Timer auf 10 Minuten (für die Nudeln)“, „Wie lange noch?“, „Timer abbrechen“

Die Sätze stehen in `SCHNELL_TEXTE` in `assistent.py` und lassen sich dort ergänzen.

## Codequalität

```sh
uvx ruff check .       # Konfiguration in ruff.toml, Ziel Python 3.9
```

## App und DMG

```sh
packaging/build.sh          # baut build/heyAtlas.app und build/heyAtlas-<Version>.dmg
.venv312/bin/python app.py  # Menüleisten-App ohne Bauen ausprobieren
```

- Menüleisten-App (`app.py`) mit Status, Pause und Einstellungen; die Einstellungen landen in
  `~/Library/Application Support/heyAtlas/einstellungen.json`, Schlüssel im Schlüsselbund, Ausgaben in
  `~/Library/Logs/heyAtlas.log`.
- Die App enthält ihre Python-Pakete, nutzt aber das Homebrew-Python dieses Macs (`/opt/homebrew/opt/python@3.12`).
  Sie läuft deshalb nur auf Macs mit diesem Python.
- Lokal signiert, ohne Apple-Entwicklerkonto: beim ersten Öffnen Rechtsklick → Öffnen. Nach einem Neubau kann
  macOS die Berechtigungen erneut abfragen.
- Whisper-Modell und Stimme lädt die App beim ersten Start herunter.
