# Auto Pumping

This Python package is meant to provide a generic system for automating high-vacuum pumping systems.

## Config

To support arbitrary pumping systems, a configuration file format was created where all valid valve, pump, and stage[^1] transition can be enumerated. Using this configuration file, the safety of any given valve transition can be evaluated, and the system automated. This configuration file format is documented [here](docs/config.md).

## Solver

To support automated pumping of the vacuum system a solver was implemented using the information in the configuration file. This information is used to build a directed graph of all valid pumping system states where discretized pressure is included. After this directed graph is built, Dijkstra's algorithm is used for find the optimal method for pumping or venting the system, or reaching any other state. The built graph can be viewed by running: `python -m auto_pumping -c config/config.yaml network`.

## UI

A NiceGUI based UI is also included. It is automatically built based on the configuration file. A dummy pumping system is available as an example and can be run with: `python -m auto_pumping -c config/config.yaml run`.

## Getting Started

To implement automated pumping for your system subclass the `AutoPumping` class and define the following methods:

* `get_valve_state(valve)`
* `actuate_valve(valve, state)`
* `get_pump_state(pump)`
* `set_pump(pump, state)`
* `get_pump_current(pump)`
* `get_pump_voltage(pump)`
* `get_pump_flow(pump)`
* `get_pump_speed(pump)`
* `get_gauge_pressure(gauge)`
* `get_stage_position()`
* `move_stage(position)`

If you would like to add more fields to the configuration file for your specific hardware, subclass the `Config` class and set the `AutoPumping.CONFIG_CLASS` variable to your new config class.

To allow the system to be easily started, you can use the provided `main()` function. Simply run `main(<your_auto_pumping_subclass>)`, for example:

```python
from auto_pumping.__main__ import main


if __name__ == "__main__":
    main(<your_auto_pumping_subclass>)
```

Finally, a diagram can be added to the UI by setting the `AutoPumping.DIAGRAM_PATH` variable to the path to a diagram image of your system.

[^1]: this library was originally written for a custom microscope system.
