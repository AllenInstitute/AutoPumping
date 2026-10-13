import asyncio
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

from pytest import fixture, mark

from auto_pumping.config import PumpState, ValveState
from auto_pumping.dummy import DummyPumping
from auto_pumping.executor import ConfirmationRequest
from auto_pumping.graph import PlanStep
from auto_pumping.ui import GaugeHistory, _Page


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
    page._local_confirmation = None
    page._build_confirmation_content = FakeRefreshable()
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
    request = SimpleNamespace(step=PlanStep(stage="loadlock"), resolved=False)
    page._sync_confirm_dialog(
        snapshot({"valves": ["VA"], "pumps": [], "stage": False}, pending=request)
    )
    # Two stacked persistent dialogs would trap the operator.
    assert page._confirm_dialog.is_open is False
    assert page._shown_confirmation is None


def test_confirmation_dialog_closes_if_state_becomes_unknown_while_shown():
    page = make_sync_page()
    request = SimpleNamespace(step=PlanStep(stage="loadlock"), resolved=False)
    page._sync_confirm_dialog(snapshot(dict(KNOWN), pending=request))
    assert page._confirm_dialog.is_open is True
    page._sync_confirm_dialog(
        snapshot({"valves": ["VA"], "pumps": [], "stage": False}, pending=request)
    )
    assert page._confirm_dialog.is_open is False


def test_stale_snapshot_does_not_reopen_answered_confirmation():
    page = make_sync_page()
    request = ConfirmationRequest(PlanStep(stage="loadlock"))
    stale_snapshot = snapshot(dict(KNOWN), pending=request)
    page._sync_confirm_dialog(stale_snapshot)
    assert page._confirm_dialog.is_open is True

    request.resolve(True)
    page._sync_confirm_dialog(stale_snapshot)

    assert page._confirm_dialog.is_open is False
    assert page._shown_confirmation is None


def make_action_page(auto_mode=False, action_lock=None):
    page = _Page.__new__(_Page)
    page._pumping_system = SimpleNamespace(
        plan=[],
        _plan_executor=SimpleNamespace(execute_plan=Mock()),
        _pumping_graph=SimpleNamespace(
            make_plan=Mock(),
            _last_goal_state_data={},
        ),
        _get_state=Mock(return_value=object()),
        _actuate_valve=Mock(return_value=True),
        _set_pump=Mock(return_value=True),
        _move_stage=Mock(return_value=True),
    )
    page._auto_switch = FakeSwitch(auto_mode)
    page._confirm_dialog = FakeDialog()
    page._shown_confirmation = None
    page._local_confirmation = None
    page._build_confirmation_content = FakeRefreshable()
    page._action_lock = action_lock if action_lock is not None else Lock()

    async def refresh():
        return None

    page._refresh = refresh
    return page


async def complete_after_confirmation(page, operation, approved):
    task = asyncio.create_task(operation)
    while page._local_confirmation is None:
        await asyncio.sleep(0)
    request = page._local_confirmation
    assert page._confirm_dialog.is_open
    page._on_confirm_response(approved)
    await task
    return request


def patch_async_ui_io(monkeypatch):
    import auto_pumping.ui as ui_module

    async def inline_io_bound(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(ui_module.run, "io_bound", inline_io_bound)
    monkeypatch.setattr(
        ui_module.background_tasks,
        "create",
        lambda coroutine: asyncio.create_task(coroutine),
    )


def test_manual_valve_action_requires_confirmation_and_cancel_does_not_actuate(
    monkeypatch,
):
    patch_async_ui_io(monkeypatch)
    page = make_action_page()

    request = asyncio.run(
        complete_after_confirmation(
            page,
            page._on_valve_change(SimpleNamespace(value=False), "VA"),
            approved=False,
        )
    )

    assert request.description == "Close VA"
    page._pumping_system._actuate_valve.assert_not_called()
    assert page._action_lock.locked() is False


def test_manual_valve_action_runs_after_confirmation(monkeypatch):
    patch_async_ui_io(monkeypatch)
    page = make_action_page()

    request = asyncio.run(
        complete_after_confirmation(
            page,
            page._on_valve_change(SimpleNamespace(value=True), "VA"),
            approved=True,
        )
    )

    assert request.description == "Open VA"
    page._pumping_system._actuate_valve.assert_called_once_with(
        "VA", ValveState.OPEN
    )


@mark.parametrize(
    "operation, description",
    [
        ("pump", "Turn on turbo pump"),
        ("stage", "Move stage to loadlock"),
    ],
)
def test_manual_pump_and_stage_actions_require_confirmation(
    monkeypatch, operation, description
):
    patch_async_ui_io(monkeypatch)
    page = make_action_page()

    async def run_action():
        if operation == "pump":
            return await complete_after_confirmation(
                page,
                page._on_pump_change(SimpleNamespace(value=True), "turbo"),
                approved=True,
            )
        return await complete_after_confirmation(
            page, page._on_stage_click("loadlock"), approved=True
        )

    assert asyncio.run(run_action()).description == description
    if operation == "pump":
        page._pumping_system._set_pump.assert_called_once_with(
            "turbo", PumpState.ON
        )
    else:
        page._pumping_system._move_stage.assert_called_once_with("loadlock")


@mark.parametrize("approved", [True, False])
def test_auto_mode_shows_proposed_plan_and_starts_only_on_approval(
    monkeypatch, approved
):
    patch_async_ui_io(monkeypatch)
    page = make_action_page(auto_mode=True)
    plan = [
        PlanStep(wait={"PLL": {"GE": 450}}),
        PlanStep(valve="VV1", state=ValveState.CLOSED),
    ]
    page._pumping_system._pumping_graph.make_plan.return_value = plan

    request = asyncio.run(
        complete_after_confirmation(
            page,
            page._on_named_state_click("venting"),
            approved=approved,
        )
    )

    assert request.description == "Proposed plan"
    assert request.plan == plan
    if approved:
        page._pumping_system._plan_executor.execute_plan.assert_called_once_with(plan)
    else:
        page._pumping_system._plan_executor.execute_plan.assert_not_called()


def test_action_lock_prevents_overlapping_confirmations():
    lock = Lock()
    first_page = make_action_page(action_lock=lock)
    second_page = make_action_page(action_lock=lock)

    assert first_page._begin_action() is True
    assert second_page._begin_action() is False

    first_page._end_action()
    assert second_page._begin_action() is True
    second_page._end_action()


def test_plan_timeline_renders_steps_with_shared_timeline_widgets(monkeypatch):
    import auto_pumping.ui as ui_module

    timeline_context = MagicMock()
    timeline_context.classes.return_value = timeline_context
    timeline = Mock(return_value=timeline_context)
    timeline_entry = Mock()
    monkeypatch.setattr(ui_module.ui, "timeline", timeline)
    monkeypatch.setattr(ui_module.ui, "timeline_entry", timeline_entry)
    page = _Page.__new__(_Page)
    plan = [
        PlanStep(wait={"PLL": {"GE": 450}}),
        PlanStep(valve="VV1", state=ValveState.CLOSED),
        PlanStep(pump="turbo", state=PumpState.ON),
        PlanStep(stage="loadlock"),
    ]

    page._build_plan_timeline(plan, compact=True)

    timeline.assert_called_once_with(side="right")
    timeline_context.classes.assert_called_once_with("confirmation-plan-timeline")
    assert [call.args[0] for call in timeline_entry.call_args_list] == [
        "Wait for PLL >= 450.0",
        "Close VV1",
        "Turn on turbo pump",
        "Move stage to loadlock",
    ]


# --- GaugeHistory ---------------------------------------------------------


def test_history_records_samples_in_order():
    history = GaugeHistory(["PLL"])
    history.record({"PLL": 1e-5}, now=100.0)
    history.record({"PLL": 2e-5}, now=101.0)
    assert history.series() == {
        "PLL": [[100_000, 1e-5], [101_000, 2e-5]]
    }


def test_history_timestamps_are_milliseconds():
    history = GaugeHistory(["PLL"])
    history.record({"PLL": 1e-5}, now=1.5)
    assert history.series()["PLL"][0][0] == 1500


def test_history_dedupes_samples_that_are_too_close():
    history = GaugeHistory(["PLL"], min_interval=0.5)
    assert history.record({"PLL": 1e-5}, now=100.0) is True
    # A second tab polling almost simultaneously must not double-sample.
    assert history.record({"PLL": 2e-5}, now=100.1) is False
    assert history.record({"PLL": 3e-5}, now=100.6) is True
    assert history.series()["PLL"] == [[100_000, 1e-5], [100_600, 3e-5]]


def test_history_drops_samples_outside_the_window():
    history = GaugeHistory(["PLL"], window=10.0, min_interval=0.0)
    for offset in range(0, 25, 5):
        history.record({"PLL": 1e-5}, now=100.0 + offset)
    timestamps = [point[0] for point in history.series()["PLL"]]
    # now=120, window=10 -> anything before t=110 is dropped.
    assert timestamps == [110_000, 115_000, 120_000]


@mark.parametrize("value", [0, -1e-6, None])
def test_history_maps_unplottable_readings_to_none(value):
    history = GaugeHistory(["PLL"])
    history.record({"PLL": value}, now=100.0)
    # A log axis cannot show these; a gap is honest, a clamped value is not.
    assert history.series() == {"PLL": [[100_000, None]]}


def test_history_keeps_positive_readings_intact():
    history = GaugeHistory(["PLL"], min_interval=0.0)
    history.record({"PLL": 5e-6}, now=100.0)
    history.record({"PLL": 0.0}, now=101.0)
    history.record({"PLL": 7e-6}, now=102.0)
    assert history.series()["PLL"] == [
        [100_000, 5e-6],
        [101_000, None],
        [102_000, 7e-6],
    ]


def test_history_tracks_each_gauge_separately():
    history = GaugeHistory(["A", "B"])
    history.record({"A": 1e-5, "B": 2e-5}, now=100.0)
    assert history.series() == {"A": [[100_000, 1e-5]], "B": [[100_000, 2e-5]]}


def test_history_reports_missing_gauge_as_gap():
    history = GaugeHistory(["A", "B"])
    history.record({"A": 1e-5}, now=100.0)
    assert history.series()["B"] == [[100_000, None]]


def test_history_starts_empty():
    assert GaugeHistory(["PLL"]).series() == {"PLL": []}


def test_history_is_shared_between_pages(config):
    # A tab opened later should see history already collected, not a blank plot.
    history = GaugeHistory(config.gauges)
    history.record({gauge: 1e-5 for gauge in config.gauges}, now=100.0)
    page = _Page.__new__(_Page)
    page._history = history
    assert page._history.series()[config.gauges[0]] == [[100_000, 1e-5]]


class _StubChart:
    """Stands in for `ui.echart`, which needs a browser client to exist."""

    def __init__(self, options):
        self.options = options
        self.updates = 0

    def update(self):
        self.updates += 1


def _chart_page(config):
    page = _Page.__new__(_Page)
    page._config = config
    page._action_lock = Lock()
    page._gauge_chart = _StubChart(page._gauge_chart_options())
    return page


def test_chart_uses_a_log_pressure_axis(config):
    options = _chart_page(config)._gauge_chart.options
    assert options["yAxis"]["type"] == "log"
    assert options["xAxis"]["type"] == "time"
    assert [s["name"] for s in options["series"]] == list(config.gauges)


def test_applying_series_fills_the_chart(config):
    page = _chart_page(config)
    gauge = config.gauges[0]
    page._apply_gauge_series({gauge: [[100_000, 1e-5]]})
    series = {s["name"]: s["data"] for s in page._gauge_chart.options["series"]}
    assert series[gauge] == [[100_000, 1e-5]]
    assert page._gauge_chart.updates == 1


def test_applying_unchanged_series_does_not_redraw(config):
    page = _chart_page(config)
    data = {gauge: [[100_000, 1e-5]] for gauge in config.gauges}
    page._apply_gauge_series(data)
    page._apply_gauge_series(data)
    # Pushing an identical frame every second would redraw the plot for nothing.
    assert page._gauge_chart.updates == 1


def test_applying_series_without_a_chart_is_a_no_op(config):
    page = _Page.__new__(_Page)
    page._config = config
    page._gauge_chart = None
    page._apply_gauge_series({})


def test_recorded_history_reaches_the_chart(config):
    # The whole path: reading -> history -> series -> chart data.
    history = GaugeHistory(config.gauges, min_interval=0.0)
    page = _chart_page(config)
    gauge = config.gauges[0]
    history.record({g: 1e-5 for g in config.gauges}, now=100.0)
    history.record({g: 2e-5 for g in config.gauges}, now=101.0)
    page._apply_gauge_series(history.series())
    series = {s["name"]: s["data"] for s in page._gauge_chart.options["series"]}
    assert series[gauge] == [[100_000, 1e-5], [101_000, 2e-5]]


def test_snapshot_accumulates_gauge_history(config, system):
    # Drives the real read path: hardware -> history -> snapshot -> chart.
    page = _chart_page(config)
    page._pumping_system = system
    page._history = GaugeHistory(config.gauges, min_interval=0.0)
    gauge = config.gauges[0]

    for _ in range(3):
        snapshot = page._get_snapshot()
        page._apply_gauge_series(snapshot["gauge_series"])

    series = {s["name"]: s["data"] for s in page._gauge_chart.options["series"]}
    assert len(series[gauge]) == 3
    timestamps = [point[0] for point in series[gauge]]
    assert timestamps == sorted(timestamps)


def test_pressure_axis_labels_use_a_javascript_formatter(config):
    axis_label = _chart_page(config)._gauge_chart.options["yAxis"]["axisLabel"]
    # The ':' prefix is what makes NiceGUI send this as a function, not a string.
    assert ":formatter" in axis_label
    assert "toExponential" in axis_label[":formatter"]
    # Arrow syntax would be mangled by HTML escaping of '>' in the page.
    assert "=>" not in axis_label[":formatter"]
