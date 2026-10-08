// Живой поиск списков Действия / Учётные записи: как на карте, debounce 250 мс.
(function () {
  const form = document.querySelector("[data-live-list]");
  if (!form || !window.BawhHttp) return;

  const kind = form.getAttribute("data-live-kind") || "";
  const input = form.querySelector("[data-live-q]");
  const kindFilter = form.querySelector("[data-live-kind-filter]");
  const perPageInput = form.querySelector("[data-live-per-page]");
  const resetBtn = form.querySelector("[data-live-reset]");
  const hint = form.querySelector("[data-live-hint]");
  const rows = document.querySelector("[data-live-rows]");
  const meta = document.querySelector("[data-live-meta]");
  const pager = document.querySelector("[data-live-pager]");
  const csvLink = document.querySelector("[data-live-csv]");
  if (!input || !rows) return;

  const emptyText =
    kind === "accounts"
      ? "Пока никто не замечен на доступных устройствах."
      : "Событий пока нет.";
  const showDisplayName =
    kind !== "accounts" ||
    Boolean(
      rows.closest("table") &&
        rows.closest("table").querySelector("thead th[data-col='display-name']")
    );
  const emptyCols = kind === "accounts" ? (showDisplayName ? 4 : 3) : 6;
  const { escapeHtml, fetchJson } = window.BawhHttp;

  let timer = null;
  let seq = 0;
  let page = Number(new URL(window.location.href).searchParams.get("page") || "1") || 1;

  function currentQuery() {
    return (input.value || "").trim();
  }

  function currentKind() {
    return kindFilter && kindFilter.value ? kindFilter.value : "";
  }

  function currentPerPage() {
    const raw = perPageInput && perPageInput.value ? Number(perPageInput.value) : 50;
    if (!Number.isFinite(raw) || raw < 1) return 50;
    return Math.min(200, Math.max(1, Math.floor(raw)));
  }

  function listUrl(nextPage) {
    const url = new URL(form.getAttribute("action") || window.location.pathname, window.location.origin);
    const query = currentQuery();
    const type = currentKind();
    const perPage = currentPerPage();
    if (query) url.searchParams.set("q", query);
    else url.searchParams.delete("q");
    if (type) url.searchParams.set("kind", type);
    else url.searchParams.delete("kind");
    url.searchParams.set("per_page", String(perPage));
    if (nextPage > 1) url.searchParams.set("page", String(nextPage));
    else url.searchParams.delete("page");
    // Лента действий: режим «Показать все» (превью 5 без all).
    if (kind === "actions") {
      url.searchParams.set("all", "1");
    }
    return url;
  }

  function updateHint(query) {
    if (!hint) return;
    if (query.length >= 1) {
      hint.textContent = "На списке показаны строки, подходящие под запрос.";
    } else {
      hint.textContent = "Начните вводить запрос — список обновится сразу.";
    }
  }

  function updateCsv(url) {
    if (!csvLink) return;
    const href = new URL(csvLink.href, window.location.origin);
    const query = url.searchParams.get("q") || "";
    const type = url.searchParams.get("kind") || "";
    if (kind === "accounts") {
      if (query) href.searchParams.set("q", query);
      else href.searchParams.delete("q");
    } else {
      if (type) href.searchParams.set("kind", type);
      else href.searchParams.delete("kind");
    }
    csvLink.href = href.pathname + href.search;
  }

  function cell(html) {
    return `<td>${html}</td>`;
  }

  function linkOrText(url, text, extraClass) {
    const label = escapeHtml(text || "—");
    if (!url) return label;
    const cls = extraClass ? ` class="${extraClass}"` : "";
    return `<a${cls} href="${escapeHtml(url)}">${label}</a>`;
  }

  function entityLink(url, text, kind) {
    const label = escapeHtml(text || "—");
    if (!url) return label;
    const classes =
      kind === "account"
        ? "entity-link entity-account font-monospace"
        : "entity-link entity-device";
    return `<a class="${classes}" href="${escapeHtml(url)}">${label}</a>`;
  }

  function renderActions(items) {
    if (!items.length) {
      return `<tr><td colspan="${emptyCols}" class="text-muted">${escapeHtml(emptyText)}</td></tr>`;
    }
    return items
      .map((item) => {
        const title = linkOrText(item.url, item.title);
        const device =
          item.device_id && item.device_url
            ? entityLink(
                item.device_url,
                item.device_label || "#" + item.device_id,
                "device"
              )
            : "—";
        return `<tr>${cell(escapeHtml(item.when || "—"))}${cell(
          escapeHtml(item.kind_title || "")
        )}${cell(title)}${cell(device)}${cell(escapeHtml(item.actor || "—"))}${cell(
          escapeHtml(item.status || "")
        )}</tr>`;
      })
      .join("");
  }

  function renderAccounts(items) {
    if (!items.length) {
      return `<tr><td colspan="${emptyCols}" class="text-muted">${escapeHtml(emptyText)}</td></tr>`;
    }
    return items
      .map((item) => {
        let html = `<tr>${cell(entityLink(item.url, item.account_key, "account"))}`;
        if (showDisplayName) {
          html += cell(escapeHtml(item.display_name || "—"));
        }
        html += `${cell(escapeHtml(item.first_seen_at || "—"))}${cell(
          escapeHtml(item.last_seen_at || "—")
        )}</tr>`;
        return html;
      })
      .join("");
  }

  function pageHref(pageNum) {
    const url = listUrl(pageNum);
    return url.pathname + url.search;
  }

  function renderPager(total, currentPage, perPage) {
    if (!pager) return;
    const totalPages = perPage > 0 ? Math.ceil(total / perPage) : 1;
    if (totalPages <= 1) {
      pager.innerHTML = "";
      return;
    }
    const start = currentPage > 2 ? currentPage - 2 : 1;
    const end = currentPage + 2 < totalPages ? currentPage + 2 : totalPages;
    const items = [];

    function pageItem(label, pageNum, opts) {
      const disabled = opts && opts.disabled;
      const active = opts && opts.active;
      const ellipsis = opts && opts.ellipsis;
      let inner;
      if (ellipsis || disabled || active) {
        inner = `<span class="page-link">${escapeHtml(label)}</span>`;
      } else {
        inner = `<a class="page-link" href="${escapeHtml(pageHref(pageNum))}">${escapeHtml(
          label
        )}</a>`;
      }
      const cls = ["page-item"];
      if (disabled) cls.push("disabled");
      if (active) cls.push("active");
      const aria = active ? ' aria-current="page"' : "";
      return `<li class="${cls.join(" ")}"${aria}>${inner}</li>`;
    }

    items.push(pageItem("Назад", currentPage - 1, { disabled: currentPage <= 1 }));
    if (start > 1) {
      items.push(pageItem("1", 1));
      if (start > 2) items.push(pageItem("…", 0, { ellipsis: true }));
    }
    for (let p = start; p <= end; p += 1) {
      items.push(pageItem(String(p), p, { active: p === currentPage }));
    }
    if (end < totalPages) {
      if (end < totalPages - 1) items.push(pageItem("…", 0, { ellipsis: true }));
      items.push(pageItem(String(totalPages), totalPages));
    }
    items.push(pageItem("Вперёд", currentPage + 1, { disabled: currentPage >= totalPages }));
    pager.innerHTML = `<nav class="app-pagination px-3 py-2" aria-label="Страницы"><ul class="pagination pagination-sm mb-0">${items.join(
      ""
    )}</ul></nav>`;
  }

  function applyPayload(payload, url) {
    const items = payload.items || [];
    rows.innerHTML = kind === "accounts" ? renderAccounts(items) : renderActions(items);
    const total = Number(payload.total || 0);
    const currentPage = Number(payload.page || 1);
    const perPage = Number(payload.per_page || currentPerPage());
    page = currentPage;
    if (meta) {
      meta.textContent = `Всего: ${total} · страница ${currentPage}`;
    }
    renderPager(total, currentPage, perPage);
    updateCsv(url);
    const next = `${url.pathname}${url.search}${window.location.hash}`;
    window.history.replaceState(null, "", next);
  }

  function load(nextPage, immediate) {
    const query = currentQuery();
    updateHint(query);
    const url = listUrl(nextPage);
    const ticket = ++seq;
    const run = function () {
      fetchJson(url.pathname + url.search)
        .then(function (payload) {
          if (ticket !== seq) return;
          applyPayload(payload, url);
        })
        .catch(function () {
          if (ticket !== seq) return;
          if (hint) hint.textContent = "Не удалось обновить список. Повторите запрос.";
        });
    };
    if (immediate) {
      clearTimeout(timer);
      run();
      return;
    }
    clearTimeout(timer);
    timer = setTimeout(run, 250);
  }

  input.addEventListener("input", function () {
    page = 1;
    load(1, false);
  });
  if (kindFilter) {
    kindFilter.addEventListener("change", function () {
      page = 1;
      load(1, true);
    });
  }
  if (perPageInput) {
    perPageInput.addEventListener("change", function () {
      page = 1;
      load(1, true);
    });
  }
  if (resetBtn) {
    resetBtn.addEventListener("click", function () {
      input.value = "";
      if (kindFilter) kindFilter.value = "";
      page = 1;
      load(1, true);
      input.focus();
    });
  }
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    page = 1;
    load(1, true);
  });
  if (pager) {
    pager.addEventListener("click", function (event) {
      const link = event.target.closest("a.page-link");
      if (!link) return;
      event.preventDefault();
      const target = new URL(link.href, window.location.origin);
      const nextPage = Number(target.searchParams.get("page") || "1") || 1;
      load(nextPage, true);
    });
  }
})();
