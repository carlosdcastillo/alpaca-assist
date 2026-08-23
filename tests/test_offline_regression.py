from __future__ import annotations

from argparse import Namespace

import pytest

from offline_regression import Usage
from offline_regression import _resolve_pricing
from offline_regression import _update_usage
from offline_regression import calculate_cost
from offline_regression import compare_baseline
from offline_regression import summarize


def test_usage_and_cost_include_cached_input_discount() -> None:
    usage = Usage()
    _update_usage(
        usage,
        {
            "type": "message_delta",
            "usage": {
                "input_tokens": 100,
                "cache_read_input_tokens": 900,
                "output_tokens": 50,
            },
        },
    )

    assert usage == Usage(input_tokens=1000, cached_input_tokens=900, output_tokens=50)
    assert (
        calculate_cost(
            usage,
            {"input": 1.40, "cached_input": 0.14, "output": 4.40},
        )
        == 0.000486
    )


def test_summary_and_baseline_regression() -> None:
    results = [
        {
            "wall_seconds": 1.0,
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "total_tokens": 15,
            "cost_usd": 0.01,
        },
        {
            "wall_seconds": 3.0,
            "input_tokens": 20,
            "cached_input_tokens": 5,
            "output_tokens": 10,
            "total_tokens": 30,
            "cost_usd": 0.02,
        },
    ]

    summary = summarize(results)

    assert summary["wall_seconds"] == 4.0
    assert summary["p50_wall_seconds"] == 2.0
    assert summary["total_tokens"] == 45
    assert summary["cost_usd"] == 0.03
    regressions = compare_baseline(
        summary,
        {"summary": {"wall_seconds": 3, "total_tokens": 45, "cost_usd": 0.03}},
        10,
    )
    assert regressions == ["wall_seconds increased 33.3% (3 -> 4)"]


def test_custom_model_pricing_can_be_supplied() -> None:
    args = Namespace(
        model="another-model",
        input_cost_per_million=1.0,
        cached_input_cost_per_million=0.1,
        output_cost_per_million=3.0,
    )

    assert _resolve_pricing(args) == {
        "input": 1.0,
        "cached_input": 0.1,
        "output": 3.0,
    }


def test_custom_model_requires_complete_pricing() -> None:
    args = Namespace(
        model="another-model",
        input_cost_per_million=1.0,
        cached_input_cost_per_million=None,
        output_cost_per_million=3.0,
    )

    with pytest.raises(SystemExit, match="Specify input, cached-input, and output"):
        _resolve_pricing(args)
