"""Sprachassistent: Wake-Word -> Whisper (lokal) -> Claude mit Tools -> osascript -> say.

Start:  python3 assistent.py          (Wake-Word, Aufnahme endet bei einer Sprechpause)
        python3 assistent.py --ptt    (Push-to-Talk: Enter startet und beendet die Aufnahme)
        python3 assistent.py --text   (Tippen statt Sprechen, zum Testen der Tools)
        python3 assistent.py --wakeword-test  (zeigt live, wie sicher das Wake-Word erkannt wird)
        python3 assistent.py --pmset-einrichten / --pmset-entfernen
                                      (Mac vor Weckern aus dem Ruhezustand wecken; fragt einmal nach dem Admin-Passwort)
"""

import atexit
import base64
import getpass
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Literal, Optional

import anthropic
from anthropic import beta_tool

ORDNER = os.path.dirname(os.path.abspath(__file__))
DATENORDNER = os.path.expanduser("~/Library/Application Support/heyAtlas")

EINSTELLUNGEN_DATEI = os.path.join(DATENORDNER, "einstellungen.json")


def einstellungen_laden() -> dict:
    try:
        with open(EINSTELLUNGEN_DATEI) as datei:
            return json.load(datei)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def einstellungen_speichern(werte: dict) -> None:
    os.makedirs(DATENORDNER, exist_ok=True)
    with open(EINSTELLUNGEN_DATEI + ".neu", "w") as datei:
        json.dump(werte, datei, indent=2, ensure_ascii=False)
    os.replace(EINSTELLUNGEN_DATEI + ".neu", EINSTELLUNGEN_DATEI)


_EINSTELLUNGEN = einstellungen_laden()


def einstellung(name: str, standard: str) -> str:
    """Umgebungsvariable vor Einstellungsdatei (von der Menüleisten-App geschrieben) vor Standardwert."""
    return os.environ.get(name) or str(_EINSTELLUNGEN.get(name, standard))


MODELL = einstellung("CLAUDE_MODELL", "claude-sonnet-5")  # gemessen etwa doppelt so schnell wie claude-opus-5
# Lokales Sprachmodell aus LM Studio statt Claude: "lmstudio:<Modellschlüssel>", z. B. "lmstudio:qwen3.6-35b-a3b-mlx"
LMSTUDIO_PRAEFIX = "lmstudio:"
LOKALES_MODELL = MODELL[len(LMSTUDIO_PRAEFIX):] if MODELL.startswith(LMSTUDIO_PRAEFIX) else ""
LMSTUDIO_URL = einstellung("LMSTUDIO_URL", "http://localhost:1234")
LMS = os.path.expanduser("~/.lmstudio/bin/lms")
# Piper-Stimme: Name wie "de_DE-thorsten-medium" oder Pfad zu einer .onnx-Datei
STIMME = einstellung("STIMME", "de_DE-thorsten-medium")
STIMMEN_ORDNER = os.path.join(DATENORDNER, "stimmen")
KALENDER = einstellung("KALENDER", "")  # leer = erster beschreibbarer Kalender
HEIMATORT = einstellung("HEIMATORT", "")  # Ort für Wetterfragen ohne Ortsangabe
HEIMATHALTESTELLE = einstellung("HEIMATHALTESTELLE", "")  # z. B. "Leipzig, Breite Str."
FAHRPLAN_STADT = einstellung("FAHRPLAN_STADT", "Leipzig")  # Haltestellennamen werden zuerst hier gesucht
# Transitous verlangt eine Kontaktangabe im User-Agent: E-Mail oder Website
FAHRPLAN_KONTAKT = einstellung("FAHRPLAN_KONTAKT", "+https://github.com/cliverenwood/heyAtlas")
BROWSER = einstellung("BROWSER", "Safari")  # für YouTube: "Safari" oder "Google Chrome"
if BROWSER not in ("Safari", "Google Chrome"):  # der Name landet im AppleScript, deshalb nur bekannte Werte
    BROWSER = "Safari"
# "cohere" (Cohere Transcribe über transcribe.cpp, gemessen genauer und schneller) oder "whisper" (mlx-whisper)
SPRACHERKENNUNG = einstellung("SPRACHERKENNUNG", "cohere")
WHISPER_MODELL = einstellung("WHISPER_MODELL", "mlx-community/whisper-small-mlx")
COHERE_REPO = "handy-computer/cohere-transcribe-03-2026-gguf"  # Apache-2.0, dieselbe Datei wie in der App Handy
COHERE_DATEI = einstellung("COHERE_DATEI", "cohere-transcribe-03-2026-Q5_K_M.gguf")
ABTASTRATE = 16000

# Pfad zu einer .onnx-Datei oder ein mitgeliefertes openWakeWord-Modell (hey_jarvis, alexa, hey_mycroft, hey_rhasspy).
# Standard ist das selbst trainierte "Hey Atlas" (zweite Version; die erste erkannte die echte Stimme kaum).
# Fehlt die Datei, bleibt "Hey Jarvis" als Rückfall.
HEY_ATLAS = os.path.join(ORDNER, "assets", "wakeword", "hey_atlas.onnx")
WAKEWORD = einstellung("WAKEWORD", HEY_ATLAS if os.path.exists(HEY_ATLAS) else "hey_jarvis")
WAKEWORD_SCHWELLE = float(einstellung("WAKEWORD_SCHWELLE", "0.5"))
BLOCK = 1280  # 80 ms bei 16 kHz, die Blockgröße, die openWakeWord erwartet
# So lange hört der Assistent nach einer Antwort ohne Wake-Word weiter zu; 0 schaltet Folgefragen ab
NACHFRAGE_SEK = float(einstellung("NACHFRAGE_SEK", "4"))
# Optionale Funktionen mit weitreichenden Berechtigungen, standardmäßig aus (Schalter im Einstellungsmenü)
NACHRICHTEN_LESEN = einstellung("NACHRICHTEN_LESEN", "") in ("1", "True", "true")  # braucht Festplattenvollzugriff
ANRUFE_ANNEHMEN = einstellung("ANRUFE_ANNEHMEN", "") in ("1", "True", "true")  # braucht Bedienungshilfen
# Nur Kurzbefehle aus diesem Ordner der Kurzbefehle-App darf der Assistent ausführen
KURZBEFEHL_ORDNER = einstellung("KURZBEFEHL_ORDNER", "Sprachassistent")

MODUS = "text" if "--text" in sys.argv else "ptt" if "--ptt" in sys.argv else "wake"
TEXTMODUS = MODUS == "text"

SYSTEMPROMPT = """Du bist Atlas, ein Sprachassistent auf einem Mac. Deine Antworten werden vorgelesen.
Antworte auf Deutsch in ein bis zwei kurzen Sätzen, ohne Aufzählungen, Markdown oder Emojis.
Nutze die Werkzeuge für Erinnerungen und Kalender. Wenn Datum oder Uhrzeit unklar sind, frag kurz nach,
statt zu raten. Um einen Termin zu verschieben oder zu löschen, lies ihn zuerst mit termine_lesen und nutze
dessen Kennung wie T1. Für Musik nutze die Spotify-Werkzeuge, fürs Wetter das Wetter-Werkzeug; nenne Temperaturen
gerundet und ohne Nachkommastellen. Für Licht, Heizung und andere Abläufe nutze die Kurzbefehle; schau mit
kurzbefehle_auflisten nach, welche es gibt. Für Timer und Wecker nutze die Timer- und Wecker-Werkzeuge. Sprich Uhrzeiten
natürlich aus, etwa "um halb drei" oder "um 14 Uhr".
Musik spielst du über Spotify, außer der Nutzer nennt ausdrücklich Apple Music. Für aktuelle Fakten, die kein anderes
Werkzeug liefert, nutze die Websuche; nenne danach nur das Wichtigste in ein bis zwei Sätzen, ohne Quellen.
Mails und Nachrichten verschickst du nur, wenn der Nutzer es ausdrücklich will; sonst legst du einen Entwurf an.
Soll eine Mail vorgelesen werden, nenne Absender und Betreff und gib dann den Text von mail_vorlesen wörtlich
wieder; das ist die Ausnahme von der Regel mit den kurzen Antworten.
Für Bus und Bahn nutze abfahrten und verbindung_suchen; nenne Abfahrtszeiten natürlich und Verspätungen nur,
wenn es welche gibt.
Sagt der Nutzer "Guten Morgen" oder bittet um ein Briefing, hole parallel das Wetter, die Termine von heute, die
Wecker, die Nachrichten und die ungelesenen Mails und fasse alles in vier bis fünf Sätzen zusammen: Nachrichten nur
mit den zwei wichtigsten Schlagzeilen, Mails in einem einzigen Satz mit ihrer Anzahl und knapp den Betreffs der
höchstens drei neuesten, ohne Inhalte."""

# Lokale Modelle haben keine Websuche
SYSTEMPROMPT_LOKAL = SYSTEMPROMPT.replace(
    "Für aktuelle Fakten, die kein anderes\nWerkzeug liefert, nutze die Websuche; nenne danach nur das Wichtigste in "
    "ein bis zwei Sätzen, ohne Quellen.",
    "Eine Websuche hast du nicht; sag bei\naktuellen Fakten ohne passendes Werkzeug ehrlich, dass du sie nicht weißt.")

SCHLUESSELBUND_DIENST = "anthropic-api-key"


def schluesselbund(dienst: str) -> Optional[str]:
    """Liest ein Geheimnis aus dem macOS-Schlüsselbund, None wenn es fehlt."""
    ergebnis = subprocess.run(
        ["/usr/bin/security", "find-generic-password", "-s", dienst, "-a", getpass.getuser(), "-w"],
        capture_output=True, text=True)
    return ergebnis.stdout.strip() if ergebnis.returncode == 0 else None


def schluesselbund_speichern(dienst: str, geheimnis: str) -> None:
    """Speichert oder ersetzt ein Geheimnis im Schlüsselbund. Das Geheimnis geht über stdin an
    "security -i" statt als Argument, damit es nicht in der Prozessliste auftaucht."""
    if not geheimnis.isascii():
        # "security find-generic-password -w" gibt Nicht-ASCII als Hex zurück; API-Schlüssel sind reines ASCII
        raise ValueError("Der Schlüssel enthält Umlaute oder Sonderzeichen. Bitte genau so einfügen, wie er "
                         "beim Anbieter angezeigt wird.")

    def zitiert(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'

    befehl = (f"add-generic-password -U -s {zitiert(dienst)} -a {zitiert(getpass.getuser())} "
              f"-w {zitiert(geheimnis)}\n")
    ergebnis = subprocess.run(["/usr/bin/security", "-i"], input=befehl, capture_output=True, text=True)
    if ergebnis.returncode != 0 or "error" in ergebnis.stderr.lower():
        raise RuntimeError(f"Schlüsselbund hat abgelehnt: {ergebnis.stderr.strip()}")


def api_schluessel() -> Optional[str]:
    """Nimmt ANTHROPIC_API_KEY aus der Umgebung, sonst aus dem macOS-Schlüsselbund."""
    return os.environ.get("ANTHROPIC_API_KEY") or schluesselbund(SCHLUESSELBUND_DIENST)


# Nur nötig, wenn der API-Schlüssel keinem Workspace zugeordnet ist
WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

_client = None


def claude() -> anthropic.Anthropic:
    """Claude-Client (oder LM Studio), erst beim ersten Gebrauch erzeugt. So startet die App auch ohne Schlüssel
    und kann danach fragen."""
    global _client
    if _client is None and LOKALES_MODELL:
        # LM Studio spricht die Anthropic-Schnittstelle; der Schlüssel wird dort nicht geprüft
        _client = anthropic.Anthropic(base_url=LMSTUDIO_URL, api_key="lmstudio", timeout=180)
    if _client is None:
        schluessel = api_schluessel()
        if not schluessel:
            raise RuntimeError("Kein Anthropic-API-Schlüssel. In der Menüleiste unter Einstellungen eintragen oder:\n"
                               f"  security add-generic-password -s {SCHLUESSELBUND_DIENST} -a \"$USER\" -w")
        _client = anthropic.Anthropic(
            api_key=schluessel,
            default_headers={"anthropic-workspace-id": WORKSPACE_ID} if WORKSPACE_ID else None,
        )
    return _client


def lmstudio_modelle() -> list:
    """In LM Studio heruntergeladene Sprachmodelle, die Werkzeuge aufrufen können (für das Menü)."""
    try:
        ausgabe = subprocess.run([LMS, "ls", "--llm", "--json"], capture_output=True, text=True, timeout=15).stdout
        return [m for m in json.loads(ausgabe) if m.get("trainedForToolUse")]
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return []


def _lms_geladen() -> list:
    try:
        return json.loads(subprocess.run([LMS, "ps", "--json"], capture_output=True, text=True, timeout=15).stdout)
    except json.JSONDecodeError:
        return []


def _lms_laden() -> subprocess.CompletedProcess:
    return subprocess.run([LMS, "load", LOKALES_MODELL, "-y", "-c", "32768", "--identifier", LOKALES_MODELL],
                          capture_output=True, text=True, timeout=600)


def lokales_modell_laden() -> None:
    """Startet den LM-Studio-Server und lädt das gewählte Modell, falls es noch nicht geladen ist.
    Beim Laden über die Schnittstelle nähme LM Studio seine Standard-Kontextlänge, die für Systemprompt
    und Werkzeugbeschreibungen zu knapp sein kann."""
    if not os.path.exists(LMS):
        raise RuntimeError("LM Studio ist nicht installiert (lms fehlt).")
    subprocess.run([LMS, "server", "start"], capture_output=True, timeout=60)
    geladen = _lms_geladen()
    if any(m.get("identifier") == LOKALES_MODELL for m in geladen):
        return
    melde_status("Lade lokales Sprachmodell …")
    print(f"Lade '{LOKALES_MODELL}' in LM Studio …")
    ergebnis = _lms_laden()
    if ergebnis.returncode != 0 and "insufficient system resources" in ergebnis.stdout + ergebnis.stderr:
        # Ein anderes geladenes Sprachmodell belegt den Speicher (gemessen: 27B und 35B passen nicht zusammen)
        for modell in geladen:
            if modell.get("type") == "llm":
                print(f"Entlade '{modell['identifier']}', um Platz zu schaffen …")
                subprocess.run([LMS, "unload", modell["identifier"]], capture_output=True, timeout=60)
        ergebnis = _lms_laden()
    if ergebnis.returncode != 0:
        raise RuntimeError(f"LM Studio konnte '{LOKALES_MODELL}' nicht laden: "
                           f"{(ergebnis.stderr or ergebnis.stdout).strip()[-200:]}")


def lokales_modell_vorbereiten() -> None:
    try:
        lokales_modell_laden()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as fehler:
        print(f"Lokales Modell: {fehler}", file=sys.stderr)
        sprich("Das lokale Sprachmodell lässt sich nicht laden. Den Grund findest du in der Logdatei.")


# ---------------------------------------------------------------- Ein- und Ausgabe

# ---------------------------------------------------------------- Status für die Menüleisten-App

STATUS = {"text": "Startet …", "hoert": False}  # liest die App regelmäßig aus
PAUSIERT = threading.Event()  # gesetzt: Mikrofon zu, kein Wake-Word


def melde_status(text: str, hoert: bool = False) -> None:
    STATUS.update(text=text, hoert=hoert)


# ---------------------------------------------------------------- Stimmen

_stimme = None


_download_sperre = threading.Lock()


def stimmen_pfad(name: str) -> str:
    """Pfad zur Piper-Stimme; lädt sie beim ersten Mal herunter (20 bis 120 MB).
    Eine Stimme gilt erst als vorhanden, wenn Modell und Konfiguration vollständig da sind."""
    if name.endswith(".onnx"):
        return name
    with _download_sperre:  # zweiter Klick während eines Downloads wartet, statt Halbfertiges zu laden
        for ordner in (STIMMEN_ORDNER, os.path.join(ORDNER, "stimmen")):
            pfad = os.path.join(ordner, name + ".onnx")
            if os.path.exists(pfad) and os.path.exists(pfad + ".json"):
                return pfad
        import shutil
        import tempfile
        from pathlib import Path

        from piper.download_voices import download_voice

        melde_status(f"Lade Stimme {name} …")
        os.makedirs(STIMMEN_ORDNER, exist_ok=True)
        # Erst in einen Zwischenordner, dann verschieben: ein abgebrochener Download hinterlässt nichts Halbes
        with tempfile.TemporaryDirectory(dir=STIMMEN_ORDNER) as zwischen:
            download_voice(name, Path(zwischen))
            for endung in (".onnx.json", ".onnx"):  # Modell zuletzt, es zählt als Zeichen für "fertig"
                shutil.move(os.path.join(zwischen, name + endung), os.path.join(STIMMEN_ORDNER, name + endung))
        return os.path.join(STIMMEN_ORDNER, name + ".onnx")


def lade_stimme(name: Optional[str] = None):
    """Die eingestellte Stimme (gecacht) oder, mit name, eine andere zum Probehören."""
    global _stimme
    from piper import PiperVoice

    if name:
        return PiperVoice.load(stimmen_pfad(name))
    if _stimme is None:
        _stimme = PiperVoice.load(stimmen_pfad(STIMME))
    return _stimme


MACOS_PRAEFIX = "macos:"  # Stimmen-Einstellung "macos:Anna (Premium)" nutzt eine Systemstimme über say


def macos_stimmen() -> list:
    """Installierte deutsche macOS-Stimmen in Premium- oder erweiterter Qualität, z. B. "Anna (Premium)".
    Die einfachen Stimmen und die Effektstimmen wie "Grandpa" bleiben außen vor."""
    ergebnis = subprocess.run(["/usr/bin/say", "-v", "?"], capture_output=True, text=True)
    stimmen = []
    for zeile in ergebnis.stdout.splitlines():
        treffer = re.match(r"(.+?)\s+de_(DE|AT|CH)\s", zeile)
        if treffer and re.search(r"\((Premium|Enhanced|Erweitert)\)", treffer.group(1)):
            stimmen.append(treffer.group(1).strip())
    return stimmen


def vorlesen(text: str, name: Optional[str] = None) -> None:
    """Spricht den Text mit der eingestellten Stimme, oder mit name (zum Probehören)."""
    name = name or STIMME
    with _ausgabe_sperre:  # Timer und Wecker sprechen aus eigenen Threads
        if name.startswith(MACOS_PRAEFIX):
            # Text über stdin, damit er nie als Option von say gelesen wird
            subprocess.run(["/usr/bin/say", "-v", name.removeprefix(MACOS_PRAEFIX), "-f", "-"], input=text, text=True)
        else:
            _piper_vorlesen(text, lade_stimme(None if name == STIMME else name))


def _piper_vorlesen(text: str, stimme) -> None:
    """Spielt Piper-Sprache mit afplay ab, Satz für Satz; der nächste Satz entsteht, während der vorige läuft.
    Bewusst nicht über sounddevice: dessen Abspielfunktion läuft in Python und bekommt neben der
    Wake-Word-Erkennung nicht rechtzeitig Rechenzeit, das gab heftiges Knacken."""
    import tempfile
    import wave

    with tempfile.TemporaryDirectory() as ordner:
        laufend = None
        for nummer, teil in enumerate(stimme.synthesize(text)):
            datei = os.path.join(ordner, f"{nummer}.wav")
            with wave.open(datei, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(teil.sample_rate)
                w.writeframes(teil.audio_int16_bytes)
            if laufend:
                laufend.wait()
            laufend = subprocess.Popen(["/usr/bin/afplay", datei])
        if laufend:
            laufend.wait()


def sprich(text: str) -> None:
    print(f"🔊 {text}")
    if not TEXTMODUS:
        vorlesen(text)


_ausgabe_sperre = threading.Lock()

# Startet ein Werkzeug Musik oder ein Video, hört Atlas danach nicht ohne Aktivierungswort weiter;
# sonst hält er den Ton der Wiedergabe für eine Folgefrage
_wiedergabe_gestartet = threading.Event()


def wiedergabe_gestartet() -> None:
    _wiedergabe_gestartet.set()


# Nach "Hey Atlas" hält Atlas laufende Musik und Videos an, damit sie nicht in die Aufnahme geraten, und setzt sie
# nach der Antwort fort; außer der Nutzer wollte ausdrücklich Pause oder hat etwas Neues gestartet
_angehalten: list = []  # was Atlas für die aktuelle Frage angehalten hat: "Spotify", "Music", "YouTube"
_anhalten_thread: Optional[threading.Thread] = None  # hält die Wiedergabe im Hintergrund an
_wiedergabe_pausiert = threading.Event()


def wiedergabe_pausiert() -> None:
    _wiedergabe_pausiert.set()


def _anhalte_skript(app: str) -> str:
    return f"""if application "{app}" is running then
    tell application "{app}"
        if player state is playing then
            pause
            return "ja"
        end if
    end tell
end if
return "nein"
"""


def _app_anhalten(app: str) -> bool:
    try:
        # Kurzer Timeout: Eine App, die auf Apple Events nicht antwortet (z. B. offener Dialog),
        # darf die Runde nicht blockieren – sonst geht der Befehl nach dem Wake-Word verloren.
        return osascript(_anhalte_skript(app), timeout=5) == "ja"
    except (RuntimeError, subprocess.TimeoutExpired) as fehler:
        print(f"{app} anhalten: {fehler}", file=sys.stderr)
        return False


PLAYER_ANHALTEN_APPS = ("Spotify", "Music")
YOUTUBE_ANHALTEN = ("(function(){var v=document.querySelector('video'); if(!v||v.paused) return 'nein';"
                    " v.pause(); return 'ok';})()")
YOUTUBE_FORTSETZEN = "(function(){var v=document.querySelector('video'); if(v) v.play(); return 'ok';})()"


def wiedergabe_anhalten() -> list:
    angehalten = [app for app in PLAYER_ANHALTEN_APPS if _app_anhalten(app)]
    try:
        if youtube_js(YOUTUBE_ANHALTEN, timeout=5) == "ok":
            angehalten.append("YouTube")
    except (RuntimeError, subprocess.TimeoutExpired):
        pass  # z. B. "JavaScript von Apple Events" in Safari nicht erlaubt: Video läuft dann eben weiter
    return angehalten


def wiedergabe_fortsetzen(angehalten: list) -> None:
    for quelle in angehalten:
        try:
            if quelle == "YouTube":
                youtube_js(YOUTUBE_FORTSETZEN, timeout=5)
            else:
                osascript(f'tell application "{quelle}" to play', timeout=5)
        except (RuntimeError, subprocess.TimeoutExpired) as fehler:
            print(f"Fortsetzen {quelle}: {fehler}", file=sys.stderr)


def wiedergabe_anhalten_starten() -> None:
    """Hält die Wiedergabe im Hintergrund an, damit eine App ohne Apple-Event-Antwort (z. B. offener Dialog)
    den Signalton nach dem Wake-Word nicht verzögert."""
    global _anhalten_thread

    def ausfuehren() -> None:
        _angehalten[:] = wiedergabe_anhalten()

    _anhalten_thread = threading.Thread(target=ausfuehren, daemon=True)
    _anhalten_thread.start()


def wiedergabe_anhalten_abwarten() -> None:
    """Wartet, bis die Wiedergabe angehalten ist, damit _angehalten vollständig ist, bevor Atlas sie fortsetzt."""
    global _anhalten_thread
    if _anhalten_thread is not None:
        _anhalten_thread.join()
        _anhalten_thread = None


def ton(name: str) -> None:
    subprocess.run(["/usr/bin/afplay", f"/System/Library/Sounds/{name}.aiff"])


def nimm_auf():
    """Enter startet die Aufnahme, Enter beendet sie. Gibt float32-Audio mit 16 kHz zurück."""
    import numpy as np
    import sounddevice as sd

    input("⏎  Enter zum Sprechen …")
    teile = []
    with sd.InputStream(samplerate=ABTASTRATE, channels=1, dtype="float32",
                        callback=lambda daten, *_: teile.append(daten.copy())):
        input("🎙  Aufnahme läuft, Enter zum Beenden …")
    return np.concatenate(teile).flatten() if teile else np.zeros(0, dtype="float32")


_wakeword = None


def warte_auf_wakeword() -> None:
    """Hört lokal mit, bis das Wake-Word erkannt wird. Bis dahin verlässt kein Audio den Mac."""
    import sounddevice as sd

    modell = lade_wakeword()
    name = os.path.basename(WAKEWORD).removesuffix(".onnx")
    while True:
        while PAUSIERT.is_set():  # pausiert: Mikrofon bleibt zu
            melde_status("Pausiert")
            time.sleep(0.3)
        modell.reset()
        melde_status(f"Wartet auf „{name}“")
        print(f"👂 Warte auf „{name}“ (Mikrofon: {sd.query_devices(kind='input')['name']}) …")
        protokoll = WakewordProtokoll()
        with sd.InputStream(samplerate=ABTASTRATE, channels=1, dtype="int16", blocksize=BLOCK) as strom:
            while not PAUSIERT.is_set():
                block, uebergelaufen = strom.read(BLOCK)
                wert = max(modell.predict(block.flatten()).values())
                protokoll.block(wert, uebergelaufen)
                if wert >= WAKEWORD_SCHWELLE:
                    print(f"✅ Erkannt ({wert:.2f})")
                    return


class WakewordProtokoll:
    """Schreibt knapp verfehlte Aktivierungen und verlorene Tonblöcke ins Log, um Aussetzer zu unterscheiden:
    knapp verfehlt (Wert zwischen 0,2 und der Schwelle) oder gar nicht gehört (Ton verloren oder Wert 0)."""

    KNAPP = 0.2

    def __init__(self) -> None:
        self.hoechster = 0.0  # höchster Wert des laufenden Anlaufs unter der Schwelle
        self.verloren = 0
        self.gemeldet = 0.0  # Zeitpunkt der letzten Meldung verlorener Blöcke

    def block(self, wert: float, uebergelaufen: bool) -> None:
        if wert >= self.KNAPP:
            self.hoechster = max(self.hoechster, wert)
        elif self.hoechster:  # Anlauf vorbei, ohne die Schwelle zu erreichen
            if self.hoechster < WAKEWORD_SCHWELLE:
                print(f"🤏 Knapp verfehlt ({self.hoechster:.2f}, Schwelle {WAKEWORD_SCHWELLE})")
            self.hoechster = 0.0
        if uebergelaufen:
            self.verloren += 1
            if time.time() - self.gemeldet > 60:  # höchstens einmal pro Minute
                print(f"⚠️ Mikrofon: {self.verloren} Tonblöcke verloren (Rechner ausgelastet?)")
                self.gemeldet = time.time()


def lade_wakeword():
    """Lädt das Wake-Word-Modell. Mitgelieferte Modelle liegen schon im Paket; nur wenn eines fehlt, wird es
    heruntergeladen (in der App ist das Paket dafür nicht vorgesehen, deshalb vorab im Build)."""
    global _wakeword
    if _wakeword is None:
        from openwakeword.model import Model

        try:
            _wakeword = Model(wakeword_models=[WAKEWORD], inference_framework="onnx")
        except (ValueError, FileNotFoundError):
            from openwakeword.utils import download_models

            download_models([WAKEWORD])
            _wakeword = Model(wakeword_models=[WAKEWORD], inference_framework="onnx")
    return _wakeword


# True, wenn die letzte Aufnahme bis max_sek lief, ohne dass eine Sprechpause kam –
# dann war es durchgehendes Geräusch (Musik, Klingelton), kein Befehl
_aufnahme_lief_voll = False


def nimm_auf_bis_stille(stille_sek: float = 0.7, max_warten_sek: float = 5.0, max_sek: float = 20.0):
    """Signalton, dann Aufnahme bis zu einer Sprechpause. Gibt float32-Audio mit 16 kHz zurück."""
    global _aufnahme_lief_voll
    import numpy as np
    import sounddevice as sd

    ton("Tink")
    melde_status("Hört zu", hoert=True)
    print("🎙  Ich höre …")
    teile, pegel = [], []
    schwelle, gesprochen, still = None, False, 0.0
    dauer = BLOCK / ABTASTRATE
    with sd.InputStream(samplerate=ABTASTRATE, channels=1, dtype="float32", blocksize=BLOCK) as strom:
        while len(teile) * dauer < max_sek:
            block, _ = strom.read(BLOCK)
            teile.append(block.flatten())
            rms = float(np.sqrt(np.mean(block ** 2)))
            if schwelle is None:
                # Grundrauschen aus den ersten 5 Blöcken, das Minimum ist robust gegen frühes Sprechen
                pegel.append(rms)
                if len(pegel) == 5:
                    schwelle = max(0.01, 3 * min(pegel))
                continue
            if rms > schwelle:
                gesprochen, still = True, 0.0
            else:
                still += dauer
            if gesprochen and still >= stille_sek:
                _aufnahme_lief_voll = False
                break
            if not gesprochen and len(teile) * dauer > max_warten_sek:
                _aufnahme_lief_voll = False
                return np.zeros(0, dtype="float32")
    _aufnahme_lief_voll = True  # keine Sprechpause: durchgehendes Geräusch, kein Befehl
    ton("Pop")
    return np.concatenate(teile)


def transcribe_bibliothek() -> str:
    """libtranscribe: in der App unter Contents/Frameworks/transcribe, sonst von packaging/transcribe_bauen.sh."""
    for ordner in (os.path.join(os.path.dirname(sys.executable), "..", "Frameworks", "transcribe"),
                   os.path.join(ORDNER, "build", "transcribe")):
        pfad = os.path.normpath(os.path.join(ordner, "libtranscribe.dylib"))
        if os.path.exists(pfad):
            return pfad
    return ""


_cohere = None  # (Modell, Sitzung); das Modell muss leben, solange die Sitzung genutzt wird


def cohere_sitzung():
    global _cohere
    if _cohere is None:
        bibliothek = transcribe_bibliothek()
        if not bibliothek:
            raise RuntimeError("transcribe.cpp fehlt, bitte packaging/transcribe_bauen.sh ausführen")
        os.environ.setdefault("TRANSCRIBE_LIBRARY", bibliothek)
        import transcribe_cpp
        from huggingface_hub import hf_hub_download

        modell = transcribe_cpp.Model(hf_hub_download(COHERE_REPO, COHERE_DATEI))  # liegt danach im HF-Cache
        _cohere = (modell, modell.session())
        atexit.register(spracherkennung_freigeben)
    return _cohere[1]


def spracherkennung_freigeben() -> None:
    """Gibt Cohere-Sitzung und -Modell frei. Bleiben sie bis zum Prozessende offen, bricht ggml dort mit
    einem Assert ab (gemessen). Die App ruft das beim Beenden auf, weil macOS dann Pythons atexit überspringt."""
    global _cohere
    if _cohere is not None:
        modell, sitzung = _cohere
        _cohere = None
        sitzung.close()
        modell.close()


def transcribe(audio) -> str:
    """Lokale Spracherkennung auf der Grafikeinheit: Cohere Transcribe oder mlx-whisper."""
    import numpy as np

    if len(audio) < ABTASTRATE * 0.3:
        return ""
    melde_status("Denkt nach …")
    if SPRACHERKENNUNG == "cohere":
        return cohere_sitzung().run(np.asarray(audio, dtype=np.float32).ravel(), language="de").text.strip()
    import mlx_whisper

    return mlx_whisper.transcribe(audio, path_or_hf_repo=WHISPER_MODELL, language="de")["text"].strip()


def lade_spracherkennung() -> None:
    """Lädt das Modell vorab. Fehlt Cohere Transcribe oder seine Bibliothek, bleibt Whisper als Rückfall."""
    global SPRACHERKENNUNG
    import numpy as np

    if SPRACHERKENNUNG == "cohere":
        melde_status("Lade Spracherkennung (beim ersten Start etwa 1,8 GB) …")
        print(f"Lade Cohere Transcribe '{COHERE_DATEI}' …")
        try:
            cohere_sitzung()
        except Exception as fehler:  # fehlende Bibliothek, Download- oder Ladefehler: Atlas soll trotzdem hören
            print(f"Cohere Transcribe nicht verfügbar ({fehler}), nehme Whisper.", file=sys.stderr)
            SPRACHERKENNUNG = "whisper"
    if SPRACHERKENNUNG != "cohere":
        melde_status("Lade Spracherkennung (beim ersten Start etwa 500 MB) …")
        print(f"Lade Whisper-Modell '{WHISPER_MODELL}' (beim ersten Mal wird es heruntergeladen) …")
    transcribe(np.zeros(ABTASTRATE, dtype="float32"))  # wärmt die Grafikeinheit auf; das Modell bleibt im Speicher


def hoere(direkt: bool = False, max_warten_sek: float = 5.0) -> str:
    """direkt=True überspringt das Wake-Word, etwa für Rückfragen und Folgefragen."""
    if MODUS == "text":
        return input("Du: ").strip()
    if MODUS == "ptt":
        audio = nimm_auf()
    else:
        if not direkt:
            warte_auf_wakeword()
            # Klingelt gerade ein Wecker oder Timer, reicht "Hey Atlas" zum Ausschalten, ohne Umweg über Claude
            if klingeln_beenden():
                sprich("Okay, ist aus.")
                return ""
            wiedergabe_anhalten_starten()  # im Hintergrund: der Signalton darf nicht auf das Anhalten warten
        audio = nimm_auf_bis_stille(max_warten_sek=max_warten_sek)
    text = transcribe(audio)
    print(f"📝 {text}")
    return text


def bestaetigen(frage: str) -> bool:
    """Fragt per Sprache nach; nur ein klares Ja zählt, jedes Nein oder Nicht gewinnt."""
    sprich(frage)
    woerter = set(hoere(direkt=True).lower().replace(",", " ").replace(".", " ").replace("!", " ").split())
    if woerter & {"nein", "nicht", "nee", "stopp", "abbrechen"}:
        return False
    return bool(woerter & {"ja", "jawohl", "genau", "mach", "okay", "ok", "gerne", "bitte"})


# ---------------------------------------------------------------- AppleScript

DATUM_HELFER = """
on mkdate(y, mo, d, h, mi)
    set dt to current date
    set day of dt to 1
    set year of dt to (y as integer)
    set month of dt to (mo as integer)
    set day of dt to (d as integer)
    set hours of dt to (h as integer)
    set minutes of dt to (mi as integer)
    set seconds of dt to 0
    return dt
end mkdate
"""


def osascript(skript: str, *argumente: str, timeout: float = 60) -> str:
    """Führt AppleScript aus. Werte gehen als argv hinein, nie per String-Verkettung ins Skript."""
    ergebnis = subprocess.run(["/usr/bin/osascript", "-e", skript, *argumente],
                              capture_output=True, text=True, timeout=timeout)
    if ergebnis.returncode != 0:
        raise RuntimeError(ergebnis.stderr.strip())
    return ergebnis.stdout.strip()


def datum_teile(iso: str) -> list:
    d = datetime.fromisoformat(iso)
    return [str(d.year), str(d.month), str(d.day), str(d.hour), str(d.minute)]


# ---------------------------------------------------------------- Werkzeuge

@beta_tool
def erinnerung_anlegen(titel: str, faellig: Optional[str] = None, liste: Optional[str] = None) -> str:
    """Legt eine Erinnerung in der Erinnerungen-App an.

    Args:
        titel: Text der Erinnerung.
        faellig: Optionaler Zeitpunkt im ISO-Format, z. B. 2026-10-11T09:00.
        liste: Optionale Liste, z. B. "Einkauf"; weglassen für die Standardliste.
    """
    # Leere Werte als Platzhalter, damit die argv-Positionen im Skript fest bleiben
    teile = datum_teile(faellig) if faellig else ["", "", "", "", ""]
    skript = DATUM_HELFER + """
on run argv
    if (item 3 of argv) is not "" then
        set dt to mkdate(item 3 of argv, item 4 of argv, item 5 of argv, item 6 of argv, item 7 of argv)
    end if
    tell application "Reminders"
        -- "remind me date" ist Vokabular der Erinnerungen-App und muss innerhalb von tell stehen
        if (item 3 of argv) is "" then
            set eigenschaften to {name:(item 1 of argv)}
        else
            set eigenschaften to {name:(item 1 of argv), remind me date:dt}
        end if
        if (item 2 of argv) is "" then
            make new reminder with properties eigenschaften
        else
            make new reminder at end of reminders of list (item 2 of argv) with properties eigenschaften
        end if
    end tell
end run"""
    osascript(skript, titel, liste or "", *teile)
    return f"Erinnerung '{titel}' angelegt" + (f" in der Liste {liste}." if liste else ".")


_eventkit = None
_eventkit_freigaben: set = set()


def kalender_speicher(art: str = "termine"):
    """EventKit-Zugang zu Kalender ("termine") oder Erinnerungen ("erinnerungen").
    Schneller als die Apps per AppleScript fernzusteuern."""
    import EventKit

    global _eventkit
    if _eventkit is None:
        _eventkit = EventKit.EKEventStore.alloc().init()
    if art not in _eventkit_freigaben:
        typ, anfrage, name = {
            "termine": (EventKit.EKEntityTypeEvent, "requestFullAccessToEventsWithCompletion_", "Kalender"),
            "erinnerungen": (EventKit.EKEntityTypeReminder, "requestFullAccessToRemindersWithCompletion_",
                             "Erinnerungen"),
        }[art]
        if EventKit.EKEventStore.authorizationStatusForEntityType_(typ) != EventKit.EKAuthorizationStatusFullAccess:
            fertig, ergebnis = threading.Event(), {}

            def antwort(erlaubt, _fehler):
                ergebnis["erlaubt"] = erlaubt
                fertig.set()

            getattr(_eventkit, anfrage)(antwort)
            fertig.wait(120)
            if not ergebnis.get("erlaubt"):
                raise RuntimeError(f"Kein Zugriff auf {name}. Systemeinstellungen → Datenschutz & Sicherheit → "
                                   f"{name} → Terminal auf „Vollzugriff“ stellen.")
        _eventkit_freigaben.add(art)
    return _eventkit


_letzte_erinnerungen: dict = {}


@beta_tool
def erinnerungen_lesen(liste: Optional[str] = None) -> str:
    """Liest offene Erinnerungen, z. B. die Einkaufsliste. Jede bekommt eine Kennung wie E1 zum Abhaken.

    Args:
        liste: Name der Liste, z. B. "Einkauf"; weglassen für alle Listen.
    """
    import EventKit

    speicher = kalender_speicher("erinnerungen")
    listen = list(speicher.calendarsForEntityType_(EventKit.EKEntityTypeReminder))
    if liste:
        passend = [k for k in listen if k.title().lower() == liste.lower()] or \
                  [k for k in listen if liste.lower() in k.title().lower()]
        if not passend:
            return f"Keine Liste '{liste}'. Vorhanden: {', '.join(k.title() for k in listen)}"
        listen = passend
    abfrage = speicher.predicateForIncompleteRemindersWithDueDateStarting_ending_calendars_(None, None, listen)
    fertig, ergebnis = threading.Event(), {}

    def antwort(erinnerungen):
        ergebnis["liste"] = list(erinnerungen or [])
        fertig.set()

    speicher.fetchRemindersMatchingPredicate_completion_(abfrage, antwort)
    fertig.wait(30)
    _letzte_erinnerungen.clear()
    zeilen = []
    for nummer, r in enumerate(ergebnis.get("liste", []), 1):
        _letzte_erinnerungen[f"E{nummer}"] = r
        faellig = r.dueDateComponents()
        wann = ""
        if faellig is not None and faellig.date() is not None:
            wann = f" | fällig {datetime.fromtimestamp(faellig.date().timeIntervalSince1970()):%Y-%m-%d %H:%M}"
        zeilen.append(f"[E{nummer}] {r.title()} | {r.calendar().title()}{wann}")
    return "\n".join(zeilen) or "Keine offenen Erinnerungen."


@beta_tool
def erinnerung_abhaken(kennung: str) -> str:
    """Hakt eine zuvor mit erinnerungen_lesen gelesene Erinnerung als erledigt ab.

    Args:
        kennung: Kennung aus erinnerungen_lesen, z. B. E3.
    """
    r = _letzte_erinnerungen.get(kennung.strip().upper())
    if r is None:
        return f"Unbekannte Kennung {kennung}. Lies die Erinnerungen zuerst mit erinnerungen_lesen."
    r.setCompleted_(True)
    ok, fehler = kalender_speicher("erinnerungen").saveReminder_commit_error_(r, True, None)
    if not ok:
        raise RuntimeError(f"Erinnerungen hat abgelehnt: {fehler.localizedDescription() if fehler else 'unbekannt'}")
    _letzte_erinnerungen.pop(kennung.strip().upper(), None)
    return f"'{r.title()}' abgehakt."


@beta_tool
def termine_lesen(von: str, bis: str) -> str:
    """Liest Kalendertermine aus allen Kalendern in einem Zeitraum.

    Args:
        von: Beginn im ISO-Format, z. B. 2026-10-11T00:00.
        bis: Ende im ISO-Format, z. B. 2026-10-12T00:00.
    """
    from Foundation import NSDate

    def nsdate(iso: str):
        return NSDate.dateWithTimeIntervalSince1970_(datetime.fromisoformat(iso).timestamp())

    def zeit(d) -> datetime:
        return datetime.fromtimestamp(d.timeIntervalSince1970())

    speicher = kalender_speicher()
    # EventKit liefert auch Serientermine und Termine, die vor "von" beginnen und noch laufen
    abfrage = speicher.predicateForEventsWithStartDate_endDate_calendars_(nsdate(von), nsdate(bis), None)
    termine = sorted(speicher.eventsMatchingPredicate_(abfrage) or [], key=lambda e: zeit(e.startDate()))
    # Kurze Kennungen statt EventKit-IDs: Serientermine teilen sich eine ID, das Objekt meint genau ein Vorkommen
    _letzte_termine.clear()
    zeilen = []
    for nummer, e in enumerate(termine, 1):
        _letzte_termine[f"T{nummer}"] = e
        beginn = zeit(e.startDate())
        wann = f"{beginn:%Y-%m-%d} ganztägig" if e.isAllDay() else f"{beginn:%Y-%m-%d %H:%M}–{zeit(e.endDate()):%H:%M}"
        ort = f" | Ort: {e.location()}" if e.location() else ""
        zeilen.append(f"[T{nummer}] {e.title()} | {wann} | {e.calendar().title()}{ort}")
    return "\n".join(zeilen) or "Keine Termine in diesem Zeitraum."


_letzte_termine: dict = {}


def gelesener_termin(kennung: str):
    termin = _letzte_termine.get(kennung.strip().upper())
    if termin is None:
        raise ValueError(f"Unbekannte Kennung {kennung}. Lies die Termine zuerst mit termine_lesen.")
    return termin


def speichere_termin(aktion: str, termin) -> None:
    import EventKit

    if aktion == "loeschen":
        ok, fehler = kalender_speicher().removeEvent_span_error_(termin, EventKit.EKSpanThisEvent, None)
    else:
        ok, fehler = kalender_speicher().saveEvent_span_error_(termin, EventKit.EKSpanThisEvent, None)
    if not ok:
        raise RuntimeError(f"Kalender hat abgelehnt: {fehler.localizedDescription() if fehler else 'unbekannt'}")


@beta_tool
def termin_verschieben(kennung: str, neuer_beginn: str, dauer_minuten: Optional[int] = None) -> str:
    """Verschiebt einen zuvor mit termine_lesen gelesenen Termin. Fragt vorher per Sprache nach.
    Bei Serienterminen wird nur dieses eine Vorkommen verschoben.

    Args:
        kennung: Kennung aus termine_lesen, z. B. T2.
        neuer_beginn: Neuer Beginn im ISO-Format, z. B. 2026-10-12T15:00.
        dauer_minuten: Neue Dauer in Minuten; weglassen, um die bisherige Dauer zu behalten.
    """
    from Foundation import NSDate

    termin = gelesener_termin(kennung)
    alt_dauer = termin.endDate().timeIntervalSince1970() - termin.startDate().timeIntervalSince1970()
    start = datetime.fromisoformat(neuer_beginn)
    dauer = dauer_minuten * 60 if dauer_minuten else alt_dauer
    if not bestaetigen(f"Soll ich '{termin.title()}' auf den {start:%d.%m.} um {start:%H:%M} Uhr verschieben?"):
        return "Der Nutzer hat abgelehnt, der Termin bleibt, wie er ist."
    termin.setStartDate_(NSDate.dateWithTimeIntervalSince1970_(start.timestamp()))
    termin.setEndDate_(NSDate.dateWithTimeIntervalSince1970_(start.timestamp() + dauer))
    speichere_termin("speichern", termin)
    return f"Termin '{termin.title()}' verschoben."


@beta_tool
def termin_loeschen(kennung: str) -> str:
    """Löscht einen zuvor mit termine_lesen gelesenen Termin. Fragt vorher per Sprache nach.
    Bei Serienterminen wird nur dieses eine Vorkommen gelöscht.

    Args:
        kennung: Kennung aus termine_lesen, z. B. T2.
    """
    termin = gelesener_termin(kennung)
    beginn = datetime.fromtimestamp(termin.startDate().timeIntervalSince1970())
    if not bestaetigen(f"Soll ich '{termin.title()}' am {beginn:%d.%m.} um {beginn:%H:%M} Uhr wirklich löschen?"):
        return "Der Nutzer hat abgelehnt, der Termin wurde nicht gelöscht."
    speichere_termin("loeschen", termin)
    _letzte_termine.pop(kennung.strip().upper(), None)
    return f"Termin '{termin.title()}' gelöscht."


@beta_tool
def termin_anlegen(titel: str, beginn: str, dauer_minuten: int = 60) -> str:
    """Legt einen Kalendertermin an. Der Nutzer wird vorher per Sprache um Bestätigung gebeten.

    Args:
        titel: Titel des Termins.
        beginn: Beginn im ISO-Format, z. B. 2026-10-11T14:30.
        dauer_minuten: Dauer in Minuten.
    """
    start = datetime.fromisoformat(beginn)
    ende = start + timedelta(minutes=dauer_minuten)
    frage = f"Soll ich '{titel}' am {start:%d.%m.} um {start:%H:%M} Uhr eintragen?"
    if not bestaetigen(frage):
        return "Der Nutzer hat abgelehnt, der Termin wurde nicht angelegt."
    skript = DATUM_HELFER + """
on run argv
    set d1 to mkdate(item 3 of argv, item 4 of argv, item 5 of argv, item 6 of argv, item 7 of argv)
    set d2 to mkdate(item 8 of argv, item 9 of argv, item 10 of argv, item 11 of argv, item 12 of argv)
    tell application "Calendar"
        if (item 2 of argv) is "" then
            set k to first calendar whose writable is true
        else
            set k to calendar (item 2 of argv)
        end if
        make new event at end of events of k with properties {summary:(item 1 of argv), start date:d1, end date:d2}
    end tell
end run"""
    osascript(skript, titel, KALENDER, *datum_teile(beginn), *datum_teile(ende.isoformat()))
    return f"Termin '{titel}' angelegt."


def hole_json(url: str, **kw) -> dict:
    if not url.startswith("https://"):
        raise ValueError(f"Nur https-Adressen erlaubt: {url}")
    anfrage = urllib.request.Request(url, **kw)  # noqa: S310 - Schema oben auf https geprüft
    with urllib.request.urlopen(anfrage, timeout=10) as antwort:  # noqa: S310
        return json.load(antwort)


# ---------------------------------------------------------------- Spotify

_spotify_token = {"wert": "", "bis": 0.0}


def spotify_token() -> str:
    """Token für die Spotify-Suche (Client-Credentials, ohne Browser-Anmeldung), wird eine Stunde gecacht."""
    if time.time() < _spotify_token["bis"]:
        return _spotify_token["wert"]
    cid, geheim = schluesselbund("spotify-client-id"), schluesselbund("spotify-client-secret")
    if not (cid and geheim):
        raise RuntimeError("Spotify-Zugangsdaten fehlen im Schlüsselbund (spotify-client-id, spotify-client-secret).")
    daten = hole_json("https://accounts.spotify.com/api/token", data=b"grant_type=client_credentials",
                      headers={"Authorization": "Basic " + base64.b64encode(f"{cid}:{geheim}".encode()).decode()})
    _spotify_token.update(wert=daten["access_token"], bis=time.time() + daten["expires_in"] - 60)
    return _spotify_token["wert"]


@beta_tool
def spotify_abspielen(suche: str, art: Literal["track", "artist", "album"] = "track") -> str:
    """Sucht bei Spotify und spielt den besten Treffer in der Spotify-App ab.

    Args:
        suche: Suchbegriff, z. B. "Bohemian Rhapsody Queen" oder "Herbert Grönemeyer".
        art: "track" für einen Titel, "artist" für die Musik eines Künstlers, "album" für ein Album.
    """
    url = "https://api.spotify.com/v1/search?" + urllib.parse.urlencode(
        {"q": suche, "type": art, "limit": 1, "market": "DE"})
    antwort = hole_json(url, headers={"Authorization": "Bearer " + spotify_token()})
    treffer = [t for t in antwort[art + "s"]["items"] if t]
    if not treffer:
        return f"Nichts gefunden für '{suche}'."
    t = treffer[0]
    osascript('on run argv\ntell application "Spotify" to play track (item 1 of argv)\nend run', t["uri"])
    wiedergabe_gestartet()
    von = f" von {', '.join(k['name'] for k in t['artists'])}" if "artists" in t else ""
    return f"Spiele {t['name']}{von}."


SPOTIFY_BEFEHLE = {
    "play": "play", "pause": "pause", "weiter": "next track", "zurueck": "previous track",
    "lauter": "set sound volume to (sound volume + 15)", "leiser": "set sound volume to (sound volume - 15)",
    "zufall_an": "set shuffling to true", "zufall_aus": "set shuffling to false",
}


@beta_tool
def spotify_steuern(aktion: Literal["play", "pause", "weiter", "zurueck", "lauter", "leiser",
                                    "zufall_an", "zufall_aus"]) -> str:
    """Steuert die laufende Wiedergabe in der Spotify-App.

    Args:
        aktion: play, pause, weiter (nächster Titel), zurueck (vorheriger Titel), lauter, leiser, zufall_an, zufall_aus.
    """
    osascript(f'tell application "Spotify" to {SPOTIFY_BEFEHLE[aktion]}')
    if aktion in ("play", "weiter", "zurueck"):
        wiedergabe_gestartet()
    elif aktion == "pause":
        wiedergabe_pausiert()
    return f"Erledigt: {aktion}."


@beta_tool
def spotify_aktueller_titel() -> str:
    """Sagt, welcher Titel in Spotify gerade läuft."""
    return osascript("""tell application "Spotify"
    if player state is stopped then return "Gerade läuft nichts."
    return (name of current track) & " von " & (artist of current track) & " (" & (player state as string) & ")"
end tell""")


# ---------------------------------------------------------------- Wetter

WETTERCODES = {
    0: "klar", 1: "überwiegend klar", 2: "teils bewölkt", 3: "bedeckt", 45: "Nebel", 48: "Nebel mit Reif",
    51: "leichter Nieselregen", 53: "Nieselregen", 55: "starker Nieselregen", 56: "gefrierender Niesel",
    57: "gefrierender Niesel", 61: "leichter Regen", 63: "Regen", 65: "starker Regen", 66: "gefrierender Regen",
    67: "gefrierender Regen", 71: "leichter Schneefall", 73: "Schneefall", 75: "starker Schneefall",
    77: "Schneegriesel", 80: "leichte Regenschauer", 81: "Regenschauer", 82: "heftige Regenschauer",
    85: "Schneeschauer", 86: "starke Schneeschauer", 95: "Gewitter", 96: "Gewitter mit Hagel", 99: "Gewitter mit Hagel",
}


@beta_tool
def wetter(ort: Optional[str] = None, tage: int = 3) -> str:
    """Aktuelles Wetter und Vorhersage von Open-Meteo.

    Args:
        ort: Ortsname, z. B. "Hamburg". Weglassen für den Heimatort des Nutzers.
        tage: Anzahl Vorhersagetage ab heute, 1 bis 7.
    """
    ort = ort or HEIMATORT
    if not ort:
        return "Kein Ort angegeben und kein Heimatort eingestellt. Frag den Nutzer nach dem Ort."
    orte = hole_json("https://geocoding-api.open-meteo.com/v1/search?" + urllib.parse.urlencode(
        {"name": ort, "count": 1, "language": "de"})).get("results")
    if not orte:
        return f"Ort '{ort}' nicht gefunden."
    o = orte[0]
    w = hole_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": o["latitude"], "longitude": o["longitude"], "timezone": "auto",
        "forecast_days": max(1, min(tage, 7)),
        "current": "temperature_2m,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,precipitation_sum",
    }))
    j, d = w["current"], w["daily"]
    zeilen = [f"{o['name']} ({o.get('admin1', o.get('country', ''))}), jetzt: {j['temperature_2m']} °C, "
              f"{WETTERCODES.get(j['weather_code'], 'unbekannt')}, Wind {j['wind_speed_10m']} km/h"]
    for i, tag in enumerate(d["time"]):
        zeilen.append(f"{tag}: {d['temperature_2m_min'][i]} bis {d['temperature_2m_max'][i]} °C, "
                      f"{WETTERCODES.get(d['weather_code'][i], 'unbekannt')}, "
                      f"Regenwahrscheinlichkeit {d['precipitation_probability_max'][i]} %, "
                      f"Niederschlag {d['precipitation_sum'][i]} mm")
    return "\n".join(zeilen)


# ---------------------------------------------------------------- Timer und Wecker

WOCHENTAGE = ["mo", "di", "mi", "do", "fr", "sa", "so"]
WECKER_DATEI = os.path.join(DATENORDNER, "wecker.json")
SUDOERS_DATEI = "/etc/sudoers.d/heyatlas"
PMSET_BESITZER = "heyAtlas"
VERPASST_TOLERANZ = timedelta(minutes=30)  # nach Ruhezustand verpasste Wecker klingeln nur, wenn so frisch

_uhr_sperre = threading.Lock()
_timer: dict = {}    # Name -> Ende (datetime)
_wecker: list = []   # [{"id", "name", "zeit", "tage", "wake"}], gespeichert in WECKER_DATEI
_klingeln: dict = {} # Beschreibung -> threading.Event zum Stoppen


def wecker_laden() -> None:
    global _wecker
    try:
        with open(WECKER_DATEI) as datei:
            _wecker = json.load(datei)
    except FileNotFoundError:
        _wecker = []


def wecker_speichern() -> None:
    os.makedirs(DATENORDNER, exist_ok=True)
    with open(WECKER_DATEI + ".neu", "w") as datei:
        json.dump(_wecker, datei, indent=2, ensure_ascii=False)
    os.replace(WECKER_DATEI + ".neu", WECKER_DATEI)


def naechstes_vorkommen(zeit: datetime, tage: list) -> datetime:
    """Erster Zeitpunkt ab jetzt mit gleicher Uhrzeit an einem der Wochentage."""
    while zeit <= datetime.now() or WOCHENTAGE[zeit.weekday()] not in tage:
        zeit += timedelta(days=1)
    return zeit


# --- pmset: weckt den Mac aus dem Ruhezustand, eine Minute vor dem Wecker

def pmset(*argumente: str) -> bool:
    """Ruft "pmset schedule" über sudo ohne Passwort auf; geht nur nach pmset_einrichten()."""
    ergebnis = subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/pmset", "schedule", *argumente],
                              capture_output=True, text=True)
    return ergebnis.returncode == 0


def pmset_eingerichtet() -> bool:
    ergebnis = subprocess.run(["/usr/bin/sudo", "-n", "-l", "/usr/bin/pmset", "schedule", "wake", "01/01/30 00:00:00"],
                              capture_output=True, text=True)
    return ergebnis.returncode == 0


def pmset_einrichten() -> None:
    """Erlaubt (nach einmaliger Admin-Abfrage) nur "pmset schedule wake/cancel wake" ohne Passwort."""
    regel = (f"# heyAtlas: Mac vor einem Wecker aus dem Ruhezustand wecken\n"
             f"{os.environ['USER']} ALL=(root) NOPASSWD: /usr/bin/pmset schedule wake *, "
             f"/usr/bin/pmset schedule cancel wake *\n")
    os.makedirs(DATENORDNER, exist_ok=True)
    entwurf = os.path.join(DATENORDNER, "sudoers-heyatlas")
    with open(entwurf, "w") as datei:
        datei.write(regel)
    # visudo prüft die Regel, bevor sie aktiv wird; eine kaputte sudoers-Datei würde sudo blockieren
    befehl = (f"/usr/sbin/visudo -cf {sh(entwurf)} && "
              f"/usr/bin/install -m 0440 -o root -g wheel {sh(entwurf)} {SUDOERS_DATEI}")
    osascript('on run argv\ndo shell script (item 1 of argv) with administrator privileges\nend run', befehl)
    os.remove(entwurf)
    with _uhr_sperre:
        for w in _wecker:
            wecker_aufwecken_planen(w)
        wecker_speichern()


def pmset_entfernen() -> None:
    with _uhr_sperre:
        for w in _wecker:
            if w.get("wake"):
                pmset("cancel", "wake", w["wake"], PMSET_BESITZER)
                w["wake"] = ""
        wecker_speichern()
    osascript('on run argv\ndo shell script (item 1 of argv) with administrator privileges\nend run',
              f"/bin/rm -f {SUDOERS_DATEI}")


def sh(text: str) -> str:
    return "'" + text.replace("'", "'\\''") + "'"


def wecker_aufwecken_planen(w: dict) -> None:
    """Plant das Aufwecken für einen Wecker neu (alte Planung wird vorher gelöscht)."""
    if w.get("wake"):
        pmset("cancel", "wake", w["wake"], PMSET_BESITZER)
        w["wake"] = ""
    aufwachen = datetime.fromisoformat(w["zeit"]) - timedelta(minutes=1)
    if aufwachen > datetime.now():
        zeitpunkt = aufwachen.strftime("%m/%d/%y %H:%M:%S")
        if pmset("wake", zeitpunkt, PMSET_BESITZER):
            w["wake"] = zeitpunkt


# --- Klingeln

def klingeln(beschreibung: str, ansage: str, max_sek: int) -> None:
    """Spielt Ton und Ansage im Wechsel mit Pausen, damit "Hey Atlas" dazwischen gehört wird."""
    stopp = threading.Event()
    with _uhr_sperre:
        _klingeln[beschreibung] = stopp
    # Mac während des Klingelns nicht wieder einschlafen lassen
    wach = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-d", "-t", str(max_sek)])
    try:
        ende = time.time() + max_sek
        while not stopp.is_set() and time.time() < ende:
            for _ in range(3):
                ton("Glass")
            if not stopp.is_set():
                sprich(ansage)
            stopp.wait(8)
    finally:
        wach.terminate()
        with _uhr_sperre:
            _klingeln.pop(beschreibung, None)


def klingeln_beenden() -> bool:
    """Stoppt alles, was gerade klingelt. True, wenn etwas geklingelt hat."""
    with _uhr_sperre:
        laeuft = list(_klingeln.values())
    for stopp in laeuft:
        stopp.set()
    return bool(laeuft)


def faellige_timer(jetzt: datetime) -> list:
    """Entfernt abgelaufene Timer und liefert ihre Klingel-Aufträge. Aufruf mit gehaltener _uhr_sperre."""
    abgelaufen = [name for name, ende in _timer.items() if ende <= jetzt]
    for name in abgelaufen:
        del _timer[name]
    return [(f"Timer {name}", f"Dein Timer {name} ist abgelaufen.", 60) for name in abgelaufen]


def faellige_wecker(jetzt: datetime) -> tuple:
    """Behandelt fällige Wecker: Serien rücken weiter, einmalige fallen weg. Nach dem Ruhezustand verpasste
    Wecker klingeln nur, wenn sie frisch sind. Liefert (Klingel-Aufträge, ob sich die Wecker geändert haben).
    Aufruf mit gehaltener _uhr_sperre."""
    auftraege = []
    faellig = [w for w in _wecker if datetime.fromisoformat(w["zeit"]) <= jetzt]
    for w in faellig:
        zeit = datetime.fromisoformat(w["zeit"])
        if jetzt - zeit < VERPASST_TOLERANZ:
            auftraege.append((f"Wecker {w['name']}", f"{w['name']}! Es ist {zeit:%H:%M} Uhr.", 600))
        if w["tage"]:
            w["zeit"] = naechstes_vorkommen(zeit, w["tage"]).isoformat(timespec="minutes")
            wecker_aufwecken_planen(w)
        else:
            _wecker.remove(w)
    return auftraege, bool(faellig)


def uhr_schleife() -> None:
    """Prüft jede Sekunde Timer und Wecker. Nutzt die Wanduhr, damit nach dem Ruhezustand nichts verrutscht."""
    while True:
        jetzt = datetime.now()
        with _uhr_sperre:
            wecker_auftraege, geaendert = faellige_wecker(jetzt)
            faellig = faellige_timer(jetzt) + wecker_auftraege
            if geaendert:
                wecker_speichern()
        for beschreibung, ansage, max_sek in faellig:
            threading.Thread(target=klingeln, args=(beschreibung, ansage, max_sek), daemon=True).start()
        time.sleep(1)


def wann(zeit: datetime) -> str:
    return f"{WOCHENTAGE[zeit.weekday()].capitalize()} {zeit:%d.%m. %H:%M} Uhr"


def dauer_gesprochen(sekunden: float) -> str:
    """Wie dauer_text, aber zum Vorlesen: ausgeschriebene Einheiten, gerundet."""
    sekunden = max(0, int(round(sekunden)))
    if sekunden < 60:
        return f"{sekunden} Sekunden"
    minuten = round(sekunden / 60)
    if minuten < 60:
        return "eine Minute" if minuten == 1 else f"{minuten} Minuten"
    h, m = divmod(minuten, 60)
    return ("eine Stunde" if h == 1 else f"{h} Stunden") + (f" {m} Minuten" if m else "")


def dauer_text(sekunden: float) -> str:
    sekunden = max(0, int(round(sekunden)))
    h, rest = divmod(sekunden, 3600)
    m, s = divmod(rest, 60)
    teile = [f"{h} Std."] * bool(h) + [f"{m} Min."] * bool(m) + [f"{s} Sek."] * bool(s and not h)
    return " ".join(teile) or "0 Sek."


@beta_tool
def timer_stellen(dauer_sekunden: int, name: str = "Timer") -> str:
    """Startet einen Timer. Mehrere Timer mit verschiedenen Namen können gleichzeitig laufen.

    Args:
        dauer_sekunden: Dauer in Sekunden, z. B. 600 für zehn Minuten.
        name: Kurzer Name, z. B. "Nudeln". Ein Timer gleichen Namens wird ersetzt.
    """
    if dauer_sekunden <= 0:
        return "Die Dauer muss positiv sein."
    with _uhr_sperre:
        _timer[name] = datetime.now() + timedelta(seconds=dauer_sekunden)
    return f"Timer '{name}' gestartet, Dauer {dauer_text(dauer_sekunden)}"


@beta_tool
def timer_abfragen() -> str:
    """Listet laufende Timer mit Restzeit auf."""
    jetzt = datetime.now()
    with _uhr_sperre:
        laufend = sorted(_timer.items(), key=lambda t: t[1])
    if not laufend:
        return "Es läuft kein Timer."
    return "\n".join(f"{name}: noch {dauer_text((ende - jetzt).total_seconds())}" for name, ende in laufend)


@beta_tool
def timer_abbrechen(name: Optional[str] = None) -> str:
    """Bricht einen laufenden Timer ab, oder alle, wenn kein Name angegeben ist.

    Args:
        name: Name des Timers; weglassen für alle.
    """
    with _uhr_sperre:
        namen = [n for n in _timer if name is None or n.lower() == name.lower()]
        for n in namen:
            del _timer[n]
    return f"Abgebrochen: {', '.join(namen)}." if namen else "Kein passender Timer läuft."


@beta_tool
def klingeln_stoppen() -> str:
    """Schaltet einen gerade klingelnden Wecker oder abgelaufenen Timer aus."""
    return "Ausgeschaltet." if klingeln_beenden() else "Gerade klingelt nichts."


@beta_tool
def wecker_stellen(zeitpunkt: str, name: str = "Wecker",
                   wochentage: Optional[list[Literal["mo", "di", "mi", "do", "fr", "sa", "so"]]] = None) -> str:
    """Stellt einen Wecker, einmalig oder wiederkehrend an bestimmten Wochentagen. Bleibt nach einem Neustart erhalten.

    Args:
        zeitpunkt: Nächster Weckzeitpunkt im ISO-Format, z. B. 2026-10-12T06:30.
        name: Kurzer Name, der beim Klingeln angesagt wird, z. B. "Aufstehen".
        wochentage: Für wiederkehrende Wecker die Tage, z. B. ["mo", "di", "mi", "do", "fr"]; weglassen für einmalig.
    """
    zeit = datetime.fromisoformat(zeitpunkt).replace(second=0, microsecond=0)
    tage = list(wochentage or [])
    if tage:
        zeit = naechstes_vorkommen(zeit, tage)  # bleibt gleich, wenn der Zeitpunkt schon passt
    elif zeit <= datetime.now():
        return "Dieser Zeitpunkt liegt in der Vergangenheit."
    with _uhr_sperre:
        nummer = max([w["id"] for w in _wecker], default=0) + 1
        w = {"id": nummer, "name": name, "zeit": zeit.isoformat(timespec="minutes"), "tage": tage, "wake": ""}
        wecker_aufwecken_planen(w)
        _wecker.append(w)
        wecker_speichern()
    wiederholung = f", jeden {', '.join(tage)}" if tage else ""
    hinweis = "" if w["wake"] else " Hinweis: Der Mac weckt sich dafür nicht selbst auf (pmset nicht eingerichtet)."
    return f"Wecker W{nummer} '{name}' gestellt auf {wann(zeit)}{wiederholung}.{hinweis}"


@beta_tool
def wecker_auflisten() -> str:
    """Listet alle gestellten Wecker mit Kennung wie W1 auf."""
    with _uhr_sperre:
        alle = sorted(_wecker, key=lambda w: w["zeit"])
    if not alle:
        return "Es ist kein Wecker gestellt."
    return "\n".join(f"[W{w['id']}] {w['name']}: {wann(datetime.fromisoformat(w['zeit']))}"
                     + (f", wiederholt {', '.join(w['tage'])}" if w["tage"] else "") for w in alle)


@beta_tool
def wecker_loeschen(kennung: str) -> str:
    """Löscht einen Wecker. Bei wiederkehrenden Weckern fällt die ganze Serie weg.

    Args:
        kennung: Kennung aus wecker_auflisten, z. B. W2.
    """
    nummer = kennung.strip().upper().lstrip("W")
    with _uhr_sperre:
        passend = [w for w in _wecker if str(w["id"]) == nummer]
        if not passend:
            return f"Unbekannte Kennung {kennung}. Schau mit wecker_auflisten nach."
        w = passend[0]
        if w.get("wake"):
            pmset("cancel", "wake", w["wake"], PMSET_BESITZER)
        _wecker.remove(w)
        wecker_speichern()
    return f"Wecker '{w['name']}' gelöscht."


# ---------------------------------------------------------------- Notizen

@beta_tool
def notiz_anlegen(titel: str, text: str) -> str:
    """Legt eine Notiz in der Notizen-App an (Standardordner des Standardaccounts).

    Args:
        titel: Überschrift der Notiz.
        text: Inhalt der Notiz.
    """
    osascript("""on run argv
    tell application "Notes"
        tell default account
            make new note at default folder with properties {name:(item 1 of argv), body:(item 2 of argv)}
        end tell
    end tell
end run""", titel, text)
    return f"Notiz '{titel}' angelegt."


@beta_tool
def notizen_suchen(suche: str) -> str:
    """Sucht Notizen, deren Titel den Suchbegriff enthält, und liefert bis zu fünf davon mit Inhalt.

    Args:
        suche: Suchbegriff im Titel, z. B. "Einkauf".
    """
    ergebnis = osascript("""on run argv
    set ausgabe to ""
    set anzahl to 0
    tell application "Notes"
        repeat with n in (notes whose name contains (item 1 of argv))
            set ausgabe to ausgabe & "### " & (name of n) & linefeed & (plaintext of n) & linefeed
            set anzahl to anzahl + 1
            if anzahl = 5 then exit repeat
        end repeat
    end tell
    return ausgabe
end run""", suche)
    return ergebnis[:4000] or f"Keine Notiz mit '{suche}' im Titel."


# ---------------------------------------------------------------- iMessage

_adressbuch = {"bis": 0.0, "kontakte": []}


def adressbuch() -> list:
    """Alle Kontakte mit Telefonnummer oder E-Mail, direkt über Apples Kontakte-Schnittstelle (die Kontakte-App
    muss dafür nicht laufen). [(Name, [Telefonnummern], [E-Mail-Adressen])], 10 Minuten zwischengespeichert."""
    if time.time() < _adressbuch["bis"]:
        return _adressbuch["kontakte"]
    import Contacts

    if Contacts.CNContactStore.authorizationStatusForEntityType_(Contacts.CNEntityTypeContacts) != \
            Contacts.CNAuthorizationStatusAuthorized:
        raise RuntimeError("Kein Zugriff auf die Kontakte. Systemeinstellungen → Datenschutz & Sicherheit → "
                           "Kontakte → heyAtlas einschalten.")
    schluessel = [Contacts.CNContactGivenNameKey, Contacts.CNContactFamilyNameKey,
                  Contacts.CNContactOrganizationNameKey, Contacts.CNContactPhoneNumbersKey,
                  Contacts.CNContactEmailAddressesKey]
    anfrage = Contacts.CNContactFetchRequest.alloc().initWithKeysToFetch_(schluessel)
    kontakte = []

    def sammeln(kontakt, _stopp):
        name = f"{kontakt.givenName()} {kontakt.familyName()}".strip() or str(kontakt.organizationName())
        nummern = [str(t.value().stringValue()) for t in kontakt.phoneNumbers()]
        mails = [str(m.value()) for m in kontakt.emailAddresses()]
        if name and (nummern or mails):
            kontakte.append((name, nummern, mails))

    Contacts.CNContactStore.alloc().init().enumerateContactsWithFetchRequest_error_usingBlock_(anfrage, None, sammeln)
    _adressbuch.update(bis=time.time() + 600, kontakte=kontakte)
    return kontakte


def kontakte_erlauben() -> str:
    import Contacts

    speicher = Contacts.CNContactStore.alloc().init()
    if Contacts.CNContactStore.authorizationStatusForEntityType_(Contacts.CNEntityTypeContacts) == \
            Contacts.CNAuthorizationStatusNotDetermined:
        fertig, ergebnis = threading.Event(), {}

        def antwort(erlaubt, _fehler):
            ergebnis["erlaubt"] = erlaubt
            fertig.set()

        speicher.requestAccessForEntityType_completionHandler_(Contacts.CNEntityTypeContacts, antwort)
        fertig.wait(300)
    erlaubt = Contacts.CNContactStore.authorizationStatusForEntityType_(Contacts.CNEntityTypeContacts) == \
        Contacts.CNAuthorizationStatusAuthorized
    return "erlaubt" if erlaubt else "abgelehnt"


def kontakte_suchen(name: str) -> list:
    """Kontakte, deren Name den Suchbegriff enthält. [(Name, [Telefonnummern], [E-Mail-Adressen])]"""
    return [k for k in adressbuch() if name.lower() in k[0].lower()]


def eindeutiger_kontakt(name: str, nur_mail: bool = False, nur_telefon: bool = False):
    """Ein passender Kontakt mit Adresse, oder ein Text für Claude, warum nicht."""
    kontakte = [(n, (m if nur_mail else t if nur_telefon else t + m)) for n, t, m in kontakte_suchen(name)]
    kontakte = [(n, adressen) for n, adressen in kontakte if adressen]
    art = "E-Mail-Adresse" if nur_mail else "Telefonnummer" if nur_telefon else "Telefonnummer oder E-Mail"
    if not kontakte:
        return None, f"Kein Kontakt '{name}' mit {art} gefunden."
    if len(kontakte) > 1:
        return None, "Mehrere Kontakte passen, frag nach, wer gemeint ist: " + ", ".join(n for n, _ in kontakte[:6])
    return kontakte[0], ""


# Erst iMessage, sonst SMS (SMS nur mit SMS-Weiterleitung auf dem iPhone, siehe README); argv: Adresse, Text, nur SMS
NACHRICHT_SENDEN = """on run argv
    tell application "Messages"
        if (item 3 of argv) is "0" then
            try
                send (item 2 of argv) to participant (item 1 of argv) of (1st account whose service type = iMessage)
                return "iMessage"
            end try
        end if
        send (item 2 of argv) to participant (item 1 of argv) of (1st account whose service type = SMS)
        return "SMS"
    end tell
end run"""


@beta_tool
def nachricht_senden(empfaenger: str, text: str, per_sms: bool = False) -> str:
    """Schickt eine Nachricht an einen Kontakt aus der Kontakte-App, als iMessage oder, wenn das nicht geht, als
    SMS. Fragt vorher per Sprache nach.

    Args:
        empfaenger: Name des Kontakts, wie er in Kontakte steht, z. B. "Anna" oder "Anna Müller".
        text: Text der Nachricht.
        per_sms: True, wenn der Nutzer ausdrücklich eine SMS will.
    """
    kontakt, problem = eindeutiger_kontakt(empfaenger, nur_telefon=per_sms)
    if not kontakt:
        return problem
    name, adressen = kontakt
    if not bestaetigen(f"Soll ich {name} schreiben: {text}?"):
        return "Der Nutzer hat abgelehnt, die Nachricht wurde nicht gesendet."
    try:
        dienst = osascript(NACHRICHT_SENDEN, adressen[0], text, "1" if per_sms else "0")
    except RuntimeError as fehler:
        raise RuntimeError(f"Senden fehlgeschlagen. Für SMS muss auf dem iPhone die SMS-Weiterleitung für diesen "
                           f"Mac an sein. ({fehler})") from fehler
    return f"Nachricht an {name} als {dienst} gesendet."


# ---------------------------------------------------------------- iMessages und SMS lesen (optional)

NACHRICHTEN_DB = os.path.expanduser("~/Library/Messages/chat.db")
APPLE_EPOCHE = 978307200  # 2001-01-01 in Unix-Sekunden; Nachrichten zählt Nanosekunden ab dort

NACHRICHTEN_ABFRAGE = """
SELECT m.ROWID, m.text, m.attributedBody, m.date, m.is_read, m.service, h.id, c.guid, c.display_name
FROM message m
LEFT JOIN handle h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join j ON j.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = j.chat_id
WHERE m.is_from_me = 0 AND m.date > ? AND (? = 0 OR m.is_read = 0)
  AND (m.associated_message_type IS NULL OR m.associated_message_type = 0)
ORDER BY m.date DESC LIMIT 200
"""

_verzeichnis = {"bis": 0.0, "eintraege": {}}
_letzte_imessages: dict = {}  # Kennung N1… -> (Chat-Kennung, Absender, Name)


def handle_schluessel(adresse: str) -> str:
    """Vergleichbare Form: E-Mails klein, Telefonnummern als letzte 9 Ziffern (+49 170 … und 0170 … gleich)."""
    if "@" in adresse:
        return adresse.strip().lower()
    return re.sub(r"\D", "", adresse)[-9:]


def kontaktname(adresse: str) -> str:
    """Name zur Nummer oder Adresse; ohne Kontakte-Zugriff bleibt es bei der Nummer statt eines Fehlers."""
    if time.time() > _verzeichnis["bis"]:
        try:
            eintraege = {handle_schluessel(w): name for name, nummern, mails in adressbuch() for w in nummern + mails}
        except RuntimeError as fehler:
            print(f"Kontaktnamen: {fehler}", file=sys.stderr)
            eintraege = {}
        _verzeichnis.update(bis=time.time() + 600, eintraege=eintraege)
    return _verzeichnis["eintraege"].get(handle_schluessel(adresse or ""), adresse or "unbekannt")


def nachrichtentext(text: Optional[str], attributed: Optional[bytes]) -> str:
    """Neuere macOS-Versionen speichern den Text oft nur in attributedBody (NSAttributedString, typedstream)."""
    if text:
        return text
    if not attributed:
        return ""
    from Foundation import NSData, NSUnarchiver

    try:
        objekt = NSUnarchiver.unarchiveObjectWithData_(NSData.dataWithBytes_length_(attributed, len(attributed)))
        return str(objekt.string()) if objekt is not None else ""
    except Exception:  # beschädigte oder unbekannte Einträge überspringen statt abzubrechen
        return ""


KEIN_FESTPLATTENZUGRIFF = ("Kein Zugriff auf die Nachrichten. heyAtlas braucht Festplattenvollzugriff: "
                           "Systemeinstellungen → Datenschutz & Sicherheit → Festplattenvollzugriff.")


def nachrichten_db_lesbar() -> bool:
    """Prüft nur die Berechtigung: Ohne Festplattenvollzugriff verweigert macOS schon das Öffnen der Datei."""
    try:
        with open(NACHRICHTEN_DB, "rb") as datei:
            datei.read(16)
        return True
    except OSError:
        return False


def _nachrichten_abfragen(pfad: str, parameter: tuple) -> list:
    import sqlite3

    verbindung = sqlite3.connect(f"file:{pfad}?mode=ro", uri=True)
    try:
        return verbindung.execute(NACHRICHTEN_ABFRAGE, parameter).fetchall()
    finally:
        verbindung.close()


def nachrichten_db_lesen(seit_tagen: int, nur_ungelesen: bool) -> list:
    import shutil
    import sqlite3
    import tempfile

    if not nachrichten_db_lesbar():
        raise RuntimeError(KEIN_FESTPLATTENZUGRIFF)
    seit = (time.time() - max(0, seit_tagen) * 86400 - APPLE_EPOCHE) * 1_000_000_000
    parameter = (seit, 1 if nur_ungelesen else 0)
    try:
        return _nachrichten_abfragen(NACHRICHTEN_DB, parameter)
    except sqlite3.DatabaseError as fehler:
        # Nur-Lesen kann an den Zwischendateien (-wal, -shm) der Nachrichten-App scheitern, etwa wenn sie nicht
        # läuft. Dann eine Kopie samt Zwischendateien lesen: die neuesten Nachrichten sind dabei, das Original bleibt
        # unberührt.
        print(f"Nachrichten-Datenbank direkt nicht lesbar ({fehler}), lese eine Kopie", file=sys.stderr)
        with tempfile.TemporaryDirectory() as ordner:
            for endung in ("", "-wal", "-shm"):
                if os.path.exists(NACHRICHTEN_DB + endung):
                    shutil.copy2(NACHRICHTEN_DB + endung, os.path.join(ordner, "chat.db" + endung))
            return _nachrichten_abfragen(os.path.join(ordner, "chat.db"), parameter)


@beta_tool
def imessages_lesen(nur_ungelesen: bool = True, seit_tagen: int = 2, kontakt: Optional[str] = None,
                    anzahl: int = 10) -> str:
    """Liest empfangene iMessages und SMS, neueste zuerst. Kennungen wie N1 dienen zum Antworten.

    Args:
        nur_ungelesen: Nur ungelesene Nachrichten.
        seit_tagen: Zeitraum in Tagen bis heute.
        kontakt: Nur von diesem Kontakt, z. B. "Anna".
        anzahl: Höchstens so viele Nachrichten.
    """
    zeilen = []
    _letzte_imessages.clear()
    for _, text, attributed, datum, gelesen, dienst, absender, chat_guid, gruppe in \
            nachrichten_db_lesen(seit_tagen, nur_ungelesen):
        inhalt, name = nachrichtentext(text, attributed).strip(), kontaktname(absender)
        if not inhalt or (kontakt and kontakt.lower() not in f"{name} {absender}".lower()):
            continue
        nummer = f"N{len(zeilen) + 1}"
        _letzte_imessages[nummer] = (chat_guid, absender, name, dienst)
        wann = datetime.fromtimestamp(datum / 1_000_000_000 + APPLE_EPOCHE)
        ort = f" in „{gruppe}“" if gruppe else ""
        zeilen.append(f"[{nummer}] {wann:%d.%m. %H:%M} von {name}{ort} ({dienst}"
                      f"{', ungelesen' if not gelesen else ''}): {inhalt}")
        if len(zeilen) >= anzahl:
            break
    return "\n".join(zeilen) or "Keine passenden Nachrichten."


@beta_tool
def imessage_antworten(kennung: str, text: str) -> str:
    """Antwortet im selben Chat auf eine mit imessages_lesen gelesene Nachricht. Fragt vorher per Sprache nach.

    Args:
        kennung: Kennung aus imessages_lesen, z. B. N1.
        text: Antworttext.
    """
    eintrag = _letzte_imessages.get(kennung.strip().upper())
    if eintrag is None:
        return f"Unbekannte Kennung {kennung}. Lies die Nachrichten zuerst mit imessages_lesen."
    chat_guid, absender, name, dienst = eintrag
    if not bestaetigen(f"Soll ich {name} antworten: {text}?"):
        return "Der Nutzer hat abgelehnt, die Antwort wurde nicht gesendet."
    if chat_guid:  # im selben Chat, auch in Gruppen und mit demselben Dienst
        osascript('on run argv\ntell application "Messages" to send (item 1 of argv) to chat id (item 2 of argv)'
                  '\nend run', text, chat_guid)
    else:
        osascript(NACHRICHT_SENDEN, absender, text, "1" if dienst == "SMS" else "0")
    return f"Antwort an {name} gesendet."


# ---------------------------------------------------------------- Mail

_letzte_mails: dict = {}  # Kennung M1… -> Mail-ID im Posteingang

# Stufe 1: alle passenden Mails ohne Text (schnell); argv: Absender, Betreff, nur ungelesen (1/0), Datum (5 Teile).
# Absender und Betreff werden im Skript gefiltert: AppleScripts "contains" ignoriert Groß- und Kleinschreibung.
# Achtung: In AppleScript ist 'x contains ""' falsch, leere Filter werden deshalb ausdrücklich übersprungen.
MAIL_LISTE = DATUM_HELFER + """
on run argv
    set feld to character id 31
    set satz to character id 30
    set seit to mkdate(item 4 of argv, item 5 of argv, item 6 of argv, item 7 of argv, item 8 of argv)
    set ausgabe to ""
    tell application "Mail"
        if (item 3 of argv) is "1" then
            set kandidaten to (messages of inbox whose read status is false and date received ≥ seit)
        else
            set kandidaten to (messages of inbox whose date received ≥ seit)
        end if
        repeat with m in kandidaten
            set absender to sender of m
            set betreff to subject of m
            if ((item 1 of argv) is "" or absender contains (item 1 of argv)) and ¬
                ((item 2 of argv) is "" or betreff contains (item 2 of argv)) then
                set ausgabe to ausgabe & (id of m) & feld & absender & feld & betreff & feld & ¬
                    ((date received of m) as «class isot» as string) & feld & (read status of m) & satz
            end if
        end repeat
    end tell
    return ausgabe
end run"""

# Stufe 2: Text einer Mail; argv: Mail-ID, Länge (0 = ganz), als gelesen markieren (1/0)
MAIL_TEXT = """on run argv
    tell application "Mail"
        set m to first message of inbox whose id is ((item 1 of argv) as integer)
        set inhalt to content of m
        set laenge to (item 2 of argv) as integer
        if laenge > 0 and (length of inhalt) > laenge then set inhalt to text 1 thru laenge of inhalt
        if (item 3 of argv) is "1" then set read status of m to true
        return inhalt
    end tell
end run"""

# Ab hier beginnt bei Antworten meist der zitierte Verlauf, der beim Vorlesen nur stört
ZITAT_BEGINN = re.compile(r"^(>|Am .+ schrieb .+:|On .+ wrote:|-{2,} ?(Ursprüngliche|Original) )", re.M)


def mails_abfragen(absender: str = "", betreff: str = "", nur_ungelesen: bool = False,
                   seit_tagen: int = 7, anzahl: int = 10) -> str:
    """Gemeinsame Abfrage für mails_lesen und mails_suchen: neueste zuerst, mit Kennungen M1, M2 …"""
    seit = (datetime.now() - timedelta(days=max(0, seit_tagen))).replace(hour=0, minute=0, second=0, microsecond=0)
    roh = osascript(MAIL_LISTE, absender, betreff, "1" if nur_ungelesen else "0", *datum_teile(seit.isoformat()))
    mails = [z.split("\x1f") for z in roh.split("\x1e") if z.count("\x1f") == 4]
    mails.sort(key=lambda m: m[3], reverse=True)
    _letzte_mails.clear()
    zeilen = []
    for nummer, (mail_id, von, titel, datum, gelesen) in enumerate(mails[:max(1, min(anzahl, 25))], 1):
        _letzte_mails[f"M{nummer}"] = mail_id
        anfang = " ".join(osascript(MAIL_TEXT, mail_id, "300", "0").split())
        markierung = "" if gelesen == "true" else " | ungelesen"
        wann = datum[:16].replace("T", " ")
        zeilen.append(f"[M{nummer}] {wann} | von {von} | {titel}{markierung}\n    {anfang}")
    if len(mails) > len(zeilen):
        zeilen.append(f"(insgesamt {len(mails)} Treffer, gezeigt die {len(zeilen)} neuesten)")
    return "\n".join(zeilen)


@beta_tool
def mails_lesen(anzahl: int = 10) -> str:
    """Liest ungelesene Mails im Posteingang der letzten 30 Tage (Absender, Betreff, Datum, Anfang des Textes),
    neueste zuerst. Kennungen wie M1 dienen zum Vorlesen und Antworten.

    Args:
        anzahl: Höchstens so viele Mails.
    """
    return mails_abfragen(nur_ungelesen=True, seit_tagen=30, anzahl=anzahl) or "Keine ungelesenen Mails."


@beta_tool
def mails_suchen(absender: Optional[str] = None, betreff: Optional[str] = None, seit_tagen: int = 7,
                 nur_ungelesen: bool = False, anzahl: int = 10) -> str:
    """Sucht Mails im Posteingang, auch bereits gelesene, neueste zuerst. Kennungen wie M1 dienen zum Vorlesen
    und Antworten.

    Args:
        absender: Teil von Name oder Adresse des Absenders, z. B. "Bank" oder "anna@".
        betreff: Teil des Betreffs, z. B. "Rechnung".
        seit_tagen: Zeitraum in Tagen bis heute, z. B. 7 für die letzte Woche.
        nur_ungelesen: Nur ungelesene Mails.
        anzahl: Höchstens so viele Mails.
    """
    ergebnis = mails_abfragen(absender or "", betreff or "", nur_ungelesen, seit_tagen, anzahl)
    return ergebnis or "Keine passenden Mails gefunden."


@beta_tool
def mail_vorlesen(kennung: str) -> str:
    """Liefert den ganzen Text einer zuvor gelesenen oder gesuchten Mail zum Vorlesen, ohne zitierten Verlauf,
    und markiert sie als gelesen.

    Args:
        kennung: Kennung aus mails_lesen oder mails_suchen, z. B. M2.
    """
    mail_id = _letzte_mails.get(kennung.strip().upper())
    if mail_id is None:
        return f"Unbekannte Kennung {kennung}. Hol die Mails zuerst mit mails_lesen oder mails_suchen."
    text = osascript(MAIL_TEXT, mail_id, "0", "1")
    zitat = ZITAT_BEGINN.search(text)
    if zitat:
        text = text[:zitat.start()]
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > 3000:
        return text[:3000] + "\n[… gekürzt, die Mail ist länger. Frag, ob du den Rest zusammenfassen sollst.]"
    return text or "Die Mail enthält keinen Text."


@beta_tool
def mail_antworten(kennung: str, text: str, senden: bool = False) -> str:
    """Antwortet auf eine mit mails_lesen oder mails_suchen gefundene Mail. Standard ist ein Entwurf, der offen
    in Mail liegt; mit senden=True wird nach Rückfrage per Sprache sofort gesendet.

    Args:
        kennung: Kennung aus mails_lesen, z. B. M2.
        text: Antworttext.
        senden: Nur True, wenn der Nutzer ausdrücklich sofort senden will.
    """
    mail_id = _letzte_mails.get(kennung.strip().upper())
    if mail_id is None:
        return f"Unbekannte Kennung {kennung}. Lies die Mails zuerst mit mails_lesen."
    if senden and not bestaetigen(f"Soll ich die Antwort jetzt senden: {text}?"):
        senden = False
    osascript("""on run argv
    tell application "Mail"
        set m to first message of inbox whose id is ((item 1 of argv) as integer)
        set r to reply m with opening window
        delay 0.5
        set content of r to (item 2 of argv) & return & return & (content of r)
        if (item 3 of argv) is "1" then send r
    end tell
end run""", mail_id, text, "1" if senden else "0")
    return "Antwort gesendet." if senden else "Antwort liegt als Entwurf offen in Mail."


@beta_tool
def mail_schreiben(empfaenger: str, betreff: str, text: str, senden: bool = False) -> str:
    """Schreibt eine neue Mail. Standard ist ein Entwurf, der offen in Mail liegt; mit senden=True wird nach
    Rückfrage per Sprache sofort gesendet.

    Args:
        empfaenger: Name aus den Kontakten oder eine E-Mail-Adresse.
        betreff: Betreff.
        text: Text der Mail.
        senden: Nur True, wenn der Nutzer ausdrücklich sofort senden will.
    """
    if "@" in empfaenger:
        name, adresse = empfaenger, empfaenger
    else:
        kontakt, problem = eindeutiger_kontakt(empfaenger, nur_mail=True)
        if not kontakt:
            return problem
        name, adresse = kontakt[0], kontakt[1][0]
    if senden and not bestaetigen(f"Soll ich die Mail an {name} mit dem Betreff {betreff} jetzt senden?"):
        senden = False
    osascript("""on run argv
    tell application "Mail"
        set r to make new outgoing message with properties ¬
            {subject:(item 2 of argv), content:(item 3 of argv), visible:true}
        tell r to make new to recipient at end of to recipients with properties {address:(item 1 of argv)}
        if (item 4 of argv) is "1" then send r
    end tell
end run""", adresse, betreff, text, "1" if senden else "0")
    return f"Mail an {name} gesendet." if senden else f"Entwurf an {name} liegt offen in Mail."


# ---------------------------------------------------------------- Mac-Steuerung

@beta_tool
def lautstaerke(prozent: Optional[int] = None,
                aktion: Optional[Literal["lauter", "leiser", "stumm", "ton_an"]] = None) -> str:
    """Stellt die Systemlautstärke des Macs.

    Args:
        prozent: Lautstärke 0 bis 100; oder stattdessen eine aktion.
        aktion: lauter, leiser (je 10 Prozentpunkte), stumm, ton_an.
    """
    if prozent is not None:
        osascript(f"set volume output volume {max(0, min(int(prozent), 100))}")
    elif aktion in ("lauter", "leiser"):
        schritt = 10 if aktion == "lauter" else -10
        osascript(f"set volume output volume ((output volume of (get volume settings)) + {schritt})")
    elif aktion == "stumm":
        osascript("set volume with output muted")
    elif aktion == "ton_an":
        osascript("set volume without output muted")
    else:
        return "Gib prozent oder aktion an."
    return f"Lautstärke jetzt {osascript('output volume of (get volume settings)')} Prozent."


@beta_tool
def app_oeffnen(name: str) -> str:
    """Öffnet ein Programm auf dem Mac, z. B. "Safari" oder "Notizen".

    Args:
        name: Name des Programms, wie er im Programme-Ordner steht.
    """
    ergebnis = subprocess.run(["/usr/bin/open", "-a", name], capture_output=True, text=True)
    return f"{name} ist offen." if ergebnis.returncode == 0 else f"Programm '{name}' nicht gefunden."


@beta_tool
def app_beenden(name: str) -> str:
    """Beendet ein laufendes Programm. Das Programm fragt selbst nach, falls etwas ungesichert ist.

    Args:
        name: Name des Programms.
    """
    lief = osascript("""on run argv
    if application (item 1 of argv) is running then
        tell application (item 1 of argv) to quit
        return "ja"
    end if
    return "nein"
end run""", name)
    return f"{name} beendet." if lief == "ja" else f"{name} läuft gar nicht."


@beta_tool
def dunkelmodus(modus: Literal["an", "aus", "umschalten"] = "umschalten") -> str:
    """Schaltet den Dunkelmodus von macOS.

    Args:
        modus: an, aus oder umschalten.
    """
    wert = {"an": "true", "aus": "false", "umschalten": "not dark mode"}[modus]
    osascript(f'tell application "System Events" to tell appearance preferences to set dark mode to {wert}')
    return "Dunkelmodus ist " + ("an." if osascript(
        'tell application "System Events" to tell appearance preferences to get dark mode') == "true" else "aus.")


@beta_tool
def bildschirm_sperren() -> str:
    """Schaltet den Bildschirm aus; mit Passwortabfrage beim Aufwachen ist der Mac damit gesperrt."""
    subprocess.run(["/usr/bin/pmset", "displaysleepnow"], check=True)
    return "Bildschirm ist aus."


# ---------------------------------------------------------------- Apple Music (eigene Mediathek)

@beta_tool
def apple_music_abspielen(suche: str) -> str:
    """Spielt einen Titel oder Künstler aus der eigenen Apple-Music-Mediathek. Der Streaming-Katalog ist per
    AppleScript nicht erreichbar.

    Args:
        suche: Teil des Titels oder Künstlernamens.
    """
    ergebnis = osascript("""on run argv
    tell application "Music"
        set treffer to (every track of library playlist 1 ¬
            whose name contains (item 1 of argv) or artist contains (item 1 of argv))
        if treffer is {} then return ""
        play item 1 of treffer
        return (name of item 1 of treffer) & " von " & (artist of item 1 of treffer)
    end tell
end run""", suche)
    if ergebnis:
        wiedergabe_gestartet()
    return f"Spiele {ergebnis}." if ergebnis else f"Nichts in der Mediathek gefunden für '{suche}'."


MUSIC_BEFEHLE = {
    "play": "play", "pause": "pause", "weiter": "next track", "zurueck": "previous track",
    "lauter": "set sound volume to (sound volume + 15)", "leiser": "set sound volume to (sound volume - 15)",
}


@beta_tool
def apple_music_steuern(aktion: Literal["play", "pause", "weiter", "zurueck", "lauter", "leiser"]) -> str:
    """Steuert die Wiedergabe in der Musik-App (Apple Music).

    Args:
        aktion: play, pause, weiter, zurueck, lauter, leiser.
    """
    osascript(f'tell application "Music" to {MUSIC_BEFEHLE[aktion]}')
    if aktion in ("play", "weiter", "zurueck"):
        wiedergabe_gestartet()
    elif aktion == "pause":
        wiedergabe_pausiert()
    return f"Erledigt: {aktion}."


# ---------------------------------------------------------------- YouTube

@beta_tool
def youtube_abspielen(suche: str) -> str:
    """Sucht ein YouTube-Video über die offizielle YouTube Data API und öffnet es im Browser.

    Args:
        suche: Suchbegriff, z. B. "Tagesschau in 100 Sekunden" oder "Pasta Carbonara Rezept".
    """
    schluessel = schluesselbund("youtube-api-key")
    if not schluessel:
        raise RuntimeError("YouTube-API-Schlüssel fehlt im Schlüsselbund (youtube-api-key).")
    url = "https://www.googleapis.com/youtube/v3/search?" + urllib.parse.urlencode({
        "part": "snippet", "q": suche, "type": "video", "maxResults": 1,
        "regionCode": "DE", "relevanceLanguage": "de", "key": schluessel})
    treffer = hole_json(url).get("items", [])
    if not treffer:
        return f"Kein Video gefunden für '{suche}'."
    video = treffer[0]
    adresse = f"https://www.youtube.com/watch?v={video['id']['videoId']}"
    subprocess.run(["/usr/bin/open", "-a", BROWSER, adresse], check=True)
    wiedergabe_gestartet()
    s = video["snippet"]
    return f"Öffne '{s['title']}' vom Kanal {s['channelTitle']}."


YOUTUBE_JS = {
    "pause": "v.pause()", "play": "v.play()",
    "vor": "v.currentTime += 10", "zurueck": "v.currentTime -= 10",
    "lauter": "v.volume = Math.min(1, v.volume + 0.2)", "leiser": "v.volume = Math.max(0, v.volume - 0.2)",
    # Browser erlauben Vollbild oft nur nach echter Nutzeraktion; dann passiert hier nichts
    "vollbild": "document.querySelector('.ytp-fullscreen-button').click()",
}


def youtube_js(js: str, timeout: float = 60) -> str:
    """Führt JavaScript im ersten YouTube-Tab aus. "kein Tab", wenn keiner offen ist oder der Browser nicht läuft
    (ein nicht laufender Browser wird bewusst nicht gestartet)."""
    if BROWSER == "Google Chrome":
        befehl = "execute t javascript (item 1 of argv)"
    else:
        befehl = "do JavaScript (item 1 of argv) in t"
    return osascript(f"""on run argv
    if application "{BROWSER}" is not running then return "kein Tab"
    tell application "{BROWSER}"
        repeat with w in windows
            repeat with t in tabs of w
                if URL of t contains "youtube.com/watch" then return {befehl}
            end repeat
        end repeat
    end tell
    return "kein Tab"
end run""", js, timeout=timeout)


@beta_tool
def youtube_steuern(aktion: Literal["pause", "play", "vor", "zurueck", "lauter", "leiser", "vollbild"]) -> str:
    """Steuert das offene YouTube-Video im Browser.

    Args:
        aktion: pause, play, vor (10 Sekunden), zurueck (10 Sekunden), lauter, leiser, vollbild.
    """
    js = ("(function(){var v=document.querySelector('video'); if(!v) return 'kein Video';"
          + YOUTUBE_JS[aktion] + "; return 'ok';})()")
    try:
        ergebnis = youtube_js(js)
    except RuntimeError as fehler:
        if "JavaScript" in str(fehler):
            raise RuntimeError(f"{BROWSER} erlaubt keine Steuerung. Einmalig einschalten: Safari → Einstellungen → "
                               "Erweitert → Funktionen für Webentwickler zeigen, dann Menü Entwickler → "
                               "JavaScript von Apple Events erlauben.") from fehler
        raise
    if ergebnis == "ok" and aktion == "pause":
        wiedergabe_pausiert()
    if ergebnis == "ok" and aktion in ("play", "vor", "zurueck"):
        wiedergabe_gestartet()
    return {"ok": f"Erledigt: {aktion}.", "kein Tab": "Es ist kein YouTube-Video offen.",
            "kein Video": "Auf der Seite läuft kein Video."}.get(ergebnis, ergebnis)


# ---------------------------------------------------------------- Nachrichten (Tagesschau)

@beta_tool
def nachrichten(anzahl: int = 5) -> str:
    """Aktuelle Schlagzeilen der Tagesschau mit dem ersten Satz.

    Args:
        anzahl: Anzahl Meldungen, 1 bis 10.
    """
    # Ohne Schrägstrich am Ende: mit Schrägstrich leitet der Server per 308 um, das kann urllib unter Python 3.9 nicht
    daten = hole_json("https://www.tagesschau.de/api2u/homepage", headers={"User-Agent": "heyAtlas/1.0"})
    zeilen = [f"- {n['title']}: {n.get('firstSentence') or n.get('topline') or ''}".strip()
              for n in daten.get("news", [])[:max(1, min(anzahl, 10))]]
    return "\n".join(zeilen) or "Keine Nachrichten abrufbar."


# ---------------------------------------------------------------- Fahrplan (Transitous)

# Freier, gemeinschaftlich betriebener Fahrplandienst mit Echtzeit. Nutzungsregeln: https://transitous.org/api/
# (Open Source, nicht-kommerziell, wenig Last, Kontakt im User-Agent, Quellenangabe transitous.org/sources)
TRANSITOUS = "https://api.transitous.org/api"


def transitous(pfad: str, **parameter) -> dict:
    url = f"{TRANSITOUS}/{pfad}?" + urllib.parse.urlencode({k: v for k, v in parameter.items() if v is not None})
    return hole_json(url, headers={"User-Agent": f"heyAtlas/0.1 ({FAHRPLAN_KONTAKT})"})


def ort_finden(name: str, nur_haltestellen: bool = False) -> Optional[dict]:
    """Haltestelle oder Adresse; ohne Stadtangabe zuerst in FAHRPLAN_STADT gesucht."""
    versuche = [name] if FAHRPLAN_STADT.lower() in name.lower() else [f"{FAHRPLAN_STADT} {name}", name]
    for text in versuche:
        treffer = transitous("v1/geocode", text=text, type="STOP" if nur_haltestellen else None)
        if treffer:
            return treffer[0]
    return None


def ort_parameter(ort: dict) -> str:
    """Haltestellen über ihre Kennung, Adressen über ihre Koordinaten."""
    return ort["id"] if ort.get("type") == "STOP" else f"{ort['lat']},{ort['lon']}"


def ortszeit(zeit: str) -> datetime:
    """Transitous liefert UTC ("…Z"); umgerechnet in die Zeitzone des Macs."""
    return datetime.fromisoformat(zeit.replace("Z", "+00:00")).astimezone()


def verspaetung(geplant: str, tatsaechlich: str) -> str:
    minuten = round((ortszeit(tatsaechlich) - ortszeit(geplant)).total_seconds() / 60)
    return f" (+{minuten} min)" if minuten > 0 else (f" ({minuten} min)" if minuten < 0 else "")


@beta_tool
def abfahrten(haltestelle: Optional[str] = None, linie: Optional[str] = None, anzahl: int = 6) -> str:
    """Nächste Abfahrten an einer Haltestelle mit Echtzeit (Straßenbahn, Bus, S-Bahn, Zug).

    Args:
        haltestelle: Name, z. B. "Augustusplatz"; weglassen für die Heimathaltestelle des Nutzers.
        linie: Nur diese Linie, z. B. "4" oder "S3".
        anzahl: Anzahl Abfahrten.
    """
    name = haltestelle or HEIMATHALTESTELLE
    if not name:
        return "Keine Haltestelle genannt und keine Heimathaltestelle eingestellt. Frag nach der Haltestelle."
    ort = ort_finden(name, nur_haltestellen=True)
    if not ort:
        return f"Haltestelle '{name}' nicht gefunden."
    daten = transitous("v5/stoptimes", stopId=ort["id"], n=max(1, min(anzahl, 10)) * (4 if linie else 1))
    zeilen = []
    for halt in daten.get("stopTimes", []):
        if linie and str(halt.get("routeShortName", "")).lower() != linie.lower():
            continue
        p = halt["place"]
        zeit = ortszeit(p.get("departure") or p["scheduledDeparture"])
        verzug = verspaetung(p["scheduledDeparture"], p["departure"]) if halt.get("realTime") else " (Plan)"
        zeilen.append(f"{halt['mode'].capitalize()} {halt.get('routeShortName', '')} → {halt.get('headsign', '')}: "
                      f"{zeit:%H:%M}{verzug}")
        if len(zeilen) >= anzahl:
            break
    kopf = f"Abfahrten {ort['name']}:"
    return "\n".join([kopf, *zeilen]) if zeilen else f"Keine passenden Abfahrten an {ort['name']} gefunden."


def _abschnitt(leg: dict) -> str:
    start, ende = ortszeit(leg["startTime"]), ortszeit(leg["endTime"])
    if leg["mode"] == "WALK":
        # Bei Adressen statt Haltestellen heißen Start und Ziel bei Transitous "START" und "END"
        ziel = {"END": "zum Ziel", "START": "zum Start"}.get(leg["to"]["name"], f"zu {leg['to']['name']}")
        return f"zu Fuß {round(leg.get('duration', 0) / 60)} min {ziel}"
    linie = f"{leg['mode'].capitalize()} {leg.get('routeShortName', '')}".strip()
    return (f"{linie} Richtung {leg.get('headsign', '')} ab {leg['from']['name']} {start:%H:%M}, "
            f"an {leg['to']['name']} {ende:%H:%M}")


@beta_tool
def verbindung_suchen(ziel: str, start: Optional[str] = None, zeitpunkt: Optional[str] = None,
                      ankunft: bool = False) -> str:
    """Sucht Verbindungen mit Bus und Bahn, mit Echtzeit und Fußwegen.

    Args:
        ziel: Haltestelle oder Adresse, z. B. "Hauptbahnhof" oder "Karl-Liebknecht-Str. 10".
        start: Startpunkt; weglassen für die Heimathaltestelle des Nutzers.
        zeitpunkt: Gewünschte Zeit im ISO-Format, z. B. 2026-10-12T08:30; weglassen für jetzt.
        ankunft: True, wenn zeitpunkt die gewünschte Ankunftszeit ist ("bis 9 Uhr dort sein").
    """
    start = start or HEIMATHALTESTELLE
    if not start:
        return "Kein Startpunkt genannt und keine Heimathaltestelle eingestellt. Frag nach dem Start."
    von, nach = ort_finden(start), ort_finden(ziel)
    if not von or not nach:
        return f"'{start if not von else ziel}' nicht gefunden."
    zeit = datetime.fromisoformat(zeitpunkt).astimezone().isoformat() if zeitpunkt else None
    daten = transitous("v5/plan", fromPlace=ort_parameter(von), toPlace=ort_parameter(nach), time=zeit,
                       arriveBy="true" if ankunft else None, numItineraries=3)
    zeilen = []
    for nummer, fahrt in enumerate(daten.get("itineraries", [])[:3], 1):
        los, an = ortszeit(fahrt["startTime"]), ortszeit(fahrt["endTime"])
        zeilen.append(f"{nummer}. los {los:%H:%M}, an {an:%H:%M}, {round(fahrt['duration'] / 60)} min, "
                      f"{fahrt.get('transfers', 0)} Umstiege: " + "; ".join(_abschnitt(leg) for leg in fahrt["legs"]))
    kopf = f"Von {von['name']} nach {nach['name']}:"
    return "\n".join([kopf, *zeilen]) if zeilen else "Keine Verbindung gefunden."


# ---------------------------------------------------------------- Berechtigungen beim ersten Start

# Apps, die Atlas per AppleScript steuert: (Anzeigename, App-Name)
AUTOMATION_ZIELE = [("Kalender", "Calendar"), ("Erinnerungen", "Reminders"), ("Notizen", "Notes"),
                    ("Mail", "Mail"), ("Nachrichten", "Messages"), ("Musik", "Music"),
                    ("Spotify", "Spotify"), (BROWSER, BROWSER), ("Systemereignisse", "System Events")]


def mikrofon_erlauben() -> str:
    import AVFoundation

    status = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio)
    if status == AVFoundation.AVAuthorizationStatusNotDetermined:
        fertig, ergebnis = threading.Event(), {}

        def antwort(erlaubt):
            ergebnis["erlaubt"] = erlaubt
            fertig.set()

        AVFoundation.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            AVFoundation.AVMediaTypeAudio, antwort)
        fertig.wait(300)
        return "erlaubt" if ergebnis.get("erlaubt") else "abgelehnt"
    return "erlaubt" if status == AVFoundation.AVAuthorizationStatusAuthorized else "abgelehnt"


def eventkit_erlauben(art: str) -> str:
    try:
        kalender_speicher(art)
        return "erlaubt"
    except RuntimeError:
        return "abgelehnt"


# Harmloser Befehl je App, den sie per AppleScript versteht (Spotify und Musik kennen keine Fenster)
TESTBEFEHLE = {"System Events": "count processes", "Spotify": "get player state", "Music": "get player state"}


def automation_erlauben(app: str) -> str:
    """Löst die Abfrage "heyAtlas möchte <App> steuern" aus. macOS fragt erst beim ersten echten Befehl, und dafür
    muss die App laufen; nicht laufende Apps startet Atlas dazu unsichtbar und beendet sie danach wieder."""
    if app != "System Events" and subprocess.run(["/usr/bin/open", "-Ra", app], capture_output=True).returncode:
        return "nicht installiert"
    lief = osascript(f'application "{app}" is running') == "true"
    if not lief:
        subprocess.run(["/usr/bin/open", "-g", "-j", "-a", app], capture_output=True)
        for _ in range(40):  # bis zu 10 Sekunden auf den Start warten
            if osascript(f'application "{app}" is running') == "true":
                break
            time.sleep(0.25)
    befehl = TESTBEFEHLE.get(app, "count windows")
    # Lange Wartezeit: Der Dialog bleibt offen, bis der Nutzer antwortet
    ergebnis = subprocess.run(["/usr/bin/osascript", "-e", f'tell application "{app}" to {befehl}'],
                              capture_output=True, text=True, timeout=300)
    if not lief and app != "System Events":
        app_beenden_ohne_steuerung(app)
    # Jeder andere Fehler als -1743 heißt: Der Befehl kam an, die Steuerung ist also erlaubt
    if ergebnis.returncode == 0 or "-1743" not in ergebnis.stderr and "-600" not in ergebnis.stderr:
        return "erlaubt"
    return "abgelehnt" if "-1743" in ergebnis.stderr else "nicht geprüft (App lief nicht)"


def app_beenden_ohne_steuerung(app: str) -> None:
    """Beendet eine App über macOS selbst statt per AppleScript; das klappt auch, wenn die Steuerung abgelehnt
    wurde. Die Kennung ("id of application") liest AppleScript aus dem App-Paket, ohne die App anzusprechen."""
    from AppKit import NSRunningApplication

    kennung = subprocess.run(["/usr/bin/osascript", "-e", f'id of application "{app}"'],
                             capture_output=True, text=True).stdout.strip()
    for laufend in NSRunningApplication.runningApplicationsWithBundleIdentifier_(kennung) if kennung else []:
        laufend.terminate()


def festplattenvollzugriff_pruefen() -> str:
    """Festplattenvollzugriff kann keine App abfragen, nur prüfen; erteilt wird er in den Systemeinstellungen."""
    return "erlaubt" if nachrichten_db_lesbar() else "abgelehnt"


def berechtigungen_einrichten() -> list:
    """Fragt alle Zugriffe nacheinander ab. Liefert [(Bereich, Ergebnis)] für die Übersicht."""
    schritte = [("Mikrofon", mikrofon_erlauben),
                ("Kalender (Vollzugriff)", lambda: eventkit_erlauben("termine")),
                ("Erinnerungen (Vollzugriff)", lambda: eventkit_erlauben("erinnerungen")),
                ("Kontakte", kontakte_erlauben)]
    schritte += [(f"Steuerung: {anzeige}", lambda app=app: automation_erlauben(app))
                 for anzeige, app in AUTOMATION_ZIELE]
    if NACHRICHTEN_LESEN:
        schritte.append(("Festplattenvollzugriff (Nachrichten)", festplattenvollzugriff_pruefen))
    if ANRUFE_ANNEHMEN:
        schritte.append(("Bedienungshilfen (Anrufe)",
                         lambda: "erlaubt" if bedienungshilfen_erlaubt(nachfragen=True) else "abgelehnt"))
        schritte.append(("Bildschirmaufnahme (Anrufe)", bildschirmaufnahme_erlauben))
    ergebnisse = []
    for nummer, (bereich, abfrage) in enumerate(schritte, 1):
        melde_status(f"Berechtigungen {nummer}/{len(schritte)}: {bereich}")
        try:
            ergebnisse.append((bereich, abfrage()))
        except Exception as fehler:  # ein fehlgeschlagener Schritt soll die übrigen nicht verhindern
            ergebnisse.append((bereich, f"Fehler: {str(fehler)[:60]}"))
        print(f"Berechtigung {bereich}: {ergebnisse[-1][1]}")
    return ergebnisse


# ---------------------------------------------------------------- Anrufe annehmen (optional)

# Atlas bedient die Anrufanzeige von macOS per Bedienungshilfen, wie ein Klick. Apple bietet dafür keine Schnittstelle;
# der Aufbau der Anzeige kann sich mit macOS-Updates ändern. Knopfnamen deutsch und englisch.
ANNEHMEN = ("Annehmen", "Accept", "Answer")
ABLEHNEN = ("Ablehnen", "Decline")

# Direkt über die Bedienungshilfen-Schnittstelle statt per AppleScript: Bei AppleScript prüft macOS die
# Berechtigung von osascript statt von heyAtlas ("osascript is not allowed assistive access", -25211).
ANRUF_APPS = {"com.apple.notificationcenterui": "NotificationCenter", "com.apple.FaceTime": "FaceTime"}
# So kennzeichnet macOS 26 die Anruf-Mitteilung (aus dem Log eines echten Anrufs); ihr Inhalt erscheint verzögert
ANRUF_KENNUNG = "FACETIME_NOTIFICATION"
AX_KEIN_ZUGRIFF = -25211


def _ax(element, attribut):
    import ApplicationServices

    fehler, wert = ApplicationServices.AXUIElementCopyAttributeValue(element, attribut, None)
    if fehler == AX_KEIN_ZUGRIFF:
        raise RuntimeError(f"Bedienungshilfen fehlen ({AX_KEIN_ZUGRIFF})")
    return wert if fehler == 0 else None


def _ax_aktionen(element) -> list:
    """Benannte Aktionen eines Elements als [(Beschriftung, Aktionsname)]. Mitteilungen stellen ihre Knöpfe oft so
    bereit statt als echte Knöpfe, im Format "Name:Annehmen\nTarget:0x0\nSelector:(null)"."""
    import ApplicationServices

    fehler, namen = ApplicationServices.AXUIElementCopyActionNames(element, None)
    if fehler == AX_KEIN_ZUGRIFF:
        raise RuntimeError(f"Bedienungshilfen fehlen ({AX_KEIN_ZUGRIFF})")
    aktionen = []
    for name in (namen or []) if fehler == 0 else []:
        treffer = re.match(r"Name:(.+)", str(name))
        if treffer:
            aktionen.append((treffer.group(1).strip(), str(name)))
    return aktionen


def _ax_sammeln(element, knoepfe: list, texte: list, tiefe: int = 0, mitteilungen: Optional[list] = None) -> None:
    """Knöpfe (Beschriftung, Element, Aktion) und Texte eines Fensters, Anruf-Mitteilungen in mitteilungen;
    Tiefe und Menge begrenzt."""
    import ApplicationServices

    if tiefe > 12 or len(knoepfe) + len(texte) > 300:
        return
    if mitteilungen is not None and ANRUF_KENNUNG in (str(_ax(element, ApplicationServices.kAXIdentifierAttribute)),
                                                      str(_ax(element, ApplicationServices.kAXDescriptionAttribute))):
        mitteilungen.append(element)
    rolle = _ax(element, ApplicationServices.kAXRoleAttribute)
    if rolle == "AXButton":
        # Bei Mitteilungen steht die Beschriftung oft nur in der Beschreibung, der Titel ist leer
        beschriftung = (_ax(element, ApplicationServices.kAXTitleAttribute)
                        or _ax(element, ApplicationServices.kAXDescriptionAttribute))
        if beschriftung:
            knoepfe.append((str(beschriftung), element, ApplicationServices.kAXPressAction))
    elif rolle == "AXStaticText" and _ax(element, ApplicationServices.kAXValueAttribute):
        texte.append(str(_ax(element, ApplicationServices.kAXValueAttribute)))
    knoepfe += [(beschriftung, element, aktion) for beschriftung, aktion in _ax_aktionen(element)]
    # Manche Elemente nennen ihre Kinder nur in der Navigationsreihenfolge
    kinder = _ax(element, ApplicationServices.kAXChildrenAttribute) or _ax(element, "AXChildrenInNavigationOrder")
    for kind in kinder or []:
        _ax_sammeln(kind, knoepfe, texte, tiefe + 1, mitteilungen)


def ax_baum(element, tiefe: int = 0, zeilen: Optional[list] = None) -> list:
    """Aufbau eines Fensters als Textzeilen fürs Log (Diagnose der Anrufanzeige)."""
    import ApplicationServices as A  # noqa: N812 - nur hier, für kurze Zeilen

    zeilen = [] if zeilen is None else zeilen
    if tiefe > 15 or len(zeilen) > 250:
        return zeilen
    werte = {name: _ax(element, attribut) for name, attribut in (
        ("rolle", A.kAXRoleAttribute), ("unterrolle", A.kAXSubroleAttribute), ("titel", A.kAXTitleAttribute),
        ("beschreibung", A.kAXDescriptionAttribute), ("wert", A.kAXValueAttribute),
        ("kennung", A.kAXIdentifierAttribute))}
    fehler, aktionen = A.AXUIElementCopyActionNames(element, None)
    teile = [f"{k}={str(v)[:60]!r}" for k, v in werte.items() if v not in (None, "")]
    if fehler == 0 and aktionen:
        teile.append(f"aktionen={[str(a).splitlines()[0] for a in aktionen]}")
    zeilen.append("  " * tiefe + " ".join(teile))
    for kind in _ax(element, A.kAXChildrenAttribute) or []:
        ax_baum(kind, tiefe + 1, zeilen)
    return zeilen


def _anruf_fenster() -> list:
    """[(Prozess, [(Beschriftung, Element, Aktion)], [Texte], [Anruf-Mitteilungen])] aller Fenster von
    Mitteilungen und FaceTime mit Knöpfen oder einer Anruf-Mitteilung."""
    import ApplicationServices
    from AppKit import NSRunningApplication

    ergebnis = []
    for kennung, name in ANRUF_APPS.items():
        for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(kennung):
            programm = ApplicationServices.AXUIElementCreateApplication(app.processIdentifier())
            for fenster in _ax(programm, ApplicationServices.kAXWindowsAttribute) or []:
                knoepfe, texte, mitteilungen = [], [], []
                _ax_sammeln(fenster, knoepfe, texte, mitteilungen=mitteilungen)
                if knoepfe or mitteilungen:
                    ergebnis.append((name, knoepfe, texte, mitteilungen))
    return ergebnis


# ---- Annehmen per Bild: Die Anruf-Mitteilung ist für die Bedienungshilfen leer (Inhalt aus einem anderen Prozess).
# Atlas fotografiert deshalb nur ihren Bereich, sucht den grünen (Annehmen) bzw. roten (Ablehnen) runden Knopf und
# klickt genau dort. Geklickt wird nur bei genau einem eindeutigen Treffer innerhalb der Mitteilung.
KNOPFFARBE = {ANNEHMEN: "grün", ABLEHNEN: "rot"}
LETZTES_ANRUFBILD = os.path.join(DATENORDNER, "letzte-anruf-mitteilung.png")


def _ax_punkt_und_groesse(element):
    """Position und Größe eines Elements in Bildschirmpunkten (Ursprung oben links), oder (None, None)."""
    import ApplicationServices

    werte = []
    for attribut, typ in ((ApplicationServices.kAXPositionAttribute, ApplicationServices.kAXValueCGPointType),
                          (ApplicationServices.kAXSizeAttribute, ApplicationServices.kAXValueCGSizeType)):
        roh = _ax(element, attribut)
        ok, wert = ApplicationServices.AXValueGetValue(roh, typ, None) if roh is not None else (False, None)
        werte.append(wert if ok else None)
    return werte[0], werte[1]


def _knopfform(hoehe: float, breite: float, gefuellt: float) -> bool:
    """Kreis (ältere Anzeigen) oder abgerundeter Balken (macOS 26: grün „Annehmen“ über rot „Ablehnen“, oben
    rechts im Anruffenster). Maße in Punkten, gefuellt = Anteil der Fläche an ihrem Rechteck."""
    kreis = 16 <= max(hoehe, breite) <= 60 and abs(hoehe - breite) <= 0.25 * max(hoehe, breite) \
        and 0.45 <= gefuellt <= 0.86
    balken = 14 <= hoehe <= 40 and 30 <= breite <= 110 and 1.5 <= breite / hoehe <= 4.5 and 0.6 <= gefuellt <= 0.97
    return kreis or balken


def farbknoepfe(bild, farbe: str, skala: float) -> list:
    """Einfarbige Knopfflächen (Kreis oder Balken) in der rechten Bildhälfte als [(x, y)] in Punkten des
    Ausschnitts."""
    import numpy as np
    from scipy import ndimage

    r, g, b = (bild[..., i].astype(np.int16) for i in range(3))
    # Grün: Annehmen, Rot: Ablehnen; deutlich gesättigte Farben, damit Grau- und Hauttöne nicht zählen
    gruen = (g > 140) & (g - r > 60) & (g - b > 50)
    rot = (r > 170) & (r - g > 80) & (r - b > 80)
    maske = gruen if farbe == "grün" else rot
    flaechen, anzahl = ndimage.label(maske)
    knoepfe = []
    for nummer, (zeilen, spalten) in enumerate(ndimage.find_objects(flaechen), 1):
        hoehe, breite = zeilen.stop - zeilen.start, spalten.stop - spalten.start
        # Ein Kreis füllt sein Rechteck zu etwa 79 %, mit weißem Telefonsymbol darin weniger; ein abgerundetes
        # Quadrat wie das grüne FaceTime-Symbol fast ganz und fällt deshalb heraus
        gefuellt = (flaechen[zeilen, spalten] == nummer).sum() / (hoehe * breite)
        mitte_x = (spalten.start + spalten.stop) / 2
        rechts = mitte_x > 0.5 * bild.shape[1]  # Anruf-Knöpfe sitzen rechts, App-Symbol und Name links
        if rechts and _knopfform(hoehe / skala, breite / skala, gefuellt):
            knoepfe.append((mitte_x / skala, (zeilen.start + zeilen.stop) / 2 / skala))
    return knoepfe


def _maus(art, x: float, y: float) -> None:
    import Quartz

    Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                       Quartz.CGEventCreateMouseEvent(None, art, (x, y), Quartz.kCGMouseButtonLeft))


def mitteilung_aufnehmen(mitteilung):
    """Fotografiert nur den Bereich der Anruf-Mitteilung. Liefert (Bild als Array, Position, Größe) oder None."""
    import numpy as np
    import Quartz
    from PIL import Image

    punkt, groesse = _ax_punkt_und_groesse(mitteilung)
    if punkt is None or groesse is None or not Quartz.CGPreflightScreenCaptureAccess():
        print(f"Anruf per Bild: Position {punkt}, Bildschirmaufnahme erlaubt {Quartz.CGPreflightScreenCaptureAccess()}")
        return None
    os.makedirs(DATENORDNER, exist_ok=True)
    bereich = f"{int(punkt.x)},{int(punkt.y)},{int(groesse.width)},{int(groesse.height)}"
    subprocess.run(["/usr/sbin/screencapture", "-x", "-t", "png", "-R", bereich, LETZTES_ANRUFBILD], check=True)
    return np.array(Image.open(LETZTES_ANRUFBILD).convert("RGB")), punkt, groesse


def anrufer_erkennen(mitteilung) -> str:
    """Liest den Namen des Anrufers per Texterkennung (Apple Vision, auf dem Mac) aus dem Bild der Mitteilung.
    Nimmt nur Text oben links in der Anzeige: an den abgerundeten Ecken scheinen sonst fremde Fenster durch."""
    import Vision
    from Foundation import NSURL

    if mitteilung_aufnehmen(mitteilung) is None:
        return ""
    anfrage = Vision.VNRecognizeTextRequest.alloc().init()
    anfrage.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    anfrage.setRecognitionLanguages_(["de-DE", "en-US"])
    anfrage.setUsesLanguageCorrection_(False)
    bild = NSURL.fileURLWithPath_(LETZTES_ANRUFBILD)
    Vision.VNImageRequestHandler.alloc().initWithURL_options_(bild, None).performRequests_error_([anfrage], None)
    kandidaten = []
    for beobachtung in anfrage.results() or []:
        rahmen = beobachtung.boundingBox()  # normiert, Ursprung unten links
        oben = 1 - rahmen.origin.y - rahmen.size.height
        if 0.03 <= rahmen.origin.x <= 0.6 and 0.02 <= oben <= 0.25:
            kandidaten.append((oben, str(beobachtung.topCandidates_(1)[0].string())))
    name = anrufer_aus([text for _, text in sorted(kandidaten)])
    print(f"Anrufer per Texterkennung: {name!r} aus {sorted(kandidaten)}")
    return "" if name == "unbekannt" else name


def knopf_per_bild(mitteilung, farbe: str) -> bool:
    """Fotografiert die Mitteilung, sucht genau einen Knopf der Farbe und klickt ihn. True bei Klick."""
    import Quartz

    punkt, groesse = _ax_punkt_und_groesse(mitteilung)
    if punkt is None or groesse is None:
        return False
    vorher = Quartz.CGEventGetLocation(Quartz.CGEventCreate(None))
    # Mauszeiger auf die Mitteilung: manche zeigen ihre Knöpfe erst beim Darüberfahren
    _maus(Quartz.kCGEventMouseMoved, punkt.x + groesse.width / 2, punkt.y + groesse.height / 2)
    time.sleep(0.4)
    aufnahme = mitteilung_aufnehmen(mitteilung)
    if aufnahme is None:
        _maus(Quartz.kCGEventMouseMoved, vorher.x, vorher.y)
        return False
    bild, punkt, groesse = aufnahme
    bereich = f"{int(punkt.x)},{int(punkt.y)},{int(groesse.width)},{int(groesse.height)}"
    skala = bild.shape[1] / groesse.width
    knoepfe = farbknoepfe(bild, farbe, skala)
    print(f"Anruf per Bild: Bereich {bereich}, Skala {skala:.1f}, {farbe}e Knöpfe {knoepfe}")
    if len(knoepfe) != 1:  # kein oder mehrdeutiger Treffer: lieber nicht klicken
        _maus(Quartz.kCGEventMouseMoved, vorher.x, vorher.y)
        return False
    x, y = punkt.x + knoepfe[0][0], punkt.y + knoepfe[0][1]
    for art in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventLeftMouseUp):
        _maus(art, x, y)
        time.sleep(0.05)
    _maus(Quartz.kCGEventMouseMoved, vorher.x, vorher.y)
    print(f"Anruf per Bild: {farbe}en Knopf bei ({x:.0f}, {y:.0f}) angeklickt")
    return True


def _knopf_suchen_und_druecken(beschriftungen: tuple) -> str:
    import ApplicationServices

    for _, knoepfe, _, _ in _anruf_fenster():
        for beschriftung, element, aktion in knoepfe:
            if beschriftung in beschriftungen:
                fehler = ApplicationServices.AXUIElementPerformAction(element, aktion)
                print(f"Anruf: drücke {beschriftung!r} ({aktion.splitlines()[0]}), Ergebnis {fehler}")
                return beschriftung
    return ""


def anruf_knopf_druecken(beschriftungen: tuple) -> str:
    """Drückt den ersten Knopf mit einer der Beschriftungen; liefert die Beschriftung oder "".
    Findet sich keiner, wird die Anruf-Mitteilung selbst angeklickt (öffnet die Anrufansicht) und erneut gesucht."""
    import ApplicationServices

    gedrueckt = _knopf_suchen_und_druecken(beschriftungen)
    if gedrueckt:
        return gedrueckt
    for _, _, _, mitteilungen in _anruf_fenster():
        for mitteilung in mitteilungen:
            if beschriftungen in KNOPFFARBE and knopf_per_bild(mitteilung, KNOPFFARBE[beschriftungen]):
                return beschriftungen[0]
    for _, _, _, mitteilungen in _anruf_fenster():
        for mitteilung in mitteilungen:
            fehler, aktionen = ApplicationServices.AXUIElementCopyActionNames(mitteilung, None)
            print(f"Anruf: kein Knopf {beschriftungen}, Aktionen der Mitteilung: {aktionen} (Fehler {fehler})")
            if fehler == 0 and aktionen and "AXPress" in aktionen:
                print("Anruf: klicke die Mitteilung an")
                ApplicationServices.AXUIElementPerformAction(mitteilung, "AXPress")
                time.sleep(1.0)
                fenster_aufbau_protokollieren()
                return _knopf_suchen_und_druecken(beschriftungen)
    return ""


ANRUF = {"klingelt": False, "anrufer": "", "pausiert_seit": 0.0, "zuletzt_gesehen": 0.0}


def anrufanzeige() -> list:
    """[(Prozess, [Knopfbeschriftungen], [Texte])] aller Fenster der Anrufanzeige mit Knöpfen."""
    # Eine Anruf-Mitteilung erscheint als Pseudo-Knopf ANRUF_KENNUNG, auch wenn ihr Inhalt noch fehlt
    return [(prozess, [b for b, _, _ in knoepfe] + [ANRUF_KENNUNG] * bool(mitteilungen), texte)
            for prozess, knoepfe, texte, mitteilungen in _anruf_fenster()]


def anrufer_aus(texte: list) -> str:
    """Erster Text, der nach einem Namen aussieht, nicht nach "FaceTime-Audio" oder "Anruf"."""
    for text in texte:
        if not re.search(r"facetime|anruf|call|iphone|audio|video", text, re.I):
            return text
    return "unbekannt"


_diagnose = {"anzeige": None, "besitzer": None, "mitteilung_protokolle": 0}


def anruf_mitteilung_protokollieren() -> None:
    """Solange die Anruf-Mitteilung sichtbar ist, ihren Aufbau die ersten fünf Male ins Log schreiben: Ihr Inhalt
    erscheint erst nach und nach. Mit allen Eigenschaften und Aktionen, für die Diagnose."""
    import ApplicationServices

    if _diagnose["mitteilung_protokolle"] >= 5:
        return
    _diagnose["mitteilung_protokolle"] += 1
    for _, _, _, mitteilungen in _anruf_fenster():
        for mitteilung in mitteilungen:
            _, eigenschaften = ApplicationServices.AXUIElementCopyAttributeNames(mitteilung, None)
            fehler, aktionen = ApplicationServices.AXUIElementCopyActionNames(mitteilung, None)
            print(f"Anruf-Mitteilung ({_diagnose['mitteilung_protokolle']}/5): Eigenschaften "
                  f"{list(eigenschaften or [])}, Aktionen {list(aktionen or [])} (Fehler {fehler})\n"
                  + "\n".join(ax_baum(mitteilung)))


def anruf_diagnose(fenster: list) -> None:
    """Schreibt Änderungen ins Log: welche Fenster die Anrufanzeige hat und welche Programme neu Fenster zeigen.
    Damit lässt sich nach einem Testanruf sehen, wie die Anzeige unter dieser macOS-Version aufgebaut ist."""
    if fenster != _diagnose["anzeige"]:
        print(f"Anrufanzeige: {fenster}")
        _diagnose["anzeige"] = fenster
    try:
        import Quartz

        liste = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        besitzer = {str(f.get("kCGWindowOwnerName")) for f in liste if f.get("kCGWindowLayer", 0) > 0}
    except Exception:  # Diagnose darf die Überwachung nie stören
        return
    if _diagnose["besitzer"] is not None and besitzer - _diagnose["besitzer"]:
        print(f"Neue Fenster von: {sorted(besitzer - _diagnose['besitzer'])}")
        if (besitzer - _diagnose["besitzer"]) & {"Notification Center", "FaceTime"}:
            fenster_aufbau_protokollieren()
    _diagnose["besitzer"] = besitzer


def fenster_aufbau_protokollieren() -> None:
    import ApplicationServices
    from AppKit import NSRunningApplication

    for kennung, name in ANRUF_APPS.items():
        for app in NSRunningApplication.runningApplicationsWithBundleIdentifier_(kennung):
            programm = ApplicationServices.AXUIElementCreateApplication(app.processIdentifier())
            for nummer, fenster in enumerate(_ax(programm, ApplicationServices.kAXWindowsAttribute) or []):
                print(f"Aufbau {name}, Fenster {nummer}:\n" + "\n".join(ax_baum(fenster)))


def _klingeln_beginnt(fenster: list) -> None:
    """Erstes Klingeln: den Anrufer ansagen, notfalls mit dem Namen aus der Texterkennung."""
    texte = next(ts for _, ks, ts in fenster if set(ks) & (set(ANNEHMEN) | {ANRUF_KENNUNG}))
    ANRUF["anrufer"] = anrufer_aus(texte) if texte else ""
    if ANRUF["anrufer"] in ("", "unbekannt"):
        try:
            mitteilungen = [m for _, _, _, ms in _anruf_fenster() for m in ms]
            ANRUF["anrufer"] = anrufer_erkennen(mitteilungen[0]) if mitteilungen else ""
        except Exception as fehler:  # ohne Namen trotzdem ansagen
            print(f"Anrufer-Erkennung: {fehler}", file=sys.stderr)
    ansage = f"Anruf von {ANRUF['anrufer']}" if ANRUF["anrufer"] not in ("", "unbekannt") else "Eingehender Anruf"
    melde_status(ansage)
    sprich(f"{ansage}.")


def _gespraech_pruefen(fenster_sichtbar: bool) -> None:
    """Während eines angenommenen Gesprächs: Das Anruffenster trägt weiter die Kennung FACETIME_NOTIFICATION.
    Ist es einige Sekunden verschwunden, ist aufgelegt, und Atlas hört wieder zu."""
    jetzt = time.time()
    if fenster_sichtbar:
        ANRUF["zuletzt_gesehen"] = jetzt
    elif jetzt - ANRUF["pausiert_seit"] > 5 and jetzt - ANRUF["zuletzt_gesehen"] > 4:
        print("Anruf beendet, höre wieder zu")
        ANRUF.update(pausiert_seit=0.0, klingelt=False)
        PAUSIERT.clear()


def anruf_pruefen() -> None:
    """Ein Durchgang der Anrufüberwachung: Klingeln ansagen, nach dem Gespräch fortsetzen."""
    fenster = anrufanzeige()
    anruf_diagnose(fenster)
    knoepfe = {k for _, ks, _ in fenster for k in ks}
    sichtbar = bool(knoepfe & (set(ANNEHMEN) | {ANRUF_KENNUNG}))
    if ANRUF["pausiert_seit"]:  # Gespräch läuft: das Fenster ist das Gespräch, kein neuer Anruf
        _gespraech_pruefen(sichtbar)
        return
    if sichtbar:
        anruf_mitteilung_protokollieren()
    else:
        _diagnose["mitteilung_protokolle"] = 0
    if sichtbar and not ANRUF["klingelt"]:
        _klingeln_beginnt(fenster)
    ANRUF["klingelt"] = sichtbar


def anruf_berechtigung_fehlt(fehler: str) -> str:
    """Welche Berechtigung fehlt, laut AppleScript-Fehler; leer, wenn es ein anderer Fehler ist."""
    if "-1743" in fehler:
        return "Steuerung von Systemereignissen"
    if "-1719" in fehler or "-25211" in fehler or "assistive" in fehler.lower():
        return "Bedienungshilfen"
    return ""


def anruf_ueberwachung() -> None:
    while True:
        try:
            anruf_pruefen()
        except RuntimeError as fehler:  # meist fehlende Berechtigungen; dann nicht jede Sekunde ins Log schreiben
            fehlt = anruf_berechtigung_fehlt(str(fehler))
            if fehlt:
                melde_status(f"Anrufe: {fehlt} für heyAtlas fehlt")
                print(f"Anrufüberwachung: {fehlt} fehlt ({fehler})", file=sys.stderr)
                time.sleep(30)
            else:
                print(f"Anrufüberwachung: {fehler}", file=sys.stderr)
        time.sleep(1.5)


def anruf_annehmen() -> Optional[str]:
    if not ANRUF["klingelt"]:
        return None
    if not anruf_knopf_druecken(ANNEHMEN):
        return "Per Sprache kann ich den Anruf leider nicht annehmen. Klick bitte auf Annehmen."
    ANRUF.update(klingelt=False, pausiert_seit=time.time(), zuletzt_gesehen=time.time())
    PAUSIERT.set()  # während des Gesprächs nicht auf das Aktivierungswort hören
    wiedergabe_pausiert()  # angehaltene Musik nach dem Annehmen nicht wieder starten
    return ""  # nichts sagen, das Gespräch beginnt


def anruf_ablehnen() -> Optional[str]:
    if not ANRUF["klingelt"]:
        return None
    ANRUF["klingelt"] = False
    if anruf_knopf_druecken(ABLEHNEN):
        return "Abgelehnt."
    return "Per Sprache kann ich den Anruf leider nicht ablehnen. Klick bitte auf Ablehnen."


def bildschirmaufnahme_erlauben() -> str:
    """Fragt nach der Bildschirmaufnahme (nötig, um den Annehmen-Knopf im Bild der Mitteilung zu finden).
    macOS übernimmt eine neu erteilte Freigabe erst nach einem Neustart von heyAtlas."""
    import Quartz

    if Quartz.CGPreflightScreenCaptureAccess():
        return "erlaubt"
    Quartz.CGRequestScreenCaptureAccess()
    if Quartz.CGPreflightScreenCaptureAccess():
        return "erlaubt"
    return "abgelehnt (nach dem Erlauben heyAtlas neu starten)"


def bedienungshilfen_erlaubt(nachfragen: bool = False) -> bool:
    """Mit nachfragen=True zeigt macOS den Dialog, der zu den Bedienungshilfen-Einstellungen führt."""
    import ApplicationServices

    if nachfragen:
        return bool(ApplicationServices.AXIsProcessTrustedWithOptions(
            {ApplicationServices.kAXTrustedCheckOptionPrompt: True}))
    return bool(ApplicationServices.AXIsProcessTrusted())


# ---------------------------------------------------------------- Kurzbefehle (z. B. Home-Szenen)

def erlaubte_kurzbefehle() -> list:
    def liste(*argumente: str) -> list:
        ergebnis = subprocess.run(["/usr/bin/shortcuts", "list", *argumente],
                                  capture_output=True, text=True, timeout=20)
        if ergebnis.returncode != 0:
            raise RuntimeError(ergebnis.stderr.strip() or "Kurzbefehle-App antwortet nicht.")
        return [z.strip() for z in ergebnis.stdout.splitlines() if z.strip()]

    # Wichtig: Bei unbekanntem Ordner liefert "shortcuts list --folder-name" alle Kurzbefehle statt keinem
    if KURZBEFEHL_ORDNER not in liste("--folders"):
        raise RuntimeError(f"Ordner '{KURZBEFEHL_ORDNER}' fehlt in der Kurzbefehle-App. "
                           "Lege ihn an und verschiebe die gewünschten Kurzbefehle hinein.")
    return liste("--folder-name", KURZBEFEHL_ORDNER)


@beta_tool
def kurzbefehle_auflisten() -> str:
    """Listet die Kurzbefehle auf, die der Assistent ausführen darf, etwa Home-Szenen wie "Licht aus"."""
    namen = erlaubte_kurzbefehle()
    return ", ".join(namen) if namen else f"Der Ordner '{KURZBEFEHL_ORDNER}' ist leer."


@beta_tool
def kurzbefehl_ausfuehren(name: str) -> str:
    """Führt einen Kurzbefehl aus dem freigegebenen Ordner aus, etwa eine Home-Szene.

    Args:
        name: Genauer Name aus kurzbefehle_auflisten.
    """
    passend = [n for n in erlaubte_kurzbefehle() if n.lower() == name.strip().lower()]
    if not passend:
        return f"Kurzbefehl '{name}' ist nicht freigegeben. Erlaubt sind: {kurzbefehle_auflisten.call({})}"
    ergebnis = subprocess.run(["/usr/bin/shortcuts", "run", passend[0]], capture_output=True, text=True, timeout=120)
    if ergebnis.returncode != 0:
        raise RuntimeError(ergebnis.stderr.strip() or "Kurzbefehl fehlgeschlagen.")
    return f"Kurzbefehl '{passend[0]}' ausgeführt. {ergebnis.stdout.strip()}".strip()


# Claudes eingebaute Websuche, läuft auf den Servern von Anthropic
WEBSUCHE = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}
if HEIMATORT:
    WEBSUCHE["user_location"] = {"type": "approximate", "city": HEIMATORT, "country": "DE",
                                 "timezone": "Europe/Berlin"}

WERKZEUGE = [erinnerung_anlegen, erinnerungen_lesen, erinnerung_abhaken,
             termine_lesen, termin_anlegen, termin_verschieben, termin_loeschen,
             spotify_abspielen, spotify_steuern, spotify_aktueller_titel, wetter, nachrichten,
             timer_stellen, timer_abfragen, timer_abbrechen, klingeln_stoppen,
             wecker_stellen, wecker_auflisten, wecker_loeschen,
             mails_lesen, mails_suchen, mail_vorlesen, mail_antworten, mail_schreiben,
             lautstaerke, app_oeffnen, app_beenden, dunkelmodus, bildschirm_sperren,
             apple_music_abspielen, apple_music_steuern, youtube_abspielen, youtube_steuern,
             abfahrten, verbindung_suchen,
             notiz_anlegen, notizen_suchen, nachricht_senden, kurzbefehle_auflisten, kurzbefehl_ausfuehren]
# Optionale Werkzeuge nur bei eingeschaltetem Schalter, sonst versucht Claude sie gar nicht erst
if NACHRICHTEN_LESEN:
    WERKZEUGE += [imessages_lesen, imessage_antworten]
    SYSTEMPROMPT += ("\nFragt der Nutzer nach Nachrichten von Personen („Hab ich neue Nachrichten?“, „Was hat Anna "
                     "geschrieben?“), sind iMessages und SMS gemeint: nutze imessages_lesen. Das Werkzeug nachrichten "
                     "ist nur für Schlagzeilen der Tagesschau.")


# ---------------------------------------------------------------- Schnellbefehle ohne Claude

# Häufige kurze Befehle erledigt Atlas sofort selbst. Ein Befehl greift nur, wenn der ganze Satz passt;
# alles andere geht an Claude.

FUELLWOERTER = {"bitte", "mal", "atlas", "hey", "doch", "jetzt", "okay", "ok", "danke"}

ZAHLWOERTER = {
    "ein": 1, "eine": 1, "einen": 1, "einer": 1, "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6,
    "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12, "dreizehn": 13, "vierzehn": 14,
    "fünfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18, "neunzehn": 19, "zwanzig": 20,
    "fünfundzwanzig": 25, "dreißig": 30, "vierzig": 40, "fünfundvierzig": 45, "fünfzig": 50, "sechzig": 60,
    "neunzig": 90,
}
EINHEITEN = {"sekunde": 1, "sekunden": 1, "minute": 60, "minuten": 60, "stunde": 3600, "stunden": 3600}

SCHNELL_TEXTE = {
    "stopp": {"stopp", "stop", "aus", "ruhe", "schluss", "wecker aus", "timer aus", "stopp den wecker",
              "stopp den timer", "mach den wecker aus", "mach den timer aus"},
    "uhrzeit": {"wie spät ist es", "wie viel uhr ist es", "wieviel uhr ist es", "uhrzeit", "wie spät"},
    "lauter": {"lauter", "mach lauter", "etwas lauter", "ein bisschen lauter", "lauter machen"},
    "leiser": {"leiser", "mach leiser", "etwas leiser", "ein bisschen leiser", "leiser machen"},
    "stumm": {"stumm", "ton aus", "stummschalten", "mach den ton aus"},
    "ton_an": {"ton an", "mach den ton an", "stummschaltung aus"},
    "pause": {"pause", "musik pause", "musik aus", "stopp die musik", "halt die musik an", "musik stoppen",
              "anhalten", "pausieren"},
    "play": {"weiterspielen", "spiel weiter", "fortsetzen", "musik an", "musik weiter", "play"},
    "naechster": {"nächster titel", "nächstes lied", "nächster song", "überspringen", "skip"},
    "vorheriger": {"vorheriger titel", "vorheriges lied", "letztes lied", "lied zurück"},
    "titel": {"was läuft gerade", "was läuft da", "welches lied ist das", "was ist das für ein lied",
              "wie heißt das lied"},
    "timer_abfragen": {"wie lange noch", "wie lange läuft der timer noch", "wie lange dauert der timer noch"},
    "timer_abbrechen": {"timer abbrechen", "brich den timer ab", "timer löschen", "lösch den timer"},
    "anruf_annehmen": {"nimm an", "annehmen", "anruf annehmen", "nimm den anruf an", "geh ran", "abnehmen"},
    "anruf_ablehnen": {"lehn ab", "ablehnen", "anruf ablehnen", "lehn den anruf ab", "drück weg", "wegdrücken"},
}

# "timer auf zehn minuten", "stell einen timer für 5 minuten für die nudeln", "zehn minuten timer"
TIMER_MUSTER = [
    re.compile(r"(?:stell |starte |mach )?(?:einen |den |nen )?timer (?:auf |für |von )?"
               r"(?P<zahl>\w+) (?P<einheit>\w+)(?: (?:für (?:die |den |das )?)?(?P<name>.+))?"),
    re.compile(r"(?P<zahl>\w+) (?P<einheit>\w+) timer(?: (?:für (?:die |den |das )?)?(?P<name>.+))?"),
]


def normalisiere(text: str) -> str:
    woerter = re.sub(r"[^\wäöüß ]", " ", text.lower()).split()
    return " ".join(w for w in woerter if w not in FUELLWOERTER)


def zahl(wort: str) -> Optional[int]:
    return int(wort) if wort.isdigit() else ZAHLWOERTER.get(wort)


def aktiver_player(nur_spielend: bool = True) -> Optional[str]:
    """Spotify oder Musik-App, wenn sie gerade spielt. Mit nur_spielend=False auch eine pausierte."""
    ergebnis = osascript("""on run argv
    set pausiert to ""
    if application "Spotify" is running then
        tell application "Spotify" to if player state is playing then return "Spotify"
        set pausiert to "Spotify"
    end if
    if application "Music" is running then
        tell application "Music" to if player state is playing then return "Music"
        if pausiert is "" then set pausiert to "Music"
    end if
    if (item 1 of argv) is "1" then return ""
    return pausiert
end run""", "1" if nur_spielend else "0")
    return ergebnis or None


def player_befehl(aktion: str) -> Optional[str]:
    """Steuert Spotify oder Musik. None, wenn keiner passt; dann entscheidet Claude, z. B. für YouTube."""
    # Was Atlas für diese Frage selbst angehalten hat, zählt hier als "spielt gerade"
    player = (aktiver_player(nur_spielend=aktion != "play")
              or next((quelle for quelle in _angehalten if quelle in ("Spotify", "Music")), None))
    if aktion == "pause" and (player or "YouTube" in _angehalten):
        wiedergabe_pausiert()
        if not player:
            return "Okay."  # nur das Video lief, und das steht schon
    if not player:
        return None
    befehle = SPOTIFY_BEFEHLE if player == "Spotify" else MUSIC_BEFEHLE
    osascript(f'tell application "{player}" to {befehle[aktion]}')
    if aktion in ("weiter", "zurueck") and player in _angehalten:
        osascript(f'tell application "{player}" to play')  # von Atlas angehalten: nach dem Wechsel weiterspielen
    if aktion in ("play", "weiter", "zurueck"):
        wiedergabe_gestartet()
    return "Okay."


def _schnell_stopp() -> Optional[str]:
    # "Stopp" schaltet zuerst etwas Klingelndes aus, sonst hält es die Musik an
    return "Okay, ist aus." if klingeln_beenden() else player_befehl("pause")


def _schnell_lautstaerke(aktion: str) -> str:
    lautstaerke.call({"aktion": aktion})
    return "Okay."


def _schnell_titel() -> Optional[str]:
    # Nur Spotify kennt hier den Titel; bei der Musik-App oder ohne Wiedergabe fragt Claude
    return spotify_aktueller_titel.call({}) if aktiver_player() == "Spotify" else None


def _schnell_timer_abfragen() -> str:
    with _uhr_sperre:
        laufend = sorted(_timer.items(), key=lambda t: t[1])
    if not laufend:
        return "Es läuft kein Timer."
    jetzt = datetime.now()
    return ", ".join(f"{name} noch {dauer_gesprochen((ende - jetzt).total_seconds())}"
                     for name, ende in laufend) + "."


def _schnell_timer_abbrechen() -> Optional[str]:
    with _uhr_sperre:
        anzahl = len(_timer)
    if anzahl > 1:
        return None  # mehrere Timer: Claude fragt nach, welcher gemeint ist
    return timer_abbrechen.call({}) if anzahl else "Es läuft kein Timer."


SCHNELL_AKTIONEN = {
    "stopp": _schnell_stopp,
    "uhrzeit": lambda: f"Es ist {datetime.now():%H:%M} Uhr.",
    "lauter": lambda: _schnell_lautstaerke("lauter"),
    "leiser": lambda: _schnell_lautstaerke("leiser"),
    "stumm": lambda: _schnell_lautstaerke("stumm"),
    "ton_an": lambda: _schnell_lautstaerke("ton_an"),
    "pause": lambda: player_befehl("pause"),
    "play": lambda: player_befehl("play"),
    "naechster": lambda: player_befehl("weiter"),
    "vorheriger": lambda: player_befehl("zurueck"),
    "titel": _schnell_titel,
    "timer_abfragen": _schnell_timer_abfragen,
    "timer_abbrechen": _schnell_timer_abbrechen,
    # Greifen nur, solange es klingelt (sonst None, dann übernimmt Claude)
    "anruf_annehmen": lambda: anruf_annehmen(),
    "anruf_ablehnen": lambda: anruf_ablehnen(),
}


def _schnell_timer_stellen(t: str) -> Optional[str]:
    for muster in TIMER_MUSTER:
        treffer = muster.fullmatch(t)
        if treffer and zahl(treffer["zahl"]) and treffer["einheit"] in EINHEITEN:
            sekunden = zahl(treffer["zahl"]) * EINHEITEN[treffer["einheit"]]
            name = (treffer["name"] or "").strip().capitalize() or "Timer"
            timer_stellen.call({"dauer_sekunden": sekunden, "name": name})
            return f"Timer für {treffer['zahl']} {treffer['einheit']} läuft."
    return None


def _schnell_anruf(t: str) -> Optional[str]:
    """Solange es klingelt: jeder Satz mit Ablehnen-Wörtern lehnt ab, jeder mit „Anruf“ oder „annehmen“ nimmt an.
    So klappt es auch, wenn die Spracherkennung „Dann den Anruf an“ statt „Nimm den Anruf an“ versteht."""
    if not ANRUF["klingelt"]:
        return None
    woerter = set(t.split())
    if woerter & {"ablehnen", "lehn", "weg", "wegdrücken", "nicht", "nein"}:
        return anruf_ablehnen()
    if "anruf" in t or woerter & {"annehmen", "abnehmen", "ran", "an"}:
        return anruf_annehmen()
    return None


def schnellbefehl(text: str) -> Optional[str]:
    """Antwort für einen bekannten Kurzbefehl, oder None, wenn Claude übernehmen soll."""
    t = normalisiere(text)
    anruf = _schnell_anruf(t)
    if anruf is not None:
        return anruf
    art = next((a for a, saetze in SCHNELL_TEXTE.items() if t in saetze), None)
    if art:
        return SCHNELL_AKTIONEN[art]()
    return _schnell_timer_stellen(t)


# ---------------------------------------------------------------- Claude-Schleife

verlauf: list = []  # nur Text-Paare der letzten Runden, damit Rückfragen funktionieren


def anfrage_parameter() -> dict:
    if LOKALES_MODELL:
        # LM Studio kennt weder die Websuche von Anthropic noch Fallbacks, Aufwand oder Cache-Angaben
        return {"model": LOKALES_MODELL, "system": SYSTEMPROMPT_LOKAL, "tools": WERKZEUGE}
    return {
        "model": MODELL,
        "system": SYSTEMPROMPT,
        "output_config": {"effort": "low"},  # kurze Antworten, wenig Wartezeit
        "betas": ["server-side-fallback-2026-07-01"],
        "fallbacks": "default",
        "cache_control": {"type": "ephemeral"},  # Werkzeuge und Systemprompt aus dem Cache: schneller, günstiger
        "tools": WERKZEUGE + [WEBSUCHE],
    }


def ask(text: str) -> str:
    jetzt = datetime.now().strftime("%A, %d.%m.%Y, %H:%M Uhr")
    nachricht = {"role": "user", "content": f"[Jetzt: {jetzt}]\n{text}"}
    gespraech = verlauf[-10:] + [nachricht]
    # Eine lange Websuche kann mit pause_turn enden; der Python-Runner setzt das nicht selbst fort,
    # deshalb wird das Gespräch mitgeschrieben und der Runner mit dem pausierten Zug neu gestartet
    for _ in range(4):
        runner = claude().beta.messages.tool_runner(max_tokens=4000, messages=gespraech, **anfrage_parameter())
        letzte = None
        for letzte in runner:
            gespraech.append({"role": "assistant", "content": letzte.content})
            werkzeug_antwort = runner.generate_tool_call_response()
            if werkzeug_antwort is not None:
                gespraech.append(werkzeug_antwort)
        if letzte is None or letzte.stop_reason != "pause_turn":
            break
    if letzte is None or letzte.stop_reason == "refusal":
        return "Dabei kann ich leider nicht helfen."
    # Nach einer Websuche ist der Text in viele Blöcke mit Quellenangaben zerteilt; Leerraum glätten
    antwort = " ".join(" ".join(b.text for b in letzte.content if b.type == "text").split())
    antwort = re.sub(r"\s+([.,;:!?])", r"\1", antwort)
    verlauf.extend([nachricht, {"role": "assistant", "content": antwort or "(erledigt)"}])
    return antwort or "Erledigt."


def wakeword_test() -> None:
    """--wakeword-test: zeigt live, wie sicher das Wake-Word-Modell ist. Ctrl+C beendet."""
    import sounddevice as sd
    from openwakeword.model import Model

    modell = Model(wakeword_models=[WAKEWORD], inference_framework="onnx")
    print(f"Sag ein paar Mal „Hey Atlas“ und zum Vergleich andere Sätze. Schwelle: {WAKEWORD_SCHWELLE}\n")
    hoechster, bloecke = 0.0, 0
    with sd.InputStream(samplerate=ABTASTRATE, channels=1, dtype="int16", blocksize=BLOCK) as strom:
        while True:
            block, _ = strom.read(BLOCK)
            hoechster = max(hoechster, max(modell.predict(block.flatten()).values()))
            bloecke += 1
            if bloecke == 6:  # etwa zweimal pro Sekunde eine Zeile
                balken = "█" * int(hoechster * 40)
                treffer = "  ← erkannt" if hoechster >= WAKEWORD_SCHWELLE else ""
                print(f"{hoechster:5.2f} {balken}{treffer}")
                hoechster, bloecke = 0.0, 0


def pmset_befehl() -> bool:
    """Führt --pmset-einrichten oder --pmset-entfernen aus. True, wenn einer der beiden angegeben war."""
    if "--pmset-einrichten" in sys.argv:
        pmset_einrichten()
    elif "--pmset-entfernen" in sys.argv:
        pmset_entfernen()
    else:
        return False
    print("pmset eingerichtet: Der Mac wacht vor Weckern auf." if pmset_eingerichtet()
          else "pmset ist nicht (mehr) eingerichtet.")
    return True


def eine_runde(folgefrage: bool) -> bool:
    """Hört zu und antwortet einmal. Liefert, ob danach ohne Wake-Word weitergehört werden soll."""
    # Nach einer Antwort kurz ohne Wake-Word weiterhören; Stille führt zurück zum Wake-Word
    text = hoere(direkt=True, max_warten_sek=NACHFRAGE_SEK) if folgefrage else hoere()
    wiedergabe_anhalten_abwarten()  # _angehalten muss vollständig sein, bevor Atlas die Wiedergabe fortsetzt
    _wiedergabe_gestartet.clear()
    _wiedergabe_pausiert.clear()
    try:
        if text:
            antwort = schnellbefehl(text)
            if antwort is None:
                antwort = ask(text)
            if antwort:  # leer heißt schweigen, z. B. beim Annehmen eines Anrufs
                sprich(antwort)
    finally:
        fortsetzen = _angehalten and not (_wiedergabe_gestartet.is_set() or _wiedergabe_pausiert.is_set())
        if fortsetzen:
            wiedergabe_fortsetzen(list(_angehalten))
        _angehalten.clear()
    if _aufnahme_lief_voll:
        return False  # durchgehendes Geräusch ohne Sprechpause, kein Nachhören: sonst hört Atlas endlos weiter
    if not text or fortsetzen or _wiedergabe_gestartet.is_set() or PAUSIERT.is_set():
        return False  # läuft wieder etwas oder ein Anruf, kein Nachhören: der Ton landete sonst in der Aufnahme
    return MODUS == "wake" and NACHFRAGE_SEK > 0 and not musik_laeuft()


def musik_laeuft() -> bool:
    """Spielt Spotify oder Musik gerade? Dann kein Nachhören, sonst landet die Musik in der Aufnahme."""
    try:
        return aktiver_player(nur_spielend=True) is not None
    except RuntimeError:
        return False


def modelle_vorladen() -> None:
    """Vorab laden, damit die erste Antwort nicht hängt."""
    if not TEXTMODUS:
        lade_spracherkennung()
        if not STIMME.startswith(MACOS_PRAEFIX):
            lade_stimme()
    if LOKALES_MODELL:
        lokales_modell_vorbereiten()


def main() -> None:
    wecker_laden()
    if pmset_befehl():
        return
    if "--wakeword-test" in sys.argv:
        try:
            wakeword_test()
        except KeyboardInterrupt:
            print()
        return
    threading.Thread(target=uhr_schleife, daemon=True).start()
    if ANRUFE_ANNEHMEN:
        threading.Thread(target=anruf_ueberwachung, daemon=True).start()
    modelle_vorladen()
    sprich("Ich bin bereit.")
    folgefrage = False
    while True:
        try:
            folgefrage = eine_runde(folgefrage)
        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as fehler:  # Assistent soll bei einzelnen Fehlern weiterlaufen
            folgefrage = False
            print(f"Fehler: {fehler}", file=sys.stderr)
            melde_status(f"Fehler: {str(fehler)[:60]}")
            sprich("Da ist etwas schiefgegangen.")
            time.sleep(3)  # bei Dauerfehlern (z. B. kein Mikrofon) nicht im Sekundentakt melden


if __name__ == "__main__":
    main()
