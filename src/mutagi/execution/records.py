"""Read the fixed frontmatter format of experiment records."""

import re

OBSERVATION_STATUSES = ("observed", "refuted", "unresolved", "blocked")


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
        raise ValueError(f"the record frontmatter requires exactly one {name} field")
    return values[0]


def record_parents(frontmatter: str) -> list[str]:
    parents = re.fullmatch(r"\[(.*)\]", record_field(frontmatter, "parent_ids"))
    if parents is None:
        raise ValueError("the record requires parent_ids in inline form")
    return [p.strip().strip("\"'") for p in parents[1].split(",") if p.strip()]
