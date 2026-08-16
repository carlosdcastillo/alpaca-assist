/**
 * SurfacePanel - live, interactive view of a GUI app running on a Pack host.
 *
 * Why this is a custom element in a dock rather than a fold in the transcript:
 * `chatDisplay.clear()` wipes the chat container on every tab switch and
 * `_renderFullAnswer` rebuilds it, so anything living inside it is destroyed
 * and recreated. A video re-fetches and nobody notices. A live VNC session
 * would be torn down and reconnected every time you glanced at another tab.
 * The dock is a sibling of `.main-content`, never touched by that path;
 * panels are created once and merely hidden when their tab is not active.
 *
 * Why a custom element rather than an iframe pointed at noVNC: the lease
 * banner and the canvas share one shadow root, so they lay out together. In
 * an iframe the banner would be a separate overlay fighting for alignment.
 *
 * Pixels never touch Python. noVNC opens a WebSocket to the local end of an
 * SSH tunnel (core/surface_tunnel.py) and speaks RFB to the remote x11vnc
 * through it. Pushing framebuffer data through the 50 ms `get_pending_js()`
 * poller would be a category error — that queue is for chat tokens.
 */

const SURFACE_HEARTBEAT_MS = 3000;
const SURFACE_LEASE_TTL = 30;
const SURFACE_LEASE_THROTTLE_MS = 2000;

class SurfacePanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.tabId = null;
    this.surfaceId = null;
    this.description = "";
    this.surfaceWidth = 0;
    this.surfaceHeight = 0;
    this._rfb = null;
    this._state = "idle";
    this._heartbeatTimer = null;
    this._lastLeaseTouch = 0;
    this._rendered = false;
  }

  configure({ tabId, surfaceId, description, width, height }) {
    this.tabId = tabId;
    this.surfaceId = surfaceId;
    this.description = description || surfaceId;
    this.surfaceWidth = width || 0;
    this.surfaceHeight = height || 0;
    if (this._rendered) this._renderHeader();
  }

  connectedCallback() {
    if (this._rendered) return;
    this._rendered = true;
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: flex;
          flex-direction: column;
          height: 100%;
          min-height: 0;
          background: var(--bg-secondary, #252526);
          font-family: var(--font-ui, 'Segoe UI', sans-serif);
          font-size: 12px;
          color: var(--text-primary, #d4d4d4);
        }
        .surface-header {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 6px 10px;
          border-bottom: 1px solid var(--border-color, #3e3e42);
          flex: 0 0 auto;
        }
        .surface-title {
          flex: 1;
          font-weight: 600;
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }
        .surface-geometry {
          color: var(--text-secondary, #9d9d9d);
          font-family: var(--font-mono, 'Consolas', monospace);
        }
        button {
          background: var(--bg-tertiary, #3c3c3c);
          color: inherit;
          border: 1px solid var(--border-color, #3e3e42);
          border-radius: 4px;
          padding: 3px 8px;
          cursor: pointer;
          font-size: 11px;
        }
        button:hover { background: var(--bg-hover, #4a4a4a); }
        button.danger:hover { background: #7a2e2e; }
        .lease-banner {
          padding: 4px 10px;
          font-size: 11px;
          flex: 0 0 auto;
          border-bottom: 1px solid var(--border-color, #3e3e42);
        }
        .lease-banner.human { background: #1f3a1f; color: #b6e3b6; }
        .lease-banner.model { background: #3a2f14; color: #e8d3a0; cursor: pointer; }
        .lease-banner.idle { background: transparent; color: var(--text-secondary, #9d9d9d); }
        .surface-viewport {
          flex: 1 1 auto;
          min-height: 0;
          position: relative;
          background: #000;
          overflow: hidden;
        }
        .surface-status {
          position: absolute;
          inset: 0;
          display: flex;
          align-items: center;
          justify-content: center;
          text-align: center;
          padding: 16px;
          color: var(--text-secondary, #9d9d9d);
          pointer-events: none;
        }
        .surface-status.hidden { display: none; }
        .surface-status.error { color: #e88; pointer-events: auto; }
      </style>
      <div class="surface-header">
        <span class="surface-title"></span>
        <span class="surface-geometry"></span>
        <button class="reconnect-btn" title="Reconnect to this surface">Reconnect</button>
        <button class="stop-btn danger" title="Stop the app and destroy this surface">Stop app</button>
      </div>
      <div class="lease-banner idle">Not connected</div>
      <div class="surface-viewport">
        <div class="surface-status">Idle</div>
      </div>
    `;
    this._renderHeader();

    this.shadowRoot
      .querySelector(".stop-btn")
      .addEventListener("click", () => this.stopApp());
    this.shadowRoot
      .querySelector(".reconnect-btn")
      .addEventListener("click", () => this.reattach());
    this.shadowRoot
      .querySelector(".lease-banner")
      .addEventListener("click", () => this._takeControl());

    // Human interaction preempts any model lease immediately. Capture phase
    // on the shadow root so this fires no matter where inside the noVNC
    // canvas the event originated.
    for (const type of ["pointerdown", "keydown", "wheel"]) {
      this.shadowRoot.addEventListener(type, () => this._touchLease(), {
        capture: true,
      });
    }
  }

  _renderHeader() {
    const title = this.shadowRoot?.querySelector(".surface-title");
    const geometry = this.shadowRoot?.querySelector(".surface-geometry");
    if (title) title.textContent = this.description || "Surface";
    if (geometry) {
      geometry.textContent =
        this.surfaceWidth && this.surfaceHeight
          ? `${this.surfaceWidth}×${this.surfaceHeight}`
          : "";
    }
  }

  _setStatus(message, isError = false) {
    const status = this.shadowRoot?.querySelector(".surface-status");
    if (!status) return;
    status.textContent = message || "";
    status.classList.toggle("hidden", !message);
    status.classList.toggle("error", Boolean(isError));
  }

  /**
   * Attach noVNC to a tunnelled WebSocket.
   *
   * noVNC 1.7 wants a secure context. The app is served from
   * http://127.0.0.1:<port>/ (pywebview's own bottle server — see
   * webview_app.py), and a loopback origin counts as potentially
   * trustworthy, so `window.isSecureContext` holds and crypto.subtle is
   * available for VNC auth.
   */
  async connect({ wsUrl, password }) {
    this.disconnect();
    this._setStatus("Connecting…");
    let RFB;
    try {
      // Loaded lazily: noVNC is ~700 KB of ES modules and costs nothing
      // until a surface is actually opened. Every other script here is a
      // classic <script> tag, and this deliberately does not change that.
      ({ default: RFB } = await import("../lib/novnc/core/rfb.js"));
    } catch (error) {
      this._setStatus(`Could not load the VNC client: ${error.message}`, true);
      return false;
    }

    const viewport = this.shadowRoot.querySelector(".surface-viewport");
    try {
      this._rfb = new RFB(viewport, wsUrl, {
        credentials: { password },
      });
    } catch (error) {
      this._setStatus(`Could not connect: ${error.message}`, true);
      return false;
    }
    this._rfb.scaleViewport = true;
    // Fixed geometry in v1: the remote Xvfb screen is created at a set size
    // and there is no window manager to reflow anything inside it.
    this._rfb.resizeSession = false;
    this._rfb.background = "#000";

    this._rfb.addEventListener("connect", () => {
      this._state = "connected";
      this._setStatus("");
      this._startHeartbeat();
    });
    this._rfb.addEventListener("disconnect", (event) => {
      this._state = "disconnected";
      this._stopHeartbeat();
      this._setStatus(
        event.detail?.clean
          ? "Disconnected. The surface may still be running — use Reconnect."
          : "Connection lost. The tunnel, the Pack daemon, or the surface itself may be gone.",
        !event.detail?.clean,
      );
      this._renderLease(null);
    });
    this._rfb.addEventListener("securityfailure", (event) => {
      this._setStatus(
        `VNC authentication failed: ${
          event.detail?.reason || "unknown reason"
        }`,
        true,
      );
    });
    return true;
  }

  disconnect() {
    this._stopHeartbeat();
    if (this._rfb) {
      try {
        this._rfb.disconnect();
      } catch (error) {
        /* already gone */
      }
      this._rfb = null;
    }
    this._state = "idle";
  }

  /** Tear down the remote surface as well as the local view. */
  async stopApp() {
    this.disconnect();
    this._setStatus("Stopping…");
    await window.pythonAPI.surface_close(this.tabId, this.surfaceId);
    window.SurfaceDock?.remove(this.tabId, this.surfaceId);
  }

  /** Rebuild the tunnel and reconnect, e.g. after a network blip. */
  async reattach() {
    this._setStatus("Reattaching…");
    const result = await window.pythonAPI.surface_attach(
      this.tabId,
      this.surfaceId,
    );
    if (!result?.success) {
      this._setStatus(
        `Could not reattach: ${result?.error || "the surface is gone"}`,
        true,
      );
      return false;
    }
    this.configure({
      tabId: this.tabId,
      surfaceId: this.surfaceId,
      description: result.description,
      width: result.width,
      height: result.height,
    });
    return this.connect({ wsUrl: result.ws_url, password: result.password });
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this._heartbeatTimer = setInterval(
      () => this._heartbeat(),
      SURFACE_HEARTBEAT_MS,
    );
    this._heartbeat();
  }

  _stopHeartbeat() {
    if (this._heartbeatTimer) {
      clearInterval(this._heartbeatTimer);
      this._heartbeatTimer = null;
    }
  }

  /**
   * Tell the supervisor somebody is still watching, and refresh the banner.
   *
   * Without this a surface the human is reading but not touching would be
   * collected by the idle reaper mid-session.
   */
  async _heartbeat() {
    if (!this.isConnected || this._state !== "connected") return;
    const result = await window.pythonAPI.surface_control(
      this.tabId,
      "surface_touch",
      { surface_id: this.surfaceId },
    );
    if (!result?.success) {
      this._setStatus(
        `Surface unreachable: ${result?.error || "unknown"}`,
        true,
      );
      this._stopHeartbeat();
      return;
    }
    this._renderLease(result.lease);
  }

  /** Take control, throttled, on any human input. */
  async _touchLease() {
    const now = Date.now();
    if (now - this._lastLeaseTouch < SURFACE_LEASE_THROTTLE_MS) return;
    this._lastLeaseTouch = now;
    const result = await window.pythonAPI.surface_control(
      this.tabId,
      "surface_lease_touch",
      {
        surface_id: this.surfaceId,
        holder: "human",
        ttl: SURFACE_LEASE_TTL,
      },
    );
    if (result?.success) this._renderLease(result);
  }

  _takeControl() {
    this._lastLeaseTouch = 0;
    this._touchLease();
  }

  _renderLease(lease) {
    const banner = this.shadowRoot?.querySelector(".lease-banner");
    if (!banner) return;
    banner.classList.remove("human", "model", "idle");
    if (!lease || !lease.holder) {
      banner.classList.add("idle");
      banner.textContent =
        this._state === "connected" ? "Nobody has control" : "Not connected";
      return;
    }
    const until = new Date(lease.expires_at * 1000).toLocaleTimeString();
    if (lease.holder === "human") {
      banner.classList.add("human");
      banner.textContent = "You have control";
    } else {
      banner.classList.add("model");
      banner.textContent = `Model has control until ${until} — click to take over`;
    }
  }
}

try {
  customElements.define("surface-panel", SurfacePanel);
} catch (error) {
  console.error("[SURFACE] Could not register <surface-panel>:", error);
}

/**
 * Owns every panel in `#surface-dock`, keyed by tab.
 *
 * Panels are created once and hidden on tab switch, never destroyed and
 * rebuilt — that is the whole reason the dock sits outside `#chat-container`.
 */
const SurfaceDock = {
  _panels: new Map(), // tabId -> Map(surfaceId -> SurfacePanel)
  _activeTabId: null,

  _dock() {
    return document.getElementById("surface-dock");
  },

  _forTab(tabId) {
    if (!this._panels.has(tabId)) this._panels.set(tabId, new Map());
    return this._panels.get(tabId);
  },

  has(tabId, surfaceId) {
    return Boolean(this._panels.get(tabId)?.has(surfaceId));
  },

  /** Create (or reveal) a panel and connect it. */
  async show(tabId, connection) {
    const dock = this._dock();
    if (!dock) return null;
    const surfaceId = connection.surface_id;
    let panel = this._forTab(tabId).get(surfaceId);
    if (!panel) {
      panel = document.createElement("surface-panel");
      this._forTab(tabId).set(surfaceId, panel);
      dock.appendChild(panel);
    }
    panel.configure({
      tabId,
      surfaceId,
      description: connection.description,
      width: connection.width,
      height: connection.height,
    });
    this.setActiveTab(this._activeTabId ?? tabId);
    this._syncVisibility();
    await panel.connect({
      wsUrl: connection.ws_url,
      password: connection.password,
    });
    return panel;
  },

  remove(tabId, surfaceId) {
    const panels = this._panels.get(tabId);
    const panel = panels?.get(surfaceId);
    if (panel) {
      panel.disconnect();
      panel.remove();
      panels.delete(surfaceId);
    }
    this._syncVisibility();
  },

  /** Drop every panel for a tab — called when the tab itself closes. */
  removeTab(tabId) {
    const panels = this._panels.get(tabId);
    if (!panels) return;
    for (const panel of panels.values()) {
      panel.disconnect();
      panel.remove();
    }
    this._panels.delete(tabId);
    this._syncVisibility();
  },

  setActiveTab(tabId) {
    this._activeTabId = tabId;
    for (const [ownerTabId, panels] of this._panels) {
      for (const panel of panels.values()) {
        panel.style.display = ownerTabId === tabId ? "flex" : "none";
      }
    }
    this._syncVisibility();
  },

  /** Show the dock only while the active tab actually has a panel in it. */
  _syncVisibility() {
    const dock = this._dock();
    const splitter = document.getElementById("surface-splitter");
    if (!dock) return;
    const count = this._panels.get(this._activeTabId)?.size || 0;
    dock.classList.toggle("hidden", count === 0);
    if (splitter) splitter.classList.toggle("hidden", count === 0);
  },
};

window.SurfacePanel = SurfacePanel;
window.SurfaceDock = SurfaceDock;
