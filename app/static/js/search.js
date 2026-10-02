// Подсказки поиска. Запрос уходит на /search/suggest, без отдельного фреймворка.
(function () {
  const input = document.getElementById("q");
  const box = document.getElementById("suggest");
  if (!input || !box) return;

  let timer = null;
  input.addEventListener("input", function () {
    const value = input.value.trim();
    clearTimeout(timer);
    if (value.length < 2) {
      box.classList.add("d-none");
      box.innerHTML = "";
      return;
    }
    timer = setTimeout(function () {
      fetch("/search/suggest?q=" + encodeURIComponent(value), { headers: { "Accept": "application/json" } })
        .then(function (response) { return response.json(); })
        .then(function (items) {
          box.innerHTML = "";
          items.forEach(function (item) {
            const link = document.createElement("a");
            link.className = "list-group-item list-group-item-action";
            link.href = item.url;
            link.textContent = item.label;
            box.appendChild(link);
          });
          box.classList.toggle("d-none", items.length === 0);
        })
        .catch(function () { box.classList.add("d-none"); });
    }, 250);
  });
})();
