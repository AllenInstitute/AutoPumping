from . import AutoPumping
import random


class DummyPumping(AutoPumping):
    def __init__(self, config):
        self._valve_states = {valve: None for valve in config.valves}
        self._pump_states = {pump: None for pump in config.pumps}
        self._stage_position = None
        super().__init__(config)

    def actuate_valve(self, valve, state):
        self._valve_states[valve] = state

    def get_valve_state(self, valve):
        return self._valve_states[valve]

    def set_pump(self, pump, state):
        self._pump_states[pump] = state

    def get_pump_state(self, pump):
        return self._pump_states[pump]

    def get_pump_current(self, pump):
        if pump == "turbo":
            return random.uniform(0.0, 2.0)
        return None

    def get_pump_voltage(self, pump):
        if pump == "turbo":
            return random.uniform(23.0, 25.0)
        return None

    def get_pump_flow(self, pump):
        return None

    def get_pump_speed(self, pump):
        if pump == "turbo":
            return random.uniform(998.0, 1002.0)
        return None

    def get_stage_position(self):
        return self._stage_position

    def move_stage(self, position):
        self._stage_position = position

    def get_gauge_pressure(self, gauge):
        return random.uniform(1e-6, 1e-5)
