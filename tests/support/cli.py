"""Invoke the actual public Typer tree while preserving pytest's streams."""

import sys

from mutagi.cli import app


def invoke(module):
    name = module.__name__.rsplit(".", 1)[-1]
    result = app(args=[name, *sys.argv[1:]], prog_name="mutagi", standalone_mode=False)
    if isinstance(result, int) and result:
        raise SystemExit(result)
    return result
