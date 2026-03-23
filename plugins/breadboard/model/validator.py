"""
Circuit validator.

Compares the student's breadboard state against the schematic netlist and
reports two classes of error:

  OPEN_NET   — pins that belong to the same net are not electrically connected.
  SHORT      — pins from different nets are electrically connected.

Usage:
    results = validate(breadboard, netlist, placements_type_map)
    for r in results:
        print(r)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple

from .breadboard import Breadboard, Hole, TieHole, Terminal, TERMINAL_NAMES
from .components import guess_type_id
from .netlist import Net, Netlist


class IssueKind(Enum):
    OPEN_NET = 'open_net'
    SHORT = 'short'
    UNPLACED = 'unplaced'


@dataclass
class ValidationIssue:
    kind: IssueKind
    net_name: str
    description: str
    # Holes involved (for canvas highlighting)
    holes: List[Hole] = field(default_factory=list)

    def __str__(self):
        return f"[{self.kind.name}] {self.net_name}: {self.description}"


@dataclass
class ValidationResult:
    issues: List[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.issues) == 0

    def __str__(self):
        if self.ok:
            return "Circuit OK — all nets match."
        return '\n'.join(str(i) for i in self.issues)


def validate(board: Breadboard, netlist: Netlist) -> ValidationResult:
    """
    Validate the student's breadboard against the schematic netlist.

    Steps:
      1. Check all components are placed.
      2. Build the connectivity graph.
      3. For each schematic net, collect the holes of all its pins.
         If they are not all in the same connected component → OPEN_NET.
      4. Collect all (root, net_name) pairs; if two different nets share a
         root → SHORT.
    """
    result = ValidationResult()
    uf = board.build_connectivity()

    # Step 1 — unplaced physical components
    # Virtual components (simulation sources, power symbols) have no known
    # type_id and are handled via terminal assignments instead.
    for ref, comp in netlist.components.items():
        if board.get_placement(ref) is None:
            type_id = guess_type_id(ref, comp.value, comp.symbol)
            if type_id is not None:
                result.issues.append(ValidationIssue(
                    kind=IssueKind.UNPLACED,
                    net_name='',
                    description=f"{ref} ({comp.value}) is not placed on the breadboard.",
                ))

    # Step 2 — build a map: net_name → list of holes
    # Terminals assigned to a net count as a hole on that net.
    net_holes: Dict[str, List[Hole]] = {}

    for net in netlist.nets:
        holes: List[Hole] = []
        for pin_node in net.pins:
            hole = board.hole_for_pin(pin_node.ref, pin_node.pin)
            if hole is not None:
                holes.append(hole)
        if holes:
            net_holes[net.name] = holes

    # Add terminal holes for their assigned nets
    for term_name in TERMINAL_NAMES:
        net_name = board.get_terminal_net(term_name)
        if net_name:
            net_holes.setdefault(net_name, []).append(Terminal(term_name))

    # Step 3 — open nets
    for net_name, holes in net_holes.items():
        if len(holes) < 2:
            continue
        roots = {uf.find(h) for h in holes}
        if len(roots) > 1:
            result.issues.append(ValidationIssue(
                kind=IssueKind.OPEN_NET,
                net_name=net_name,
                description=(
                    f"Net '{net_name}' is not fully connected "
                    f"({len(roots)} disconnected groups)."
                ),
                holes=holes,
            ))

    # Step 4 — shorts: two nets sharing the same connected component
    root_to_nets: Dict[object, Set[str]] = {}
    for net_name, holes in net_holes.items():
        for h in holes:
            root = uf.find(h)
            root_to_nets.setdefault(root, set()).add(net_name)

    for root, net_names in root_to_nets.items():
        if len(net_names) > 1:
            names = ', '.join(sorted(net_names))
            # Collect all holes for these nets (for highlighting)
            shorted_holes = []
            for net_name in net_names:
                shorted_holes.extend(net_holes.get(net_name, []))
            result.issues.append(ValidationIssue(
                kind=IssueKind.SHORT,
                net_name=names,
                description=f"Nets {names} are shorted together.",
                holes=shorted_holes,
            ))

    return result
