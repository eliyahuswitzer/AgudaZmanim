"""
Turns one Ahavas Israel monthly calendar PDF (ShulCloud) into structured data.

This calendar works differently from the Aguda one. Each day lists only its
unusual times and ends with "& below", e.g.

    Min: 1:22**, 5:30*, 6:50** & below

The everyday times live in footer lines under the grid:

    Sunday-Thursday Mincha Times (in addition to above): 1:30*, 2:00*, ...

so this parser merges the footer times into each day. Some footer times only
apply for part of the month ("6:20***(thru 9/25)"), which is honoured too.

The symbols mark which room the minyan is in; they're kept as-is and the
legend from the top of the calendar is returned alongside the days.

Run directly to inspect what the parser sees:
    python scraper/ahavas_parser.py path/to/calendar0926.pdf
"""

import json
import re
import sys
from datetime import date, timedelta

import pdfplumber

from parsing import MONTHS, ParseError, cluster_positions, group_lines, split_label

# The calendar abbreviates; these read better in the app.
LABELS = {
    "Shach": "Shacharis",
    "Min": "Mincha",
    "Mar": "Ma'ariv",
    "Selich": "Selichos",
    "Selich. & Shach": "Selichos & Shacharis",
    "Kab Shab": "Kabbalas Shabbos",
}
TIME = r"\d{1,2}:\d{2}"
# "1:30*", "6:20***(thru 9/25)", "7:20**(begins 9/22)"
FOOTER_TIME = re.compile(rf"({TIME}[*♦]*)\s*(?:\((thru|begins)\s+(\d{{1,2}})/(\d{{1,2}})\))?")
FOOTER_LINE = re.compile(r"^(.*?)\s*(Mincha|Ma'ariv|Ma’ariv)\s+Times\s*\(in addition to above\)\s*:\s*(.*)$",
                         re.IGNORECASE)
WEEKDAY_WORDS = {"sun": 6, "sunday": 6, "mon": 0, "monday": 0, "tues": 1, "tuesday": 1,
                 "wed": 2, "wednesday": 2, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
                 "shabbos": 5, "sat": 5}
LEGEND_LINE = re.compile(r"^\s*([*♦]+)\s*=\s*(.+)$")


def tidy(text):
    """Use plain apostrophes so labels match between the grid and the footer."""
    return text.replace("’", "'").strip()


def weekday_numbers(text):
    """'Sunday-Thursday' or 'Sun.-Thurs.' -> the weekday numbers it covers (Monday=0)."""
    words = [w for w in re.split(r"[^A-Za-z]+", text.lower()) if w in WEEKDAY_WORDS]
    if not words:
        return set()
    if len(words) == 1:
        return {WEEKDAY_WORDS[words[0]]}
    # Calendar weeks run Sunday..Shabbos, so walk the range in that order.
    order = [6, 0, 1, 2, 3, 4, 5]
    start, end = order.index(WEEKDAY_WORDS[words[0]]), order.index(WEEKDAY_WORDS[words[-1]])
    return set(order[start:end + 1])


def sort_key_evening(time_text):
    """Orders afternoon/evening times: 1:30 comes after 12:30 and before 5:00."""
    hour, minute = (int(part) for part in re.match(TIME, time_text).group().split(":"))
    return ((hour + 12 if hour < 12 else hour) * 60 + minute, time_text)


def parse_footer(lines, year, month):
    """Footer rules: which extra times apply on which weekdays and dates."""
    rules, notes = [], []
    for line in lines:
        match = FOOTER_LINE.match(line)
        if not match:
            if line.strip():
                notes.append(line.strip())
            continue
        days = weekday_numbers(match.group(1))
        label = "Mincha" if match.group(2).lower() == "mincha" else "Ma'ariv"
        times = []
        for time_text, qualifier, qual_month, qual_day in FOOTER_TIME.findall(match.group(3)):
            limit = None
            if qualifier:
                limit_year = year + 1 if int(qual_month) < month - 6 else year
                limit = (qualifier.lower(), date(limit_year, int(qual_month), int(qual_day)))
            times.append((time_text, limit))
        rules.append({"days": days, "label": label, "times": times})
    return rules, notes


def apply_footer(day, day_date, rules):
    """Replaces '& below' with the extra times that apply on this date."""
    for item in day["items"]:
        if "below" not in item["time"]:
            continue
        item["time"] = re.sub(r"\s*&\s*below\s*", "", item["time"]).strip().rstrip(",")
        extra = []
        for rule in rules:
            if rule["label"] != item["label"] or day_date.weekday() not in rule["days"]:
                continue
            for time_text, limit in rule["times"]:
                if limit and ((limit[0] == "thru" and day_date > limit[1])
                              or (limit[0] == "begins" and day_date < limit[1])):
                    continue
                extra.append(time_text)
        if extra:
            times = [t for t in re.split(r",\s*", item["time"]) if t] + extra
            item["time"] = ", ".join(sorted(times, key=sort_key_evening))


def column_of(word, columns):
    """Which day column (0 = Sunday) a word sits in."""
    return max(i for i, x in enumerate(columns[:-1]) if word["x0"] >= x - 2)


def first_cell_date(day_number, column, month, year):
    """
    Works out the date of the first day cell. A calendar titled September can
    still open in late August, so try the neighbouring months too and keep the
    one landing on the weekday its column stands for (column 0 = Sunday).
    """
    for month_offset in (0, -1, 1):
        candidate_month, candidate_year = month + month_offset, year
        if candidate_month < 1:
            candidate_month, candidate_year = 12, year - 1
        elif candidate_month > 12:
            candidate_month, candidate_year = 1, year + 1
        try:
            candidate = date(candidate_year, candidate_month, day_number)
        except ValueError:
            continue
        if (candidate.weekday() + 1) % 7 == column:
            return candidate
    raise ParseError(f"Could not date the first cell (day {day_number}, column {column})")


def grid_columns(page):
    """
    The 8 vertical rules that divide the 7 day columns. Column borders are
    redrawn on every week row, so they add up to far more ink than the one-off
    dividers inside the information block at the top.
    """
    edges = [e for e in page.edges if e["orientation"] == "v" and e["height"] > 40]
    totals = {x: sum(e["height"] for e in edges if abs(e["x0"] - x) <= 3)
              for x in cluster_positions(e["x0"] for e in edges)}
    columns = sorted(x for x, height in totals.items() if height > max(totals.values()) * 0.25)
    if len(columns) != 8:
        raise ParseError(f"Expected 7 calendar columns, found {len(columns) - 1}")
    return columns


def parse_pdf(path):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]

        title = page.extract_text() or ""
        title_match = re.search(rf"({'|'.join(MONTHS)})\s+(\d{{4}})", title, re.IGNORECASE)
        if not title_match:
            raise ParseError("No '<Month> <Year>' title found")
        month = MONTHS.index(title_match.group(1).lower()) + 1
        year = int(title_match.group(2))

        columns = grid_columns(page)

        # The footer notes sit under a full-width rule; nothing below it belongs to a day.
        full_width = [e["top"] for e in page.edges if e["orientation"] == "h" and e["width"] > 600]
        grid_bottom = min(full_width) if full_width else page.height
        words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
        # Each day cell is anchored by its civil date: a bold Times number from 1 to 31
        # sitting in a calendar column. (The year in the title is bold Times too, but
        # is out of that range.)
        anchors = sorted((w for w in words
                          if "TimesNewRomanPS-Bold" in w["fontname"] and w["text"].isdigit()
                          and 1 <= int(w["text"]) <= 31
                          and columns[0] - 2 <= w["x0"] < columns[-1]
                          and w["top"] < grid_bottom),
                         key=lambda w: (w["top"], w["x0"]))
        if not anchors:
            raise ParseError("No day numbers found")

        # The room-marker legend sits in the information block, left of the first
        # week's day cells and above the first full week row.
        # The information block at the top is split into its own sub-columns, and the
        # room-marker legend is the leftmost one.
        first_full_row = min(a["top"] for a in anchors if a["x0"] < columns[1])
        dividers = [e["x0"] for e in page.edges
                    if e["orientation"] == "v" and e["top"] < first_full_row - 4
                    and e["height"] > 40 and e["x0"] > columns[0] + 10]
        legend_right = min(dividers) if dividers else columns[1]
        legend_block = [tidy(line) for line in group_lines(
            [w for w in words if w["top"] < first_full_row - 4 and w["x0"] < legend_right])
            if line.strip()]
        start = next((i for i, line in enumerate(legend_block)
                      if LEGEND_LINE.search(line) or "davening locations" in line.lower()), None)
        legend = legend_block[start:] if start is not None else []

        first_date = first_cell_date(int(anchors[0]["text"]), column_of(anchors[0], columns), month, year)

        days = []
        for index, anchor in enumerate(anchors):
            column = column_of(anchor, columns)
            left, right = columns[column], columns[column + 1]
            # The cell runs down to the next day in the same column.
            below = [a["top"] for a in anchors[index + 1:]
                     if left - 2 <= a["x0"] < right and a["top"] > anchor["top"] + 5]
            # Stop just short of the next day, whose Hebrew date prints a shade
            # higher than its date number.
            bottom = min(below) - 4 if below else grid_bottom

            in_cell = [w for w in words
                       if left - 2 <= (w["x0"] + w["x1"]) / 2 < right
                       and anchor["top"] - 2 <= w["top"] < bottom]
            header = [w for w in in_cell if abs(w["top"] - anchor["top"]) < 4 and w is not anchor]
            body = [w for w in in_cell
                    if w["top"] >= anchor["top"] + 4 and "TimesNewRoman" not in w["fontname"]]
            # Daf yomi and similar sit under the date in Times; keep them as a note.
            extras = [w for w in in_cell
                      if w["top"] >= anchor["top"] + 4 and "TimesNewRoman" in w["fontname"]]

            # Cells are consecutive days, so counting from the first one also
            # checks that no cell was missed: the printed number must still match.
            day_date = first_date + timedelta(days=index)
            if day_date.day != int(anchor["text"]) or (day_date.weekday() + 1) % 7 != column:
                raise ParseError(f"Day cell {anchor['text']} doesn't line up with {day_date}")

            day = {
                "date": day_date.isoformat(),
                "hebrew_date": tidy(" ".join(w["text"] for w in sorted(header, key=lambda w: w["x0"]))),
                "titles": [],   # parsha or Yom Tov, printed in bold under the date
                "items": [],
            }
            for line, is_bold in group_lines(body, with_bold=True):
                label, value = split_label(tidy(line), keep_colon_labels=True)
                label = LABELS.get(label.rstrip("."), label)
                items = day["items"]
                if is_bold and not value and not items:
                    day["titles"].append(label)
                    continue
                # A long list can wrap so that "& below" lands on its own line.
                if not value and label.strip("& ").lower() == "below" and items:
                    items[-1]["time"] = items[-1]["time"].rstrip() + " below"
                    continue
                # A line that is only times continues the line above it (wrapped text).
                if not label and items and items[-1]["time"]:
                    items[-1]["time"] += " " + value
                    continue
                items.append({"label": label, "time": value, "bold": is_bold})
            for line in group_lines(extras):
                day["items"].append({"label": tidy(line), "time": "", "bold": False})
            days.append(day)

        footer_lines = group_lines([w for w in words if w["top"] >= grid_bottom])
        rules, notes = parse_footer(footer_lines, year, month)
        for day in days:
            apply_footer(day, date.fromisoformat(day["date"]), rules)

    days.sort(key=lambda d: d["date"])
    for previous, current in zip(days, days[1:]):
        gap = (date.fromisoformat(current["date"]) - date.fromisoformat(previous["date"])).days
        if gap != 1:
            raise ParseError(f"Dates not consecutive: {previous['date']} -> {current['date']}")

    return {"month": f"{year}-{month:02d}", "days": days, "legend": legend, "notes": notes}


if __name__ == "__main__":
    print(json.dumps(parse_pdf(sys.argv[1]), ensure_ascii=False, indent=2))
