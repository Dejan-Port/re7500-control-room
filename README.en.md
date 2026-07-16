**[Srpski](README.md) · English**

<img width="1920" height="1120" alt="Screenshot from 2026-07-16 01-54-18" src="https://github.com/user-attachments/assets/d3ade380-b8ea-425a-83f8-54f7e1313d08" />

# 🔥 Jovy RE-7500 Control Room — Linux

Linux control application for the Jovy RE-7500 SMD rework station. A
replacement for the original Windows software — the protocol has been
fully reverse-engineered from the original `.NET` software (no sniffing,
no dependency on Wine/VM).

![Python](https://img.shields.io/badge/python-3.x-blue)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## Features

- Live temperature graph (sliding 60s window)
- Upper heater control (OFF / Reflow / Fast Reflow) and lower heater control
  (OFF / Preheat / Reflow / Fast Reflow)
- Fan control, with automatic rules for the station's PARK/NORMAL mode
- **Reflow profiles** — any number of steps (target temperature + duration),
  bang-bang regulation via the heater's discrete power levels, save/load
  profiles to/from a JSON file
- Buzzer signaling per profile step and on mode changes
- Bilingual interface (Serbian / English, toggle button)

## Protocol (reverse-engineered)

The station uses FTDI **D2XX** (not a standard COM/serial port), so
communication is done via `pyftdi`/`libusb`, not `pyserial`.

```
Connection: 115200 8N1, RTS/CTS flow control, latency 1
Command:    AA | checksum | 01 | cmd     (cmd = 0xF0 + key, checksum = (1+cmd)&0xFF)
Status:     AA 02 01 01  ->  10B response (header 0x55):
              [4]|[5]<<8 = temperature °C
              [6] = LHStatus (lower heater)   1=OFF 2=Preheat 3=Reflow 4=FastReflow
              [7] = UHStatus (upper heater)   1=OFF 2=Reflow 3=FastReflow
              [8] = MachineMode               1=NORMAL 2=PARK
              [9] = FanMode                   0=OFF 1=ON

Keys (GetKey): UH-Up=1  UH-Down=2  LH-Up=3  LH-Down=4  Fan=6  Buzzer=7
```

## Architecture

Accessing the FTDI/libusb device requires root privileges (for USB reset),
but root is not authorized for the user's X11 display, so it can't show a
GUI window. That's why control is split into two processes:

```
┌──────────────────────┐        unix socket         ┌──────────────────────┐
│   ROOT daemon         │ ◄────────────────────────► │   GUI (regular user) │
│   holds the FTDI link │      /tmp/re7500.sock      │   tkinter + matplotlib│
└──────────────────────┘                             └──────────────────────┘
```

## Running

```bash
git clone https://github.com/Dejan-Port/re7500-control-room.git
cd re7500-control-room
pip install pyftdi pyusb matplotlib
./pokreni.sh
```

`pokreni.sh` starts the root daemon (will prompt for sudo/authentication)
and the GUI as a regular user.

## Dependencies

- Python 3
- `pyftdi`, `pyusb` — FTDI D2XX communication
- `matplotlib` — temperature graph
- `tkinter` — GUI (usually already part of the system Python)

## License

MIT
