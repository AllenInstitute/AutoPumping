from types import SimpleNamespace

from pytest import fixture, mark

from auto_pumping.config import Confirm, PumpState, ValveState
from auto_pumping.executor import ConfirmationRequest, PlanExecutor
from auto_pumping.graph import PlanStep


def make_executor(confirm):
    pumping_system = SimpleNamespace(_config=SimpleNamespace(confirm=confirm))
    return PlanExecutor(pumping_system)


@mark.parametrize(
    "confirm, step, expected",
    [
        # Unlisted targets fall back to `default`.
        (Confirm(default=True), PlanStep(valve="VA", state=ValveState.OPEN), True),
        (Confirm(default=False), PlanStep(valve="VA", state=ValveState.OPEN), False),
        # A boolean entry applies to every action on that target.
        (
            Confirm(default=False, valve={"VA": True}),
            PlanStep(valve="VA", state=ValveState.CLOSED),
            True,
        ),
        (
            Confirm(default=True, valve={"VA": False}),
            PlanStep(valve="VA", state=ValveState.OPEN),
            False,
        ),
        # A state entry only applies to that target state.
        (
            Confirm(default=False, valve={"VA": ValveState.OPEN}),
            PlanStep(valve="VA", state=ValveState.OPEN),
            True,
        ),
        (
            Confirm(default=False, valve={"VA": ValveState.OPEN}),
            PlanStep(valve="VA", state=ValveState.CLOSED),
            False,
        ),
        # Other targets still use the default.
        (
            Confirm(default=True, valve={"VA": False}),
            PlanStep(valve="VB", state=ValveState.OPEN),
            True,
        ),
        # Pumps behave the same way.
        (
            Confirm(default=False, pump={"turbo": PumpState.ON}),
            PlanStep(pump="turbo", state=PumpState.ON),
            True,
        ),
        (
            Confirm(default=False, pump={"turbo": PumpState.ON}),
            PlanStep(pump="turbo", state=PumpState.OFF),
            False,
        ),
        (
            Confirm(default=True, pump={"turbo": False}),
            PlanStep(pump="turbo", state=PumpState.OFF),
            False,
        ),
        # Stage entries are boolean only.
        (Confirm(default=True), PlanStep(stage="loadlock"), True),
        (
            Confirm(default=True, stage={"loadlock": False}),
            PlanStep(stage="loadlock"),
            False,
        ),
        (
            Confirm(default=False, stage={"loadlock": True}),
            PlanStep(stage="loadlock"),
            True,
        ),
    ],
)
def test_requires_confirmation(confirm, step, expected):
    assert make_executor(confirm)._requires_confirmation(step) is expected


@mark.parametrize("default", [True, False])
def test_wait_steps_are_never_confirmed(default):
    step = PlanStep(wait={"PLL": {"LE": 1e-5}})
    assert make_executor(Confirm(default=default))._requires_confirmation(step) is False


def test_confirmation_request_resolves_approved():
    request = ConfirmationRequest(PlanStep(stage="loadlock"))
    assert not request.resolved
    request.resolve(True)
    assert request.resolved
    assert request.wait_for_response() is True


def test_confirmation_request_resolves_rejected():
    request = ConfirmationRequest(PlanStep(stage="loadlock"))
    request.resolve(False)
    assert request.wait_for_response() is False


def test_confirmation_request_gives_up_when_cancelled():
    request = ConfirmationRequest(PlanStep(stage="loadlock"))
    assert request.wait_for_response(should_continue=lambda: False) is False


class RecordingSystem:
    """Minimal pumping system that records the actions an executor performs."""

    def __init__(self, confirm):
        self._config = SimpleNamespace(confirm=confirm)
        self.actions = []

    def _move_stage(self, position):
        self.actions.append(("stage", position))
        return True

    def _actuate_valve(self, valve, state):
        self.actions.append(("valve", valve, state))
        return True

    def _set_pump(self, pump, state):
        self.actions.append(("pump", pump, state))
        return True

    def get_gauge_pressure(self, gauge):
        return 0.0


def run_plan(system, plan, answers):
    executor = PlanExecutor(system)
    executor.execute_plan(list(plan))
    for answer in answers:
        request = wait_for_request(executor)
        assert request is not None, "expected a confirmation request"
        request.resolve(answer)
    executor._thread.join(timeout=5)
    return executor


def wait_for_request(executor, timeout=5):
    from time import monotonic, sleep

    deadline = monotonic() + timeout
    while monotonic() < deadline:
        request = executor.pending_confirmation
        if request is not None and not request.resolved:
            return request
        sleep(0.01)
    return None


def test_approved_steps_are_executed():
    system = RecordingSystem(Confirm(default=True))
    plan = [
        PlanStep(valve="VA", state=ValveState.OPEN),
        PlanStep(pump="turbo", state=PumpState.ON),
    ]
    run_plan(system, plan, answers=[True, True])
    assert system.actions == [
        ("valve", "VA", ValveState.OPEN),
        ("pump", "turbo", PumpState.ON),
    ]


def test_rejecting_stops_the_plan_at_that_step():
    system = RecordingSystem(Confirm(default=True))
    plan = [
        PlanStep(valve="VA", state=ValveState.OPEN),
        PlanStep(pump="turbo", state=PumpState.ON),
    ]
    executor = run_plan(system, plan, answers=[True, False])
    # The first step was applied, the rejected one and everything after it was not.
    assert system.actions == [("valve", "VA", ValveState.OPEN)]
    assert executor.plan == []


def test_unconfirmed_steps_run_without_prompting():
    system = RecordingSystem(Confirm(default=False))
    plan = [PlanStep(valve="VA", state=ValveState.OPEN)]
    executor = PlanExecutor(system)
    executor.execute_plan(list(plan))
    executor._thread.join(timeout=5)
    assert system.actions == [("valve", "VA", ValveState.OPEN)]
    assert executor.pending_confirmation is None


def test_cancel_releases_a_pending_confirmation():
    system = RecordingSystem(Confirm(default=True))
    executor = PlanExecutor(system)
    executor.execute_plan([PlanStep(valve="VA", state=ValveState.OPEN)])
    assert wait_for_request(executor) is not None
    # Would deadlock if cancel() joined the thread without resolving the request.
    executor.cancel()
    assert executor.pending_confirmation is None
    assert system.actions == []
