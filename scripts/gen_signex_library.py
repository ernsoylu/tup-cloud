#!/usr/bin/env python3
"""Generate the hosted Signex component library from the JLCPCB catalog.

Reads the JLCPCB Basic/Preferred parts CSV maintained by
https://github.com/CDFER/jlcpcb-parts-database (MIT, refreshed daily from
yaqwsx/jlcparts) and emits backend/app/assets/signex-jlcpcb-basic.json — a
bundle of signex-library `ComponentRow`s + generic schematic `Symbol`s in
their exact serde shapes. The Signex web build fetches the bundle from
/api/eda/library and mounts it as a read-only in-memory library.

Symbols are generic per category (R/C/L/D/LED/BJT/MOSFET/…); ICs get a
DIP-style box sized from the part's joint count. UUIDs are uuid5-derived
from stable keys, so regenerating the bundle never breaks references in
existing schematics.

Usage:
    python3 scripts/gen_signex_library.py [--csv path]   # else downloads
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import urllib.request
import uuid
from pathlib import Path

CSV_URL = "https://cdfer.github.io/jlcpcb-parts-database/jlcpcb-components-basic-preferred.csv"
OUT = Path(__file__).resolve().parent.parent / "backend" / "app" / "assets" / "signex-jlcpcb-basic.json"

NS = uuid.uuid5(uuid.NAMESPACE_URL, "https://tup-cloud/signex-weblib")
LIB_ID = str(uuid.uuid5(NS, "jlcpcb-basic"))
STAMP = "2026-07-21T00:00:00Z"

# ── Symbol construction helpers ─────────────────────────────────────────────
# Units are mm. pin.position is the wire tip; orientation points tip→body.


def pin(number, name, x, y, orient, length, electrical="Passive"):
    return {
        "number": str(number), "name": str(name), "electrical": electrical,
        "position": [x, y], "orientation": orient, "length": length,
    }


def line(x1, y1, x2, y2, w=0.254):
    return {"kind": {"kind": "line", "from": [x1, y1], "to": [x2, y2]}, "stroke_width": w}


def rect(x1, y1, x2, y2, w=0.254):
    return {"kind": {"kind": "rectangle", "from": [x1, y1], "to": [x2, y2]}, "stroke_width": w}


def circle(cx, cy, r, w=0.254):
    return {"kind": {"kind": "circle", "center": [cx, cy], "radius": r}, "stroke_width": w}


def arc(cx, cy, r, a0, a1, w=0.254):
    return {"kind": {"kind": "arc", "center": [cx, cy], "radius": r,
                     "start_deg": a0, "end_deg": a1}, "stroke_width": w}


def poly(pts, w=0.254, fill=None):
    g = {"kind": {"kind": "polygon", "vertices": [list(p) for p in pts]}, "stroke_width": w}
    if fill:
        g["fill"] = fill
    return g


def text(x, y, content, size=1.27):
    return {"kind": {"kind": "text", "position": [x, y], "content": content, "size": size}}


def symbol(key, name, designator, description, pins, graphics):
    return {
        "uuid": str(uuid.uuid5(NS, f"sym/{key}")),
        "name": name,
        "anchor": [0.0, 0.0],
        "pins": pins,
        "graphics": graphics,
        "designator": designator,
        "comment": "*",
        "description": description,
        "created": STAMP,
        "updated": STAMP,
    }


BLACK = None  # unfilled; filled shapes use stroke-colored fill at render time


def two_lead(body):
    """Horizontal two-lead pin pair reaching a body half-width of 2.54."""
    return [pin(1, "1", -5.08, 0.0, "Right", 2.54), pin(2, "2", 5.08, 0.0, "Left", 2.54)]


def sym_resistor():
    return symbol("resistor", "Resistor", "R?", "Generic resistor",
                  two_lead(2.54), [rect(-2.54, -1.016, 2.54, 1.016)])


def sym_resistor_network():
    return symbol("resistor-network", "Resistor Network", "RN?", "Resistor array",
                  two_lead(2.54), [rect(-2.54, -1.016, 2.54, 1.016),
                                   line(-2.54, 1.6, 2.54, 1.6)])


def sym_capacitor():
    plates = [line(-0.508, -1.6, -0.508, 1.6, 0.4), line(0.508, -1.6, 0.508, 1.6, 0.4)]
    return symbol("capacitor", "Capacitor", "C?", "Ceramic capacitor",
                  [pin(1, "1", -3.81, 0.0, "Right", 3.302), pin(2, "2", 3.81, 0.0, "Left", 3.302)],
                  plates)


def sym_capacitor_pol():
    g = [line(-0.508, -1.6, -0.508, 1.6, 0.4), arc(1.8, 0.0, 1.7, 130.0, 230.0, 0.4),
         text(-2.3, 1.5, "+", 1.1)]
    return symbol("capacitor-pol", "Capacitor (polarized)", "C?", "Electrolytic/tantalum capacitor",
                  [pin(1, "+", -3.81, 0.0, "Right", 3.302), pin(2, "-", 3.81, 0.0, "Left", 3.302)],
                  g)


def sym_inductor():
    arcs = [arc(-1.905 + i * 1.27, 0.0, 0.635, 0.0, 180.0) for i in range(4)]
    return symbol("inductor", "Inductor", "L?", "Inductor / coil",
                  two_lead(2.54), arcs)


def sym_ferrite():
    return symbol("ferrite-bead", "Ferrite Bead", "FB?", "Ferrite bead / EMI filter",
                  two_lead(2.54), [rect(-2.0, -0.9, 2.0, 0.9)])


def _diode_core(extra):
    return [poly([(-1.27, -1.27), (-1.27, 1.27), (1.27, 0.0)]),
            line(1.27, -1.27, 1.27, 1.27, 0.4), *extra]


def sym_diode(key="diode", name="Diode", desc="General purpose diode", extra=()):
    return symbol(key, name, "D?", desc,
                  [pin(2, "A", -3.81, 0.0, "Right", 2.54), pin(1, "K", 3.81, 0.0, "Left", 2.54)],
                  _diode_core(list(extra)))


def sym_schottky():
    hooks = [line(1.27, 1.27, 0.85, 1.27, 0.4), line(0.85, 1.27, 0.85, 0.95, 0.4),
             line(1.27, -1.27, 1.69, -1.27, 0.4), line(1.69, -1.27, 1.69, -0.95, 0.4)]
    return sym_diode("schottky", "Schottky Diode", "Schottky diode", hooks)


def sym_zener():
    wings = [line(1.27, 1.27, 1.69, 1.62, 0.4), line(1.27, -1.27, 0.85, -1.62, 0.4)]
    return sym_diode("zener", "Zener Diode", "Zener diode", wings)


def sym_led():
    arrows = [line(0.3, 1.5, 1.2, 2.6, 0.3), poly([(1.2, 2.6), (0.75, 2.35), (1.05, 2.05)]),
              line(1.2, 1.2, 2.1, 2.3, 0.3), poly([(2.1, 2.3), (1.65, 2.05), (1.95, 1.75)])]
    return sym_diode("led", "LED", "Light-emitting diode", arrows)


def sym_tvs_bi():
    g = [poly([(-2.54, -1.27), (-2.54, 1.27), (-0.3, 0.0)]),
         poly([(2.54, -1.27), (2.54, 1.27), (0.3, 0.0)]),
         line(-0.3, -1.27, -0.3, 1.27, 0.4), line(0.3, -1.27, 0.3, 1.27, 0.4)]
    return symbol("tvs-bi", "TVS (bidirectional)", "D?", "Bidirectional TVS",
                  [pin(1, "1", -5.08, 0.0, "Right", 2.54), pin(2, "2", 5.08, 0.0, "Left", 2.54)],
                  g)


def sym_bridge():
    g = [rect(-3.81, -3.81, 3.81, 3.81), text(-3.0, 2.8, "~", 1.4), text(2.2, 2.8, "+", 1.4),
         text(-3.0, -1.6, "~", 1.4), text(2.2, -1.6, "-", 1.4)]
    pins = [pin(1, "~", -6.35, 2.54, "Right", 2.54), pin(2, "~", -6.35, -2.54, "Right", 2.54),
            pin(3, "+", 6.35, 2.54, "Left", 2.54), pin(4, "-", 6.35, -2.54, "Left", 2.54)]
    return symbol("bridge", "Bridge Rectifier", "D?", "Bridge rectifier", pins, g)


def _bjt_core(pnp):
    g = [circle(0.635, 0.0, 2.8), line(-0.635, -1.9, -0.635, 1.9, 0.5),
         line(-0.635, 0.7, 1.9, 2.0, 0.35), line(-0.635, -0.7, 1.9, -2.0, 0.35)]
    if pnp:
        g.append(poly([(-0.25, -0.95), (0.75, -0.85), (0.15, -1.65)]))
    else:
        g.append(poly([(1.9, -2.0), (0.95, -1.85), (1.5, -1.15)]))
    return g


def sym_npn():
    pins = [pin(1, "B", -3.81, 0.0, "Right", 3.175, "Input"),
            pin(2, "E", 1.9, -4.445, "Up", 2.445, "Passive"),
            pin(3, "C", 1.9, 4.445, "Down", 2.445, "Passive")]
    return symbol("npn", "NPN Transistor", "Q?", "NPN bipolar transistor", pins, _bjt_core(False))


def sym_pnp():
    pins = [pin(1, "B", -3.81, 0.0, "Right", 3.175, "Input"),
            pin(2, "E", 1.9, -4.445, "Up", 2.445, "Passive"),
            pin(3, "C", 1.9, 4.445, "Down", 2.445, "Passive")]
    return symbol("pnp", "PNP Transistor", "Q?", "PNP bipolar transistor", pins, _bjt_core(True))


def _fet_core(p_channel):
    g = [circle(0.9, 0.0, 3.0),
         line(-0.4, -1.8, -0.4, 1.8, 0.45),           # gate plate
         line(0.35, -2.0, 0.35, -0.9, 0.45), line(0.35, -0.55, 0.35, 0.55, 0.45),
         line(0.35, 0.9, 0.35, 2.0, 0.45),            # channel dashes
         line(0.35, 1.45, 2.2, 1.45, 0.35), line(0.35, -1.45, 2.2, -1.45, 0.35),
         line(0.35, 0.0, 2.2, 0.0, 0.35), line(2.2, -1.45, 2.2, 0.0, 0.35)]
    if p_channel:
        g.append(poly([(0.5, 0.0), (1.5, 0.4), (1.5, -0.4)]))
    else:
        g.append(poly([(1.6, 0.0), (0.7, 0.4), (0.7, -0.4)]))
    return g


def sym_nmos():
    pins = [pin(1, "G", -3.81, -1.8, "Right", 3.41, "Input"),
            pin(2, "S", 2.2, -4.445, "Up", 2.995, "Passive"),
            pin(3, "D", 2.2, 4.445, "Down", 2.995, "Passive")]
    return symbol("nmos", "N-MOSFET", "Q?", "N-channel MOSFET", pins, _fet_core(False))


def sym_pmos():
    pins = [pin(1, "G", -3.81, -1.8, "Right", 3.41, "Input"),
            pin(2, "S", 2.2, -4.445, "Up", 2.995, "Passive"),
            pin(3, "D", 2.2, 4.445, "Down", 2.995, "Passive")]
    return symbol("pmos", "P-MOSFET", "Q?", "P-channel MOSFET", pins, _fet_core(True))


def sym_fuse():
    return symbol("fuse", "Fuse / PTC", "F?", "Fuse or resettable fuse",
                  two_lead(2.54), [rect(-2.54, -0.9, 2.54, 0.9), line(-2.54, 0.0, 2.54, 0.0, 0.3)])


def sym_crystal():
    g = [line(-1.0, -1.8, -1.0, 1.8, 0.4), line(1.0, -1.8, 1.0, 1.8, 0.4),
         rect(-0.55, -2.2, 0.55, 2.2)]
    return symbol("crystal", "Crystal", "Y?", "Quartz crystal / resonator",
                  [pin(1, "1", -3.81, 0.0, "Right", 2.81), pin(2, "2", 3.81, 0.0, "Left", 2.81)],
                  g)


def sym_ic(joints):
    """DIP-style generic box: pins 1..k down the left, k+1..n up the right."""
    n = max(2, joints)
    left = (n + 1) // 2
    right = n - left
    rows = max(left, right)
    half_h = rows * 1.27 + 1.27
    width = 7.62
    pins = []
    for i in range(left):
        y = half_h - 2.54 - i * 2.54 + 1.27
        pins.append(pin(i + 1, str(i + 1), -width / 2 - 2.54, y, "Right", 2.54))
    for i in range(right):
        y = -(half_h - 2.54 - i * 2.54 + 1.27)
        pins.append(pin(left + i + 1, str(left + i + 1), width / 2 + 2.54, y, "Left", 2.54))
    return symbol(f"ic-{n}", f"IC ({n} pin)", "U?", f"Generic {n}-pin IC",
                  pins, [rect(-width / 2, -half_h, width / 2, half_h)])


def sym_connector(joints):
    n = max(1, joints)
    half_h = n * 1.27 + 0.635
    pins = []
    g = [rect(-1.27, -half_h, 1.27, half_h)]
    for i in range(n):
        y = half_h - 1.27 - i * 2.54 - 0.635 + 0.635
        pins.append(pin(i + 1, str(i + 1), -3.81, y, "Right", 2.54))
        g.append(line(-1.27, y, 0.0, y, 0.4))
    return symbol(f"conn-{n}", f"Connector ({n} pin)", "J?", f"Generic {n}-pin connector", pins, g)


# ── Category mapping ────────────────────────────────────────────────────────

def classify(row, extra):
    """→ (table, class, symbol_key, designator_hint) using category + attrs."""
    cat = (extra.get("category") or {})
    n1 = (cat.get("name1") or row["category"] or "").lower()
    n2 = (cat.get("name2") or row["subcategory"] or "").lower()
    desc = (extra.get("description") or row["description"] or "").lower()
    attrs = extra.get("attributes") or {}
    joints = int(row["joints"] or 2)

    if "resistor" in n1 or "resistor" in n2:
        if "network" in n2 or "array" in n2:
            return "Resistors", "resistor", "resistor-network"
        return "Resistors", "resistor", "resistor"
    if "capacitor" in n1 or "capacitor" in n2:
        if any(k in n2 for k in ("electrolytic", "tantalum", "polymer")):
            return "Capacitors", "capacitor", "capacitor-pol"
        return "Capacitors", "capacitor", "capacitor"
    if "inductor" in n1 or "inductor" in n2 or "coil" in n1:
        return "Inductors", "inductor", "inductor"
    if "ferrite" in n2 or "bead" in n2:
        return "Inductors", "inductor", "ferrite-bead"
    if "led" in n2 or "light emitting" in n2:
        return "LEDs", "led", "led"
    if "tvs" in n1 or "tvs" in n2 or "esd" in n2 or "surge" in n2:
        return "Protection", "diode", "tvs-bi"
    if "fuse" in n1 or "fuse" in n2 or "ptc" in n2:
        return "Protection", "fuse", "fuse"
    if "bridge" in n2:
        return "Diodes", "diode", "bridge"
    if "schottky" in n2:
        return "Diodes", "diode", "schottky"
    if "zener" in n2:
        return "Diodes", "diode", "zener"
    if "diode" in n1 or "diode" in n2 or "rectifier" in n2:
        return "Diodes", "diode", "diode"
    if "mosfet" in n2 or "mos tube" in n1:
        t = str(attrs.get("Type", "")) + " " + desc
        return "Transistors", "mosfet", "pmos" if re.search(r"\bp[- ]?channel|\bpnp\b", t, re.I) else "nmos"
    if "bipolar" in n2 or "bjt" in n2 or "transistor" in n1:
        t = str(attrs.get("Type", "")) + " " + desc
        return "Transistors", "bjt", "pnp" if re.search(r"\bpnp\b", t, re.I) else "npn"
    if "crystal" in n1 or "crystal" in n2 or "resonator" in n2 or "oscillator" in n2:
        return "Crystals", "crystal", "crystal"
    if "connector" in n1 or "connector" in n2 or "header" in n2:
        return "Connectors", "connector", f"conn-{joints}"
    return "ICs", "generic", f"ic-{joints}"


def value_of(extra, row):
    attrs = extra.get("attributes") or {}
    for key in ("Resistance", "Capacitance", "Inductance", "Frequency",
                "Reverse Stand-Off Voltage (Vrwm)", "Voltage - DC Reverse(Vr)",
                "Output Voltage", "Current Rating", "Voltage - Zener(Vz)"):
        v = attrs.get(key)
        if v and v not in ("-", ""):
            return str(v)
    m = re.search(r"\b(\d+(?:\.\d+)?\s?[kKmMuUnNpPμ]?[ΩΩFfHhVv])\b", row["description"] or "")
    return m.group(1) if m else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", type=Path, help="local CSV (else downloads)")
    args = ap.parse_args()

    if args.csv:
        raw = args.csv.read_text(errors="replace")
    else:
        print(f"downloading {CSV_URL}…", file=sys.stderr)
        raw = urllib.request.urlopen(CSV_URL, timeout=120).read().decode(errors="replace")

    rows = list(csv.DictReader(raw.splitlines()))
    tables: dict[str, list] = {}
    symbols: dict[str, dict] = {}
    for factory in (sym_resistor, sym_resistor_network, sym_capacitor, sym_capacitor_pol,
                    sym_inductor, sym_ferrite, sym_diode, sym_schottky, sym_zener, sym_led,
                    sym_tvs_bi, sym_bridge, sym_npn, sym_pnp, sym_nmos, sym_pmos,
                    sym_fuse, sym_crystal):
        s = factory()
        symbols[s["name"]] = s
    by_key = {k: s for k, s in (
        ("resistor", symbols["Resistor"]), ("resistor-network", symbols["Resistor Network"]),
        ("capacitor", symbols["Capacitor"]), ("capacitor-pol", symbols["Capacitor (polarized)"]),
        ("inductor", symbols["Inductor"]), ("ferrite-bead", symbols["Ferrite Bead"]),
        ("diode", symbols["Diode"]), ("schottky", symbols["Schottky Diode"]),
        ("zener", symbols["Zener Diode"]), ("led", symbols["LED"]),
        ("tvs-bi", symbols["TVS (bidirectional)"]), ("bridge", symbols["Bridge Rectifier"]),
        ("npn", symbols["NPN Transistor"]), ("pnp", symbols["PNP Transistor"]),
        ("nmos", symbols["N-MOSFET"]), ("pmos", symbols["P-MOSFET"]),
        ("fuse", symbols["Fuse / PTC"]), ("crystal", symbols["Crystal"]))}

    skipped = 0
    for row in rows:
        lcsc = row["lcsc"]
        if not lcsc:
            skipped += 1
            continue
        try:
            extra = json.loads(row["extra"]) if row["extra"] and row["extra"] != "{}" else {}
        except json.JSONDecodeError:
            extra = {}
        table, klass, sym_key = classify(row, extra)
        if sym_key.startswith("ic-") or sym_key.startswith("conn-"):
            if sym_key not in by_key:
                joints = int(row["joints"] or 2)
                s = sym_ic(joints) if sym_key.startswith("ic-") else sym_connector(joints)
                symbols[s["name"]] = s
                by_key[sym_key] = s
            sym = by_key[sym_key]
        else:
            sym = by_key[sym_key]

        attrs = extra.get("attributes") or {}
        params = {"Value": value_of(extra, row), "Package": row["package"] or "",
                  "Description": (extra.get("description") or row["description"] or "")[:160],
                  "LCSC": f"C{lcsc}",
                  "JLCPCB Class": "Basic" if row["basic"] == "1" else "Preferred",
                  "Stock": row["stock"] or "0"}
        for k, v in list(attrs.items())[:6]:
            if v and v != "-" and k not in params:
                params[k] = str(v)
        parameters = {k: {"kind": "Text", "value": v} for k, v in params.items() if v}

        datasheet = (extra.get("datasheet") or {}).get("pdf") or row["datasheet"] or ""
        mpn = row["mfr"] or f"C{lcsc}"
        url = (extra.get("url")
               or f"https://lcsc.com/product-detail/C{lcsc}.html")
        description = (extra.get("description") or row["description"] or mpn)[:200]

        component = {
            "row_id": str(uuid.uuid5(NS, f"row/C{lcsc}")),
            "internal_pn": f"JLC-C{lcsc}",
            "class": klass,
            "datasheet": {"kind": "url", "url": datasheet},
            "state": "Released",
            "symbol_ref": {"library_id": LIB_ID, "uuid": sym["uuid"]},
            "primary_mpn": {"manufacturer": row["manufacturer"] or "?",
                            "mpn": mpn, "status": "Primary"},
            "supply": [{"distributor": "JLCPCB", "sku": f"C{lcsc}", "url": url,
                        "moq": int(row["Min Order Qty"] or 1)}],
            "parameters": parameters,
            "version": "0.0.1",
            "released": True,
            "created": STAMP,
            "updated": STAMP,
        }
        tables.setdefault(table, []).append((description, component))

    bundle = {
        "format": "tup-signex-weblib/1",
        "name": "JLCPCB Basic",
        "library_id": LIB_ID,
        "description": ("JLCPCB Basic + Preferred assembly parts with generic schematic "
                        "symbols; data via CDFER/jlcpcb-parts-database (MIT) from "
                        "yaqwsx/jlcparts. Regenerate: scripts/gen_signex_library.py"),
        "tables": {name: [c for _, c in sorted(parts, key=lambda p: p[0])]
                   for name, parts in sorted(tables.items())},
        "symbols": sorted(symbols.values(), key=lambda s: s["name"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(bundle, separators=(",", ":")))
    total = sum(len(v) for v in bundle["tables"].values())
    print(f"wrote {OUT} — {total} parts in {len(bundle['tables'])} tables, "
          f"{len(bundle['symbols'])} symbols, {OUT.stat().st_size // 1024} KiB "
          f"({skipped} rows skipped)")


if __name__ == "__main__":
    main()
