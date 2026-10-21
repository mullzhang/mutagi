"""Fill packaged prompts with fixed execution conditions."""

from importlib.resources import files


def render_prompt(name: str, **values) -> str:
    return (
        files("mutagi")
        .joinpath(f"resources/prompts/{name}.md")
        .read_text()
        .format(**values)
    )
