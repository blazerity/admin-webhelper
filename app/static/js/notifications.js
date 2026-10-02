(() => {
  const root = document.getElementById("app-notifications");
  if (!root) return;

  const listUrl = root.dataset.listUrl || "/api/notifications";
  const readUrl = root.dataset.readUrl || "/api/notifications/read";
  const csrf = root.dataset.csrfToken || "";
  const pollMs = Number(root.dataset.pollMs || 30000);

  const toggle = root.querySelector("[data-role='toggle']");
  const panel = root.querySelector("[data-role='panel']");
  const badge = root.querySelector("[data-role='badge']");
  const listEl = root.querySelector("[data-role='list']");
  const emptyEl = root.querySelector("[data-role='empty']");
  const markAllBtn = root.querySelector("[data-role='mark-all']");

  let open = false;
  let items = [];

  function setBadge(unread) {
    if (!badge) return;
    const count = Number(unread) || 0;
    if (count <= 0) {
      badge.hidden = true;
      badge.textContent = "";
      return;
    }
    badge.hidden = false;
    badge.textContent = count > 99 ? "99+" : String(count);
  }

  function formatWhen(iso) {
    if (!iso) return "";
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return date.toLocaleString("ru-RU", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (_err) {
      return date.toISOString();
    }
  }

  function renderList() {
    if (!listEl || !emptyEl) return;
    listEl.innerHTML = "";
    if (!items.length) {
      emptyEl.hidden = false;
      return;
    }
    emptyEl.hidden = true;
    const frag = document.createDocumentFragment();
    items.forEach((item) => {
      const row = document.createElement(item.link_url ? "a" : "div");
      row.className = "notif-item" + (item.unread ? " is-unread" : "");
      row.dataset.id = String(item.id);
      if (item.link_url) {
        row.href = item.link_url;
      }
      const title = document.createElement("p");
      title.className = "notif-item-title";
      title.textContent = item.title || "Уведомление";
      const body = document.createElement("p");
      body.className = "notif-item-body";
      body.textContent = item.body || "";
      if (!item.body) body.hidden = true;
      const meta = document.createElement("p");
      meta.className = "notif-item-meta";
      meta.textContent = formatWhen(item.created_at);
      row.append(title, body, meta);
      frag.appendChild(row);
    });
    listEl.appendChild(frag);
  }

  async function markRead(payload) {
    const response = await fetch(readUrl, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": csrf,
      },
      body: JSON.stringify({ ...payload, csrf_token: csrf }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  }

  async function refresh() {
    try {
      const response = await fetch(listUrl, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = await response.json();
      items = Array.isArray(payload.items) ? payload.items : [];
      setBadge(payload.unread);
      if (open) renderList();
    } catch (_err) {
      /* keep last good state */
    }
  }

  function setOpen(next) {
    open = Boolean(next);
    root.classList.toggle("is-open", open);
    if (toggle) toggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (panel) panel.hidden = !open;
    if (open) {
      renderList();
      const unreadIds = items.filter((item) => item.unread).map((item) => item.id);
      if (unreadIds.length) {
        markRead({ ids: unreadIds })
          .then(() => refresh())
          .catch(() => {});
      }
    }
  }

  if (toggle) {
    toggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(!open);
    });
  }

  if (markAllBtn) {
    markAllBtn.addEventListener("click", (event) => {
      event.preventDefault();
      markRead({ all: true })
        .then(() => refresh())
        .catch(() => {});
    });
  }

  if (listEl) {
    listEl.addEventListener("click", (event) => {
      const row = event.target.closest(".notif-item");
      if (!row) return;
      const id = Number(row.dataset.id);
      if (!id) return;
      const item = items.find((entry) => entry.id === id);
      if (item && item.unread) {
        markRead({ ids: [id] }).catch(() => {});
      }
    });
  }

  document.addEventListener("click", (event) => {
    if (!open) return;
    if (root.contains(event.target)) return;
    setOpen(false);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && open) setOpen(false);
  });

  refresh();
  if (pollMs > 0) {
    window.setInterval(refresh, pollMs);
  }
})();
