import RFB from "../vendor/novnc/core/rfb.js";

const root = document.getElementById("vnc-session");
const screen = document.getElementById("vnc-screen");
const statusEl = document.getElementById("vnc-status");
const logEl = document.getElementById("vnc-ensure-log");
const passwordInput = document.getElementById("vnc-password");
const connectBtn = document.getElementById("vnc-connect");
const disconnectBtn = document.getElementById("vnc-disconnect");

if (!root || !screen || !connectBtn) {
  throw new Error("vnc session markup missing");
}

const prepareUrl = root.dataset.prepareUrl || "";
const ticketUrl = root.dataset.ticketUrl || "";
const csrf = root.dataset.csrfToken || "";
let rfb = null;
let waitingForPassword = false;
let preparing = false;

function setStatus(text, kind) {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = text;
  statusEl.dataset.kind = kind || "";
}

function setLog(text) {
  if (!logEl) {
    return;
  }
  const value = text || "";
  if (!value.trim()) {
    logEl.hidden = true;
    logEl.textContent = "";
    return;
  }
  logEl.hidden = false;
  logEl.textContent = value;
  logEl.scrollTop = logEl.scrollHeight;
}

function http() {
  return window.BawhHttp;
}

async function pollRun(statusUrl) {
  const fetchJson = http().fetchJson;
  for (;;) {
    const payload = await fetchJson(statusUrl);
    setLog(payload.log_text || "");
    if (payload.finished) {
      return payload;
    }
    const label = payload.status_label || payload.status || "агент";
    setStatus(`агент TightVNC: ${label}…`, "");
    await new Promise((resolve) => setTimeout(resolve, 800));
  }
}

function attachRfb(wsUrl) {
  setStatus("подключение…", "");
  rfb = new RFB(screen, wsUrl, {
    credentials: { password: passwordInput ? passwordInput.value : "" },
  });
  rfb.scaleViewport = true;
  rfb.clipViewport = true;
  rfb.resizeSession = false;
  rfb.background = "#1e1e1e";
  rfb.addEventListener("connect", () => {
    waitingForPassword = false;
    setStatus("подключено", "ok");
    if (disconnectBtn) {
      disconnectBtn.disabled = false;
    }
    connectBtn.disabled = true;
  });
  rfb.addEventListener("disconnect", (ev) => {
    const clean = Boolean(ev.detail && ev.detail.clean);
    setStatus(clean ? "отключено" : "связь оборвалась", clean ? "" : "bad");
    if (disconnectBtn) {
      disconnectBtn.disabled = true;
    }
    connectBtn.disabled = false;
    waitingForPassword = false;
    rfb = null;
  });
  rfb.addEventListener("credentialsrequired", () => {
    waitingForPassword = true;
    connectBtn.disabled = false;
    setStatus("нужен пароль агента VNC", "warn");
    if (passwordInput) {
      passwordInput.focus();
    }
  });
  rfb.addEventListener("securityfailure", (ev) => {
    const status = ev.detail && ev.detail.status;
    setStatus(status ? `доступ к VNC отклонён (${status})` : "доступ к VNC отклонён", "bad");
  });
}

async function connect() {
  if (rfb) {
    if (waitingForPassword) {
      rfb.sendCredentials({ password: passwordInput ? passwordInput.value : "" });
      waitingForPassword = false;
      setStatus("пароль отправлен…", "");
      return;
    }
    rfb.disconnect();
  }
  if (preparing) {
    return;
  }
  if (!prepareUrl || !ticketUrl || !http()) {
    setStatus("нет адреса подготовки агента", "bad");
    return;
  }
  preparing = true;
  connectBtn.disabled = true;
  setStatus("агент TightVNC…", "");
  try {
    const started = await http().postJson(
      prepareUrl,
      { password: passwordInput ? passwordInput.value : "" },
      csrf,
    );
    const result = await pollRun(started.status_url);
    if (result.status !== "success") {
      setStatus(result.status_label || "агент TightVNC не готов", "bad");
      connectBtn.disabled = false;
      return;
    }
    setStatus("агент готов, открываю стол…", "");
    const ticket = await http().fetchJson(started.ticket_url || ticketUrl);
    if (!ticket.ws_url) {
      setStatus("нет билета шлюза", "bad");
      connectBtn.disabled = false;
      return;
    }
    attachRfb(ticket.ws_url);
  } catch (err) {
    setStatus(err && err.message ? err.message : "не удалось подготовить агент", "bad");
    connectBtn.disabled = false;
  } finally {
    preparing = false;
  }
}

connectBtn.addEventListener("click", (event) => {
  event.preventDefault();
  connect();
});
if (disconnectBtn) {
  disconnectBtn.addEventListener("click", (event) => {
    event.preventDefault();
    if (rfb) {
      rfb.disconnect();
    }
  });
}
if (passwordInput) {
  passwordInput.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      connect();
    }
  });
}

if (root.dataset.hasPassword === "1" && passwordInput && passwordInput.value) {
  connect();
}
