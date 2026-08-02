#!/usr/bin/env python3
"""Capture status-bar screenshots for the before/after report.

Loads web/index.html in headless Chromium with a stubbed pywebview API,
drives the app into representative states (local tab, pack connected,
pack disconnected), and saves full-window plus status-bar-cropped PNGs
into docs/screenshots/.
"""

import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = REPO_ROOT / "docs" / "screenshots"
INDEX_URL = f"file://{REPO_ROOT / 'web' / 'index.html'}"

# Stub the pywebview JS bridge. The real bridge is injected by pywebview;
# in a plain browser we emulate the endpoints app.js touches during boot
# and while switching tabs.
API_STUB = r"""
window.pywebview = {
    api: {
        get_pending_js: async () => [],
        get_preferences: async () => ({success: true, preferences: {}}),
        get_ui_preferences: async () => ({}),
        get_last_chat: async () => null,
        get_ui_state: async () => null,
        get_tabs: async () => [],
        get_models: async () => ({success: true, connected: true, models: ['demo-model'], current: 'demo-model'}),
        get_status_info: async (tabId) => {
            const tab = window.__demoTabs[tabId];
            if (!tab) return {success: false, error: 'Tab not found'};
            const text = tab.messages.map(m => m.content).join('\n');
            const charCount = text.length;
            return {
                success: true,
                char_count: charCount,
                line_count: text.split('\n').length,
                token_estimate: Math.ceil(charCount / 4),
                session_input_tokens: tab.sessionInput || 0,
                session_cached_input_tokens: 0,
                session_output_tokens: tab.sessionOutput || 0,
                latency_ms: tab.latencyMs || null,
                skill_count: 2,
                is_pack: tab.isPack,
                host: tab.host,
                connected: tab.connected,
                session_id: tab.isPack ? 'demo-session' : null
            };
        },
        get_conversation_history: async (tabId) => {
            const tab = window.__demoTabs[tabId];
            return tab ? tab.messages : [];
        },
        get_conversation_state: async (tabId) => {
            const tab = window.__demoTabs[tabId];
            if (!tab) return {success: false, error: 'Tab not found'};
            return {
                success: true,
                state: {
                    chat_state: {
                        graph: {
                            nodes: tab.messages.map((m, i) => ({
                                id: 'n' + i,
                                type: m.role,
                                content: m.content
                            })),
                            edges: []
                        }
                    }
                }
            };
        },
        get_tab_states: async () => ({}),
        get_pending_approvals: async () => [],
        set_active_tab: async () => null,
        switch_tab: async () => ({success: true}),
        save_ui_state: async () => null,
        log_js_error: async () => null
    }
};
"""

DEMO_SEED = r"""
window.__demoTabs = {
    'tab-local': {
        title: 'Local Chat',
        isPack: false,
        host: null,
        connected: false,
        sessionInput: 12400,
        sessionOutput: 3120,
        latencyMs: 4200,
        messages: [
            {role: 'user', content: 'Explain the difference between a stack and a queue.'},
            {role: 'assistant', content: 'A stack is LIFO; a queue is FIFO. Both are linear data structures but differ in how elements are removed.'}
        ]
    },
    'tab-pack': {
        title: 'Pack: build-server-01',
        isPack: true,
        host: 'build-server-01',
        connected: true,
        sessionInput: 0,
        sessionOutput: 0,
        messages: [
            {role: 'user', content: 'Run the test suite on the remote host.'}
        ]
    }
};
"""


async def wait_for_app(page):
    await page.wait_for_function(
        "() => window.app && window.app.tabManager && window.app.chatDisplay",
        timeout=20000,
    )
    # Give the app a moment to finish async init work
    await page.wait_for_timeout(800)
    state = await page.evaluate(
        "() => ({app: !!window.app, tm: !!(window.app && window.app.tabManager)})"
    )
    print(f"  app state: {state}")


async def seed_tabs(page):
    await page.evaluate(DEMO_SEED)
    ok = await page.evaluate("() => !!window.__demoTabs")
    print(f"  demo tabs present: {ok}")
    await page.evaluate(
        """() => {
            const app = window.app;
            for (const [id, data] of Object.entries(window.__demoTabs)) {
                const tab = app.tabManager.createTabUI(id, data.title);
                tab.isPack = data.isPack;
                tab.host = data.host;
                tab.offline = !data.connected;
                tab.messageCount = data.messages.length;
                if (!data.connected) {
                    app.tabManager.setTabOffline(id, true);
                }
            }
        }"""
    )
    await page.wait_for_timeout(200)


async def activate_tab(page, tab_id):
    await page.evaluate(
        """(tabId) => {
            const app = window.app;
            app.tabManager.setActiveTab(tabId);
            document.dispatchEvent(new CustomEvent('tabSwitched', {
                detail: { tabId, tab: app.tabManager.getTab(tabId) }
            }));
        }""",
        tab_id,
    )
    await page.wait_for_timeout(400)


async def snap(page, name, full=True):
    bar = await page.query_selector("#status-bar")
    bar_path = SCREENSHOT_DIR / f"{name}_bar.png"
    await bar.screenshot(path=str(bar_path))
    if full:
        await page.screenshot(
            path=str(SCREENSHOT_DIR / f"{name}_full.png"), full_page=False
        )
    print(f"  saved {bar_path.name}")


async def capture_before(page):
    print("Capturing BEFORE screenshots (current status bar)...")
    await activate_tab(page, "tab-local")
    await snap(page, "before_local")

    await activate_tab(page, "tab-pack")
    await snap(page, "before_pack_connected")

    await page.evaluate(
        "() => window.app.tabManager.setTabOffline('tab-pack', true)"
    )
    await page.wait_for_timeout(300)
    await snap(page, "before_pack_disconnected")


async def capture_after(page):
    print("Capturing AFTER screenshots (improved status bar)...")
    await activate_tab(page, "tab-local")
    await snap(page, "after_local")

    await activate_tab(page, "tab-pack")
    await snap(page, "after_pack_connected")

    await page.evaluate(
        """() => {
            const app = window.app;
            app.tabManager.setTabOffline('tab-pack', true);
            window.__demoTabs['tab-pack'].connected = false;
            app._updateStatusBar();
        }"""
    )
    await page.wait_for_timeout(300)
    await snap(page, "after_pack_disconnected")


async def main():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        sys.exit("playwright is not installed in this environment")

    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1280, "height": 800})
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
        page.on("console", lambda m: print(f"  [console.{m.type}] {m.text}") if m.type == "error" else None)

        await page.add_init_script(API_STUB)
        await page.goto(INDEX_URL)
        await wait_for_app(page)
        await seed_tabs(page)

        await capture_before(page)
        await capture_after(page)
        await browser.close()

    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
