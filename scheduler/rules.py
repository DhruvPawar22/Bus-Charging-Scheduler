from __future__ import annotations
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from scheduler.models import Scenario


class ScoringRule(ABC):
    """
    Base class for all priority-scoring rules.

    When multiple buses queue at a station, each rule contributes a score.
    The scheduler sums: weight * rule.score(...) across all registered rules
    and serves the bus with the highest total first.

    Contract:
      - Higher return value = higher priority = served sooner.
      - All built-in rules return values in MINUTES, keeping them
        directly comparable when weights are equal.
      - Rules must not mutate any state — they are pure scoring functions.
    """

    @abstractmethod
    def score(
        self,
        bus_id: str,
        arrival_time: float,
        current_time: float,
        bus_states: dict,
        bus_configs: dict,
        scenario: "Scenario",
    ) -> float:
        """
        Args:
            bus_id:       The bus being scored.
            arrival_time: When this bus arrived at the current station (minutes).
            current_time: The simulation clock right now (minutes).
            bus_states:   Dict[bus_id, BusState] — live simulation state.
            bus_configs:  Dict[bus_id, BusConfig] — static scenario config.
            scenario:     The full scenario (route, physics, weights, etc.).

        Returns:
            A float score. Higher = higher priority.
        """


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------

class IndividualWaitRule(ScoringRule):
    """
    Prioritises buses that have accumulated the most delay at previous stations.

    Score = total wait this bus has already served across all prior charging stops.
    Does NOT include the current wait — that is handled by OverallEfficiencyRule —
    so the two rules remain genuinely independent.

    Effect: a bus that was unlucky earlier (long queues at A or B) gets
    compensated at later stations.
    """

    def score(self, bus_id, arrival_time, current_time, bus_states, bus_configs, scenario):
        return bus_states[bus_id].total_wait_min


class OperatorFairnessRule(ScoringRule):
    """
    Prioritises buses from the operator whose fleet is most behind schedule.

    Score = mean total_wait_min across ALL buses belonging to the same operator.

    Effect: if KPN's buses are collectively running 30 min late while Freshbus
    is on time, KPN buses get a score boost at every contested station.
    Tuning operator weight up amplifies inter-operator rebalancing;
    setting it to 0 makes the scheduler operator-blind.
    """

    def score(self, bus_id, arrival_time, current_time, bus_states, bus_configs, scenario):
        op_id = bus_configs[bus_id].operator_id
        delays = [
            bus_states[bid].total_wait_min
            for bid, bc in bus_configs.items()
            if bc.operator_id == op_id
        ]
        return sum(delays) / len(delays) if delays else 0.0


class OverallEfficiencyRule(ScoringRule):
    """
    Prioritises buses that have been waiting longest at the CURRENT station (FIFO).

    Score = current_time - arrival_time  (minutes spent in this queue so far).

    Effect: earlier-arriving buses get priority, which minimises total waiting
    time across all buses at this station. For equal service times (all charges
    take 25 min), FIFO is the queue discipline that minimises average wait.
    Tuning overall weight up pushes the scheduler toward strict local FIFO.
    """

    def score(self, bus_id, arrival_time, current_time, bus_states, bus_configs, scenario):
        return current_time - arrival_time


# ---------------------------------------------------------------------------
# Rule registry
# ---------------------------------------------------------------------------

DEFAULT_RULES: Dict[str, ScoringRule] = {
    "individual": IndividualWaitRule(),
    "operator":   OperatorFairnessRule(),
    "overall":    OverallEfficiencyRule(),
}
"""
The scheduler iterates over this dict and multiplies each rule's score by
the matching weight from scenario.weights (defaulting to 0.0 if absent).

To add a new rule:
  1. Define a class inheriting ScoringRule above.
  2. Add it here:  "my_rule": MyRule()
  3. Add its weight to the scenario JSON: "weights": { ..., "my_rule": 1.0 }

No other file needs to change.
"""
