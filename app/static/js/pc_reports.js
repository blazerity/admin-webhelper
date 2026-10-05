/* Отчёты о ПК: диаграммы Chart.js на дашборде. */
(function () {
  "use strict";

  function readCharts() {
    var node = document.getElementById("pc-report-charts");
    if (!node) return null;
    try {
      return JSON.parse(node.textContent || "{}");
    } catch (err) {
      console.warn("pc-reports: bad chart JSON", err);
      return null;
    }
  }

  function empty(series) {
    return !series || !series.labels || !series.labels.length;
  }

  function palette(n) {
    var base = [
      "#0f766e",
      "#1d4ed8",
      "#b45309",
      "#15803d",
      "#7c3aed",
      "#be123c",
      "#0e7490",
      "#a16207",
      "#334155",
      "#047857",
    ];
    var out = [];
    for (var i = 0; i < n; i += 1) out.push(base[i % base.length]);
    return out;
  }

  function doughnut(canvas, series, opts) {
    if (!canvas || empty(series) || typeof Chart === "undefined") return;
    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: series.labels,
        datasets: [
          {
            data: series.values,
            backgroundColor: palette(series.labels.length),
            borderWidth: 0,
          },
        ],
      },
      options: Object.assign(
        {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
          },
        },
        opts || {}
      ),
    });
  }

  function bar(canvas, series, opts) {
    if (!canvas || empty(series) || typeof Chart === "undefined") return;
    var horizontal = opts && opts.horizontal;
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: series.labels,
        datasets: [
          {
            label: (opts && opts.label) || "",
            data: series.values,
            backgroundColor: (opts && opts.color) || "#0f766e",
            borderRadius: 4,
            maxBarThickness: horizontal ? 18 : 36,
          },
        ],
      },
      options: {
        indexAxis: horizontal ? "y" : "x",
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: Boolean(opts && opts.label) },
        },
        scales: {
          x: {
            beginAtZero: true,
            ticks: { precision: 0, font: { size: 11 } },
            grid: { color: "rgba(15, 118, 110, 0.08)" },
          },
          y: {
            ticks: { font: { size: 11 } },
            grid: horizontal ? { color: "rgba(15, 118, 110, 0.08)" } : { display: false },
          },
        },
      },
    });
  }

  function stackedSectors(canvas, data) {
    if (!canvas || typeof Chart === "undefined") return;
    if (empty(data.notebooks) && empty(data.desktops)) return;
    var labels = (data.notebooks && data.notebooks.labels) || (data.desktops && data.desktops.labels) || [];
    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Ноутбуки",
            data: (data.notebooks && data.notebooks.values) || [],
            backgroundColor: "#0f766e",
            borderRadius: 3,
            maxBarThickness: 40,
          },
          {
            label: "СБ",
            data: (data.desktops && data.desktops.values) || [],
            backgroundColor: "#1d4ed8",
            borderRadius: 3,
            maxBarThickness: 40,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 11 } } },
        },
        scales: {
          x: {
            stacked: true,
            ticks: { font: { size: 11 }, maxRotation: 45, minRotation: 0 },
            grid: { display: false },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            ticks: { precision: 0, font: { size: 11 } },
            grid: { color: "rgba(15, 118, 110, 0.08)" },
          },
        },
      },
    });
  }

  function boot() {
    var data = readCharts();
    if (!data) return;
    doughnut(document.getElementById("pc-chart-kind"), data.kind);
    stackedSectors(document.getElementById("pc-chart-sectors"), data);
    doughnut(document.getElementById("pc-chart-ram"), data.ram);
    doughnut(document.getElementById("pc-chart-disk"), data.disk);
    doughnut(document.getElementById("pc-chart-os"), data.os);
    bar(document.getElementById("pc-chart-cpu"), data.cpu, {
      horizontal: true,
      color: "#0e7490",
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
