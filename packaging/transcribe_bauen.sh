#!/bin/zsh
# Baut transcribe.cpp (MIT, github.com/handy-computer/transcribe.cpp) für die Spracherkennung Cohere Transcribe
# nach build/transcribe/ und installiert die Python-Anbindung in .venv312.
# Voraussetzung: cmake (brew install cmake). build.sh nimmt die Bibliotheken danach in die App auf.
set -euo pipefail
cd "${0:A:h}/.."

STAND=b61b7f9  # getesteter Stand vom 2026-10-16; die Anbindung prüft die Version der Bibliothek beim Laden
QUELLE=build/transcribe.cpp
ZIEL=build/transcribe

if [[ ! -d $QUELLE ]]; then
  git clone -q https://github.com/handy-computer/transcribe.cpp.git $QUELLE
fi
git -C $QUELLE fetch -q origin && git -C $QUELLE checkout -q $STAND

# Metal ist auf Apple Silicon automatisch an. @loader_path: alle Bibliotheken liegen später in einem Ordner
cmake -S $QUELLE -B $QUELLE/build-shared -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON \
  -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF \
  -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON -DCMAKE_INSTALL_RPATH=@loader_path >/dev/null
cmake --build $QUELLE/build-shared -j 8 --target transcribe >/dev/null

rm -rf $ZIEL && mkdir -p $ZIEL
# Nur die versionierten Namen, auf die die Bibliotheken untereinander verweisen, plus den Einstiegspunkt
cp $QUELLE/build-shared/src/libtranscribe.0.4.dylib $ZIEL/libtranscribe.dylib
find $QUELLE/build-shared/ggml \( -name "libggml.0.dylib" -o -name "libggml-*[a-z].0.dylib" \) -exec cp {} $ZIEL/ \;
.venv312/bin/python -m pip install -q --no-deps $QUELLE/bindings/python
echo "✔ $(du -sh $ZIEL | cut -f1) in $ZIEL, Python-Anbindung in .venv312"
