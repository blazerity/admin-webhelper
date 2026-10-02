(() => {
  const REFRESH_MS = 15000;
  const STATUS_LABELS = {
    online: "доступен",
    offline: "недоступен",
    unknown: "неизвестно",
  };

  const mapRoot = document.getElementById("sector-map");
  const filtersRoot = document.getElementById("map-filters");
  const resetBtn = document.getElementById("map-filters-reset");
  const refreshStatus = document.getElementById("map-refresh-status");
  const filtersEmpty = document.getElementById("map-filters-empty");

  if (!mapRoot || !filtersRoot) {
    return;
  }

  function selectedValues(group) {
    return Array.from(
      filtersRoot.querySelectorAll(
        `[data-filter-group="${group}"].active`
      )
    ).map((btn) => btn.dataset.filterValue);
  }

  function applyFilters() {
    const statuses = selectedValues("status");
    const types = selectedValues("type");
    let anyVisibleSector = false;

    mapRoot.querySelectorAll(".sector-panel").forEach((panel) => {
      const cards = panel.querySelectorAll("[data-device-card]");
      let visibleCount = 0;

      cards.forEach((card) => {
        const statusOk =
          statuses.length === 0 || statuses.includes(card.dataset.status);
        const typeOk =
          types.length === 0 || types.includes(card.dataset.type);
        const show = statusOk && typeOk;
        card.classList.toggle("d-none", !show);
        if (show) {
          visibleCount += 1;
        }
      });

      const emptyFilterHint = panel.querySelector("[data-sector-empty-filter]");
      const hasDevices = cards.length > 0;
      const hidePanel = hasDevices && visibleCount === 0 && (statuses.length > 0 || types.length > 0);
      panel.classList.toggle("d-none", hidePanel);
      if (emptyFilterHint) {
        emptyFilterHint.classList.add("d-none");
      }
      if (!hidePanel && hasDevices) {
        anyVisibleSector = true;
      }
      if (!hasDevices) {
        anyVisibleSector = true;
      }
    });

    if (filtersEmpty) {
      const filtering = statuses.length > 0 || types.length > 0;
      filtersEmpty.classList.toggle("d-none", !filtering || anyVisibleSector);
    }
  }

  function toggleFilterButton(btn) {
    const active = !btn.classList.contains("active");
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    applyFilters();
  }

  filtersRoot.querySelectorAll("[data-filter-group]").forEach((btn) => {
    btn.addEventListener("click", () => toggleFilterButton(btn));
  });

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      filtersRoot.querySelectorAll("[data-filter-group]").forEach((btn) => {
        btn.classList.remove("active");
        btn.setAttribute("aria-pressed", "false");
      });
      applyFilters();
    });
  }

  function setStatusClasses(el, status) {
    el.classList.remove("online", "offline", "unknown");
    if (status) {
      el.classList.add(status);
    }
  }

  function updateStatusDot(cardLink, status) {
    const dot = cardLink.querySelector(".status-dot");
    if (!dot) {
      return;
    }
    setStatusClasses(dot, status);
    const label = STATUS_LABELS[status] || STATUS_LABELS.unknown;
    dot.title = label;
    dot.setAttribute("aria-label", label);
  }

  function applyStatusPayload(payload) {
    (payload.sectors || []).forEach((sector) => {
      const panel = mapRoot.querySelector(
        `.sector-panel[data-sector-id="${sector.id}"]`
      );
      if (!panel) {
        return;
      }

      const onlineEl = panel.querySelector("[data-sector-online]");
      const totalEl = panel.querySelector("[data-sector-total]");
      if (onlineEl) {
        onlineEl.textContent = String(sector.online);
      }
      if (totalEl) {
        totalEl.textContent = String(sector.total);
      }

      (sector.devices || []).forEach((device) => {
        const card = panel.querySelector(
          `[data-device-card][data-device-id="${device.id}"]`
        );
        if (!card) {
          return;
        }
        card.dataset.status = device.status;
        card.dataset.type = device.kind;
        const link = card.querySelector("[data-device-link]");
        if (link) {
          setStatusClasses(link, device.status);
          updateStatusDot(link, device.status);
        }
      });
    });

    applyFilters();

    if (refreshStatus && payload.updated_at) {
      const stamp = new Date(payload.updated_at);
      const time = Number.isNaN(stamp.getTime())
        ? ""
        : stamp.toLocaleTimeString("ru-RU", {
            hour: "2-digit",
            minute: "2-digit",
            second: "2-digit",
          });
      refreshStatus.textContent = time
        ? `Обновлено в ${time}`
        : "Статусы обновлены";
    }
  }

  async function refreshStatuses() {
    const url = mapRoot.dataset.statusUrl;
    if (!url) {
      return;
    }
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      applyStatusPayload(payload);
    } catch (_err) {
      if (refreshStatus) {
        refreshStatus.textContent = "Не удалось обновить статусы";
      }
    }
  }

  applyFilters();
  window.setInterval(refreshStatuses, REFRESH_MS);
})();
