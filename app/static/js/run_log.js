// Обновление лога запуска, пока статус не финальный.
// Куски вывода пишутся в базу по мере появления, обычного GET достаточно.
// WebSocket сознательно не используем: это работает на синхронных воркерах
// Gunicorn. Позже этот файл можно заменить подпиской.
(function () {
  const script = document.currentScript;
  const url = script && script.dataset.statusUrl;
  const logNode = document.getElementById("run-log");
  const statusNode = document.getElementById("run-status");
  const cancelNode = document.getElementById("run-cancel");
  const closeNode = document.getElementById("run-close-session");
  if (!url || !logNode) return;

  function tick() {
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        const next = payload.log_text || "";
        const distance = logNode.scrollHeight - logNode.scrollTop - logNode.clientHeight;
        const stick = distance < 48;
        if (logNode.textContent !== next) logNode.textContent = next;
        if (stick) logNode.scrollTop = logNode.scrollHeight;
        if (statusNode) statusNode.textContent = payload.status_label || payload.status;
        if (cancelNode) cancelNode.hidden = !!payload.finished;
        if (closeNode) closeNode.hidden = !payload.session_open;
        if (!payload.finished || payload.session_open) setTimeout(tick, 800);
      })
      .catch(function () { setTimeout(tick, 3000); });
  }
  setTimeout(tick, 800);
})();
