"""
Direct KiCad .kicad_sch parser.

Reads a flat (non-hierarchical) KiCad schematic file and returns the same
Netlist dataclass as model/netlist.py, without requiring kicad-cli.

Public API
----------
    parse_schematic(path: str | Path) -> Netlist

The helper find_schematic() lives in netlist.py — import it from there.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .netlist import (
    Netlist, Net, NetlistComponent, NetlistPin,
    _tokenize, _parse_one, _find, _find_all, _val,
)

# ---------------------------------------------------------------------------
# Internal geometry helpers
# ---------------------------------------------------------------------------

def _round(v: float) -> float:
    return round(v, 3)


def _transform(rx: float, ry: float,
               angle_deg: float,
               mirror_x: bool, mirror_y: bool,
               ix: float, iy: float) -> Tuple[float, float]:
    """Apply mirror → rotate → translate to a library-relative pin position."""
    # KiCad Y axis points downward on screen, but in the file coordinates the
    # rotation is standard mathematical CCW.  Empirically verified:
    #   LED at (104.14, 74.93) rotation=90; pin K lib=(-3.81, 0)
    #   after 90° CCW: (0, -3.81) → absolute (104.14, 71.12) ✓
    x, y = rx, ry
    if mirror_x:
        x = -x
    if mirror_y:
        y = -y
    rad = math.radians(angle_deg)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    xr = x * cos_a - y * sin_a
    yr = x * sin_a + y * cos_a
    return _round(xr + ix), _round(yr + iy)


# ---------------------------------------------------------------------------
# Step 1 – parse lib_symbols
# ---------------------------------------------------------------------------

@dataclass
class _LibPin:
    num: str
    name: str
    ptype: str
    x: float  # connection-point position in lib coordinates
    y: float


def _parse_lib_symbols(sexp) -> Dict[str, List[_LibPin]]:
    """Return {lib_id: [_LibPin, ...]} from the (lib_symbols ...) block."""
    result: Dict[str, List[_LibPin]] = {}
    lib_syms_node = _find(sexp, 'lib_symbols')
    if lib_syms_node is None:
        return result

    for sym_node in _find_all(lib_syms_node, 'symbol'):
        # sym_node[1] is the lib_id string, e.g. "Device:R"
        if len(sym_node) < 2 or not isinstance(sym_node[1], str):
            continue
        lib_id = sym_node[1]
        pins: List[_LibPin] = []

        # Pins live inside sub-symbol children like (symbol "R_1_1" ...)
        # Walk all nested (symbol ...) children and collect their (pin ...) items.
        for child in sym_node:
            if not isinstance(child, list):
                continue
            if child and child[0] == 'symbol':
                # sub-symbol; collect pins from it
                for item in child:
                    if isinstance(item, list) and item and item[0] == 'pin':
                        pin = _parse_lib_pin(item)
                        if pin:
                            pins.append(pin)
            elif child and child[0] == 'pin':
                # pin directly inside the top-level symbol (rare)
                pin = _parse_lib_pin(child)
                if pin:
                    pins.append(pin)

        result[lib_id] = pins

    return result


def _parse_lib_pin(pin_node) -> Optional[_LibPin]:
    """Parse a (pin <type> <shape> (at x y angle) ... (name ...) (number ...)) node."""
    # Structure: [pin, ptype, shape, (at x y angle), (name ...), (number ...)]
    if len(pin_node) < 4:
        return None
    ptype = pin_node[1] if isinstance(pin_node[1], str) else ''
    at_node = _find(pin_node, 'at')
    if at_node is None or len(at_node) < 3:
        return None
    try:
        x = float(at_node[1])
        y = float(at_node[2])
    except (ValueError, TypeError):
        return None
    name_node = _find(pin_node, 'name')
    num_node  = _find(pin_node, 'number')
    pname = _val(name_node) if name_node else '~'
    pnum  = _val(num_node)  if num_node  else ''
    if not pnum:
        return None
    return _LibPin(num=pnum, name=pname, ptype=ptype, x=x, y=y)


# ---------------------------------------------------------------------------
# Step 2 – parse placed symbols
# ---------------------------------------------------------------------------

@dataclass
class _PlacedPin:
    num: str
    name: str
    ptype: str
    abs_x: float
    abs_y: float


@dataclass
class _PlacedSymbol:
    ref: str
    lib_id: str
    value: str
    description: str
    ix: float
    iy: float
    angle: float
    mirror_x: bool
    mirror_y: bool
    is_power: bool      # ref starts with '#'
    pins: List[_PlacedPin] = field(default_factory=list)


def _parse_placed_symbols(sexp,
                          lib_pins: Dict[str, List[_LibPin]]) -> List[_PlacedSymbol]:
    """Return all top-level placed symbol instances."""
    placed: List[_PlacedSymbol] = []

    for sym_node in _find_all(sexp, 'symbol'):
        # Placed symbols have a (lib_id ...) child.
        # lib_symbols sub-symbols do NOT (they have a string second element).
        lib_id_node = _find(sym_node, 'lib_id')
        if lib_id_node is None:
            continue  # this is a nested lib sub-symbol, not a placed instance

        lib_id = _val(lib_id_node)

        at_node = _find(sym_node, 'at')
        if at_node is None or len(at_node) < 3:
            continue
        try:
            ix = float(at_node[1])
            iy = float(at_node[2])
            angle = float(at_node[3]) if len(at_node) > 3 else 0.0
        except (ValueError, TypeError):
            continue

        # Mirror flags
        mirror_node = _find(sym_node, 'mirror')
        mirror_x = False
        mirror_y = False
        if mirror_node and len(mirror_node) > 1:
            axis = mirror_node[1]
            if axis == 'x':
                mirror_x = True
            elif axis == 'y':
                mirror_y = True

        # Properties
        ref   = ''
        value = ''
        desc  = ''
        for prop in _find_all(sym_node, 'property'):
            key = prop[1] if len(prop) > 1 and isinstance(prop[1], str) else ''
            val = prop[2] if len(prop) > 2 and isinstance(prop[2], str) else ''
            if key == 'Reference':
                ref = val
            elif key == 'Value':
                value = val
            elif key == 'Description':
                desc = val

        if not ref:
            continue

        is_power = ref.startswith('#')

        # Compute absolute pin positions using lib_pins
        pins: List[_PlacedPin] = []
        lp_list = lib_pins.get(lib_id, [])
        for lp in lp_list:
            ax, ay = _transform(lp.x, lp.y, angle, mirror_x, mirror_y, ix, iy)
            pins.append(_PlacedPin(
                num=lp.num,
                name=lp.name,
                ptype=lp.ptype,
                abs_x=ax,
                abs_y=ay,
            ))

        placed.append(_PlacedSymbol(
            ref=ref,
            lib_id=lib_id,
            value=value,
            description=desc,
            ix=ix,
            iy=iy,
            angle=angle,
            mirror_x=mirror_x,
            mirror_y=mirror_y,
            is_power=is_power,
            pins=pins,
        ))

    return placed


# ---------------------------------------------------------------------------
# Step 3 – build connectivity via Union-Find
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self):
        self._parent: Dict[int, int] = {}

    def _key(self, x: float, y: float) -> int:
        # encode as a large integer to avoid hashing floats
        # scale by 1000 (3 decimal places) and pack into a single int
        xi = round(x * 1000)
        yi = round(y * 1000)
        return xi * 10_000_000 + yi

    def find(self, k: int) -> int:
        if k not in self._parent:
            self._parent[k] = k
        r = k
        while self._parent[r] != r:
            r = self._parent[r]
        # path compression
        while self._parent[k] != r:
            self._parent[k], k = r, self._parent[k]
        return r

    def union(self, x1: float, y1: float, x2: float, y2: float):
        a = self.find(self._key(x1, y1))
        b = self.find(self._key(x2, y2))
        if a != b:
            self._parent[a] = b

    def same(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        return self.find(self._key(x1, y1)) == self.find(self._key(x2, y2))

    def root_of(self, x: float, y: float) -> int:
        return self.find(self._key(x, y))


def _build_connectivity(sexp,
                        placed: List[_PlacedSymbol]
                        ) -> Tuple[_UnionFind, Dict[int, str]]:
    """
    Union wire endpoints, junctions, bus entries, labels, and power-pin positions.

    Returns:
        uf         — UnionFind over coordinate keys
        net_names  — {root_key: net_name} for named nets
    """
    uf = _UnionFind()
    net_names: Dict[int, str] = {}   # root → preferred name

    # --- wires ---
    for wire_node in _find_all(sexp, 'wire'):
        pts_node = _find(wire_node, 'pts')
        if pts_node is None:
            continue
        xy_nodes = _find_all(pts_node, 'xy')
        if len(xy_nodes) < 2:
            continue
        try:
            x1, y1 = float(xy_nodes[0][1]), float(xy_nodes[0][2])
            x2, y2 = float(xy_nodes[1][1]), float(xy_nodes[1][2])
        except (ValueError, TypeError, IndexError):
            continue
        uf.union(x1, y1, x2, y2)

    # --- bus wire entries (diagonal half-bridges) ---
    for be_node in _find_all(sexp, 'bus_wire_entry'):
        pts_node = _find(be_node, 'pts')
        if pts_node is None:
            continue
        xy_nodes = _find_all(pts_node, 'xy')
        if len(xy_nodes) < 2:
            continue
        try:
            x1, y1 = float(xy_nodes[0][1]), float(xy_nodes[0][2])
            x2, y2 = float(xy_nodes[1][1]), float(xy_nodes[1][2])
        except (ValueError, TypeError, IndexError):
            continue
        uf.union(x1, y1, x2, y2)

    # --- junctions ---
    for jn_node in _find_all(sexp, 'junction'):
        at_node = _find(jn_node, 'at')
        if at_node and len(at_node) >= 3:
            try:
                x, y = float(at_node[1]), float(at_node[2])
                # A junction just ensures its point exists in the UF; merging
                # with itself is a no-op but registers the key.
                uf.union(x, y, x, y)
            except (ValueError, TypeError):
                pass

    # --- local labels ---
    for lbl_node in _find_all(sexp, 'label'):
        name = lbl_node[1] if len(lbl_node) > 1 and isinstance(lbl_node[1], str) else None
        if not name:
            continue
        at_node = _find(lbl_node, 'at')
        if at_node is None or len(at_node) < 3:
            continue
        try:
            x, y = float(at_node[1]), float(at_node[2])
        except (ValueError, TypeError):
            continue
        root = uf.root_of(x, y)
        if root not in net_names:
            net_names[root] = name

    # --- global labels ---
    for lbl_node in _find_all(sexp, 'global_label'):
        name = lbl_node[1] if len(lbl_node) > 1 and isinstance(lbl_node[1], str) else None
        if not name:
            continue
        at_node = _find(lbl_node, 'at')
        if at_node is None or len(at_node) < 3:
            continue
        try:
            x, y = float(at_node[1]), float(at_node[2])
        except (ValueError, TypeError):
            continue
        root = uf.root_of(x, y)
        # Global labels take priority; overwrite local labels
        net_names[root] = name

    # --- hierarchical labels (treated like global for connectivity purposes) ---
    for lbl_node in _find_all(sexp, 'hierarchical_label'):
        name = lbl_node[1] if len(lbl_node) > 1 and isinstance(lbl_node[1], str) else None
        if not name:
            continue
        at_node = _find(lbl_node, 'at')
        if at_node is None or len(at_node) < 3:
            continue
        try:
            x, y = float(at_node[1]), float(at_node[2])
        except (ValueError, TypeError):
            continue
        root = uf.root_of(x, y)
        if root not in net_names:
            net_names[root] = name

    # --- power symbols: their pin position defines a net name ---
    for sym in placed:
        if not sym.is_power:
            continue
        power_name = sym.value  # e.g. "GND", "VCC", "+5V", "0"
        for p in sym.pins:
            root = uf.root_of(p.abs_x, p.abs_y)
            if root not in net_names:
                net_names[root] = power_name

    # Second pass: after all labels are set, union same-named local label groups.
    # (Two labels with the same name on disconnected wires are the same net.)
    # We first invert net_names to group roots by name.
    name_to_root: Dict[str, int] = {}
    for root, name in list(net_names.items()):
        if name in name_to_root:
            # merge the two roots
            existing = name_to_root[name]
            # use the internal parent dict to union them directly
            a = uf.find(existing)
            b = uf.find(root)
            if a != b:
                uf._parent[a] = b
                # keep the name on the new root
                net_names[b] = name
                if a in net_names:
                    del net_names[a]
            name_to_root[name] = uf.find(b)
        else:
            name_to_root[name] = uf.find(root)

    # Rebuild net_names with canonical roots after the merges above
    rebuilt: Dict[int, str] = {}
    for name, root in name_to_root.items():
        canonical = uf.find(root)
        rebuilt[canonical] = name
    net_names = rebuilt

    return uf, net_names


# ---------------------------------------------------------------------------
# Step 4 – assemble Netlist
# ---------------------------------------------------------------------------

def _split_lib_id(lib_id: str) -> Tuple[str, str]:
    """Split "Device:R" into ("Device", "R")."""
    if ':' in lib_id:
        lib, sym = lib_id.split(':', 1)
        return lib, sym
    return '', lib_id


def _build_netlist(placed: List[_PlacedSymbol],
                   uf: _UnionFind,
                   net_names: Dict[int, str]) -> Netlist:
    components: Dict[str, NetlistComponent] = {}
    # net_key → list of (pin, ref, pin_name, ptype)
    net_map: Dict[int, List[Tuple[str, str, str, str]]] = {}

    for sym in placed:
        if sym.is_power:
            continue

        lib, symbol = _split_lib_id(sym.lib_id)
        components[sym.ref] = NetlistComponent(
            ref=sym.ref,
            value=sym.value,
            symbol=symbol,
            lib=lib,
            description=sym.description,
            pin_count=len(sym.pins),
        )

        for p in sym.pins:
            root = uf.root_of(p.abs_x, p.abs_y)
            net_map.setdefault(root, []).append(
                (p.num, sym.ref, p.name, p.ptype)
            )

    # Build Net objects
    nets: List[Net] = []
    code_counter = 1
    for root, pin_list in net_map.items():
        # Determine net name
        canonical_root = uf.find(root)
        name = net_names.get(canonical_root)
        if name is None:
            # Try the root directly (should be same, but be defensive)
            name = net_names.get(root)
        if name is None:
            # Auto-generate: Net-({ref}-Pad{pin}) from first entry
            pnum, pref, _, _ = pin_list[0]
            name = f'Net-({pref}-Pad{pnum})'

        pins: List[NetlistPin] = []
        for pnum_str, pref, pfunc, ptype in pin_list:
            try:
                pin_int = int(pnum_str)
            except ValueError:
                pin_int = 0
            pins.append(NetlistPin(
                ref=pref,
                pin=pin_int,
                pintype=ptype,
                pinfunction=pfunc,
            ))

        nets.append(Net(code=code_counter, name=name, pins=pins))
        code_counter += 1

    # Back-fill pin_count from actual net data (overrides the lib count)
    pins_seen: Dict[str, set] = {}
    for net in nets:
        for pin in net.pins:
            pins_seen.setdefault(pin.ref, set()).add(pin.pin)
    for ref, pin_set in pins_seen.items():
        if ref in components:
            components[ref].pin_count = len(pin_set)

    return Netlist(components=components, nets=nets)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def parse_schematic(path: str | Path) -> Netlist:
    """
    Parse a flat KiCad .kicad_sch file and return a Netlist.

    Hierarchical sub-sheets are not followed; only the single file is parsed.
    Power symbols (ref starting with '#') are excluded from components but
    their pin positions define net names.
    """
    text = Path(path).read_text(encoding='utf-8')
    tokens = _tokenize(text)
    sexp, _ = _parse_one(tokens, 0)

    lib_pins  = _parse_lib_symbols(sexp)
    placed    = _parse_placed_symbols(sexp, lib_pins)
    uf, net_names = _build_connectivity(sexp, placed)
    return _build_netlist(placed, uf, net_names)
