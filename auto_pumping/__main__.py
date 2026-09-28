import argparse
from .dummy import DummyPumping


def build_parser(system_class):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c",
        "--config",
        type=str,
        required=True,
        help="Path to the configuration file.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Sub-command help")
    network_parser = subparsers.add_parser("network", help="Draw the network graph.")
    network_parser.add_argument(
        "--output", "-o", type=str, help="Output file for the network graph."
    )
    run_parser = subparsers.add_parser("run", help="Run the pumping system.")

    return parser


def main(system_class):
    parser = build_parser(system_class)
    args = parser.parse_args()
    system = system_class.from_config_file(args.config)
    if args.command == "network":
        system._pumping_graph.draw(args.output)
    elif args.command == "run":
        system.run()


if __name__ == "__main__":
    main(DummyPumping)
