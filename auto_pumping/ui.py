from nicegui import ui


class UI:
    def __init__(self, config, pumping_system, diagram=None):
        self._pumping_system = pumping_system

        @ui.page("/")
        def _index():
            self._build_ui(config, diagram)

    def _build_ui(self, config, diagram):
        with ui.row().classes('w-full'):
            ui.switch("Auto Mode", value=True).classes('flex-shrink-0')
            if config.named_states:
                with ui.button_group().classes('flex-1 ml-4'):
                    for named_state in config.named_states:
                        ui.button(named_state).classes('flex-1')
        with ui.row().classes('w-full'):
            with ui.column().classes('w-fit item-stretch'):
                if config.gauges:
                    with ui.card().tight().classes('w-full'):
                        with ui.card_section():
                            ui.label("Guages")
                        ui.separator()
                        with ui.card_section():
                            for gauge in config.gauges:
                                ui.label()
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
                                    ui.switch(value=None)
                                    ui.label("Open")
            with ui.column().classes('grow'):
                if config.stage:
                    with ui.card().tight().classes('w-full'):
                        with ui.card_section():
                            ui.label("Stage")
                        ui.separator()
                        with ui.card_section():
                            with ui.button_group().classes('flex-1 ml-4'):
                                for position in config.stage:
                                    ui.button(position).classes('flex-1')
                for pump in config.pumps:
                    with ui.card().tight().classes('w-full'):
                        with ui.card_section():
                            ui.label(pump)
                        ui.separator()
                        with ui.card_section():
                            with ui.row():
                                ui.label("Off")
                                ui.switch()
                                ui.label("On")
                                ui.label()
                                ui.label()
                                ui.label()
                                ui.label()
                with ui.row():
                    self._build_plan()
                    if diagram:
                        ui.image(diagram)

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
                    ui.button("Abort Plan")

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

    def run(self):
        ui.run(reload=False)
