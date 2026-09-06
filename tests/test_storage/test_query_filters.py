from __future__ import annotations

from sciforge.storage.query import compile_search_filters


def test_include_ids_is_parameterized_and_empty_allowlist_matches_nothing() -> None:
    conditions, params = compile_search_filters(
        {"include_ids": ["q-1", "q-2"]},
        expected_item_kind="question",
    )
    empty_conditions, empty_params = compile_search_filters(
        {"include_ids": []},
        expected_item_kind="question",
    )

    assert conditions == ["id IN (?,?)"]
    assert params == ["q-1", "q-2"]
    assert empty_conditions == ["0 = 1"]
    assert empty_params == []
