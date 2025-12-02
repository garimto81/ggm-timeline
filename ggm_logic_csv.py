from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Tuple

from ggm_io import LINESEP, ensure_trailing_one, blank_csv, ALL_SLOTS


def map_seatindex_to_table(seatidx_raw: Any) -> int | None:
    """
    Map serialize SeatIndex (0-based clockwise, Vlada=0) to table seat number (1..10).
    Mapping:
      0->5,1->6,2->7,3->8,4->9,5->1,6->2,7->3,8->4,9->10
    """
    try:
        s = int(seatidx_raw)
    except Exception:
        return None
    mapping = {0: 5, 1: 6, 2: 7, 3: 8, 4: 9, 5: 1, 6: 2, 7: 3, 8: 4, 9: 10}
    return mapping.get(s)


SEAT_ANGLES = {
    1: 270,
    2: 230,
    3: 210,
    4: 150,
    5: 120,
    6: 90,
    7: 60,
    8: 30,
    9: 330,
    10: 300,
}


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def choose_orientation(seat: int, other_seat: int | None) -> int:
    """
    Decide Y (1/2) based on seat and other seat to avoid overlap.
    Rules:
      - Seats with single orientation: {1,8,9,10} -> 1
      - If other_seat is None -> 1
      - If seats are not adjacent (difference not 1 with wrap) -> 1
      - Seats 2,7: Y1=down(270), Y2=up(90); pick direction away from other seat angle.
      - Seats 3,4,5,6: Y1=right(0), Y2=left(180); pick away from other seat angle.
    """
    if seat in (1, 8, 9, 10):
        return 1
    if other_seat is None:
        return 1

    def _adjacent(a: int, b: int) -> bool:
        if a is None or b is None:
            return False
        diff = abs(a - b) % 10
        return diff in (1, 9)

    if not _adjacent(seat, other_seat):
        return 1

    other_ang = SEAT_ANGLES.get(other_seat, 0)
    if seat in (2, 7):
        dirs = {1: 270, 2: 90}
    elif seat in (3, 4, 5, 6):
        dirs = {1: 0, 2: 180}
    else:
        dirs = {1: 0}
    best_y = 1
    best_diff = -1
    for y, ang in dirs.items():
        d = _angle_diff(ang, other_ang)
        if d > best_diff:
            best_diff = d
            best_y = y
    return best_y


def _parse_ts(val: Any) -> float:
    if val is None:
        return 0.0
    if isinstance(val, datetime):
        t = val
    else:
        s = str(val).strip()
        fmts = [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]
        t = None
        for f in fmts:
            try:
                t = datetime.strptime(s, f)
                break
            except Exception:
                continue
        if t is None:
            return 0.0
    return t.timestamp()


def _get(r: Dict[str, Any], *names: str) -> Any:
    for n in names:
        if n in r:
            return r.get(n)
    return None


def _fmt_cell_value(val: Any) -> str:
    """
    Preserve human-friendly formatting for numeric values.
    - Percent cells in Sheets come as 0.xx; emit "xx%" instead of 0.xx.
    - Otherwise keep the original number/string as-is.
    """
    if val is None:
        return ""
    try:
        # bool is int subclass; keep as literal
        if isinstance(val, bool):
            return str(val)
        if isinstance(val, (int, float)):
            v = float(val)
            if 0 <= v <= 1:
                pct = v * 100
                if abs(pct - round(pct)) < 1e-6:
                    return f"{int(round(pct))}%"
                return f"{pct:.2f}%"
            return str(val)
    except Exception:
        pass
    return str(val)


def build_gtow_csv_from_rows(
    rows_payload: Any,
    daily_diff_seconds: int,
    row_filter: set[tuple[str, str]] | None = None,
    block_index: int | None = None,
) -> Tuple[str, str, str, str]:
    """
    From serialize rows payload, infer hero/villain slots and CSV text.
    Steps:
      - Scan rows in order, inheriting blank CommandType from previous.
      - Take the target GTO-W block:
          * if block_index is given -> pick that numbered GTO-W block (1-based).
          * else -> first GTO-W block.
      - Map SeatIndex to table seat (0->5,...,9->10), pick first two distinct seats as Hero/Villain.
      - Orientation(Y) chosen to avoid overlap using seat angle heuristic.
      - Rows assigned to hero/villain by seat; build 6-column CSV (Text1..Value3) in time order.
      - Pad to at least 10 rows (ensure trailing 1 at row 20).
    """
    raw_rows = rows_payload.get("rows") if isinstance(rows_payload, dict) else rows_payload
    if not isinstance(raw_rows, list):
        raise RuntimeError("rows_payload is not list/dict with rows")

    # 1) inherit CommandType for blank rows
    rows_norm = []
    last_cmd = ""
    for r in raw_rows:
        if not isinstance(r, dict):
            continue
        ct_raw = (_get(r, "CommandType", "command_type") or "").strip()
        if ct_raw:
            last_cmd = ct_raw
        ct = ct_raw or last_cmd
        row_copy = dict(r)
        row_copy["CommandType"] = ct
        # normalize common fields for downstream access
        row_copy.setdefault("SeatIndex", _get(r, "SeatIndex", "seat_index"))
        row_copy.setdefault("ActionStart", _get(r, "ActionStart", "action_start"))
        row_copy.setdefault("ActionEnd", _get(r, "ActionEnd", "action_end"))
        row_copy.setdefault("Text1", _get(r, "Text1", "text1"))
        row_copy.setdefault("Text2", _get(r, "Text2", "text2"))
        row_copy.setdefault("Text3", _get(r, "Text3", "text3"))
        row_copy.setdefault("Value1", _get(r, "Value1", "value1"))
        row_copy.setdefault("Value2", _get(r, "Value2", "value2"))
        row_copy.setdefault("Value3", _get(r, "Value3", "value3"))
        # optional filter by sheet/row
        if row_filter:
            sh = str(row_copy.get("SheetName") or "").strip()
            rw = str(row_copy.get("Row") or "").strip()
            if sh or rw:
                if (sh, rw) not in row_filter and ("", rw) not in row_filter:
                    continue
        rows_norm.append(row_copy)

    # 2) split into blocks and pick first GTO-W block
    blocks: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_cmd = None
    for r in rows_norm:
        ct = (r.get("CommandType") or "").strip()
        if ct and ct != cur_cmd:
            if cur:
                blocks.append(cur)
            cur = []
            cur_cmd = ct
        if not ct:
            continue
        cur.append(r)
    if cur:
        blocks.append(cur)

    gtow_block: List[Dict[str, Any]] = []
    target_idx = 1 if block_index is None else int(block_index)
    seen_idx = 0
    for blk in blocks:
        if blk and (blk[0].get("CommandType") or "").strip() == "GTO-W":
            seen_idx += 1
            if seen_idx == target_idx:
                gtow_block = blk
                break
    if not gtow_block:
        raise RuntimeError("No GTO-W block found")

    gtow_rows: List[Tuple[float, int, Dict[str, Any]]] = []
    for r in gtow_block:
        seat_mapped = map_seatindex_to_table(r.get("SeatIndex"))
        if seat_mapped is None:
            continue
        ts = _parse_ts(r.get("ActionStart")) + daily_diff_seconds
        gtow_rows.append((ts, seat_mapped, r))

    if not gtow_rows:
        raise RuntimeError("No GTO-W rows in first block")

    gtow_rows.sort(key=lambda x: x[0] if x[0] > 0 else 1e20)

    seen = []
    for _, seat, _ in gtow_rows:
        if seat not in seen:
            seen.append(seat)
        if len(seen) >= 2:
            break
    if len(seen) < 2:
        raise RuntimeError("Not enough distinct seats for hero/villain")
    hero_seat, villain_seat = seen[0], seen[1]

    hero_y = choose_orientation(hero_seat, villain_seat)
    vill_y = choose_orientation(villain_seat, hero_seat)
    hero_slot = f"Hero{hero_seat}-{hero_y}"
    villain_slot = f"Villain{villain_seat}-{vill_y}"

    hero_rows_csv: List[List[str]] = []
    vill_rows_csv: List[List[str]] = []

    for ts, seat, r in gtow_rows:
        seat_table = seat
        cols = [
            _fmt_cell_value(r.get("Text1")),
            _fmt_cell_value(r.get("Text2")),
            _fmt_cell_value(r.get("Text3")),
            _fmt_cell_value(r.get("Value1")),
            _fmt_cell_value(r.get("Value2")),
            _fmt_cell_value(r.get("Value3")),
        ]
        if seat_table == hero_seat:
            hero_rows_csv.append(cols)
        elif seat_table == villain_seat:
            vill_rows_csv.append(cols)

    def to_csv(rows: List[List[str]], rows_target: int = 10) -> str:
        text_rows = []
        for r in rows[:rows_target]:
            text_rows.append(",".join(r))
        while len(text_rows) < rows_target:
            text_rows.append(",".join([""] * 6))
        return ensure_trailing_one(LINESEP.join(text_rows) + LINESEP, rows_target, 6)

    hero_csv = to_csv(hero_rows_csv)
    vill_csv = to_csv(vill_rows_csv)

    return hero_slot, villain_slot, hero_csv, vill_csv
