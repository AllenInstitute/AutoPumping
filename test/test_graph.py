from auto_pumping.graph import PumpingGraph, NodeModel, PlanStep
from pytest import mark, fixture
from math import inf
from networkx import DiGraph
from auto_pumping.config import Transition, Inequality


@fixture(scope="module")
def graph(config):
    return PumpingGraph(config)


@mark.parametrize(
    "state, expected_name",
    [
        (
            {
                "valves": {
                    "VA": "open",
                    "VB": "closed",
                    "VE": "open",
                    "VT": "open",
                    "VF": "open",
                    "VV1": "closed",
                    "VR": "closed",
                },
                "pumps": {
                    "roughing": "on",
                    "turbo": "on",
                },
                "stage": "microscope",
            },
            "imaging",
        ),
        (
            {
                "valves": {
                    "VA": "closed",
                    "VB": "closed",
                    "VE": "closed",
                    "VT": "closed",
                    "VF": "closed",
                    "VV1": "closed",
                    "VR": "closed",
                },
                "pumps": {
                    "roughing": "on",
                    "turbo": "on",
                },
                "stage": "loadlock",
            },
            None,
        ),
        (
            {
                "valves": {
                    "VA": "closed",
                    "VB": "closed",
                    "VE": "closed",
                    "VT": "closed",
                    "VF": "open",
                    "VV1": "open",
                    "VR": "closed",
                },
                "pumps": {
                    "roughing": "on",
                    "turbo": "on",
                },
                "stage": "loadlock",
            },
            "venting",
        ),
    ],
)
def test_get_state_name(config, state, expected_name):
    assert (
        PumpingGraph._get_state_name(NodeModel(**state), config.named_states)
        == expected_name
    )


def test_discretize_gauge_ranges(config):
    gauge_ranges = PumpingGraph._discretize_gauge_ranges(
        config.gauges, config.transitions
    )
    assert gauge_ranges == {
        "PLL": [(0, 9e-6), (9e-6, 4.5e-5), (4.5e-5, 0.1), (0.1, 430), (430, inf)]
    }


def test_add_initial_states(config):
    class TestPumpingGraph(PumpingGraph):
        def __init__(self, config):
            self._gauge_ranges = self._discretize_gauge_ranges(
                config.gauges, config.transitions
            )
            self._graph = DiGraph()

    graph = TestPumpingGraph(config)
    graph._add_initial_states(config.initial_states[:1], config.named_states)
    assert len(graph._graph.nodes) == 1
    node_id = next(iter(graph._graph.nodes))
    node_data = graph._graph.nodes[node_id]
    assert node_data["name"] == "imaging"
    assert node_data["valves"] == {
        "VA": "open",
        "VB": "closed",
        "VE": "open",
        "VT": "open",
        "VF": "open",
        "VV1": "closed",
        "VR": "closed",
    }
    assert node_data["pumps"] == {"roughing": "on", "turbo": "on"}
    assert node_data["stage"] == "microscope"


@mark.parametrize(
    "pressure, expected_range",
    [
        (1e-6, (0, 9e-6)),
        (9e-6, (9e-6, 4.5e-5)),
        (4.5e-5, (4.5e-5, 0.1)),
        (0.1, (0.1, 430)),
        (450, (430, inf)),
    ],
)
def test_get_gauge_range(config, pressure, expected_range):
    class TestPumpingGraph(PumpingGraph):
        def __init__(self, config):
            self._gauge_ranges = self._discretize_gauge_ranges(
                config.gauges, config.transitions
            )

    graph = TestPumpingGraph(config)
    gauge_range = graph._get_gauge_range("PLL", pressure)
    assert gauge_range == expected_range


@mark.parametrize(
    "transition, valid",
    [
        (
            {
                "valve": "VR",
                "from": "closed",
                "to": "open",
                "condition": {
                    "valve": {"VT": "closed", "VF": "closed"},
                },
            },
            False,
        ),
        (
            {
                "valve": "VR",
                "from": "open",
                "to": "closed",
            },
            False,
        ),
        (
            {
                "valve": "VR",
                "from": "closed",
                "to": "open",
                "condition": {
                    "gauge": {"PLL": {"LE": 0.1}},
                },
            },
            True,
        ),
        (
            {
                "valve": "VR",
                "from": "closed",
                "to": "open",
                "condition": {
                    "gauge": {"PLL": {"GE": 0.1}},
                },
            },
            False,
        ),
        (
            {
                "valve": "VR",
                "from": "closed",
                "to": "open",
                "condition": {
                    "pump": {"roughing": "on"},
                },
            },
            True,
        ),
        (
            {
                "valve": "VR",
                "from": "closed",
                "to": "open",
                "condition": {
                    "pump": {"turbo": "off"},
                },
            },
            False,
        ),
        (
            {
                "stage": True,
                "from": "exchange",
                "to": "loadlock",
            },
            False,
        ),
        (
            {
                "stage": True,
                "from": "microscope",
                "to": "loadlock",
            },
            True,
        ),
    ],
)
def test_is_valid_transition(config, transition, valid):
    state_data = config.initial_states[0].model_dump()
    state_data["gauges"]["PLL"] = (0.1, 450)
    state = NodeModel(**state_data)
    PumpingGraph._is_valid_transition(state, Transition(**transition)) == valid


def test_create_graph(graph, config):
    for named_state in config.named_states:
        assert any(
            node_data["name"] == named_state
            for _, node_data in graph._graph.nodes(data=True)
        )


def test_make_plan(graph):
    plan = graph.make_plan(
        {
            "valves": {
                "VA": "closed",
                "VB": "closed",
                "VV1": "closed",
                "VR": "open",
                "VF": "closed",
                "VT": "closed",
                "VE": "closed",
            },
            "pumps": {
                "roughing": "on",
                "turbo": "on",
            },
            "gauges": {
                "PLL": (4.5e-5, 0.1),
            },
            "stage": "loadlock",
        },
        name="imaging",
    )
    assert list(plan) == [
        PlanStep(wait={"PLL": Inequality(LE=0.085)}),
        PlanStep(valve="VR", state="closed"),
        PlanStep(valve="VF", state="open"),
        PlanStep(valve="VT", state="open"),
        PlanStep(valve="VE", state="open"),
        PlanStep(valve="VA", state="open"),
        PlanStep(stage="microscope"),
    ]


@mark.parametrize(
    "state",
    [
        {
            "valves": {
                "VA": "closed",
                "VB": "closed",
                "VE": "closed",
                "VT": "open",
                "VF": "open",
                "VR": "closed",
                "VV1": "closed",
            },
            "pumps": {
                "roughing": "on",
                "turbo": "on",
            },
            "gauges": {
                "PLL": (4.5e-5, 0.1),
            },
            "stage": "loadlock",
        },
        {
            "valves": {
                "VA": "closed",
                "VB": "closed",
                "VE": "open",
                "VT": "open",
                "VF": "open",
                "VR": "closed",
                "VV1": "closed",
            },
            "pumps": {
                "roughing": "on",
                "turbo": "on",
            },
            "gauges": {
                "PLL": (9e-6, 4.5e-5),
            },
            "stage": "loadlock",
        },
    ],
)
def test_make_plan_no_loops(graph, state):
    plan = list(graph.make_plan(state, name="imaging"))
    assert plan is not None
    for i in range(len(plan) - 1):
        step1 = plan[i]
        step2 = plan[i + 1]
        assert step1.valve is None or step1.valve != step2.valve
        assert step1.pump is None or step1.pump != step2.pump


@mark.parametrize(
    "plan, steps_removed",
    [
        (
            [
                PlanStep(valve="VF", state="closed"),
                PlanStep(valve="VF", state="open"),
                PlanStep(wait={"PLL": Inequality(LE=0.1)}),
                PlanStep(pump="roughing", state="off"),
            ],
            2,
        ),
        (
            [
                PlanStep(valve="VR", state="closed"),
                PlanStep(valve="VF", state="open"),
                PlanStep(valve="VR", state="open"),
            ],
            0,
        ),
        (
            [
                PlanStep(pump="roughing", state="off"),
                PlanStep(pump="roughing", state="on"),
                PlanStep(valve="VR", state="closed"),
            ],
            2,
        ),
        (
            [
                PlanStep(pump="turbo", state="off"),
                PlanStep(pump="roughing", state="off"),
                PlanStep(valve="VV1", state="open"),
            ],
            0,
        ),
        (
            [
                PlanStep(stage="loadlock"),
                PlanStep(stage="microscope"),
                PlanStep(valve="VR", state="closed"),
            ],
            2,
        ),
        (
            [
                PlanStep(stage="loadlock"),
                PlanStep(stage="exchange"),
                PlanStep(valve="VA", state="closed"),
            ],
            0,
        ),
    ],
)
def test_remove_redundant_steps(graph, plan, steps_removed):
    graph._make_plan = lambda *args, **kwargs: plan
    graph._find_state = lambda *args, **kwargs: True
    assert graph.make_plan({"stage": "microscope"}) == plan[steps_removed:]

