#!/bin/zsh
# Startet den Sprachassistenten im Terminal (dort gelten Mikrofon-, Kalender- und Automationsrechte)
# und startet ihn nach einem Absturz neu. Ctrl+C beendet ihn endgültig.
source ~/.zshrc 2>/dev/null  # HEIMATORT und andere Einstellungen
cd "${0:A:h}"
while true; do
  .venv312/bin/python assistent.py
  [ $? -eq 0 ] && break
  echo "Assistent ist abgestürzt, Neustart in 5 Sekunden …"
  sleep 5
done
