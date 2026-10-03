/**
 * Shared fetch / HTML helpers for map, batch, notifications, search.
 * Exposes window.BawhHttp.
 */
(() => {
  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fetchJson(url) {
    return fetch(url, {
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    }).then((response) => {
      if (!response.ok) {
        const error = new Error("HTTP " + response.status);
        error.status = response.status;
        throw error;
      }
      return response.json();
    });
  }

  async function postJson(url, body, csrf) {
    const token = csrf || "";
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
        "X-CSRFToken": token,
      },
      body: JSON.stringify({ ...(body || {}), csrf_token: token }),
    });
    let payload = null;
    try {
      payload = await response.json();
    } catch (_err) {
      payload = null;
    }
    if (!response.ok) {
      const message =
        (payload && payload.error) || `HTTP ${response.status}`;
      const error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }
    return payload;
  }

  window.BawhHttp = { escapeHtml, fetchJson, postJson };
})();
