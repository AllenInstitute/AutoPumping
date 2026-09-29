from time import time


class SafetyMachine:
    def __init__(self, config, pumping_system):
        self._config = config
        self._pumping_system = pumping_system
        self._valve_times = {valve: time() for valve in config.valves}
        self._pump_times = {pump: time() for pump in config.pumps}

    def record_valve_change(self, valve):
        self._valve_times[valve] = time()

    def record_pump_change(self, pump):
        self._pump_times[pump] = time()

    def can_actuate_valve(self, valve, state):
        current_state = self._pumping_system.get_valve_state(valve)
        transition = self._find_transition(
            valve=valve, from_state=current_state, to_state=state
        )
        return self._check_conditions(transition.condition)

    def can_set_pump(self, pump, state):
        current_state = self._pumping_system.get_pump_state(pump)
        transition = self._find_transition(
            pump=pump, from_state=current_state, to_state=state
        )
        return self._check_conditions(transition.condition)

    def can_move_stage(self, position):
        current_position = self._pumping_system.get_stage_position()
        transition = self._find_transition(
            stage=True, from_state=current_position, to_state=position
        )
        return self._check_conditions(transition.condition)

    def _find_transition(self, **kwargs):
        for transition in self._config.transitions:
            if all(getattr(transition, key) == value for key, value in kwargs.items()):
                return transition
        raise ValueError(f"No transition found for {kwargs}")

    def _check_conditions(self, conditions):
        if conditions is None:
            return True
        for gauge, ineq in conditions.gauge.items():
            if not ineq.evaluate(self._pumping_system.get_gauge_pressure(gauge)):
                return False
        if (
            conditions.stage is not None
            and conditions.stage != self._pumping_system.get_stage_position()
        ):
            return False
        for valve, condition in conditions.valve.items():
            if self._pumping_system.get_valve_state(valve) != condition.state:
                return False
            if (
                condition.time is not None
                and time() - self._valve_times[valve] < condition.time
            ):
                return False
        for pump, condition in conditions.pump.items():
            if condition.state is not None and self._pumping_system.get_pump_state(pump) != condition.state:
                return False
            if (
                condition.time is not None
                and time() - self._pump_times[pump] < condition.time
            ):
                return False
            if condition.current is not None and not condition.current.evaluate(
                self._pumping_system.get_pump_current(pump)
            ):
                return False
            if condition.voltage is not None and not condition.voltage.evaluate(
                self._pumping_system.get_pump_voltage(pump)
            ):
                return False
            if condition.flow is not None and not condition.flow.evaluate(self._pumping_system.get_pump_flow(pump)):
                return False
            if condition.speed is not None and not condition.speed.evaluate(self._pumping_system.get_pump_speed(pump)):
                return False
        return True
