from types import SimpleNamespace

from pytest import fixture, mark

from auto_pumping.config import PumpState, ValveState
from auto_pumping.dummy import DummyPumping
from auto_pumping.graph import PlanStep
from auto_pumping.ui import _Page


@fixture(scope="module")
def _dummy(config):
    # Building the pumping graph is expensive, so construct the system once and
    # reset its state between tests.
    return DummyPumping(config)


@fixture
def system(config, _dummy):
    _dummy._valve_states = {valve: None for valve in config.valves}
    _dummy._pump_states = {pump: None for pump in config.pumps}
    _dummy._stage_position = None
    return _dummy


def unknown(config, system):
    return _Page._get_unknown_state(config, system)


def test_everything_unknown_at_startup(config, system):
    # DummyPumping starts with no observability of any state.
    result = unknown(config, system)
    assert result["valves"] == list(config.valves)
    assert result["pumps"] == list(config.pumps)
    assert result["stage"] is True
    assert _Page._has_unknown_state(result) is True


def test_nothing_unknown_once_state_is_declared(config, system):
    for valve in config.valves:
        system.actuate_valve(valve, ValveState.CLOSED)
    for pump in config.pumps:
        system.set_pump(pump, PumpState.OFF)
    system.move_stage(config.stage[0])
    result = unknown(config, system)
    assert result == {"valves": [], "pumps": [], "stage": False}
    assert _Page._has_unknown_state(result) is False


def test_partially_unknown_state(config, system):
    for valve in config.valves:
        system.actuate_valve(valve, ValveState.CLOSED)
    system.move_stage(config.stage[0])
    first_valve = config.valves[0]
    system._valve_states[first_valve] = None
    result = unknown(config, system)
    assert result["valves"] == [first_valve]
    assert result["pumps"] == list(config.pumps)
    assert result["stage"] is False
    assert _Page._has_unknown_state(result) is True


def test_stage_outside_configured_positions_is_unknown(config, system):
    system.move_stage("somewhere-else")
    assert unknown(config, system)["stage"] is True


def test_declaring_state_makes_it_known(config, system):
    page = _Page.__new__(_Page)
    page._pumping_system = system
    valves = {valve: ValveState.OPEN for valve in config.valves}
    pumps = {pump: PumpState.ON for pump in config.pumps}
    page._declare_state(valves, pumps, config.stage[0])

    assert _Page._has_unknown_state(unknown(config, system)) is False
    for valve in config.valves:
        assert system.get_valve_state(valve) == ValveState.OPEN
    for pump in config.pumps:
        assert system.get_pump_state(pump) == PumpState.ON
    assert system.get_stage_position() == config.stage[0]


def test_declaring_state_does_not_disturb_safety_timers(config, system):
    page = _Page.__new__(_Page)
    page._pumping_system = system
    safety = system._safety_machine
    before_valves = dict(safety._valve_times)
    before_pumps = dict(safety._pump_times)

    page._declare_state(
        {config.valves[0]: ValveState.OPEN},
        {config.pumps[0]: PumpState.ON},
        config.stage[0],
    )

    # Nothing physically moved, so the time-based safety conditions must not restart.
    assert safety._valve_times == before_valves
    assert safety._pump_times == before_pumps


def test_declaring_omits_stage_when_not_requested(config, system):
    page = _Page.__new__(_Page)
    page._pumping_system = system
    page._declare_state({}, {}, None)
    assert system.get_stage_position() is None


class FakeSwitch:
    def __init__(self, value=None):
        self.value = value


class FakeButton:
    def __init__(self):
        self.enabled = True


def make_page(valves=None, pumps=None, stage=None, has_stage=False):
    page = _Page.__new__(_Page)
    page._unknown_valve_switches = valves or {}
    page._unknown_pump_switches = pumps or {}
    page._unknown_stage_toggle = FakeSwitch(stage) if has_stage else None
    page._unknown_submit_button = FakeButton()
    return page


@mark.parametrize(
    "valves, pumps, stage, has_stage, expected",
    [
        ({}, {}, None, False, True),
        ({"VA": FakeSwitch(None)}, {}, None, False, False),
        ({"VA": FakeSwitch(True)}, {}, None, False, True),
        ({"VA": FakeSwitch(False)}, {}, None, False, True),
        ({}, {"turbo": FakeSwitch(None)}, None, False, False),
        ({}, {"turbo": FakeSwitch(False)}, None, False, True),
        ({}, {}, None, True, False),
        ({}, {}, "loadlock", True, True),
        ({"VA": FakeSwitch(True)}, {"turbo": FakeSwitch(True)}, None, True, False),
        (
            {"VA": FakeSwitch(True)},
            {"turbo": FakeSwitch(True)},
            "loadlock",
            True,
            True,
        ),
    ],
)
def test_submit_is_gated_until_every_item_is_set(
    valves, pumps, stage, has_stage, expected
):
    page = make_page(valves, pumps, stage, has_stage)
    assert page._unknown_selection_complete() is expected
    page._update_unknown_submit_state()
    assert page._unknown_submit_button.enabled is expected


class FakeDialog:
    def __init__(self):
        self.is_open = False
        self.opens = 0

    def open(self):
        self.is_open = True
        self.opens += 1

    def close(self):
        self.is_open = False


class FakeRefreshable:
    def __init__(self):
        self.refreshes = 0

    def refresh(self):
        self.refreshes += 1


def make_sync_page():
    page = _Page.__new__(_Page)
    page._unknown_dialog = FakeDialog()
    page._unknown_state = None
    page._build_unknown_content = FakeRefreshable()
    page._confirm_dialog = FakeDialog()
    page._shown_confirmation = None
    page._confirm_content = FakeSwitch("")
    return page


def snapshot(unknown, pending=None):
    return {"unknown": unknown, "pending_confirmation": pending}


KNOWN = {"valves": [], "pumps": [], "stage": False}


def test_dialog_opens_when_state_is_unknown():
    page = make_sync_page()
    page._sync_unknown_dialog(snapshot({"valves": ["VA"], "pumps": [], "stage": False}))
    assert page._unknown_dialog.is_open is True
    assert page._build_unknown_content.refreshes == 1


def test_dialog_is_not_reopened_while_unknown_set_is_unchanged():
    page = make_sync_page()
    unknown = {"valves": ["VA"], "pumps": [], "stage": False}
    page._sync_unknown_dialog(snapshot(unknown))
    page._sync_unknown_dialog(snapshot(dict(unknown)))
    page._sync_unknown_dialog(snapshot(dict(unknown)))
    # Rebuilding each tick would discard partly-entered input.
    assert page._unknown_dialog.opens == 1
    assert page._build_unknown_content.refreshes == 1


def test_dialog_rebuilds_when_unknown_set_changes():
    page = make_sync_page()
    page._sync_unknown_dialog(snapshot({"valves": ["VA"], "pumps": [], "stage": False}))
    page._sync_unknown_dialog(
        snapshot({"valves": ["VA", "VB"], "pumps": [], "stage": False})
    )
    assert page._build_unknown_content.refreshes == 2


def test_dialog_closes_once_state_becomes_known():
    page = make_sync_page()
    page._sync_unknown_dialog(snapshot({"valves": ["VA"], "pumps": [], "stage": False}))
    # e.g. another tab answered.
    page._sync_unknown_dialog(snapshot(dict(KNOWN)))
    assert page._unknown_dialog.is_open is False
    assert page._unknown_state is None


def test_dialog_stays_closed_when_nothing_is_unknown():
    page = make_sync_page()
    page._sync_unknown_dialog(snapshot(dict(KNOWN)))
    assert page._unknown_dialog.is_open is False
    assert page._unknown_dialog.opens == 0


def test_confirmation_dialog_is_suppressed_while_state_is_unknown():
    page = make_sync_page()
    request = SimpleNamespace(step=PlanStep(stage="loadlock"))
    page._sync_confirm_dialog(
        snapshot({"valves": ["VA"], "pumps": [], "stage": False}, pending=request)
    )
    # Two stacked persistent dialogs would trap the operator.
    assert page._confirm_dialog.is_open is False
    assert page._shown_confirmation is None


def test_confirmation_dialog_closes_if_state_becomes_unknown_while_shown():
    page = make_sync_page()
    request = SimpleNamespace(step=PlanStep(stage="loadlock"))
    page._sync_confirm_dialog(snapshot(dict(KNOWN), pending=request))
    assert page._confirm_dialog.is_open is True
    page._sync_confirm_dialog(
        snapshot({"valves": ["VA"], "pumps": [], "stage": False}, pending=request)
    )
    assert page._confirm_dialog.is_open is False
