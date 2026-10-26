"""Shared human presentation; machine payloads deliberately bypass Rich."""

import json
import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def console(*, error=False):
    return Console(
        file=sys.stderr if error else sys.stdout,
        markup=False,
        highlight=False,
        emoji=False,
    )


def title(text):
    console().print(Text(text, style="bold cyan"))


def fields(items):
    output = console()
    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(style="dim", max_width=max(12, min(25, output.width // 3)))
    table.add_column(overflow="fold")
    for label, value in items:
        table.add_row(Text(str(label)), Text(str(value)))
    output.print(table)


def note(message, *, error=False):
    console(error=error).print(Text(message, style="yellow"), soft_wrap=False)


def failure(message):
    console(error=True).print(
        Panel(Text(message), title="Error", border_style="red", expand=False)
    )


def next_steps(*commands):
    console().print(Text("Next", style="bold"))
    for command in commands:
        console().print(Text("  " + command), soft_wrap=True)


def excerpt(text, limit=180):
    """A literal prefix, not a rewritten finding or inferred conclusion."""
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "…"


def payload(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def text_payload(value):
    sys.stdout.write(value)


def table(headers, rows):
    output = console()
    if output.width <= 60:
        for row in rows:
            output.print(Text(str(row[0]), style="bold cyan"))
            fields(list(zip(headers[1:], row[1:])))
            output.print()
        return
    result = Table(box=None, padding=(0, 1), expand=True, show_edge=False)
    for index, heading in enumerate(headers):
        result.add_column(
            heading,
            style="cyan" if index == 0 else None,
            overflow="fold",
            ratio=3 if index == 1 else 1,
        )
    for row in rows:
        result.add_row(*(Text(str(value)) for value in row))
    output.print(result)
