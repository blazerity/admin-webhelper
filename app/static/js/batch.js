(() => {
  const POLL_MS = 2000;
  const STATUS_LABELS = {
    pending: "ожидание",
    running: "выполняется",
    success: "успешно",
    failed: "ошибка",
    cancelled: "остановлен",
    error: "ошибка",
  };
  const RUN_TYPE_LABELS = {
    ping: "Ping",
    script: "Скрипт",
    command: "Команда",
    tracert: "Трассировка",
  };
  const root = document.getElementById("batch-progress");
  if (!root) return;

  const statusUrl = root.dataset.statusUrl;
  if (!statusUrl) return;

  const progressLabel = document.getElementById("batch-progress-label");
  const progressBar = document.getElementById("batch-progress-bar");
  const pollStatus = document.getElementById("batch-poll-status");
  const runsBody = document.getElementById("batch-runs");
  const metricIds = {
    total: document.getElementById("batch-total"),
    success: document.getElementById("batch-success"),
    failed: document.getElementById("batch-failed"),
    cancelled: document.getElementById("batch-cancelled"),
    pending: document.getElementById("batch-pending"),
    running: document.getElementById("batch-running"),
  };

  let finished = root.dataset.finished === "1";
  let timer = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function setMetric(key, value) {
    const el = metricIds[key];
    if (el) el.textContent = String(value ?? 0);
  }

  function doneCount(payload) {
    return (
      Number(payload.success || 0) +
      Number(payload.failed || 0) +
      Number(payload.cancelled || 0)
    );
  }

  function statusLabel(status) {
    return STATUS_LABELS[status] || status || "—";
  }

  function runTypeLabel(runType) {
    return RUN_TYPE_LABELS[runType] || runType || "—";
  }

  function ensureRow(run) {
    if (!runsBody) return null;
    let row = runsBody.querySelector(`[data-run-id="${run.id}"]`);
    if (row) return row;

    const empty = runsBody.querySelector("[data-batch-empty]");
    if (empty) empty.remove();

    row = document.createElement("tr");
    row.dataset.runId = String(run.id);
    row.innerHTML = `
      <td><a href="${escapeHtml(run.url || `/scripts/runs/${run.id}`)}">${escapeHtml(run.hostname || run.ip || `запуск #${run.id}`)}</a></td>
      <td class="font-monospace">${escapeHtml(run.ip || "—")}</td>
      <td>${escapeHtml(runTypeLabel(run.run_type))}</td>
      <td><span class="batch-run-status" data-status=""></span></td>
      <td class="text-end"><a class="btn btn-sm btn-ghost" href="${escapeHtml(run.url || `/scripts/runs/${run.id}`)}">Открыть</a></td>
    `;
    runsBody.appendChild(row);
    return row;
  }

  function updateRow(run) {
    const row = ensureRow(run);
    if (!row) return;
    row.dataset.runStatus = run.status || "";
    const statusEl = row.querySelector(".batch-run-status");
    if (statusEl) {
      statusEl.dataset.status = run.status || "";
      statusEl.textContent = statusLabel(run.status);
    }
    const nameLink = row.querySelector("td:first-child a");
    if (nameLink) {
      const label = run.hostname || run.ip || `запуск #${run.id}`;
      nameLink.textContent = label;
      if (run.url) nameLink.setAttribute("href", run.url);
    }
    const ipCell = row.querySelector("td.font-monospace");
    if (ipCell && run.ip) ipCell.textContent = run.ip;
  }

  function apply(payload) {
    if (!payload) return;

    setMetric("total", payload.total);
    setMetric("success", payload.success);
    setMetric("failed", payload.failed);
    setMetric("cancelled", payload.cancelled);
    setMetric("pending", payload.pending);
    setMetric("running", payload.running);

    const total = Number(payload.total || 0);
    const done = doneCount(payload);
    if (progressLabel) {
      progressLabel.textContent = `${done}/${total}`;
    }
    if (progressBar) {
      const pct = total > 0 ? Math.min(100, (done * 100) / total) : 0;
      progressBar.style.width = `${pct}%`;
      const barWrap = progressBar.parentElement;
      if (barWrap) {
        barWrap.setAttribute("aria-valuenow", String(done));
        barWrap.setAttribute("aria-valuemax", String(total || 1));
      }
    }

    (payload.runs || []).forEach(updateRow);

    finished = Boolean(payload.finished);
    root.dataset.finished = finished ? "1" : "0";
    if (pollStatus) {
      pollStatus.textContent = finished ? "Завершено" : "Обновление каждые 2 с";
    }

    if (finished) {
      stopPolling();
    }
  }

  function stopPolling() {
    if (timer != null) {
      window.clearTimeout(timer);
      timer = null;
    }
  }

  function scheduleNext() {
    stopPolling();
    if (finished) return;
    timer = window.setTimeout(tick, POLL_MS);
  }

  function tick() {
    fetch(statusUrl, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.json();
      })
      .then((payload) => {
        apply(payload);
        scheduleNext();
      })
      .catch(() => {
        if (pollStatus) {
          pollStatus.textContent = "Не удалось обновить статус — повтор…";
        }
        // Keep trying while not finished.
        if (!finished) {
          timer = window.setTimeout(tick, POLL_MS * 1.5);
        }
      });
  }

  // Initial paint may already be finished from SSR.
  if (!finished) {
    timer = window.setTimeout(tick, POLL_MS);
  }
})();
