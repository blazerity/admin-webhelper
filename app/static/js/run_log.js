// Обновление лога запуска раз в 1.5 секунды, пока статус не финальный.
// WebSocket сознательно не используем: хватает обычного GET, и это работает
// на синхронных воркерах Gunicorn. Позже этот файл можно заменить подпиской.
(function () {
  const script = document.currentScript;
  const url = script && script.dataset.statusUrl;
  const logNode = document.getElementById("run-log");
  const statusNode = document.getElementById("run-status");
  if (!url || !logNode) return;

  function tick() {
    fetch(url, { headers: { "Accept": "application/json" } })
      .then(function (response) { return response.json(); })
      .then(function (payload) {
        logNode.textContent = payload.log_text || "";
        if (statusNode) statusNode.textContent = payload.status;
        if (!payload.finished) setTimeout(tick, 1500);
      })
      .catch(function () { setTimeout(tick, 3000); });
  }
  setTimeout(tick, 1500);
})();
