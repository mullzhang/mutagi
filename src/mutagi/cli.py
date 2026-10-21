"""Entry point for operational commands."""

import argparse
from importlib import import_module, resources
import sys

COMMANDS = {
    "experiment": "run_experiment",
    "round": "run_round",
    "series": "run_series",
    "service": "series_service",
    "handoff": "app_handoff",
    "job": "command_job",
}


def main():
    parser = argparse.ArgumentParser(
        prog="mutagi", description="Manage autonomous exploration, preservation, and inheritance"
    )
    parser.add_argument("command", choices=[*COMMANDS, "template"])
    args = parser.parse_args(sys.argv[1:2])
    remaining = sys.argv[2:]
    if args.command == "template":
        template_parser = argparse.ArgumentParser(
            prog="mutagi template", description="Print an experiment record template to standard output"
        )
        template_parser.add_argument("name", choices=["experiment"])
        template_parser.parse_args(remaining)
        print(
            resources.files("mutagi")
            .joinpath("resources/templates/experiment.md")
            .read_text(),
            end="",
        )
        return

    sys.argv = [f"mutagi {args.command}", *remaining]
    import_module(f"mutagi.{COMMANDS[args.command]}").main()
