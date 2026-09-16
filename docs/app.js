// Loads data/schedule.json (built by scraper/update.py) and shows today onward.

const daysEl = document.getElementById("days");

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

function renderDay(day, isToday) {
  const section = el("section", "day");
  const date = parseDate(day.date);

  const weekday = date.getDay() === 6 ? "Shabbos" : date.toLocaleDateString("en-US", { weekday: "long" });
  const heading = el("h2");
  heading.append(weekday, el("br"),
    date.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" }));
  if (isToday) heading.append(el("span", "today-badge", "Today"));
  section.append(heading);

  const hebrew = [day.hebrew_date, ...day.hebrew].filter(Boolean).join(" · ");
  if (hebrew) {
    const p = el("p", "hebrew", hebrew);
    p.dir = "rtl";
    p.lang = "he";
    section.append(p);
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
  section.append(list);
  return section;
}

async function load() {
  let schedule;
  try {
    const response = await fetch("data/schedule.json", { cache: "no-cache" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    schedule = await response.json();
  } catch (error) {
    daysEl.replaceChildren(el("p", "status", "Couldn't load the times. Check your connection and try again."));
    return;
  }

  const today = todayIso();
  const upcoming = schedule.days.filter((day) => day.date >= today);

  daysEl.replaceChildren(...upcoming.map((day) => renderDay(day, day.date === today)));
  if (upcoming.length === 0) {
    daysEl.append(el("p", "status", "No times posted yet for today. The shul may not have uploaded the new luach."));
  }

  document.getElementById("pdf-link").href = schedule.pdf_url;
  if (schedule.updated) {
    const updated = new Date(schedule.updated).toLocaleDateString("en-US", { month: "short", day: "numeric" });
    document.getElementById("updated").textContent = `Luach last updated ${updated}`;
  }
}

load();

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("sw.js");
}
