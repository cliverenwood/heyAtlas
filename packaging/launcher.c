// Startprogramm der App (Contents/MacOS/heyAtlas).
// Startet das mitgelieferte Python (Contents/MacOS/python) mit app.py. Das Python liegt im App-Paket,
// damit macOS den Prozess der App zuordnet: Berechtigungen, Menüleiste ohne Dock-Symbol, Name "heyAtlas".

#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

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

    char *argumente[] = {python, skript, NULL};
    execv(python, argumente);
    perror("heyAtlas: Python konnte nicht gestartet werden");
    return 1;
}
