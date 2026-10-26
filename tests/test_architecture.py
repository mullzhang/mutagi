"""Keep package dependencies and shared test support independent of controllers."""

import ast
from importlib.util import resolve_name
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"


def module_name(path, root):
    parts = list(path.relative_to(root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imports(path, root):
    name = module_name(path, root)
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from ((alias.name, ()) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            yield base, tuple(alias.name for alias in node.names)


def source_graph():
    modules = {
        module_name(path, SOURCE): path for path in (SOURCE / "mutagi").rglob("*.py")
    }
    graph = {name: set() for name in modules}
    for name, path in modules.items():
        for imported, names in imports(path, SOURCE):
            if imported != "mutagi" and not imported.startswith("mutagi."):
                continue
            assert imported in modules, (name, imported)
            if imported != name:
                graph[name].add(imported)
            for symbol in names:
                child = imported + "." + symbol
                if child in modules and child != name:
                    graph[name].add(child)
    return graph


def test_source_dependencies_respect_package_boundaries():
    allowed = {
        "commands": {"commands", "execution", "project", "runtime", "prompts"},
        "execution": {"execution", "project", "runtime", "prompts"},
        "project": {"project"},
        "runtime": {"runtime"},
        "cli": {"commands", "prompts"},
        "__main__": {"cli"},
        "prompts": set(),
    }
    graph = source_graph()
    for name, dependencies in graph.items():
        if name == "mutagi":
            assert not dependencies
            continue
        owner = name.split(".")[1]
        for dependency in dependencies:
            assert dependency in graph, (name, dependency)
            if dependency != "mutagi":
                assert dependency.split(".")[1] in allowed[owner], (name, dependency)


def test_source_imports_have_no_cycles():
    graph = source_graph()
    visited = set()

    def visit(name, ancestors):
        assert name not in ancestors, " -> ".join([*ancestors, name])
        if name in visited:
            return
        for dependency in graph[name]:
            visit(dependency, [*ancestors, name])
        visited.add(name)

    for name in graph:
        visit(name, [])


def test_tests_do_not_import_each_other():
    for path in (ROOT / "tests").rglob("*.py"):
        for imported, names in imports(path, ROOT):
            assert not any(
                part.startswith("test_") or part == "conftest"
                for candidate in (imported, *(imported + "." + name for name in names))
                for part in candidate.split(".")
            ), (path.relative_to(ROOT), imported)
