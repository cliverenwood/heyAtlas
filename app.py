"""heyAtlas: Menüleisten-App um den Sprachassistenten in assistent.py.

Start zum Entwickeln:  .venv312/bin/python app.py
In der fertigen App startet Contents/MacOS/heyAtlas diese Datei mit der eingebauten Python-Umgebung.
"""

import os
import subprocess
import sys
import threading
import time

# In der App setzt das Startprogramm HEYATLAS_SITE auf Contents/Resources/site-packages
if os.environ.get("HEYATLAS_SITE"):
    import site

    site.addsitedir(os.environ["HEYATLAS_SITE"])

ORDNER = os.path.dirname(os.path.abspath(__file__))
LOGDATEI = os.path.expanduser("~/Library/Logs/heyAtlas.log")

# Ohne Terminal landen alle Ausgaben in der Logdatei; vor dem Import von assistent, damit nichts verloren geht
if not sys.stdout.isatty():
    os.makedirs(os.path.dirname(LOGDATEI), exist_ok=True)
    sys.stdout = sys.stderr = open(LOGDATEI, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    print(f"\n===== heyAtlas gestartet {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

import rumps  # noqa: E402

import assistent as atlas  # noqa: E402

ICONS = os.path.join(ORDNER, "assets", "icons", "out", "MenuBar")
ICON_BEREIT = os.path.join(ICONS, "AtlasIdleTemplate.png")
ICON_HOERT = os.path.join(ICONS, "AtlasListeningTemplate.png")

STIMMEN = {
    "Thorsten": "de_DE-thorsten-medium",
    "Thorsten, hohe Qualität (114 MB)": "de_DE-thorsten-high",
    "Kerstin": "de_DE-kerstin-low",
    "Ramona": "de_DE-ramona-low",
    "Eva": "de_DE-eva_k-x_low",
    "Karlsson": "de_DE-karlsson-low",
    "Pavoque": "de_DE-pavoque-low",
}
AUSWAHLEN = {
    # Menütitel: (Einstellung, {Anzeigename: Wert})
    "Claude-Modell": ("CLAUDE_MODELL", {
        "Claude Sonnet 5 (schnell)": "claude-sonnet-5",
        "Claude Opus 5 (gründlicher, langsamer)": "claude-opus-5",
        "Claude Haiku 4.5": "claude-haiku-4-5",
    }),
    "Aktivierungswort": ("WAKEWORD", {
        "Hey Jarvis": "hey_jarvis",
        "Alexa": "alexa",
        "Hey Mycroft": "hey_mycroft",
        "Hey Atlas (eigenes Modell)": atlas.HEY_ATLAS,
    }),
    "Empfindlichkeit": ("WAKEWORD_SCHWELLE", {
        "Sehr empfindlich (0,3)": "0.3",
        "Empfindlich (0,4)": "0.4",
        "Normal (0,5)": "0.5",
        "Zurückhaltend (0,6)": "0.6",
        "Streng (0,7)": "0.7",
    }),
    "Browser für YouTube": ("BROWSER", {"Safari": "Safari", "Google Chrome": "Google Chrome"}),
}
TEXTFELDER = {
    # Menütitel: (Einstellung, Erklärung)
    "Heimatort …": ("HEIMATORT", "Ort für Wetterfragen ohne Ortsangabe, z. B. Hamburg."),
    "Nachfragezeit …": ("NACHFRAGE_SEK", "Sekunden, die Atlas nach einer Antwort ohne Aktivierungswort "
                                         "weiter zuhört (0 schaltet das ab)."),
    "Kurzbefehl-Ordner …": ("KURZBEFEHL_ORDNER", "Ordner in der Kurzbefehle-App, dessen Kurzbefehle Atlas "
                                                 "ausführen darf."),
    "Heimathaltestelle …": ("HEIMATHALTESTELLE", "Haltestelle für „Wann fährt meine Bahn?“, "
                                                 "z. B. Leipzig, Breite Str."),
    "Fahrplan-Kontakt …": ("FAHRPLAN_KONTAKT", "Kontaktangabe, die Atlas bei Fahrplanabfragen an Transitous "
                                               "mitschickt (E-Mail oder Website). Transitous verlangt das."),
}
SCHLUESSEL = {
    # Menütitel: (Schlüsselbund-Dienst, Erklärung)
    "Anthropic-API-Schlüssel …": (atlas.SCHLUESSELBUND_DIENST, "Anthropic-API-Schlüssel (beginnt mit sk-ant-)."),
    "Spotify Client-ID …": ("spotify-client-id", "Client ID aus dem Spotify-Developer-Dashboard."),
    "Spotify Client-Secret …": ("spotify-client-secret", "Client Secret aus dem Spotify-Developer-Dashboard."),
    "YouTube-API-Schlüssel …": ("youtube-api-key", "API-Schlüssel für die YouTube Data API v3."),
}


EINSTELLUNGSSEITE = "x-apple.systempreferences:com.apple.preference.security?"
OPTIONALE_FUNKTIONEN = {
    # Menütitel: (Einstellung, Systemeinstellungen-Seite, Text beim Einschalten, Text beim Ausschalten)
    "Nachrichten vorlesen (iMessage/SMS)": (
        "NACHRICHTEN_LESEN", EINSTELLUNGSSEITE + "Privacy_AllFiles",
        "Atlas liest dann deine iMessages und SMS aus der Nachrichten-Datenbank und kann darauf antworten. Dafür "
        "braucht heyAtlas „Festplattenvollzugriff“. Diese Berechtigung erlaubt das Lesen aller Dateien; heyAtlas "
        "öffnet nur die Nachrichten-Datenbank. Vorgelesene Nachrichten gehen zur Beantwortung an Claude.\n\n"
        "Gleich öffnen sich die Systemeinstellungen: heyAtlas dort unter Festplattenvollzugriff einschalten. "
        "SMS kommen nur mit eingeschalteter SMS-Weiterleitung auf dem iPhone an.",
        "Atlas liest dann keine Nachrichten mehr. Entziehe heyAtlas bitte auch den Festplattenvollzugriff in den "
        "Systemeinstellungen, die sich gleich öffnen."),
    "Anrufe annehmen": (
        "ANRUFE_ANNEHMEN", EINSTELLUNGSSEITE + "Privacy_Accessibility",
        "Atlas sagt dann eingehende FaceTime- und iPhone-Anrufe an; mit „Hey Atlas, nimm an“ oder „… lehn ab“ "
        "nimmt er sie an oder lehnt sie ab. Dafür braucht heyAtlas zwei Berechtigungen: „Bedienungshilfen“ (andere "
        "Apps bedienen, klicken) und „Bildschirm- und Systemaudioaufnahme“ (Atlas fotografiert nur den Bereich der "
        "Anruf-Mitteilung, um den grünen bzw. roten Knopf zu finden). macOS fragt gleich danach; nach dem Erlauben "
        "der Bildschirmaufnahme heyAtlas einmal neu starten.\n\nFür iPhone-Anrufe: iPhone → Telefon → Auf "
        "anderen Geräten und Mac → FaceTime → Anrufe vom iPhone einschalten.",
        "Atlas nimmt dann keine Anrufe mehr an. Entziehe heyAtlas bitte auch Bedienungshilfen und Bildschirmaufnahme "
        "in den Systemeinstellungen, die sich gleich öffnen."),
}


def menueleisten_bild(pfad_1x: str):
    """Symbol mit allen Auflösungen (1x, 2x, 3x) als Vorlage. rumps lädt nur die 1x-Datei und vergrößert sie;
    auf Retina-Bildschirmen wirkt das Symbol dann unscharf."""
    from AppKit import NSImage, NSImageRep

    groesse = (18, 18)  # Punkte, Standardgröße für Menüleisten-Symbole
    bild = NSImage.alloc().initWithSize_(groesse)
    basis = pfad_1x.removesuffix(".png")
    for datei in (pfad_1x, basis + "@2x.png", basis + "@3x.png"):
        darstellung = NSImageRep.imageRepWithContentsOfFile_(datei)
        if darstellung is not None:
            darstellung.setSize_(groesse)
            bild.addRepresentation_(darstellung)
    bild.setTemplate_(True)  # macOS färbt es passend zu hellem und dunklem Modus
    return bild


def app_paket() -> str:
    """Pfad der .app, wenn Atlas als App läuft, sonst leer. Python liegt dort in Contents/MacOS."""
    paket = os.path.dirname(os.path.dirname(os.path.dirname(sys.executable)))
    return paket if paket.endswith(".app") else ""


class AtlasApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("heyAtlas", icon=ICON_BEREIT, template=True, quit_button=None)
        self.bilder = {ICON_BEREIT: menueleisten_bild(ICON_BEREIT), ICON_HOERT: menueleisten_bild(ICON_HOERT)}
        self._icon_nsimage = self.bilder[ICON_BEREIT]  # rumps setzt dieses Bild beim Start ins Symbol
        self.status = rumps.MenuItem("Startet …")
        self.pause = rumps.MenuItem("Pausieren", callback=self.pausieren)
        self.autostart = rumps.MenuItem("Bei Anmeldung starten", callback=self.autostart_umschalten)
        self.pmset = rumps.MenuItem("Mac vor Weckern aufwecken", callback=self.pmset_umschalten)
        self.menu = [
            self.status, self.pause, None,
            self.einstellungsmenue(), None,
            rumps.MenuItem("Berechtigungen prüfen …", callback=self.berechtigungen_pruefen),
            rumps.MenuItem("Logdatei öffnen", callback=lambda _: subprocess.run(["/usr/bin/open", LOGDATEI])),
            # Von Transitous verlangte sichtbare Quellenangabe für die Fahrplandaten
            rumps.MenuItem("Fahrplandaten: Transitous",
                           callback=lambda _: subprocess.run(["/usr/bin/open", "https://transitous.org/sources/"])),
            rumps.MenuItem("heyAtlas beenden", callback=lambda _: rumps.quit_application()),
        ]
        self.autostart.state = self.autostart_aktiv()
        self.pmset.state = atlas.pmset_eingerichtet()
        self.letztes_icon = ICON_BEREIT
        self.neustart_angefordert = False  # aus Hintergrund-Threads gesetzt, im Hauptthread ausgeführt
        self.berechtigungs_bericht = None  # ebenso: Übersicht nach der Abfrage, angezeigt im Hauptthread
        rumps.Timer(self.aktualisieren, 0.3).start()
        threading.Thread(target=self.starte_assistent, daemon=True).start()

    # ------------------------------------------------------------ Aufbau

    def einstellungsmenue(self) -> rumps.MenuItem:
        menue = rumps.MenuItem("Einstellungen")
        stimmen = rumps.MenuItem("Stimme")
        # Piper-Stimmen (werden bei Bedarf geladen), dann die installierten macOS-Systemstimmen
        self.stimmen = dict(STIMMEN)
        macos = {f"{name} (macOS)": atlas.MACOS_PRAEFIX + name for name in atlas.macos_stimmen()}
        for gruppe in (STIMMEN, macos):
            for titel, wert in gruppe.items():
                eintrag = rumps.MenuItem(titel, callback=self.stimme_waehlen)
                eintrag.state = wert == atlas.STIMME
                stimmen.add(eintrag)
            stimmen.add(None)
        self.stimmen.update(macos)
        if not macos:
            stimmen.add(rumps.MenuItem("Bessere Stimmen: Systemeinstellungen → Bedienungshilfen → "
                                       "Gesprochene Inhalte → Stimmen verwalten"))
            stimmen.add(None)
        stimmen.add(rumps.MenuItem("Probehören", callback=lambda _: self.probehoeren(atlas.STIMME)))
        menue.add(stimmen)
        for titel, (name, werte) in AUSWAHLEN.items():
            untermenue = rumps.MenuItem(titel)
            aktuell = str(getattr(atlas, name.replace("CLAUDE_", "")))  # wirksamer Wert, auch der Standard
            for anzeige, wert in werte.items():
                eintrag = rumps.MenuItem(anzeige, callback=lambda e, n=name, w=werte: self.auswahl(n, w[e.title]))
                eintrag.state = wert == aktuell
                untermenue.add(eintrag)
            menue.add(untermenue)
        menue.add(None)
        for titel in TEXTFELDER:
            menue.add(rumps.MenuItem(titel, callback=self.textfeld))
        schluessel = rumps.MenuItem("Schlüssel")
        for titel in SCHLUESSEL:
            schluessel.add(rumps.MenuItem(titel, callback=self.schluessel_eingeben))
        menue.add(schluessel)
        menue.add(None)
        for titel, (name, *_rest) in OPTIONALE_FUNKTIONEN.items():
            schalter = rumps.MenuItem(titel, callback=self.optionale_funktion)
            schalter.state = bool(getattr(atlas, name))
            menue.add(schalter)
        menue.add(None)
        menue.add(self.autostart)
        menue.add(self.pmset)
        return menue

    # ------------------------------------------------------------ Assistent

    def starte_assistent(self) -> None:
        # Beim ersten Start alle Zugriffe auf einmal abfragen, nicht erst mitten in der Nutzung
        if not atlas.einstellungen_laden().get("BERECHTIGUNGEN_EINGERICHTET"):
            self.berechtigungen_einrichten()
        if not atlas.api_schluessel():
            atlas.melde_status("Anthropic-API-Schlüssel fehlt (Einstellungen → Schlüssel)")
        while True:  # ein Fehler beim Start (z. B. in der Sprachausgabe) soll Atlas nicht dauerhaft lahmlegen
            try:
                atlas.main()
                return
            except Exception:
                import traceback

                traceback.print_exc()
                atlas.melde_status("Fehler beim Start, versuche es erneut …")
                time.sleep(5)

    def berechtigungen_einrichten(self) -> None:
        ergebnisse = atlas.berechtigungen_einrichten()
        werte = atlas.einstellungen_laden()
        werte["BERECHTIGUNGEN_EINGERICHTET"] = True
        atlas.einstellungen_speichern(werte)
        self.berechtigungs_bericht = ergebnisse

    def berechtigungen_pruefen(self, _eintrag) -> None:
        threading.Thread(target=self.berechtigungen_einrichten, daemon=True).start()

    @staticmethod
    def bericht_text(ergebnisse: list) -> str:
        zeichen = {"erlaubt": "✓", "nicht installiert": "–"}
        zeilen = [f"{zeichen.get(ergebnis, '✗')} {bereich}: {ergebnis}" for bereich, ergebnis in ergebnisse]
        if any(ergebnis not in zeichen for _, ergebnis in ergebnisse):
            zeilen += ["", "Nachträglich erlauben: Systemeinstellungen → Datenschutz & Sicherheit → Mikrofon, "
                           "Kalender, Erinnerungen bzw. Automation → heyAtlas."]
        return "\n".join(zeilen)

    def aktualisieren(self, _timer) -> None:
        if self.berechtigungs_bericht is not None:
            ergebnisse, self.berechtigungs_bericht = self.berechtigungs_bericht, None
            rumps.alert("Berechtigungen für heyAtlas", self.bericht_text(ergebnisse))
        if self.neustart_angefordert:
            self.neustart_angefordert = False
            self.neustarten()
        self.status.title = atlas.STATUS["text"]
        icon = ICON_HOERT if atlas.STATUS["hoert"] else ICON_BEREIT
        if icon != self.letztes_icon:
            self._nsapp.nsstatusitem.button().setImage_(self.bilder[icon])
            self.letztes_icon = icon

    def pausieren(self, eintrag) -> None:
        if atlas.PAUSIERT.is_set():
            atlas.ANRUF["pausiert_seit"] = 0.0  # auch eine Pause wegen eines Anrufs beenden
            atlas.PAUSIERT.clear()
            eintrag.title = "Pausieren"
        else:
            atlas.PAUSIERT.set()
            eintrag.title = "Fortsetzen"

    # ------------------------------------------------------------ Einstellungen

    def speichern_und_neustarten(self, name: str, wert: str) -> None:
        werte = atlas.einstellungen_laden()
        werte[name] = wert
        atlas.einstellungen_speichern(werte)
        self.neustarten()

    def neustarten(self) -> None:
        """Die Einstellungen werden beim Start gelesen; die App startet sich deshalb selbst neu."""
        paket = app_paket()
        if not paket:
            rumps.alert("Gespeichert", "Starte app.py neu, damit die Änderung wirkt.")
            return
        subprocess.Popen(["/bin/sh", "-c", f'sleep 1; /usr/bin/open -n "{paket}"'])
        rumps.quit_application()

    def auswahl(self, name: str, wert: str) -> None:
        self.speichern_und_neustarten(name, wert)

    def textfeld(self, eintrag) -> None:
        name, erklaerung = TEXTFELDER[eintrag.title]
        fenster = rumps.Window(erklaerung, eintrag.title.rstrip(" …"), default_text=atlas.einstellung(name, ""),
                               ok="Speichern", cancel="Abbrechen", dimensions=(320, 24))
        antwort = fenster.run()
        if antwort.clicked:
            self.speichern_und_neustarten(name, antwort.text.strip())

    def schluessel_eingeben(self, eintrag) -> None:
        dienst, erklaerung = SCHLUESSEL[eintrag.title]
        vorhanden = " Ein Schlüssel ist bereits gespeichert." if atlas.schluesselbund(dienst) else ""
        fenster = rumps.Window(erklaerung + vorhanden + " Er wird im macOS-Schlüsselbund gespeichert.",
                               eintrag.title.rstrip(" …"), ok="Speichern", cancel="Abbrechen",
                               dimensions=(320, 24), secure=True)
        antwort = fenster.run()
        if antwort.clicked and antwort.text.strip():
            try:
                atlas.schluesselbund_speichern(dienst, antwort.text.strip())
            except (ValueError, RuntimeError) as fehler:
                rumps.alert("Nicht gespeichert", str(fehler))
                return
            self.neustarten()

    def stimme_waehlen(self, eintrag) -> None:
        name = self.stimmen[eintrag.title]

        def laden_und_wechseln() -> None:
            if not name.startswith(atlas.MACOS_PRAEFIX):
                atlas.stimmen_pfad(name)  # lädt eine Piper-Stimme beim ersten Mal herunter
            self.probehoeren(name)
            werte = atlas.einstellungen_laden()
            werte["STIMME"] = name
            atlas.einstellungen_speichern(werte)
            atlas.melde_status("Neue Stimme gespeichert, startet neu …")
            time.sleep(4)  # Probe zu Ende hören lassen
            self.neustart_angefordert = True

        threading.Thread(target=laden_und_wechseln, daemon=True).start()

    def probehoeren(self, name: str) -> None:
        def sprechen() -> None:
            atlas.vorlesen("Hallo, ich bin Atlas. So klinge ich mit dieser Stimme.", name)

        threading.Thread(target=sprechen, daemon=True).start()

    def optionale_funktion(self, eintrag) -> None:
        name, seite, text_an, text_aus = OPTIONALE_FUNKTIONEN[eintrag.title]
        einschalten = not eintrag.state
        if rumps.alert(eintrag.title, text_an if einschalten else text_aus,
                       ok="Einschalten" if einschalten else "Ausschalten", cancel="Abbrechen") != 1:
            return
        werte = atlas.einstellungen_laden()
        werte[name] = einschalten
        atlas.einstellungen_speichern(werte)
        if einschalten and name == "ANRUFE_ANNEHMEN":
            atlas.bedienungshilfen_erlaubt(nachfragen=True)  # macOS zeigt eigene Dialoge mit Link
            atlas.bildschirmaufnahme_erlauben()
        else:
            subprocess.run(["/usr/bin/open", seite])  # Berechtigung erteilen bzw. wieder entziehen
        self.neustarten()

    # ------------------------------------------------------------ Autostart und pmset

    @staticmethod
    def anmeldedienst():
        from ServiceManagement import SMAppService

        return SMAppService.mainAppService()

    def autostart_aktiv(self) -> bool:
        if not app_paket():
            return False
        from ServiceManagement import SMAppServiceStatusEnabled

        return self.anmeldedienst().status() == SMAppServiceStatusEnabled

    def autostart_umschalten(self, eintrag) -> None:
        if not app_paket():
            rumps.alert("Nur in der App", "Der Autostart lässt sich nur in der installierten App einschalten.")
            return
        dienst = self.anmeldedienst()
        ok, fehler = (dienst.unregisterAndReturnError_(None) if eintrag.state
                      else dienst.registerAndReturnError_(None))
        if not ok:
            rumps.alert("Autostart", f"macOS hat abgelehnt: {fehler.localizedDescription() if fehler else '?'}")
        eintrag.state = self.autostart_aktiv()

    def pmset_umschalten(self, eintrag) -> None:
        try:
            atlas.pmset_entfernen() if eintrag.state else atlas.pmset_einrichten()
        except RuntimeError as fehler:  # z. B. Admin-Abfrage abgebrochen
            print(f"pmset: {fehler}")
        eintrag.state = atlas.pmset_eingerichtet()


if __name__ == "__main__":
    AtlasApp().run()
