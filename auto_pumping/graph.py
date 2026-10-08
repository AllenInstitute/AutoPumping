from networkx import DiGraph, dijkstra_path, dijkstra_path_length, relabel_nodes
from yaml import Node
from .config import ValveState, PumpState, Inequality, StrictModel
from pydantic import model_validator
from uuid import uuid4, UUID
from math import inf, log10
from pyvis.network import Network
from http.server import BaseHTTPRequestHandler, HTTPServer
import webbrowser
from itertools import product


def mean(vals):
    no_inf = [v for v in vals if v != inf]
    return sum(no_inf) / len(no_inf)


def log10mean(vals):
    m = mean(vals)
    if m <= 0:
        return 0
    return log10(mean(vals))


class NodeModel(StrictModel):
    name: str | None = None
    valves: dict[str, ValveState] = {}
    pumps: dict[str, PumpState] = {}
    stage: str | None = None
    gauges: dict[str, tuple[float, float]] = {}


class EdgeModel(StrictModel):
    time: float
    wait: dict[str, Inequality] = {}


class PlanStep(StrictModel):
    wait: dict[str, Inequality] | None = None
    stage: str | None = None
    valve: str | None = None
    pump: str | None = None
    state: ValveState | PumpState | None = None

    @model_validator(mode="after")
    def validate_wait(self):
        if self.wait is not None:
            if self.stage is not None:
                raise ValueError("Cannot specify both wait and stage")
            if self.valve is not None:
                raise ValueError("Cannot specify both wait and valve")
            if self.pump is not None:
                raise ValueError("Cannot specify both wait and pump")
            if self.state is not None:
                raise ValueError("Cannot specify both wait and state")
        return self

    @model_validator(mode="after")
    def validate_state(self):
        if self.state is not None:
            if self.valve is None and self.pump is None:
                raise ValueError("State specified without valve or pump")
            if self.valve is not None and self.pump is not None:
                raise ValueError("Cannot specify both valve and pump with state")
            if self.valve is not None and not isinstance(self.state, ValveState):
                raise ValueError("State must be a ValveState when valve is specified")
            if self.pump is not None and not isinstance(self.state, PumpState):
                raise ValueError("State must be a PumpState when pump is specified")
        return self


class PumpingGraph:
    def __init__(self, config):
        self._gauge_ranges = self._discretize_gauge_ranges(
            config.gauges, config.transitions
        )
        self._graph = DiGraph()
        self._last_goal_state_data = {}
        self._add_initial_states(config.initial_states, config.named_states)
        self._add_reachable_states(config.transitions, config.named_states)

    def _get_gauge_range(self, gauge, pressure):
        for gauge_range in self._gauge_ranges[gauge]:
            if gauge_range[0] <= pressure < gauge_range[1]:
                return gauge_range
        raise ValueError(f"Pressure {pressure} is out of range for gauge {gauge}")

    def _get_gauge_range_ind(self, gauge, pressure):
        return self._gauge_ranges[gauge].index(self._get_gauge_range(gauge, pressure))

    def _add_initial_states(self, initial_states, named_states):
        for state in initial_states:
            node_data = NodeModel(
                name=self._get_state_name(state, named_states),
                valves=state.valves,
                pumps=state.pumps,
                stage=state.stage,
                gauges={
                    gauge: self._get_gauge_range(gauge, pressure)
                    for gauge, pressure in state.gauges.items()
                },
            )
            self._graph.add_node(
                uuid4(),
                **node_data.model_dump(),
            )

    def _add_reachable_states(self, transitions, named_states):
        for state_id in list(self._graph.nodes):
            self._add_reachable_states_from(state_id, transitions, named_states)

    def _get_state_data(self, state_id):
        return NodeModel(**self._graph.nodes[state_id])

    def _add_state(self, state_data):
        assert self._find_state(state_data) is None
        state_id = uuid4()
        self._graph.add_node(state_id, **state_data.model_dump())
        return state_id

    def _add_reachable_states_from(self, state_id, transitions, named_states):
        state_data = self._get_state_data(state_id)
        for transition in transitions:
            if not self._is_valid_transition(state_data, transition):
                continue
            reachable_states = self._build_reachable_states(
                state_data, transition, named_states
            )
            for new_state_data, time in reachable_states:
                assert time > 0, f"Time must be positive, got {time} for transition {transition}"
                new_state_id = self._find_state(new_state_data)
                if new_state_id is None:
                    new_state_id = self._add_state(new_state_data)
                    self._add_reachable_states_from(
                        new_state_id, transitions, named_states
                    )
                self._add_edge(state_id, new_state_id, transition, time)

    def _add_edge(self, from_state_id, to_state_id, transition, time):
        if not self._graph.has_edge(from_state_id, to_state_id):
            edge_data = EdgeModel(
                wait=transition.wait,
                time=time,
            )
            self._graph.add_edge(
                from_state_id,
                to_state_id,
                **edge_data.model_dump(),
            )

    @staticmethod
    def _is_valid_transition(state_data, transition):
        if transition.stage and state_data.stage != transition.from_state:
            return False
        if (
            transition.valve is not None
            and state_data.valves.get(transition.valve) != transition.from_state
        ):
            return False
        if (
            transition.pump is not None
            and state_data.pumps.get(transition.pump) != transition.from_state
        ):
            return False
        if transition.condition is None:
            return True
        for gauge, ineq in transition.condition.gauge.items():
            pressure_range = state_data.gauges[gauge]
            pressure = sum(pressure_range) / 2
            if not ineq.evaluate(pressure):
                return False
        for valve, valve_condition in transition.condition.valve.items():
            valve_state = valve_condition.state
            if state_data.valves.get(valve) != valve_state:
                return False
        for pump, pump_state in transition.condition.pump.items():
            if state_data.pumps.get(pump) != pump_state.state:
                return False
        if (
            transition.condition.stage is not None
            and state_data.stage != transition.condition.stage
        ):
            return False
        return True

    def _find_state(self, state_data):
        for node_id in self._graph.nodes:
            node_data = self._get_state_data(node_id)
            if (
                node_data.valves == state_data.valves
                and node_data.pumps == state_data.pumps
                and node_data.stage == state_data.stage
                and node_data.gauges == state_data.gauges
            ):
                return node_id
        return None

    def _find_states(self, name=None, valves=None, pumps=None, stage=None):
        for node_id in self._graph.nodes:
            node_data = self._get_state_data(node_id)
            if name is not None and node_data.name != name:
                continue
            if valves is not None and any(
                state != node_data.valves.get(valve) for valve, state in valves.items()
            ):
                continue
            if pumps is not None and any(
                state != node_data.pumps.get(pump) for pump, state in pumps.items()
            ):
                continue
            if stage is not None and node_data.stage != stage:
                continue
            yield node_id

    def _build_reachable_states(self, state_data, transition, named_states):
        new_state_data = state_data.model_copy(deep=True)
        if transition.valve is not None:
            new_state_data.valves[transition.valve] = transition.to_state
        if transition.pump is not None:
            new_state_data.pumps[transition.pump] = transition.to_state
        if transition.stage:
            new_state_data.stage = transition.to_state
        new_state_data.name = self._get_state_name(new_state_data, named_states)
        yield new_state_data, 1
        if transition.pressure is None:
            return
        start_pressure_ind = {
            gauge: self._gauge_ranges[gauge].index(state_data.gauges[gauge])
            for gauge in self._gauge_ranges
        }
        end_pressure_ind = {
            gauge: self._get_gauge_range_ind(gauge, transition.pressure[gauge])
                if gauge in transition.pressure
                else start_pressure_ind[gauge]
            for gauge in self._gauge_ranges
        }
        start_mean_pressures_log = {
            gauge: log10mean(self._gauge_ranges[gauge][start_pressure_ind[gauge]])
            for gauge in self._gauge_ranges
        }
        pressure_diffs_log = {
            gauge: log10mean(self._gauge_ranges[gauge][end_pressure_ind[gauge]]) - log10mean(self._gauge_ranges[gauge][start_pressure_ind[gauge]])
            for gauge in self._gauge_ranges
        }
        pressure_ind_ranges = {
            gauge: range(
                min(start_pressure_ind[gauge], end_pressure_ind[gauge]),
                max(start_pressure_ind[gauge], end_pressure_ind[gauge]) + 1,
            ) for gauge in self._gauge_ranges
        }
        for pressure_range_inds in product(*pressure_ind_ranges.values()):
            fractions_complete = []
            for gauge, ind in zip(pressure_ind_ranges.keys(), pressure_range_inds):
                new_state_data.gauges[gauge] = self._gauge_ranges[gauge][ind]
                if pressure_diffs_log[gauge] == 0:
                    continue
                fractions_complete.append(
                    (log10mean(self._gauge_ranges[gauge][ind]) - start_mean_pressures_log[gauge])
                    / pressure_diffs_log[gauge]
                )
            new_state_data.name = self._get_state_name(new_state_data, named_states)
            if transition.time is None or len(fractions_complete) == 0:
                yield new_state_data, 1
            else:
                yield new_state_data, max(60 * transition.time * mean(fractions_complete), 1)

    @staticmethod
    def _get_state_name(state, named_states):
        for name, named_state in named_states.items():
            is_named = True
            for valve, valve_state in named_state.valves.items():
                if state.valves.get(valve) != valve_state:
                    is_named = False
                    break
            for pump, pump_state in named_state.pumps.items():
                if state.pumps.get(pump) != pump_state:
                    is_named = False
                    break
            if named_state.stage is not None and state.stage != named_state.stage:
                is_named = False
            if is_named:
                return name
        return None

    @staticmethod
    def _discretize_gauge_ranges(gauges, transitions):
        gauge_range_edges = {gauge: [0, inf] for gauge in gauges}
        for transition in transitions:
            if transition.condition is None:
                continue
            if transition.condition.gauge is None:
                continue
            for gauge, ineq in transition.condition.gauge.items():
                if ineq.LE is not None and ineq.LE not in gauge_range_edges[gauge]:
                    gauge_range_edges[gauge].append(ineq.LE)
                if ineq.GE is not None and ineq.GE not in gauge_range_edges[gauge]:
                    gauge_range_edges[gauge].append(ineq.GE)
        for edges in gauge_range_edges.values():
            edges.sort()
        return {
            gauge: [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]
            for gauge, edges in gauge_range_edges.items()
        }

    @staticmethod
    def _state_diff(start_state_data, end_state_data):
        if start_state_data.stage != end_state_data.stage:
            return PlanStep(stage=end_state_data.stage)
        for valve, state in end_state_data.valves.items():
            if start_state_data.valves.get(valve) != state:
                return PlanStep(valve=valve, state=state)
        for pump, state in end_state_data.pumps.items():
            if start_state_data.pumps.get(pump) != state:
                return PlanStep(pump=pump, state=state)
        raise ValueError("No difference between states")

    def _make_plan(self, start_state, **goal_state_data):
        self._last_goal_state_data = goal_state_data
        goal_lengths = sorted(
            [
                (
                    dijkstra_path_length(self._graph, start_state, goal_state, "time"),
                    goal_state,
                )
                for goal_state in self._find_states(**goal_state_data)
            ],
            key=lambda x: x[0],
        )
        assert len(goal_lengths), "No goal state found matching criteria"
        path = dijkstra_path(self._graph, start_state, goal_lengths[0][1], "time")
        for i in range(len(path) - 1):
            edge_data = self._graph.get_edge_data(path[i], path[i + 1])
            start_node_data = self._get_state_data(path[i])
            end_node_data = self._get_state_data(path[i + 1])
            if edge_data["wait"]:
                yield PlanStep(wait=edge_data["wait"])
            yield self._state_diff(start_node_data, end_node_data)

    def make_plan(self, start_state, **goal_state_data):
        if not isinstance(start_state, NodeModel):
            start_state = NodeModel(**start_state)
        start_state_id = self._find_state(start_state)
        assert start_state_id is not None, f"Cannot find state {start_state}"
        plan = list(self._make_plan(start_state_id, **goal_state_data))
        if plan[0].valve is not None and plan[0].valve == plan[1].valve:
            plan = plan[2:]
        elif plan[0].pump is not None and plan[0].pump == plan[1].pump:
            plan = plan[2:]
        elif plan[0].stage is not None and plan[1].stage == start_state.stage:
            plan = plan[2:]
        return plan

    def draw(self, output_file=None, port=8000):
        net = Network(height="100vh", width="100%", directed=True)
        graph = relabel_nodes(self._graph, str, copy=True)
        for node in graph.nodes:
            node_data = self._get_state_data(UUID(node))
            title = ""
            title += f"Stage: {node_data.stage}\n"
            title += "Valves:\n"
            for valve, state in node_data.valves.items():
                title += f"  {valve}: {state.value}\n"
            title += "Pumps:\n"
            for pump, state in node_data.pumps.items():
                title += f"  {pump}: {state.value}\n"
            title += "Gauges:\n"
            for gauge, pressure_range in node_data.gauges.items():
                title += f"  {gauge}: {pressure_range[0]} - {pressure_range[1]}\n"
            graph.nodes[node]["label"] = node_data.name if node_data.name else " "
            graph.nodes[node]["title"] = title.strip()
        for edge in graph.edges:
            edge_data = EdgeModel(**graph.edges[edge])
            title = f"Time: {edge_data.time} s\n"
            if edge_data.wait:
                title += "Wait:\n"
                for gauge, ineq in edge_data.wait.items():
                    title += f"  {gauge}: {ineq}\n"
            graph.edges[edge]["title"] = title.strip()
            edge_diff = self._state_diff(
                self._get_state_data(UUID(edge[0])), self._get_state_data(UUID(edge[1]))
            )
            if edge_diff.stage is not None:
                graph.edges[edge]["label"] = f"to {edge_diff.stage}"
            if edge_diff.valve is not None:
                graph.edges[edge][
                    "label"
                ] = f"{edge_diff.valve} {edge_diff.state.value}"
            if edge_diff.pump is not None:
                graph.edges[edge]["label"] = f"{edge_diff.pump} {edge_diff.state.value}"
        net.from_nx(graph)
        if output_file:
            net.write_html(output_file)
            return
        page = net.generate_html().encode("utf-8")

        class InMemoryGraphHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.send_header("Content-length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)

            def log_message(self, format, *args):
                return  # Suppress logging

        HTTPServer.allow_reuse_address = True
        server = HTTPServer(("127.0.0.1", port), InMemoryGraphHandler)
        print(f"Server at http://127.0.0.1:{port}")

        # Open the browser automatically
        webbrowser.open(f"http://127.0.0.1:{port}")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")
            server.server_close()
