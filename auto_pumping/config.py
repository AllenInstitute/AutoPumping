from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from enum import Enum
import yaml


class ValveState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class PumpState(str, Enum):
    ON = "on"
    OFF = "off"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NamedState(StrictModel):
    valves: dict[str, ValveState] = Field({}, description="A mapping of valve names to their states in this named state (not all valves need to be specified)")
    stage: str | None = Field(None, description="Stage position in this named state")
    pumps: dict[str, PumpState] = Field({}, description="A mapping of pump names to their states in this named state (not all pumps need to be specified)")

    def _check_valves(self, valves: list[str]):
        for valve in self.valves.keys():
            if valve not in valves:
                raise ValueError(f"Valve {valve} not in valves list")

    def _check_pumps(self, pumps: list[str]):
        for pump in self.pumps.keys():
            if pump not in pumps:
                raise ValueError(f"Pump {pump} not in pumps list")

    def _check_stage(self, stages: list[str]):
        if self.stage is not None and self.stage not in stages:
            raise ValueError(f"Stage position {self.stage} not in stage position list")


class Inequality(StrictModel):
    LE: float | None = Field(None, description="Upper bound of the inequality (less than or equal to)")
    GE: float | None = Field(None, description="Lower bound of the inequality (greater than or equal to)")

    @model_validator(mode="after")
    def check_inequality(self):
        if self.LE is not None and self.GE is not None and self.LE < self.GE:
            raise ValueError(
                f"LE {self.LE} must be greater than or equal to GE {self.GE}"
            )
        if self.LE is None and self.GE is None:
            raise ValueError("At least one of LE or GE must be defined")
        return self

    def evaluate(self, value: float) -> bool:
        if self.LE is not None and value > self.LE:
            return False
        if self.GE is not None and value < self.GE:
            return False
        return True

    def to_str(self, variable):
        if self.LE is not None and self.GE is not None:
            return f"{self.GE} <= {variable} <= {self.LE}"
        elif self.LE is not None:
            return f"{variable} <= {self.LE}"
        elif self.GE is not None:
            return f"{variable} >= {self.GE}"
        raise ValueError("Inequality must have at least one of LE or GE defined")


class PumpCondition(StrictModel):
    state: PumpState | None = Field(None, description="The required state of the pump for the transition to be valid")
    time: float | None = Field(None, description="Time in seconds that the pump must be in the required state before the transition is valid")
    current: Inequality | None = Field(None, description="Require the pump current draw to be within the range specified by the inequality for the transition to be valid")
    voltage: Inequality | None = Field(None, description="Require the pump voltage to be within the range specified by the inequality for the transition to be valid")
    flow: Inequality | None = Field(None, description="Require the pump flow rate to be within the range specified by the inequality for the transition to be valid")
    speed: Inequality | None = Field(None, description="Require the pump speed to be within the range specified by the inequality for the transition to be valid")

    @model_validator(mode="before")
    def str2model(cls, v):
        if isinstance(v, str):
            return {"state": v}
        return v


class SafetyConditions(StrictModel):
    pump: dict[str, PumpCondition] | None = Field(None, description="A mapping of pump names to conditions under which the transition should be automatically executed to prevent damage to the system")

    def _check_pumps(self, pumps: list[str]):
        if self.pump is not None:
            for pump in self.pump.keys():
                if pump not in pumps:
                    raise ValueError(f"Pump {pump} not in pumps list")


class PressureChange(StrictModel):
    time: float = Field(..., description="Time in minutes that it is expected it will take to reach the specified pressure")
    gauge: str = Field(..., description="Name of the gauge for the pressure change")
    pressure: float = Field(..., description="Expected gauge pressure after the specified time has elapsed")

    @model_validator(mode="before")
    def dict2fields(cls, raw_data):
        transformed_data = {}
        for key, val in raw_data.items():
            if key not in cls.model_fields:
                if isinstance(key, str):
                    transformed_data["gauge"] = key
                    transformed_data["pressure"] = val
                    continue
            transformed_data[key] = val
        return transformed_data

    def _check_gauges(self, gauges: list[str]):
        if self.gauge not in gauges:
            raise ValueError(f"Gauge {self.gauge} not in gauges list")


class ValveCondition(StrictModel):
    state: ValveState = Field(..., description="Required state of the valve")
    time: float | None = Field(None, description="Time in seconds that the valve must be in the required state before the transition is valid")

    @model_validator(mode="before")
    def str2model(cls, v):
        if isinstance(v, str):
            return {"state": v}
        return v


class TransitionConditions(StrictModel):
    gauge: dict[str, Inequality] = Field({}, description="A mapping of gauge names to pressure inequalities that must be satisfied for the transition to be valid")
    pump: dict[str, PumpCondition] = Field({}, description="A mapping of pump names to conditions that must be satisfied for the transition to be valid")
    valve: dict[str, ValveCondition] = Field({}, description="A mapping of valve names to conditions that must be satisfied for the transition to be valid")
    stage: str | None = Field(None, description="Current stage position required for the transition to be valid")

    def _check_valves(self, valves: list[str]):
        if self.valve is not None:
            for valve in self.valve.keys():
                if valve not in valves:
                    raise ValueError(f"Valve {valve} not in valves list")

    def _check_gauges(self, gauges: list[str]):
        if self.gauge is not None:
            for gauge in self.gauge.keys():
                if gauge not in gauges:
                    raise ValueError(f"Gauge {gauge} not in gauges list")

    def _check_pumps(self, pumps: list[str]):
        if self.pump is not None:
            for pump in self.pump.keys():
                if pump not in pumps:
                    raise ValueError(f"Pump {pump} not in pumps list")

    def _check_stage(self, stages: list[str]):
        if self.stage is not None and self.stage not in stages:
            raise ValueError(f"Stage position {self.stage} not in stage position list")


class Transition(StrictModel):
    stage: bool = Field(False, description="Set to true if this transition is a stage movement")
    valve: str | None = Field(None, description="Name of the valve to be actuated in this transition")
    pump: str | None = Field(None, description="Name of the pump to be actuated in this transition")
    from_state: ValveState | PumpState | str = Field(
        ..., validation_alias="from", serialization_alias="from", description="State of the valve, pump, or stage before the transition"
    )
    to_state: ValveState | PumpState | str = Field(
        ..., validation_alias="to", serialization_alias="to", description="State of the valve, pump, or stage after the transition"
    )
    condition: TransitionConditions | None = Field(None, description="Conditions that must be met for this transition to be valid")
    wait: dict[str, Inequality] = Field({}, description="A mapping of gauge names to pressure inequalities that should be satisfied before this transition is executed when automated pumping is enabled")
    safety: SafetyConditions | None = Field(None, description="Safety conditions under which this transition should be automatically executed to prevent damage to the system")
    pressure: list[PressureChange] = Field([], description="A list of pressure changes that are expected if this transition is executed")

    @model_validator(mode="before")
    def pressure2list(cls, v):
        if "pressure" in v and isinstance(v["pressure"], dict):
            v["pressure"] = [v["pressure"]]
        return v

    @model_validator(mode="after")
    def from_to_state2enum(self):
        if self.valve is not None:
            self.from_state = ValveState(self.from_state)
            self.to_state = ValveState(self.to_state)
        if self.pump is not None:
            self.from_state = PumpState(self.from_state)
            self.to_state = PumpState(self.to_state)
        return self

    def _check_valves(self, valves: list[str]):
        if self.valve is not None and self.valve not in valves:
            raise ValueError(f"Valve {self.valve} not in valves list")
        if self.condition is not None:
            self.condition._check_valves(valves)

    def _check_gauges(self, gauges: list[str]):
        if self.condition is not None:
            self.condition._check_gauges(gauges)
        if self.pressure is not None:
            for pressure_change in self.pressure:
                pressure_change._check_gauges(gauges)
        if self.wait is not None:
            for gauge in self.wait.keys():
                if gauge not in gauges:
                    raise ValueError(f"Gauge {gauge} not in gauges list")

    def _check_pumps(self, pumps: list[str]):
        if self.pump is not None and self.pump not in pumps:
            raise ValueError(f"Pump {self.pump} not in pumps list")
        if self.safety is not None:
            self.safety._check_pumps(pumps)
        if self.condition is not None:
            self.condition._check_pumps(pumps)

    def _check_stage(self, stages: list[str]):
        if self.condition is not None:
            self.condition._check_stage(stages)
        if self.stage:
            if self.from_state not in stages:
                raise ValueError(
                    f"Stage position {self.from_state} not in stage position list"
                )
            if self.to_state not in stages:
                raise ValueError(
                    f"Stage position {self.to_state} not in stage position list"
                )


class Confirm(StrictModel):
    default: bool = Field(False, description="Default confirmation requirement for transitions")
    pump: dict[str, PumpState | bool] | None = Field(None, description="Require confirmation before turning pumps on or off, or True to require confirmation for all transitions of a specific pump")
    stage: list[str] | None = Field(None, description="Require confirmation before moving the stage to any of these positions")
    valve: dict[str, ValveState | bool] | None = Field(None, description="Require confirmation before opening or closing valves, or True to require confirmation for all transitions of a specific valve")

    def _check_valves(self, valves: list[str]):
        if self.valve is not None:
            for valve in self.valve.keys():
                if valve not in valves:
                    raise ValueError(f"Valve {valve} not in valves list")

    def _check_pumps(self, pumps: list[str]):
        if self.pump is not None:
            for pump in self.pump.keys():
                if pump not in pumps:
                    raise ValueError(f"Pump {pump} not in pumps list")

    def _check_stage(self, stages: list[str]):
        if self.stage is not None:
            for position in self.stage:
                if position not in stages:
                    raise ValueError(
                        f"Stage position {position} not in stage position list"
                    )


class InitialState(StrictModel):
    valves: dict[str, ValveState] | None = Field(None, description="Initial state of each valve")
    pumps: dict[str, PumpState] | None = Field(None, description="Initial state of each pump")
    stage: str | None = Field(None, description="Initial stage location")
    gauges: dict[str, float] | None = Field(None, description="Initial pressure of each gauge")

    def _check_valves(self, valves: list[str]):
        for valve in valves:
            if self.valves is None or valve not in self.valves.keys():
                raise ValueError(f"Valve {valve} not in initial state")

    def _check_gauges(self, gauges: list[str]):
        for gauge in gauges:
            if self.gauges is None or gauge not in self.gauges.keys():
                raise ValueError(f"Gauge {gauge} not in initial state")

    def _check_pumps(self, pumps: list[str]):
        for pump in pumps:
            if self.pumps is None or pump not in self.pumps.keys():
                raise ValueError(f"Pump {pump} not in initial state")

    def _check_stage(self, stages: list[str]):
        if len(stages) > 0:
            if self.stage is None:
                raise ValueError(f"Stage position not in initial state")
            if self.stage not in stages:
                raise ValueError(
                    f"Initial stage position {self.stage} not in stage position list"
                )


class Config(StrictModel):
    valves: list[str] = Field(..., description="List of all valve names")
    gauges: list[str] = Field(..., description="List of all gauge names")
    pumps: list[str] = Field(..., description="List of all pump names")
    stage: list[str] = Field(..., description="List of all stage positions")
    named_states: dict[str, NamedState] = Field(..., description="A mapping of names of named states to their corresponding state definitions")
    transitions: list[Transition] = Field(..., description="List of all possible transitions")
    confirm: Confirm = Field(..., description="Confirmation requirements for transitions when automated pumping is enabled")
    initial_states: list[InitialState] = Field(..., description="List of initial states to use when building the state graph")

    @field_validator("initial_states")
    def check_initial_states(cls, v):
        if len(v) == 0:
            raise ValueError("At least one initial state must be defined")
        return v

    @model_validator(mode="after")
    def check_valves(self):
        for transition in self.transitions:
            transition._check_valves(self.valves)
        self.confirm._check_valves(self.valves)
        for state in self.named_states.values():
            state._check_valves(self.valves)
        for state in self.initial_states:
            state._check_valves(self.valves)
        return self

    @model_validator(mode="after")
    def check_gauges(self):
        for transition in self.transitions:
            transition._check_gauges(self.gauges)
        for state in self.initial_states:
            state._check_gauges(self.gauges)
        return self

    @model_validator(mode="after")
    def check_pumps(self):
        for transition in self.transitions:
            transition._check_pumps(self.pumps)
        self.confirm._check_pumps(self.pumps)
        for state in self.named_states.values():
            state._check_pumps(self.pumps)
        for state in self.initial_states:
            state._check_pumps(self.pumps)
        return self

    @model_validator(mode="after")
    def check_stage(self):
        for transition in self.transitions:
            transition._check_stage(self.stage)
        self.confirm._check_stage(self.stage)
        for state in self.named_states.values():
            state._check_stage(self.stage)
        for state in self.initial_states:
            state._check_stage(self.stage)
        return self

    @classmethod
    def load_file(cls, path):
        with open(path) as f:
            return cls.load_yaml(f.read())

    @classmethod
    def load_yaml(cls, yaml_str):
        data = yaml.safe_load(yaml_str)
        return cls(**data)
