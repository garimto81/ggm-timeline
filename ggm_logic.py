from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from typing import Any, Dict, Iterable, List, Tuple, Union
from decimal import Decimal, ROUND_HALF_UP

import ggm_logic_csv

# ----------------------------------------------------------------------
# Event model
# ----------------------------------------------------------------------


@dataclass
class Event:
    """
    Single timeline event produced from spreadsheet rows.

    time_sec : absolute seconds-of-day (daily diff applied)
    kind     : "GTO-W" / "MysteryHands" / "BlindsUp" / "BreakSkip" / spacer
    bcode    : vMix / Companion button code (or None for spacer)
    label    : short label for UI
    meta     : optional extra info (sheet/row/seat etc)
    """

    time_sec: float
    kind: str
    bcode: int | None
    label: str
    meta: Dict[str, Any] = field(default_factory=dict)


# ----------------------------------------------------------------------
# Utilities
# ----------------------------------------------------------------------


HEADER_KEYS = [
    "CommandType",
    "SeatIndex",
    "ActionStart",
    "ActionEnd",
    "Action",
    "Text1",
    "Text2",
    "Text3",
    "Value1",
    "Value2",
    "Value3",
    "SheetName",
    "Row",
]

KEY_ALIASES = {
    "CommandType": ("CommandType", "command_type", "commandtype"),
    "SeatIndex": ("SeatIndex", "seat_index", "seatindex"),
    "ActionStart": ("ActionStart", "action_start", "actionstart"),
    "ActionEnd": ("ActionEnd", "action_end", "actionend"),
    "Action": ("Action", "action"),
    "Text1": ("Text1", "text1"),
    "Text2": ("Text2", "text2"),
    "Text3": ("Text3", "text3"),
    "Value1": ("Value1", "value1"),
    "Value2": ("Value2", "value2"),
    "Value3": ("Value3", "value3"),
    "SheetName": ("SheetName", "sheet_name", "sheetname"),
    "Row": ("Row", "row"),
    "Hand": ("Hand", "hand"),
}


def _parse_time_to_sec(ts: Any, daily_diff_seconds: int) -> float:
    """
    Convert timestamp to absolute seconds-of-day (float) then apply daily_diff_seconds.
    - Accepts datetime/int/float(str) (numeric is epoch -> localtime-of-day).
    - Parses strings with microseconds/ISO/T/Z/offset variants.
    - Returns 0 on failure.
    """
    if ts is None:
        return 0

    def _dt_to_daysec(dt: datetime) -> float:
        base = dt.hour * 3600 + dt.minute * 60 + dt.second
        base += dt.microsecond / 1_000_000
        return base

    dt: datetime | None = None

    # Numeric epoch -> local datetime
    if isinstance(ts, (int, float)):
        val = float(ts)
        if val > 1e12:  # looks like milliseconds
            val = val / 1000.0
        try:
            dt = datetime.fromtimestamp(val)
        except Exception:
            return 0

    elif isinstance(ts, datetime):
        dt = ts
    else:
        s = str(ts).strip()
        if not s:
            return 0

        fmts = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y/%m/%d %H:%M:%S.%f",
            "%Y/%m/%d %H:%M:%S",
            "%Y.%m.%d %H:%M:%S.%f",
            "%Y.%m.%d %H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
        ]

        for fmt in fmts:
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue

        if dt is None and s.endswith("Z"):
            for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
        # Try fromtimestamp if looks like epoch string
        if dt is None:
            try:
                val = float(s)
                if val > 1e12:
                    val = val / 1000.0
                dt = datetime.fromtimestamp(val)
            except Exception:
                dt = None

    if dt is None:
        return 0

    sec = _dt_to_daysec(dt)
    sec = sec + int(daily_diff_seconds or 0)
    sec = _quantize(sec)
    # keep within a day range for comparison with replay timecodes
    if sec < 0:
        sec = 0
    return sec


def _normalize_row(raw: Any) -> Dict[str, str]:
    """
    Normalize GAS row payload (dict/list) into a canonical dict with header keys.
    - dict: accepts both CamelCase and snake_case keys
    - list/tuple: fallback mapping by index (legacy)
    """
    row: Dict[str, str] = {}

    # dict format (current GAS result)
    if isinstance(raw, dict):
        # Delete 플래그 보존
        delete_val = raw.get("Delete")
        if delete_val is True or delete_val == "true" or delete_val == 1 or delete_val == "1":
            row["_delete"] = True

        for target, aliases in KEY_ALIASES.items():
            val = ""
            for key in aliases:
                if key in raw:
                    v = raw.get(key, "")
                    if v is None:
                        val = ""
                    elif isinstance(v, datetime):
                        val = v.strftime("%Y-%m-%d %H:%M:%S.%f")
                    else:
                        val = str(v).strip()
                    break
            row[target] = val
        return row

    # list/tuple format (very old backup format)
    if isinstance(raw, (list, tuple)):
        def _get(i: int) -> str:
            try:
                v = raw[i]
            except IndexError:
                return ""
            if v is None:
                return ""
            return str(v).strip()

        mapping = {
            "CommandType": _get(0),
            "SeatIndex": _get(1),
            "ActionStart": _get(2),
            "ActionEnd": _get(3),
            "Action": _get(4),
            "Text1": _get(4),
            "Text2": _get(5),
            "Text3": _get(6),
            "Value1": _get(7),
            "Value2": _get(8),
            "Value3": _get(9),
            "SheetName": "",
            "Row": "",
        }
        return mapping

    # Fallback: unknown shape -> empty row
    return {k: "" for k in HEADER_KEYS}


def _is_empty_row(row: Dict[str, str]) -> bool:
    """True if the row is effectively empty for our purposes."""
    return not any(
        row.get(k, "")
        for k in (
            "CommandType",
            "SeatIndex",
            "ActionStart",
            "ActionEnd",
            "Action",
            "Text1",
            "Text2",
            "Value1",
            "Value2",
        )
    )


# ----------------------------------------------------------------------
# GTO-W block -> events
# ----------------------------------------------------------------------


def _build_events_gtow_block(
    block_rows: List[Dict[str, str]], daily_diff_seconds: int, block_index: int | None = None
) -> List[Event]:
    """
    Convert a single GTO-W block (rows) into Event list.

    Rules (summary):
      - First seat seen -> Hero, second -> Villain (HU assumption)
      - Timeline order by ActionStart -> BCode:
          Hero first  -> 2
          Villain first -> 4
          Next:
            prev=Villain, cur=Hero -> 5
            prev=Hero, cur=Hero    -> 7
            prev=Hero, cur=Villain -> 6
            prev=Villain, cur=Villain -> 6
      - Block end: ActionEnd of last row
          last actor Hero   -> BCode 8
          last actor Villain -> BCode 17
    """
    # sort rows with valid ActionStart
    rows_with_ts: List[Tuple[int, Dict[str, str]]] = []
    for r in block_rows:
        ts = r.get("ActionStart", "")
        sec = _parse_time_to_sec(ts, daily_diff_seconds)
        if sec <= 0:
            continue
        rows_with_ts.append((sec, r))

    if not rows_with_ts:
        return []

    rows_with_ts.sort(key=lambda x: x[0])

    # Hero / Villain seat detection
    hero_seat = None
    villain_seat = None
    seen_seats = []
    for _, r in rows_with_ts:
        s = r.get("SeatIndex", "").strip()
        if not s:
            continue
        if s not in seen_seats:
            seen_seats.append(s)
    if seen_seats:
        hero_seat = seen_seats[0]
    if len(seen_seats) >= 2:
        villain_seat = seen_seats[1]

    def actor_of(seat: str) -> str:
        if hero_seat is not None and seat == hero_seat:
            return "Hero"
        if villain_seat is not None and seat == villain_seat:
            return "Villain"
        return "Hero"

    events: List[Event] = []

    first_hero_done = False
    first_villain_done = False
    prev_actor: str | None = None
    last_actor: str | None = None
    last_row_for_end: Dict[str, str] | None = None

    for sec, r in rows_with_ts:
        seat = r.get("SeatIndex", "").strip()
        seat_mapped = seat
        try:
            if seat.isdigit():
                seat_mapped = str(ggm_logic_csv.map_seatindex_to_table(int(seat)))
        except Exception:
            seat_mapped = seat
        actor = actor_of(seat)
        last_actor = actor
        last_row_for_end = r

        if actor == "Hero" and not first_hero_done:
            bcode = 2
            first_hero_done = True
        elif actor == "Villain" and not first_villain_done:
            bcode = 4
            first_villain_done = True
        else:
            if actor == "Hero" and prev_actor == "Villain":
                bcode = 5
            elif actor == "Hero" and prev_actor == "Hero":
                bcode = 7
            elif actor == "Villain" and prev_actor == "Hero":
                bcode = 6
            else:
                bcode = 6

        prev_tag = prev_actor or "Start"
        label_short = f"{actor[0]}_After_{prev_tag[0] if prev_tag else 'S'}"
        label = f"{label_short} Seat {seat_mapped}"

        events.append(
            Event(
                time_sec=sec,
                kind="GTO-W",
                bcode=bcode,
                label=label,
                meta={
                    "block_index": block_index,
                    "seat": seat,
                    "seat_mapped": seat_mapped,
                    "actor": actor,
                    "label_short": label_short,
                    "sheet": r.get("SheetName", ""),
                    "row": r.get("Row", ""),
                },
            )
        )

        prev_actor = actor

    # Add end-of-hand BCode (8 / 17)
    if last_row_for_end and last_actor:
        ae_ts = last_row_for_end.get("ActionEnd", "")
        end_sec = _parse_time_to_sec(ae_ts, daily_diff_seconds)
        if end_sec > 0:
            end_bcode = 8 if last_actor == "Hero" else 17
            events.append(
                Event(
                    time_sec=end_sec,
                    kind="GTO-W",
                    bcode=end_bcode,
                    label=f"GTO-W End ({last_actor})",
                    meta={
                        "block_index": block_index,
                        "sheet": last_row_for_end.get("SheetName", ""),
                        "row": last_row_for_end.get("Row", ""),
                    },
                )
            )

    return events


# ----------------------------------------------------------------------
# MysteryHands block -> events
# ----------------------------------------------------------------------


def _build_events_mh_block(block_rows: List[Dict[str, str]], daily_diff_seconds: int) -> List[Event]:
    """
    Convert a MysteryHands block into events.

    Rules:
      - SeatIndex startswith 'Shuffle' OR Action startswith 'Shuffle' -> ActionStart BCode 22
      - SeatIndex is digit and Action startswith 'Fold' -> ActionStart BCode 23 per seat (fold order preserved)
      - SeatIndex/Action contains 'Showdown/End' -> ActionStart (fallback ActionEnd) BCode 24
      - Pre-shuffle: if survivors (players - folds) > 0, send that many BCode 23 events spaced 1s before shuffle (open overlays for non-folders)
    """

    def _extract_seats(text: str) -> List[str]:
        if not text:
            return []
        # unify separators
        for ch in ["/", ";"]:
            text = text.replace(ch, ",")
        return [tok for tok in text.replace(" ", "").split(",") if tok.isdigit()]

    shuffle_sec = 0.0
    showdown_sec = 0.0
    fold_events: List[Event] = []
    players_count = None
    open_seat_raw: str | None = None
    open_seat_mapped: str | None = None
    fold_seats: List[str] = []
    showdown_seats: List[str] = []
    timeline_seats: List[str] = []

    def _map_seat(seat_str: str) -> str | None:
        try:
            if seat_str and seat_str.isdigit():
                mapped = ggm_logic_csv.map_seatindex_to_table(int(seat_str))
                if mapped is None or mapped > 9:
                    return None
                return str(mapped)
        except Exception:
            return None
        return None

    for r in block_rows:
        seat = (r.get("SeatIndex") or "").strip()
        action = (r.get("Action") or "").strip()
        ct = (r.get("CommandType") or "").strip()

        # header info
        if "players" in seat.lower():
            try:
                players_count = int("".join(ch for ch in seat if ch.isdigit()))
            except Exception:
                players_count = None
            continue
        if "open seat" in seat.lower():
            try:
                open_seat_raw = "".join(ch for ch in seat if ch.isdigit())
                open_seat_mapped = _map_seat(open_seat_raw)
            except Exception:
                open_seat_raw = None
                open_seat_mapped = None
            continue

        # Shuffle
        if seat.lower().startswith("shuffle") or action.lower().startswith("shuffle"):
            sec = _parse_time_to_sec(r.get("ActionStart", ""), daily_diff_seconds)
            if sec > 0 and shuffle_sec == 0:
                shuffle_sec = sec

        # Showdown/End
        seat_lower = seat.lower()
        if "showdown" in seat_lower or "showdown/end" in seat_lower or "end" in seat_lower or "showdown" in action.lower():
            seats_here = _extract_seats(action) or _extract_seats(seat)
            mapped_seats = []
            for s in seats_here:
                try:
                    sr = int(s)
                except Exception:
                    continue
                ms = _map_seat(str(sr))
                if ms:
                    mapped_seats.append(ms)
            showdown_seats.extend(mapped_seats)
            sec = _parse_time_to_sec(r.get("ActionStart", ""), daily_diff_seconds)
            if sec <= 0:
                sec = _parse_time_to_sec(r.get("ActionEnd", ""), daily_diff_seconds)
            if sec > 0 and showdown_sec == 0:
                showdown_sec = sec

        # Fold (SeatIndex is digit)
        if seat.isdigit() and action.lower().startswith("fold"):
            sec = _parse_time_to_sec(r.get("ActionStart", ""), daily_diff_seconds)
            if sec > 0:
                seat_mapped = _map_seat(seat)
                if seat_mapped:
                    fold_seats.append(seat_mapped)
                    timeline_seats.append(seat_mapped)
                fold_events.append(
                    Event(
                        time_sec=sec,
                        kind="MysteryHands",
                        bcode=23,
                        label=f"MH Fold seat {seat_mapped}",
                        meta={
                            "seat": seat_mapped,
                            "seat_mapped": seat_mapped,
                            "sheet": r.get("SheetName", ""),
                            "row": r.get("Row", ""),
                            "players": players_count,
                            "open_seat": open_seat_mapped,
                        },
                    )
                )

    events: List[Event] = []

    # Build missing/eliminated seats list (players not appearing in fold/showdown/open)
    total_players = players_count if players_count is not None and players_count > 0 else 9
    total_players = min(total_players, 9)  # MH는 9인 고정 (10번 없음)
    all_mapped = [v for v in (_map_seat(str(i)) for i in range(1, total_players + 1)) if v]
    present = set(
        [s for s in timeline_seats if s]
        + [s for s in showdown_seats if s]
        + ([open_seat_mapped] if open_seat_mapped else [])
    )
    missing_seats = [s for s in all_mapped if s not in present]

    # Build MH seat sequence for sheet write:
    #   1) open seat + missing (overlay off at start)
    #   2) fold order
    #   3) showdown seats (villain etc.) at the end
    mh_sequence: List[str] = []
    if open_seat_mapped is not None:
        mh_sequence.append(str(open_seat_mapped))
    for s in missing_seats:
        if s not in mh_sequence:
            mh_sequence.append(s)
    for s in timeline_seats:
        if s not in mh_sequence:
            mh_sequence.append(s)
    for s in showdown_seats:
        if s not in mh_sequence:
            mh_sequence.append(s)

    # Add mh_sequence trigger event right before shuffle
    if mh_sequence and shuffle_sec > 0:
        events.append(
            Event(
                time_sec=shuffle_sec - 0.5,
                kind="mh_sequence",
                bcode=None,
                label="MH sequence send",
                meta={"mh_sequence": mh_sequence},
            )
        )

    # Pre-shuffle overlay removals for open/missing seats (1s interval)
    if shuffle_sec > 0:
        cnt_open = 1 if open_seat_mapped is not None else 0
        seq_pre = mh_sequence[: cnt_open + len(missing_seats)]
        for idx, seat in enumerate(seq_pre):
            t = shuffle_sec - (len(seq_pre) - idx)
            if t <= 0:
                t = shuffle_sec - 0.1 * (len(seq_pre) - idx)
            events.append(
                Event(
                    time_sec=_quantize(t),
                    kind="MysteryHands",
                    bcode=23,
                    label=f"MH Pre-open seat {seat}",
                    meta={
                        "seat": seat,
                        "seat_mapped": seat,
                        "sheet": "",
                        "row": "",
                        "players": players_count,
                        "open_seat": open_seat_mapped,
                    },
                )
            )

    if shuffle_sec > 0:
        events.append(
            Event(
                time_sec=shuffle_sec,
                kind="MysteryHands",
                bcode=22,
                label="MH Start (Shuffle)",
                meta={"mh_sequence": mh_sequence},
            )
        )

    events.extend(sorted(fold_events, key=lambda e: e.time_sec))

    if showdown_sec > 0:
        events.append(
            Event(
                time_sec=showdown_sec,
                kind="MysteryHands",
                bcode=24,
                label="MH End (Showdown/End)",
                meta={},
            )
        )

    return events


# ----------------------------------------------------------------------
# Basic CommandType (BlindsUp / BreakSkip)
# ----------------------------------------------------------------------


def _build_events_blindsup_block(block_rows: List[Dict[str, str]], daily_diff_seconds: int) -> List[Event]:
    """
    CommandType = BlindsUp
    - Use ActionStart of first row -> BCode 20 (single event)
    """
    if not block_rows:
        return []
    r0 = block_rows[0]
    sec = _parse_time_to_sec(r0.get("ActionStart", ""), daily_diff_seconds)
    if sec <= 0:
        return []
    label = "Blinds Up"
    return [
        Event(
            time_sec=sec,
            kind="BlindsUp",
            bcode=20,
            label=label,
            meta={"sheet": r0.get("SheetName", ""), "row": r0.get("Row", "")},
        )
    ]


def _build_events_breakskip_block(block_rows: List[Dict[str, str]], daily_diff_seconds: int) -> List[Event]:
    """
    CommandType = BreakSkip
    - Use ActionStart of first row -> BCode 21 (single event)
    """
    if not block_rows:
        return []
    r0 = block_rows[0]
    sec = _parse_time_to_sec(r0.get("ActionStart", ""), daily_diff_seconds)
    if sec <= 0:
        return []
    label = "Break Skip"
    return [
        Event(
            time_sec=sec,
            kind="BreakSkip",
            bcode=21,
            label=label,
            meta={"sheet": r0.get("SheetName", ""), "row": r0.get("Row", "")},
        )
    ]


# ----------------------------------------------------------------------
# Top-level rows_payload -> Event list
# ----------------------------------------------------------------------


def build_timeline_from_rows(rows_payload: Union[Dict[str, Any], List[Any]], daily_diff_seconds: int) -> Tuple[List[Event], List[str]]:
    """
    Convert GAS Serialize WebApp result (JSON) to Event list.

    rows_payload shapes:
      1) {"ok": true, "rows": [...]}  # current GAS
      2) [{"CommandType": ...}, ...]  # legacy direct list
    Both are accepted.

    Returns:
        Tuple of (events, deleted_keys):
        - events: List of Event objects
        - deleted_keys: List of block keys (Hand_CommandType) marked for deletion
    """
    # 1) pull rows list from payload
    if isinstance(rows_payload, dict):
        raw_rows = rows_payload.get("rows") or []
    elif isinstance(rows_payload, list):
        raw_rows = rows_payload
    else:
        raw_rows = []

    # 2) normalize + drop empty rows + collect deleted block keys
    rows: List[Dict[str, str]] = []
    deleted_keys: List[str] = []  # Delete=1인 블록의 키 수집
    for raw in raw_rows:
        nr = _normalize_row(raw)
        if not _is_empty_row(nr):
            # Delete 플래그가 있는 행은 삭제 대상 키로 수집하고 스킵
            if nr.get("_delete"):
                # 블록 키 생성 (Hand + CommandType 조합)
                hand = nr.get("Hand", "")
                cmd = nr.get("CommandType", "")
                if hand or cmd:
                    deleted_keys.append(f"{hand}_{cmd}")
                continue
            rows.append(nr)

    if not rows:
        return ([], list(set(deleted_keys)))

    # 3) group into CommandType blocks (inherit previous when blank)
    blocks: List[Tuple[str, List[Dict[str, str]]]] = []
    cur_block: List[Dict[str, str]] = []
    cur_cmd: str | None = None

    for r in rows:
        cmd_raw = r.get("CommandType", "").strip()
        cmd = cmd_raw or cur_cmd

        # New CommandType starts a new block
        if cmd and cmd != cur_cmd:
            if cur_block:
                blocks.append((cur_cmd, cur_block))
            cur_block = []
            cur_cmd = cmd

        # If still no CommandType, skip
        if not cmd:
            continue

        cur_block.append(r)

    if cur_block:
        blocks.append((cur_cmd, cur_block))

    # 4) Convert each block to events
    events: List[Event] = []
    gto_block_idx = 0
    for cmd, blk in blocks:
        if cmd == "GTO-W":
            gto_block_idx += 1
            events.extend(_build_events_gtow_block(blk, daily_diff_seconds, block_index=gto_block_idx))
        elif cmd == "MysteryHands":
            events.extend(_build_events_mh_block(blk, daily_diff_seconds))
        elif cmd == "BlindsUp":
            events.extend(_build_events_blindsup_block(blk, daily_diff_seconds))
        elif cmd == "BreakSkip":
            events.extend(_build_events_breakskip_block(blk, daily_diff_seconds))
        else:
            continue
        # spacer between blocks for readability
        if events:
            last = events[-1]
            events.append(
                Event(
                    time_sec=last.time_sec + 0.0005,
                    kind="spacer",
                    bcode=None,
                    label="",
                    meta={"spacer": True},
                )
            )

    # 5) sort by time
    events.sort(key=lambda ev: ev.time_sec)
    return (events, list(set(deleted_keys)))
QUANT_STEP = 0.2  # seconds quantization for scheduler
_STEP_DEC = Decimal(str(QUANT_STEP))


def _quantize(sec: float, step: float = QUANT_STEP) -> float:
    try:
        d = Decimal(str(sec))
        s = Decimal(str(step))
        return float((d / s).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * s)
    except Exception:
        return sec
