import RFB from "../vendor/novnc/core/rfb.js";

const root = document.getElementById("vnc-session");
const screen = document.getElementById("vnc-screen");
const statusEl = document.getElementById("vnc-status");
const passwordInput = document.getElementById("vnc-password");
const connectBtn = document.getElementById("vnc-connect");
const disconnectBtn = document.getElementById("vnc-disconnect");

if (!root || !screen || !connectBtn) {
  throw new Error("vnc session markup missing");
}

const wsUrl = root.dataset.wsUrl || "";
let rfb = null;
let waitingForPassword = false;

function setStatus(text, kind) {
  if (!statusEl) {
    return;
  }
  statusEl.textContent = text;
  statusEl.dataset.kind = kind || "";
}

function connect() {
  if (!wsUrl) {
    setStatus("нет адреса шлюза", "bad");
    return;
  }
  if (rfb) {
    if (waitingForPassword) {
      rfb.sendCredentials({ password: passwordInput ? passwordInput.value : "" });
      waitingForPassword = false;
      setStatus("пароль отправлен…", "");
      return;
    }
    rfb.disconnect();
  }
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
