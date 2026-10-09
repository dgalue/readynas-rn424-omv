#!/usr/bin/env python3
"""
dump_pinmap.py - list the front-panel display pin maps inside a NETGEAR
ReadyNAS OS 6 kernel, and translate the Denverton (gpio_dnv) ones into the
physical PADCFG_DW0 addresses the panel driver needs.

Usage:  python3 dump_pinmap.py <kernel>
        <kernel> = the file called 'kernel' on the original 256 MB DOM
                   (a bzImage), or an already-decompressed vmlinux.

Needs:  python3 -m pip install pyelftools

How it works (details in docs/PINMAP.md):
  * finds the rn_oled config entries: {model alias*, gpiochip label*, ...,
    six u32 pins at +0x10: MOSI, CLK, DC, CS, EN, RST}
  * for gpio_dnv entries, applies gpio_dnv's reg_map_offset_dnv() rules using
    its table5 (located by byte pattern and checked against known values)
"""
import lzma
import struct
import sys

from elftools.elf.elffile import ELFFile

ROLES = ("MOSI", "CLK", "DC", "CS", "EN", "RST")
NORTH, PADBAR = 0xFDC20000, 0x400
# first 14 entries of gpio_dnv's table5 in 4.4.x - used to locate the table
TABLE5_HEAD = struct.pack("<14i", 0xd8, 0x108, 0x110, 0x380, 0x168, 0x170,
                          0x178, 0x180, 0x1c8, 0x1d0, 0x310, 0x318, 0x90, 0x98)


def vmlinux_from(path):
    raw = open(path, "rb").read()
    if raw[:4] == b"\x7fELF":
        return raw
    if raw[0x202:0x206] != b"HdrS":
        sys.exit("not a bzImage or ELF vmlinux")
    off = (raw[0x1f1] + 1) * 512
    po, pl = struct.unpack_from("<II", raw, 0x248)
    payload = raw[off + po:off + po + pl]
    if payload[:6] != b"\xfd7zXZ\x00":
        sys.exit("kernel payload is not xz-compressed; decompress it first")
    return lzma.LZMADecompressor().decompress(payload)


class Image:
    def __init__(self, blob):
        import io
        self.blob = blob
        elf = ELFFile(io.BytesIO(blob))
        self.segs = [(s["p_vaddr"], s["p_offset"], s["p_filesz"])
                     for s in elf.iter_segments() if s["p_type"] == "PT_LOAD"]

    def rd(self, va, n):
        for v, o, sz in self.segs:
            if v <= va < v + sz:
                return self.blob[o + va - v:o + va - v + n]
        return None

    def cstr(self, va):
        b = self.rd(va, 48)
        if not b or b"\0" not in b:
            return None
        s = b.split(b"\0")[0]
        return s.decode() if s and all(32 < c < 127 for c in s) else None

    def va_of(self, off):
        for v, o, sz in self.segs:
            if o <= off < o + sz:
                return v + off - o
        return None


def dnv_offset(table5, pin):
    """Port of reg_map_offset_dnv(reg=5, pin) from the stock kernel."""
    if not 0 <= pin < len(table5) or table5[pin] < 0:
        return None
    off = table5[pin]
    if pin > 0x2d:
        if pin <= 0x8c:
            if pin >= 0x35:
                return off + 0x30000
            return None if pin <= 0x2e else off
        return off + 0x30000 if 0 <= pin - 0x92 <= 6 else None
    if pin >= 0xe:
        return off
    return off + 0x30000 if pin > 2 else off


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    img = Image(vmlinux_from(sys.argv[1]))
    blob = img.blob

    table5 = None
    i = blob.find(TABLE5_HEAD)
    if i >= 0:
        table5 = list(struct.unpack_from("<153i", blob, i))
    labels = {}
    for name in (b"gpio_dnv.0\0", b"gpio_ich\0"):
        j = blob.find(name)
        while j >= 0:
            va = img.va_of(j)
            if va:
                labels[va] = name[:-1].decode()
            j = blob.find(name, j + 1)

    seen = set()
    for v, o, sz in img.segs:
        for off in range(o, o + sz - 0x28, 8):
            p0, p1 = struct.unpack_from("<QQ", blob, off)
            if p1 not in labels:
                continue
            model = img.cstr(p0)
            if not model or not model.startswith("r") or len(model) > 12:
                continue
            pins = struct.unpack_from("<6I", blob, off + 0x10)
            if max(pins) > 255 or (model, pins) in seen:
                continue
            seen.add((model, pins))
            chip = labels[p1]
            print("%-8s %-10s %s" % (model, chip, "  ".join(
                "%s=%d" % rp for rp in zip(ROLES, pins))))
            if chip == "gpio_dnv.0" and table5:
                for role, pin in zip(ROLES, pins):
                    o5 = dnv_offset(table5, pin)
                    addr = "invalid" if o5 is None else "0x%08X  (%s+0x%03X)" % (
                        NORTH + PADBAR + o5, "S" if o5 >= 0x30000 else "N",
                        (PADBAR + o5) & 0xFFFF)
                    print("           %-4s pin %3d -> PADCFG_DW0 %s" % (role, pin, addr))
    if not seen:
        print("no display config entries found")
    if table5 is None:
        print("note: gpio_dnv table5 not found; addresses not translated")


if __name__ == "__main__":
    main()
