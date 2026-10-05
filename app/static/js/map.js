(() => {
  const http = window.BawhHttp || {};
  const escapeHtml = http.escapeHtml || ((value) => String(value ?? ""));
  const fetchJson = http.fetchJson;
  const postJson = http.postJson;
  const REFRESH_MS = 15000;
  const FAV_KEY = "bawh.map.favorites";
  const STATUS_LABELS = {
    online: "доступен",
    offline: "недоступен",
    unknown: "неизвестно",
  };
  const STATUS_SHORT = {
    online: "Онлайн",
    offline: "Офлайн",
    unknown: "Неизв.",
  };
  const KIND_LABELS = {
    notebook: "Ноутбук",
    desktop: "СБ",
    vds: "VDS",
    server: "Сервер",
    firewall: "Firewall",
    router: "MikroTik",
    camera: "Камера",
    printer: "МФУ",
    other: "Прочее",
  };
  const KIND_ORDER = [
    "notebook",
    "desktop",
    "vds",
    "server",
    "firewall",
    "router",
    "camera",
    "printer",
    "other",
  ];
  const TYPE_ICONS = {
    notebook: `<span class="device-type-icon" title="Ноутбук" aria-label="Ноутбук"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="12" rx="1.5"></rect><path d="M2 20h20"></path><path d="M8 20h8"></path></svg></span>`,
    desktop: `<span class="device-type-icon" title="СБ" aria-label="СБ"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="18" height="13" rx="1.5"></rect><path d="M8 20h8"></path><path d="M12 16v4"></path></svg></span>`,
    vds: `<span class="device-type-icon" title="VDS" aria-label="VDS"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="3" width="20" height="14" rx="2"></rect><path d="M8 21h8"></path><path d="M12 17v4"></path><path d="m15 8 5-5"></path><path d="M16 3h5v5"></path></svg></span>`,
    server: `<span class="device-type-icon" title="Сервер" aria-label="Сервер"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="2" width="20" height="8" rx="2"></rect><rect x="2" y="14" width="20" height="8" rx="2"></rect><circle cx="6" cy="6" r="0.8" fill="currentColor" stroke="none"></circle><circle cx="6" cy="18" r="0.8" fill="currentColor" stroke="none"></circle></svg></span>`,
    firewall: `<span class="device-type-icon" title="Сетевое устройство" aria-label="Сетевое устройство"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="10" width="18" height="10" rx="2"></rect><path d="M7 10V7a5 5 0 0 1 10 0v3"></path><circle cx="8" cy="15" r="0.8" fill="currentColor" stroke="none"></circle><circle cx="12" cy="15" r="0.8" fill="currentColor" stroke="none"></circle><circle cx="16" cy="15" r="0.8" fill="currentColor" stroke="none"></circle></svg></span>`,
    router: `<span class="device-type-icon" title="MikroTik" aria-label="MikroTik"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="4" y="8" width="16" height="10" rx="2"></rect><path d="M8 8V5"></path><path d="M16 8V5"></path><circle cx="8" cy="4" r="1"></circle><circle cx="16" cy="4" r="1"></circle><path d="M8 13h.01"></path><path d="M12 13h.01"></path><path d="M16 13h.01"></path></svg></span>`,
    camera: `<span class="device-type-icon" title="Камера" aria-label="Камера"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 8h11a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2z"></path><path d="m17 11 5-3v8l-5-3"></path><circle cx="9.5" cy="14.5" r="2.2"></circle></svg></span>`,
    printer: `<span class="device-type-icon" title="МФУ" aria-label="МФУ"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8V4h12v4"></path><rect x="4" y="8" width="16" height="8" rx="1.5"></rect><path d="M6 16h12v4H6z"></path><path d="M7 12h.01"></path></svg></span>`,
    other: `<span class="device-type-icon muted" title="Прочее" aria-label="Прочее"><svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"></circle><path d="M9.1 9a3 3 0 0 1 5.8 1c0 2-3 2.5-3 4.5"></path><circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none"></circle></svg></span>`,
  };
  const FILTER_PARAM_KEYS = ["status", "type", "fav"];
  const DEFAULT_TYPES = ["notebook", "desktop"];
  const LAYOUT_KEY = "bawh.map.layout";
  const SORT_KEY = "bawh.map.sort";
  const VIEW_KEY = "bawh.map.view";
  const RESTORE_KEY = "bawh.map.restore";
  const LAYOUTS = ["list", "cards", "tiles"];
  const SORTS = ["hostname", "ip", "mac"];

  const mapRoot = document.getElementById("sector-map");
  const filtersRoot = document.getElementById("map-filters");
  const resetBtn = document.getElementById("map-filters-reset");
  const refreshStatus = document.getElementById("map-refresh-status");
  const filtersEmpty = document.getElementById("map-filters-empty");
  const summaryRoot = document.getElementById("map-summary");
  const summaryStatus = document.getElementById("map-summary-status");
  const summaryMetrics = document.getElementById("map-summary-metrics");
  const bulkBar = document.getElementById("map-bulk-bar");
  const bulkCount = document.getElementById("map-bulk-count");
  const bulkPingBtn = document.getElementById("map-bulk-ping");
  const bulkClearBtn = document.getElementById("map-bulk-clear");
  const bulkError = document.getElementById("map-bulk-error");
  const bulkScriptConfirm = document.getElementById("map-bulk-script-confirm");
  const bulkScriptSelect = document.getElementById("map-bulk-script-select");
  const sortSelect = document.getElementById("map-sort");
  const favFilterBtn = filtersRoot
    ? filtersRoot.querySelector("[data-filter-fav]")
    : null;
  const layoutButtons = filtersRoot
    ? Array.from(filtersRoot.querySelectorAll("[data-map-layout]"))
    : [];

  if (!mapRoot || !filtersRoot || !fetchJson || !postJson) {
    return;
  }

  /** @type {Set<number>} */
  let favorites = loadFavorites();
  /** @type {Set<string>} */
  const selectedIds = new Set();
  /** @type {string} */
  let searchQuery = "";
  /** @type {string} */
  let searchSectorId = "";
  let favOnly = false;
  let restoringView = false;
  /** @type {string} */
  let currentLayout = loadLayout();
  /** @type {string} */
  let currentSort = loadSort();

  function loadFavorites() {
    try {
      const raw = localStorage.getItem(FAV_KEY);
      if (!raw) return new Set();
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return new Set();
      return new Set(
        parsed
          .map((id) => Number(id))
          .filter((id) => Number.isFinite(id) && id > 0)
      );
    } catch (_err) {
      return new Set();
    }
  }

  function saveFavorites() {
    try {
      localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(favorites)));
    } catch (_err) {
      /* ignore quota / private mode */
    }
  }

  function loadLayout() {
    try {
      const raw = localStorage.getItem(LAYOUT_KEY);
      if (LAYOUTS.includes(raw)) {
        return raw;
      }
    } catch (_err) {
      /* ignore */
    }
    return "cards";
  }

  function saveLayout(layout) {
    try {
      localStorage.setItem(LAYOUT_KEY, layout);
    } catch (_err) {
      /* ignore quota / private mode */
    }
  }

  function loadSort() {
    try {
      const raw = localStorage.getItem(SORT_KEY);
      if (SORTS.includes(raw)) {
        return raw;
      }
    } catch (_err) {
      /* ignore */
    }
    return "hostname";
  }

  function saveSort(sort) {
    try {
      localStorage.setItem(SORT_KEY, sort);
    } catch (_err) {
      /* ignore quota / private mode */
    }
  }

  function applyLayoutToGrids() {
    mapRoot.querySelectorAll("[data-sector-devices]").forEach((row) => {
      row.dataset.layout = currentLayout;
    });
    layoutButtons.forEach((btn) => {
      const active = btn.dataset.mapLayout === currentLayout;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function setLayout(layout) {
    if (!LAYOUTS.includes(layout)) {
      return;
    }
    currentLayout = layout;
    saveLayout(layout);
    applyLayoutToGrids();
  }

  function syncSortSelect() {
    if (sortSelect) {
      sortSelect.value = currentSort;
    }
  }

  function setSort(sort) {
    if (!SORTS.includes(sort)) {
      return;
    }
    currentSort = sort;
    saveSort(sort);
    syncSortSelect();
    sortAllSectorCards();
  }

  function formatPollStamp(lastPoll) {
    if (!lastPoll) {
      return "нет данных";
    }
    const raw = lastPoll.finished_at || lastPoll.started_at;
    if (!raw) {
      return lastPoll.mode || "нет данных";
    }
    const stamp = new Date(raw);
    if (Number.isNaN(stamp.getTime())) {
      return String(raw);
    }
    const time = stamp.toLocaleTimeString("ru-RU", {
      hour: "2-digit",
      minute: "2-digit",
    });
    const mode = lastPoll.mode ? ` · ${lastPoll.mode}` : "";
    return `${time}${mode}`;
  }

  function setSummaryText(key, value) {
    if (!summaryMetrics) return;
    const el = summaryMetrics.querySelector(`[data-summary="${key}"]`);
    if (el) {
      el.textContent = value;
    }
  }

  function renderSummary(payload) {
    if (!summaryRoot || !summaryStatus || !summaryMetrics) {
      return;
    }
    setSummaryText("online", String(payload.devices_online ?? "—"));
    setSummaryText("offline", String(payload.devices_offline ?? "—"));
    setSummaryText("unknown", String(payload.devices_unknown ?? "—"));
    setSummaryText("total", String(payload.devices_total ?? "—"));
    setSummaryText("last-poll", formatPollStamp(payload.last_poll));
    setSummaryText(
      "failed-scripts",
      String(payload.failed_script_runs_24h ?? "—")
    );
    summaryMetrics.classList.remove("d-none");
    summaryStatus.classList.add("d-none");
    summaryRoot.classList.remove("is-empty", "is-error");
  }

  function renderSummaryEmpty(message, isError) {
    if (!summaryRoot || !summaryStatus || !summaryMetrics) {
      return;
    }
    summaryMetrics.classList.add("d-none");
    summaryStatus.classList.remove("d-none");
    summaryStatus.textContent = message;
    summaryRoot.classList.toggle("is-error", Boolean(isError));
    summaryRoot.classList.toggle("is-empty", !isError);
  }

  async function refreshSummary() {
    if (!summaryRoot) {
      return;
    }
    const url = summaryRoot.dataset.summaryUrl || "/api/network/summary";
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      const payload = await response.json();
      if (
        payload == null ||
        (payload.devices_total == null &&
          payload.devices_online == null &&
          !payload.last_poll)
      ) {
        renderSummaryEmpty("Сводка сети пока пуста.", false);
        return;
      }
      renderSummary(payload);
    } catch (_err) {
      renderSummaryEmpty(
        "Сводка сети недоступна — карта работает без неё.",
        true
      );
    }
  }

  function selectedValues(group) {
    return Array.from(
      filtersRoot.querySelectorAll(`[data-filter-group="${group}"].active`)
    ).map((btn) => btn.dataset.filterValue);
  }

  function parseListParam(params, key) {
    const values = [];
    params.getAll(key).forEach((raw) => {
      String(raw)
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .forEach((part) => values.push(part));
    });
    return [...new Set(values)];
  }

  function syncUrlFromFilters() {
    const url = new URL(window.location.href);
    const params = url.searchParams;
    FILTER_PARAM_KEYS.forEach((key) => params.delete(key));

    const statuses = selectedValues("status");
    const types = selectedValues("type");
    if (statuses.length) {
      params.set("status", statuses.join(","));
    }
    if (types.length) {
      params.set("type", types.join(","));
    }
    if (favOnly) {
      params.set("fav", "1");
    }

    const next = `${url.pathname}${params.toString() ? `?${params}` : ""}${url.hash}`;
    window.history.replaceState(null, "", next);
  }

  function applyUrlToFilters() {
    const params = new URLSearchParams(window.location.search);
    const statuses = new Set(parseListParam(params, "status"));
    const hasTypeParam = params.has("type");
    const types = hasTypeParam
      ? new Set(parseListParam(params, "type"))
      : new Set(DEFAULT_TYPES);
    favOnly = params.get("fav") === "1";

    filtersRoot.querySelectorAll("[data-filter-group]").forEach((btn) => {
      const group = btn.dataset.filterGroup;
      const value = btn.dataset.filterValue;
      const active =
        (group === "status" && statuses.has(value)) ||
        (group === "type" && types.has(value));
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });

    if (favFilterBtn) {
      favFilterBtn.classList.toggle("active", favOnly);
      favFilterBtn.setAttribute("aria-pressed", favOnly ? "true" : "false");
    }
  }

  function applyDefaultTypeFilters() {
    filtersRoot.querySelectorAll('[data-filter-group="type"]').forEach((btn) => {
      const active = DEFAULT_TYPES.includes(btn.dataset.filterValue);
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function searchIsActive() {
    return searchQuery.length >= 2 || Boolean(searchSectorId);
  }

  function resolveKind(kind) {
    return KIND_LABELS[kind] ? kind : "other";
  }

  function cardMatchesSearch(card, query) {
    if (query.length < 2) {
      return true;
    }
    const hay = [
      card.dataset.hostname,
      card.dataset.ip,
      card.dataset.mac,
      card.dataset.serial,
      card.textContent,
    ]
      .join(" ")
      .toLowerCase();
    return hay.includes(query);
  }

  function collapseEl(panel) {
    return panel.querySelector(".collapse");
  }

  function setPanelExpanded(panel, expanded) {
    const body = collapseEl(panel);
    const btn = panel.querySelector(".sector-toggle");
    if (!body || !btn) {
      return;
    }
    const Collapse = window.bootstrap && window.bootstrap.Collapse;
    if (Collapse) {
      const instance = Collapse.getOrCreateInstance(body, { toggle: false });
      if (expanded) {
        instance.show();
      } else {
        instance.hide();
      }
    } else {
      body.classList.toggle("show", expanded);
    }
    btn.classList.toggle("collapsed", !expanded);
    btn.setAttribute("aria-expanded", expanded ? "true" : "false");
  }

  function getExpandedSectorIds() {
    return Array.from(mapRoot.querySelectorAll(".sector-panel"))
      .filter((panel) => {
        const btn = panel.querySelector(".sector-toggle");
        return btn && btn.getAttribute("aria-expanded") === "true";
      })
      .map((panel) => String(panel.dataset.sectorId || ""));
  }

  function saveMapView(deviceId) {
    try {
      sessionStorage.setItem(
        VIEW_KEY,
        JSON.stringify({
          url: `${window.location.pathname}${window.location.search}`,
          scrollY: window.scrollY,
          expanded: getExpandedSectorIds(),
          deviceId: deviceId ? String(deviceId) : null,
        })
      );
      sessionStorage.setItem(RESTORE_KEY, "1");
    } catch (_err) {
      /* ignore quota / private mode */
    }
  }

  function restoreMapView() {
    if (sessionStorage.getItem(RESTORE_KEY) !== "1") {
      return;
    }
    sessionStorage.removeItem(RESTORE_KEY);
    let state = null;
    try {
      state = JSON.parse(sessionStorage.getItem(VIEW_KEY) || "null");
    } catch (_err) {
      return;
    }
    if (!state || typeof state !== "object") {
      return;
    }

    restoringView = true;
    const expanded = new Set(
      Array.isArray(state.expanded) ? state.expanded.map(String) : []
    );
    let returnedCard = null;
    if (state.deviceId) {
      returnedCard = mapRoot.querySelector(
        `[data-device-card][data-device-id="${state.deviceId}"]`
      );
      if (returnedCard) {
        const panel = returnedCard.closest(".sector-panel");
        if (panel && panel.dataset.sectorId) {
          expanded.add(String(panel.dataset.sectorId));
        }
        returnedCard.classList.add("is-returned");
        window.setTimeout(() => {
          returnedCard.classList.remove("is-returned");
        }, 2400);
      }
    }

    mapRoot.querySelectorAll(".sector-panel").forEach((panel) => {
      setPanelExpanded(panel, expanded.has(String(panel.dataset.sectorId)));
    });

    const y = Number(state.scrollY);
    const applyScroll = () => {
      window.scrollTo(0, Number.isFinite(y) ? y : 0);
    };
    applyScroll();
    window.setTimeout(applyScroll, 50);
    window.setTimeout(applyScroll, 360);
    restoringView = false;
  }

  function hostnameSortKey(hostname, ip) {
    const name = String(hostname || "").trim().toLowerCase();
    const ipKey = String(ip || "");
    if (!name) {
      return `~\t${ipKey}`;
    }
    return `${name.replace(/\d+/g, (digits) => digits.padStart(10, "0"))}\t${ipKey}`;
  }

  function ipSortParts(ip) {
    const bits = String(ip || "")
      .trim()
      .split(".");
    if (bits.length === 4) {
      const nums = bits.map((part) => Number(part));
      if (nums.every((n) => Number.isFinite(n) && n >= 0 && n <= 255)) {
        return nums;
      }
    }
    return [999, 999, 999, 999];
  }

  function compareIp(left, right) {
    const a = ipSortParts(left);
    const b = ipSortParts(right);
    for (let i = 0; i < 4; i += 1) {
      if (a[i] !== b[i]) {
        return a[i] - b[i];
      }
    }
    return 0;
  }

  function macSortKey(mac) {
    const normalized = String(mac || "")
      .trim()
      .toLowerCase()
      .replace(/[^0-9a-f]/g, "");
    if (!normalized) {
      return "~";
    }
    return normalized.padStart(12, "0");
  }

  function compareCards(left, right) {
    if (currentSort === "ip") {
      const byIp = compareIp(left.dataset.ip, right.dataset.ip);
      if (byIp !== 0) return byIp;
      const a = hostnameSortKey(left.dataset.hostname, left.dataset.ip);
      const b = hostnameSortKey(right.dataset.hostname, right.dataset.ip);
      if (a < b) return -1;
      if (a > b) return 1;
      return 0;
    }
    if (currentSort === "mac") {
      const aMac = macSortKey(left.dataset.mac);
      const bMac = macSortKey(right.dataset.mac);
      if (aMac < bMac) return -1;
      if (aMac > bMac) return 1;
      return compareIp(left.dataset.ip, right.dataset.ip);
    }
    const a = hostnameSortKey(left.dataset.hostname, left.dataset.ip);
    const b = hostnameSortKey(right.dataset.hostname, right.dataset.ip);
    if (a < b) return -1;
    if (a > b) return 1;
    return 0;
  }

  function sortSectorCards(row) {
    if (!row) {
      return;
    }
    const cards = Array.from(row.querySelectorAll("[data-device-card]"));
    cards.sort(compareCards);
    cards.forEach((card) => row.appendChild(card));
  }

  function sortAllSectorCards() {
    mapRoot.querySelectorAll("[data-sector-devices]").forEach(sortSectorCards);
  }

  function renderSectorKindCounts(panel, cards) {
    const host = panel.querySelector("[data-sector-kinds]");
    if (!host) {
      return;
    }
    const counts = {};
    cards.forEach((card) => {
      const kind = resolveKind(card.dataset.type);
      counts[kind] = (counts[kind] || 0) + 1;
    });
    host.innerHTML = KIND_ORDER.filter((kind) => counts[kind])
      .map((kind) => {
        const label = KIND_LABELS[kind];
        return `<span class="sector-kind-stat" title="${escapeHtml(label)}">${
          TYPE_ICONS[kind] || TYPE_ICONS.other
        }<span>${counts[kind]}</span></span>`;
      })
      .join("");
  }

  function updateSectorHeaderFromVisible(panel, cards) {
    const visible = [];
    let online = 0;
    let offline = 0;
    let unknown = 0;
    cards.forEach((card) => {
      if (card.classList.contains("d-none")) {
        return;
      }
      visible.push(card);
      const status = card.dataset.status;
      if (status === "online") {
        online += 1;
      } else if (status === "offline") {
        offline += 1;
      } else {
        unknown += 1;
      }
    });
    const onlineEl = panel.querySelector("[data-sector-online]");
    const offlineEl = panel.querySelector("[data-sector-offline]");
    const unknownEl = panel.querySelector("[data-sector-unknown]");
    const totalEl = panel.querySelector("[data-sector-total]");
    if (onlineEl) onlineEl.textContent = String(online);
    if (offlineEl) offlineEl.textContent = String(offline);
    if (unknownEl) unknownEl.textContent = String(unknown);
    if (totalEl) totalEl.textContent = String(visible.length);
    renderSectorKindCounts(panel, visible);
  }

  function applyFilters(options) {
    const opts = options || {};
    const statuses = selectedValues("status");
    const types = selectedValues("type");
    const query = searchQuery;
    const searchActive = searchIsActive();
    let anyVisibleSector = false;

    mapRoot.querySelectorAll(".sector-panel").forEach((panel) => {
      const cards = panel.querySelectorAll("[data-device-card]");
      let visibleCount = 0;
      const sectorOk =
        !searchSectorId || String(panel.dataset.sectorId) === searchSectorId;

      cards.forEach((card) => {
        const deviceId = Number(card.dataset.deviceId);
        const statusOk =
          statuses.length === 0 || statuses.includes(card.dataset.status);
        const typeOk =
          types.length === 0 || types.includes(card.dataset.type);
        const searchOk = !searchActive || (sectorOk && cardMatchesSearch(card, query));
        const favOk = !favOnly || favorites.has(deviceId);
        const show = statusOk && typeOk && searchOk && favOk;
        card.classList.toggle("d-none", !show);
        if (show) {
          visibleCount += 1;
        }
      });

      const emptyFilterHint = panel.querySelector("[data-sector-empty-filter]");
      const emptyHint = panel.querySelector("[data-sector-empty]");
      const hasDevices = cards.length > 0;
      const filtering =
        statuses.length > 0 || types.length > 0 || searchActive || favOnly;
      const hidePanel =
        (hasDevices && visibleCount === 0 && filtering) ||
        (searchSectorId && !sectorOk);
      const hideEmptySector = !hasDevices && (searchActive || favOnly);
      panel.classList.toggle("d-none", hidePanel || hideEmptySector);
      if (emptyFilterHint) {
        emptyFilterHint.classList.toggle(
          "d-none",
          !(hasDevices && visibleCount === 0 && filtering && sectorOk)
        );
      }
      if (emptyHint) {
        emptyHint.classList.toggle("d-none", hasDevices);
      }
      if (!hidePanel && !hideEmptySector) {
        if (hasDevices || !(searchActive || favOnly)) {
          anyVisibleSector = true;
        }
      }

      updateSectorHeaderFromVisible(panel, cards);

      if (!restoringView && opts.expandMatches && searchActive && !hidePanel) {
        setPanelExpanded(panel, visibleCount > 0);
      } else if (!restoringView && opts.collapseAll) {
        setPanelExpanded(panel, false);
      }
    });

    if (filtersEmpty) {
      const filtering =
        statuses.length > 0 || types.length > 0 || searchActive || favOnly;
      filtersEmpty.textContent = searchActive
        ? "Нет секторов с подходящими машинами."
        : favOnly
          ? "Нет избранных машин по выбранным фильтрам."
          : "Ни один сектор не подходит под выбранные фильтры.";
      filtersEmpty.classList.toggle("d-none", !filtering || anyVisibleSector);
    }
  }

  function setSearch(query, sectorId) {
    const nextQuery = String(query || "").trim().toLowerCase();
    const nextSector = sectorId ? String(sectorId) : "";
    const wasActive = searchIsActive();
    searchQuery = nextQuery;
    searchSectorId = nextSector;
    const isActive = searchIsActive();
    applyFilters({
      expandMatches: isActive,
      collapseAll: wasActive && !isActive,
    });
  }

  function toggleFilterButton(btn) {
    const active = !btn.classList.contains("active");
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
    applyFilters();
    syncUrlFromFilters();
  }

  filtersRoot.querySelectorAll("[data-filter-group]").forEach((btn) => {
    btn.addEventListener("click", () => toggleFilterButton(btn));
  });

  if (favFilterBtn) {
    favFilterBtn.addEventListener("click", () => {
      favOnly = !favOnly;
      favFilterBtn.classList.toggle("active", favOnly);
      favFilterBtn.setAttribute("aria-pressed", favOnly ? "true" : "false");
      applyFilters();
      syncUrlFromFilters();
    });
  }

  if (resetBtn) {
    resetBtn.addEventListener("click", () => {
      filtersRoot.querySelectorAll('[data-filter-group="status"]').forEach((btn) => {
        btn.classList.remove("active");
        btn.setAttribute("aria-pressed", "false");
      });
      applyDefaultTypeFilters();
      favOnly = false;
      if (favFilterBtn) {
        favFilterBtn.classList.remove("active");
        favFilterBtn.setAttribute("aria-pressed", "false");
      }
      applyFilters();
      syncUrlFromFilters();
    });
  }

  layoutButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      setLayout(btn.dataset.mapLayout);
    });
  });

  if (sortSelect) {
    sortSelect.addEventListener("change", () => {
      setSort(sortSelect.value);
    });
  }

  function setStatusClasses(el, status) {
    el.classList.remove("online", "offline", "unknown");
    if (status) {
      el.classList.add(status);
    }
  }

  function updateStatusDot(root, status) {
    const dot = root.querySelector(".status-dot");
    if (!dot) {
      return;
    }
    setStatusClasses(dot, status);
    const label = STATUS_LABELS[status] || STATUS_LABELS.unknown;
    dot.title = label;
    dot.setAttribute("aria-label", label);
  }

  function updateStatusChip(card, status) {
    const article = card.querySelector(".device-card");
    if (article) {
      setStatusClasses(article, status);
    }
    updateStatusDot(card, status);
    const short = STATUS_SHORT[status] || STATUS_SHORT.unknown;
    const chip = card.querySelector(".device-card-status");
    if (chip) {
      chip.title = short;
    }
    const labelEl = card.querySelector("[data-status-label]");
    if (labelEl) {
      labelEl.textContent = short;
    }
  }

  function syncKindUi(card, kind) {
    const resolved = resolveKind(kind);
    const kindWrap = card.querySelector(".device-card-kind");
    if (!kindWrap) {
      return;
    }
    kindWrap.innerHTML = `${TYPE_ICONS[resolved] || TYPE_ICONS.other}<span class="device-card-kind-label">${escapeHtml(KIND_LABELS[resolved] || KIND_LABELS.other)}</span>`;
  }

  function syncFavoriteUi(card) {
    const deviceId = Number(card.dataset.deviceId);
    const isFav = favorites.has(deviceId);
    card.classList.toggle("is-favorite", isFav);
    const btn = card.querySelector("[data-device-fav]");
    if (!btn) return;
    btn.classList.toggle("is-active", isFav);
    btn.setAttribute("aria-pressed", isFav ? "true" : "false");
    btn.setAttribute("aria-label", isFav ? "Убрать из избранного" : "В избранное");
    btn.title = isFav ? "Убрать из избранного" : "Избранное";
  }

  function syncSelectUi(card) {
    const id = String(card.dataset.deviceId || "");
    const selected = selectedIds.has(id);
    card.classList.toggle("is-selected", selected);
    const btn = card.querySelector("[data-device-select]");
    if (!btn) return;
    btn.classList.toggle("is-active", selected);
    btn.setAttribute("aria-pressed", selected ? "true" : "false");
  }

  function updateBulkBar() {
    if (!bulkBar || !bulkCount) return;
    const count = selectedIds.size;
    bulkCount.textContent = String(count);
    const show = count > 0;
    bulkBar.classList.toggle("d-none", !show);
    bulkBar.hidden = !show;
    if (bulkError && show === false) {
      bulkError.classList.add("d-none");
      bulkError.textContent = "";
    }
  }

  function showBulkError(message) {
    if (!bulkError) return;
    bulkError.textContent = message || "Не удалось выполнить действие.";
    bulkError.classList.remove("d-none");
  }

  function clearSelection() {
    selectedIds.clear();
    mapRoot.querySelectorAll("[data-device-card].is-selected").forEach((card) => {
      syncSelectUi(card);
    });
    updateBulkBar();
  }

  function toggleSelect(card) {
    const id = String(card.dataset.deviceId || "");
    if (!id) return;
    if (selectedIds.has(id)) {
      selectedIds.delete(id);
    } else {
      selectedIds.add(id);
    }
    syncSelectUi(card);
    updateBulkBar();
  }

  function toggleFavorite(card) {
    const deviceId = Number(card.dataset.deviceId);
    if (!Number.isFinite(deviceId) || deviceId <= 0) return;
    if (favorites.has(deviceId)) {
      favorites.delete(deviceId);
    } else {
      favorites.add(deviceId);
    }
    saveFavorites();
    syncFavoriteUi(card);
    if (favOnly) {
      applyFilters();
    }
  }

  function bindCardControls(card) {
    const selectBtn = card.querySelector("[data-device-select]");
    if (selectBtn && !selectBtn.dataset.bound) {
      selectBtn.dataset.bound = "1";
      selectBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleSelect(card);
      });
    }
    const favBtn = card.querySelector("[data-device-fav]");
    if (favBtn && !favBtn.dataset.bound) {
      favBtn.dataset.bound = "1";
      favBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        toggleFavorite(card);
      });
    }
    syncFavoriteUi(card);
    syncSelectUi(card);
  }

  function createDeviceCardElement(device) {
    const status = device.status || "unknown";
    const kind = resolveKind(device.kind);
    const href = device.url || `/devices/${device.id}`;
    const hostname = device.hostname || "без имени";
    const hasName = Boolean(device.hostname);
    const ip = device.ip || "";
    const mac = device.mac || "";
    const serial = device.serial || "";
    const statusShort = STATUS_SHORT[status] || STATUS_SHORT.unknown;
    const col = document.createElement("div");
    col.dataset.deviceCard = "";
    col.dataset.deviceId = String(device.id);
    col.dataset.status = status;
    col.dataset.type = kind;
    col.dataset.hostname = device.hostname || "";
    col.dataset.ip = ip;
    col.dataset.mac = mac;
    col.dataset.serial = serial;
    col.innerHTML = `
      <div class="device-card-shell">
        <article class="device-card ${escapeHtml(status)}">
          <div class="device-card-head">
            <button type="button" class="device-select-hit" data-device-select aria-pressed="false" aria-label="Выбрать устройство" title="Выбрать">
              <span class="device-select-box" aria-hidden="true"></span>
            </button>
            <span class="device-card-kind">
              ${TYPE_ICONS[kind] || TYPE_ICONS.other}
              <span class="device-card-kind-label">${escapeHtml(KIND_LABELS[kind] || KIND_LABELS.other)}</span>
            </span>
            <span class="device-card-head-actions">
              <button type="button" class="device-fav-btn" data-device-fav aria-pressed="false" aria-label="В избранное" title="Избранное">
                <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                  <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
                </svg>
              </button>
              <span class="device-card-status" title="${escapeHtml(statusShort)}">
                <span class="status-dot ${escapeHtml(status)}" title="${escapeHtml(STATUS_LABELS[status] || STATUS_LABELS.unknown)}" aria-label="${escapeHtml(STATUS_LABELS[status] || STATUS_LABELS.unknown)}"></span>
                <span class="device-card-status-label" data-status-label>${escapeHtml(statusShort)}</span>
              </span>
            </span>
          </div>
          <a class="device-card-body text-decoration-none" href="${escapeHtml(href)}" data-device-link>
            <div class="device-card-name${hasName ? "" : " is-empty"}" title="${escapeHtml(hostname)}">${escapeHtml(hostname)}</div>
            <div class="device-card-ip font-monospace">${escapeHtml(ip)}</div>
            ${
              serial || mac
                ? `<div class="device-card-meta font-monospace">${
                    serial ? `<span title="Серийный номер">${escapeHtml(serial)}</span>` : ""
                  }${serial && mac ? `<span class="device-card-meta-sep">·</span>` : ""}${
                    mac ? `<span title="MAC">${escapeHtml(mac)}</span>` : ""
                  }</div>`
                : ""
            }
          </a>
        </article>
      </div>
    `;
    bindCardControls(col);
    return col;
  }

  function ensureSectorDevicesRow(panel) {
    let row = panel.querySelector("[data-sector-devices]");
    if (row) {
      row.dataset.layout = currentLayout;
      return row;
    }
    const devicesWrap = panel.querySelector(".sector-devices");
    if (!devicesWrap) return null;
    row = document.createElement("div");
    row.className = "device-card-grid";
    row.dataset.sectorDevices = "";
    row.dataset.layout = currentLayout;
    devicesWrap.prepend(row);
    return row;
  }

  function applyStatusPayload(payload) {
    (payload.sectors || []).forEach((sector) => {
      const panel = mapRoot.querySelector(
        `.sector-panel[data-sector-id="${sector.id}"]`
      );
      if (!panel) {
        return;
      }

      const subnetsEl = panel.querySelector("[data-sector-subnets]");
      if (subnetsEl && Array.isArray(sector.subnets)) {
        const labels = sector.subnets.filter(Boolean);
        subnetsEl.textContent = labels.join(" · ");
        subnetsEl.hidden = labels.length === 0;
      }

      (sector.devices || []).forEach((device) => {
        let card = panel.querySelector(
          `[data-device-card][data-device-id="${device.id}"]`
        );
        if (!card) {
          const row = ensureSectorDevicesRow(panel);
          if (!row) return;
          card = createDeviceCardElement(device);
          row.appendChild(card);
        }
        const nextKind = resolveKind(device.kind);
        const kindChanged = card.dataset.type !== nextKind;
        card.dataset.status = device.status;
        card.dataset.type = nextKind;
        if (device.hostname != null) {
          card.dataset.hostname = device.hostname || "";
        }
        if (device.ip) {
          card.dataset.ip = device.ip;
        }
        if (device.mac != null) {
          card.dataset.mac = device.mac || "";
        }
        if (device.serial != null) {
          card.dataset.serial = device.serial || "";
        }
        updateStatusChip(card, device.status);
        if (kindChanged) {
          syncKindUi(card, nextKind);
        }
        const link = card.querySelector("[data-device-link]");
        if (link) {
          if (device.url && link.getAttribute("href") !== device.url) {
            link.setAttribute("href", device.url);
          }
          const nameEl = link.querySelector(".device-card-name");
          if (nameEl) {
            const hostname = device.hostname || "без имени";
            nameEl.textContent = hostname;
            nameEl.title = hostname;
            nameEl.classList.toggle("is-empty", !device.hostname);
          }
          const ipEl = link.querySelector(".device-card-ip");
          if (ipEl && device.ip) {
            ipEl.textContent = device.ip;
          }
        }
      });

      sortSectorCards(panel.querySelector("[data-sector-devices]"));
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
    if (!url || document.visibilityState === "hidden") {
      return;
    }
    try {
      const payload = await fetchJson(url);
      applyStatusPayload(payload);
    } catch (_err) {
      if (refreshStatus) {
        refreshStatus.textContent = "Не удалось обновить статусы";
      }
    }
  }

  async function postBulk(url, body) {
    const csrf = mapRoot.dataset.csrfToken || "";
    return postJson(url, body, csrf);
  }

  function selectedDeviceIds() {
    return Array.from(selectedIds)
      .map((id) => Number(id))
      .filter((id) => Number.isFinite(id) && id > 0);
  }

  async function runBulkPing() {
    const url = mapRoot.dataset.bulkPingUrl;
    if (!url) return;
    const deviceIds = selectedDeviceIds();
    if (!deviceIds.length) return;
    if (bulkPingBtn) bulkPingBtn.disabled = true;
    try {
      const payload = await postBulk(url, { device_ids: deviceIds });
      if (payload && payload.progress_url) {
        window.location.href = payload.progress_url;
        return;
      }
      showBulkError("Сервер не вернул ссылку на прогресс.");
    } catch (err) {
      showBulkError(err.message || "Не удалось запустить Ping.");
    } finally {
      if (bulkPingBtn) bulkPingBtn.disabled = false;
    }
  }

  async function runBulkScript() {
    const url = mapRoot.dataset.bulkScriptUrl;
    if (!url || mapRoot.dataset.canRunScripts !== "1") return;
    const deviceIds = selectedDeviceIds();
    if (!deviceIds.length) return;
    const scriptId = bulkScriptSelect
      ? Number(bulkScriptSelect.value)
      : NaN;
    if (!Number.isFinite(scriptId) || scriptId <= 0) {
      showBulkError("Выберите скрипт.");
      return;
    }
    if (bulkScriptConfirm) bulkScriptConfirm.disabled = true;
    try {
      const payload = await postBulk(url, {
        device_ids: deviceIds,
        script_id: scriptId,
      });
      if (payload && payload.progress_url) {
        window.location.href = payload.progress_url;
        return;
      }
      showBulkError("Сервер не вернул ссылку на прогресс.");
    } catch (err) {
      showBulkError(err.message || "Не удалось запустить скрипт.");
    } finally {
      if (bulkScriptConfirm) bulkScriptConfirm.disabled = false;
    }
  }

  if (bulkPingBtn) {
    bulkPingBtn.addEventListener("click", () => {
      runBulkPing();
    });
  }
  if (bulkClearBtn) {
    bulkClearBtn.addEventListener("click", () => clearSelection());
  }
  if (bulkScriptConfirm) {
    bulkScriptConfirm.addEventListener("click", () => runBulkScript());
  }

  mapRoot.querySelectorAll("[data-device-card]").forEach(bindCardControls);

  mapRoot.addEventListener("click", (event) => {
    const link = event.target.closest("[data-device-link]");
    if (!link || !mapRoot.contains(link)) {
      return;
    }
    const card = link.closest("[data-device-card]");
    saveMapView(card && card.dataset.deviceId);
  });

  window.MapFilters = {
    setSearch,
  };

  applyUrlToFilters();
  syncUrlFromFilters();
  syncSortSelect();
  applyLayoutToGrids();
  sortAllSectorCards();
  applyFilters();
  updateBulkBar();
  refreshSummary();
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) {
      sessionStorage.removeItem(RESTORE_KEY);
      return;
    }
    restoreMapView();
  });
  window.setInterval(refreshStatuses, REFRESH_MS);
  window.setInterval(refreshSummary, REFRESH_MS * 4);
})();
