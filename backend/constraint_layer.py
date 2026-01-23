from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, List, Optional, Tuple
import math

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

@dataclass
class Thresholds:
    warning: int = 40
    critical: int = 70
    collapse: int = 85

@dataclass
class PendingEvent:
    id: str
    execute_round: int
    payload: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ConstraintState:
    # meters: 0..100 (higher = worse)
    climate_pressure: float = 35
    economic_pressure: float = 40
    ecology_pressure: float = 30

    # optional separate resource for mitigation
    capacity_points: int = 0

    # delayed events
    event_queue: List[PendingEvent] = field(default_factory=list)

@dataclass
class ConstraintResult:
    state: ConstraintState
    notices: List[str] = field(default_factory=list)     # UI system cards
    penalties: Dict[str, Any] = field(default_factory=dict)  # e.g. token_regen_delta, council_support_delta
    locks: List[str] = field(default_factory=list)       # e.g. ["increase_density", "increase_height"]
    triggered_events: List[str] = field(default_factory=list)
    executed_events: List[str] = field(default_factory=list)

class ConstraintLayer:
    """
    Minimal, explainable constraint layer (v0.1):
    - 3 meters: climate/economy/ecology pressures (0..100)
    - thresholds: warning/critical/collapse
    - mitigation via capacity_points (optional)
    - delayed events queue
    """

    def __init__(self, thresholds: Thresholds | None = None):
        self.t = thresholds or Thresholds()

    # ---------- Public API ----------
    def step(self, state: ConstraintState, actions: Dict[str, Any], round_idx: int) -> ConstraintResult:
        """
        Call once per round AFTER spatial/policy actions are applied.
        round_idx: 1-based or 0-based is fine as long as consistent with execute_round scheduling.
        """
        res = ConstraintResult(state=state)

        # 1) capacity points regeneration (if you choose to use it)
        # Default: +1 per round.
        state.capacity_points += 1

        # 2) normalize inputs with defaults
        a = self._normalize_actions(actions)

        # 3) apply mitigation purchase first (so it can reduce upcoming events/pressures)
        self._apply_mitigation(state, a, res)

        # 4) update meters based on actions (proxy rules)
        self._update_climate(state, a, res)
        self._update_economy(state, a, res)
        self._update_ecology(state, a, res)

        # 5) cross-coupling (very light but makes it feel real)
        self._apply_couplings(state, res)

        # 6) threshold penalties + locks/veto
        self._apply_threshold_effects(state, a, res)

        # 7) schedule delayed events (based on current + past)
        self._schedule_events(state, a, round_idx, res)

        # 8) execute due events
        self._execute_due_events(state, round_idx, res)

        # 9) clamp meters
        state.climate_pressure = clamp(state.climate_pressure, 0, 100)
        state.economic_pressure = clamp(state.economic_pressure, 0, 100)
        state.ecology_pressure = clamp(state.ecology_pressure, 0, 100)

        return res

    # ---------- Internal helpers ----------
    def _normalize_actions(self, actions: Dict[str, Any]) -> Dict[str, Any]:
        venue_scale = actions.get("venue_scale", "small")
        if venue_scale not in ("small", "medium", "large"):
            venue_scale = "small"

        mitigation = actions.get("mitigation", "none")
        if mitigation not in ("none", "basic", "strong"):
            mitigation = "none"

        # ecological proxy: if green not provided, fallback to public space change
        delta_green = actions.get("delta_green_space_pct", None)
        if delta_green is None:
            delta_green = actions.get("delta_public_space_pct", 0)

        return {
            "delta_height_pct": float(actions.get("delta_height_pct", 0)),
            "delta_density": float(actions.get("delta_density", 0)),
            "delta_public_space_pct": float(actions.get("delta_public_space_pct", 0)),
            "delta_green_space_pct": float(delta_green),
            "delta_affordable_pct": float(actions.get("delta_affordable_pct", 0)),
            "venue_scale": venue_scale,
            "mitigation": mitigation,
        }

    def _apply_mitigation(self, state: ConstraintState, a: Dict[str, Any], res: ConstraintResult) -> None:
        """
        mitigation is a *design/policy package* that costs capacity_points and reduces pressures.
        """
        m = a["mitigation"]
        if m == "none":
            return

        cost = 2 if m == "basic" else 4
        if state.capacity_points < cost:
            res.notices.append(f"Mitigation '{m}' requested but insufficient capacity points (need {cost}).")
            return

        state.capacity_points -= cost
        # keep it simple & explainable: reduce each meter a bit; strong reduces more.
        if m == "basic":
            state.climate_pressure -= 6
            state.ecology_pressure -= 4
            state.economic_pressure -= 3
            res.notices.append("Mitigation (basic) applied: reduced climate/ecology/economy pressures.")
        else:
            state.climate_pressure -= 12
            state.ecology_pressure -= 9
            state.economic_pressure -= 6
            res.notices.append("Mitigation (strong) applied: significantly reduced system pressures.")

    def _update_climate(self, state: ConstraintState, a: Dict[str, Any], res: ConstraintResult) -> None:
        # background drift (time enemy)
        state.climate_pressure += 2

        # action impacts
        dh = a["delta_height_pct"]
        dd = a["delta_density"]
        dpub = a["delta_public_space_pct"]
        dgreen = a["delta_green_space_pct"]

        # increase in height/density adds climate stress
        state.climate_pressure += 0.4 * dh
        state.climate_pressure += 12.0 * dd

        # reducing public space worsens ventilation/heat
        if dpub < 0:
            state.climate_pressure += 0.8 * (-dpub)

        # reducing green worsens heat/carbon sink
        if dgreen < 0:
            state.climate_pressure += 1.0 * (-dgreen)

        # venue scale adds energy/footfall pressure
        vs = a["venue_scale"]
        state.climate_pressure += {"small": 2, "medium": 4, "large": 7}[vs]

    def _update_economy(self, state: ConstraintState, a: Dict[str, Any], res: ConstraintResult) -> None:
        # baseline market drift
        state.economic_pressure += 1.5

        dh = a["delta_height_pct"]
        dd = a["delta_density"]
        daff = a["delta_affordable_pct"]
        dpub = a["delta_public_space_pct"]
        vs = a["venue_scale"]

        # build/finance stress
        state.economic_pressure += 0.2 * dh
        if dd > 0:
            state.economic_pressure += 6.0 * dd

        # increasing affordable percentage increases viability pressure (short-term cash)
        if daff > 0:
            state.economic_pressure += 0.7 * daff

        # increasing public space (more generous scheme) adds short-term cost
        if dpub > 0:
            state.economic_pressure += 0.3 * dpub

        # venue operational risk
        state.economic_pressure += {"small": 1, "medium": 3, "large": 6}[vs]

    def _update_ecology(self, state: ConstraintState, a: Dict[str, Any], res: ConstraintResult) -> None:
        # slow variable drift
        state.ecology_pressure += 1

        dd = a["delta_density"]
        dpub = a["delta_public_space_pct"]
        dgreen = a["delta_green_space_pct"]
        vs = a["venue_scale"]

        # green proxy (tree canopy proxy): losing green increases eco pressure
        if dgreen < 0:
            state.ecology_pressure += 1.5 * (-dgreen)

        # losing public space fragments habitat / increases imperviousness proxy
        if dpub < 0:
            state.ecology_pressure += 0.6 * (-dpub)

        # higher density increases disturbance/imperviousness
        if dd > 0:
            state.ecology_pressure += 8.0 * dd

        # keep venue effect very light (optional); can be removed if you prefer
        state.ecology_pressure += {"small": 0, "medium": 1, "large": 2}[vs]

    def _apply_couplings(self, state: ConstraintState, res: ConstraintResult) -> None:
        # if ecology degrades, climate worsens
        if state.ecology_pressure >= self.t.warning:
            state.climate_pressure += 2
        # if climate critical, economy worsens (reviews/retrofits)
        if state.climate_pressure >= self.t.critical:
            state.economic_pressure += 3
        # if economy critical, ecology worsens (maintenance cut)
        if state.economic_pressure >= self.t.critical:
            state.ecology_pressure += 2

    def _apply_threshold_effects(self, state: ConstraintState, a: Dict[str, Any], res: ConstraintResult) -> None:
        t = self.t

        # --- Climate threshold effects ---
        if state.climate_pressure >= t.warning:
            res.penalties["action_token_cost_delta_for_growth"] = 1
            res.notices.append("Climate: warning — growth actions become harder (review friction).")

        if state.climate_pressure >= t.critical:
            res.penalties["token_regen_delta"] = res.penalties.get("token_regen_delta", 0) - 1
            res.penalties["council_support_delta"] = res.penalties.get("council_support_delta", 0) - 10
            res.notices.append("Climate: critical — approvals slow down; council becomes more cautious.")

        if state.climate_pressure >= t.collapse:
            res.locks.extend(["increase_density", "increase_height"])
            res.notices.append("Climate: collapse — further densification is vetoed by the system.")

        # --- Economy threshold effects ---
        if state.economic_pressure >= t.warning:
            res.penalties["token_regen_delta"] = res.penalties.get("token_regen_delta", 0) - 1
            res.notices.append("Economy: warning — capacity tightens (less room to act).")

        if state.economic_pressure >= t.critical:
            res.notices.append("Economy: critical — value engineering required (forced trade-off next round).")
            res.penalties["forced_tradeoff_next_round"] = True

        if state.economic_pressure >= 88:
            res.notices.append("Economy: funding withdrawal risk — approaching failure condition.")

        # --- Ecology threshold effects ---
        if state.ecology_pressure >= t.warning:
            res.notices.append("Ecology: warning — fragmentation risk rising; climate coupling intensifies.")

        if state.ecology_pressure >= t.critical:
            res.penalties["resident_trust_drift_delta"] = res.penalties.get("resident_trust_drift_delta", 0) - 2
            res.notices.append("Ecology: critical — public realm quality declines; resident trust erodes.")

        if state.ecology_pressure >= t.collapse:
            res.locks.extend(["decrease_green", "decrease_public_space"])
            res.notices.append("Ecology: collapse — further loss of green/public space is vetoed (irreversible risk).")

    def _schedule_events(self, state: ConstraintState, a: Dict[str, Any], round_idx: int, res: ConstraintResult) -> None:
        """
        Add delayed events to queue when triggers hit.
        Keep triggers simple and explainable.
        """
        # Helper to avoid duplicates
        def already_scheduled(event_id: str) -> bool:
            return any(e.id == event_id for e in state.event_queue)

        # C1 Heatwave shock (delayed)
        # Trigger: climate >= 72 (critical-ish)
        if state.climate_pressure >= 72 and not already_scheduled("C1_heatwave"):
            state.event_queue.append(PendingEvent(
                id="C1_heatwave",
                execute_round=round_idx + 2,
                payload={}
            ))
            res.triggered_events.append("C1_heatwave")
            res.notices.append("Event scheduled: Heatwave shock (in 2 rounds).")

        # E1 Interest rate spike
        if state.economic_pressure >= 75 and not already_scheduled("E1_rate_spike"):
            state.event_queue.append(PendingEvent(
                id="E1_rate_spike",
                execute_round=round_idx + 1,
                payload={}
            ))
            res.triggered_events.append("E1_rate_spike")
            res.notices.append("Event scheduled: Interest rate spike (next round).")

        # N2 Flood event proxy (only if public space reduced and eco already stressed)
        if a["delta_public_space_pct"] < 0 and state.ecology_pressure >= 60 and not already_scheduled("N2_flood"):
            state.event_queue.append(PendingEvent(
                id="N2_flood",
                execute_round=round_idx + 1,
                payload={}
            ))
            res.triggered_events.append("N2_flood")
            res.notices.append("Event scheduled: Flooded ground floor (next round).")

        # R1 Revenue relief (growth can relieve economy, but with eco cost)
        if a["delta_density"] > 0 and state.climate_pressure < 70 and not already_scheduled("R1_revenue_relief"):
            state.event_queue.append(PendingEvent(
                id="R1_revenue_relief",
                execute_round=round_idx + 2,
                payload={}
            ))
            res.triggered_events.append("R1_revenue_relief")
            res.notices.append("Event scheduled: Revenue relief (in 2 rounds).")

    def _execute_due_events(self, state: ConstraintState, round_idx: int, res: ConstraintResult) -> None:
        due = [e for e in state.event_queue if e.execute_round <= round_idx]
        if not due:
            return

        for e in due:
            if e.id == "C1_heatwave":
                # delayed consequence: trust + economy pressure up, public realm suffers
                state.climate_pressure += 4
                state.economic_pressure += 5
                # we can't directly change trust here unless you wire it; return as penalty suggestion:
                res.penalties["resident_trust_delta"] = res.penalties.get("resident_trust_delta", 0) - 8
                res.notices.append("Heatwave shock hits: resident comfort drops; emergency costs rise.")
                res.executed_events.append("C1_heatwave")

            elif e.id == "E1_rate_spike":
                state.economic_pressure += 8
                res.penalties["developer_trust_delta"] = res.penalties.get("developer_trust_delta", 0) - 6
                res.notices.append("Interest rate spike hits: financing pressure increases sharply.")
                res.executed_events.append("E1_rate_spike")

            elif e.id == "N2_flood":
                state.economic_pressure += 6
                res.penalties["council_support_delta"] = res.penalties.get("council_support_delta", 0) - 5
                res.notices.append("Flood event hits: repair/insurance costs; public accountability rises.")
                res.executed_events.append("N2_flood")

            elif e.id == "R1_revenue_relief":
                state.economic_pressure -= 6
                state.ecology_pressure += 2
                res.notices.append("Revenue relief arrives: viability improves, but ecological stress increases.")
                res.executed_events.append("R1_revenue_relief")

        # remove executed events
        state.event_queue = [e for e in state.event_queue if e.execute_round > round_idx]


# Convenience helpers for (de)serialization
def state_to_dict(state: ConstraintState) -> Dict[str, Any]:
    return {
        "climate_pressure": state.climate_pressure,
        "economic_pressure": state.economic_pressure,
        "ecology_pressure": state.ecology_pressure,
        "capacity_points": state.capacity_points,
        "event_queue": [asdict(e) for e in state.event_queue],
    }

def state_from_dict(d: Dict[str, Any]) -> ConstraintState:
    eq = [PendingEvent(**e) for e in d.get("event_queue", [])]
    return ConstraintState(
        climate_pressure=float(d.get("climate_pressure", 35)),
        economic_pressure=float(d.get("economic_pressure", 40)),
        ecology_pressure=float(d.get("ecology_pressure", 30)),
        capacity_points=int(d.get("capacity_points", 0)),
        event_queue=eq,
    )
