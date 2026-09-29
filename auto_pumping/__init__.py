from abc import ABC, abstractmethod
from .safety import SafetyMachine
from .graph import PumpingGraph
from .config import Config
from .executor import PlanExecutor
from .ui import UI


class AutoPumping(ABC):
    CONFIG_CLASS = Config
    DIAGRAM_PATH = None

    def __init__(self, config, ui=True):
        self._config = config
        self._safety_machine = SafetyMachine(config, self)
        self._pumping_graph = PumpingGraph(config)
        self._plan_executor = PlanExecutor(self)
        self._ui = None
        if ui:
            self._ui = UI(config, self, diagram=self.DIAGRAM_PATH)

    def run(self):
        if self._ui is None:
            raise RuntimeError("UI is not enabled for this instance.")
        self._ui.run()

    def auto(self, **goal_state_data):
        self._plan_executor.execute_plan(
            list(self._pumping_graph.make_plan(self._get_state(), **goal_state_data))
        )

    def cancel(self):
        self._plan_executor.cancel()

    @property
    def plan(self):
        return self._plan_executor.plan

    def _get_state(self):
        return {
            "valves": {
                valve: self.get_valve_state(valve) for valve in self._config.valves
            },
            "pumps": {pump: self.get_pump_state(pump) for pump in self._config.pumps},
            "stage": self.get_stage_position(),
            "gauges": {gauge: self._pumping_graph._get_gauge_range(gauge, self.get_gauge_pressure(gauge)) for gauge in self._config.gauges},
        }

    @classmethod
    def from_config_file(cls, config_file):
        config = cls.CONFIG_CLASS.load_file(config_file)
        return cls(config)

    @abstractmethod
    def get_valve_state(self, valve):
        pass

    def _actuate_valve(self, valve, state):
        if self._safety_machine.can_actuate_valve(valve, state):
            self.actuate_valve(valve, state)
            self._safety_machine.record_valve_change(valve)
            return True
        return False

    @abstractmethod
    def actuate_valve(self, valve, state):
        pass

    @abstractmethod
    def get_pump_state(self, pump):
        pass

    def _set_pump(self, pump, state):
        if self._safety_machine.can_set_pump(pump, state):
            self.set_pump(pump, state)
            self._safety_machine.record_pump_change(pump)
            return True
        return False

    @abstractmethod
    def set_pump(self, pump, state):
        pass

    @abstractmethod
    def get_pump_current(self, pump):
        pass

    @abstractmethod
    def get_pump_voltage(self, pump):
        pass

    @abstractmethod
    def get_pump_flow(self, pump):
        pass

    @abstractmethod
    def get_pump_speed(self, pump):
        pass

    @abstractmethod
    def get_gauge_pressure(self, gauge):
        pass

    @abstractmethod
    def get_stage_position(self):
        pass

    def _move_stage(self, position):
        if self._safety_machine.can_move_stage(position):
            self.move_stage(position)
            return True
        return False

    @abstractmethod
    def move_stage(self, position):
        pass
