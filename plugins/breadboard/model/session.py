"""
Save / load breadboard sessions to / from JSON.

File format (version 1):
{
  "version": 1,
  "netlist": "/absolute/path/to/project.net",
  "terminals": {"GND": "0", "V1": "/Vplus"},
  "placements": [
    {"ref": "R1", "type_id": "R", "flipped": false,
     "pins": [[1, ["tie", 10, "e"]], [2, ["tie", 15, "e"]]]}
  ],
  "wires": [
    {"h1": ["tie", 5, "a"], "h2": ["rail", "top_plus", 3], "color": "#e8c020"}
  ]
}
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from .breadboard import (
    Breadboard, PlacedComponent, Wire,
    TieHole, RailHole, Terminal, Hole,
    TERMINAL_NAMES, PROBE_NAMES,
)

SESSION_VERSION = 1


# ---------------------------------------------------------------------------
# Hole serialisation helpers
# ---------------------------------------------------------------------------

def _hole_to_json(h: Hole) -> list:
    if isinstance(h, TieHole):
        # Omit section when 0 for backward compatibility
        return ['tie', h.col, h.row] if h.section == 0 else ['tie', h.col, h.row, h.section]
    if isinstance(h, RailHole):
        return ['rail', h.rail, h.index] if h.section == 0 else ['rail', h.rail, h.index, h.section]
    if isinstance(h, Terminal):
        return ['terminal', h.name]
    raise TypeError(f"Unknown hole type: {type(h)}")


def _hole_from_json(data: list) -> Hole:
    kind = data[0]
    if kind == 'tie':
        section = data[3] if len(data) > 3 else 0
        return TieHole(col=data[1], row=data[2], section=section)
    if kind == 'rail':
        section = data[3] if len(data) > 3 else 0
        return RailHole(rail=data[1], index=data[2], section=section)
    if kind == 'terminal':
        return Terminal(name=data[1])
    raise ValueError(f"Unknown hole kind: {kind!r}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save_session(board: Breadboard, netlist_path: Optional[str], path: str) -> None:
    """Serialise the current board state to a .kicad_bbrd JSON file."""
    doc: Dict[str, Any] = {
        'version': SESSION_VERSION,
        'netlist': netlist_path or '',
        'board': {'layout': board.layout},
        'terminals': {},
        'placements': [],
        'wires': [],
    }

    for name in TERMINAL_NAMES:
        net = board.get_terminal_net(name)
        if net:
            doc['terminals'][name] = net

    for ref, placed in board.placements.items():
        doc['placements'].append({
            'ref': ref,
            'type_id': placed.type_id,
            'flipped': placed.flipped,
            'pins': [
                [pin_num, _hole_to_json(hole)]
                for pin_num, hole in sorted(placed.pin_holes.items())
            ],
        })

    for wire in board.wires:
        doc['wires'].append({
            'h1': _hole_to_json(wire.h1),
            'h2': _hole_to_json(wire.h2),
            'color': wire.color,
        })

    probes = {}
    for name in PROBE_NAMES:
        hole   = board.get_probe_hole(name)
        net    = board.get_probe_net(name)
        offset = board.get_probe_label_offset(name)
        if hole is not None or net:
            probes[name] = {
                'hole':   _hole_to_json(hole) if hole is not None else None,
                'net':    net,
                'offset': list(offset),
            }
    if probes:
        doc['probes'] = probes

    Path(path).write_text(json.dumps(doc, indent=2), encoding='utf-8')


def load_session(path: str) -> Dict[str, Any]:
    """
    Load a .kicad_bbrd file and return a dict with:
      'netlist_path': str
      'board': Breadboard  (restored state)

    Raises ValueError on format errors.
    """
    raw = json.loads(Path(path).read_text(encoding='utf-8'))

    version = raw.get('version', 0)
    if version != SESSION_VERSION:
        raise ValueError(f"Unsupported session version {version} (expected {SESSION_VERSION})")

    board_cfg = raw.get('board', {})
    board = Breadboard(layout=board_cfg.get('layout', 'full'))

    for name, net in raw.get('terminals', {}).items():
        if name in TERMINAL_NAMES and net:
            board.assign_terminal(name, net)

    for p in raw.get('placements', []):
        pin_holes = {int(pin): _hole_from_json(hole) for pin, hole in p['pins']}
        placed = PlacedComponent(
            ref=p['ref'],
            type_id=p['type_id'],
            pin_holes=pin_holes,
            flipped=p.get('flipped', False),
        )
        board.place(placed)

    for w in raw.get('wires', []):
        board.add_wire(_hole_from_json(w['h1']), _hole_from_json(w['h2']), w.get('color', '#e8c020'))

    for name, info in raw.get('probes', {}).items():
        if name in PROBE_NAMES:
            if info.get('hole'):
                board.place_probe(name, _hole_from_json(info['hole']))
            if info.get('net'):
                board.assign_probe_net(name, info['net'])
            off = info.get('offset')
            if off and len(off) == 2:
                board.set_probe_label_offset(name, int(off[0]), int(off[1]))

    return {
        'netlist_path': raw.get('netlist', '') or None,
        'board': board,
        'board_layout': board_cfg.get('layout', 'full'),
    }
