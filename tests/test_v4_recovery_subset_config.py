from __future__ import annotations

from v4.tools.build_recovery_subset_config import filter_fast_settlement


def test_subset_filters_unknown_fast_settlement_rows() -> None:
    value = {
        "fast_settlement": {
            "repositories": [
                {"instance_id": "keep", "native_primary_coverage": 1.0},
                {"instance_id": "drop", "native_primary_coverage": 2.0},
            ]
        }
    }
    filter_fast_settlement(value, {"keep"})
    assert value["fast_settlement"]["repositories"] == [
        {"instance_id": "keep", "native_primary_coverage": 1.0}
    ]


def test_subset_removes_empty_fast_settlement_section() -> None:
    value = {
        "fast_settlement": {
            "repositories": [{"instance_id": "drop", "native_primary_coverage": 2.0}]
        }
    }
    filter_fast_settlement(value, {"keep"})
    assert "fast_settlement" not in value
