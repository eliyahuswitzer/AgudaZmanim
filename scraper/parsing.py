"""Bits shared by the per-shul calendar parsers."""

import re

from pdfplumber.utils import cluster_objects

MONTHS = ["january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december"]

TIME = r"\d{1,2}:\d{2}"
# Where the "value" part of a line starts: the first time, optionally
# preceded by a word like "after" or "Approx."
VALUE_START = re.compile(rf"(?:\b(?:after|approx\.?)\s+)?{TIME}", re.IGNORECASE)
# "Shacharis: 7:00" — a label ending in a colon. The character before the colon
# must not be a digit, so the colon inside a time ("after 7:26") isn't mistaken
# for a label separator.
COLON_LABEL = re.compile(r"^([^:\d][^:]{0,38}[^:\d\s]):\s*(.*)$")


class ParseError(Exception):
    pass


def cluster_positions(values, tolerance=3):
    """Collapse nearly-equal coordinates (double-drawn borders) into one."""
    result = []
    for value in sorted(values):
        if not result or value - result[-1] > tolerance:
            result.append(value)
    return result


def group_lines(words, with_bold=False):
    """
    Groups words into printed lines, left to right. A change of font starts a
    new word even mid-number ("7", ":", "26"), so words that touch are joined
    without a space.
    """
    lines = []
    for row in cluster_objects(words, lambda w: (w["top"] + w["bottom"]) / 2, tolerance=2.5):
        row.sort(key=lambda w: w["x0"])
        text = ""
        for previous, word in zip([None] + row, row):
            if previous is not None and word["x0"] - previous["x1"] > 1.2:
                text += " "
            text += word["text"]
        if with_bold:
            lines.append((text, any("Bold" in w.get("fontname", "") for w in row)))
        else:
            lines.append(text)
    return lines


def split_label(text, keep_colon_labels=False):
    """'Mincha 1:40 pm' -> ('Mincha', '1:40 pm'). Lines without times get no value."""
    text = text.strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    if keep_colon_labels:
        colon = COLON_LABEL.match(text)
        if colon:
            return colon.group(1).strip(), colon.group(2).strip()
    match = VALUE_START.search(text)
    if not match:
        return text, ""
    return text[:match.start()].strip(), text[match.start():].strip()
