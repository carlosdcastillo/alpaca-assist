# SURFACE_PLAN — Live remote app panel (X11/VNC) in the conversation UI

Goal: a persistent panel in the app window showing a **live, interactive** GUI
application running on a Pack tab's remote host. The human can drive it with
mouse and keyboard; the model can observe it and drive it under a lease.

This is the implementation plan. It resolves the three items left open in the
design discussion, then sequences the build.

Scope boundary for v1: **Pack tabs only.** Windows has no local X server, so
local-tab support would drag in WSLg or a container and is deferred.

---

## 0. Decisions resolved

### 0.1 The tunnel is a named component

`core/surface_tunnel.py`, new file, two classes:

- `SurfaceTunnel` — owns one `ssh -N -L 127.0.0.1:<local>:127.0.0.1:<remote>`
  subprocess, exposes `local_port`, `alive`, and `close()`.
- `SurfaceTunnelManager` — owned by `PackTab`, keyed by `surface_id`, closes
  everything in `PackTab.cleanup_resources()` and in `PackTab._on_disconnect()`.

This is a **second** SSH connection, separate from the one `PackTransport`
already holds open for JSON-RPC (`core/pack_transport.py:92`). That connection's
stdio is fully consumed by the bridge relay, so it cannot also carry a
port-forward.

> **Gotcha:** the usual fix (`-o ControlMaster=auto -o ControlPath=…`) is **not
> available** — Windows OpenSSH does not implement connection multiplexing. Each
> surface therefore costs one extra SSH authentication. `BatchMode=yes` is
> already the convention here, so auth is key-based and non-interactive; this is
> a startup-latency cost, not a UX cost.

Local port allocation: bind `127.0.0.1:0`, read the port, close, hand it to
`ssh`. Racy in principle; `-o ExitOnForwardFailure=yes` turns a lost race into a
clean immediate failure, and the manager retries once.

### 0.2 MCP server placement — the problem dissolves

The design note worried that `mcp_manager.py:47` only speaks stdio, so a
"remote MCP server" needs either `ssh host python …` as the configured command
or a local server that shells out. **Neither is necessary.**

For a Pack tab, the `ChatTab` and its whole tool stack run *inside
`pack_daemon.py` on the remote host*. `_prepare_mcp_config`
(`pack_daemon.py:409`) snapshots `mcp_servers.json` into the session directory
with absolutised paths, and `AppCore.load_mcp_servers` (`core/app_core.py:80`)
reads it relative to the daemon's cwd. So an ordinary stdio MCP server listed in
`mcp_servers.json` is **already launched on the remote host, by the daemon, in
the daemon's session directory** — the same machine as the display.

Therefore: `surface_mcp_server.py` is a plain stdio MCP server, added to
`mcp_servers.json` like any other. No new transport, no `ssh` in the command
line, no changes to `mcp_manager.py`. It reaches the surface supervisor over a
Unix socket in the session directory.

Consequence worth noting: the same server is a no-op on local (Windows) tabs,
where it should report "no display available" rather than fail to start.

### 0.3 Session lifetime is anchored to the pack daemon

The surface supervisor lives **in the `pack_daemon.py` process**, not in the CLI
media server and not in the panel. Justification:

- The daemon is exactly per-Pack-tab, which is the granularity the panel wants.
- It already survives SSH drops and app restarts by design
  (`core/pack_transport.py:212`), so a surface survives a network blip the same
  way the conversation does. That is the correct behaviour, not an accident.
- It is on the host with the display, so it can `waitpid` its children instead of
  guessing about them.

Orphan control, since the daemon deliberately outlives the local app:

1. `SurfaceSupervisor` registers an `atexit` + `SIGTERM` handler that kills its
   process group.
2. Each surface writes `~/.alpaca_pack/<session>/surfaces/<id>.json` with pids.
   On daemon start, a reaper scans that directory and kills anything still alive
   from a previous daemon incarnation.
3. Idle reaper: a surface with no client attached and no input for
   `SURFACE_IDLE_TIMEOUT` (default 30 min) is torn down. This is the real
   backstop, because closing a Pack tab locally does **not** stop the remote
   daemon (`core/pack_tab.py:694`) and never has.

The panel also gets an explicit **Stop app** button, which is the intended way
to end a surface.

---

## 1. Component inventory

### New files

| Path | Role |
|---|---|
| `core/surface_protocol.py` | Sentinel encode/parse for the surface tool result, mirroring `video_tool_result.py`. Shared local/remote. |
| `core/surface_supervisor.py` | Remote-side. Owns `Xvfb` + `x11vnc` + `websockify` + the app process per surface. Lease state. Snapshot and input injection. |
| `core/surface_tunnel.py` | Local-side. `SurfaceTunnel` / `SurfaceTunnelManager` (§0.1). |
| `surface_mcp_server.py` | Stdio MCP server exposing the model-facing tools. Talks to the supervisor over a Unix socket. |
| `web/js/components/SurfacePanel.js` | `<surface-panel>` custom element: noVNC canvas, lease banner, toolbar. |
| `web/js/lib/novnc/` | Vendored noVNC ES modules (`core/rfb.js` and its deps). |
| `web/css/surface.css` | Panel + splitter styling. |

### Changed files

| Path | Change |
|---|---|
| `pack_daemon.py` | Construct `SurfaceSupervisor`; add `surface_*` methods to `make_dispatcher`. |
| `core/pack_tab.py` | `surface_open` / `surface_close` / `surface_list` / `surface_lease_touch` proxies; own the `SurfaceTunnelManager`; tear it down in `cleanup_resources` and `_on_disconnect`. |
| `webview_api.py` | JS-callable `surface_*` methods, following the shape of `get_video_chunk` (`webview_api.py:1146`) — duck-type on `hasattr(tab, "surface_open")` so local tabs degrade to a clean error. |
| `web/index.html` | Panel container inside `.workspace`, a second splitter, `surface.css`, `SurfacePanel.js`. |
| `web/js/app.js` | Panel show/hide on tab switch; wire the splitter. |
| `web/js/components/ToolFolds.js` | Recognise the surface sentinel, render the handle card. |
| `mcp_servers.json.example` | Document the `alpaca-surface` entry. |
| `agents/SETUP.md` | `x11vnc`, `python-websockify` in the pacman list. |
| `AGENTS.md` | New section on the surface panel's lifecycle rules. |

### Deliberately unchanged

`chat_state.py` gets **no new component type**. A surface appears in the
transcript as an ordinary `tool_result` whose body carries the surface sentinel,
so all existing persistence, export, compaction, and re-render paths work
untouched. This is the same trick videos use.

---

## 2. Phase 0 — Spikes (half a day, do these first)

These three unknowns can each invalidate a chunk of the plan. None takes long.

**S1 — Can the page reach the network at all from `file://`?** The app loads
from `file://` (`webview_app.py:69`) with no CSP. Two separate questions, and
they share one fix, so test them together in the WebView2 devtools console
(`DEBUG=1` already enables them, `webview_app.py:104`):

1. `new WebSocket("ws://127.0.0.1:PORT")` against a throwaway echo server.
   WebSockets are not subject to same-origin the way `fetch` is, so this
   probably passes.
2. `await import("./js/lib/novnc/core/rfb.js")` from the page. This one
   probably **fails**. Chromium treats a `file://` page as an opaque origin and
   blocks ES module loads, static *and* dynamic, on CORS grounds. Nothing in
   this codebase uses `type="module"` or dynamic `import()` today, so it has
   never been exercised.

*Fix for either:* pywebview's `http_server=True` with a relative URL —
verified present in the installed pywebview 6.1 (`webview.start(...,
http_server=True, http_port=...)`). The theme query-string trick still works
over `http://`, so the blast radius is `_setup_window` only.

Because (2) is the likely outcome, **plan on serving over `http://`** and treat
staying on `file://` as the surprise. Doing this in Phase 0, before any surface
code exists, is a contained change; discovering it in Phase 3 is not.

**S2 — Remote stack.** On the Arch host, verify:
```bash
Xvfb :77 -screen 0 1280x800x24 &
DISPLAY=:77 xeyes &
x11vnc -display :77 -localhost -rfbport 5977 -rfbauth /tmp/pw -forever -shared -nopw_off
```
Then decide the WebSocket hop: x11vnc has built-in WebSocket support in recent
builds; if it works, drop `websockify` entirely. If not, `websockify
127.0.0.1:6077 127.0.0.1:5977`. **Confirm which before writing the supervisor**,
because it changes the process count per surface from three to four.

**S3 — Second SSH.** With a Pack tab already connected, confirm a concurrent
`ssh -N -L …` to the same host succeeds under `BatchMode=yes`, and measure how
long it takes to become connectable. That number sets the panel's spinner
budget.

---

## 3. Phase 1 — Surface lifecycle on the remote host (2–3 days)

`core/surface_supervisor.py`. One `Surface` owns:

```
Xvfb :<n> -screen 0 <W>x<H>x24 +extension RANDR
x11vnc -display :<n> -localhost -rfbport <p> -rfbauth <file> -forever -shared
[websockify 127.0.0.1:<wsp> 127.0.0.1:<p>]     # only if S2 says so
<the application>                               # DISPLAY=:<n>
```

Display number and ports are allocated by the supervisor, not fixed. Note `:99`
is already spoken for by the RUNBOOK's own headless-app workflow, so start the
search at `:100`.

`+extension RANDR` costs nothing now and is what makes dynamic resize possible
later.

Dispatcher methods added to `pack_daemon.make_dispatcher` (`pack_daemon.py:261`):

| Method | Params | Returns |
|---|---|---|
| `surface_open` | `spec` (argv or named profile), `width`, `height` | `surface_id`, `ws_port`, `password`, `width`, `height` |
| `surface_close` | `surface_id` | `{ok}` |
| `surface_list` | — | array of descriptors |
| `surface_snapshot` | `surface_id` | base64 PNG, bounded |
| `surface_input` | `surface_id`, `events[]` | `{ok}` or lease refusal |
| `surface_lease_acquire` / `_release` / `_touch` | `surface_id`, `holder`, `ttl` | lease state |

`surface_snapshot` reuses `import -window root` — already installed and already
the documented capture method (`agents/RUNBOOK.md:148`) — and returns through
`image_tool_result.encode_image_result`, so it renders in the existing image
fold with zero new UI. `surface_input` uses `xdotool`, likewise already
installed and already the documented input method
(`agents/RUNBOOK.md:124`).

That combination means **the observation plane and a working model-input path
exist at the end of Phase 1**, before any pixels stream. Useful on its own, and
it de-risks the host setup independently of the panel.

Password handling: 8+ random bytes, `x11vnc -storepasswd` into
`<session>/surfaces/<id>.pw` at mode `0600`, returned to the local side over the
existing SSH channel only. Never `-nopw`.

**Tests:** `tests/test_surface_supervisor.py` with fake `subprocess.Popen`, plus
`tests/test_pack_daemon.py` additions for the new dispatcher methods (the file
already exists and covers the dispatcher).

---

## 4. Phase 2 — Tunnel and panel skeleton (2 days)

`core/surface_tunnel.py` per §0.1. `PackTab` proxies. `WebViewAPI.surface_open`
composes: daemon call → tunnel → return `ws://127.0.0.1:<local_port>` plus the
password to JS.

Panel shell, **no noVNC yet** — just a `<surface-panel>` element that opens the
WebSocket, holds it, shows connection state, and renders a static snapshot
fetched via `surface_snapshot`. The point of this phase is to prove transport,
tunnel lifecycle, and element lifecycle across tab switches, not to render
video.

DOM placement is the load-bearing decision here:

```
.workspace
  .main-content        ← chat-container, splitter, input-area (unchanged)
  #surface-splitter    ← new vertical splitter
  #surface-dock        ← new; hosts <surface-panel>, one per open surface
```

The dock is a **sibling of `.main-content`**, outside `#chat-container`. This is
the whole reason a live surface can't be a fold: `chatDisplay.clear()` wipes the
chat container on every tab switch and `_renderFullAnswer` rebuilds it
(`AGENTS.md`, "Fold Re-injection on Tab Switch"). Anything inside it is
destroyed and recreated. The dock is never touched by that path; panels are
created once, keyed by `tab_id`, and merely hidden when their tab is not active.

> **Do not** push framebuffer data through `_safe_evaluate_js` / the 50 ms
> `get_pending_js()` poller (`webview_api.py:73`). That queue is for chat tokens.
> Pixels go over the WebSocket, which bypasses Python entirely.

noVNC ships as ES modules while every other script here is a classic
`<script>` tag (`web/index.html:439`). Rather than convert anything, load it
lazily from `SurfacePanel.js`:

```js
const { default: RFB } = await import("../lib/novnc/core/rfb.js");
```

No build step, no change to the existing load order, and noVNC costs nothing
until a surface is actually opened — **but this works only once the page is
served over `http://`** (S1). A dynamic `import()` is still a module load, so
it is blocked from a `file://` page exactly like a static one; laziness buys
timing, not an exemption. If S1's `http_server=True` switch is rejected for
some reason, the alternative is bundling noVNC to a classic IIFE script with
esbuild/rollup, which introduces the build step this repo currently doesn't
have. Prefer the server switch.

---

## 5. Phase 3 — noVNC (4–5 days, not 3)

Swap the placeholder for a real `RFB` instance inside the panel's shadow root.

This is where pointer, keyboard, cursor compositing, clipboard, reconnect, and
scaling all arrive at once — which is exactly why the MJPEG-style skeleton in
Phase 2 is deliberately kept dumb. Writing an input stack by hand and then
deleting it would be the main way to waste a week here.

Settings: `scaleViewport = true`, `resizeSession = false` (fixed geometry in
v1), `credentials.password` from `surface_open`.

The honest estimate is above the 3 days the design note guessed, because the
integration is the small part. The rest is: reconnect after the tunnel dies vs.
after the daemon dies vs. after the surface dies (three different messages),
panel state across tab switch, and geometry/scaling behaviour in a resizable
dock.

**Tests:** `tests/js/` jest specs for the element's state machine with `RFB`
mocked. Pixel behaviour is not unit-testable and shouldn't be faked.

---

## 6. Phase 4 — Control lease (2 days)

State is authoritative in the supervisor, because it is the one place both
actors can be observed.

- **Human input never blocks.** It goes noVNC → x11vnc directly and the
  supervisor is not in that path. Correct by construction.
- The panel calls `surface_lease_touch` on first interaction and then throttled
  (say every 2 s while active). That preempts any model lease immediately.
- **Model input is refused** by `surface_input` while a human lease is live,
  with a message naming the expiry.
- Every lease has a TTL, so a stalled model cannot deadlock the surface.
- The banner lives in the panel's shadow DOM: "You have control" / "Model has
  control until 14:32 — click to take over".

Putting the banner in shadow DOM alongside the canvas is the concrete payoff for
choosing a custom element over an iframe; in an iframe it would have been a
separate overlay fighting for alignment.

---

## 7. Phase 5 — Model channel (2–3 days)

`surface_mcp_server.py`, registered in `mcp_servers.json` as `alpaca-surface`,
launched on the remote host by the daemon (§0.2):

| Tool | Notes |
|---|---|
| `surface_open` | Named profiles preferred over raw argv. |
| `surface_snapshot` | Returns native `ImageContent` + an `alpaca://image/...` reference, exactly as `cli_media_mcp_server.py:92` does. |
| `surface_click` / `surface_type` / `surface_key` | Lease-checked. |
| `surface_close` | |

Coordinates carry a `seq` and the resolution they were computed against;
the supervisor rejects input whose `seq` is stale, which catches the
click-after-the-screen-changed class of bug.

**Accessibility tree is explicitly a stretch goal, not v1.** Server-side target
resolution ("click the node named Save") is the right end state and does kill
coordinate drift outright, but AT-SPI reachable from inside the supervisor for
an arbitrary toolkit is not free, and the design note's "free if the toolkit
exposes a11y" is doing a lot of work. Ship coordinates plus `seq` first; add the
tree when there is a real app to test it against.

---

## 8. Persistence and the transcript

`core/surface_protocol.py` mirrors `video_tool_result.py:28` exactly:

```
@@ALPACA_SURFACE_RESULT@@<surface_id>@@ALPACA_FIELD@@<w>x<h>@@ALPACA_FIELD@@<description>
```

`ToolFolds.js` recognises it (alongside the existing video branch at
`ToolFolds.js:150`) and renders a small card: app name, geometry, and a
**Show panel** button. Clicking calls `surface_list`; if the surface is still
alive the dock attaches to it, otherwise the card shows "session ended".

What is persisted is a *descriptor*, never a session. A conversation reopened
next week shows the card, the card reports the surface is gone, and nothing
breaks. If a still image is wanted in the record, the model calls
`surface_snapshot`, which lands as an ordinary image fold — no new persistence
machinery.

---

## 9. Security posture

- x11vnc bound `-localhost` only; nothing listens on a routable interface at
  either end.
- Per-surface generated password in a `0600` file, delivered only over the
  existing SSH JSON-RPC channel.
- Tunnel is `-L 127.0.0.1:local:127.0.0.1:remote` — the local end is loopback
  too, so other users on the Windows box cannot reach it.
- `-shared` is intentional: it is what would let a human and an RFB-driving
  model watch the same display. In v1 the model uses `xdotool`, so this is
  forward-compat only.
- `surface_open` should take **named profiles** from a config file in preference
  to arbitrary argv, so the model cannot ask for a shell on the remote host by
  spelling it as an app.

---

## 10. Sequencing and estimate

| Phase | Days | Standalone value if we stop here |
|---|---|---|
| 0 Spikes | 0.5 | Three unknowns retired |
| 1 Supervisor + snapshot + xdotool input | 2–3 | Model can see and drive a remote GUI. Real capability. |
| 2 Tunnel + panel skeleton | 2 | Transport and lifecycle proven |
| 3 noVNC | 4–5 | **The actual ask.** Human drives a live app. |
| 4 Control lease | 2 | Human and model can share safely |
| 5 MCP tools | 2–3 | Model channel is first-class |

**12–15 working days** to the full thing; **~5 days** to the first genuinely
useful checkpoint (end of Phase 1); **~9 days** to a live interactive panel.

If time gets cut, cut Phase 5 and keep the lease — a live panel the human drives
with the model watching through snapshots is a coherent product. Cutting the
lease instead leaves two actors fighting over one pointer.

---

## 11. Known risks

1. **S1 forces a move off `file://`.** Expected, not feared: the ES module load
   noVNC needs is blocked from an opaque origin. Mitigated by
   `http_server=True`, localised to `_setup_window`, and done in Phase 0 before
   anything depends on it. The residual risk is that serving over `http://`
   perturbs something unrelated — `window.pythonAPI` injection, the theme
   query string, or relative asset paths — so re-run the existing jest suite
   and a manual smoke test right after the switch rather than at the end.
2. **Second SSH auth is slow** on this host, making surface open feel laggy.
   Measured in S3. No multiplexing escape hatch on Windows; the fallback is to
   pre-warm the tunnel when a Pack tab connects rather than when a surface
   opens.
3. **Orphaned `Xvfb`.** Three independent guards (§0.3) because the daemon
   outliving the app is a deliberate feature, not a bug to fix.
4. **Xvfb has no window manager**, so apps get no decorations, no focus
   management, and `xdotool windowactivate` fails — already documented at
   `agents/RUNBOOK.md:253`. For v1, run one app per surface fullscreen. If real
   apps need decorations, add a lightweight WM to the supervisor's process set.
5. **Estimate risk concentrated in Phase 3.** Everything before it is
   subprocess plumbing with clear success criteria; noVNC integration is where
   the unknowns actually live.
