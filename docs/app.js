// Loads the shul data files (built by scraper/update.py) and shows today onward.
// Which shuls to show is remembered on this device; Aguda is on by default.

const daysEl = document.getElementById("days");
const shulPickerEl = document.getElementById("shul-picker");
const headerEl = document.getElementById("shul-header");
const footerEl = document.getElementById("sources");
const STORAGE_KEY = "selectedShuls";

let shuls = [];          // from data/shuls.json
let loaded = {};         // shul id -> that shul's data file
let selected = [];       // shul ids currently ticked

// "2026-09-16" -> Date at local midnight. (new Date("2026-09-16") would use UTC,
// which shows the previous day in New Jersey.)
function parseDate(iso) {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day);
}

function todayIso() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

// Builds an element with text content (never innerHTML, so PDF text can't inject markup).
function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function link(href, text, className) {
  const node = el("a", className, text);
  node.href = href;
  return node;
}

function hasHebrew(text) {
  return /[֐-׿]/.test(text);
}

function readSelection() {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
    if (Array.isArray(saved)) return saved.filter((id) => shuls.some((s) => s.id === id));
  } catch (error) {
    // Private browsing or blocked storage: fall back to the default below.
  }
  return shuls.filter((s) => s.default_on).map((s) => s.id);
}

function saveSelection() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(selected));
  } catch (error) {
    // Not being able to remember the choice shouldn't break the page.
  }
}

function renderPicker() {
  shulPickerEl.replaceChildren(el("span", "picker-label", "Show:"));
  for (const shul of shuls) {
    const label = el("label", "shul-toggle");
    const box = el("input");
    box.type = "checkbox";
    box.checked = selected.includes(shul.id);
    box.addEventListener("change", async () => {
      selected = box.checked
        ? [...shuls.map((s) => s.id).filter((id) => selected.includes(id) || id === shul.id)]
        : selected.filter((id) => id !== shul.id);
      saveSelection();
      await loadSelected();
      render();
    });
    label.append(box, el("span", null, shul.short_name));
    shulPickerEl.append(label);
  }
}

function renderShulTimes(day, showName, shul) {
  const block = el("div", "shul-block");
  if (showName) {
    block.append(el("h3", "shul-name", shul.name));
  }
  const list = el("dl", "times");
  for (const item of day.items) {
    const bold = item.bold ? " bold" : "";
    if (item.time) {
      list.append(el("dt", bold.trim(), item.label), el("dd", bold.trim(), item.time));
    } else {
      list.append(el("dd", "note" + bold, item.label));
    }
  }
  block.append(list);
  return block;
}

function renderDay(dateIso, isToday) {
  const section = el("section", "day");
  const date = parseDate(dateIso);
  const weekday = date.getDay() === 6 ? "Shabbos" : date.toLocaleDateString("en-US", { weekday: "long" });

  const heading = el("h2");
  heading.append(weekday, el("br"),
    date.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }));
  if (isToday) heading.append(el("span", "today-badge", "Today"));
  section.append(heading);

  const showing = selected
    .map((id) => ({ shul: shuls.find((s) => s.id === id), day: (loaded[id]?.days || []).find((d) => d.date === dateIso) }))
    .filter((entry) => entry.shul);

  // The Hebrew date and parsha are the same for every shul, so show them once.
  const withDay = showing.find((entry) => entry.day);
  if (withDay) {
    const captions = [withDay.day.hebrew_date, ...withDay.day.titles].filter(Boolean);
    if (captions.length) {
      const caption = el("p", "hebrew", captions.join(" · "));
      if (hasHebrew(caption.textContent)) {
        caption.dir = "rtl";
        caption.lang = "he";
      }
      section.append(caption);
    }
  }

  for (const entry of showing) {
    if (entry.day) {
      section.append(renderShulTimes(entry.day, showing.length > 1, entry.shul));
    } else if (showing.length > 1) {
      const block = el("div", "shul-block");
      block.append(el("h3", "shul-name", entry.shul.name),
        el("p", "note", "No times posted for this day."));
      section.append(block);
    }
  }
  return section;
}

function renderHeader() {
  const chosen = selected.map((id) => loaded[id]).filter(Boolean);
  headerEl.replaceChildren();
  if (chosen.length === 1) {
    const shul = chosen[0];
    headerEl.append(el("h1", null, shul.name));
    headerEl.append(link(`https://maps.google.com/?q=${encodeURIComponent(shul.address)}`, shul.address));
    headerEl.append(link(shul.website, shul.website.replace(/^https?:\/\/|\/$/g, "")));
  } else {
    headerEl.append(el("h1", null, "Minyan Times"));
    headerEl.append(el("p", "subtitle", "Passaic, NJ"));
  }
}

function renderSources() {
  footerEl.replaceChildren();
  for (const id of selected) {
    const shul = loaded[id];
    if (!shul) continue;
    const line = el("p");
    line.append(`${shul.name}: `, link(shul.pdf_url, "view calendar PDF"));
    if (shul.updated) {
      const updated = new Date(shul.updated).toLocaleDateString("en-US", { month: "short", day: "numeric" });
      line.append(` (updated ${updated})`);
    }
    footerEl.append(line);
    for (const note of [...shul.legend, ...shul.notes]) {
      footerEl.append(el("p", "legend", note));
    }
  }
}

function render() {
  renderHeader();
  renderSources();

  const today = todayIso();
  const dates = new Set();
  for (const id of selected) {
    for (const day of loaded[id]?.days || []) {
      if (day.date >= today) dates.add(day.date);
    }
  }

  const sorted = [...dates].sort();
  daysEl.replaceChildren(...sorted.map((date) => renderDay(date, date === today)));
  if (sorted.length === 0) {
    daysEl.append(el("p", "status", selected.length
      ? "No times posted yet for today. The shul may not have uploaded the new calendar."
      : "Tick a shul above to see its minyan times."));
  }
}

async function fetchJson(path) {
  const response = await fetch(path, { cache: "no-cache" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function loadSelected() {
  await Promise.all(selected.map(async (id) => {
    if (loaded[id]) return;
    const shul = shuls.find((s) => s.id === id);
    try {
      loaded[id] = await fetchJson(`data/${shul.file}`);
    } catch (error) {
      loaded[id] = null;
    }
  }));
}

async function start() {
  try {
    shuls = await fetchJson("data/shuls.json");
  } catch (error) {
    daysEl.replaceChildren(el("p", "status", "Couldn't load the times. Check your connection and try again."));
    return;
  }
  selected = readSelection();
  renderPicker();
  await loadSelected();
  render();
}

start();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js");
}
