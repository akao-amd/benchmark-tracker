const LINES = ["#4F81BD", "#C0504D", "#9BBB59", "#8064A2", "#4BACC6", "#F79646"];
const MARKS = ["#95B3D7", "#D99694", "#C3D69B", "#B3A2C7", "#93CDDD", "#FAC090"];

const state = {
  workspace: null,
  fs: null,
  selected: null,
  mode: "blocks",
  searchOpen: false,
  query: "",
  pendingPath: "",
  editorStamp: 0,
  copiedAttributes: null,
  copiedFrom: "",
  mergeChoice: "",
  pendingMerge: null,
};

function h(tag, props, kids) {
  const node = document.createElement(tag);
  Object.entries(props || {}).forEach(([key, value]) => {
    if (value == null || value === false) return;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  });
  (kids || []).forEach((kid) => node.append(kid));
  return node;
}

function num(value, digits) {
  if (value == null || Number.isNaN(Number(value))) return "";
  return Number(value).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

function pct(value) {
  if (value == null) return "";
  return (Number(value) * 100).toFixed(1) + "%";
}

function cell(text, title) {
  const node = h("td", { text: text == null ? "" : String(text) });
  if (title != null && title !== "") node.title = String(title);
  return node;
}

function showError(error) {
  const bar = document.getElementById("error");
  bar.classList.remove("ok");
  bar.hidden = !error;
  bar.textContent = error ? String(error.message || error) : "";
}

function showStatus(text) {
  const bar = document.getElementById("error");
  bar.classList.add("ok");
  bar.hidden = !text;
  bar.textContent = text || "";
}

async function readJson(response) {
  const data = await response.json().catch(() => ({}));
  if (response.status === 409) {
    const error = new Error("collision");
    error.collisions = data.collisions || [];
    error.notices = data.notices || [];
    throw error;
  }
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

let pending = Promise.resolve();

function act(body) {
  const run = pending.then(() => fetch("/api/act", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }).then(readJson));
  pending = run.then(() => {}, () => {});
  return run;
}

function blockById(id) {
  return (state.workspace.blocks || []).find((block) => block.id === id) || null;
}

function queryText() {
  return state.searchOpen ? state.query.trim().toLowerCase() : "";
}

function matches(block) {
  const query = queryText();
  if (!query) return true;
  const filled = block.attributes.filter((item) => String(item.value || "").trim());
  const hay = [block.label].concat(filled.flatMap((item) => [item.key, item.value])).join("\n").toLowerCase();
  return hay.includes(query);
}

function groupNotices(notices) {
  const groups = [];
  const index = new Map();
  notices.forEach((notice) => {
    const path = notice.path || "";
    let message = notice.message || "";
    if (path && message.startsWith(path)) message = message.slice(path.length).replace(/^:\s*/, "");
    const name = path.split(/[\\/]/).pop() || "";
    if (!index.has(message)) {
      index.set(message, groups.length);
      groups.push({ message, paths: [] });
    }
    const group = groups[index.get(message)];
    group.paths.push(name || path);
    if (notice.kind === "note") group.kind = "note";
  });
  return groups;
}

function visibleBlocks() {
  return (state.workspace.blocks || []).filter(matches);
}

function setWorkspace(workspace, rebuildEditor) {
  state.workspace = workspace;
  const model = document.getElementById("model");
  if (document.activeElement !== model) model.value = workspace.model || "";
  renderMeta();
  renderBoard();
  if (rebuildEditor) renderEditor();
  if (state.selected && !blockById(state.selected)) {
    state.selected = null;
    renderEditor();
  }
}

async function ingestInto(path, id) {
  showError("");
  try {
    const workspace = await act({ action: "ingest", path, into: id });
    setWorkspace(workspace, true);
    const block = (workspace.blocks || []).find((item) => item.id === id);
    showStatus("Added files to " + (block ? block.label : "the block") + ".");
  } catch (error) {
    showError(error);
  }
}

async function mergeBlocks(sourceId, targetId) {
  const source = blockById(sourceId);
  const target = blockById(targetId);
  if (!source || !target || sourceId === targetId) return;
  if (state.mergeChoice) {
    finishMerge(sourceId, targetId, state.mergeChoice);
    return;
  }
  state.pendingMerge = { sourceId, targetId };
  document.getElementById("collision-form").hidden = true;
  document.getElementById("onedrive-form").hidden = true;
  document.getElementById("merge-form").hidden = false;
  document.getElementById("merge-title").textContent = "Move " + source.label + " into " + target.label;
  document.getElementById("merge-blurb").textContent = source.label + " is removed. Where the same shape, concurrency, and run exist in both blocks:";
  document.querySelectorAll("input[name=merge-choice]").forEach((input) => { input.checked = input.value === "keep"; });
  document.getElementById("merge-always").checked = false;
  document.getElementById("modal").hidden = false;
}

async function finishMerge(sourceId, targetId, choice) {
  const target = blockById(targetId);
  showError("");
  document.getElementById("modal").hidden = true;
  state.pendingMerge = null;
  try {
    setWorkspace(await act({ action: "merge", id: sourceId, into: targetId, choice }), true);
    showStatus("Moved measurements into " + (target ? target.label : "the block") + ".");
  } catch (error) {
    showError(error);
  }
}

async function ingest(path, resolutions) {
  showError("");
  try {
    const workspace = await act({ action: "ingest", path, resolutions: resolutions || {} });
    document.getElementById("modal").hidden = true;
    state.pendingPath = "";
    setWorkspace(workspace, true);
  } catch (error) {
    if (error.collisions) {
      state.pendingPath = path;
      openCollision(error.collisions, error.notices || []);
      return;
    }
    showError(error);
  }
}

function openCollision(collisions, notices) {
  const list = document.getElementById("collision-list");
  list.replaceChildren();
  collisions.forEach((item) => {
    const rename = h("input", { type: "text", value: item.suggestion, "data-label": item.label });
    const over = h("input", { type: "radio", name: "c-" + item.label, value: "overwrite", checked: "checked" });
    const ren = h("input", { type: "radio", name: "c-" + item.label, value: "rename" });
    list.append(h("div", { class: "choice" }, [
      h("strong", { text: item.label }),
      h("div", { class: "opts" }, [over, h("span", { text: "Overwrite" }), ren, h("span", { text: "Rename" }), rename]),
    ]));
  });
  const notes = document.getElementById("collision-notes");
  const skipCount = notices.filter((notice) => notice.kind !== "note").length;
  notes.textContent = skipCount ? skipCount + " file" + (skipCount === 1 ? "" : "s") + " will be skipped." : "";
  document.getElementById("merge-form").hidden = true;
  document.getElementById("onedrive-form").hidden = true;
  document.getElementById("collision-form").hidden = false;
  document.getElementById("modal").hidden = false;
}

function renderMeta() {
  const workspace = state.workspace;
  const count = (workspace.blocks || []).length;
  document.getElementById("meta").textContent = count + " block" + (count === 1 ? "" : "s") + " · " + (workspace.path || "");
}

function renderFiles() {
  const fs = state.fs;
  const aside = document.getElementById("files");
  aside.replaceChildren();
  if (!fs) return;
  const crumbs = h("div", { class: "crumbs" });
  const computer = h("button", { type: "button", text: "Computer" });
  computer.addEventListener("click", () => openDir(""));
  crumbs.append(computer);
  (fs.crumbs || []).forEach((crumb, index) => {
    crumbs.append(h("span", { text: " / " }));
    const button = h("button", { type: "button", text: crumb.name });
    button.addEventListener("click", () => openDir(crumb.path));
    crumbs.append(button);
  });
  aside.append(crumbs);
  if (fs.truncated) aside.append(h("p", { class: "hint", text: "Showing the 400 most recently changed." }));
  (fs.entries || []).forEach((entry) => {
    const row = h("div", { class: "file " + entry.kind });
    if (entry.kind === "dir" || entry.kind === "bench") row.setAttribute("draggable", "true");
    row.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", entry.path);
      event.dataTransfer.effectAllowed = "copy";
    });
    const name = h("button", { type: "button", class: "name", text: entry.name, title: entry.path });
    name.addEventListener("click", () => {
      if (entry.kind === "dir") openDir(entry.path);
      else if (entry.kind === "bta") importPath(entry.path);
    });
    row.append(name);
    if (entry.kind === "dir" || entry.kind === "bench") {
      const add = h("button", { type: "button", class: "mini", text: "Add" });
      add.addEventListener("click", () => ingest(entry.path));
      row.append(add);
    }
    if (entry.kind === "bta") {
      const open = h("button", { type: "button", class: "mini", text: "Open" });
      open.addEventListener("click", () => importPath(entry.path));
      row.append(open);
    }
    aside.append(row);
  });
}

async function openDir(path) {
  showError("");
  try {
    state.fs = await readJson(await fetch("/api/fs?path=" + encodeURIComponent(path)));
    renderFiles();
  } catch (error) {
    showError(error);
  }
}

function confirmReplace() {
  return !(state.workspace.blocks || []).length || window.confirm("Replace the workspace with this .bta?");
}

async function importPath(path) {
  if (!confirmReplace()) return;
  showError("");
  try {
    setWorkspace(await act({ action: "import", path }), true);
  } catch (error) {
    showError(error);
  }
}

function renderBoard() {
  const board = document.getElementById("board");
  const query = queryText();
  const shown = visibleBlocks();
  document.getElementById("search-count").textContent = state.searchOpen
    ? shown.length + " of " + (state.workspace.blocks || []).length
    : "";
  board.replaceChildren();
  const notices = groupNotices(state.workspace.notices || []);
  if (notices.length) {
    const box = h("div", { class: "notices" });
    const dismiss = h("button", { type: "button", text: "Dismiss" });
    dismiss.addEventListener("click", async () => {
      try { setWorkspace(await act({ action: "clear_notices" }), false); } catch (error) { showError(error); }
    });
    const skips = notices.filter((notice) => notice.kind !== "note");
    const notes = notices.filter((notice) => notice.kind === "note");
    const skipCount = skips.reduce((count, notice) => count + notice.paths.length, 0);
    const heading = [];
    if (skipCount) heading.push(skipCount + " file" + (skipCount === 1 ? "" : "s") + " skipped");
    if (notes.length) heading.push(notes.length + " note" + (notes.length === 1 ? "" : "s"));
    box.append(h("div", { class: "actions" }, [h("span", { text: heading.join(" · ") }), dismiss]));
    const list = h("ul");
    skips.concat(notes).slice(0, 6).forEach((notice) => {
      const text = notice.kind === "note"
        ? notice.message
        : notice.paths.length > 1
          ? notice.paths.length + " files: " + notice.message
          : (notice.paths[0] || "") + " — " + notice.message;
      list.append(h("li", { text, title: notice.paths.join("\n") }));
    });
    if (notices.length > 6) list.append(h("li", { text: (notices.length - 6) + " more reasons" }));
    box.append(list);
    board.append(box);
  }
  if (state.mode === "figures") {
    renderFigures(board, shown);
    return;
  }
  if (!shown.length) {
    board.append(h("p", { class: "empty", text: query
      ? "Nothing matches."
      : "Drag a results directory here. Drop it on a block to add the files to that block. The name in the file is not the block." }));
    return;
  }
  const cards = h("div", { class: "cards" });
  shown.forEach((block) => cards.append(card(block)));
  board.append(cards);
}

function card(block) {
  const shapes = (block.shapes || []).filter((shape) => shape.included);
  const bits = ["÷ " + block.gpus];
  if (shapes.length) bits.push(shapes.map((shape) => shape.input_len + "/" + shape.output_len).join(", "));
  else bits.push("no rows in this run");
  const badges = [];
  if (block.baseline) badges.push("baseline");
  if (block.baseline_label) badges.push("vs " + block.baseline_label);
  if (block.chart) badges.push("on figure");
  if ((block.runs || []).length > 1) badges.push("run " + block.active_run);
  const node = h("article", { class: "card" + (state.selected === block.id ? " on" : ""), "data-id": block.id });
  node.append(h("header", { text: block.label }));
  node.append(h("p", { text: bits.join(" · ") }));
  node.append(h("p", { class: "badges", text: badges.join(" · ") }));
  if (block.missing) node.append(h("p", { class: "miss", text: block.missing + " source file" + (block.missing === 1 ? "" : "s") + " missing" }));
  node.setAttribute("draggable", "true");
  node.addEventListener("dragstart", (event) => {
    event.dataTransfer.setData("text/plain", "block:" + block.id);
    event.dataTransfer.effectAllowed = "copy";
    event.stopPropagation();
  });
  node.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.stopPropagation();
    node.classList.add("drop");
  });
  node.addEventListener("dragleave", (event) => {
    if (!node.contains(event.relatedTarget)) node.classList.remove("drop");
  });
  node.addEventListener("drop", (event) => {
    event.preventDefault();
    event.stopPropagation();
    node.classList.remove("drop");
    document.getElementById("board").classList.remove("drop");
    const payload = event.dataTransfer.getData("text/plain");
    if (!payload || payload === "block:" + block.id) return;
    if (payload.startsWith("block:")) mergeBlocks(payload.slice(6), block.id);
    else ingestInto(payload, block.id);
  });
  node.addEventListener("mouseenter", (event) => showHover(block, event));
  node.addEventListener("mousemove", moveHover);
  node.addEventListener("mouseleave", hideHover);
  node.addEventListener("click", () => {
    state.selected = block.id;
    hideHover();
    renderBoard();
    renderEditor();
  });
  return node;
}

function showHover(block, event) {
  const tip = document.getElementById("hover");
  const lines = block.attributes.filter((item) => String(item.value || "").trim());
  tip.replaceChildren(h("strong", { text: block.label }));
  if (!lines.length) tip.append(h("p", { text: "No attributes yet." }));
  lines.forEach((item) => tip.append(h("p", { text: item.key + ": " + item.value })));
  tip.hidden = false;
  moveHover(event);
}

function moveHover(event) {
  const tip = document.getElementById("hover");
  tip.style.left = Math.min(event.clientX + 14, window.innerWidth - 340) + "px";
  tip.style.top = Math.min(event.clientY + 14, window.innerHeight - 40) + "px";
}

function hideHover() {
  document.getElementById("hover").hidden = true;
}

function renderFigures(board, blocks) {
  const wanted = new Set(blocks.map((block) => block.label));
  const figures = (state.workspace.figures || []).map((figure) => ({
    ...figure,
    series: figure.series.filter((series) => wanted.has(series.label)),
  })).filter((figure) => figure.series.length);
  if (!figures.length) {
    board.append(h("p", { class: "empty", text: "Tick “On figure” on a block to draw throughput per GPU against interactivity. The line follows concurrency order." }));
    return;
  }
  figures.forEach((figure) => board.append(figureView(figure)));
}

function figureView(figure) {
  const wrap = h("section", { class: "figure" });
  wrap.append(draw(figure));
  const table = h("table");
  table.append(h("tr", {}, ["", "conc", "tok/s/gpu", "interactivity"].map((name) => h("th", { text: name }))));
  figure.series.forEach((series) => {
    series.points.forEach((point, index) => {
      const row = h("tr");
      row.append(cell(index ? "" : series.label));
      row.append(cell(point.concurrency));
      row.append(cell(num(point.y, 0), point.y));
      row.append(cell(num(point.x, 2), point.x));
      table.append(row);
    });
  });
  wrap.append(h("div", { class: "scroll readout" }, [table]));
  return wrap;
}

function draw(figure) {
  const width = 880;
  const height = 460;
  const pad = { l: 72, r: 24, t: 58, b: 54 };
  const points = figure.series.flatMap((series) => series.points);
  let x0 = Math.min(...points.map((point) => point.x));
  let x1 = Math.max(...points.map((point) => point.x));
  let y0 = Math.min(...points.map((point) => point.y));
  let y1 = Math.max(...points.map((point) => point.y));
  if (x0 === x1) { x0 -= 1; x1 += 1; }
  if (y0 === y1) { y0 -= 1; y1 += 1; }
  const xPad = (x1 - x0) * 0.08;
  const yPad = (y1 - y0) * 0.08;
  x0 -= xPad; x1 += xPad; y0 -= yPad; y1 += yPad;
  const sx = (value) => pad.l + (value - x0) / (x1 - x0) * (width - pad.l - pad.r);
  const sy = (value) => height - pad.b - (value - y0) / (y1 - y0) * (height - pad.t - pad.b);
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 " + width + " " + height);
  const ns = "http://www.w3.org/2000/svg";
  function el(name, attrs) {
    const node = document.createElementNS(ns, name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    svg.append(node);
    return node;
  }
  el("rect", { x: 0, y: 0, width, height, fill: "#404040" });
  for (let tick = 0; tick <= 4; tick += 1) {
    const x = pad.l + (width - pad.l - pad.r) * tick / 4;
    const y = pad.t + (height - pad.t - pad.b) * tick / 4;
    el("line", { x1: x, x2: x, y1: pad.t, y2: height - pad.b, stroke: "#8C8C8C", "stroke-width": 1 });
    el("line", { x1: pad.l, x2: width - pad.r, y1: y, y2: y, stroke: "#8C8C8C", "stroke-width": 1 });
    el("text", { x, y: height - pad.b + 16, fill: "#BFBFBF", "font-size": 11, "text-anchor": "middle" }).textContent = num(x0 + (x1 - x0) * tick / 4, 0);
    el("text", { x: pad.l - 8, y: y + 4, fill: "#BFBFBF", "font-size": 11, "text-anchor": "end" }).textContent = num(y1 - (y1 - y0) * tick / 4, 0);
  }
  el("text", { x: width / 2, y: 24, fill: "#D9D9D9", "font-size": 16, "text-anchor": "middle" }).textContent = figure.title;
  el("text", { x: width / 2, y: height - 12, fill: "#BFBFBF", "font-size": 12, "text-anchor": "middle" }).textContent = "Interactivity (token/s/user)";
  const yTitle = el("text", { x: 16, y: height / 2, fill: "#BFBFBF", "font-size": 12, "text-anchor": "middle", transform: "rotate(-90 16 " + (height / 2) + ")" });
  yTitle.textContent = "Total token throughput per GPU";
  figure.series.forEach((series) => {
    const color = series.color || 0;
    const line = LINES[color % LINES.length];
    const mark = MARKS[color % MARKS.length];
    if (series.points.length > 1) {
      el("polyline", {
        fill: "none", stroke: line, "stroke-width": 2,
        points: series.points.map((point) => sx(point.x) + "," + sy(point.y)).join(" "),
      });
    }
    series.points.forEach((point) => {
      const dot = el("circle", { cx: sx(point.x), cy: sy(point.y), r: 3.5, fill: mark, stroke: line });
      const title = document.createElementNS(ns, "title");
      title.textContent = series.label + " c" + point.concurrency + "  " + num(point.y, 1) + " tok/s  " + num(point.x, 2) + " interactivity";
      dot.append(title);
    });
  });
  figure.series.forEach((series, index) => {
    const x = pad.l + (index % 3) * 250;
    const y = 40 + Math.floor(index / 3) * 14;
    el("line", { x1: x, x2: x + 16, y1: y, y2: y, stroke: LINES[(series.color || 0) % LINES.length], "stroke-width": 2 });
    el("text", { x: x + 20, y: y + 4, fill: "#D9D9D9", "font-size": 11 }).textContent = series.label;
  });
  return svg;
}

function renderEditor() {
  const aside = document.getElementById("editor");
  const block = state.selected ? blockById(state.selected) : null;
  if (!block || state.mode !== "blocks") {
    aside.hidden = true;
    aside.replaceChildren();
    return;
  }
  aside.hidden = false;
  const stamp = ++state.editorStamp;
  aside.replaceChildren();
  const close = h("button", { type: "button", text: "Close" });
  close.addEventListener("click", () => { state.selected = null; renderBoard(); renderEditor(); });
  aside.append(h("div", { class: "actions" }, [close]));
  aside.append(field("Label", h("input", { id: "f-label", value: block.label, spellcheck: "false" }), (input) => {
    input.addEventListener("change", () => patch({ label: input.value }, false));
  }));
  const gpus = h("input", { id: "f-gpus", type: "number", min: "0", step: "any", value: block.gpus });
  aside.append(field("GPUs, the divisor", gpus, (input) => {
    input.addEventListener("change", () => patch({ gpus: Number(input.value) }, true));
  }));
  aside.append(h("p", { class: "hint", text: "Per-GPU throughput is the server total divided by this. The folder name is not the divisor." }));
  const tp = (block.attributes.find((item) => item.key === "tp") || {}).value;
  if (tp && Number(tp) > 0 && Number(tp) !== Number(block.gpus)) {
    const use = h("button", { type: "button", text: "Use TP " + tp + " as the divisor" });
    use.addEventListener("click", () => patch({ gpus: Number(tp) }, true));
    aside.append(use);
  }
  if ((block.runs || []).length > 1) {
    const select = h("select", { id: "f-run" });
    block.runs.forEach((run) => select.append(h("option", { value: run, text: "r" + run, selected: run === block.active_run ? "selected" : null })));
    select.addEventListener("change", () => patch({ active_run: Number(select.value) }, true));
    aside.append(field("Run", select));
    aside.append(h("p", { class: "hint", text: "Other runs stay in the file. The sheet and the figure use this one." }));
  }
  const baseline = h("input", { type: "checkbox" });
  baseline.checked = !!block.baseline;
  baseline.addEventListener("change", () => patch({ baseline: baseline.checked }, true));
  const chart = h("input", { type: "checkbox" });
  chart.checked = !!block.chart;
  chart.addEventListener("change", () => patch({ chart: chart.checked }, false).then(renderBoard));
  aside.append(h("div", { class: "checks" }, [
    h("label", {}, [baseline, document.createTextNode("Baseline")]),
    h("label", {}, [chart, document.createTextNode("On figure")]),
  ]));
  if (!block.baseline) {
    const select = h("select");
    select.append(h("option", { value: "", text: "No baseline" }));
    (state.workspace.blocks || []).filter((item) => item.baseline && item.id !== block.id).forEach((item) => {
      select.append(h("option", { value: item.id, text: item.label, selected: item.id === block.baseline_id ? "selected" : null }));
    });
    select.addEventListener("change", () => patch({ baseline_id: select.value || null }, true));
    aside.append(field("Ratio against", select));
  }
  aside.append(h("h2", { text: "Attributes" }));
  const copyAttrs = h("button", { type: "button", text: "Copy attributes" });
  copyAttrs.addEventListener("click", () => {
    state.copiedAttributes = block.attributes.map((item) => ({ key: item.key, value: item.value }));
    state.copiedFrom = block.label;
    renderEditor();
  });
  const pasteAttrs = h("button", { type: "button", text: "Paste attributes" });
  pasteAttrs.disabled = !state.copiedAttributes;
  pasteAttrs.addEventListener("click", () => {
    if (!state.copiedAttributes) return;
    const filled = block.attributes.some((item) => String(item.value || "").trim());
    const question = "Replace the attributes on " + block.label + " with the copy from " + state.copiedFrom + "?";
    if (filled && !window.confirm(question)) return;
    const attributes = {};
    state.copiedAttributes.forEach((item) => { attributes[item.key] = item.value; });
    patch({ attributes }, true);
  });
  aside.append(h("div", { class: "attr-actions" }, [copyAttrs, pasteAttrs]));
  if (state.copiedFrom) aside.append(h("p", { class: "hint", text: "Copied from " + state.copiedFrom + "." }));
  const attrs = h("div", { class: "attrs" });
  block.attributes.forEach((item, index) => attrs.append(attrRow(block, item, index)));
  aside.append(attrs);
  const add = h("button", { type: "button", text: "Add attribute" });
  add.addEventListener("click", () => {
    const attributes = Object.fromEntries(block.attributes.map((item) => [item.key, item.value]));
    let name = "note";
    let n = 2;
    while (Object.prototype.hasOwnProperty.call(attributes, name)) name = "note " + n++;
    attributes[name] = "";
    patch({ attributes }, true);
  });
  aside.append(add);
  if ((block.shapes || []).length) {
    aside.append(h("h2", { text: "Shapes" }));
    const shapes = h("div", { class: "shapes" });
    block.shapes.forEach((shape) => {
      const box = h("input", { type: "checkbox" });
      box.checked = !!shape.included;
      box.addEventListener("change", () => {
        const excluded = block.shapes
          .filter((item) => (item === shape ? !box.checked : !item.included))
          .map((item) => [item.input_len, item.output_len]);
        patch({ excluded_shapes: excluded }, true);
      });
      shapes.append(h("label", {}, [box, document.createTextNode(shape.input_len + "/" + shape.output_len)]));
    });
    aside.append(shapes);
  }
  aside.append(measurementTable(block));
  if (block.ingested_from) aside.append(h("p", { class: "from", text: block.ingested_from }));
  if (block.missing) aside.append(h("p", { class: "miss", text: "A missing file keeps its numbers. The link is marked, and nothing else fails." }));
  const others = (state.workspace.blocks || []).filter((item) => item.id !== block.id);
  if (others.length) {
    const select = h("select");
    others.forEach((item) => select.append(h("option", { value: item.id, text: item.label })));
    const moveInto = h("button", { type: "button", text: "Move measurements into" });
    moveInto.addEventListener("click", () => mergeBlocks(block.id, select.value));
    aside.append(h("div", { class: "attr-actions" }, [moveInto, select]));
  }
  const mergeHint = state.mergeChoice
    ? "Drag a block onto another block. Matching rows use " + state.mergeChoice + " until you refresh."
    : "Drag a block onto another block and choose overwrite, keep, the worse row, or the better row.";
  aside.append(h("p", { class: "hint", text: "Drag a results folder onto this block to add those files here. " + mergeHint }));
  const up = h("button", { type: "button", text: "Move up" });
  const down = h("button", { type: "button", text: "Move down" });
  const remove = h("button", { type: "button", text: "Remove block" });
  up.addEventListener("click", () => patch({ action: "move", direction: -1 }, true));
  down.addEventListener("click", () => patch({ action: "move", direction: 1 }, true));
  remove.addEventListener("click", async () => {
    if (!window.confirm("Remove " + block.label + "?")) return;
    try {
      state.selected = null;
      setWorkspace(await act({ action: "delete", id: block.id }), true);
    } catch (error) { showError(error); }
  });
  aside.append(h("div", { class: "actions" }, [up, down, remove]));
  if (stamp !== state.editorStamp) return;
}

function field(label, input, bind) {
  if (bind) bind(input);
  return h("label", { class: "field" }, [document.createTextNode(label), input]);
}

function attrRow(block, item, index) {
  const known = ["docker image", "host name", "accuracy score", "BKC", "chip", "framework", "tp"].indexOf(item.key) >= 0;
  const key = h("input", { value: item.key, spellcheck: "false", "data-attr": "key", "data-index": index });
  const value = h("input", { value: item.value, spellcheck: "false", "data-attr": "value", "data-index": index });
  if (known) key.readOnly = true;
  const save = () => {
    const attributes = {};
    document.querySelectorAll("#editor [data-attr='key']").forEach((node) => {
      const val = document.querySelector("#editor [data-attr='value'][data-index='" + node.getAttribute("data-index") + "']");
      const name = node.value.trim();
      if (name) attributes[name] = val.value;
    });
    patch({ attributes }, false);
  };
  key.addEventListener("change", save);
  value.addEventListener("change", save);
  const row = h("div", { class: "attr" }, [key, value]);
  if (!known) {
    const drop = h("button", { type: "button", text: "×", title: "Remove attribute" });
    drop.addEventListener("click", () => {
      const attributes = Object.fromEntries(block.attributes.filter((attr) => attr.key !== item.key).map((attr) => [attr.key, attr.value]));
      patch({ attributes }, true);
    });
    row.append(drop);
  }
  return row;
}

function measurementTable(block) {
  const ratios = (block.shapes || []).some((shape) => shape.rows.some((row) => row.ratios) || shape.geomean);
  const head = h("tr");
  ["isl", "osl", "conc", "tok/s/gpu", "interactivity", "TTFT", "TPOT"].forEach((name) => head.append(h("th", { text: name })));
  if (ratios) ["tok/s %", "TTFT %", "TPOT %"].forEach((name) => head.append(h("th", { text: name })));
  head.append(h("th", { text: "source" }));
  head.append(h("th", { text: "" }));
  const table = h("table", {}, [head]);
  (block.shapes || []).forEach((shape) => {
    shape.rows.forEach((row) => {
      const tr = h("tr", { class: shape.included ? "" : "dim" });
      tr.append(cell(row.input_len), cell(row.output_len), cell(row.concurrency));
      tr.append(cell(num(row.per_gpu, 0), row.per_gpu));
      tr.append(cell(num(row.interactivity, 2), row.interactivity));
      tr.append(cell(num(row.median_ttft_ms, 1), row.median_ttft_ms));
      tr.append(cell(num(row.median_tpot_ms, 1), row.median_tpot_ms));
      if (ratios) {
        tr.append(cell(row.ratios ? pct(row.ratios.tput) : "", row.ratios && row.ratios.tput));
        tr.append(cell(row.ratios ? pct(row.ratios.ttft) : "", row.ratios && row.ratios.ttft));
        tr.append(cell(row.ratios ? pct(row.ratios.tpot) : "", row.ratios && row.ratios.tpot));
      }
      const name = (row.source || "").split(/[\\/]/).pop();
      const src = cell(row.missing ? name + " missing" : name, row.source);
      src.className = row.missing ? "src miss" : "src";
      tr.append(src);
      const drop = h("button", { type: "button", text: "×", title: "Remove this measurement" });
      drop.addEventListener("click", () => patch({
        action: "delete_point",
        input_len: row.input_len,
        output_len: row.output_len,
        concurrency: row.concurrency,
        run: row.run,
      }, true));
      tr.append(h("td", {}, [drop]));
      table.append(tr);
    });
    if (shape.geomean) {
      const tr = h("tr", { class: "geo" });
      tr.append(cell(""), cell(""), cell("geomean"), cell(""), cell(""), cell(""), cell(""));
      tr.append(cell(pct(shape.geomean.tput), shape.geomean.tput));
      tr.append(cell(pct(shape.geomean.ttft), shape.geomean.ttft));
      tr.append(cell(pct(shape.geomean.tpot), shape.geomean.tpot));
      tr.append(cell(""), cell(""));
      table.append(tr);
    }
  });
  return h("div", { class: "scroll" }, [table]);
}

async function patch(fields, rebuildEditor) {
  const id = state.selected;
  showError("");
  try {
    const body = Object.assign({ action: "patch", id }, fields);
    if (fields.action) body.action = fields.action;
    const workspace = await act(body);
    setWorkspace(workspace, rebuildEditor);
    if (!rebuildEditor) renderBoard();
  } catch (error) {
    showError(error);
    renderEditor();
  }
}

function download(url) {
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  document.body.append(link);
  link.click();
  link.remove();
}

function wire() {
  document.getElementById("new").addEventListener("click", async () => {
    if ((state.workspace.blocks || []).length && !window.confirm("Clear the workspace?")) return;
    try {
      state.selected = null;
      setWorkspace(await act({ action: "new" }), true);
    } catch (error) { showError(error); }
  });
  document.getElementById("import").addEventListener("click", () => document.getElementById("import-file").click());
  document.getElementById("import-file").addEventListener("change", async (event) => {
    const file = event.target.files && event.target.files[0];
    event.target.value = "";
    if (!file || !confirmReplace()) return;
    try {
      state.selected = null;
      setWorkspace(await act({ action: "import", text: await file.text() }), true);
    } catch (error) { showError(error); }
  });
  document.getElementById("save-bta").addEventListener("click", () => download("/api/export.bta"));
  document.getElementById("save-xlsx").addEventListener("click", () => download("/api/export.xlsx"));
  document.getElementById("save-onedrive").addEventListener("click", () => {
    document.getElementById("collision-form").hidden = true;
    document.getElementById("merge-form").hidden = true;
    document.getElementById("onedrive-form").hidden = false;
    const box = document.getElementById("onedrive-link");
    box.value = (state.workspace.onedrive && state.workspace.onedrive.url) || "";
    document.getElementById("modal").hidden = false;
    box.focus();
  });
  document.getElementById("onedrive-cancel").addEventListener("click", () => {
    document.getElementById("modal").hidden = true;
  });
  document.getElementById("onedrive-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const url = document.getElementById("onedrive-link").value.trim();
    const button = document.getElementById("onedrive-add");
    if (!url) {
      showError("Paste the OneDrive link.");
      return;
    }
    button.disabled = true;
    showError("");
    try {
      const result = await readJson(await fetch("/api/onedrive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      }));
      state.workspace.onedrive = { file: result.file, url };
      document.getElementById("modal").hidden = true;
      showStatus("Added " + result.sheet + " to " + result.file + ".");
    } catch (error) {
      showError(error);
    } finally {
      button.disabled = false;
    }
  });
  document.getElementById("model").addEventListener("change", async (event) => {
    try { setWorkspace(await act({ action: "model", model: event.target.value }), false); } catch (error) { showError(error); }
  });
  document.getElementById("search-toggle").addEventListener("click", () => toggleSearch(true));
  document.getElementById("search").addEventListener("input", (event) => {
    state.query = event.target.value;
    renderBoard();
  });
  document.getElementById("mode-blocks").addEventListener("click", () => setMode("blocks"));
  document.getElementById("mode-figures").addEventListener("click", () => setMode("figures"));
  const board = document.getElementById("board");
  board.addEventListener("dragover", (event) => { event.preventDefault(); board.classList.add("drop"); });
  board.addEventListener("dragleave", () => board.classList.remove("drop"));
  board.addEventListener("drop", (event) => {
    event.preventDefault();
    board.classList.remove("drop");
    const path = event.dataTransfer.getData("text/plain");
    if (path && !path.startsWith("block:")) ingest(path);
  });
  document.getElementById("collision-cancel").addEventListener("click", () => {
    document.getElementById("modal").hidden = true;
    state.pendingPath = "";
  });
  document.getElementById("merge-cancel").addEventListener("click", () => {
    document.getElementById("modal").hidden = true;
    state.pendingMerge = null;
  });
  document.getElementById("merge-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const pending = state.pendingMerge;
    if (!pending) return;
    const picked = document.querySelector("input[name=merge-choice]:checked");
    const choice = picked ? picked.value : "keep";
    state.mergeChoice = document.getElementById("merge-always").checked ? choice : "";
    finishMerge(pending.sourceId, pending.targetId, choice);
  });
  document.getElementById("collision-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const resolutions = {};
    document.querySelectorAll("#collision-list .choice").forEach((choice) => {
      const picked = choice.querySelector("input[type=radio]:checked");
      const rename = choice.querySelector("input[type=text]");
      const label = rename.getAttribute("data-label");
      resolutions[label] = picked.value === "rename"
        ? { action: "rename", label: rename.value }
        : { action: "overwrite" };
    });
    ingest(state.pendingPath, resolutions);
  });
  document.addEventListener("keydown", (event) => {
    const typing = event.target && (event.target.closest("input, textarea, select"));
    if (event.key === "/" && !typing) {
      event.preventDefault();
      toggleSearch(true);
    } else if (event.key === "Escape") {
      if (state.searchOpen) toggleSearch(false);
      else if (state.selected) {
        state.selected = null;
        renderBoard();
        renderEditor();
      }
    }
  });
  window.addEventListener("resize", hideHover);
}

function toggleSearch(open) {
  state.searchOpen = open;
  const bar = document.getElementById("searchbar");
  bar.hidden = !open;
  document.getElementById("search-toggle").classList.toggle("on", open);
  if (open) document.getElementById("search").focus();
  else state.query = document.getElementById("search").value;
  renderBoard();
}

function setMode(mode) {
  state.mode = mode;
  document.getElementById("mode-blocks").classList.toggle("on", mode === "blocks");
  document.getElementById("mode-figures").classList.toggle("on", mode === "figures");
  hideHover();
  renderBoard();
  renderEditor();
}

async function boot() {
  wire();
  try {
    state.workspace = await readJson(await fetch("/api/workspace"));
    document.getElementById("model").value = state.workspace.model || "";
    state.fs = await readJson(await fetch("/api/fs?path=" + encodeURIComponent(state.workspace.browser_root || "")));
    renderMeta();
    renderFiles();
    renderBoard();
  } catch (error) {
    showError(error);
  }
}

boot();
