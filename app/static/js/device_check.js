/**
 * «Проверить сейчас»: короткие шаги поиска, пока сервер ищет машину.
 * Без fetch форма уходит обычным POST и возвращает редирект.
 */
(() => {
  const forms = document.querySelectorAll("form[data-device-check]");
  if (!forms.length || !window.fetch || !window.ReadableStream) return;

  forms.forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void runCheck(form);
    });
  });

  async function runCheck(form) {
    const button = form.querySelector("[data-check-button]");
    const log = form.parentElement
      ? form.parentElement.querySelector("[data-check-log]")
      : null;
    const redirect = form.getAttribute("data-redirect") || window.location.href;
    const label = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Ищу…";
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
        appendStep(log, "Не удалось выполнить проверку.");
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
        buffer += decoder.decode(chunk.value || new Uint8Array(), { stream: !chunk.done });
        let breakAt = buffer.indexOf("\n");
        while (breakAt >= 0) {
          const line = buffer.slice(0, breakAt).trim();
          buffer = buffer.slice(breakAt + 1);
          if (line) {
            const event = JSON.parse(line);
            if (event.type === "step" && event.text) appendStep(log, event.text);
            if (event.type === "done") {
              notice = event.notice || "";
              target = event.redirect || redirect;
            }
          }
          breakAt = buffer.indexOf("\n");
        }
        if (chunk.done) {
          const tail = buffer.trim();
          if (tail) {
            const event = JSON.parse(tail);
            if (event.type === "step" && event.text) appendStep(log, event.text);
            if (event.type === "done") {
              notice = event.notice || "";
              target = event.redirect || redirect;
            }
          }
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
