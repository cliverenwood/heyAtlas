// Startprogramm der App (Contents/MacOS/heyAtlas).
// Startet das mitgelieferte Python (Contents/MacOS/python) mit app.py als Kindprozess und wartet auf dessen Ende.
//
// Wichtig: Nicht per execv ersetzen. macOS 26 vergibt den Platz in der Menüleiste nur, wenn die Anfrage zur
// Code-Kennung des gestarteten App-Prozesses passt. Ersetzt sich heyAtlas durch Python, fragt "python" im
// Namen von heyAtlas an und bekommt nie einen Platz (das Symbol bleibt unsichtbar, gemessen). Als Kindprozess
// funktioniert es; Berechtigungen ordnet macOS trotzdem der App zu.

#include <errno.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;
static pid_t kind = 0;

// Beenden-Signale (z. B. beim Abmelden) an Python weiterreichen, damit kein verwaister Prozess bleibt
static void weiterreichen(int signal_nummer) {
    if (kind > 0) kill(kind, signal_nummer);
}

int main(void) {
    char pfad[PATH_MAX], echt[PATH_MAX];
    uint32_t groesse = sizeof(pfad);
    if (_NSGetExecutablePath(pfad, &groesse) != 0 || realpath(pfad, echt) == NULL) {
        fprintf(stderr, "heyAtlas: eigener Pfad nicht ermittelbar\n");
        return 1;
    }
    char macos[PATH_MAX];
    strlcpy(macos, dirname(echt), sizeof(macos));  // .../heyAtlas.app/Contents/MacOS

    char python[PATH_MAX], skript[PATH_MAX], pakete[PATH_MAX];
    snprintf(python, sizeof(python), "%s/python", macos);
    snprintf(skript, sizeof(skript), "%s/../Resources/app/app.py", macos);
    snprintf(pakete, sizeof(pakete), "%s/../Resources/site-packages", macos);

    // app.py bindet die Pakete per site.addsitedir ein (so greifen auch .pth-Dateien)
    setenv("HEYATLAS_SITE", pakete, 1);
    setenv("PYTHONNOUSERSITE", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  // nichts ins signierte App-Paket schreiben
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");

    signal(SIGTERM, weiterreichen);
    signal(SIGINT, weiterreichen);
    signal(SIGHUP, weiterreichen);

    char *argumente[] = {python, skript, NULL};
    if (posix_spawn(&kind, python, NULL, NULL, argumente, environ) != 0) {
        perror("heyAtlas: Python konnte nicht gestartet werden");
        return 1;
    }
    int status = 0;
    while (waitpid(kind, &status, 0) < 0) {
        if (errno != EINTR) return 1;  // nur nach einem weitergereichten Signal weiter warten
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
