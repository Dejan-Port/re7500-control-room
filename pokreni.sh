#!/bin/bash
# Jovy RE-7500 Control Room (Linux)
# Arhitektura: root daemon drži FTDI, GUI radi kao običan korisnik.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$DIR/re7500_control.py"
VENV="$DIR/venv"

# Napravi venv i instaliraj zavisnosti pri prvom pokretanju
if [ ! -d "$VENV" ]; then
    echo "Prvo pokretanje — pravim venv i instaliram zavisnosti..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install --quiet -r "$DIR/requirements.txt"
fi

cleanup() {
    sudo pkill -f "re7500_control.py --daemon" 2>/dev/null
    sudo rm -f /tmp/re7500.sock 2>/dev/null
}
trap cleanup EXIT

# 1) Root daemon za FTDI (pyftdi treba root za USB reset)
sudo pkill -f "re7500_control.py --daemon" 2>/dev/null; sleep 0.5
sudo "$VENV/bin/python" "$APP" --daemon &
sleep 2

# 2) GUI kao običan korisnik — X radi bez problema
"$VENV/bin/python" "$APP"
