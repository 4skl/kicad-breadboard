"""
Component type definitions: pin counts, hole offsets, and physical layout.

Placement convention
--------------------
Every component has an "anchor" — the hole where pin 1 lands when the user drops it.

For single-bank components (R, C, L, POT, TO-92 transistors):
  All pins are in the same bank (top or bottom) and the same row as the anchor.
  Pins are at anchor_col + col_delta, same row.

For DIP ICs (TL081, RC4558, TL084):
  The IC straddles the center gap.  Anchor = pin 1, always placed in row 'e'.
  Top-side pins land in row 'e', bottom-side pins land in row 'f'.

Pin numbering follows the standard KiCad symbol convention for each part.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .breadboard import (
    TOP_ROWS, BOT_ROWS, ALL_ROWS, COLUMNS,
    TieHole, RailHole, Terminal, Hole,
)


@dataclass(frozen=True)
class PinOffset:
    """
    Offset of a single pin from the anchor hole.

    col_delta   : column shift from anchor column (may be negative)
    cross_gap   : True for bottom-side pins of DIP ICs (land in row 'f' always)
    row_delta   : additional row shift within the same bank (0 for most parts)
    """
    col_delta: int
    cross_gap: bool = False
    row_delta: int = 0

    def resolve(self, anchor: TieHole) -> TieHole:
        col = anchor.col + self.col_delta
        if self.cross_gap:
            row = 'f'   # DIP bottom side always in row f (closest to gap)
        else:
            bank = TOP_ROWS if anchor.row in TOP_ROWS else BOT_ROWS
            idx = bank.index(anchor.row) + self.row_delta
            idx = max(0, min(len(bank) - 1, idx))
            row = bank[idx]
        return TieHole(col, row)


@dataclass
class ComponentDef:
    type_id: str                        # internal identifier, e.g. 'R', 'NPN', 'TL081'
    display_name: str
    ref_prefix: str                     # KiCad ref prefix: R, C, Q, U …
    pin_offsets: Dict[int, PinOffset]   # pin_number → offset from anchor
    pin_names: Dict[int, str]           # pin_number → net-facing name (B, C, E, IN+, …)
    color: str = '#888888'              # body fill color for canvas
    is_dip: bool = False                # True → anchor forced to row 'e'

    @property
    def pin_count(self) -> int:
        return len(self.pin_offsets)

    def place(self, anchor: TieHole) -> Dict[int, Hole]:
        """
        Resolve all pin holes given an anchor hole.
        For DIP ICs the anchor row is forced to 'e'.
        Returns {pin_number: TieHole}.
        """
        if self.is_dip:
            anchor = TieHole(anchor.col, 'e')
        return {pin: offset.resolve(anchor) for pin, offset in self.pin_offsets.items()}

    def footprint_cols(self) -> int:
        """Number of breadboard columns the component occupies."""
        deltas = [o.col_delta for o in self.pin_offsets.values()]
        return max(deltas) - min(deltas) + 1


# ---------------------------------------------------------------------------
# Passive 2-pin components (R, C, L)
# Default span: 5 holes between the two leads.
# ---------------------------------------------------------------------------

RESISTOR = ComponentDef(
    type_id='R',
    display_name='Resistor',
    ref_prefix='R',
    pin_offsets={1: PinOffset(0), 2: PinOffset(5)},
    pin_names={1: '1', 2: '2'},
    color='#c8a050',
)

CAPACITOR = ComponentDef(
    type_id='C',
    display_name='Capacitor',
    ref_prefix='C',
    pin_offsets={1: PinOffset(0), 2: PinOffset(3)},
    pin_names={1: '1', 2: '2'},
    color='#4080c0',
)

CAPACITOR_ELECTROLYTIC = ComponentDef(
    type_id='C_POL',
    display_name='Capacitor (electrolytic)',
    ref_prefix='C',
    pin_offsets={1: PinOffset(0), 2: PinOffset(2)},
    pin_names={1: '+', 2: '-'},
    color='#4060a0',
)

INDUCTOR = ComponentDef(
    type_id='L',
    display_name='Inductor',
    ref_prefix='L',
    pin_offsets={1: PinOffset(0), 2: PinOffset(5)},
    pin_names={1: '1', 2: '2'},
    color='#60a080',
)

# ---------------------------------------------------------------------------
# Potentiometer (3-pin, 3 consecutive holes)
# Pin 1 = CCW terminal, Pin 2 = Wiper, Pin 3 = CW terminal
# ---------------------------------------------------------------------------

POTENTIOMETER = ComponentDef(
    type_id='POT',
    display_name='Potentiometer',
    ref_prefix='RV',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: '1', 2: '2', 3: '3'},
    color='#a06830',
)

# ---------------------------------------------------------------------------
# TO-92 transistors (3 consecutive holes in the same bank/row)
#
# Physical pin order (flat face toward viewer, left to right):
#   NPN/PNP generic (e.g. BC547/BC557): C – B – E
#   JFET N-ch (e.g. 2N5457):            D – G – S   (varies; use BF245: S – G – D)
#   BS170 MOSFET:                        S – G – D
#
# KiCad schematic pin names and the breadboard pin offsets must agree so that
# the validator can match nets to holes.
# ---------------------------------------------------------------------------

NPN_BJT = ComponentDef(
    type_id='NPN',
    display_name='NPN BJT',
    ref_prefix='Q',
    # Physical: C(pin1)–B(pin2)–E(pin3), left to right
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'C', 2: 'B', 3: 'E'},
    color='#a0a0c0',
)

PNP_BJT = ComponentDef(
    type_id='PNP',
    display_name='PNP BJT',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'C', 2: 'B', 3: 'E'},
    color='#c0a0a0',
)

JFET_N = ComponentDef(
    type_id='JFET_N',
    display_name='N-ch JFET',
    ref_prefix='Q',
    # Physical (BF245 / 2N5457): S(pin1)–G(pin2)–D(pin3)
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#80c0a0',
)

JFET_P = ComponentDef(
    type_id='JFET_P',
    display_name='P-ch JFET',
    ref_prefix='Q',
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#c0a080',
)

BS170 = ComponentDef(
    type_id='BS170',
    display_name='BS170 MOSFET',
    ref_prefix='Q',
    # Physical (BS170 TO-92): S(pin1)–G(pin2)–D(pin3)
    pin_offsets={1: PinOffset(0), 2: PinOffset(1), 3: PinOffset(2)},
    pin_names={1: 'S', 2: 'G', 3: 'D'},
    color='#70b0c0',
)

# ---------------------------------------------------------------------------
# DIP op-amps — anchor always in row 'e', bottom side in row 'f'
#
# 8-DIP pin layout (counterclockwise from notch, top view):
#   Pins 1–4 along the top side  (row e, cols anchor … anchor+3)
#   Pins 5–8 along the bottom side (row f, cols anchor+3 … anchor)
#
# 14-DIP:
#   Pins 1–7  top side  (row e, cols anchor … anchor+6)
#   Pins 8–14 bottom side (row f, cols anchor+6 … anchor)
# ---------------------------------------------------------------------------

def _dip8_offsets() -> Dict[int, PinOffset]:
    top = {i + 1: PinOffset(i, cross_gap=False) for i in range(4)}        # pins 1-4
    bot = {i + 5: PinOffset(3 - i, cross_gap=True) for i in range(4)}     # pins 5-8
    return {**top, **bot}

def _dip14_offsets() -> Dict[int, PinOffset]:
    top = {i + 1: PinOffset(i, cross_gap=False) for i in range(7)}        # pins 1-7
    bot = {i + 8: PinOffset(6 - i, cross_gap=True) for i in range(7)}     # pins 8-14
    return {**top, **bot}

# TL081 — single op-amp, 8-DIP
# Pinout: 1=N1, 2=IN-, 3=IN+, 4=V-, 5=N2, 6=OUT, 7=V+, 8=N3
TL081 = ComponentDef(
    type_id='TL081',
    display_name='TL081 (single op-amp)',
    ref_prefix='U',
    pin_offsets=_dip8_offsets(),
    pin_names={
        1: 'N1', 2: 'IN-', 3: 'IN+', 4: 'V-',
        5: 'N2', 6: 'OUT', 7: 'V+', 8: 'N3',
    },
    color='#303080',
    is_dip=True,
)

# RC4558 — dual op-amp, 8-DIP
# Pinout: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V-, 5=IN+_B, 6=IN-_B, 7=OUT_B, 8=V+
RC4558 = ComponentDef(
    type_id='RC4558',
    display_name='RC4558 (dual op-amp)',
    ref_prefix='U',
    pin_offsets=_dip8_offsets(),
    pin_names={
        1: 'OUT_A', 2: 'IN-_A', 3: 'IN+_A', 4: 'V-',
        5: 'IN+_B', 6: 'IN-_B', 7: 'OUT_B', 8: 'V+',
    },
    color='#303080',
    is_dip=True,
)

# TL084 — quad op-amp, 14-DIP
# Pinout: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V+, 5=IN+_B, 6=IN-_B, 7=OUT_B,
#         8=OUT_C, 9=IN-_C, 10=IN+_C, 11=V-, 12=IN+_D, 13=IN-_D, 14=OUT_D
TL084 = ComponentDef(
    type_id='TL084',
    display_name='TL084 (quad op-amp)',
    ref_prefix='U',
    pin_offsets=_dip14_offsets(),
    pin_names={
        1: 'OUT_A', 2: 'IN-_A', 3: 'IN+_A', 4:  'V+',
        5: 'IN+_B', 6: 'IN-_B', 7: 'OUT_B',
        8: 'OUT_C', 9: 'IN-_C', 10: 'IN+_C', 11: 'V-',
        12: 'IN+_D', 13: 'IN-_D', 14: 'OUT_D',
    },
    color='#303080',
    is_dip=True,
)

# ---------------------------------------------------------------------------
# Registry: map type_id → ComponentDef
# Also provides heuristic lookup from KiCad symbol/value strings.
# ---------------------------------------------------------------------------

ALL_DEFS: Dict[str, ComponentDef] = {
    d.type_id: d for d in [
        RESISTOR, CAPACITOR, CAPACITOR_ELECTROLYTIC, INDUCTOR, POTENTIOMETER,
        NPN_BJT, PNP_BJT, JFET_N, JFET_P, BS170,
        TL081, RC4558, TL084,
    ]
}


def guess_type_id(ref: str, value: str, symbol: str) -> Optional[str]:
    """
    Heuristically map a KiCad component to a ComponentDef type_id.

    ref    : schematic reference, e.g. 'R1', 'Q3', 'U1'
    value  : component value, e.g. '10k', 'BC547', 'TL081'
    symbol : KiCad symbol name from the netlist libsource, e.g. 'R', 'NPN', 'TL081'
    """
    v = value.upper()
    s = symbol.upper()

    # Exact value/symbol matches first
    for key in ('TL084', 'RC4558', 'TL081', 'BS170'):
        if key in v or key in s:
            return key

    # Transistor types from symbol library name
    if 'NPN' in s:
        return 'NPN'
    if 'PNP' in s:
        return 'PNP'
    if 'PJFE' in s or ('JFET' in s and 'P' in s):
        return 'JFET_P'
    if 'JFET' in s or 'NJFE' in s:
        return 'JFET_N'
    if 'NMOS' in s or 'MOSFET' in s:
        return 'BS170'

    # Reference prefix fallback
    prefix = ''.join(c for c in ref if c.isalpha()).upper()
    if prefix == 'R':
        return 'C_POL' if ('POL' in s or 'ELEC' in v) else 'R'
    if prefix == 'C':
        return 'C_POL' if ('+' in value or 'POL' in s or 'ELEC' in v) else 'C'
    if prefix == 'L':
        return 'L'
    if prefix in ('RV', 'POT'):
        return 'POT'

    return None
