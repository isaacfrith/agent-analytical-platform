// Answer charts (Vega-Lite). The agent's spec names columns; rows come from the captured step.

function renderChart(ev) {
  const { spec, rows, columns } = ev;
  const card = el("div", { class: "chart-card" }, el("div", { class: "chart-title", text: spec.title }));
  const plot = el("div");
  card.append(plot);
  const table = dataTable(columns, rows);
  if (spec.kind !== "table") card.append(el("details", {}, el("summary", { text: "Show data table" }), table));
  else card.append(table);
  $("charts").append(card);
  if (spec.kind === "table") return;

  const data = rows.map((r) => {
    const o = {};
    for (const c of columns) o[c] = dayOnly(r[c]);
    return o;
  });
  const xTemporal = data.length && isDayString(data[0][spec.x]);
  const multiY = spec.y.length > 1;
  const colorField = spec.series || (multiY ? "measure" : null);
  const fuelish = colorField && /fuel/.test(colorField);
  const xFuelish = /fuel/.test(spec.x);
  const valueField = multiY ? "value" : spec.y[0];

  const colorScale = (field) => {
    if (/fuel/.test(field)) {
      const domain = [...new Set(data.map((d) => d[field]))];
      return { domain, range: domain.map((v) => FUEL[fuelGroup(v)].color) };
    }
    return { range: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"] };
  };

  const tooltip = [
    { field: spec.x, type: xTemporal ? "temporal" : "nominal", title: humanize(spec.x) },
    ...(colorField ? [{ field: colorField, type: "nominal" }] : []),
    { field: valueField, type: "quantitative", format: ",.2~f", title: multiY ? "value" : humanize(spec.y[0]) },
  ];

  let encoding;
  let mark;
  if (spec.kind === "line") {
    mark = { type: "line", strokeWidth: 2, strokeCap: "round", strokeJoin: "round", point: { filled: true, size: 36 } };
    encoding = {
      x: { field: spec.x, type: xTemporal ? "temporal" : "ordinal", title: null, axis: { format: xTemporal ? "%d %b" : undefined } },
      y: { field: valueField, type: "quantitative", title: multiY ? null : humanize(spec.y[0]), axis: { format: ",.2~s" } },
    };
  } else {
    // Horizontal bars: category labels (regions, facility names) stay readable.
    mark = { type: "bar", cornerRadiusEnd: 4, height: { band: 0.7 } };
    const sortField = spec.kind === "stacked_bar" ? "x" : "-x";
    encoding = {
      y: { field: spec.x, type: xTemporal ? "temporal" : "nominal", title: null, sort: xTemporal ? undefined : sortField },
      x: { field: valueField, type: "quantitative", title: multiY ? null : humanize(spec.y[0]), axis: { format: ",.2~s" }, stack: spec.kind === "stacked_bar" ? "zero" : null },
    };
  }
  if (colorField) {
    encoding.color = { field: colorField, type: "nominal", scale: colorScale(colorField), legend: { orient: "bottom", title: null } };
  } else if (xFuelish && mark.type === "bar") {
    encoding.color = { field: spec.x, type: "nominal", scale: colorScale(spec.x), legend: null };
  } else {
    encoding.color = { value: "#2a78d6" };
  }
  encoding.tooltip = tooltip;

  const categories = new Set(data.map((d) => d[spec.x])).size;
  const vl = {
    $schema: "https://vega.github.io/schema/vega-lite/v5.json",
    data: { values: data },
    transform: multiY ? [{ fold: spec.y, as: ["measure", "value"] }] : [],
    mark,
    encoding,
    width: "container",
    height: mark.type === "bar" && !xTemporal ? Math.max(120, Math.min(categories, 24) * 22) : 220,
    config: {
      background: null,
      font: "system-ui, -apple-system, Segoe UI, sans-serif",
      view: { stroke: null },
      axis: { labelColor: "#52514e", titleColor: "#898781", gridColor: "#e1e0d9", domainColor: "#c3c2b7", tickColor: "#c3c2b7", labelFontSize: 11, titleFontSize: 11, titleFontWeight: 500, labelLimit: 180 },
      legend: { labelColor: "#52514e", labelFontSize: 11, symbolType: "circle" },
      bar: { binSpacing: 2 },
    },
  };
  if (typeof vegaEmbed === "undefined") {
    plot.replaceChildren(el("p", { class: "muted", text: "The chart library didn't load. The data table below has the same rows." }));
    return;
  }
  vegaEmbed(plot, vl, { actions: false, renderer: "svg" }).catch((e) => plot.replaceChildren(el("p", { class: "step-error", text: `Chart failed: ${e.message}` })));
}

function dataTable(columns, rows) {
  const t = el("table", { class: "data-table" });
  t.append(el("thead", {}, el("tr", {}, ...columns.map((c) => el("th", { text: c })))));
  const body = el("tbody");
  for (const r of rows.slice(0, 200)) {
    body.append(el("tr", {}, ...columns.map((c) => {
      const v = r[c];
      return typeof v === "number" ? el("td", { class: "num", text: fmt(v) }) : el("td", { text: dayOnly(v ?? "") });
    })));
  }
  t.append(body);
  return t;
}
