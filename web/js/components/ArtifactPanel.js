/** A sandboxed local HTML artifact shown in the same durable dock as surfaces. */
class ArtifactPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.tabId = null;
    this.manifest = null;
    this._html = "";
  }

  connectedCallback() {
    if (this.shadowRoot.childElementCount) return;
    this.shadowRoot.innerHTML = `
      <style>
        :host { display:flex; flex-direction:column; height:100%; min-height:0;
          background:var(--bg-primary,#1e1e1e); color:var(--text-primary,#ddd);
          font:12px var(--font-ui,'Segoe UI',sans-serif); }
        header { display:flex; align-items:center; gap:8px; padding:6px 10px;
          border-bottom:1px solid var(--border-color,#3e3e42); }
        .title { flex:1; min-width:0; overflow:hidden; text-overflow:ellipsis;
          white-space:nowrap; font-weight:600; }
        .meta { color:var(--text-secondary,#999); font-family:monospace; }
        button { color:inherit; background:var(--bg-tertiary,#3c3c3c);
          border:1px solid var(--border-color,#555); border-radius:4px;
          padding:3px 8px; cursor:pointer; font-size:11px; }
        button:hover { background:var(--bg-hover,#4a4a4a); }
        iframe { flex:1 1 auto; min-height:0; width:100%; border:0; background:white; }
        .status { flex:1; display:flex; align-items:center; justify-content:center;
          padding:16px; color:var(--text-secondary,#999); text-align:center; }
      </style>
      <header><span class="title"></span><span class="meta"></span>
        <button class="reload">Reload</button><button class="close">Close</button></header>
      <div class="status">Loading…</div>
    `;
    this.shadowRoot
      .querySelector(".reload")
      .addEventListener("click", () => this.reload());
    this.shadowRoot.querySelector(".close").addEventListener("click", () => {
      window.ArtifactDock?.remove(this.tabId, this.manifest?.artifact_id);
    });
    this._renderHeader();
  }

  configure(tabId, manifest) {
    this.tabId = tabId;
    this.manifest = manifest;
    this._renderHeader();
  }

  _renderHeader() {
    const title = this.shadowRoot.querySelector(".title");
    const meta = this.shadowRoot.querySelector(".meta");
    if (title)
      title.textContent = this.manifest?.title || "Interactive artifact";
    if (meta && this.manifest) {
      meta.textContent = `${this.manifest.artifact_id} · r${this.manifest.revision}`;
    }
  }

  render(html) {
    this._html = html;
    this.shadowRoot.querySelector("iframe")?.remove();
    this.shadowRoot.querySelector(".status")?.remove();
    const iframe = document.createElement("iframe");
    // No allow-same-origin: scripts execute in a unique opaque origin and
    // cannot access the parent DOM or pywebview.api. Omitted sandbox tokens
    // also block top navigation, popups, forms, and downloads.
    iframe.setAttribute("sandbox", "allow-scripts");
    iframe.setAttribute("referrerpolicy", "no-referrer");
    iframe.setAttribute(
      "title",
      this.manifest?.title || "Interactive artifact",
    );
    iframe.srcdoc = html;
    this.shadowRoot.appendChild(iframe);
  }

  async reload() {
    const result = await window.pythonAPI.artifact_attach(
      this.tabId,
      this.manifest.artifact_id,
    );
    if (!result?.success) {
      this.shadowRoot.querySelector("iframe")?.remove();
      let status = this.shadowRoot.querySelector(".status");
      if (!status) {
        status = document.createElement("div");
        status.className = "status";
        this.shadowRoot.appendChild(status);
      }
      status.textContent = `Artifact unavailable: ${
        result?.error || "unknown error"
      }`;
      return false;
    }
    this.configure(this.tabId, result.manifest);
    this.render(result.html);
    return true;
  }
}

customElements.define("artifact-panel", ArtifactPanel);

const ArtifactDock = {
  _panels: new Map(),
  _activeTabId: null,

  _forTab(tabId) {
    if (!this._panels.has(tabId)) this._panels.set(tabId, new Map());
    return this._panels.get(tabId);
  },

  count(tabId) {
    return this._panels.get(tabId)?.size || 0;
  },

  async show(tabId, attached) {
    const dock = document.getElementById("surface-dock");
    if (!dock) return null;
    const manifest = attached.manifest;
    let panel = this._forTab(tabId).get(manifest.artifact_id);
    if (!panel) {
      panel = document.createElement("artifact-panel");
      this._forTab(tabId).set(manifest.artifact_id, panel);
      dock.appendChild(panel);
    }
    panel.configure(tabId, manifest);
    panel.render(attached.html);
    this.setActiveTab(this._activeTabId ?? tabId);
    return panel;
  },

  remove(tabId, artifactId) {
    const panels = this._panels.get(tabId);
    panels?.get(artifactId)?.remove();
    panels?.delete(artifactId);
    this._syncVisibility();
  },

  removeTab(tabId) {
    for (const panel of this._panels.get(tabId)?.values() || []) panel.remove();
    this._panels.delete(tabId);
    this._syncVisibility();
  },

  setActiveTab(tabId) {
    this._activeTabId = tabId;
    for (const [owner, panels] of this._panels) {
      for (const panel of panels.values()) {
        panel.style.display = owner === tabId ? "flex" : "none";
      }
    }
    this._syncVisibility();
  },

  _syncVisibility() {
    const dock = document.getElementById("surface-dock");
    const splitter = document.getElementById("surface-splitter");
    const count =
      this.count(this._activeTabId) +
      (window.SurfaceDock?.count(this._activeTabId) || 0);
    dock?.classList.toggle("hidden", count === 0);
    splitter?.classList.toggle("hidden", count === 0);
  },
};

window.ArtifactPanel = ArtifactPanel;
window.ArtifactDock = ArtifactDock;
