# RN424 front panel: how the pin map was found

Everything here was recovered from NETGEAR's own GPL kernel for ReadyNAS OS 6,
taken from the `kernel` file on the original 256 MB DOM. Two builds were
examined and agree byte-for-byte on every value below:

* `4.4.218.x86_64.1` (built 2023-06-28)
* `4.4.178.x86_64.1` (built 2019-04-24)

Reproduce it with [`tools/dump_pinmap.py`](../tools/dump_pinmap.py).

## Result

| Line | RN422 / RN424 (`rn422_4`) | RN426 / RN428 (`rn426_8`) |
|---|---|---|
| MOSI (data) | pin 34 → `0xFDC204A0` | pin 30 → `0xFDC20480` |
| CLK | pin 35 → `0xFDC204A8` | pin 28 → `0xFDC20470` |
| D/C (low = command) | pin 0 → `0xFDC204D8` | pin 7 → `0xFDC50580` |
| CS (active low) | pin 122 → `0xFDC50560` | pin 8 → `0xFDC505C8` |
| EN (panel enable) | pin 17 → `0xFDC20418` | same |
| RST | pin 31 → `0xFDC20488` | same |
| Button interrupt (MSP430) | pin 5 → `0xFDC50570`, input, active low | same |

Pins are NETGEAR `gpio_dnv` numbers; addresses are physical `PADCFG_DW0`
registers. The RN426 column reproduces the addresses in
[riplatt/truenas-rn426-panel](https://github.com/riplatt/truenas-rn426-panel),
which are known-good on real hardware – that's the cross-check for the method.

Display: SSD1305, 128×32 (4 pages), 4-wire SPI, MSB first, data latched on the
rising clock edge, visible columns 4–131. 33-byte init sequence from
`init_oled()`:

```
ae d5 71 a8 1f d9 22 20 02 a1 c8 da 12 d8 00 81 cf b0 d3 00 21 04 83 22 00 03 10 00 40 a6 a4 db 18
```

(It contains no display-on; `0xAF` is sent afterwards.)

Buttons: an MSP430 on the front board at SMBus address `0x1C` on the Intel
**I801** adapter. Register `0x04` is the button bitmap: left bit 0, right 1,
up 2, down 3, OK 4.

### Live register values on an RN424 running OMV

```
DMI  : ReadyNAS 424 | 01/23/2018 ReadyNAS 424 V3.11
PADBAR N/S : 0x00000400 / 0x00000400
MOSI N+0x4a0 DW0=0x05000200  output tx=0
CLK  N+0x4a8 DW0=0x05000201  output tx=1
DC   N+0x4d8 DW0=0x45000201  output tx=1
CS   S+0x560 DW0=0x45000201  output tx=1
EN   N+0x418 DW0=0x45000201  output tx=1
RST  N+0x488 DW0=0x05000201  output tx=1
INT  S+0x570 DW0=0x45000102  input  rx=1
```

The idle levels (CS, D/C and CLK high) are exactly what `spi_send()` leaves
behind after a byte, which confirms these are the lines the BIOS used to draw
"Booting…".

## How the numbers were derived

### 1. Kernel and symbols

The DOM's `kernel` is a bzImage with an xz payload. Decompress it, then rebuild
a symbol table from the embedded kallsyms with
[vmlinux-to-elf](https://github.com/marin-m/vmlinux-to-elf). Relevant
functions: `rn_oled_init`, `oled_probe`, `spi_send`, `init_oled`,
`clear_oled`, `readynas_io_compatible`, `dnv_gpio_init`, `dnv_gpio_probe`,
`dnv_gpio_reg`, `reg_map_offset_dnv`, `dnv_gpio_direction_output`,
`i2cfb_init`, `i2cfb_button_depressed`, `button_irq_init`.

### 2. Per-model display config

`rn_oled_init()` walks a table in `.data` (entries 0x1C0 bytes apart) and
picks the first entry whose model alias matches the machine. Each entry:
`+0x00` model alias, `+0x08` gpiochip label, `+0x10..+0x24` six `u32` pins.
`spi_send()` and `init_oled()` give the roles of the six slots: MOSI, CLK, D/C
(set to `!is_command`), CS, EN (set after the screen is cleared), RST (pulsed).

The full table, for anyone porting to other models:

| Alias | Models | gpiochip | MOSI | CLK | DC | CS | EN | RST |
|---|---|---|---|---|---|---|---|---|
| `rn316` | RN316 | `gpio_ich` | 21 | 19 | 16 | 7 | 32 | 24 |
| `rn422_4` | RN422, RN424 | `gpio_dnv.0` | 34 | 35 | 0 | 122 | 17 | 31 |
| `rn426_8` | RN426, RN428 | `gpio_dnv.0` | 30 | 28 | 7 | 8 | 17 | 31 |
| `rnx16` | RN516, RN716, RDD516 | `gpio_ich` | 54 | 52 | 32 | 50 | 6 | 7 |
| `rnx24` | RN524, RN624 | `gpio_ich` | 54 | 1 | 32 | 50 | 6 | 7 |
| `rnx26` | RN526, RN626 | `gpio_ich` | 54 | 1 | 32 | 50 | 6 | 7 |
| `rnx28` | RN528, RN628 | `gpio_ich` | 54 | 1 | 32 | 50 | 6 | 7 |

Model matching (`readynas_io_compatible`): the DMI product name or version must
contain e.g. `ReadyNAS 424` or `RN424`, followed by end of string, a space,
`X` or `S`. Alias groups: `rn422_4` = {rn422, rn424}, `rn426_8` = {rn426,
rn428}, `rn42x` = all four.

### 3. gpio_dnv pin number → register address

NETGEAR's `gpio_dnv` driver does **not** use mainline `pinctrl-denverton`
numbering, so the pin numbers can't be used with a mainline gpiochip.
`dnv_gpio_reg(chip, pin, 5)` returns:

```
mmio_base + PADBAR(0x400) + reg_map_offset_dnv(5, pin)
```

* `mmio_base` is hard-coded in the platform device registered by
  `dnv_gpio_init()`: memory `0xFDC20000–0xFDC5FFFF` (North community, South at
  +0x30000), IRQ 9. The driver checks that the register at `base+0x0C`
  (PADBAR) reads `0x400` before using it.
* `reg_map_offset_dnv(5, pin)` looks the pin up in a 153-entry table
  ("table5") and adds `0x30000` (→ South community) for pins 3–13, 53–140 and
  146–152. Pins 46 and 141–145 are invalid.

The stock kernel never touches P2SB, so the GPIO MMIO works with P2SB hidden.

### 4. PADCFG_DW0 bits

From `dnv_gpio_direction_output()` (clears bit 8, sets bit 9) and
`dnv_gpio_direction_input()` (clears bit 9, sets bit 8):

| Bit | Meaning |
|---|---|
| 0 | TX state (output level) |
| 1 | RX state (input level) |
| 8 | **TX disable** (1 = not an output) |
| 9 | **RX disable** |
| 10–12 | pad mode (0 = GPIO) |
| 17–20 | interrupt routing (NMI / SMI / SCI / IOxAPIC) |

The RN426 driver treats bit 9 as TX disable; it works there only because the
BIOS already left the pads as outputs.

### 5. Buttons

The `rn42x` button table has two entries: a `reset` button on `gpio_it87`
pin 9 (ITE Super I/O), and the `front-board` controller on `gpio_dnv.0` pin 5
(→ `0xFDC50570`) handled by `i2cfb_*` with five `fb_button`s (left, right, up,
down, ok → bits 0–4 of register `0x04`). For comparison, `rnx2x` uses
`gpio_it87` pin 23 for reset and `gpio_ich` line 2 for the front board.

At boot the stock `i2cfb_init()` reads MSP430 register `0x00` (expects
`0x46`), writes `0x0F` to register `0x02`, reads `0x04` and writes `0` to
`0x05`. **Don't replicate those writes**: register `0x02` also gates button
scanning, and a wrong value disables the buttons until the NAS is unplugged
(a warm reboot doesn't help – the front board runs on standby power). The panel
driver here only ever reads register `0x04`, and only when the interrupt line
is low.

## Things deliberately *not* done

* **No P2SB unhide.** The RN426 driver unhides P2SB by writing to
  `/dev/port` at `0xCF8`. `/dev/port` writes one byte at a time, so that
  touches `0xCF9`, the chipset's reset-control register. Unnecessary (see §3)
  and risky.
* **No RST pulse, EN never driven low.** Both lines are shared with the
  MSP430; pulling them low knocks the buttons out until a power cycle.
* **No direction or routing changes.** The driver only flips bit 0 of pads
  the BIOS already configured as plain GPIO outputs, and refuses to run if
  that isn't the case.
