# SETUP — One-time installation for pywebview_demo on a remote Linux machine

This guide covers the prerequisites and one-time setup needed before the app
can be run. The example uses `192.168.0.58` (Omarchy / Arch Linux) as the
target host, logged in as `carlos`.

---

## 1. SSH key-based authentication

Passwordless SSH must be configured so the remote machine accepts connections
without interactive prompts.

On the **local** machine, print your public key:

```bash
cat ~/.ssh/id_ed25519.pub
```

On the **remote** machine, add it to `authorized_keys`:

```bash
mkdir -p ~/.ssh && chmod 700 ~/.ssh
echo '<paste-your-public-key-here>' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

Verify from the local machine:

```bash
ssh 192.168.0.58 echo connected
# Expected: connected
```

---

## 2. Install system packages

These require `sudo` and only need to be done once:

```bash
sudo pacman -S --noconfirm xorg-server-xvfb xdotool xorg-xeyes imagemagick \
  python-gobject gtk3 webkit2gtk-4.1
```

| Package            | Purpose                                      |
|--------------------|----------------------------------------------|
| `xorg-server-xvfb` | Virtual framebuffer X server                  |
| `xdotool`          | Keyboard/mouse automation via X11             |
| `xorg-xeyes`       | Optional X11 test app                         |
| `imagemagick`       | Screenshots via `import -window root`         |
| `python-gobject`   | PyGObject (`gi`) — required by pywebview GTK  |
| `gtk3`             | GTK3 runtime — required by pywebview GTK      |
| `webkit2gtk-4.1`   | WebKit rendering engine — required by pywebview |

Verify everything is installed:

```bash
pacman -Q xorg-server-xvfb xdotool xorg-xeyes imagemagick \
  python-gobject gtk3 webkit2gtk-4.1
```

---

## 3. Copy the project to the remote machine

From the local machine, create a tarball excluding large/irrelevant files and
pipe it over SSH:

```bash
python -c "
import tarfile, io, os, subprocess

exclude = {'.git', 'node_modules', '__pycache__', '.mypy_cache', '.pytest_cache',
           '.claude', 'out-of-the-way', 'conversations.db', 'chat_session.json',
           'chat_session.json.bak', 'conversations_export.json'}

src = r'C:\Users\Carlos\Desktop\pywebview_demo'
buf = io.BytesIO()

with tarfile.open(fileobj=buf, mode='w:gz') as tar:
    for root, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if f in exclude:
                continue
            fp = os.path.join(root, f)
            arcname = os.path.relpath(fp, src)
            tar.add(fp, arcname=arcname)

buf.seek(0)
data = buf.read()
print(f'Archive size: {len(data)} bytes')

cmd = ['ssh', '-o', 'BatchMode=yes', '192.168.0.58',
       'mkdir -p ~/pywebview_demo && tar xzf - -C ~/pywebview_demo']
r = subprocess.run(cmd, input=data, capture_output=True, timeout=60)
print('RC:', r.returncode, 'STDERR:', r.stderr.decode()[:500])
"
```

To update the project later, re-run the same command. It will overwrite
existing files in place.

---

## 4. Set up the Python environment with mise

[mise](https://mise.jdx.dev/) manages the Python version. On Omarchy it is
already available at `/usr/bin/mise`.

```bash
cd ~/pywebview_demo
mise install python@3.12
mise use python@3.12
```

Create a venv with `--system-site-packages` so pywebview can access the
system-installed `gi` (PyGObject) module, which cannot be pip-installed:

```bash
mise exec -- python -m venv --system-site-packages .venv-sys
```

Install all pip dependencies:

```bash
.venv-sys/bin/pip install requests pyperclip pygments Markdown mcp pillow \
  pywebview tree-sitter tree-sitter-rust typing-extensions pyyaml
```

Verify `gi` is accessible from the venv:

```bash
.venv-sys/bin/python -c "import gi; print(gi.__version__)"
# Expected: 3.56.3 (or similar)
```

> **Why `--system-site-packages`?** The `gi` (PyGObject) module is a C
> extension compiled against the system Python version. It cannot be
> pip-installed. The `--system-site-packages` flag makes the venv fall through
> to system packages for `gi` while keeping pip packages isolated.

---

## 5. Verify the `.env` file

The project's `.env` file must contain valid API keys:

```bash
cat ~/pywebview_demo/.env
```

Expected contents:

```
ANTHROPIC_API_KEY=sk-ant-api03-...
FIREWORKS_API_KEY=fw_...
```

The `anthropic_ollama_server.py` reads these from `os.environ` at startup. If
either key is missing, the corresponding models will be unavailable.

---

## 6. (Optional) Fix MCP server paths

The `mcp_servers.json` ships with Windows-specific paths that will fail on
Linux. The errors are logged but do not prevent the app from functioning. To
fix, edit `mcp_servers.json` with Linux-appropriate paths or remove entries
that are not needed.

---

## Quick verification checklist

```bash
# SSH works without password
ssh 192.168.0.58 echo connected

# System packages installed
ssh 192.168.0.58 'pacman -Q xorg-server-xvfb xdotool imagemagick python-gobject gtk3 webkit2gtk-4.1'

# Project files present
ssh 192.168.0.58 'ls ~/pywebview_demo/webview_app.py ~/pywebview_demo/anthropic_ollama_server.py'

# Python venv works and gi loads
ssh 192.168.0.58 'cd ~/pywebview_demo && .venv-sys/bin/python -c "import gi, webview, requests, yaml; print(\"all imports OK\")"'

# .env has API keys
ssh 192.168.0.58 'grep -c API_KEY ~/pywebview_demo/.env'
```
