#!/bin/bash
# Jovy RE-7500 Control Room (Linux)
# Arhitektura: root daemon drži FTDI, GUI radi kao običan korisnik.
DN=/tmp/claude-1000/-home-port/d1101069-9d31-4f68-9fc2-ac2708f07bd8/scratchpad/dnenv
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP="$DIR/re7500_control.py"

cleanup() {
    sudo pkill -f "re7500_control.py --daemon" 2>/dev/null
    sudo rm -f /tmp/re7500.sock 2>/dev/null
}
trap cleanup EXIT

# 1) Root daemon za FTDI (pyftdi treba root za USB reset)
sudo pkill -f "re7500_control.py --daemon" 2>/dev/null; sleep 0.5
sudo "$DN/bin/python" "$APP" --daemon &
sleep 2

# 2) GUI kao običan korisnik — X radi bez problema
"$DN/bin/python" "$APP"
