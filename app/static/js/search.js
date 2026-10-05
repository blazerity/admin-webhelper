// Поиск на карте: фильтр секторов внизу страницы, без выпадающих списков.
(function () {
  const form = document.getElementById("map-search-form");
  const input = document.getElementById("map-search-q");
  const sector = document.getElementById("map-search-sector");
  const resetBtn = document.getElementById("map-search-reset");
  const hint = document.getElementById("map-search-hint");
  if (!form || !input) return;

  let timer = null;

  function applyToMap(query, sectorId) {
    if (window.MapFilters && typeof window.MapFilters.setSearch === "function") {
      window.MapFilters.setSearch(query, sectorId);
    }
  }

  function updateHint(query, sectorId) {
    if (!hint) return;
    const value = (query || "").trim();
    if (value.length >= 2) {
      hint.textContent = "На карте показаны машины, подходящие под запрос.";
    } else if (value.length === 1) {
      hint.textContent = "Введите ещё символ для поиска.";
    } else if (sectorId) {
      hint.textContent = "Показан выбранный сектор.";
    } else {
      hint.textContent = "Начните вводить запрос — на карте останутся подходящие машины.";
    }
  }

  function syncSearchUrl(query, sectorId) {
    const url = new URL(window.location.href);
    const params = url.searchParams;
    if (query) {
      params.set("q", query);
    } else {
      params.delete("q");
    }
    if (sectorId) {
      params.set("sector_id", sectorId);
    } else {
      params.delete("sector_id");
    }
    const next = `${url.pathname}${params.toString() ? `?${params}` : ""}${url.hash}`;
    window.history.replaceState(null, "", next);
  }

  function runSearch() {
    const query = input.value.trim();
    const sectorId = sector && sector.value ? sector.value : "";
    syncSearchUrl(query, sectorId);
    updateHint(query, sectorId);
    applyToMap(query, sectorId);
  }

  function scheduleSearch() {
    clearTimeout(timer);
    timer = setTimeout(runSearch, 250);
  }

  function resetSearch() {
    input.value = "";
    if (sector) sector.value = "";
    runSearch();
    input.focus();
  }

  input.addEventListener("input", scheduleSearch);
  if (sector) {
    sector.addEventListener("change", runSearch);
  }
  if (resetBtn) {
    resetBtn.addEventListener("click", resetSearch);
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    runSearch();
  });

  runSearch();
})();
