# Offline regression harness tuning report

Date: 2026-08-23
Model: `glm-5p2` on Fireworks
Suite: 9 non-UI software-engineering cases

## Suite correction

The synthetic large-write case and the benchmark-only `compact-write` tool
transport were removed. They accounted for most of the previously reported
improvement but did not represent normal software-engineering work.

Two cases replaced it:

- **Secure archive extraction:** repair a path-traversal vulnerability while
  preserving valid nested extraction and ensuring validation happens before
  any member is written.
- **Asynchronous retry contract:** repair exception classification, exponential
  backoff, final-attempt behavior, and exception identity without changing the
  public API.

The harness now verifies that case specifications and tests remain byte-for-byte
unchanged. Editing cases may use either `internal_modify_file` or
`internal_write_file`; correctness does not depend on choosing one equivalent
editing mechanism.

## Meaningful configuration result

The retained configuration uses the full production tool catalog and adds only
a generic execution policy:

```bash
python offline_regression.py \
  --temperature 0.7 \
  --output model-efficient.json
```

The policy asks the agent to minimize unnecessary discovery, issue independent
tool calls together, and keep reasoning concise while preserving correctness.
Unlike compact-write, it contains no case-specific capability or answer. It now
lives in the Python app proxy's stable, cached global prompt, so both the real
app and this harness use it by default without harness-only prompt injection.
The previous production/efficient profile switch has been removed.

## Repeated A/B results

Each configuration was run three times. All 27 case executions passed under the
final behavioral criteria.

| Configuration | Aggregate wall time per run | Total tokens per run | Cost per run | Invocations per run | Tool calls per run |
| --- | ---: | ---: | ---: | ---: | ---: |
| Production instructions, run 1 | 249.867 s | 180,910 | $0.142575 | 43 | 49 |
| Production instructions, run 2 | 199.025 s | 159,194 | $0.115054 | 38 | 42 |
| Production instructions, run 3 | 228.784 s | 155,077 | $0.122779 | 38 | 42 |
| **Production mean** | **225.892 s** | **165,060** | **$0.126802** | **39.7** | **44.3** |
| Efficient instructions, run 1 | 142.546 s | 137,334 | $0.083605 | 35 | 39 |
| Efficient instructions, run 2 | 197.303 s | 150,108 | $0.111694 | 36 | 41 |
| Efficient instructions, run 3 | 183.916 s | 143,448 | $0.104073 | 35 | 40 |
| **Efficient mean** | **174.588 s** | **143,630** | **$0.099791** | **35.3** | **40.0** |

Mean change with efficient instructions:

- Aggregate wall time: **22.7% lower**
- Total tokens: **13.0% lower**
- Estimated cost: **21.3% lower**
- Invocations: **10.9% fewer**
- Tool calls: **9.8% fewer**
- Pooled per-case P50 latency: **29.1% lower** (16.147 s to 11.449 s)
- Pooled per-case P95 latency: **8.4% higher** (59.630 s to 64.638 s)
- Correctness: **27/27 for both configurations**

One efficient run was initially marked failed because it rewrote `retry.py`
with `internal_write_file` rather than `internal_modify_file`. Its code passed
all behavioral tests and preserved the protected files. The final harness
correctly accepts either edit tool, and the run's original performance data is
retained here rather than replaced with a more favorable sample.

## Cross-model confirmation

The same three-run A/B was run with Kimi K3 using Fireworks Standard pricing
of $3.00 / $0.30 / $15.00 per million input / cached-input / output tokens.
The table retains every run, including timeouts.

| Model | Configuration | Passed | Mean wall/run | Pooled P50 | Pooled P95 | Max case | Mean tokens/run | Mean cost/run | Mean invocations/run | Mean tools/run |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GLM 5.2 | Before: production | 27/27 | 225.892 s | 16.147 s | 59.630 s | 67.391 s | 165,060 | $0.126802 | 39.7 | 44.3 |
| GLM 5.2 | After: efficient | 27/27 | 174.588 s | 11.449 s | 64.638 s | 66.946 s | 143,630 | $0.099791 | 35.3 | 40.0 |
| Kimi K3 | Before: production | 27/27 | 190.984 s | 18.222 s | 52.674 s | 65.410 s | 164,438 | $0.305693 | 40.7 | 45.3 |
| Kimi K3 | After: efficient | **26/27** | **231.526 s** | 13.884 s | 29.545 s | **301.047 s** | **836,840** | **$0.640404** | **65.3** | **71.3** |

| Model | Wall | Tokens | Cost | Invocations | Tool calls | Verdict |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| GLM 5.2 | **-22.7%** | **-13.0%** | **-21.3%** | **-10.9%** | **-9.8%** | Faster and cheaper; tail unchanged/slightly worse |
| Kimi K3 | **+21.2%** | **+408.9%** | **+109.5%** | **+60.7%** | **+57.4%** | Regression; do not enable |

Kimi's efficient run 2 repeatedly called `internal_read_file_range` in the
gated-output case: 95 invocations, 95 tool calls, 301.047 seconds, and 2.23
million tokens before timeout. The other two efficient Kimi runs passed and
were fast, but that does not offset a 1-in-3 catastrophic failure rate. P95
also hides this single outlier in a 27-case pool, which is why the table includes
maximum case latency and correctness.

The failure exposed two interacting production-path weaknesses. The gate is
applied to the JSON-serialized MCP result rather than its formatted text, so an
800-line file becomes one giant escaped JSON string in a nine-line temporary
file. The truncation notice reports those misleading nine lines, points at that
serialized file, and tells the model to inspect it with `read_file_range`. A
model that follows the pointer can create another oversized result and another
gate notice. Every tool completion then starts another continuation; there is
no repeated-call or turn-level tool budget before the 300-second timeout.

The failed run did not retain tool arguments, so the exact ranges cannot be
reconstructed. Three capped diagnostic reruns all succeeded by correctly
reading the original `events.log` around lines 700-800. The recursive temp-file
path is therefore a strongly supported trigger, not a proven call-by-call trace
of that stochastic failure. Future reports should retain bounded diagnostics
for failed tool loops.

## Interpretation

This is materially stronger evidence than the removed large-write result: the
gain appears across realistic debugging and implementation work and uses the
production tool contract. It is **not model-agnostic**. The policy is a credible
GLM-specific candidate, but the Kimi result disproves promoting it as a global
default. Instruction profiles need per-model qualification, a hard loop budget,
and an acceptance gate covering correctness and maximum latency—not only
aggregate means or P95.

## Harness reliability fixes

Raw provider SSE events, tool schemas, and messages now use debug logging
instead of synchronous stdout printing, avoiding pipe backpressure. Regression
HTTP workers are daemonized so a timed-out provider request cannot indefinitely
block server shutdown. Reports also record generation settings, tool profiles,
the execution policy, and actual end-to-end suite elapsed time.

The gate now saves formatted multiline tool text rather than JSON-escaped MCP
envelopes, so `read_file_range` can page the advertised temporary file. The
Fireworks harness defaults to an 8,000-token output ceiling per invocation and
stops each case at the first of: 20 invocations, 20 tool calls, three identical
calls, 250,000 total tokens, or $0.25 estimated cost. Failed cases include only
the last 20 tool calls, with long argument strings truncated.

## Post-fix rerun

One complete paired run per model passed all 36 case executions. These are
single stochastic runs and do not replace the three-run evidence above, but
they verify the corrected gate and spill controls end to end.

| Model | Configuration | Passed | Wall | P50 | P95 | Tokens | Cost | Invocations | Tools |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Kimi K3 | Production | 9/9 | 163.171 s | 15.502 s | 41.624 s | 130,480 | $0.280454 | 35 | 41 |
| Kimi K3 | Efficient | 9/9 | 133.583 s | 14.659 s | 25.603 s | 132,184 | $0.235255 | 36 | 40 |
| GLM 5.2 | Production | 9/9 | 279.467 s | 14.034 s | 99.517 s | 146,988 | $0.142307 | 35 | 39 |
| GLM 5.2 | Efficient | 9/9 | 152.546 s | 14.526 s | 35.026 s | 130,195 | $0.082218 | 33 | 38 |

Post-fix efficient-versus-production change:

| Model | Wall | P50 | P95 | Tokens | Cost | Tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Kimi K3 | **-18.1%** | **-5.4%** | **-38.5%** | +1.3% | **-16.1%** | -2.4% |
| GLM 5.2 | **-45.4%** | +3.5% | **-64.8%** | **-11.4%** | **-42.2%** | -2.6% |

The formerly failing Kimi gate case completed in 7.887 seconds with two tool
calls, 11,047 tokens, and $0.018488. No circuit breaker fired in any post-fix
case.

Five additional targeted Kimi efficient reruns of that case also passed 5/5.
Every run used exactly three invocations and two tool calls; mean wall time was
7.439 seconds, mean usage was 11,031 tokens, and total validation cost was
$0.094251. The targeted `--case` selector avoided rerunning the other eight
cases solely to probe this failure mode.

## Tool-contract and continuation tuning

Date: 2026-08-24
Measurement model: GLM 5.2
Suite: 19 non-UI software-engineering cases

Two model-agnostic production changes were retained:

- Tool descriptions now reserve `internal_list_files` for actual discovery and
  tell the model to read user-named files directly. The write and modify tools
  also state that their successful responses include a verified content hash,
  so a follow-up read is unnecessary unless content inspection is needed.
- Model-facing continuations unwrap persisted MCP result envelopes to their
  text. Persistence and UI folds retain the original transport object. The
  context-retention byte budget now measures the same plain text sent to the
  model instead of counting JSON syntax that is no longer replayed.

The baseline combines the existing 14-case and additional 5-case GLM reports,
which have the same production configuration. The optimized row is one full
acceptance run, so it establishes a candidate rather than a stochastic
confidence interval.

| Configuration | Passed | Wall | P50 | P95/max | Tokens | Cost | Invocations | Tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing harness | 19/19 | 590.450 s | 25.502 s | 87.904 s | 345,645 | $0.289102 | 82 | 100 |
| Tool contracts + plain continuation results | 19/19 | 531.702 s | 22.376 s | 94.729 s | 329,756 | $0.248715 | 80 | 97 |
| Change | unchanged | **-9.9%** | **-12.3%** | +7.8% | **-4.6%** | **-14.0%** | **-2.4%** | **-3.0%** |

An 11-case targeted run after the tool-description change, before result
unwrapping, reduced unnecessary directory listings from 10 to 6, tool calls by
7.9%, and invocations by 5.8%, with 11/11 cases passing. This supports the
intended mechanism even though stochastic output length made that run's wall
time 2.7% and cost 4.1% worse.

Two configurations were rejected:

- Limiting the catalog to the six tools named by this suite cut tokens 17.0%
  but passed only 17/19 and increased wall time 19.9% and cost 10.7%. Static
  benchmark-aware tool removal is not a safe substitute for general tool
  selection.
- Temperature 0 passed a targeted five-case hard set but was neutral to worse
  against the matching baseline: wall time +3.3%, tokens +1.1%, and cost +3.0%.

The optimized GLM run improved aggregate and median metrics but not the tail.

### Kimi K3 confirmation

A contemporaneous full-suite Kimi K3 A/B compared unchanged commit `f1a485b`
with the optimized working tree. Both used temperature 0.7, the full 14-tool
catalog, an 8,000-token invocation limit, and the same case circuit breakers.

| Configuration | Passed | Wall | P50 | P95/max | Tokens | Cost | Invocations | Tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing harness | 18/19 | 310.190 s | 15.583 s | 34.471 s | 318,199 | $0.563977 | 84 | 97 |
| Tool contracts + plain continuation results | 19/19 | 296.935 s | 14.557 s | 30.400 s | 317,754 | $0.511552 | 86 | 98 |
| Change | +1 pass | **-4.3%** | **-6.6%** | **-11.8%** | **-0.1%** | **-9.3%** | +2.4% | +1.0% |

Cross-model change from the existing harness:

| Model | Correctness | Wall | P50 | P95/max | Tokens | Cost | Invocations | Tools |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GLM 5.2 | 19/19 → 19/19 | **-9.9%** | **-12.3%** | +7.8% | **-4.6%** | **-14.0%** | **-2.4%** | **-3.0%** |
| Kimi K3 | 18/19 → 19/19 | **-4.3%** | **-6.6%** | **-11.8%** | **-0.1%** | **-9.3%** | +2.4% | +1.0% |

The baseline stopped after reading the three supplied files in
`mocked_http_integration`; it neither edited nor tested the implementation.
Two targeted reruns passed, making baseline 2/3 and optimized 3/3 for that case
across the full run plus reruns. The full-run correctness difference therefore
must not be presented as deterministic. It does confirm that the optimized
harness is compatible with Kimi and can complete all 19 cases. The paired
aggregate improved latency and cost even though optimized Kimi completed the
case that baseline abandoned. Repeated full-suite runs are still required for
a confidence interval, especially because the GLM and Kimi token and tail
effects differ.
