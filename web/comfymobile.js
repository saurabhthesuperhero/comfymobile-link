// Comfy Mobile Link — "Comfy Mobile" sidebar panel: pairing QR, listen check, push status.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

const STYLE = `
.cml-panel{padding:14px;font:13px/1.45 system-ui,sans-serif;color:var(--fg-color,#ddd);display:flex;flex-direction:column;gap:12px}
.cml-panel h3{margin:0;font-size:15px}
.cml-qr{background:#fff;border-radius:12px;padding:10px;width:100%;max-width:260px;align-self:center}
.cml-qr svg{width:100%;height:auto;display:block}
.cml-warn{background:rgba(255,149,0,.12);border:1px solid rgba(255,149,0,.5);border-radius:10px;padding:10px;white-space:pre-wrap}
.cml-urls{font-family:ui-monospace,monospace;font-size:12px;opacity:.85}
.cml-muted{opacity:.7;font-size:12px}
.cml-row{display:flex;gap:8px;flex-wrap:wrap}
.cml-btn{background:var(--comfy-input-bg,#333);color:inherit;border:1px solid var(--border-color,#555);border-radius:8px;padding:6px 10px;cursor:pointer}
.cml-btn:hover{filter:brightness(1.2)}
`;

function ensureStyle() {
  if (document.getElementById("cml-style")) return;
  const s = document.createElement("style");
  s.id = "cml-style";
  s.textContent = STYLE;
  document.head.appendChild(s);
}

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) if (c != null) e.append(c);
  return e;
}

async function render(root) {
  ensureStyle();
  root.innerHTML = "";
  const panel = el("div", { class: "cml-panel" }, el("h3", {}, "Comfy Mobile"), el("div", { class: "cml-muted" }, "Loading…"));
  root.appendChild(panel);

  let data;
  try {
    const res = await api.fetchApi("/comfymobile/pairing");
    data = await res.json();
  } catch (e) {
    panel.lastChild.textContent = "Could not load pairing info: " + e;
    return;
  }
  panel.innerHTML = "";
  panel.append(el("h3", {}, "Comfy Mobile"));

  if (data.listen && !data.listen.lanReachable) {
    panel.append(el("div", { class: "cml-warn" }, "⚠ " + (data.listen.fix || "Start ComfyUI with --listen so your phone can reach it.")));
  }

  if (data.svg) {
    const qr = el("div", { class: "cml-qr" });
    qr.innerHTML = data.svg; // generated server-side by our own encoder, no user input
    panel.append(
      el("div", {}, "Open Comfy Mobile on your phone → Home → ", el("b", {}, "Pair with QR"), ", then scan:"),
      qr,
    );
  } else {
    panel.append(el("div", { class: "cml-warn" }, "No network address found for this computer. Connect it to Wi-Fi / Ethernet and reopen this panel."));
  }

  if (data.urls?.length) {
    panel.append(el("div", { class: "cml-muted" }, "The app will try, in order:"), el("div", { class: "cml-urls" }, data.urls.join("\n")));
  }

  const copyBtn = el("button", {
    class: "cml-btn",
    onclick: async () => {
      try { await navigator.clipboard.writeText(data.payloadText); copyBtn.textContent = "Copied"; }
      catch { prompt("Pairing code (paste it in the app):", data.payloadText); }
    },
  }, "Copy pairing code");
  const refreshBtn = el("button", { class: "cml-btn", onclick: () => render(root) }, "Refresh");
  const resetBtn = el("button", {
    class: "cml-btn",
    onclick: async () => {
      if (!confirm("Create a new pairing code? Phones paired with the old one keep working but stop getting push notifications until they pair again.")) return;
      await api.fetchApi("/comfymobile/pairing/reset", { method: "POST" });
      render(root);
    },
  }, "New code");
  panel.append(el("div", { class: "cml-row" }, copyBtn, refreshBtn, resetBtn));

  const pushN = data.push?.targets ?? 0;
  panel.append(el("div", { class: "cml-muted" },
    pushN > 0
      ? `Push: ${pushN} phone${pushN === 1 ? "" : "s"} will be notified when a job from the app finishes.`
      : "Push: no phone registered yet (Premium → Settings → \"Notify when my image is ready\")."));
  panel.append(el("div", { class: "cml-muted" },
    "Converter: built in (/comfymobile/convert) · v" + data.version +
    " · Keep ComfyUI on a trusted network — it has no password."));
}

app.registerExtension({
  name: "ComfyMobile.Link",
  async setup() {
    const mgr = app.extensionManager;
    if (mgr && typeof mgr.registerSidebarTab === "function") {
      mgr.registerSidebarTab({
        id: "comfymobile-link",
        icon: "pi pi-mobile",
        title: "Comfy Mobile",
        tooltip: "Pair the Comfy Mobile app",
        type: "custom",
        render: (container) => { render(container); },
      });
      return;
    }
    // Legacy frontend: a floating button that opens the panel in a dialog.
    const btn = el("button", {
      class: "cml-btn",
      style: "position:fixed;right:12px;bottom:12px;z-index:9999",
      onclick: () => {
        const dlg = el("dialog", { style: "max-width:320px;background:#222;color:#ddd;border-radius:12px" });
        const body = el("div");
        dlg.append(body, el("button", { class: "cml-btn", onclick: () => dlg.close() }, "Close"));
        document.body.appendChild(dlg);
        dlg.addEventListener("close", () => dlg.remove());
        dlg.showModal();
        render(body);
      },
    }, "📱 Comfy Mobile");
    document.body.appendChild(btn);
  },
});
