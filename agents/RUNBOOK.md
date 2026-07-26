# RUNBOOK — Starting and operating pywebview_demo on a remote Linux machine

This runbook assumes the project is already deployed and the Python
environment is already configured. See `SETUP.md` for one-time installation
steps.

The example uses `192.168.0.58` (Omarchy / Arch Linux) as the target host.

---

## Step 1 — Start Xvfb (virtual framebuffer)

Start a virtual X server on display `:99` at 1400x900x24:

```bash
nohup Xvfb :99 -screen 0 1400x900x24 >/tmp/xvfb.log 2>&1 &
disown
```

Verify:

```bash
ls /tmp/.X99-lock
# Should exist

DISPLAY=:99 xdotool getdisplaygeometry
# Should print: 1400 900
```

If display `:99` is already in use, either reuse it or remove the stale lock:

```bash
rm /tmp/.X99-lock
nohup Xvfb :99 -screen 0 1400x900x24 >/tmp/xvfb.log 2>&1 &
```

---

## Step 2 — Start the Ollama emulator server

`anthropic_ollama_server.py` emulates an Ollama API endpoint but routes
requests to Claude (via Anthropic) or GLM/Kimi (via Fireworks AI). It must be
running on port 11434 before starting the webview app.

```bash
cd ~/pywebview_demo
set -a && . ./.env && set +a
nohup .venv-sys/bin/python anthropic_ollama_server.py >/tmp/ollama_server.log 2>&1 &
disown
```

> The `set -a && . ./.env && set +a` pattern exports all variables from `.env`
> into the environment. The server reads `ANTHROPIC_API_KEY` and
> `FIREWORKS_API_KEY` from `os.environ`.

Verify the server is listening:

```bash
ss -tlnp | grep 11434
# Should show a python process LISTEN on 0.0.0.0:11434

curl -s http://localhost:11434/api/tags | head -c 100
# Should return JSON with a "models" array
```

Check the log for successful initialization:

```bash
cat /tmp/ollama_server.log
# Expected:
# Fireworks client initialized (kimi-k2p5 available)
# Local client initialized (qwen3.6:27b available via 192.168.0.125:11434)
# Ollama emulator server running on port 11434
```

If port 11434 is already in use, kill the old process first:

```bash
kill $(ss -tlnp | grep 11434 | grep -oP 'pid=\K\d+')
```

---

## Step 3 — Start the PyWebView app

Launch the app with `DISPLAY=:99` so it renders into the virtual framebuffer:

```bash
cd ~/pywebview_demo
set -a && . ./.env && set +a
DISPLAY=:99 nohup .venv-sys/bin/python webview_app.py >/tmp/webview_app.log 2>&1 &
disown
```

Wait a few seconds for the window to appear, then verify:

```bash
# Process is running
ps aux | grep webview_app | grep -v grep

# Window exists on the Xvfb display
DISPLAY=:99 xdotool search --name "Alpaca Assist" getwindowname %@ getwindowgeometry
# Expected:
# Alpaca Assist - New Chat
# Window <id>
#   Position: 0,0 (screen: 0)
#   Geometry: 1400x900
```

MCP connection failures in the log are expected and harmless (see
Troubleshooting below).

---

## Step 4 — Send a prompt via xdotool

Type a prompt into the app's input area and submit it:

```bash
# Position the window
DISPLAY=:99 xdotool search --name "Alpaca Assist" windowmove 0 0 windowsize 1400 900

# Click in the input textarea (approximately center-bottom)
DISPLAY=:99 xdotool mousemove 700 820 click 1
sleep 1

# Type the prompt
DISPLAY=:99 xdotool type "Say hello in one sentence."
sleep 1

# Submit with Ctrl+Enter
DISPLAY=:99 xdotool key ctrl+Return
```

Wait for the model to respond (5-15 seconds depending on the model):

```bash
sleep 15
```

---

## Step 5 — Take a screenshot

Use ImageMagick's `import` command to capture the full virtual framebuffer:

```bash
DISPLAY=:99 import -window root /tmp/screenshot.png
```

Verify:

```bash
identify /tmp/screenshot.png
# Expected: /tmp/screenshot.png PNG 1400x900 1400x900+0+0 8-bit sRGB
```

Copy the screenshot back to the local machine:

```bash
ssh 192.168.0.58 'cat /tmp/screenshot.png' > screenshot.png
```

---

## Step 6 — Send a prompt directly via curl (bypassing the UI)

To test the model without the UI, send a request directly to the Ollama
emulator:

```bash
ssh 192.168.0.58 'curl -s http://localhost:11434/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"model\":\"glm-5p2\",\"messages\":[{\"role\":\"user\",\"content\":\"Say hello in one sentence.\"}],\"stream\":false}"'
```

The response will be a series of JSON lines (streamed) ending with a
`"done": true` line.

---

## Available models

| Short name                         | Backend                                      |
|------------------------------------|----------------------------------------------|
| `us.anthropic.claude-opus-4-5-...` | Anthropic API (claude-opus-4-5-20251101)     |
| `us.anthropic.claude-sonnet-4-5-...` | Anthropic API (claude-sonnet-4-5-20250929) |
| `glm-5p1`                          | Fireworks AI (accounts/fireworks/models/glm-5p1) |
| `glm-5p2`                          | Fireworks AI (accounts/fireworks/models/glm-5p2) |
| `kimi-k2p5`                        | Fireworks AI (accounts/fireworks/models/kimi-k2p5) |
| `kimi-k2p6`                        | Fireworks AI (accounts/fireworks/models/kimi-k2p6) |
| `kimi-k2p7-code`                   | Fireworks AI (accounts/fireworks/models/kimi-k2p7-code) |
| `qwen3.6:27b`                      | Local Ollama at 192.168.0.125:11434          |
| `qwen3.6:35b`                      | Local Ollama at 192.168.0.125:11434          |

---

## Cleanup

To stop all running processes:

```bash
ssh 192.168.0.58 'pkill -f webview_app; pkill -f anthropic_ollama_server; pkill Xvfb'
```

---

## Troubleshooting

### `Address already in use` on port 11434

A previous server instance is still running. Kill it:

```bash
kill $(ss -tlnp | grep 11434 | grep -oP 'pid=\K\d+')
```

### `Server is already active for display 99`

Xvfb is already running on `:99`. Either reuse it or remove the lock:

```bash
rm /tmp/.X99-lock
nohup Xvfb :99 -screen 0 1400x900x24 >/tmp/xvfb.log 2>&1 &
```

### `ModuleNotFoundError: No module named 'gi'`

The venv was created without `--system-site-packages`. See `SETUP.md` Step 4
to recreate it.

### `GTK cannot be loaded` / `QT cannot be loaded`

Ensure `python-gobject`, `gtk3`, and `webkit2gtk-4.1` are installed on the
system. See `SETUP.md` Step 2.

### MCP server connection failures

The `mcp_servers.json` contains Windows-specific paths (e.g.,
`C:/Users/Carlos/Desktop/ripple/target/debug/ripple.exe`). These will fail on
Linux. The errors are logged but do not prevent the app from functioning. To
fix, edit `mcp_servers.json` with Linux-appropriate paths or remove entries
that are not needed.

### SSH connection reset

The SSH daemon may need restarting on the remote machine:

```bash
sudo systemctl restart sshd
```

### xdotool `windowactivate` not supported

Xvfb does not run a full window manager, so `_NET_ACTIVE_WINDOW` is not
supported. Use `windowfocus` or `windowmove` + `windowsize` instead. The
`windowactivate` error is harmless; the window still receives input.

---

## File locations on the remote machine

| Path                              | Description                          |
|-----------------------------------|--------------------------------------|
| `~/pywebview_demo/`               | Project root                         |
| `~/pywebview_demo/.venv-sys/`     | Python venv (mise + system packages) |
| `~/pywebview_demo/mise.toml`      | mise config (pins python@3.12)       |
| `~/pywebview_demo/.env`           | API keys (ANTHROPIC + FIREWORKS)     |
| `/tmp/ollama_server.log`          | Server log                           |
| `/tmp/webview_app.log`            | App log                              |
| `/tmp/xvfb.log`                   | Xvfb log                             |
| `/tmp/screenshot*.png`            | Screenshots                          |
