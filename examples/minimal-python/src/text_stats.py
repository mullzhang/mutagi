"""Count whitespace-delimited items without interpreting linguistic words."""


def count_items(text: str) -> dict[str, int]:
    items = text.split()
    return {"items": len(items), "distinct_items": len(set(items))}
