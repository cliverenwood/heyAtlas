"""Sprachassistent: Wake-Word -> Whisper (lokal) -> Claude mit Tools -> osascript -> say.

Start:  python3 assistent.py          (Wake-Word, Aufnahme endet bei einer Sprechpause)
        python3 assistent.py --ptt    (Push-to-Talk: Enter startet und beendet die Aufnahme)
        python3 assistent.py --text   (Tippen statt Sprechen, zum Testen der Tools)
"""

import base64
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Literal, Optional

import anthropic
from anthropic import beta_tool

ORDNER = os.path.dirname(os.path.abspath(__file__))

MODELL = os.environ.get("CLAUDE_MODELL", "claude-sonnet-5")  # gemessen etwa doppelt so schnell wie claude-opus-5
# Piper-Stimme; weitere mit: .venv/bin/python -m piper.download_voices <name> (im Ordner stimmen/)
STIMME = os.environ.get("STIMME", os.path.join(ORDNER, "stimmen", "de_DE-thorsten-medium.onnx"))
KALENDER = os.environ.get("KALENDER", "")  # leer = erster beschreibbarer Kalender
HEIMATORT = os.environ.get("HEIMATORT", "")  # Ort für Wetterfragen ohne Ortsangabe
WHISPER_MODELL = os.environ.get("WHISPER_MODELL", "mlx-community/whisper-small-mlx")
ABTASTRATE = 16000

# Mitgeliefertes openWakeWord-Modell (hey_jarvis, alexa, hey_mycroft, hey_rhasspy) oder Pfad zu einer .onnx-Datei
WAKEWORD = os.environ.get("WAKEWORD", "hey_jarvis")
WAKEWORD_SCHWELLE = float(os.environ.get("WAKEWORD_SCHWELLE", "0.5"))
BLOCK = 1280  # 80 ms bei 16 kHz, die Blockgröße, die openWakeWord erwartet
# So lange hört der Assistent nach einer Antwort ohne Wake-Word weiter zu; 0 schaltet Folgefragen ab
NACHFRAGE_SEK = float(os.environ.get("NACHFRAGE_SEK", "4"))
# Nur Kurzbefehle aus diesem Ordner der Kurzbefehle-App darf der Assistent ausführen
KURZBEFEHL_ORDNER = os.environ.get("KURZBEFEHL_ORDNER", "Sprachassistent")

MODUS = "text" if "--text" in sys.argv else "ptt" if "--ptt" in sys.argv else "wake"
TEXTMODUS = MODUS == "text"

SYSTEMPROMPT = """Du bist ein Sprachassistent auf einem Mac. Deine Antworten werden vorgelesen.
Antworte auf Deutsch in ein bis zwei kurzen Sätzen, ohne Aufzählungen, Markdown oder Emojis.
Nutze die Werkzeuge für Erinnerungen und Kalender. Wenn Datum oder Uhrzeit unklar sind, frag kurz nach,
statt zu raten. Um einen Termin zu verschieben oder zu löschen, lies ihn zuerst mit termine_lesen und nutze
dessen Kennung wie T1. Für Musik nutze die Spotify-Werkzeuge, fürs Wetter das Wetter-Werkzeug; nenne Temperaturen
gerundet und ohne Nachkommastellen. Für Licht, Heizung und andere Abläufe nutze die Kurzbefehle; schau mit
kurzbefehle_auflisten nach, welche es gibt. Sprich Uhrzeiten natürlich aus, etwa "um halb drei" oder "um 14 Uhr"."""

SCHLUESSELBUND_DIENST = "anthropic-api-key"


def schluesselbund(dienst: str) -> Optional[str]:
    """Liest ein Geheimnis aus dem macOS-Schlüsselbund, None wenn es fehlt."""
    ergebnis = subprocess.run(
        ["security", "find-generic-password", "-s", dienst, "-a", os.environ.get("USER", ""), "-w"],
        capture_output=True, text=True)
    return ergebnis.stdout.strip() if ergebnis.returncode == 0 else None


def api_schluessel() -> Optional[str]:
    """Nimmt ANTHROPIC_API_KEY aus der Umgebung, sonst aus dem macOS-Schlüsselbund."""
    schluessel = os.environ.get("ANTHROPIC_API_KEY") or schluesselbund(SCHLUESSELBUND_DIENST)
    if not schluessel:
        sys.exit("Kein API-Schlüssel gefunden. Einmalig speichern mit:\n"
                 f"  security add-generic-password -s {SCHLUESSELBUND_DIENST} -a \"$USER\" -w")
    return schluessel


# Nur nötig, wenn der API-Schlüssel keinem Workspace zugeordnet ist
WORKSPACE_ID = os.environ.get("ANTHROPIC_WORKSPACE_ID", "")

client = anthropic.Anthropic(
    api_key=api_schluessel(),
    default_headers={"anthropic-workspace-id": WORKSPACE_ID} if WORKSPACE_ID else None,
)


# ---------------------------------------------------------------- Ein- und Ausgabe

_stimme = None


def lade_stimme():
    global _stimme
    if _stimme is None:
        from piper import PiperVoice
        _stimme = PiperVoice.load(STIMME)
    return _stimme


def sprich(text: str) -> None:
    """Piper erzeugt die Sprache satzweise; der erste Satz läuft schon, während der Rest entsteht."""
    import sounddevice as sd

    print(f"🔊 {text}")
    if TEXTMODUS:
        return
    for teil in lade_stimme().synthesize(text):
        sd.play(teil.audio_float_array, teil.sample_rate)
        sd.wait()


def ton(name: str) -> None:
    subprocess.run(["afplay", f"/System/Library/Sounds/{name}.aiff"])


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

    global _wakeword
    if _wakeword is None:
        from openwakeword.model import Model
        from openwakeword.utils import download_models
        if not WAKEWORD.endswith(".onnx"):
            download_models([WAKEWORD])
        _wakeword = Model(wakeword_models=[WAKEWORD], inference_framework="onnx")
    _wakeword.reset()
    print(f"👂 Warte auf „{WAKEWORD}“ …")
    with sd.InputStream(samplerate=ABTASTRATE, channels=1, dtype="int16", blocksize=BLOCK) as strom:
        while True:
            block, _ = strom.read(BLOCK)
            if max(_wakeword.predict(block.flatten()).values()) >= WAKEWORD_SCHWELLE:
                return


def nimm_auf_bis_stille(stille_sek: float = 0.7, max_warten_sek: float = 5.0, max_sek: float = 20.0):
    """Signalton, dann Aufnahme bis zu einer Sprechpause. Gibt float32-Audio mit 16 kHz zurück."""
    import numpy as np
    import sounddevice as sd

    ton("Tink")
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
                break
            if not gesprochen and len(teile) * dauer > max_warten_sek:
                return np.zeros(0, dtype="float32")
    ton("Pop")
    return np.concatenate(teile)


def transcribe(audio) -> str:
    """Lokale Spracherkennung mit mlx-whisper auf der Grafikeinheit. Austauschbar gegen eine Cloud-API."""
    import mlx_whisper

    if len(audio) < ABTASTRATE * 0.3:
        return ""
    return mlx_whisper.transcribe(audio, path_or_hf_repo=WHISPER_MODELL, language="de")["text"].strip()


def lade_whisper() -> None:
    import numpy as np

    print(f"Lade Whisper-Modell '{WHISPER_MODELL}' (beim ersten Mal wird es heruntergeladen) …")
    transcribe(np.zeros(ABTASTRATE, dtype="float32"))  # mlx-whisper hält das Modell danach im Speicher


def hoere(direkt: bool = False, max_warten_sek: float = 5.0) -> str:
    """direkt=True überspringt das Wake-Word, etwa für Rückfragen und Folgefragen."""
    if MODUS == "text":
        return input("Du: ").strip()
    if MODUS == "ptt":
        audio = nimm_auf()
    else:
        if not direkt:
            warte_auf_wakeword()
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


def osascript(skript: str, *argumente: str) -> str:
    """Führt AppleScript aus. Werte gehen als argv hinein, nie per String-Verkettung ins Skript."""
    ergebnis = subprocess.run(["osascript", "-e", skript, *argumente],
                              capture_output=True, text=True, timeout=60)
    if ergebnis.returncode != 0:
        raise RuntimeError(ergebnis.stderr.strip())
    return ergebnis.stdout.strip()


def datum_teile(iso: str) -> list:
    d = datetime.fromisoformat(iso)
    return [str(d.year), str(d.month), str(d.day), str(d.hour), str(d.minute)]


# ---------------------------------------------------------------- Werkzeuge

@beta_tool
def erinnerung_anlegen(titel: str, faellig: Optional[str] = None) -> str:
    """Legt eine Erinnerung in der Erinnerungen-App an.

    Args:
        titel: Text der Erinnerung.
        faellig: Optionaler Zeitpunkt im ISO-Format, z. B. 2026-10-11T09:00.
    """
    if faellig:
        skript = DATUM_HELFER + """
on run argv
    set dt to mkdate(item 2 of argv, item 3 of argv, item 4 of argv, item 5 of argv, item 6 of argv)
    tell application "Reminders" to make new reminder with properties {name:(item 1 of argv), remind me date:dt}
end run"""
        osascript(skript, titel, *datum_teile(faellig))
    else:
        osascript('on run argv\ntell application "Reminders" to make new reminder '
                  'with properties {name:(item 1 of argv)}\nend run', titel)
    return f"Erinnerung '{titel}' angelegt."


_kalender = None


def kalender_speicher():
    """EventKit-Zugang zum Kalender, schneller als die Kalender-App per AppleScript fernzusteuern."""
    import threading

    import EventKit

    global _kalender
    if _kalender is None:
        speicher = EventKit.EKEventStore.alloc().init()
        status = EventKit.EKEventStore.authorizationStatusForEntityType_(EventKit.EKEntityTypeEvent)
        if status != EventKit.EKAuthorizationStatusFullAccess:
            fertig, ergebnis = threading.Event(), {}

            def antwort(erlaubt, _fehler):
                ergebnis["erlaubt"] = erlaubt
                fertig.set()

            speicher.requestFullAccessToEventsWithCompletion_(antwort)
            fertig.wait(120)
            if not ergebnis.get("erlaubt"):
                raise RuntimeError("Kein Kalenderzugriff. Systemeinstellungen → Datenschutz & Sicherheit → "
                                   "Kalender → Terminal auf „Vollzugriff“ stellen.")
        _kalender = speicher
    return _kalender


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
    with urllib.request.urlopen(urllib.request.Request(url, **kw), timeout=10) as antwort:
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


# ---------------------------------------------------------------- Nachrichten

@beta_tool
def nachricht_senden(empfaenger: str, text: str) -> str:
    """Schickt eine iMessage an einen Kontakt aus der Kontakte-App. Fragt vorher per Sprache nach.

    Args:
        empfaenger: Name des Kontakts, wie er in Kontakte steht, z. B. "Anna" oder "Anna Müller".
        text: Text der Nachricht.
    """
    treffer = osascript("""on run argv
    set ausgabe to ""
    tell application "Contacts"
        repeat with p in (people whose name contains (item 1 of argv))
            set adressen to ""
            repeat with t in phones of p
                set adressen to adressen & (value of t) & ";"
            end repeat
            repeat with m in emails of p
                set adressen to adressen & (value of m) & ";"
            end repeat
            set ausgabe to ausgabe & (name of p) & "|" & adressen & linefeed
        end repeat
    end tell
    return ausgabe
end run""", empfaenger)
    kontakte = [z.split("|", 1) for z in treffer.splitlines() if "|" in z]
    kontakte = [(name, [a for a in adressen.split(";") if a]) for name, adressen in kontakte]
    kontakte = [(name, adressen) for name, adressen in kontakte if adressen]
    if not kontakte:
        return f"Kein Kontakt '{empfaenger}' mit Telefonnummer oder E-Mail gefunden."
    if len(kontakte) > 1:
        return "Mehrere Kontakte passen, frag nach, wer gemeint ist: " + ", ".join(n for n, _ in kontakte[:6])
    name, adressen = kontakte[0]
    if not bestaetigen(f"Soll ich {name} schreiben: {text}?"):
        return "Der Nutzer hat abgelehnt, die Nachricht wurde nicht gesendet."
    osascript("""on run argv
    tell application "Messages"
        set dienst to 1st account whose service type = iMessage
        send (item 2 of argv) to participant (item 1 of argv) of dienst
    end tell
end run""", adressen[0], text)
    return f"Nachricht an {name} gesendet."


# ---------------------------------------------------------------- Kurzbefehle (z. B. Home-Szenen)

def erlaubte_kurzbefehle() -> list:
    def liste(*argumente: str) -> list:
        ergebnis = subprocess.run(["shortcuts", "list", *argumente], capture_output=True, text=True, timeout=20)
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
    ergebnis = subprocess.run(["shortcuts", "run", passend[0]], capture_output=True, text=True, timeout=120)
    if ergebnis.returncode != 0:
        raise RuntimeError(ergebnis.stderr.strip() or "Kurzbefehl fehlgeschlagen.")
    return f"Kurzbefehl '{passend[0]}' ausgeführt. {ergebnis.stdout.strip()}".strip()


WERKZEUGE = [erinnerung_anlegen, termine_lesen, termin_anlegen, termin_verschieben, termin_loeschen,
             spotify_abspielen, spotify_steuern, spotify_aktueller_titel, wetter,
             notiz_anlegen, notizen_suchen, nachricht_senden, kurzbefehle_auflisten, kurzbefehl_ausfuehren]


# ---------------------------------------------------------------- Claude-Schleife

verlauf: list = []  # nur Text-Paare der letzten Runden, damit Rückfragen funktionieren


def ask(text: str) -> str:
    jetzt = datetime.now().strftime("%A, %d.%m.%Y, %H:%M Uhr")
    nachricht = {"role": "user", "content": f"[Jetzt: {jetzt}]\n{text}"}
    runner = client.beta.messages.tool_runner(
        model=MODELL,
        max_tokens=4000,
        system=SYSTEMPROMPT,
        output_config={"effort": "low"},  # kurze Antworten, wenig Wartezeit
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        tools=WERKZEUGE,
        messages=verlauf[-10:] + [nachricht],
    )
    letzte = None
    for letzte in runner:
        pass
    if letzte is None or letzte.stop_reason == "refusal":
        return "Dabei kann ich leider nicht helfen."
    antwort = " ".join(b.text for b in letzte.content if b.type == "text").strip()
    verlauf.extend([nachricht, {"role": "assistant", "content": antwort or "(erledigt)"}])
    return antwort or "Erledigt."


def main() -> None:
    if not TEXTMODUS:
        lade_whisper()  # vorab laden, damit die erste Antwort nicht hängt
        lade_stimme()
    sprich("Ich bin bereit.")
    folgefrage = False
    while True:
        try:
            # Nach einer Antwort kurz ohne Wake-Word weiterhören; Stille führt zurück zum Wake-Word
            if folgefrage:
                text = hoere(direkt=True, max_warten_sek=NACHFRAGE_SEK)
            else:
                text = hoere()
            folgefrage = False
            if not text:
                continue
            sprich(ask(text))
            folgefrage = MODUS == "wake" and NACHFRAGE_SEK > 0
        except (KeyboardInterrupt, EOFError):
            print()
            break
        except Exception as fehler:  # Assistent soll bei einzelnen Fehlern weiterlaufen
            print(f"Fehler: {fehler}", file=sys.stderr)
            sprich("Da ist etwas schiefgegangen.")


if __name__ == "__main__":
    main()
