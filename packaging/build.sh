#!/bin/zsh
# Baut build/heyAtlas.app und build/heyAtlas-<Version>.dmg.
# Voraussetzung: Homebrew-Python 3.12 und die Umgebung .venv312 (siehe README).
# Die App nutzt das Homebrew-Python dieses Macs und läuft deshalb nur auf Macs, auf denen es installiert ist.
set -euo pipefail
cd "${0:A:h}/.."

VERSION=0.1.0
BUILD=$(date +%Y%m%d%H%M)
VENV=.venv312
APP=build/heyAtlas.app
INHALT=$APP/Contents
PY_FW=/opt/homebrew/opt/python@3.12/Frameworks/Python.framework/Versions/3.12
# Zur Laufzeit nie geladen, nur als Abhängigkeit von mlx-whisper installiert (gemessen): spart rund 670 MB
WEGLASSEN=(torch torchgen functorch sympy networkx mpmath PyObjCTest pip setuptools)

echo "▶ App-Gerüst"
rm -rf build && mkdir -p $INHALT/MacOS $INHALT/Resources/app/assets/icons/out
sed -e "s/VERSION/$VERSION/" -e "s/BUILD/$BUILD/" packaging/Info.plist > $INHALT/Info.plist
plutil -lint $INHALT/Info.plist >/dev/null
clang -O2 -Wall -Werror -o $INHALT/MacOS/heyAtlas packaging/launcher.c

echo "▶ Python"
# Der eigentliche Interpreter aus Python.app; bin/python3.12 würde in ein fremdes App-Paket umspringen
cp "$PY_FW/Resources/Python.app/Contents/MacOS/Python" $INHALT/MacOS/python
# Die Bibliothek über den stabilen opt-Pfad laden, damit ein "brew upgrade" auf 3.12.x die App nicht bricht
alt=$(otool -L $INHALT/MacOS/python | awk '/Python.framework\/Versions\/3.12\/Python / {print $1}')
install_name_tool -change "$alt" "$PY_FW/Python" $INHALT/MacOS/python 2>/dev/null

echo "▶ Pakete"
ausschluss=(--exclude __pycache__ --exclude "*.pyc")
for paket in $WEGLASSEN; do ausschluss+=(--exclude "/$paket" --exclude "/$paket-*.dist-info"); done
rsync -a $ausschluss $VENV/lib/python3.12/site-packages/ $INHALT/Resources/site-packages/
# Einmal vorkompilieren: Die App darf zur Laufzeit nichts ins signierte Paket schreiben
$VENV/bin/python -m compileall -q -j 0 $INHALT/Resources/site-packages >/dev/null 2>&1 || true

echo "▶ Atlas"
cp app.py assistent.py $INHALT/Resources/app/
cp -R assets/wakeword $INHALT/Resources/app/assets/
cp -R assets/icons/out/MenuBar $INHALT/Resources/app/assets/icons/out/
cp assets/icons/out/HeyAtlas.icns $INHALT/Resources/
$VENV/bin/python -m compileall -q $INHALT/Resources/app >/dev/null

echo "▶ Signatur (lokal, ohne Apple-Entwicklerkonto)"
codesign --force --deep --sign - $APP
codesign --verify --deep --strict $APP

echo "▶ DMG"
DMG=build/heyAtlas-$VERSION.dmg
uvx dmgbuild -s packaging/dmg_einstellungen.py -D app=$APP "heyAtlas" $DMG

echo "✔ $(du -sh $APP | cut -f1) App, $(du -sh $DMG | cut -f1) DMG: $DMG"
