from auto_pumping.safety import SafetyMachine
from auto_pumping.config import PumpState, ValveState
from pytest import mark


@mark.parametrize(
    "valve_states, turbo_current, gauge_pressure, VF_open_time, can_open_VT",
    [
        (
            {
                "VT": ValveState.CLOSED,
                "VA": ValveState.CLOSED,
                "VB": ValveState.CLOSED,
                "VF": ValveState.OPEN,
                "VR": ValveState.CLOSED,
                "VE": ValveState.CLOSED,
                "VV1": ValveState.CLOSED,
            },
            0.55,
            0.09,
            11,
            True,
        ),
        (
            {
                "VT": ValveState.CLOSED,
                "VA": ValveState.CLOSED,
                "VB": ValveState.CLOSED,
                "VF": ValveState.OPEN,
                "VR": ValveState.CLOSED,
                "VE": ValveState.CLOSED,
                "VV1": ValveState.CLOSED,
            },
            0.55,
            0.09,
            9,
            False,
        ),
        (
            {
                "VT": ValveState.CLOSED,
                "VA": ValveState.CLOSED,
                "VB": ValveState.CLOSED,
                "VF": ValveState.OPEN,
                "VR": ValveState.CLOSED,
                "VE": ValveState.CLOSED,
                "VV1": ValveState.CLOSED,
            },
            0.65,
            0.09,
            11,
            False,
        ),
        (
            {
                "VT": ValveState.CLOSED,
                "VA": ValveState.CLOSED,
                "VB": ValveState.CLOSED,
                "VF": ValveState.OPEN,
                "VR": ValveState.CLOSED,
                "VE": ValveState.CLOSED,
                "VV1": ValveState.CLOSED,
            },
            0.55,
            1.1,
            11,
            False,
        ),
        (
            {
                "VT": ValveState.CLOSED,
                "VA": ValveState.CLOSED,
                "VB": ValveState.CLOSED,
                "VF": ValveState.CLOSED,
                "VR": ValveState.CLOSED,
                "VE": ValveState.CLOSED,
                "VV1": ValveState.CLOSED,
            },
            0.55,
            0.09,
            11,
            False,
        ),
    ],
)
def test_crossover_VT_open(config, mocker, valve_states, turbo_current, gauge_pressure, VF_open_time, can_open_VT):
    time = mocker.patch("auto_pumping.safety.time", return_value=100)

    mock_pumping_system = mocker.Mock()
    mock_pumping_system.get_valve_state.side_effect = valve_states.get
    mock_pumping_system.get_pump_state.return_value = PumpState.ON
    mock_pumping_system.get_pump_current.side_effect = lambda pump: turbo_current if pump == "turbo" else None
    mock_pumping_system.get_pump_voltage.side_effect = lambda pump: 24 if pump == "turbo" else None
    mock_pumping_system.get_pump_flow.return_value = None
    mock_pumping_system.get_pump_speed.side_effect = lambda pump: 1000 if pump == "turbo" else None
    mock_pumping_system.get_gauge_pressure.return_value = gauge_pressure
    mock_pumping_system.get_stage_position.return_value = "loadlock"

    safety_machine = SafetyMachine(config, mock_pumping_system)

    time.return_value += VF_open_time

    assert safety_machine.can_actuate_valve("VT", "open") == can_open_VT
