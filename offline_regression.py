"""Run repeatable, non-UI LLM regression cases and report usage metrics."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import math
import os
import statistics
import subprocess
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import anthropic_ollama_server
import internal_tools
from anthropic_ollama_server import SYSTEM_PROMPT
from anthropic_ollama_server import FireworksClient
from anthropic_ollama_server import OllamaRequestHandler
from anthropic_ollama_server import map_ollama_to_model
from chat_state import ToolCall
from chat_state import ToolResult
from core.app_core import AppCore
from core.chat_tab import ChatTab

DEFAULT_CASES = Path(__file__).with_name("benchmarks") / "offline_regression_cases.json"
DEFAULT_MODEL = "glm-5p2"
DEFAULT_MAX_TOKENS_PER_INVOCATION = 8000
DEFAULT_MAX_CASE_INVOCATIONS = 20
DEFAULT_MAX_CASE_TOOL_CALLS = 20
DEFAULT_MAX_IDENTICAL_TOOL_CALLS = 3
DEFAULT_MAX_CASE_TOKENS = 250000
DEFAULT_MAX_CASE_COST_USD = 0.25
SUITE_TOOL_NAMES = {
    "internal_modify_file",
    "internal_read_file",
    "internal_read_file_range",
    "internal_run_shell_command",
    "internal_search_files_for_text",
    "internal_write_file",
}

# Fireworks standard serverless pricing, USD per million tokens.
# https://docs.fireworks.ai/serverless/pricing
MODEL_PRICING: dict[str, dict[str, float]] = {
    "glm-5p2": {"input": 1.40, "cached_input": 0.14, "output": 4.40},
    "kimi-k3": {"input": 3.00, "cached_input": 0.30, "output": 15.00},
}


@dataclass
class Usage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class CaseLimits:
    max_invocations: int = DEFAULT_MAX_CASE_INVOCATIONS
    max_tool_calls: int = DEFAULT_MAX_CASE_TOOL_CALLS
    max_identical_tool_calls: int = DEFAULT_MAX_IDENTICAL_TOOL_CALLS
    max_tokens: int = DEFAULT_MAX_CASE_TOKENS
    max_cost_usd: float = DEFAULT_MAX_CASE_COST_USD


def _update_usage(usage: Usage, event: dict[str, Any]) -> None:
    event_usage = event.get("usage")
    if event.get("type") == "message_start":
        event_usage = event.get("message", {}).get("usage")
    if not isinstance(event_usage, dict):
        return

    fresh = int(event_usage.get("input_tokens") or 0)
    cache_write = int(event_usage.get("cache_creation_input_tokens") or 0)
    cache_read = int(event_usage.get("cache_read_input_tokens") or 0)
    reported_input = fresh + cache_write + cache_read
    if reported_input:
        usage.input_tokens = reported_input
        usage.cached_input_tokens = cache_write + cache_read

    reported_output = event_usage.get("output_tokens")
    if reported_output is None:
        reported_output = event_usage.get("completion_tokens")
    if reported_output is not None:
        usage.output_tokens = int(reported_output)


def calculate_cost(usage: Usage, pricing: dict[str, float]) -> float:
    fresh_input = max(0, usage.input_tokens - usage.cached_input_tokens)
    return (
        fresh_input * pricing["input"]
        + usage.cached_input_tokens * pricing["cached_input"]
        + usage.output_tokens * pricing["output"]
    ) / 1_000_000


def run_case(
    client: FireworksClient,
    case: dict[str, Any],
    model_id: str,
    pricing: dict[str, float],
    system_prompt: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    usage = Usage()
    response_parts: list[str] = []

    stream = client.stream_complete(
        messages=[{"role": "user", "content": case["prompt"]}],
        model=model_id,
        max_tokens=int(case.get("max_tokens", 512)),
        temperature=0,
        system=system_prompt,
    )
    for event in stream:
        _update_usage(usage, event)
        if event.get("type") == "content_block_delta":
            text = event.get("delta", {}).get("text")
            if isinstance(text, str):
                response_parts.append(text)

    wall_seconds = time.perf_counter() - started
    response = "".join(response_parts).strip()
    if not response:
        raise RuntimeError(f"Case {case['id']!r} returned an empty response")
    if usage.input_tokens == 0 or usage.output_tokens == 0:
        raise RuntimeError(
            f"Case {case['id']!r} returned incomplete token usage: {usage}",
        )

    result = {
        "id": case["id"],
        "wall_seconds": round(wall_seconds, 3),
        **asdict(usage),
        "total_tokens": usage.total_tokens,
        "cost_usd": round(calculate_cost(usage, pricing), 8),
        "response_chars": len(response),
    }
    print(
        f"{result['id']}: {result['wall_seconds']:.3f}s, "
        f"{result['total_tokens']} tokens, ${result['cost_usd']:.6f}",
    )
    return result


class _RegressionAPI:
    """No-UI implementation of the WebView callbacks used by ChatTab."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def on_error(self, _tab_id: str, error: str) -> None:
        self.errors.append(error)

    def wait_for_fold_rendered(self, *_args: Any, **_kwargs: Any) -> bool:
        return True

    def __getattr__(self, _name: str) -> Any:
        return lambda *_args, **_kwargs: None


class _RegressionAppCore(AppCore):
    """Minimal AppCore boundary that retains real prompt/tool construction."""

    def __init__(
        self,
        api_url: str,
        model: str,
        tool_profile: str = "all",
    ) -> None:
        self.api: Any = _RegressionAPI()
        self.preferences = {"api_url": api_url, "model": model}
        self.tool_profile = tool_profile

    def get_skills_xml(self) -> str:
        return ""

    def get_available_mcp_tools(self) -> list[dict[str, Any]]:
        tools = list(internal_tools.TOOL_SCHEMAS)
        if self.tool_profile == "suite":
            tools = [
                tool
                for tool in tools
                if tool.get("function", {}).get("name") in SUITE_TOOL_NAMES
            ]
        return tools

    def call_mcp_tool(self, *_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Offline regression exposes internal tools only")


class _QuietOllamaRequestHandler(OllamaRequestHandler):
    def log_message(self, _format: str, *_args: Any) -> None:
        pass


class _RegressionHTTPServer(ThreadingHTTPServer):
    # Do not make server_close wait for a timed-out provider request.
    daemon_threads = True


@contextlib.contextmanager
def _ollama_harness_server(client: FireworksClient) -> Iterator[str]:
    previous_client = anthropic_ollama_server.fireworks_client
    anthropic_ollama_server.fireworks_client = client
    server = _RegressionHTTPServer(("127.0.0.1", 0), _QuietOllamaRequestHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        anthropic_ollama_server.fireworks_client = previous_client


class _ConfiguredFireworksClient:
    """Apply benchmark-only generation settings without changing app defaults."""

    def __init__(
        self,
        client: FireworksClient,
        temperature: float | None,
        max_tokens: int | None,
    ) -> None:
        self.client = client
        self.temperature = temperature
        self.max_tokens = max_tokens

    def stream_complete(self, *args: Any, **kwargs: Any) -> Any:
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return self.client.stream_complete(*args, **kwargs)


def _prepare_workspace(case: dict[str, Any], workspace: Path) -> None:
    for relative_path, content in case.get("files", {}).items():
        path = workspace / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    large_file = case.get("large_file")
    if large_file:
        line_count = int(large_file.get("line_count", 800))
        lines = [
            f"event={line:04d} status=ok payload={'x' * 48}"
            for line in range(1, line_count)
        ]
        lines.append(str(large_file["final_line"]))
        path = workspace / large_file["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _tool_name(call: ToolCall) -> str:
    try:
        parsed = json.loads(call.content)
        container = parsed.get("tool_call", parsed)
        return str(container.get("name", ""))
    except (json.JSONDecodeError, AttributeError):
        return ""


def _tool_call_data(call: ToolCall) -> tuple[str, Any]:
    try:
        parsed = json.loads(call.content)
        container = parsed.get("tool_call", parsed)
        if not isinstance(container, dict):
            return "", None
        return str(container.get("name", "")), container.get("arguments")
    except (json.JSONDecodeError, AttributeError):
        return "", None


def _tool_call_fingerprint(call: ToolCall) -> str:
    name, arguments = _tool_call_data(call)
    return json.dumps(
        {"name": name, "arguments": arguments},
        sort_keys=True,
        default=str,
    )


def _bounded_diagnostic_value(value: Any, depth: int = 0) -> Any:
    if depth >= 3:
        return "<nested>"
    if isinstance(value, str):
        if len(value) <= 200:
            return value
        return value[:200] + f"... <{len(value) - 200} chars omitted>"
    if isinstance(value, dict):
        return {
            str(key): _bounded_diagnostic_value(item, depth + 1)
            for key, item in list(value.items())[:20]
        }
    if isinstance(value, list):
        return [_bounded_diagnostic_value(item, depth + 1) for item in value[:10]]
    return value


def _failure_tool_trace(calls: list[ToolCall], limit: int = 20) -> list[dict[str, Any]]:
    trace: list[dict[str, Any]] = []
    start = max(0, len(calls) - limit)
    for index, call in enumerate(calls[start:], start=start):
        name, arguments = _tool_call_data(call)
        trace.append(
            {
                "index": index,
                "name": name,
                "arguments": _bounded_diagnostic_value(arguments),
            },
        )
    return trace


def _case_limit_failure(
    invocations: int,
    calls: list[ToolCall],
    usage: Usage,
    pricing: dict[str, float],
    limits: CaseLimits,
) -> str | None:
    if invocations >= limits.max_invocations:
        return f"case invocation limit reached ({invocations}/{limits.max_invocations})"
    if len(calls) >= limits.max_tool_calls:
        return f"case tool-call limit reached ({len(calls)}/{limits.max_tool_calls})"
    fingerprints = Counter(_tool_call_fingerprint(call) for call in calls)
    if fingerprints:
        repeated = max(fingerprints.values())
        if repeated >= limits.max_identical_tool_calls:
            return (
                "identical tool-call limit reached "
                f"({repeated}/{limits.max_identical_tool_calls})"
            )
    if usage.total_tokens >= limits.max_tokens:
        return f"case token limit reached ({usage.total_tokens}/{limits.max_tokens})"
    cost = calculate_cost(usage, pricing)
    if cost >= limits.max_cost_usd:
        return f"case cost limit reached (${cost:.6f}/${limits.max_cost_usd:.6f})"
    return None


def _validate_agent_case(
    case: dict[str, Any],
    workspace: Path,
    answer: str,
    tools: list[str],
    results: list[str],
) -> list[str]:
    failures: list[str] = []
    for required in case.get("required_tools", []):
        if required not in tools:
            failures.append(f"required tool was not called: {required}")
    for alternatives in case.get("required_tool_groups", []):
        if not any(tool in tools for tool in alternatives):
            failures.append(
                "none of the alternative tools were called: " + ", ".join(alternatives),
            )
    for expected in case.get("answer_contains", []):
        if expected.lower() not in answer.lower():
            failures.append(f"answer does not contain {expected!r}")
    for relative_path, expected in case.get("file_contains", {}).items():
        path = workspace / relative_path
        if not path.exists():
            failures.append(f"expected file was not created: {relative_path}")
        elif expected not in path.read_text(encoding="utf-8"):
            failures.append(f"{relative_path} does not contain {expected!r}")
    for relative_path, minimum in case.get("file_min_bytes", {}).items():
        path = workspace / relative_path
        if not path.exists():
            failures.append(f"expected file was not created: {relative_path}")
        elif path.stat().st_size < int(minimum):
            failures.append(
                f"{relative_path} is {path.stat().st_size} bytes; expected at least {minimum}",
            )
    for relative_path in case.get("unchanged_files", []):
        path = workspace / relative_path
        expected = case.get("files", {}).get(relative_path)
        if not path.exists():
            failures.append(f"protected file was deleted: {relative_path}")
        elif expected is None or path.read_text(encoding="utf-8") != expected:
            failures.append(f"protected file was modified: {relative_path}")
    if case.get("requires_gated_result") and not any(
        "[Output truncated:" in result for result in results
    ):
        failures.append("no tool result crossed the output gate")
    validation_command = case.get("validation_command")
    if validation_command:
        completed = subprocess.run(
            validation_command,
            cwd=workspace,
            shell=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            output = (completed.stdout + completed.stderr).strip()[-500:]
            failures.append(f"validation command failed: {output}")
    return failures


def run_agent_case(
    case: dict[str, Any],
    api_url: str,
    model: str,
    pricing: dict[str, float],
    tool_profile: str = "all",
    limits: CaseLimits | None = None,
) -> dict[str, Any]:
    limits = limits or CaseLimits()
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=f"alpaca-regression-{case['id']}-") as tmp:
        workspace = Path(tmp)
        _prepare_workspace(case, workspace)
        previous_workspace = os.environ.get("ALPACA_WORKSPACE")
        os.environ["ALPACA_WORKSPACE"] = str(workspace)
        app_core = _RegressionAppCore(
            api_url,
            model,
            tool_profile,
        )
        chat = ChatTab(f"regression-{case['id']}", case["id"], app_core, 1)
        chat.workspace_path = str(workspace)
        # A title-generation request is UI behavior, not part of the agent turn.
        chat._summary_handler._generated = True
        try:
            limit_failure: str | None = None
            with contextlib.redirect_stdout(io.StringIO()):
                chat.handle_user_message(case["prompt"], [])
                deadline = time.monotonic() + float(case.get("timeout_seconds", 300))
                while (
                    chat.current_turn_timing is not None and time.monotonic() < deadline
                ):
                    turn = chat.current_turn_timing
                    current_calls = [
                        component
                        for component in chat.chat_state.answers[0].components
                        if isinstance(component, ToolCall)
                    ]
                    current_usage = Usage(
                        input_tokens=chat.session_input_tokens,
                        cached_input_tokens=chat.session_cached_input_tokens,
                        output_tokens=chat.session_output_tokens,
                    )
                    limit_failure = _case_limit_failure(
                        turn.invocations if turn is not None else 0,
                        current_calls,
                        current_usage,
                        pricing,
                        limits,
                    )
                    if limit_failure:
                        chat.stop_streaming()
                        chat.finalize_turn_timing(0)
                        break
                    time.sleep(0.05)
            timed_out = chat.current_turn_timing is not None
            if timed_out:
                chat.stop_streaming()
                chat.finalize_turn_timing(0)

            full_answer = chat.chat_state.answers[0]
            calls = [c for c in full_answer.components if isinstance(c, ToolCall)]
            tool_results = [
                c.content for c in full_answer.components if isinstance(c, ToolResult)
            ]
            tools = [_tool_name(call) for call in calls]
            gated_calls = sum("[Output truncated:" in call.content for call in calls)
            answer = full_answer.get_text_only_content().strip()
            failures = _validate_agent_case(
                case,
                workspace,
                answer,
                tools,
                tool_results,
            )
            if case.get("requires_gated_call") and not gated_calls:
                failures.append("no tool-call argument crossed the call gate")
            if timed_out:
                failures.append("turn timed out")
            if limit_failure:
                failures.append(limit_failure)
            failures.extend(app_core.api.errors)

            timing = chat.chat_state.turn_timings[0] or {}
            usage = Usage(
                input_tokens=chat.session_input_tokens,
                cached_input_tokens=chat.session_cached_input_tokens,
                output_tokens=chat.session_output_tokens,
            )
            wall_seconds = float(timing.get("wall_ms", 0)) / 1000
            if wall_seconds <= 0:
                wall_seconds = time.perf_counter() - started
            result = {
                "id": case["id"],
                "passed": not failures,
                "failures": failures,
                "wall_seconds": round(wall_seconds, 3),
                **asdict(usage),
                "total_tokens": usage.total_tokens,
                "cost_usd": round(calculate_cost(usage, pricing), 8),
                "invocations": int(timing.get("invocations", 0)),
                "tool_calls": len(calls),
                "tools_used": tools,
                "gated_calls": gated_calls,
                "gated_results": sum(
                    "[Output truncated:" in result for result in tool_results
                ),
                "response_chars": len(answer),
            }
            if failures:
                result["failure_tool_trace"] = _failure_tool_trace(calls)
        finally:
            chat.cleanup_resources()
            if previous_workspace is None:
                os.environ.pop("ALPACA_WORKSPACE", None)
            else:
                os.environ["ALPACA_WORKSPACE"] = previous_workspace
    print(
        f"{result['id']}: {'PASS' if result['passed'] else 'FAIL'}, "
        f"{result['wall_seconds']:.3f}s, {result['invocations']} invocations, "
        f"{result['tool_calls']} tools, {result['total_tokens']} tokens, "
        f"${result['cost_usd']:.6f}",
    )
    return result


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    wall_times = [float(result["wall_seconds"]) for result in results]
    summary = {
        "case_count": len(results),
        "wall_seconds": round(sum(wall_times), 3),
        "mean_wall_seconds": round(statistics.mean(wall_times), 3),
        "p50_wall_seconds": round(statistics.median(wall_times), 3),
        "p95_wall_seconds": round(
            sorted(wall_times)[max(0, math.ceil(len(wall_times) * 0.95) - 1)],
            3,
        ),
        "input_tokens": sum(int(result["input_tokens"]) for result in results),
        "cached_input_tokens": sum(
            int(result["cached_input_tokens"]) for result in results
        ),
        "output_tokens": sum(int(result["output_tokens"]) for result in results),
        "total_tokens": sum(int(result["total_tokens"]) for result in results),
        "cost_usd": round(sum(float(result["cost_usd"]) for result in results), 8),
    }
    if any("passed" in result for result in results):
        summary.update(
            {
                "passed": sum(bool(result.get("passed")) for result in results),
                "failed": sum(not bool(result.get("passed")) for result in results),
                "invocations": sum(
                    int(result.get("invocations", 0)) for result in results
                ),
                "tool_calls": sum(
                    int(result.get("tool_calls", 0)) for result in results
                ),
                "gated_calls": sum(
                    int(result.get("gated_calls", 0)) for result in results
                ),
                "gated_results": sum(
                    int(result.get("gated_results", 0)) for result in results
                ),
            },
        )
    return summary


def compare_baseline(
    summary: dict[str, Any],
    baseline: dict[str, Any],
    threshold_percent: float,
) -> list[str]:
    regressions: list[str] = []
    baseline_summary = baseline["summary"]
    for metric in ("wall_seconds", "total_tokens", "cost_usd"):
        old = float(baseline_summary[metric])
        new = float(summary[metric])
        if old <= 0:
            continue
        change = (new - old) / old * 100
        if change > threshold_percent:
            regressions.append(
                f"{metric} increased {change:.1f}% ({old:g} -> {new:g})",
            )
    return regressions


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        help="Run only this case ID; repeat to select multiple cases",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--api-url",
        help="Existing Ollama-compatible proxy URL; required for non-Fireworks backends",
    )
    parser.add_argument("--input-cost-per-million", type=float)
    parser.add_argument("--cached-input-cost-per-million", type=float)
    parser.add_argument("--output-cost-per-million", type=float)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument(
        "--tool-profile",
        choices=("all", "suite"),
        default="all",
        help="Tool transport exposed to agent cases (default: all production tools)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        help="Fireworks sampling temperature (default: production client default, 0.7)",
    )
    parser.add_argument(
        "--max-tokens",
        type=int,
        help=f"Fireworks output-token ceiling per invocation (default: {DEFAULT_MAX_TOKENS_PER_INVOCATION})",
    )
    parser.add_argument(
        "--max-case-invocations",
        type=int,
        default=DEFAULT_MAX_CASE_INVOCATIONS,
    )
    parser.add_argument(
        "--max-case-tool-calls",
        type=int,
        default=DEFAULT_MAX_CASE_TOOL_CALLS,
    )
    parser.add_argument(
        "--max-identical-tool-calls",
        type=int,
        default=DEFAULT_MAX_IDENTICAL_TOOL_CALLS,
    )
    parser.add_argument(
        "--max-case-tokens",
        type=int,
        default=DEFAULT_MAX_CASE_TOKENS,
    )
    parser.add_argument(
        "--max-case-cost-usd",
        type=float,
        default=DEFAULT_MAX_CASE_COST_USD,
    )
    parser.add_argument(
        "--regression-threshold-percent",
        type=float,
        default=10.0,
        help="Fail when aggregate wall time, tokens, or cost exceeds the baseline by this percentage",
    )
    return parser.parse_args()


def _resolve_pricing(args: argparse.Namespace) -> dict[str, float]:
    overrides = (
        args.input_cost_per_million,
        args.cached_input_cost_per_million,
        args.output_cost_per_million,
    )
    if any(value is not None for value in overrides):
        if not all(value is not None for value in overrides):
            raise SystemExit(
                "Specify input, cached-input, and output costs together",
            )
        if any(float(value) < 0 for value in overrides):
            raise SystemExit("Token costs cannot be negative")
        return {
            "input": float(overrides[0]),
            "cached_input": float(overrides[1]),
            "output": float(overrides[2]),
        }
    pricing = MODEL_PRICING.get(args.model)
    if pricing is None:
        raise SystemExit(
            f"No verified pricing configured for {args.model!r}; provide "
            "--input-cost-per-million, --cached-input-cost-per-million, "
            "and --output-cost-per-million",
        )
    return pricing


def _select_cases(
    cases: list[dict[str, Any]],
    case_ids: list[str] | None,
) -> list[dict[str, Any]]:
    if not case_ids:
        return cases
    selected_ids = set(case_ids)
    known_ids = {str(case.get("id")) for case in cases}
    unknown = sorted(selected_ids - known_ids)
    if unknown:
        raise SystemExit("Unknown regression case(s): " + ", ".join(unknown))
    return [case for case in cases if case.get("id") in selected_ids]


def main() -> int:
    args = _parse_args()
    if args.temperature is not None and not 0 <= args.temperature <= 1:
        raise SystemExit("Temperature must be between 0 and 1")
    if args.max_tokens is not None and args.max_tokens < 1:
        raise SystemExit("--max-tokens must be at least 1")
    if (
        min(
            args.max_case_invocations,
            args.max_case_tool_calls,
            args.max_identical_tool_calls,
            args.max_case_tokens,
        )
        < 1
    ):
        raise SystemExit(
            "Case invocation, tool-call, repetition, and token limits must be positive",
        )
    if args.max_case_cost_usd <= 0:
        raise SystemExit("--max-case-cost-usd must be positive")
    if args.api_url and (args.temperature is not None or args.max_tokens is not None):
        raise SystemExit(
            "Generation settings are only supported by the built-in Fireworks proxy",
        )
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not 5 <= len(cases) <= 20:
        raise SystemExit("The regression suite must contain between 5 and 20 cases")
    cases = _select_cases(cases, args.case_ids)

    pricing = _resolve_pricing(args)
    limits = CaseLimits(
        max_invocations=args.max_case_invocations,
        max_tool_calls=args.max_case_tool_calls,
        max_identical_tool_calls=args.max_identical_tool_calls,
        max_tokens=args.max_case_tokens,
        max_cost_usd=args.max_case_cost_usd,
    )
    model_id = map_ollama_to_model(args.model)
    provider_model = args.model if args.api_url else model_id
    is_agent_suite = any(case.get("files") or case.get("large_file") for case in cases)
    suite_started = time.perf_counter()
    if args.api_url and is_agent_suite:
        results = [
            run_agent_case(
                case,
                args.api_url,
                args.model,
                pricing,
                args.tool_profile,
                limits,
            )
            for case in cases
        ]
    elif is_agent_suite:
        if not model_id.startswith("accounts/fireworks/"):
            raise SystemExit(
                "Running this backend requires its configured Ollama-compatible "
                "proxy via --api-url",
            )
        client: Any = _ConfiguredFireworksClient(
            FireworksClient(),
            args.temperature,
            args.max_tokens or DEFAULT_MAX_TOKENS_PER_INVOCATION,
        )
        with _ollama_harness_server(client) as api_url:
            results = [
                run_agent_case(
                    case,
                    api_url,
                    args.model,
                    pricing,
                    args.tool_profile,
                    limits,
                )
                for case in cases
            ]
    else:
        if args.api_url:
            raise SystemExit("--api-url is currently supported by agent suites only")
        client = FireworksClient()
        results = [
            run_case(client, case, model_id, pricing, SYSTEM_PROMPT) for case in cases
        ]
    summary = summarize(results)
    summary["elapsed_seconds"] = round(time.perf_counter() - suite_started, 3)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "provider_model": provider_model,
        "pricing_usd_per_million_tokens": pricing,
        "configuration": {
            "tool_profile": args.tool_profile if is_agent_suite else None,
            "tool_count": (
                len(
                    _RegressionAppCore(
                        "",
                        args.model,
                        args.tool_profile,
                    ).get_available_mcp_tools(),
                )
                if is_agent_suite
                else 0
            ),
            "temperature": (
                None
                if args.api_url
                else args.temperature if args.temperature is not None else 0.7
            ),
            "max_tokens": (
                None
                if args.api_url
                else args.max_tokens or DEFAULT_MAX_TOKENS_PER_INVOCATION
            ),
            "case_limits": asdict(limits) if is_agent_suite else None,
            "execution_policy": "efficient" if is_agent_suite else None,
        },
        "cases": results,
        "summary": summary,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print("\nAggregate:")
    print(json.dumps(summary, indent=2))

    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        regressions = compare_baseline(
            summary,
            baseline,
            args.regression_threshold_percent,
        )
        if regressions:
            print("\nRegressions:")
            for regression in regressions:
                print(f"- {regression}")
            return 1
    if any(not result.get("passed", True) for result in results):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
