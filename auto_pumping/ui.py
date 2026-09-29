from collections import deque
from threading import Lock
from time import time

from nicegui import background_tasks, run, ui

from .config import PumpState, ValveState

POLL_INTERVAL = 1.0
# How much gauge history the live plot keeps, in seconds.
HISTORY_WINDOW = 600.0


class GaugeHistory:
    """A bounded, thread-safe history of gauge readings shared by all pages.

    Sampling is driven by each page's poll, so a browser tab opened later sees
    the history collected so far instead of starting from a blank plot.
    """

    def __init__(self, gauges, window=HISTORY_WINDOW, min_interval=POLL_INTERVAL / 2):
        self._gauges = list(gauges)
        self._window = window
        self._min_interval = min_interval
        self._samples = deque()
        self._last_sample = None
        # `record` is called from `run.io_bound` worker threads, one per page.
        self._lock = Lock()

    def record(self, readings, now=None):
        """Store a set of readings, ignoring samples that arrive too close together.

        Every open tab polls independently, so without the interval check two
        tabs would record duplicate samples for the same instant.
        """
        now = time() if now is None else now
        with self._lock:
            if (
                self._last_sample is not None
                and now - self._last_sample < self._min_interval
            ):
                return False
            self._last_sample = now
            self._samples.append((now, dict(readings)))
            cutoff = now - self._window
            while self._samples and self._samples[0][0] < cutoff:
                self._samples.popleft()
            return True

    def series(self):
        """Return `{gauge: [[timestamp_ms, value], ...]}` ready for ECharts.

        Timestamps are milliseconds because that is what an ECharts `time` axis
        expects. Readings that a log axis cannot show (<= 0, or missing) become
        `None` so the line is drawn with a gap rather than implying a reading
        that was never taken.
        """
        with self._lock:
            samples = list(self._samples)
        series = {gauge: [] for gauge in self._gauges}
        for timestamp, readings in samples:
            milliseconds = int(timestamp * 1000)
            for gauge in self._gauges:
                value = readings.get(gauge)
                if value is None or value <= 0:
                    value = None
                series[gauge].append([milliseconds, value])
        return series


class UI:
    def __init__(self, config, pumping_system, diagram=None):
        self._pumping_system = pumping_system
        self._config = config
        history = GaugeHistory(config.gauges)

        @ui.page("/")
        def _index():
            # Each connecting client gets its own `_Page` instance so that
            # widget references, callbacks, and the polling timer are never
            # shared between browser tabs/reconnections.
            _Page(config, pumping_system, diagram, history).build()

    def run(self):
        ui.run(reload=False)


class _Page:
    def __init__(self, config, pumping_system, diagram, history=None):
        self._config = config
        self._pumping_system = pumping_system
        self._diagram = diagram
        self._history = history if history is not None else GaugeHistory(config.gauges)
        self._gauge_chart = None
        self._auto_switch = None
        self._valve_switches = {}
        self._pump_switches = {}
        self._pump_labels = {}
        self._named_state_buttons = {}
        self._stage_buttons = {}
        self._gauge_labels = {}
        self._confirm_dialog = None
        self._confirm_content = None
        self._shown_confirmation = None
        self._plan_description = None
        self._unknown_dialog = None
        self._unknown_state = None
        self._unknown_valve_switches = {}
        self._unknown_pump_switches = {}
        self._unknown_stage_toggle = None
        self._unknown_submit_button = None

    def build(self):
        config = self._config
        diagram = self._diagram
        # A flex child can only fill leftover vertical space if every ancestor
        # has a definite height, so pin the page content to the viewport.
        ui.query(".nicegui-content").classes("h-screen flex-nowrap gap-2 p-2")
        with ui.row().classes('w-full shrink-0'):
            self._auto_switch = ui.switch(
                "Auto Mode", value=True, on_change=lambda: self._on_auto_mode_change()
            ).classes('flex-shrink-0')
            if config.named_states:
                with ui.button_group().props('outline').classes('flex-1 ml-4 bg-transparent'):
                    for named_state in config.named_states:
                        button = ui.button(
                            named_state,
                            on_click=lambda _, name=named_state: self._on_named_state_click(name),
                        ).props('outline color=primary').classes('flex-1')
                        self._named_state_buttons[named_state] = button
        with ui.row().classes('w-full grow min-h-0 items-stretch flex-nowrap'):
            with ui.column().classes('w-fit item-stretch shrink min-w-0 self-start'):
                if config.gauges:
                    with ui.card().tight().classes('w-full'):
                        with ui.card_section():
                            ui.label("Guages")
                        ui.separator()
                        with ui.card_section():
                            for gauge in config.gauges:
                                self._gauge_labels[gauge] = ui.label()
                if config.valves:
                    with ui.card().tight().classes('w-full'):
                        with ui.card_section():
                            ui.label("Valves")
                        ui.separator()
                        with ui.card_section():
                            with ui.grid(columns=4):
                                for valve in config.valves:
                                    ui.label(valve)
                                    ui.label("Closed")
                                    switch = ui.switch(
                                        value=None,
                                        on_change=lambda e, valve=valve: self._on_valve_change(e, valve),
                                    )
                                    ui.label("Open")
                                    self._valve_switches[valve] = switch
            self._build_plan()
            with ui.column().classes('grow min-h-0 min-w-0 h-full flex-nowrap'):
                if config.stage:
                    with ui.card().tight().classes('w-full shrink-0'):
                        with ui.card_section():
                            ui.label("Stage")
                        ui.separator()
                        with ui.card_section():
                            with ui.button_group().props('outline').classes('flex-1 ml-4 bg-transparent'):
                                for position in config.stage:
                                    button = ui.button(
                                        position,
                                        on_click=lambda _, position=position: self._on_stage_click(position),
                                    ).props('outline color=primary').classes('flex-1')
                                    self._stage_buttons[position] = button
                for pump in config.pumps:
                    with ui.card().tight().classes('w-full shrink-0'):
                        with ui.card_section():
                            ui.label(pump)
                        ui.separator()
                        with ui.card_section():
                            with ui.row():
                                ui.label("Off")
                                switch = ui.switch(
                                    on_change=lambda e, pump=pump: self._on_pump_change(e, pump),
                                )
                                ui.label("On")
                                current_label = ui.label()
                                voltage_label = ui.label()
                                flow_label = ui.label()
                                speed_label = ui.label()
                                self._pump_switches[pump] = switch
                                self._pump_labels[pump] = {
                                    "current": current_label,
                                    "voltage": voltage_label,
                                    "flow": flow_label,
                                    "speed": speed_label,
                                }
                with ui.row().classes('w-full grow min-h-0 min-w-0 flex-nowrap'):
                    if config.gauges:
                        with ui.card().tight().classes('grow min-h-0 min-w-0 h-full p-2'):
                            self._gauge_chart = ui.echart(
                                self._gauge_chart_options()
                            ).classes('w-full h-full')
                    if diagram:
                        with ui.card().tight().classes('h-full w-auto shrink-0 p-2'):
                            # A q-img has no intrinsic width, so `w-auto` would
                            # collapse it; a native <img> derives its width from
                            # the aspect ratio once the height is pinned.
                            ui.image(diagram).props('tag=img').classes(
                                'h-full w-auto object-contain'
                            )
        self._build_confirm_dialog()
        self._build_unknown_dialog()
        ui.timer(POLL_INTERVAL, self._refresh)
        # The timer's first tick is a full interval away, so refresh once now to
        # avoid the page rendering blank (and the unknown-state prompt appearing
        # late) on load.
        background_tasks.create(self._refresh())

    def _gauge_chart_options(self):
        gauges = list(self._config.gauges)
        return {
            "tooltip": {"trigger": "axis"},
            # A single gauge labels itself via the axis name; a legend only
            # earns its space once there is more than one line to tell apart.
            "legend": {"show": len(gauges) > 1, "data": gauges},
            "grid": {
                "left": 70,
                "right": 20,
                "top": 40 if len(gauges) > 1 else 20,
                "bottom": 40,
            },
            "xAxis": {"type": "time"},
            "yAxis": {
                "type": "log",
                "name": "Pressure",
                # A leading ':' marks the value as JavaScript, so ECharts gets a
                # real formatter function. Vacuum pressures span many decades,
                # so plain decimals would be unreadable. Avoid arrow syntax:
                # the options are embedded in HTML, where '>' gets escaped.
                "axisLabel": {
                    ":formatter": "function (value) { return Number(value).toExponential(0); }"
                },
            },
            "series": [
                {
                    "name": gauge,
                    "type": "line",
                    # 600 points per line; markers would just be noise.
                    "showSymbol": False,
                    "data": [],
                }
                for gauge in gauges
            ],
        }

    def _apply_gauge_series(self, series):
        if self._gauge_chart is None:
            return
        changed = False
        for entry in self._gauge_chart.options["series"]:
            data = series.get(entry["name"], [])
            if entry["data"] != data:
                entry["data"] = data
                changed = True
        if changed:
            self._gauge_chart.update()

    @staticmethod
    def _get_unknown_state(config, pumping_system):
        """Return the valves, pumps and stage whose state the system cannot report."""
        return {
            "valves": [
                valve
                for valve in config.valves
                if pumping_system.get_valve_state(valve) is None
            ],
            "pumps": [
                pump
                for pump in config.pumps
                if pumping_system.get_pump_state(pump) is None
            ],
            "stage": bool(config.stage)
            and pumping_system.get_stage_position() not in config.stage,
        }

    @staticmethod
    def _has_unknown_state(unknown):
        return bool(unknown["valves"] or unknown["pumps"] or unknown["stage"])

    def _build_unknown_dialog(self):
        # Non-escapable: the rest of the UI is unusable until the operator tells
        # us what the hardware is actually doing.
        with ui.dialog().props(
            "persistent no-esc-dismiss no-backdrop-dismiss"
        ) as dialog, ui.card():
            self._build_unknown_content()
        self._unknown_dialog = dialog

    @ui.refreshable
    def _build_unknown_content(self):
        unknown = self._unknown_state or {"valves": [], "pumps": [], "stage": False}
        self._unknown_valve_switches = {}
        self._unknown_pump_switches = {}
        self._unknown_stage_toggle = None
        ui.label("Specify current state").classes("text-lg font-bold")
        ui.label(
            "The system cannot determine the state of the following items. "
            "Set each one to its current state to continue."
        ).classes("text-sm")
        if unknown["valves"]:
            ui.label("Valves").classes("font-bold mt-2")
            with ui.grid(columns=4):
                for valve in unknown["valves"]:
                    ui.label(valve)
                    ui.label("Closed")
                    # `value=None` renders Quasar's indeterminate position, so an
                    # untouched switch is visually distinct from one set to closed.
                    self._unknown_valve_switches[valve] = ui.switch(
                        value=None, on_change=self._update_unknown_submit_state
                    )
                    ui.label("Open")
        if unknown["pumps"]:
            ui.label("Pumps").classes("font-bold mt-2")
            with ui.grid(columns=4):
                for pump in unknown["pumps"]:
                    ui.label(pump)
                    ui.label("Off")
                    self._unknown_pump_switches[pump] = ui.switch(
                        value=None, on_change=self._update_unknown_submit_state
                    )
                    ui.label("On")
        if unknown["stage"]:
            ui.label("Stage").classes("font-bold mt-2")
            self._unknown_stage_toggle = ui.toggle(
                list(self._config.stage),
                value=None,
                on_change=self._update_unknown_submit_state,
            )
        with ui.card_actions().classes("justify-end w-full"):
            self._unknown_submit_button = ui.button(
                "Submit", on_click=self._on_unknown_submit
            )
        self._update_unknown_submit_state()

    def _unknown_selection_complete(self):
        if any(
            switch.value is None for switch in self._unknown_valve_switches.values()
        ):
            return False
        if any(switch.value is None for switch in self._unknown_pump_switches.values()):
            return False
        if self._unknown_stage_toggle is not None and (
            self._unknown_stage_toggle.value is None
        ):
            return False
        return True

    def _update_unknown_submit_state(self):
        if self._unknown_submit_button is not None:
            # Declaring a wrong state could drive an unsafe action, so require an
            # explicit choice for every item rather than defaulting.
            self._unknown_submit_button.enabled = self._unknown_selection_complete()

    async def _on_unknown_submit(self):
        if not self._unknown_selection_complete():
            return
        valves = {
            valve: ValveState.OPEN if switch.value else ValveState.CLOSED
            for valve, switch in self._unknown_valve_switches.items()
        }
        pumps = {
            pump: PumpState.ON if switch.value else PumpState.OFF
            for pump, switch in self._unknown_pump_switches.items()
        }
        stage = (
            self._unknown_stage_toggle.value
            if self._unknown_stage_toggle is not None
            else None
        )
        await run.io_bound(self._declare_state, valves, pumps, stage)
        self._unknown_state = None
        self._unknown_dialog.close()
        await self._refresh()

    def _declare_state(self, valves, pumps, stage):
        """Record the operator-declared state in the pumping system.

        Uses the raw setters rather than the safety-checked wrappers: the
        operator is describing the state the hardware is already in, not
        commanding a change, and the safety check would need the very state
        being declared. The safety machine's change timestamps are deliberately
        left alone since nothing physically moved.
        """
        for valve, state in valves.items():
            self._pumping_system.actuate_valve(valve, state)
        for pump, state in pumps.items():
            self._pumping_system.set_pump(pump, state)
        if stage is not None:
            self._pumping_system.move_stage(stage)

    def _build_confirm_dialog(self):
        # A safety prompt must get an explicit answer, so the dialog cannot be
        # dismissed by clicking the backdrop or pressing Escape.
        with ui.dialog().props("persistent") as dialog, ui.card():
            ui.label("Confirm action").classes("text-lg font-bold")
            self._confirm_content = ui.label()
            with ui.card_actions().classes("justify-end w-full"):
                ui.button("Cancel", on_click=lambda: self._on_confirm_response(False)).props(
                    "outline color=primary"
                )
                ui.button("Confirm", on_click=lambda: self._on_confirm_response(True))
        self._confirm_dialog = dialog

    def _on_confirm_response(self, approved):
        request = self._shown_confirmation
        self._shown_confirmation = None
        self._confirm_dialog.close()
        if request is not None:
            request.resolve(approved)
        background_tasks.create(self._refresh())

    def _on_auto_mode_change(self):
        background_tasks.create(self._refresh())

    async def _on_valve_change(self, e, valve):
        state = ValveState.OPEN if e.value else ValveState.CLOSED
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, valves={valve: state})
        else:
            await run.io_bound(self._pumping_system._actuate_valve, valve, state)
        await self._refresh()

    async def _on_pump_change(self, e, pump):
        state = PumpState.ON if e.value else PumpState.OFF
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, pumps={pump: state})
        else:
            await run.io_bound(self._pumping_system._set_pump, pump, state)
        await self._refresh()

    async def _on_named_state_click(self, name):
        await run.io_bound(self._pumping_system.auto, name=name)
        await self._refresh()

    async def _on_stage_click(self, position):
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, stage=position)
        else:
            await run.io_bound(self._pumping_system._move_stage, position)
        await self._refresh()

    async def _on_abort_plan(self):
        # `cancel()` joins the executor thread, which can take up to a second,
        # so keep it off the UI event loop.
        await run.io_bound(self._pumping_system.cancel)
        await self._refresh()

    def _get_snapshot(self):
        safety = self._pumping_system._safety_machine
        valves = {}
        for valve in self._config.valves:
            current = self._pumping_system.get_valve_state(valve)
            valves[valve] = {"state": current, "safe": self._can_flip_valve(safety, valve, current)}
        pumps = {}
        for pump in self._config.pumps:
            current = self._pumping_system.get_pump_state(pump)
            pumps[pump] = {
                "state": current,
                "safe": self._can_flip_pump(safety, pump, current),
                "current": self._pumping_system.get_pump_current(pump),
                "voltage": self._pumping_system.get_pump_voltage(pump),
                "flow": self._pumping_system.get_pump_flow(pump),
                "speed": self._pumping_system.get_pump_speed(pump),
            }
        stage = self._pumping_system.get_stage_position()
        stages = {
            position: self._can_move_stage(safety, position)
            for position in self._config.stage
        }
        gauges = {
            gauge: self._pumping_system.get_gauge_pressure(gauge)
            for gauge in self._config.gauges
        }
        plan = self._pumping_system.plan
        # Recording and building the series here keeps both off the event loop,
        # since `_get_snapshot` already runs via `run.io_bound`.
        self._history.record(gauges)
        return {
            "valves": valves,
            "pumps": pumps,
            "stage": stage,
            "stages": stages,
            "gauges": gauges,
            "gauge_series": self._history.series(),
            "plan_active": bool(plan),
            "plan_description": [self._describe_step(step) for step in plan],
            "pending_confirmation": self._pumping_system._plan_executor.pending_confirmation,
            "unknown": self._get_unknown_state(self._config, self._pumping_system),
        }

    @staticmethod
    def _can_flip_valve(safety, valve, current):
        if current is None:
            return False
        target = ValveState.CLOSED if current == ValveState.OPEN else ValveState.OPEN
        try:
            return safety.can_actuate_valve(valve, target)
        except ValueError:
            return False

    @staticmethod
    def _can_flip_pump(safety, pump, current):
        if current is None:
            return False
        target = PumpState.OFF if current == PumpState.ON else PumpState.ON
        try:
            return safety.can_set_pump(pump, target)
        except ValueError:
            return False

    @staticmethod
    def _can_move_stage(safety, position):
        try:
            return safety.can_move_stage(position)
        except ValueError:
            return False

    async def _refresh(self):
        snapshot = await run.io_bound(self._get_snapshot)
        self._apply_snapshot(snapshot)

    @staticmethod
    def _set_value_silently(switch, value):
        # Setting `.value` normally fires the registered change handlers (even for
        # programmatic updates), which would re-trigger actuation from feedback
        # polling. Since those handlers may be async and get scheduled as a
        # background task, a simple before/after boolean flag can't reliably guard
        # against them (the task may run after the flag is reset). Instead, detach
        # the handlers for the duration of the assignment so no event is ever
        # scheduled.
        handlers = switch._change_handlers
        switch._change_handlers = []
        try:
            switch.value = value
        finally:
            switch._change_handlers = handlers

    @staticmethod
    def _set_button_active(button, active):
        # Active buttons are solid blue; inactive ones are inverted, i.e. a white
        # background with a blue outline and blue text.
        if active:
            button.props("color=primary", remove="outline")
        else:
            button.props("outline color=primary")

    def _apply_snapshot(self, snapshot):
        auto_mode = self._auto_switch.value
        plan_active = snapshot["plan_active"]
        for valve, switch in self._valve_switches.items():
            data = snapshot["valves"][valve]
            self._set_value_silently(switch, data["state"] == ValveState.OPEN)
            switch.enabled = not plan_active and (auto_mode or data["safe"])
        for pump, switch in self._pump_switches.items():
            data = snapshot["pumps"][pump]
            self._set_value_silently(switch, data["state"] == PumpState.ON)
            switch.enabled = not plan_active and (auto_mode or data["safe"])
            labels = self._pump_labels[pump]
            labels["current"].text = (
                f"Current: {data['current']:.2f}" if data["current"] is not None else ""
            )
            labels["voltage"].text = (
                f"Voltage: {data['voltage']:.2f}" if data["voltage"] is not None else ""
            )
            labels["flow"].text = (
                f"Flow: {data['flow']:.2f}" if data["flow"] is not None else ""
            )
            labels["speed"].text = (
                f"Speed: {data['speed']:.2f}" if data["speed"] is not None else ""
            )
        for position, button in self._stage_buttons.items():
            button.enabled = not plan_active and (auto_mode or snapshot["stages"][position])
            self._set_button_active(button, position == snapshot["stage"])
        for name, button in self._named_state_buttons.items():
            button.enabled = not plan_active and auto_mode
            self._set_button_active(button, self._is_current_named_state(name, snapshot))
        for gauge, label in self._gauge_labels.items():
            label.text = f"{gauge}: {snapshot['gauges'][gauge]:.2e}"
        self._apply_gauge_series(snapshot["gauge_series"])
        self._sync_plan_card(snapshot)
        self._sync_unknown_dialog(snapshot)
        self._sync_confirm_dialog(snapshot)

    def _sync_unknown_dialog(self, snapshot):
        unknown = snapshot["unknown"]
        if not self._has_unknown_state(unknown):
            if self._unknown_state is not None:
                # Resolved here or in another tab.
                self._unknown_state = None
                self._unknown_dialog.close()
            return
        if unknown == self._unknown_state:
            # Unchanged: leave the open dialog alone so partly-entered input
            # is not discarded on every poll tick.
            return
        self._unknown_state = unknown
        self._build_unknown_content.refresh()
        self._unknown_dialog.open()

    def _sync_plan_card(self, snapshot):
        # The plan card is only rebuilt when the steps actually change, so the
        # timeline stays current as the executor works through the plan.
        if snapshot["plan_description"] != self._plan_description:
            self._plan_description = snapshot["plan_description"]
            self._build_plan.refresh()

    def _sync_confirm_dialog(self, snapshot):
        request = snapshot["pending_confirmation"]
        if self._has_unknown_state(snapshot["unknown"]):
            # Never stack two persistent dialogs; the unknown-state prompt takes
            # precedence and a plan cannot meaningfully run from an unknown state.
            if self._shown_confirmation is not None:
                self._shown_confirmation = None
                self._confirm_dialog.close()
            return
        if request is self._shown_confirmation:
            return
        if request is None:
            # Answered in another tab, or the plan was cancelled.
            self._shown_confirmation = None
            self._confirm_dialog.close()
            return
        self._shown_confirmation = request
        self._confirm_content.text = self._describe_step(request.step)
        self._confirm_dialog.open()

    def _is_current_named_state(self, name, snapshot):
        named_state = self._config.named_states[name]
        for valve, state in named_state.valves.items():
            if snapshot["valves"][valve]["state"] != state:
                return False
        for pump, state in named_state.pumps.items():
            if snapshot["pumps"][pump]["state"] != state:
                return False
        if named_state.stage is not None and snapshot["stage"] != named_state.stage:
            return False
        return True

    @staticmethod
    def _describe_step(step):
        action_verbs = {
            ValveState.CLOSED: "close",
        }
        if step.wait:
            conditions = [ineq.to_str(gauge) for gauge, ineq in step.wait.items()]
            return f"Wait for {_Page.oxford_comma(conditions)}"
        if step.valve:
            return f"{action_verbs.get(step.state, step.state.value).capitalize()} {step.valve}"
        if step.pump:
            return f"Turn {step.state.value} {step.pump} pump"
        if step.stage:
            return f"Move stage to {step.stage}"
        return ""

    @ui.refreshable
    def _build_plan(self):
        if self._pumping_system.plan:
            # `self-start` keeps the card from being stretched to full height by
            # the row's `items-stretch`. It must stay shrinkable and capped,
            # though: step descriptions are long sentences, so a `shrink-0` card
            # would size to its max-content width and push the page off-screen.
            with ui.card().tight().classes('self-start shrink min-w-0 max-w-xs'):
                with ui.card_section():
                    ui.label("Plan")
                ui.separator()
                with ui.card_section():
                    with ui.timeline(side="right"):
                        for step in self._pumping_system.plan:
                            ui.timeline_entry(self._describe_step(step))
                ui.separator()
                with ui.card_actions():
                    ui.button("Abort Plan", on_click=self._on_abort_plan)

    @staticmethod
    def oxford_comma(items):
        if len(items) == 0:
            return ""
        elif len(items) == 1:
            return items[0]
        elif len(items) == 2:
            return f"{items[0]} and {items[1]}"
        else:
            return f"{', '.join(items[:-1])}, and {items[-1]}"
