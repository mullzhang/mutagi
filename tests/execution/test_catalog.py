"""Regressions from the committed naedo-forest catalog, not translated test output."""

import re
from pathlib import Path

import pytest

from mutagi.execution.catalog import parse_catalog, read_experiment
from mutagi.project.repository import git

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/catalogs/naedo-forest.md"
REVISION = "b299d982cee2ab340ce794c6181da6d6145f98d3"
FAILING = "b9aa4d73-a78e-435c-a48c-ece2ea3e1a91"
SAVED = "934d9f0363bec344d432f6992842ddea7c9628da"


def failing_block():
    content = FIXTURE.read_text()
    return content[content.index("<!-- forest-run:" + FAILING) :].split(
        "<!-- forest-experiment:", 1
    )[0]


def test_committed_catalog_layouts_preserve_references_and_verification_actors():
    entries = {
        entry["id"]: entry for entry in parse_catalog(FIXTURE.read_text(), REVISION)
    }
    assert set(entries) == {
        FAILING,
        "8eda224e-0a1e-453b-a21c-194d6045e2bc",
        "29ce36c7-f1ce-4862-a083-176cfe3c7774",
        "b11fd223-80a1-490b-8f69-4a2673210ea5",
        "eafee8a0-800c-4786-8ffb-0c80ba149141",
        "7893e7c7-23cc-4f6f-bcf3-6c24edcfe7e4",
    }
    failing = entries[FAILING]
    assert failing["title"] == "誤り率と費用が異なる質問の順序を選ぶ"
    assert failing["saved_commit"] == SAVED
    assert failing["base_commit"] == "05f69177d713b6f59a3c3ccc6a30e7f98a8e60df"
    assert failing["started_at"] == "2026-09-16T17:10:30Z"
    assert failing["parent_ids"] == []
    assert failing["status"] == "observed" and failing["exit_reason"] == "completed"
    assert failing["checks_actor"] == "explorer"
    assert "登録当時" in failing["original_human_decision"]
    assert "11テスト成功" in failing["checks"]
    for entry in entries.values():
        assert entry["catalog_commit"] == REVISION
        assert entry["catalog_has_unmarked_references"]
        assert "unreviewed" in entry["original_human_decision"]
    inherited = entries["7893e7c7-23cc-4f6f-bcf3-6c24edcfe7e4"]
    assert inherited["parent_ids"] == ["743f532c-e0e2-4fd4-b17b-3f07715b6996"]
    for identifier in (
        "8eda224e-0a1e-453b-a21c-194d6045e2bc",
        "29ce36c7-f1ce-4862-a083-176cfe3c7774",
        "b11fd223-80a1-490b-8f69-4a2673210ea5",
        "eafee8a0-800c-4786-8ffb-0c80ba149141",
    ):
        assert entries[identifier]["checks_actor"] == "controller"
        assert "起動側" in entries[identifier]["checks"]
    app = entries["eafee8a0-800c-4786-8ffb-0c80ba149141"]
    assert app["parent_ids"] == ["29ce36c7-f1ce-4862-a083-176cfe3c7774"]
    assert app["base_commit"] == "78fec841f3f05247a8bf197f63c7f87a20058737"
    assert app["implementation_commit"] == "0559f47962e49bc16c17e640ad28503f64c4e5f7"


@pytest.mark.parametrize(
    "old,new",
    [
        ("| 項目 | 内容 |", "| Field | Value |"),
        (
            "| 採用判断 | unreviewed。登録当時は起動側が保存構造のみを確認し、観測の再現は未実施だった。下記に後日の確認を追記 |\n",
            "",
        ),
        ("| 表示名 | 誤り率と費用が異なる質問の順序を選ぶ |", "| 表示名 |  |"),
        (
            "| 開始版 | `05f69177d713b6f59a3c3ccc6a30e7f98a8e60df` |",
            "| 開始版 | `05f69177d713b6f59a3c3ccc6a30e7f98a8e60df` |\n| 開始版 | `05f69177d713b6f59a3c3ccc6a30e7f98a8e60df` |",
        ),
        ("2026-09-16T17:10:30Z、なし", "2026-09-16T17:10:30Z、"),
        ("2026-09-16T17:10:30Z", "2026-02-30T17:10:30Z"),
        ("observed、completed", "success、completed"),
        ("observed、completed", "observed、"),
        ("git show " + SAVED, "git show " + "a" * 40),
        (
            "934d9f0363bec344d432f6992842ddea7c9628da`、`codex/",
            "934d9f0363bec344d432f6992842ddea7c9628da` `codex/",
        ),
        ("<!-- forest-run:" + FAILING + " -->", "<!-- forest-run:" + FAILING),
        ("<!-- forest-run:" + FAILING + " -->", "<!-- forest-run: -->"),
        ("## 自動実行 " + FAILING, "##   "),
    ],
)
def test_malformed_localized_controller_metadata_does_not_gain_defaults(old, new):
    original = failing_block()
    assert old in original
    changed = original.replace(old, new, 1)
    with pytest.raises(ValueError):
        parse_catalog(changed, REVISION)


@pytest.mark.parametrize(
    "line",
    [
        "| 起動側の確認 |",
        "| 観測実装 |",
        "| ID、開始UTC、親 |",
        "| 親IDと開始版 |",
        "| 限界 |",
    ],
)
def test_incomplete_app_tables_fail_instead_of_disappearing(line):
    content = FIXTURE.read_text()
    changed, count = re.subn(
        r"^" + re.escape(line) + r".*\n", "", content, count=1, flags=re.M
    )
    assert count == 1
    with pytest.raises(ValueError):
        parse_catalog(changed, REVISION)


def test_app_adoption_must_be_explicit_and_is_not_inferred_from_another_entry():
    content = FIXTURE.read_text().replace("採用判断は`unreviewed`。", "", 1)
    with pytest.raises(ValueError, match="explicit App result and adoption"):
        parse_catalog(content, REVISION)


def test_adopted_catalog_decision_is_preserved_verbatim():
    content = failing_block().replace(
        "unreviewed。登録当時", "adopted only the comparison inputs。登録当時", 1
    )
    (entry,) = parse_catalog(content, REVISION)
    assert entry["original_human_decision"].startswith(
        "adopted only the comparison inputs。"
    )


def test_app_marker_is_a_boundary_and_duplicate_ids_across_formats_fail():
    content = FIXTURE.read_text()
    assert len(parse_catalog(content, REVISION)) == 6
    with pytest.raises(ValueError, match="duplicate experiment ID"):
        parse_catalog(
            content + failing_block().replace("forest-run:", "forest-experiment:", 1),
            REVISION,
        )


def test_registration_cannot_borrow_a_later_assessment_table_or_record():
    content = failing_block()
    heading = content.index("| 項目 | 内容 |")
    content = content[:heading] + "## Later assessment\n\n" + content[heading:]
    with pytest.raises(ValueError, match="no registration table"):
        parse_catalog(content, REVISION)


def test_historical_only_catalog_is_not_reported_as_an_empty_success():
    content = FIXTURE.read_text().split("<!-- forest-run:", 1)[0]
    with pytest.raises(ValueError, match="without supported registration markers"):
        parse_catalog(content, REVISION)


def test_conflicting_record_commands_and_app_base_fields_are_detected():
    content = failing_block()
    record = re.search(r"^```bash\ngit show .*\n```$", content, re.M)[0]
    with pytest.raises(ValueError, match="invalid fixed references"):
        parse_catalog(content + "\n" + record + "\n", REVISION)
    content = FIXTURE.read_text().replace(
        "| 共通開始版 |",
        "| 開始版 | `aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa` |\n| 共通開始版 |",
        1,
    )
    with pytest.raises(ValueError, match="exactly one of"):
        parse_catalog(content, REVISION)


def test_localized_registration_resolves_real_saved_record_metadata(
    repo, preserved_experiment
):
    state = preserved_experiment
    original_record = (
        "experiments/2026-09-16T171030Z_noisy-question-order_" + FAILING + "/README.md"
    )
    content = failing_block()
    for before, after in (
        (original_record, state["result"]["record"]),
        (FAILING, state["id"]),
        (SAVED, state["saved_commit"]),
        ("2026-09-16T17:10:30Z", state["started_at"]),
        ("05f69177d713b6f59a3c3ccc6a30e7f98a8e60df", state["base"]),
    ):
        content = content.replace(before, after)
    catalog = repo / "experiments/INDEX.md"
    catalog.write_text(content)
    git(repo, "add", str(catalog))
    git(repo, "commit", "-m", "test: preserve the localized catalog layout")
    source = read_experiment(repo, state["id"])
    assert source["saved_commit"] == state["saved_commit"]
    assert source["implementation_commit"] == state["result"]["implementation_commit"]
    assert "11テスト成功" in source["checks"]
