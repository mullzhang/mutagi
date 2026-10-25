"""Read the fixed frontmatter format of experiment records."""

import re


def record_frontmatter(content: str) -> str:
    lines = content.splitlines()
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        raise ValueError(
            "the observation record requires opening and closing frontmatter delimiters"
        )
    return "\n".join(lines[1 : lines.index("---", 1)])


def record_field(frontmatter: str, name: str) -> str:
    values = re.findall(r"^" + re.escape(name) + r": (.*)$", frontmatter, re.M)
    if len(values) != 1:
        raise ValueError(
            f"the record frontmatter requires exactly one {name} field"
        )
    return values[0]
