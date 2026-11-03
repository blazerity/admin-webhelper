/**
 * Короткие шаги долгой кнопки на карточке.
 * Форма с data-live-progress. Без fetch уходит обычным POST.
 */
(() => {
  const forms = document.querySelectorAll("form[data-live-progress]");
  if (!forms.length || !window.fetch || !window.ReadableStream) return;

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void runProgress(form);
    });
  });

  async function runProgress(form) {
    const button = form.querySelector("[data-progress-button]");
    const log = form.parentElement
      ? form.parentElement.querySelector("[data-progress-log]")
      : null;
    const redirect = form.getAttribute("data-redirect") || window.location.href;
    const busyLabel = form.getAttribute("data-progress-label") || "Идёт…";
    const label = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = busyLabel;
    }
    clearLog(log);

    let notice = "";
    let target = redirect;
    try {
      const response = await fetch(form.action, {
        method: "POST",
        credentials: "same-origin",
        headers: { Accept: "application/x-ndjson" },
        body: new FormData(form),
      });
      if (!response.ok || !response.body) {
        appendStep(log, "Не удалось выполнить операцию.");
        if (button) {
          button.disabled = false;
          button.textContent = label;
        }
        return;
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const chunk = await reader.read();
        buffer += decoder.decode(chunk.value || new Uint8Array(), {
          stream: !chunk.done,
        });
        let breakAt = buffer.indexOf("\n");
        while (breakAt >= 0) {
          const event = readEvent(buffer.slice(0, breakAt));
          buffer = buffer.slice(breakAt + 1);
          if (event) ({ notice, target } = applyEvent(log, event, notice, target, redirect));
          breakAt = buffer.indexOf("\n");
        }
        if (chunk.done) {
          const event = readEvent(buffer);
          if (event) ({ notice, target } = applyEvent(log, event, notice, target, redirect));
          break;
        }
      }
    } catch (_err) {
      appendStep(log, "Связь прервалась, обновляю страницу…");
    }

    if (notice) {
      let cookie =
        "bawh_check_notice=" +
        encodeURIComponent(notice) +
        "; Path=/; Max-Age=120; SameSite=Lax";
      if (window.location.protocol === "https:") cookie += "; Secure";
      document.cookie = cookie;
    }
    window.setTimeout(() => {
      window.location.assign(target);
    }, 700);
  }

  function readEvent(raw) {
    const line = String(raw || "").trim();
    if (!line) return null;
    return JSON.parse(line);
  }

  function applyEvent(log, event, notice, target, redirect) {
    if (event.type === "step" && event.text) appendStep(log, event.text);
    if (event.type === "done") {
      notice = event.notice || "";
      target = event.redirect || redirect;
    }
    return { notice, target };
  }

  function clearLog(log) {
    if (!log) return;
    log.replaceChildren();
    log.hidden = false;
  }

  function appendStep(log, text) {
    if (!log) return;
    log.hidden = false;
    const previous = log.querySelector(".is-current");
    if (previous) previous.classList.remove("is-current");
    const item = document.createElement("li");
    item.className = "is-current";
    item.textContent = text;
    log.appendChild(item);
  }
})();
