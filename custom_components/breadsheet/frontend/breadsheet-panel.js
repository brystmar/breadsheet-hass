// Home Assistant's "Breadsheet" sidebar panel (breadsheet-hass#5). It asks the integration for a fresh panel URL
// (breadsheet's bake view with a short-lived panel token in its fragment) and shows it in a frame. Notification
// and Live Activity taps open /breadsheet/<bake id>, which opens that bake.
const TOKEN_REFRESH_MS = 11 * 60 * 60 * 1000  // panel tokens last 12 hours

class BreadsheetPanel extends HTMLElement {
    constructor() {
        super()
        this.attachShadow({ mode: "open" })
        this._loadedFor = null
        this._loadedAt = 0
    }

    set hass(hass) {
        this._hass = hass
        const menu = this.shadowRoot.querySelector("ha-menu-button")
        if (menu) menu.hass = hass
        this._maybeLoad()
    }

    set narrow(narrow) {
        this._narrow = narrow
        const menu = this.shadowRoot.querySelector("ha-menu-button")
        if (menu) menu.narrow = narrow
    }

    set route(route) {
        this._route = route
        this._maybeLoad()
    }

    connectedCallback() {
        this._maybeLoad()
    }

    _bakeId() {
        const path = (this._route && this._route.path) || ""
        const id = path.replace(/^\/+/, "").split("/")[0]
        return id || null
    }

    _maybeLoad() {
        if (!this._hass || !this.isConnected) return
        const key = this._bakeId() || ""
        const stale = Date.now() - this._loadedAt > TOKEN_REFRESH_MS
        if (this._loadedFor === key && !stale) return
        this._loadedFor = key
        this._loadedAt = Date.now()
        this._load(key || null)
    }

    async _load(bakeId) {
        this._render(null, "Loading…")
        try {
            const result = await this._hass.callWS({ type: "breadsheet/panel_url", ...(bakeId ? { bake_id: bakeId } : {}) })
            this._render(result.url, null)
        } catch (err) {
            this._loadedFor = null  // try again next time the panel is shown
            this._render(null, (err && err.message) || "Breadsheet can't be reached right now. Try again soon.")
        }
    }

    _render(url, message) {
        const root = this.shadowRoot
        root.innerHTML = `
            <style>
                /* The panel's container doesn't give it a height, so take the window's */
                :host { display: flex; flex-direction: column; height: 100vh; height: 100dvh;
                        background: var(--primary-background-color); }
                .toolbar { display: flex; align-items: center; height: var(--header-height, 56px); padding: 0 8px;
                           background: var(--app-header-background-color); color: var(--app-header-text-color, white);
                           font-size: 20px; flex: none; }
                .toolbar .title { margin-left: 8px; }
                iframe { flex: 1; min-height: 0; width: 100%; border: 0; background: white; }
                .message { padding: 24px; color: var(--primary-text-color); }
            </style>
            <div class="toolbar"><ha-menu-button></ha-menu-button><span class="title">Breadsheet</span></div>`
        const menu = root.querySelector("ha-menu-button")
        menu.hass = this._hass
        menu.narrow = this._narrow
        if (url) {
            const frame = document.createElement("iframe")
            frame.title = "Breadsheet"
            frame.src = url
            root.appendChild(frame)
        } else {
            const note = document.createElement("p")
            note.className = "message"
            note.textContent = message  // text, never HTML
            root.appendChild(note)
        }
    }
}

customElements.define("breadsheet-panel", BreadsheetPanel)
