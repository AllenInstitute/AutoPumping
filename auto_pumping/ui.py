from nicegui import background_tasks, run, ui

from .config import PumpState, ValveState

POLL_INTERVAL = 1.0


class UI:
    def __init__(self, config, pumping_system, diagram=None):
        self._pumping_system = pumping_system
        self._config = config

        @ui.page("/")
        def _index():
            # Each connecting client gets its own `_Page` instance so that
            # widget references, callbacks, and the polling timer are never
            # shared between browser tabs/reconnections.
            _Page(config, pumping_system, diagram).build()

    def run(self):
        ui.run(reload=False)


class _Page:
    def __init__(self, config, pumping_system, diagram):
        self._config = config
        self._pumping_system = pumping_system
        self._diagram = diagram
        self._auto_switch = None
        self._valve_switches = {}
        self._pump_switches = {}
        self._pump_labels = {}
        self._named_state_buttons = {}
        self._stage_buttons = {}
        self._gauge_labels = {}

    def build(self):
        config = self._config
        diagram = self._diagram
        with ui.row().classes('w-full'):
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
        with ui.row().classes('w-full'):
            with ui.column().classes('w-fit item-stretch'):
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
            with ui.column().classes('grow'):
                if config.stage:
                    with ui.card().tight().classes('w-full'):
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
                    with ui.card().tight().classes('w-full'):
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
                with ui.row():
                    self._build_plan()
                    if diagram:
                        ui.image(diagram)
        ui.timer(POLL_INTERVAL, self._refresh)

    def _on_auto_mode_change(self):
        background_tasks.create(self._refresh())

    async def _on_valve_change(self, e, valve):
        state = ValveState.OPEN if e.value else ValveState.CLOSED
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, valves={valve: state})
            self._build_plan.refresh()
        else:
            await run.io_bound(self._pumping_system._actuate_valve, valve, state)
        await self._refresh()

    async def _on_pump_change(self, e, pump):
        state = PumpState.ON if e.value else PumpState.OFF
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, pumps={pump: state})
            self._build_plan.refresh()
        else:
            await run.io_bound(self._pumping_system._set_pump, pump, state)
        await self._refresh()

    async def _on_named_state_click(self, name):
        await run.io_bound(self._pumping_system.auto, name=name)
        self._build_plan.refresh()
        await self._refresh()

    async def _on_stage_click(self, position):
        if self._auto_switch.value:
            await run.io_bound(self._pumping_system.auto, stage=position)
            self._build_plan.refresh()
        else:
            await run.io_bound(self._pumping_system._move_stage, position)
        await self._refresh()

    def _on_abort_plan(self):
        self._pumping_system.cancel()
        self._build_plan.refresh()
        background_tasks.create(self._refresh())

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
        return {
            "valves": valves,
            "pumps": pumps,
            "stage": stage,
            "stages": stages,
            "gauges": gauges,
            "plan_active": bool(self._pumping_system.plan),
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

    @ui.refreshable
    def _build_plan(self):
        if self._pumping_system.plan:
            with ui.card().tight():
                with ui.card_section():
                    ui.label("Plan")
                ui.separator()
                with ui.card_section():
                    with ui.timeline(side="right"):
                        for step in self._pumping_system.plan:
                            if step.wait:
                                conditions = [
                                    ineq.to_str(gauge)
                                    for gauge, ineq in step.wait.gauges.items()
                                ]
                                ui.timeline_entry(f"Wait for {self.oxford_comma(conditions)}")
                            if step.valve:
                                ui.timeline_entry(f"{step.state.value} {step.valve}")
                            if step.pump:
                                ui.timeline_entry(f"Turn {step.state.value} {step.pump} pump")
                            if step.stage:
                                ui.timeline_entry(f"Move stage to {step.stage}")
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
