"""
Turns one Agudas Yisroel monthly luach PDF into structured data.

The PDF is a calendar grid (Sunday..Shabbos columns, one row per week).
Each day's cell looks like:

    September 16 / <hebrew month> 5
    <optional Hebrew line, e.g. holiday or parsha>
    Slichos 12:50, 5:50, 6:25, 6:55, 7:40
    Shacharis 6:25, 7:00, 7:30, 8:15
    ...

Run directly to inspect what the parser sees:
    python scraper/luach_parser.py path/to/september_2026.pdf
"""

import json
import re
import sys
from datetime import date

import pdfplumber
from pdfplumber.utils import cluster_objects

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

# The Hebrew in these PDFs is typed with an old font where each Latin key
# draws a Hebrew letter, and the text is stored left-to-right (reversed).
# So "lwla" is really "alwl" -> אלול.
HEBREW_KEYS = {
    "a": "א", "b": "ב", "g": "ג", "d": "ד", "h": "ה", "w": "ו", "z": "ז",
    "j": "ח", "f": "ט", "y": "י", "k": "כ", "l": "ל", "m": "מ", "n": "נ",
    "s": "ס", "u": "ע", "p": "פ", "x": "צ", "q": "ק", "r": "ר", "c": "ש",
    "t": "ת",
    # final letters
    "\\": "ם", "}": "ן", "]": "ך", "{": "ץ",
    "[": "ף",  # not seen in a PDF yet; inferred from the pattern above
}
# Reversing the text also flips which way brackets face.
MIRRORED = {"(": ")", ")": "("}

TIME = r"\d{1,2}:\d{2}"
# Where the "value" part of a line starts: the first time, optionally
# preceded by a word like "after" or "Approx."
VALUE_START = re.compile(rf"(?:\b(?:after|approx\.?)\s+)?{TIME}", re.IGNORECASE)
DATE_LINE = re.compile(rf"^({'|'.join(MONTHS)})\s+(\d{{1,2}})\b", re.IGNORECASE)


class LuachParseError(Exception):
    pass


def decode_hebrew(text):
    unknown = {ch for ch in text if ch.isalpha() and ch not in HEBREW_KEYS}
    if unknown:
        print(f"  warning: unknown Hebrew font characters {sorted(unknown)} in {text!r}")
    return "".join(MIRRORED.get(ch, HEBREW_KEYS.get(ch, ch)) for ch in reversed(text))


def cluster_positions(values, tolerance=3):
    """Collapse nearly-equal coordinates (double-drawn borders) into one."""
    result = []
    for v in sorted(values):
        if not result or v - result[-1] > tolerance:
            result.append(v)
    return result


def classify_fonts(page):
    """
    Returns (hebrew_fonts, emphasis_fonts).
    The Hebrew font is the one that never uses capital letters or digits.
    Of the English fonts, the most-used is normal text; any others are
    bold/highlighted notices.
    """
    chars_by_font = {}
    for c in page.chars:
        chars_by_font.setdefault(c["fontname"], []).append(c["text"])

    hebrew, english = set(), {}
    for font, chars in chars_by_font.items():
        text = "".join(chars)
        if re.search(r"[a-z]", text) and not re.search(r"[A-Z0-9]", text):
            hebrew.add(font)
        else:
            english[font] = len(chars)

    if not hebrew:
        raise LuachParseError("Could not identify the Hebrew font")
    main_font = max(english, key=english.get)
    emphasis = set(english) - {main_font}
    return hebrew, emphasis


def read_lines(cell, hebrew_fonts, emphasis_fonts):
    """Groups a cell's words into lines, decoding any Hebrew along the way."""
    words = cell.extract_words(extra_attrs=["fontname"], keep_blank_chars=False)
    lines = []
    for row in cluster_objects(words, lambda w: (w["top"] + w["bottom"]) / 2, tolerance=2.5):
        row.sort(key=lambda w: w["x0"])
        parts, hebrew_run = [], []
        for w in row + [None]:
            if w is not None and w["fontname"] in hebrew_fonts:
                hebrew_run.append(w["text"])
                continue
            if hebrew_run:
                parts.append(decode_hebrew(" ".join(hebrew_run)))
                hebrew_run = []
            if w is not None:
                parts.append(w["text"])
        lines.append({
            "text": " ".join(parts),
            "all_hebrew": all(w["fontname"] in hebrew_fonts for w in row),
            "bold": any(w["fontname"] in emphasis_fonts for w in row),
        })
    return lines


def split_label(text):
    """'Mincha 1:40 pm' -> ('Mincha', '1:40 pm'). Lines without times get no value."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    match = VALUE_START.search(text)
    if not match:
        return text, ""
    return text[:match.start()].strip(), text[match.start():].strip()


def parse_cell(lines, title_month, title_year):
    match = DATE_LINE.match(lines[0]["text"])
    if not match:
        return None  # empty or non-day cell

    month = MONTHS.index(match.group(1).lower()) + 1
    year = title_year
    # A December luach can run into January, and a January luach can start in December.
    if month - title_month > 6:
        year -= 1
    elif title_month - month > 6:
        year += 1
    day = date(year, month, int(match.group(2)))

    # Rest of the date line: "/ תשרי 5" -> Hebrew day number + month name
    rest = lines[0]["text"][match.end():].replace("/", " ").split()
    heb_day = next((t for t in reversed(rest) if t.isdigit()), "")
    heb_month = " ".join(t for t in rest if not t.isdigit())

    result = {
        "date": day.isoformat(),
        "hebrew_date": f"{heb_day} {heb_month}".strip(),
        "hebrew": [],   # holiday / parsha lines, e.g. "שבת שובה"
        "items": [],    # {"label", "time", "bold"} in the order the shul lists them
    }
    for line in lines[1:]:
        if line["all_hebrew"]:
            result["hebrew"].append(line["text"])
            continue
        label, value = split_label(line["text"])
        items = result["items"]
        # A line that is only times continues the line above it (wrapped text).
        if not label and items and items[-1]["time"]:
            items[-1]["time"] += " " + value
            continue
        items.append({"label": label, "time": value, "bold": line["bold"]})
    return result


def parse_pdf(path):
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[0]
        hebrew_fonts, emphasis_fonts = classify_fonts(page)

        title = page.extract_text(layout=False) or ""
        title_match = re.search(rf"LUACH FOR ({'|'.join(MONTHS)})\s+(\d{{4}})", title, re.IGNORECASE)
        if not title_match:
            raise LuachParseError("No 'LUACH FOR <MONTH> <YEAR>' title found")
        title_month = MONTHS.index(title_match.group(1).lower()) + 1
        title_year = int(title_match.group(2))

        # Grid borders: long vertical and horizontal edges
        xs = cluster_positions(e["x0"] for e in page.edges if e["orientation"] == "v" and e["height"] > 30)
        ys = cluster_positions(e["top"] for e in page.edges if e["orientation"] == "h" and e["width"] > 30)
        if len(xs) != 8:
            raise LuachParseError(f"Expected 7 calendar columns, found {len(xs) - 1}")

        days = []
        for top, bottom in zip(ys, ys[1:]):
            for left, right in zip(xs, xs[1:]):
                # Assign text by its center point so letters that stick
                # slightly past a border still land in the right cell.
                def inside(obj, l=left, t=top, r=right, b=bottom):
                    if "text" not in obj:
                        return False
                    cx = (obj["x0"] + obj["x1"]) / 2
                    cy = (obj["top"] + obj["bottom"]) / 2
                    return l <= cx < r and t <= cy < b

                lines = read_lines(page.filter(inside), hebrew_fonts, emphasis_fonts)
                if lines:
                    day = parse_cell(lines, title_month, title_year)
                    if day:
                        days.append(day)

    if not days:
        raise LuachParseError("No day cells found")
    days.sort(key=lambda d: d["date"])
    for prev, cur in zip(days, days[1:]):
        gap = (date.fromisoformat(cur["date"]) - date.fromisoformat(prev["date"])).days
        if gap != 1:
            raise LuachParseError(f"Dates not consecutive: {prev['date']} -> {cur['date']}")

    return {"month": f"{title_year}-{title_month:02d}", "days": days}


if __name__ == "__main__":
    print(json.dumps(parse_pdf(sys.argv[1]), ensure_ascii=False, indent=2))
