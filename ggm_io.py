"""
ggm_io.py - I/O helpers for GGM timeline app (English logging)

- Config load/save
- Serialize WebApp fetch
- GTO-W CSV update (10x6 split, position CSV 68 rows)
- MysteryHands plan send
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LINESEP = "\n"  # Always LF for CSV
POS_PATH = {
    "Blank": r"C:\GGM$\GTOW\GTOW_Blank\blank.png",
    "Default_X": r"C:\GGM$\GTOW\GTOW_Default\LOOP_SHADOW_ARROW_X_00000.png",
    "Choice_O": r"C:\GGM$\GTOW\GTOW_Choice\LOOP_SHADOW_ARROW_O_00000.png",
    "Start_T": r"C:\GGM$\GTOW\GTOW_Start_T\IN_TOP_00000.png",
    "Start_L": r"C:\GGM$\GTOW\GTOW_Start_L\IN_LEFT_00000.png",
    "Start_R": r"C:\GGM$\GTOW\GTOW_Start_R\IN_RIGHT_00000.png",
    "Start_B": r"C:\GGM$\GTOW\GTOW_Start_B\IN_BOTTOM_00000.png",
}
POS_SEAT_ORDER = [
    (1, 1),
    (2, 1), (2, 2),
    (3, 1), (3, 2),
    (4, 1), (4, 2),
    (5, 1), (5, 2),
    (6, 1), (6, 2),
    (7, 1), (7, 2),
    (8, 1),
    (9, 1), (9, 2),
    (10, 1),
]
POS_INTRO_ORIENT = {
    "1-1": "Start_B",
    "2-1": "Start_B", "2-2": "Start_T",
    "3-1": "Start_R", "3-2": "Start_L",
    "4-1": "Start_R", "4-2": "Start_L",
    "5-1": "Start_R", "5-2": "Start_L",
    "6-1": "Start_B", "6-2": "Start_L",
    "7-1": "Start_B", "7-2": "Start_T",
    "8-1": "Start_B",
    "9-1": "Start_R", "9-2": "Start_L",
    "10-1": "Start_R",
}

# ============================================================
# Common utils
# ============================================================


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{now()}] {msg}", flush=True)


def ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    """
    Atomic text write with UTF-8 BOM (for Excel-friendly CSV).
    """
    ensure_dir(path.parent)
    import tempfile

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    data = ("\ufeff" + (text or "")).encode("utf-8")
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
    os.replace(tmp, str(path))


def blank_csv(rows: int = 10, cols: int = 6, fill_one: bool = False) -> str:
    """
    Create rows x cols CSV of blanks. If fill_one=True, put "1" in the last cell.
    """
    target_rows = max(rows, 20)  # ensure space to place trailing 1 at G20
    grid = [[""] * cols for _ in range(target_rows)]
    if fill_one and rows > 0 and cols > 0:
        grid[-1][-1] = "1"
    return LINESEP.join(",".join(r) for r in grid) + LINESEP


def ensure_trailing_one(csv_text: str, rows: int, cols: int) -> str:
    """
    Ensure bottom-right cell (row G20 equivalent) is "1" in an existing CSV text.
    """
    target_rows = max(rows, 20)  # pad to at least 20 rows
    lines = csv_text.strip("\r\n").splitlines()
    if len(lines) < target_rows:
        lines += [",".join([""] * cols)] * (target_rows - len(lines))
    elif len(lines) > target_rows:
        lines = lines[:target_rows]

    out = []
    for i, ln in enumerate(lines):
        cells = ln.split(",")
        if len(cells) < cols:
            cells += [""] * (cols - len(cells))
        elif len(cells) > cols:
            cells = cells[:cols]
        if i == target_rows - 1:
            cells[-1] = "1"
        out.append(",".join(cells))
    return LINESEP.join(out) + LINESEP


# ============================================================
# Config
# ============================================================

DEFAULT_CONFIG: Dict[str, Any] = {
    "csv_dir": r"C:/GGM$/CSV",
    "gto_csv_url": "",
    "rows": 10,
    "cols": 12,
    "whitelist": [],
    "serialize_url": "",
    "mh_plan_url": "",
    "daily_diff_seconds": 0,
    "serialize_time_offset_seconds": 0,
    "vmix_ip": "",
    "vmix_port": 8088,
    "companion_ip": "",
    "companion_port": 8000,
}

# Predefined slot set (matches backup directory + orientation rules)
# Seat 1: only -1; Seat 2,7: -1 (down), -2 (up); Seat 3,4,5,6: -1 (right), -2 (left); Seat 8,9,10: only -1.
HERO_SLOTS = [
    "Hero1-1",
    "Hero2-1", "Hero2-2",
    "Hero3-1", "Hero3-2",
    "Hero4-1", "Hero4-2",
    "Hero5-1", "Hero5-2",
    "Hero6-1", "Hero6-2",
    "Hero7-1", "Hero7-2",
    "Hero8-1",
    "Hero9-1",
    "Hero10-1",
]

VILLAIN_SLOTS = [
    "Villain1-1",
    "Villain2-1", "Villain2-2",
    "Villain3-1", "Villain3-2",
    "Villain4-1", "Villain4-2",
    "Villain5-1", "Villain5-2",
    "Villain6-1", "Villain6-2",
    "Villain7-1", "Villain7-2",
    "Villain8-1",
    "Villain9-1",
    "Villain10-1",
]

ALL_SLOTS = HERO_SLOTS + VILLAIN_SLOTS

def get_config_path() -> Path:
    return Path(__file__).with_name("ggm_config.json")


def load_config() -> Dict[str, Any]:
    path = get_config_path()
    cfg: Dict[str, Any] = {}
    if path.exists():
        try:
            with path.open("r", encoding="utf-8-sig") as f:
                cfg = json.load(f)
        except Exception as e:
            # Fallback: try stripping BOM manually
            try:
                raw = path.read_text(encoding="utf-8", errors="replace")
                raw = raw.lstrip("\ufeff")
                cfg = json.loads(raw)
                log(f"Config loaded with BOM fallback ({path})")
            except Exception:
                log(f"Config load failed ({path}): {e}")
                cfg = {}
    out = DEFAULT_CONFIG.copy()
    out.update(cfg or {})
    return out


def save_config(cfg: Dict[str, Any]) -> None:
    path = get_config_path()
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        log(f"Config saved: {path}")
    except Exception as e:
        log(f"Config save failed ({path}): {e}")


# ============================================================
# Serialize WebApp fetch
# ============================================================


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value))
        except Exception:
            return None
    s = str(value).strip()
    if not s:
        return None

    fmts = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y/%m/%d %H:%M:%S",
        "%Y.%m.%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue

    if s.endswith("Z"):
        for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def _normalize_row(raw: Dict[str, Any]) -> Dict[str, Any]:
    def g(*names: str) -> Any:
        for n in names:
            if n in raw:
                return raw.get(n)
        return None

    return {
        "command_type": g("command_type", "CommandType"),
        "seat_index": g("seat_index", "SeatIndex"),
        "action_start": _parse_datetime(g("action_start", "ActionStart")),
        "action_end": _parse_datetime(g("action_end", "ActionEnd")),
        "action": g("action", "Action"),
        "text1": g("text1", "Text1") or "",
        "text2": g("text2", "Text2") or "",
        "text3": g("text3", "Text3") or "",
        "value1": g("value1", "Value1") or "",
        "value2": g("value2", "Value2") or "",
        "value3": g("value3", "Value3") or "",
    }


def fetch_serialize_rows(cfg: Dict[str, Any], timeout: float = 10.0, quiet: bool = False) -> List[Dict[str, Any]]:
    url = cfg.get("serialize_url") or ""
    if not url:
        raise RuntimeError("serialize_url not configured")

    if not quiet:
        log("Serialize WebApp fetch")
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Serialize WebApp fetch failed: {e}") from e

    try:
        j = json.loads(data)
    except Exception as e:
        raise RuntimeError(
            f"Serialize WebApp JSON parse failed: {e} / raw={data[:200]}"
        ) from e

    # Dump raw JSON for debugging
    try:
        dump_dir = Path("C:/GGM$/GGM_Timeline/Jsons")
        ensure_dir(dump_dir)
        ts_name = datetime.now().strftime("serialize_%Y%m%d_%H%M%S.json")
        (dump_dir / ts_name).write_text(data, encoding="utf-8")
    except Exception:
        pass

    if not isinstance(j, dict) or not j.get("ok"):
        raise RuntimeError(f"Serialize WebApp response ok=false / raw={data[:200]}")

    rows_raw = j.get("rows") or []
    if not isinstance(rows_raw, list):
        raise RuntimeError("Serialize WebApp response rows format error")

    rows: List[Dict[str, Any]] = []
    for r in rows_raw:
        if not isinstance(r, dict):
            continue
        rows.append(_normalize_row(r))
    if not quiet:
        log(f"Serialize rows: {len(rows)}")
    return rows


# ============================================================
# GTO-W CSV update
# ============================================================


def scan_slot_files(base_dir: Path) -> List[str]:
    names: List[str] = []
    if not base_dir.exists():
        return names
    for name in os.listdir(base_dir):
        p = base_dir / name
        if not p.is_file():
            continue
        low = name.lower()
        if not (low.endswith(".csv") or "." not in name):
            continue
        base = name[:-4] if low.endswith(".csv") else name
        if base.startswith("Hero") or base.startswith("Villain"):
            names.append(base)
    return sorted(set(names))


def pick_target_path(base_dir: Path, slot: str) -> Path:
    p_noext = base_dir / slot
    p_csv = base_dir / f"{slot}.csv"
    if p_noext.exists():
        return p_noext
    if p_csv.exists():
        return p_csv
    return p_csv


def get_slot_whitelist(csv_dir: Path, cfg: Dict[str, Any]) -> List[str]:
    wl = cfg.get("whitelist") or []
    if isinstance(wl, list) and wl:
        return [str(x) for x in wl]
    # Default: full slot set
    return ALL_SLOTS


def split_12_to_6_6(csv_12: str, rows: int, cols12: int):
    try:
        lines = csv_12.strip("\r\n").splitlines()
        outL, outR = [], []
        for ln in lines[:rows]:
            cells = ln.split(",")
            if len(cells) < cols12:
                cells += [""] * (cols12 - len(cells))
            L = ",".join(cells[: cols12 // 2])
            R = ",".join(cells[cols12 // 2 : cols12])
            outL.append(L)
            outR.append(R)
        return LINESEP.join(outL) + LINESEP, LINESEP.join(outR) + LINESEP
    except Exception:
        return None, None


def write_all(
    csv_dir: Path,
    rows: int,
    hero_slot: str,
    villain_slot: str,
    hero_text: str,
    villain_text: str,
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    all_bases = get_slot_whitelist(csv_dir, cfg)
    if not all_bases:
        all_bases = [hero_slot, villain_slot]
    active = {hero_slot, villain_slot}
    wrote = blanked = 0
    for base in all_bases:
        path = pick_target_path(csv_dir, base)
        if base in active:
            text = hero_text if base == hero_slot else villain_text
            text = ensure_trailing_one(text or blank_csv(rows, 6), rows, 6)
            atomic_write_text(path, text)
            wrote += 1
        else:
            atomic_write_text(path, blank_csv(rows, 6, fill_one=True))
            blanked += 1
    return {
        "wrote": wrote,
        "blanked": blanked,
        "hero": hero_slot,
        "villain": villain_slot,
    }


# ---- Position CSV (68 rows) ----

_SPLIT_RE = re.compile(r"[,\n;\t]+")


def _normalize_pos_to_vertical(text_or_none: Optional[str]) -> str:
    """
    Normalize position text to 68-row, 1-column CSV (legacy/vMix friendly).
    Accepts comma/semicolon/newline/tab separated text.
    """
    items: List[str]
    if text_or_none is None:
        items = []
    elif isinstance(text_or_none, (list, tuple)):
        items = [str(v) if v is not None else "" for v in text_or_none]
    else:
        s = str(text_or_none)
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        if _SPLIT_RE.search(s):
            items = [t.strip().strip('"') for t in _SPLIT_RE.split(s) if t is not None]
        else:
            if re.match(r"^[A-Za-z]:\\", s):
                items = [t for t in re.split(r"(?=[A-Za-z]:\\)", s) if t]
            else:
                items = [s]

    if len(items) < 68:
        items += [""] * (68 - len(items))
    elif len(items) > 68:
        items = items[:68]

    def esc(v: str) -> str:
        v = "" if v is None else str(v)
        return '"' + v.replace('"', '""') + '"' if ("," in v) else v

    return (LINESEP.join(esc(v) for v in items)) + LINESEP

def _is_all_blank_pos(text_or_none: Optional[str]) -> bool:
    if text_or_none is None:
        return True
    s = str(text_or_none)
    if not s.strip():
        return True
    items = _normalize_pos_to_vertical(text_or_none).strip().split("\n")

    # treat both empty strings and explicit Blank image paths as "blank"
    blank_path_norm = POS_PATH["Blank"].replace("\\", "/").lower()

    def _is_blank_item(raw: str) -> bool:
        v = raw.strip().strip('"')
        if not v:
            return True
        v_norm = v.replace("\\", "/").lower()
        return v_norm == blank_path_norm

    return all(_is_blank_item(itm) for itm in items)

def _build_pos_row_from_slot(slot: str) -> str:
    """
    Build 68-row vertical CSV from slot name (e.g., Hero7-2, Villain3-1).
    """
    m = re.match(r".*?(\d+)-(\d+)", slot or "")
    if not m:
        return _normalize_pos_to_vertical(None)
    try:
        num = int(m.group(1))
        pos = int(m.group(2))
    except Exception:
        return _normalize_pos_to_vertical(None)
    actual_key = f"{num}-{pos}"
    row: List[str] = []
    # Seat Intro
    for n, p in POS_SEAT_ORDER:
        key = f"{n}-{p}"
        path = POS_PATH.get(POS_INTRO_ORIENT.get(key, "Blank"), POS_PATH["Blank"])
        row.append(path if key == actual_key else POS_PATH["Blank"])
    # Choice
    for n, p in POS_SEAT_ORDER:
        key = f"{n}-{p}"
        row.append(POS_PATH["Choice_O"] if key == actual_key else POS_PATH["Blank"])
    # End Choice
    for n, p in POS_SEAT_ORDER:
        key = f"{n}-{p}"
        row.append(POS_PATH["Default_X"] if key == actual_key else POS_PATH["Blank"])
    # Back
    for n, p in POS_SEAT_ORDER:
        key = f"{n}-{p}"
        row.append(POS_PATH["Default_X"] if key == actual_key else POS_PATH["Blank"])
    return _normalize_pos_to_vertical(row)


def write_positions(csv_dir: Path, csvPos: Dict[str, Any], hero_slot: str | None = None, vill_slot: str | None = None) -> Dict[str, str]:
    if not isinstance(csvPos, dict):
        csvPos = {}
    out: Dict[str, str] = {}
    pH = csv_dir / "Hero_Position.csv"
    pV = csv_dir / "Villain_Position.csv"

    def _pick(text_value, fallback_slot):
        if not _is_all_blank_pos(text_value):
            return _normalize_pos_to_vertical(text_value)
        if fallback_slot and not str(fallback_slot).endswith("0-0"):
            return _build_pos_row_from_slot(fallback_slot)
        return None

    hero_txt = _pick(csvPos.get("hero"), hero_slot)
    vill_txt = _pick(csvPos.get("villain"), vill_slot)
    if hero_txt is not None:
        atomic_write_text(pH, hero_txt)
        out["hero_pos"] = str(pH)
    if vill_txt is not None:
        atomic_write_text(pV, vill_txt)
        out["vill_pos"] = str(pV)
    return out


def fetch_next_gto_block(gs_url: str, timeout: float = 10.0) -> Dict[str, Any]:
    """
    Fetch next GTO-W block from WebApp.
    """
    log("GTO-W WebApp fetch")
    req = urllib.request.Request(gs_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"GTO-W WebApp fetch failed: {e}") from e

    try:
        j = json.loads(data)
    except Exception as e:
        raise RuntimeError(
            f"GTO-W WebApp JSON parse failed: {e} / raw={data[:160]}"
        ) from e

    # Dump raw JSON for debugging
    try:
        dump_dir = Path("C:/GGM$/GGM_Timeline/Jsons")
        ensure_dir(dump_dir)
        ts_name = datetime.now().strftime("gtow_%Y%m%d_%H%M%S.json")
        (dump_dir / ts_name).write_text(data, encoding="utf-8")
    except Exception:
        pass

    if not isinstance(j, dict) or not j.get("ok"):
        raise RuntimeError(f"GTO-W WebApp response ok=false / raw={data[:160]}")
    return j


def run_gtow_csv_update(cfg: Dict[str, Any], timeout: float = 10.0) -> Dict[str, Any]:
    """
    Fetch GTO-W block and update Hero/Villain CSV + Position CSV under csv_dir.
    """
    csv_dir = Path(cfg.get("csv_dir") or DEFAULT_CONFIG["csv_dir"])
    gs_url = cfg.get("gto_csv_url") or ""
    rows = int(cfg.get("rows") or 10)
    cols12 = int(cfg.get("cols") or 12)

    if not gs_url:
        raise RuntimeError("gto_csv_url not configured")
    if not csv_dir.exists():
        ensure_dir(csv_dir)

    j = fetch_next_gto_block(gs_url, timeout=timeout)

    hero_slot = (j.get("heroSlot") or j.get("hero") or "").strip()
    vill_slot = (j.get("villSlot") or j.get("villain") or "").strip()
    if not hero_slot or not vill_slot:
        raise RuntimeError("hero/villain slot missing in response")

    hero_text = villain_text = ""
    payload = j.get("csv")
    if isinstance(payload, dict):
        hero_text = str(payload.get(hero_slot, ""))
        villain_text = str(payload.get(vill_slot, ""))
    else:
        csv_12 = str(payload or "")
        L, R = split_12_to_6_6(csv_12, rows, cols12)
        hero_text, villain_text = L or "", R or ""

    res = write_all(
        csv_dir, rows, hero_slot, vill_slot, hero_text, villain_text, cfg
    )

    posinfo = write_positions(csv_dir, j.get("csvPos") or {}, hero_slot, vill_slot)

    result: Dict[str, Any] = {"ok": True}
    result.update(res)
    result.update(posinfo)
    log(
        f"CSV updated Hero={res['hero']} Villain={res['villain']} "
        f"(written {res['wrote']}, blanked {res['blanked']})"
        + (" | pos saved" if posinfo else "")
    )
    return result


# ============================================================
# MysteryHands plan (mh_prepare)
# ============================================================


def send_mh_plan(
    plan_meta: Dict[str, Any], cfg: Dict[str, Any], timeout: float = 10.0
) -> None:
    """
    Send MysteryHands plan (plan_meta['plan']) to mh_plan_url WebApp.
    """
    url = cfg.get("mh_plan_url") or ""
    if not url:
        log("mh_plan_url missing; skip MysteryHands plan send")
        return

    payload = {
        "orange_sequence": list(plan_meta.get("orange_sequence") or []),
        "initial_open_count": int(plan_meta.get("initial_open_count") or 0),
        "players_count": int(plan_meta.get("players_count") or 0),
        "always_open_seat": plan_meta.get("always_open_seat"),
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")

    log(f"MysteryHands plan send: payload={payload}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_text = resp.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as e:
        raise RuntimeError(f"MysteryHands WebApp fetch failed: {e}") from e

    try:
        j = json.loads(resp_text)
    except Exception as e:
        raise RuntimeError(
            f"MysteryHands WebApp JSON parse failed: {e} / raw={resp_text[:160]}"
        ) from e

    if not isinstance(j, dict) or not j.get("ok"):
        raise RuntimeError(
            f"MysteryHands WebApp response ok=false / raw={resp_text[:160]}"
        )
