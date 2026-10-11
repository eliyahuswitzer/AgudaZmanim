"""
Parses BMZE's yearly Shabbos schedule PDF (e.g. BMZE_5787.pdf).

Unlike the other shuls' monthly calendars, this is one table for the whole
year. Each row is a week, dated by its Friday, with columns (right to left):
parsha, date, Friday candle lighting/Mincha, Shabbos Shacharis, Sof Zman
Krias Shma, Mincha, Shkia, Ma'ariv, and Sunday's Mincha/Ma'ariv.
Below it is a small table of fixed weekday times, which are filled in for
every weekday except Yom Tov, Chol Hamoed and Tisha B'Av.

    python scraper/bmze_parser.py path/to/BMZE_5787.pdf
"""

import json
import re
import sys
from datetime import date, timedelta

import pdfplumber
from pyluach import dates

from parsing import ParseError

TIME = re.compile(r"^\d{1,2}:\d{2}$")
ROW_DATE = re.compile(r"^\((\d{2})/(\d{2})\)$")
# Friday's cell: "6:07", or in summer "6:09¹ / 7:33" (early Mincha / regular)
FRIDAY = re.compile(r"^(\d{1,2}:\d{2})¹?(?:\s*/\s*(\d{1,2}:\d{2}))?$")

# From the "WEEKDAY" box under the table
SHACHARIS_SUNDAY = "8:30"
SHACHARIS_WEEKDAY = "7:00"
SHACHARIS_ROSH_CHODESH = "6:50"
MAARIV_MON_THU = "9:00"

# pyluach holiday names on which the regular weekday times don't apply
NO_WEEKDAY_TIMES = {"Rosh Hashana", "Yom Kippur", "Succos", "Shmini Atzeres",
                    "Simchas Torah", "Pesach", "Shavuos", "9 of Av"}

LEGEND = [
    "Early Mincha: candle lighting not before plag (15 minutes after Mincha)",
    "Weekday times are the shul's regular schedule. Check with the shul around "
    "Yom Tov, fast days and Purim.",
]


def hebrew(day):
    return dates.GregorianDate(day.year, day.month, day.day).to_heb()


def is_rosh_chodesh(day):
    h = hebrew(day)
    return h.day == 30 or (h.day == 1 and h.month != 7)  # 1 Tishrei is Rosh Hashana


def no_weekday_times(day):
    return hebrew(day).holiday() in NO_WEEKDAY_TIMES


def first_year(month, day):
    """The table doesn't print the year; find the one where this date is a Friday."""
    today = date.today()
    years = [y for y in (today.year - 1, today.year, today.year + 1)
             if date(y, month, day).weekday() == 4]
    if len(years) != 1:
        raise ParseError(f"Can't tell which year {month}/{day} is in")
    return years[0]


def parse_rows(pdf_path):
    """One entry per printed week: its Friday and the times on that row."""
    rows = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            words = page.extract_words()
            for anchor in [w for w in words if ROW_DATE.match(w["text"])]:
                middle = (anchor["top"] + anchor["bottom"]) / 2
                row = sorted((w for w in words if abs((w["top"] + w["bottom"]) / 2 - middle) < 7),
                             key=lambda w: w["x0"])
                left = [w["text"] for w in row if w["x1"] <= anchor["x0"]]
                right = [w["text"] for w in row if w["x0"] >= anchor["x1"]]

                times, friday_text = left[:6], " ".join(left[6:])
                friday = FRIDAY.match(friday_text)
                if not all(TIME.match(t) for t in times) or not friday:
                    raise ParseError(f"Row {anchor['text']} doesn't look right: {' '.join(left)}")

                # Hebrew comes out in visual order (backwards); markers are mixed in.
                right_text = " ".join(right)
                parsha = " ".join(right_text.replace("*", " ").replace("$", " ").split())[::-1]
                rows.append({
                    "month_day": tuple(int(n) for n in ROW_DATE.match(anchor["text"]).groups()),
                    "sunday": times[0],
                    "maariv": times[1],
                    "shkia": times[2],
                    "mincha": times[3],
                    "krias_shma": times[4],
                    "shacharis": times[5],
                    "friday": friday.group(2) or friday.group(1),
                    "early_mincha": friday.group(1) if friday.group(2) else None,
                    "parsha": parsha,
                    "mevarchim": "*" in right_text,
                    "rosh_chodesh": "$" in right_text,
                })
    if not rows:
        raise ParseError("No weeks found")
    return rows


def parse_pdf(pdf_path):
    rows = parse_rows(pdf_path)

    # Give each row its full date. The year rolls over when the month goes back.
    year = first_year(*rows[0]["month_day"])
    previous = None
    for row in rows:
        friday = date(year, *row["month_day"])
        if previous and friday < previous:
            year += 1
            friday = date(year, *row["month_day"])
        if friday.weekday() != 4 or (previous and (friday - previous).days not in (7, 14)):
            raise ParseError(f"Week of {friday} doesn't follow {previous}")
        row["date"] = friday
        previous = friday

    items = {}  # date -> list of items

    def add(day, label, time, bold=False):
        items.setdefault(day, []).append({"label": label, "time": time, "bold": bold})

    # Regular weekday times first, so they come before the table's times each day.
    first, last = rows[0]["date"], rows[-1]["date"] + timedelta(days=2)
    day = first
    while day <= last:
        if day.weekday() != 5 and not no_weekday_times(day):
            if day.weekday() == 6:
                add(day, "Shacharis", SHACHARIS_SUNDAY)
            else:
                add(day, "Shacharis", SHACHARIS_ROSH_CHODESH if is_rosh_chodesh(day) else SHACHARIS_WEEKDAY)
        day += timedelta(days=1)

    for row in rows:
        friday, shabbos, sunday = (row["date"] + timedelta(days=n) for n in range(3))
        if row["early_mincha"]:
            add(friday, "Early Mincha", row["early_mincha"])
        add(friday, "Candle Lighting/Mincha", row["friday"])
        add(shabbos, "Shacharis", row["shacharis"])
        add(shabbos, "Sof Zman Krias Shma", row["krias_shma"])
        add(shabbos, "Mincha", row["mincha"])
        add(shabbos, "Shkia", row["shkia"])
        add(shabbos, "Ma'ariv", row["maariv"])
        if row["mevarchim"]:
            add(shabbos, "Shabbos Mevarchim", "")
        if row["rosh_chodesh"]:
            add(shabbos, "Rosh Chodesh", "")
        if not no_weekday_times(sunday):
            add(sunday, "Mincha/Ma'ariv", row["sunday"])

    # Ma'ariv Monday to Thursday, but not going into Yom Tov or Tisha B'Av
    day = first
    while day <= last:
        if (day.weekday() < 4 and not no_weekday_times(day)
                and not no_weekday_times(day + timedelta(days=1))):
            add(day, "Ma'ariv", MAARIV_MON_THU)
        day += timedelta(days=1)

    parshas = {row["date"] + timedelta(days=1): row["parsha"] for row in rows}
    days = []
    for day in sorted(items):
        h = hebrew(day)
        days.append({
            "date": day.isoformat(),
            "hebrew_date": f"{h.day} {h.month_name(hebrew=True)}",
            "titles": [parshas[day]] if day in parshas else [],
            "items": items[day],
        })

    return {"month": f"{first.year}-{first.month:02d}", "days": days, "legend": LEGEND, "notes": []}


if __name__ == "__main__":
    print(json.dumps(parse_pdf(sys.argv[1]), ensure_ascii=False, indent=2))
