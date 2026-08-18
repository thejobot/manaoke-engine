"""TTML parser for Apple Music lyrics."""

import re
import xml.etree.ElementTree as ET


# TTML namespace
NS = {"tt": "http://www.w3.org/ns/ttml", "ttm": "http://www.w3.org/ns/ttml#metadata"}


def parse_timestamp(ts):
    """Parse TTML timestamp to milliseconds.

    Handles formats like:
        00:15.000
        00:01:15.000
        15000ms
        15.000s
    """
    if not ts:
        return 0

    if ts.endswith("ms"):
        return int(ts[:-2])
    if ts.endswith("s"):
        return int(float(ts[:-1]) * 1000)

    parts = ts.replace(",", ".").split(":")
    if len(parts) == 1:
        # Bare seconds: "11.990"
        return int(float(parts[0]) * 1000)
    elif len(parts) == 2:
        mins, secs = parts
        return int(int(mins) * 60000 + float(secs) * 1000)
    elif len(parts) == 3:
        hrs, mins, secs = parts
        return int(int(hrs) * 3600000 + int(mins) * 60000 + float(secs) * 1000)
    return 0


def ms_to_timestamp(ms, fmt="srt"):
    """Convert milliseconds to a formatted timestamp string."""
    hours = ms // 3600000
    mins = (ms % 3600000) // 60000
    secs = (ms % 60000) // 1000
    millis = ms % 1000

    if fmt == "srt":
        return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"
    elif fmt == "lrc":
        return f"{mins:02d}:{secs:02d}.{millis // 10:02d}"
    elif fmt == "vtt":
        return f"{hours:02d}:{mins:02d}:{secs:02d}.{millis:03d}"
    return f"{mins:02d}:{secs:02d}.{millis:03d}"


def _get_text(elem):
    """Recursively get all text content from an element."""
    parts = []
    if elem.text:
        parts.append(elem.text)
    for child in elem:
        parts.append(_get_text(child))
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def _get_lang(elem):
    """Get the language attribute from an element."""
    return elem.get("{http://www.w3.org/XML/1998/namespace}lang", "")


def parse_ttml(ttml_str):
    """Parse TTML string into structured lyrics data.

    Returns a list of lyric lines:
    [
        {
            "begin_ms": 15000,
            "end_ms": 20000,
            "text": "Some lyric line",
            "lang": "en",
            "translation": "translated text if available",
            "translation_lang": "ja",
            "words": [
                {"text": "Some", "begin_ms": 15000, "end_ms": 16000},
                {"text": "lyric", "begin_ms": 16000, "end_ms": 17500},
                ...
            ],
            "is_background": False,
        }
    ]
    """
    if not ttml_str or not ttml_str.strip():
        return []

    # Clean up any XML declaration issues
    ttml_str = ttml_str.strip()

    try:
        root = ET.fromstring(ttml_str)
    except ET.ParseError as e:
        raise ValueError(f"Failed to parse TTML: {e}")

    lines = []
    body = root.find(".//tt:body", NS) or root.find("body")
    if body is None:
        # Try without namespace
        body = root.find(".//{http://www.w3.org/ns/ttml}body")
    if body is None:
        return []

    # Find all <p> elements (each is a lyric line)
    for div in body.iter():
        if not div.tag.endswith("}div") and div.tag != "div":
            continue

        div_lang = _get_lang(div)

        for p in div:
            if not (p.tag.endswith("}p") or p.tag == "p"):
                continue

            begin = parse_timestamp(p.get("begin", ""))
            end = parse_timestamp(p.get("end", ""))
            text = _get_text(p).strip()
            lang = _get_lang(p) or div_lang

            if not text:
                continue

            # Check for agent/role attributes (background vocals)
            agent = p.get("{http://www.w3.org/ns/ttml#metadata}agent", "")
            role = p.get("{http://www.w3.org/ns/ttml}role", "")
            is_background = "background" in agent.lower() or "background" in role.lower()

            # Parse word/syllable spans
            words = []
            for span in p.iter():
                if not (span.tag.endswith("}span") or span.tag == "span"):
                    continue
                span_begin = parse_timestamp(span.get("begin", ""))
                span_end = parse_timestamp(span.get("end", ""))
                span_text = (span.text or "").strip()
                if span_text:
                    words.append({
                        "text": span_text,
                        "begin_ms": span_begin or begin,
                        "end_ms": span_end or end,
                    })

            line = {
                "begin_ms": begin,
                "end_ms": end,
                "text": text,
                "lang": lang,
                "translation": "",
                "translation_lang": "",
                "words": words,
                "is_background": is_background,
            }
            lines.append(line)

    # Sort by start time
    lines.sort(key=lambda x: x["begin_ms"])

    # Try to pair translations — Apple sometimes includes translated lines
    # as separate <p> elements with a different lang attribute
    _pair_translations(lines)

    return lines


def _pair_translations(lines):
    """Attempt to pair translation lines with their source lines.

    If lines with different languages share the same timestamp,
    treat the secondary language as a translation.
    """
    if not lines:
        return

    # Detect primary language (most frequent)
    lang_counts = {}
    for line in lines:
        lang = line["lang"]
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

    if not lang_counts:
        return

    primary_lang = max(lang_counts, key=lang_counts.get)

    # Find translation lines (different lang, same timestamp as a primary line)
    primary_by_time = {}
    translation_indices = set()

    for i, line in enumerate(lines):
        if line["lang"] == primary_lang or not line["lang"]:
            key = (line["begin_ms"], line["end_ms"])
            primary_by_time[key] = i

    for i, line in enumerate(lines):
        if line["lang"] and line["lang"] != primary_lang:
            key = (line["begin_ms"], line["end_ms"])
            if key in primary_by_time:
                pi = primary_by_time[key]
                lines[pi]["translation"] = line["text"]
                lines[pi]["translation_lang"] = line["lang"]
                translation_indices.add(i)

    # Remove standalone translation lines (they've been merged)
    for i in sorted(translation_indices, reverse=True):
        lines.pop(i)
