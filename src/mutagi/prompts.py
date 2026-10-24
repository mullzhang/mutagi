"""Fill packaged prompts with fixed execution conditions."""

from importlib.resources import files


def record_template() -> str:
    return (
        files("mutagi")
        .joinpath("resources/templates/experiment.md")
        .read_text(encoding="utf-8")
    )


def render_prompt(name: str, **values) -> str:
    return (
        files("mutagi")
        .joinpath(f"resources/prompts/{name}.md")
        .read_text()
        .format(**values)
    )
