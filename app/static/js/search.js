// Поиск на карте сети → /search/api; фильтрует секторы и карточки устройств.
(function () {
  const form = document.getElementById("map-search-form");
  const input = document.getElementById("map-search-q");
  const sector = document.getElementById("map-search-sector");
  const results = document.getElementById("map-search-results");
  if (!form || !input || !results) return;

  const apiUrl = form.dataset.apiUrl || "/search/api";
  let timer = null;

  const STATUS_CLASS = {
    online: "text-bg-success",
    offline: "text-bg-danger",
    unknown: "text-bg-secondary",
  };
  const STATUS_LABEL = {
    online: "доступен",
    offline: "недоступен",
    unknown: "неизвестно",
  };

  function syncMapFilter(matchIds) {
    if (window.MapFilters && typeof window.MapFilters.setSearchMatchIds === "function") {
      window.MapFilters.setSearchMatchIds(matchIds);
    }
  }

  function clearMapFilter() {
    syncMapFilter(null);
  }

  function renderEmpty(message) {
    results.innerHTML = '<p class="text-muted small mb-0">' + message + "</p>";
  }

  function renderItems(items) {
    if (!items.length) {
      renderEmpty("Ничего не найдено.");
      return;
    }
    results.innerHTML = "";
    items.forEach(function (item) {
      const link = document.createElement("a");
      link.className = "search-result";
      link.href = item.url;

      const top = document.createElement("span");
      top.className = "search-result-top";

      const name = document.createElement("span");
      name.className = "search-result-name";
      name.textContent = item.hostname || "без имени";

      const badge = document.createElement("span");
      badge.className = "badge " + (STATUS_CLASS[item.status] || STATUS_CLASS.unknown);
      badge.textContent = STATUS_LABEL[item.status] || STATUS_LABEL.unknown;

      top.appendChild(name);
      top.appendChild(badge);

      const meta = document.createElement("span");
      meta.className = "search-result-meta font-monospace";
      meta.textContent = item.ip + (item.mac ? " · " + item.mac : "");

      const sectorName = document.createElement("span");
      sectorName.className = "search-result-sector";
      sectorName.textContent = item.sector || "";

      link.appendChild(top);
      link.appendChild(meta);
      link.appendChild(sectorName);
      results.appendChild(link);
    });
  }

  function runSearch() {
    const value = input.value.trim();
    const params = new URLSearchParams();
    if (value) params.set("q", value);
    if (sector && sector.value) params.set("sector_id", sector.value);

    const nextUrl = window.location.pathname + (params.toString() ? "?" + params.toString() : "");
    window.history.replaceState({}, "", nextUrl);

    if (value.length < 2) {
      clearMapFilter();
      renderEmpty(
        value
          ? "Введите ещё символ для поиска."
          : "Начните вводить запрос — на карте останутся подходящие машины."
      );
      return;
    }

    fetch(apiUrl + "?" + params.toString(), {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    })
      .then(function (response) {
        return response.json();
      })
      .then(function (items) {
        renderItems(items);
        syncMapFilter(items.map(function (item) {
          return item.id;
        }));
      })
      .catch(function () {
        clearMapFilter();
        renderEmpty("Не удалось выполнить поиск.");
      });
  }

  function scheduleSearch() {
    clearTimeout(timer);
    timer = setTimeout(runSearch, 250);
  }

  input.addEventListener("input", scheduleSearch);
  if (sector) {
    sector.addEventListener("change", runSearch);
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    runSearch();
  });

  // Стартовый фильтр: при ?q=… или SSR-результатах сразу сужаем карту.
  const initialValue = input.value.trim();
  if (initialValue.length >= 2) {
    const initialIds = (results.dataset.initialIds || "")
      .split(",")
      .map(function (part) {
        return part.trim();
      })
      .filter(Boolean);
    if (initialIds.length) {
      syncMapFilter(initialIds);
    }
    runSearch();
  } else {
    clearMapFilter();
  }
})();
