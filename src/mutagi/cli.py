"""Entry point for operational commands."""

import argparse
import subprocess
import sys
from importlib import import_module
from importlib.metadata import version

COMMANDS = {
    "init": "commands.init",
    "check": "commands.check",
    "experiment": "commands.experiment",
    "round": "commands.round",
    "series": "commands.series",
    "service": "commands.service",
    "handoff": "commands.handoff",
    "job": "commands.job",
}


def main():
    parser = argparse.ArgumentParser(
        prog="mutagi",
        description="Manage autonomous exploration, preservation, and inheritance",
    )
    parser.add_argument(
        "--version", action="version", version="%(prog)s " + version("mutagi")
    )
    parser.add_argument("command", choices=[*COMMANDS, "template"])
    args = parser.parse_args(sys.argv[1:2])
    remaining = sys.argv[2:]
    if args.command == "template":
        template_parser = argparse.ArgumentParser(
            prog="mutagi template",
            description="Print an experiment record template to standard output",
        )
        template_parser.add_argument("name", choices=["experiment"])
        template_parser.parse_args(remaining)
        from mutagi.prompts import record_template

        print(record_template(), end="")
        return

    sys.argv = [f"mutagi {args.command}", *remaining]
    try:
        import_module(f"mutagi.{COMMANDS[args.command]}").main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        parser.error(str(error))
