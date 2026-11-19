"""Per-module lifecycle state stored in docs/foundry/state/<module>.json."""
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Literal

from .config import load_config

Track = Literal["product", "feature", "small", "takeover"]
Stage = Literal["discover", "define", "design", "build", "verify", "release",
                "learn", "takeover-1", "takeover-2"]
Takeover = Literal["none", "in-progress", "complete"]

GATES = ("G1", "G2", "G2-short", "TAKEOVER", "G4")
STATE_DIR = Path("docs") / "foundry" / "state"
_MODULE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass
class GateRecord:
    approved_by: str
    at: str
    pr: int
    commit: str


@dataclass
class HistoryEntry:
    at: str
    stage: str
    event: str
    by: str
    note: str


@dataclass
class ModuleState:
    module: str
    track: Track
    stage: Stage
    takeover: Takeover
    gates: Dict[str, GateRecord] = field(default_factory=dict)
    history: List[HistoryEntry] = field(default_factory=list)


def _path(repo: Path, module: str) -> Path:
    if not _MODULE_RE.match(module):
        raise ValueError(f"invalid module name: {module!r}")
    return Path(repo) / STATE_DIR / f"{module}.json"


def load_state(repo: Path, module: str) -> ModuleState:
    path = _path(repo, module)
    if not path.is_file():
        if load_config(repo).project_kind == "takeover":
            return ModuleState(module, "takeover", "takeover-1", "in-progress")
        return ModuleState(module, "product", "discover", "none")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ModuleState(
            module=raw["module"],
            track=raw["track"],
            stage=raw["stage"],
            takeover=raw["takeover"],
            gates={k: GateRecord(**v) for k, v in raw["gates"].items()},
            history=[HistoryEntry(**h) for h in raw["history"]],
        )
    except (ValueError, KeyError, TypeError) as e:  # JSONDecodeError is a ValueError
        raise ValueError(f"{path}: invalid state file ({e})") from e


def save_state(repo: Path, state: ModuleState) -> Path:
    path = _path(repo, state.module)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(state), sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return path


def record_gate(state: ModuleState, gate: str, rec: GateRecord) -> ModuleState:
    if gate not in GATES:
        raise ValueError(f"unknown gate {gate!r}; expected one of {', '.join(GATES)}")
    state.gates[gate] = rec
    state.history.append(HistoryEntry(
        at=rec.at, stage=state.stage, event="gate-approved", by=rec.approved_by,
        note=f"{gate} approved (PR #{rec.pr}, {rec.commit})",
    ))
    return state


# gate -> (stages it may be requested from, stage it moves the module to)
_TRANSITIONS = {"G1": (("discover", "define"), "design"), "G2": (("design",), "build")}
_NEXT_FROM = {"discover": "G1", "define": "G1", "design": "G2"}


def advance(state: ModuleState, gate: str, by: str, at: str) -> ModuleState:
    """Move the module to the stage a requested gate leads to.

    G1 is requested from `discover` or `define` (nothing separates them), G2
    from `design`. Any other stage raises ValueError naming the next gate.
    """
    if gate not in _TRANSITIONS:
        raise ValueError(f"cannot request gate {gate!r}; only G1 and G2 move state here")
    allowed, target = _TRANSITIONS[gate]
    if state.stage not in allowed:
        nxt = _NEXT_FROM.get(state.stage, "none")
        raise ValueError(f"gate {gate} cannot be requested: module {state.module} is in "
                         f"{state.stage}; next gate for {state.module} is {nxt}")
    state.stage = target
    state.history.append(HistoryEntry(at=at, stage=target, event=f"gate {gate} requested",
                                      by=by, note=""))
    return state
