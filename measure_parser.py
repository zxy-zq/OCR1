from __future__ import annotations

import re
from typing import Iterable, Optional


LOCATION_RE = re.compile(r"(\d{2,3}\.\d{3,})\s*/\s*(\d{1,2}\.\d{3,})")
PAREN_CELL_RE = re.compile(r"\(?\s*(\d{3,}\s*-\s*\d{1,})\s*\)?")
DIRECT_RSRP_RE = re.compile(
    r"(?:\bSS\s*[-:]?\s*)?\bRSRP\b\s*(?:\(\s*dBm\s*\))?\s*[:：]?\s*(-\s*\d{2,3})\s*(?:dBm)?",
    re.IGNORECASE,
)
DBM_VALUE_RE = re.compile(r"(?<!\d)-\s*(\d{2,3})(?!\d)")
SIGNAL_MIN_DBM = -140
SIGNAL_MAX_DBM = -40
METRIC_LABELS = {"RSSI", "RSRP", "RSRQ", "SINR", "SS", "RXLEV"}
SIGNAL_BLOCK_BOUNDARIES = (
    "1分钟前",
    "40S前",
    "20S前",
    "现在",
    "前一个",
    "邻小区",
    "当前状态",
    "缓存数据",
)


def parse_measure_ocr(lines: Iterable[str]) -> dict:
    raw_lines = _normalize_lines(lines)
    text = _join_lines(raw_lines)
    network = _detect_network(text)
    signal, _ = _extract_signal(raw_lines, network)

    return {
        "network": network,
        "location": _extract_location(text),
        "cell_id": _extract_cell_id(text, network),
        "signal": signal,
    }


def debug_parse_measure_ocr(lines: Iterable[str]) -> dict:
    raw_lines = _normalize_lines(lines)
    text = _join_lines(raw_lines)
    network = _detect_network(text)
    parsed = parse_measure_ocr(raw_lines)
    empty_reasons = {}

    if parsed["location"] == "":
        empty_reasons["location"] = "no valid longitude/latitude pattern"
    if parsed["cell_id"] == "":
        empty_reasons["cell_id"] = f"no trusted {network} cell id pattern"
    if parsed["signal"] == "":
        _, reason = _extract_signal(raw_lines, network)
        empty_reasons["signal"] = reason

    return {
        "raw_lines": raw_lines,
        "parsed": parsed,
        "empty_reasons": empty_reasons,
    }


def _normalize_lines(lines: Iterable[str]) -> list[str]:
    return [str(line).strip() for line in lines if str(line).strip()]


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


def _extract_signal(lines: list[str], network: str) -> tuple[int | str, str]:
    text = _join_lines(lines)
    direct_value = _extract_direct_rsrp_value(text)
    if direct_value is not None:
        return direct_value, "matched explicit RSRP value"

    table_value = _extract_metric_table_rsrp_value(lines, network)
    if table_value is not None:
        return table_value, "matched RSRP metric table"

    if not re.search(r"\bRSRP\b", text, re.IGNORECASE):
        return "", "RSRP keyword not found"
    return "", "RSRP keyword found, but no trusted dBm value in local context"


def _extract_direct_rsrp_value(text: str) -> Optional[int]:
    match = DIRECT_RSRP_RE.search(text)
    if not match:
        return None
    return _parse_dbm_value(match.group(1))


def _extract_metric_table_rsrp_value(lines: list[str], network: str) -> Optional[int]:
    tokens = _metric_tokens(lines)
    if not tokens:
        return None

    for start, end in _signal_search_ranges(tokens):
        value = _extract_metric_table_rsrp_value_from_tokens(tokens[start:end], network)
        if value is not None:
            return value
    return None


def _metric_tokens(lines: list[str]) -> list[str]:
    tokens = []
    for line in lines:
        tokens.extend(part for part in re.split(r"\s+", line.strip()) if part)
    return tokens


def _signal_search_ranges(tokens: list[str]) -> list[tuple[int, int]]:
    starts = [
        index + 1
        for index, token in enumerate(tokens)
        if "信号强度" in token or token.lower() in {"signal", "signals"}
    ]
    if not starts:
        starts = [0]

    ranges = []
    for start in starts:
        end = len(tokens)
        for index in range(start, len(tokens)):
            if any(boundary in tokens[index] for boundary in SIGNAL_BLOCK_BOUNDARIES):
                end = index
                break
        ranges.append((start, end))
    return ranges


def _extract_metric_table_rsrp_value_from_tokens(
    tokens: list[str], network: str
) -> Optional[int]:
    for index in range(len(tokens)):
        if not _is_metric_label(tokens[index]):
            continue

        labels = []
        cursor = index
        while cursor < len(tokens) and _is_metric_label(tokens[cursor]):
            labels.append(_canonical_metric_label(tokens[cursor]))
            cursor += 1

        if "RSRP" not in labels:
            continue

        values = []
        value_cursor = cursor
        while value_cursor < len(tokens) and len(values) < max(len(labels), 6):
            value = _parse_numeric_token(tokens[value_cursor])
            if value is None:
                break
            values.append(value)
            value_cursor += 1

        if not values:
            continue

        value = _value_from_metric_group(labels, values, network)
        if _is_valid_dbm(value):
            return int(value)
    return None


def _is_metric_label(token: str) -> bool:
    return _canonical_metric_label(token) in METRIC_LABELS


def _canonical_metric_label(token: str) -> str:
    normalized = re.sub(r"[^A-Za-z]", "", token).upper()
    if normalized == "SSRSRP":
        return "RSRP"
    return normalized


def _value_from_metric_group(
    labels: list[str], values: list[float], network: str
) -> Optional[float]:
    if network == "NR" and _looks_like_split_ss_table(labels):
        return values[0] if values else None

    try:
        rsrp_index = labels.index("RSRP")
    except ValueError:
        return None
    if rsrp_index >= len(values):
        return None
    return values[rsrp_index]


def _looks_like_split_ss_table(labels: list[str]) -> bool:
    return (
        labels.count("SS") >= 3
        and "RSRP" in labels
        and "RSRQ" in labels
        and "SINR" in labels
    )


def _parse_numeric_token(token: str) -> Optional[float]:
    normalized = token.replace("−", "-").replace("－", "-")
    normalized = re.sub(r"(?i)dbm", "", normalized)
    normalized = normalized.replace(" ", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", normalized):
        return None
    return float(normalized)


def _parse_dbm_value(value: str) -> Optional[int]:
    match = DBM_VALUE_RE.search(value)
    if not match:
        return None

    parsed = -int(match.group(1))
    if not _is_valid_dbm(parsed):
        return None
    return parsed


def _is_valid_dbm(value: Optional[float]) -> bool:
    if value is None:
        return False
    return SIGNAL_MIN_DBM <= value <= SIGNAL_MAX_DBM
