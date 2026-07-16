from __future__ import annotations

import re
from typing import Iterable, Optional


LOCATION_RE = re.compile(r"(\d{2,3}\.\d{3,})\s*/\s*(\d{1,2}\.\d{3,})")
PAREN_CELL_RE = re.compile(r"\(?\s*(\d{3,}\s*-\s*\d{1,})\s*\)?")


def parse_measure_ocr(lines: Iterable[str]) -> dict:
    text = _join_lines(lines)
    network = _detect_network(text)

    return {
        "network": network,
        "location": _extract_location(text),
        "cell_id": _extract_cell_id(text, network),
        "signal": _extract_signal(text, network),
    }


def _join_lines(lines: Iterable[str]) -> str:
    return " ".join(str(line).strip() for line in lines if str(line).strip())


def _detect_network(text: str) -> str:
    if re.search(r"\bNR\s*[-:]?\s*C[IIL1]\b", text, re.IGNORECASE):
        return "NR"
    if re.search(r"\bSS\s*[-:]?\s*RSRP\b", text, re.IGNORECASE):
        return "NR"
    return "LTE"


def _extract_location(text: str) -> str:
    match = LOCATION_RE.search(text)
    if not match:
        return ""
    return f"{match.group(1)}/{match.group(2)}"


def _extract_cell_id(text: str, network: str) -> str:
    label = r"NR\s*[-:]?\s*C[IIL1]" if network == "NR" else r"E\s*C[IIL1]"
    match = re.search(
        rf"{label}\s*[:：]?\s*([0-9]{{5,}})(?P<tail>.{{0,80}})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return ""

    main_id = match.group(1)
    tail = match.group("tail")
    paren = _find_parenthetical_cell(tail)
    if paren is None:
        return main_id
    return f"{main_id}({paren})"


def _find_parenthetical_cell(text: str) -> Optional[str]:
    match = PAREN_CELL_RE.search(text)
    if not match:
        return None
    return re.sub(r"\s+", "", match.group(1))


def _extract_signal(text: str, network: str) -> Optional[int]:
    label = r"SS\s*[-:]?\s*RSRP" if network == "NR" else r"(?<!SS[-:\s])RSRP"
    match = re.search(rf"{label}\s*[:：]?\s*(-?\d{{2,4}})", text, re.IGNORECASE)
    if not match:
        return None

    value = int(match.group(1))
    return value if value < 0 else -value
