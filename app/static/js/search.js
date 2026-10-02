// Поиск на карте: /search/suggest с fallback на /search/api; фильтр секторов + autocomplete.
(function () {
  const form = document.getElementById("map-search-form");
  const input = document.getElementById("map-search-q");
  const sector = document.getElementById("map-search-sector");
  const results = document.getElementById("map-search-results");
  const suggestBox = document.getElementById("map-search-suggest");
  if (!form || !input || !results) return;

  const suggestUrl = form.dataset.suggestUrl || "/search/suggest";
  const fallbackUrl = form.dataset.apiUrl || "/search/api";
  let timer = null;
  let activeIndex = -1;
  let currentItems = [];
  let preferSuggest = true;

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

  function setExpanded(open) {
    input.setAttribute("aria-expanded", open ? "true" : "false");
    if (!suggestBox) return;
    suggestBox.classList.toggle("d-none", !open);
  }

  function hideSuggest() {
    activeIndex = -1;
    setExpanded(false);
    if (suggestBox) {
      suggestBox.innerHTML = "";
    }
  }

  function renderEmpty(message) {
    results.innerHTML = '<p class="text-muted small mb-0">' + message + "</p>";
  }

  function renderResultLink(item, className) {
    const link = document.createElement("a");
    link.className = className;
    link.href = item.url || ("/devices/" + item.id);

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
    meta.textContent = (item.ip || "") + (item.mac ? " · " + item.mac : "");

    link.appendChild(top);
    link.appendChild(meta);

    if (item.sector) {
      const sectorName = document.createElement("span");
      sectorName.className = "search-result-sector";
      sectorName.textContent = item.sector;
      link.appendChild(sectorName);
    }

    return link;
  }

  function renderItems(items) {
    currentItems = items.slice();
    if (!items.length) {
      renderEmpty("Ничего не найдено.");
      hideSuggest();
      return;
    }

    results.innerHTML = "";
    items.forEach(function (item) {
      results.appendChild(renderResultLink(item, "search-result"));
    });

    if (!suggestBox) return;
    suggestBox.innerHTML = "";
    items.slice(0, 8).forEach(function (item, index) {
      const option = renderResultLink(item, "map-search-suggest-item");
      option.setAttribute("role", "option");
      option.id = "map-suggest-" + index;
      option.dataset.index = String(index);
      option.addEventListener("mousedown", function (event) {
        event.preventDefault();
        window.location.href = option.href;
      });
      suggestBox.appendChild(option);
    });
    activeIndex = -1;
    setExpanded(true);
  }

  function highlightActive() {
    if (!suggestBox) return;
    const options = suggestBox.querySelectorAll(".map-search-suggest-item");
    options.forEach(function (el, index) {
      el.classList.toggle("is-active", index === activeIndex);
      if (index === activeIndex) {
        input.setAttribute("aria-activedescendant", el.id);
      }
    });
    if (activeIndex < 0) {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function normalizeItems(payload) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload.items)) return payload.items;
    if (payload && Array.isArray(payload.results)) return payload.results;
    return [];
  }

  function fetchJson(url) {
    return fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then(function (response) {
      if (!response.ok) {
        const error = new Error("HTTP " + response.status);
        error.status = response.status;
        throw error;
      }
      return response.json();
    });
  }

  function fetchSuggestions(params) {
    const query = params.toString();
    const primary = (preferSuggest ? suggestUrl : fallbackUrl) + "?" + query;
    const secondary = fallbackUrl + "?" + query;

    return fetchJson(primary).catch(function (err) {
      if (preferSuggest && primary !== secondary) {
        preferSuggest = false;
        return fetchJson(secondary);
      }
      throw err;
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
      currentItems = [];
      hideSuggest();
      renderEmpty(
        value
          ? "Введите ещё символ для поиска."
          : "Начните вводить запрос — на карте останутся подходящие машины."
      );
      return;
    }

    fetchSuggestions(params)
      .then(function (payload) {
        const items = normalizeItems(payload);
        renderItems(items);
        syncMapFilter(
          items.map(function (item) {
            return item.id;
          })
        );
      })
      .catch(function () {
        clearMapFilter();
        currentItems = [];
        hideSuggest();
        renderEmpty("Не удалось выполнить поиск.");
      });
  }

  function scheduleSearch() {
    clearTimeout(timer);
    timer = setTimeout(runSearch, 250);
  }

  input.addEventListener("input", scheduleSearch);
  input.addEventListener("keydown", function (event) {
    if (!suggestBox || suggestBox.classList.contains("d-none")) {
      return;
    }
    const options = suggestBox.querySelectorAll(".map-search-suggest-item");
    if (!options.length) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % options.length;
      highlightActive();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = activeIndex <= 0 ? options.length - 1 : activeIndex - 1;
      highlightActive();
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      const target = options[activeIndex];
      if (target) window.location.href = target.href;
    } else if (event.key === "Escape") {
      hideSuggest();
    }
  });
  input.addEventListener("blur", function () {
    window.setTimeout(hideSuggest, 120);
  });
  input.addEventListener("focus", function () {
    if (currentItems.length) {
      setExpanded(true);
    }
  });

  if (sector) {
    sector.addEventListener("change", runSearch);
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    if (activeIndex >= 0 && currentItems[activeIndex] && currentItems[activeIndex].url) {
      window.location.href = currentItems[activeIndex].url;
      return;
    }
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
