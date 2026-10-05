(() => {
  const root = document.getElementById("device-object");
  const btn = document.getElementById("device-check-btn");
  const hint = document.getElementById("device-check-hint");
  const http = window.BawhHttp || {};
  if (!root || !btn || !http.postJson) {
    return;
  }
  if (root.dataset.canCheck !== "1") {
    return;
  }

  const STATUS_LABELS = {
    online: "доступен",
    offline: "недоступен",
    unknown: "неизвестно",
  };
  const STATUS_BADGE_CLASS = {
    online: "badge text-bg-success",
    offline: "badge text-bg-danger",
    unknown: "badge text-bg-secondary",
  };

  function setHint(text, tone) {
    if (!hint) return;
    hint.textContent = text;
    hint.classList.toggle("is-changed", tone === "changed");
    hint.classList.toggle("is-error", tone === "error");
  }

  function renderBadge(host, status) {
    if (!host) return;
    const resolved = STATUS_LABELS[status] ? status : "unknown";
    host.innerHTML = `<span class="${STATUS_BADGE_CLASS[resolved]}" data-status-badge>${STATUS_LABELS[resolved]}</span>`;
  }

  function formatStamp(iso) {
    if (!iso) return "—";
    const stamp = new Date(iso);
    if (Number.isNaN(stamp.getTime())) return String(iso);
    return stamp.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function applyResult(payload) {
    const status = payload.status || "unknown";
    root.dataset.status = status;
    root.classList.remove("online", "offline", "unknown");
    root.classList.add(status);

    renderBadge(root.querySelector("[data-device-status-host]"), status);
    renderBadge(root.querySelector("[data-device-status-meta]"), status);

    const lastSeen = root.querySelector("[data-device-last-seen]");
    if (lastSeen && payload.last_seen) {
      lastSeen.textContent = formatStamp(payload.last_seen);
    }

    const rtt = root.querySelector("[data-device-rtt]");
    if (rtt) {
      rtt.textContent =
        payload.response_time_ms != null ? `${payload.response_time_ms} мс` : "—";
    }

    const label = STATUS_LABELS[status] || STATUS_LABELS.unknown;
    if (payload.changed) {
      setHint(`Статус изменился: устройство ${label}.`, "changed");
    } else {
      setHint(`Устройство ${label}.`, "");
    }
  }

  async function runCheck() {
    const url = root.dataset.checkUrl;
    const csrf = root.dataset.csrfToken || "";
    if (!url) return;
    btn.disabled = true;
    setHint("Проверяем…", "");
    try {
      const payload = await http.postJson(url, {}, csrf);
      applyResult(payload || {});
    } catch (err) {
      setHint(err.message || "Не удалось выполнить проверку.", "error");
    } finally {
      btn.disabled = false;
    }
  }

  btn.addEventListener("click", () => {
    runCheck();
  });
})();
