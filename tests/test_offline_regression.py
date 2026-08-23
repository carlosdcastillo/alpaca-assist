from __future__ import annotations

import json
from argparse import Namespace

import pytest

from anthropic_ollama_server import SYSTEM_PROMPT
from chat_state import ToolCall
from offline_regression import CaseLimits
from offline_regression import Usage
from offline_regression import _case_limit_failure
from offline_regression import _ConfiguredFireworksClient
from offline_regression import _failure_tool_trace
from offline_regression import _RegressionAppCore
from offline_regression import _resolve_pricing
from offline_regression import _select_cases
from offline_regression import _update_usage
from offline_regression import _validate_agent_case
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


def test_suite_tool_profile_exposes_only_tools_covered_by_cases() -> None:
    tools = _RegressionAppCore(
        "http://example",
        "model",
        "suite",
    ).get_available_mcp_tools()

    assert {tool["function"]["name"] for tool in tools} == {
        "internal_modify_file",
        "internal_read_file",
        "internal_read_file_range",
        "internal_run_shell_command",
        "internal_search_files_for_text",
        "internal_write_file",
    }


def test_configured_client_overrides_temperature() -> None:
    class Client:
        def stream_complete(self, **kwargs):
            return kwargs

    client = _ConfiguredFireworksClient(Client(), 0, 12000)  # type: ignore[arg-type]

    settings = client.stream_complete(temperature=0.7, max_tokens=40000)
    assert settings["temperature"] == 0
    assert settings["max_tokens"] == 12000


def test_proxy_prompt_uses_efficient_execution_policy_by_default() -> None:
    core = _RegressionAppCore("http://example", "model")

    assert "without extra workspace discovery" in SYSTEM_PROMPT
    assert "<execution_policy>" not in core.get_system_prompt(object())


def test_validation_rejects_changes_to_protected_case_files(tmp_path) -> None:
    protected = tmp_path / "test_feature.py"
    protected.write_text("changed\n", encoding="utf-8")

    failures = _validate_agent_case(
        {
            "files": {"test_feature.py": "original\n"},
            "unchanged_files": ["test_feature.py"],
        },
        tmp_path,
        "",
        [],
        [],
    )

    assert failures == ["protected file was modified: test_feature.py"]


def test_validation_accepts_any_tool_in_required_group(tmp_path) -> None:
    case = {
        "required_tool_groups": [["internal_modify_file", "internal_write_file"]],
    }

    assert (
        _validate_agent_case(
            case,
            tmp_path,
            "",
            ["internal_write_file"],
            [],
        )
        == []
    )
    assert _validate_agent_case(case, tmp_path, "", [], []) == [
        "none of the alternative tools were called: internal_modify_file, internal_write_file",
    ]


def test_case_limit_stops_repeated_identical_tool_calls() -> None:
    call = ToolCall(
        '{"tool_call":{"name":"internal_read_file_range","arguments":{"file_path":"events.log","start_line":1}}}',
        "call-1",
    )

    failure = _case_limit_failure(
        3,
        [call, call, call],
        Usage(input_tokens=1000, output_tokens=100),
        {"input": 3.0, "cached_input": 0.3, "output": 15.0},
        CaseLimits(),
    )

    assert failure == "identical tool-call limit reached (3/3)"


def test_case_limit_caps_provider_cost() -> None:
    failure = _case_limit_failure(
        2,
        [],
        Usage(input_tokens=100000, output_tokens=1000),
        {"input": 3.0, "cached_input": 0.3, "output": 15.0},
        CaseLimits(max_cost_usd=0.25),
    )

    assert failure == "case cost limit reached ($0.315000/$0.250000)"


def test_failure_trace_is_bounded_and_preserves_range_arguments() -> None:
    call = ToolCall(
        json.dumps(
            {
                "tool_call": {
                    "name": "internal_read_file_range",
                    "arguments": {
                        "file_path": "events.log",
                        "start_line": 700,
                        "content": "x" * 500,
                    },
                },
            },
        ),
        "call-1",
    )

    trace = _failure_tool_trace([call])

    assert trace[0]["arguments"]["file_path"] == "events.log"
    assert trace[0]["arguments"]["start_line"] == 700
    assert len(trace[0]["arguments"]["content"]) < 250


def test_select_cases_supports_low_cost_targeted_reruns() -> None:
    cases = [{"id": "one"}, {"id": "two"}]

    assert _select_cases(cases, ["two"]) == [{"id": "two"}]
    with pytest.raises(SystemExit, match="Unknown regression case.*missing"):
        _select_cases(cases, ["missing"])
