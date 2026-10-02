(() => {
  const list = document.getElementById("login-status-list");
  if (!list) return;

  const url = list.dataset.statusUrl;
  if (!url) return;

  const REFRESH_MS = 15000;

  function setItem(row, online, latencyMs) {
    const dot = row.querySelector(".status-dot");
    const latency = row.querySelector('[data-role="latency"]');
    if (!dot || !latency) return;
    dot.classList.remove("online", "offline", "unknown");
    if (online) {
      dot.classList.add("online");
      latency.textContent = latencyMs == null ? "ок" : `${latencyMs} ms`;
      latency.classList.remove("is-offline");
    } else {
      dot.classList.add("offline");
      latency.textContent = "нет";
      latency.classList.add("is-offline");
    }
  }

  async function refresh() {
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      const byId = new Map(
        (payload.services || []).map((item) => [String(item.id), item])
      );
      list.querySelectorAll(".login-status-item").forEach((row) => {
        const item = byId.get(String(row.dataset.serviceId));
        if (!item) {
          setItem(row, false, null);
          return;
        }
        setItem(row, Boolean(item.online), item.latency_ms);
      });
    } catch (_err) {
      list.querySelectorAll(".login-status-item").forEach((row) => {
        const dot = row.querySelector(".status-dot");
        const latency = row.querySelector('[data-role="latency"]');
        if (dot) {
          dot.classList.remove("online", "offline");
          dot.classList.add("unknown");
        }
        if (latency) {
          latency.textContent = "—";
          latency.classList.remove("is-offline");
        }
      });
    }
  }

  refresh();
  window.setInterval(refresh, REFRESH_MS);
})();
