<img width="1920" height="1120" alt="Screenshot from 2026-07-16 01-54-18" src="https://github.com/user-attachments/assets/d3ade380-b8ea-425a-83f8-54f7e1313d08" />

# 🔥 Jovy RE-7500 Control Room — Linux

Linux kontrolna aplikacija za Jovy RE-7500 SMD rework stanicu. Zamena za
originalni Windows softver — protokol je u potpunosti reverse-engineerovan
iz originalnog `.NET` softvera (nema sniffing-a, nema zavisnosti od Wine/VM).

![Python](https://img.shields.io/badge/python-3.x-blue)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-green)

## Šta radi

- Živi grafik temperature (klizeći 60s prozor)
- Kontrola gornjeg grejača (OFF / Reflow / Fast Reflow) i donjeg grejača
  (OFF / Preheat / Reflow / Fast Reflow)
- Kontrola ventilatora, sa automatskim pravilima za PARK/NORMAL mod stanice
- **Reflow profili** — proizvoljan broj koraka (ciljna temperatura + trajanje),
  bang-bang regulacija preko diskretnih nivoa grejača, čuvanje/učitavanje
  profila u JSON fajlu
- Zvučna signalizacija (buzzer) po koraku profila i pri promeni moda
- Dvojezični interfejs (srpski / engleski, toggle dugme)

## Protokol (reverse-engineerovan)

Stanica koristi FTDI **D2XX** (ne standardni COM/serijski port), pa je
komunikacija urađena preko `pyftdi`/`libusb`, ne preko `pyserial`.

```
Konekcija:  115200 8N1, RTS/CTS flow control, latency 1
Komanda:    AA | checksum | 01 | cmd     (cmd = 0xF0 + key, checksum = (1+cmd)&0xFF)
Status:     AA 02 01 01  ->  10B odgovor (header 0x55):
              [4]|[5]<<8 = temperatura °C
              [6] = LHStatus (donji grejač)   1=OFF 2=Preheat 3=Reflow 4=FastReflow
              [7] = UHStatus (gornji grejač)  1=OFF 2=Reflow 3=FastReflow
              [8] = MachineMode               1=NORMAL 2=PARK
              [9] = FanMode                   0=OFF 1=ON

Tasteri (GetKey): UH-Up=1  UH-Down=2  LH-Up=3  LH-Down=4  Fan=6  Buzzer=7
```

## Arhitektura

Pristup FTDI/libusb uređaju preko root privilegija (za USB reset) ne dozvoljava
prikaz X11 prozora (root nije autorizovan na korisnički display). Zato je
kontrola podeljena u dva procesa:

```
┌──────────────────────┐        unix socket         ┌──────────────────────┐
│   ROOT daemon         │ ◄────────────────────────► │   GUI (obican user)  │
│   drži FTDI vezu       │      /tmp/re7500.sock      │   tkinter + matplotlib│
└──────────────────────┘                             └──────────────────────┘
```

## Pokretanje

```bash
git clone https://github.com/Dejan-Port/re7500-control-room.git
cd re7500-control-room
pip install pyftdi pyusb matplotlib
./pokreni.sh
```

`pokreni.sh` pokreće root daemon (traži sudo/autentifikaciju) i GUI kao
običnog korisnika.

## Zavisnosti

- Python 3
- `pyftdi`, `pyusb` — FTDI D2XX komunikacija
- `matplotlib` — grafik temperature
- `tkinter` — GUI (obično već deo sistemskog Python-a)

## Licenca

MIT
