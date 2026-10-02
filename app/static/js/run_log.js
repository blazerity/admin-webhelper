// Опрос статуса/лога запуска (polling вместо WebSocket — подходит синхронному Gunicorn).
(function () {
  const script = document.currentScript;
  const url = script && script.dataset.statusUrl;
  const logNode = document.getElementById("run-log");
  const statusNode = document.getElementById("run-status");
  const cancelNode = document.getElementById("run-cancel");
  const closeNode = document.getElementById("run-close-session");
  const hintNode = document.getElementById("run-session-hint");
  const sessionNode = document.getElementById("run-session-state");
  const exitNode = document.getElementById("run-exit-code");
  const finishedAtNode = document.getElementById("run-finished-at");
  const commandNode = document.getElementById("run-command");
  const commandSubmit = document.getElementById("run-command-submit");
  if (!url || !logNode) return;

  const sessionLabels = {
    busy: "открыта, идёт команда",
    open: "открыта",
    closed: "закрыта"
  };

  function apply(payload) {
    const next = payload.log_text || "";
    const distance = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight;
    const stick = distance < 48;
    if (logNode.textContent !== next) logNode.textContent = next;
    if (stick) logNode.scrollTop = logNode.scrollHeight;
    if (statusNode) statusNode.textContent = payload.status_label || payload.status;
    if (cancelNode) cancelNode.disabled = !!payload.finished;
    if (closeNode) closeNode.disabled = !payload.session_open;
    if (sessionNode) {
      sessionNode.textContent = sessionLabels[payload.session_state] || payload.session_state || "";
    }
    if (hintNode && payload.session_hint) {
      if (payload.session_state === "closed" && hintNode.dataset.closedHint) {
        hintNode.textContent = hintNode.dataset.closedHint;
      } else {
        hintNode.textContent = payload.session_hint;
      }
    }
    if (exitNode && payload.exit_code !== null && payload.exit_code !== undefined) {
      exitNode.textContent = String(payload.exit_code);
    }
    if (finishedAtNode && payload.finished_at) finishedAtNode.textContent = payload.finished_at;
    const canType = !!payload.finished;
    if (commandNode) {
      commandNode.disabled = !canType;
      commandNode.title = canType
        ? "Команда выполнится на этом компьютере"
        : "Ввод включится, когда текущая команда закончится";
    }
    if (commandSubmit) commandSubmit.disabled = !canType;
    if (!payload.finished || payload.session_open) setTimeout(tick, 800);
  }

  function tick() {
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(apply)
      .catch(function () { setTimeout(tick, 3000); });
  }
  setTimeout(tick, 800);
})();
