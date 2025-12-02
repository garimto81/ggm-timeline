"""
ggm_timeline_app.py - Main GUI app for GGM timeline automation

- Periodically polls Serialize WebApp for rows.
- Builds timeline events via ggm_logic.
- Uses vMix replay timecode (if configured) or local clock to schedule BCode firing.
- Sends BCode to Companion at the target time; also supports GTO-W CSV update / MysteryHands plan.
"""

from __future__ import annotations

import queue
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from pathlib import Path

import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.font as tkfont

import ggm_logic
import ggm_logic_csv
import ggm_io


@dataclass
class EventState:
    """Timeline event state for UI/run."""

    event: ggm_logic.Event
    enabled: bool = True
    executed: bool = False
    sending: bool = False
    failed: bool = False
    executed_at: Optional[float] = None
    tree_id: Optional[str] = None


class TimelineApp(tk.Tk):
    POLL_INTERVAL_MS = 20_000   # Serialize rows poll interval
    RUN_INTERVAL_MS = 200       # Event check interval (0.2s)
    VMIX_POLL_INTERVAL_MS = 200  # 0.2 sec
    STATUS_POLL_MS = 3_000      # connectivity status check
    QUANT_STEP = 0.2            # seconds quantization

    def __init__(self) -> None:
        super().__init__()
        self.title("GGM Timeline Controller")

        # Config
        self.cfg: Dict = ggm_io.load_config()
        self.daily_diff_seconds: int = int(self.cfg.get("daily_diff_seconds") or 0)
        self.serialize_time_offset_seconds: int = int(self.cfg.get("serialize_time_offset_seconds") or 0)
        self.vmix_replay_sec: Optional[float] = None
        self.vmix_replay_str: str = "--:--:--"
        self._vmix_last_error: Optional[str] = None
        self.vmix_ip: str = str(self.cfg.get("vmix_ip") or "").strip()
        self.vmix_port: str = str(self.cfg.get("vmix_port") or "8088").strip()
        self.companion_ip: str = str(self.cfg.get("companion_ip") or "").strip()
        self.companion_port: str = str(self.cfg.get("companion_port") or "8000").strip()
        self.status_vmix_ok: bool = False
        self.status_comp_ok: bool = False
        self.status_fetch_ok: bool = False
        self._status_symbol = "●"
        self._vmix_last_tick: float | None = None

        # State
        self.events: List[EventState] = []
        self.running: bool = True  # start running; toggle with F2/F3
        self._polling_in_progress = False
        self.executed_keys: set[tuple] = set()
        self.failed_keys: set[tuple] = set()
        self.executed_at_map: Dict[tuple, float] = {}
        self.initial_csv_triggered: bool = False
        self.gtow_csv_done: set[tuple] = set()
        self.sent_mh_seq: set[tuple] = set()
        self.last_rows: Optional[Any] = None

        # Worker queue
        self.worker_q: "queue.Queue" = queue.Queue()
        self.worker_thread = threading.Thread(
            target=self._worker_loop, name="worker", daemon=True
        )
        self.worker_thread.start()

        # UI
        self._build_ui()

        # Key binds
        self.bind("<F2>", lambda e: self.set_running(True))
        self.bind("<F3>", lambda e: self.set_running(False))

        # Kick off polling and run loop
        self.after(100, self.poll_once)
        self.after(self.RUN_INTERVAL_MS, self._run_loop)
        self.after(200, self._poll_vmix_loop)
        self.after(1000, self._poll_status_loop)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        top = ttk.Frame(self)
        top.pack(side=tk.TOP, fill=tk.X, padx=8, pady=6)

        # Row 1
        row1 = ttk.Frame(top)
        row1.pack(side=tk.TOP, fill=tk.X, pady=2)
        ttk.Label(row1, text="Daily Diff (sec):").pack(side=tk.LEFT)
        self.daily_diff_var = tk.StringVar(value=str(self.daily_diff_seconds))
        ttk.Entry(row1, textvariable=self.daily_diff_var, width=8).pack(side=tk.LEFT, padx=(2, 8))

        ttk.Label(row1, text="vMix IP:").pack(side=tk.LEFT)
        self.vmix_ip_var = tk.StringVar(value=self.vmix_ip)
        ttk.Entry(row1, textvariable=self.vmix_ip_var, width=14).pack(side=tk.LEFT, padx=(2, 4))
        ttk.Label(row1, text="Port:").pack(side=tk.LEFT)
        self.vmix_port_var = tk.StringVar(value=self.vmix_port)
        ttk.Entry(row1, textvariable=self.vmix_port_var, width=6).pack(side=tk.LEFT, padx=(2, 8))

        ttk.Label(row1, text="Companion IP:").pack(side=tk.LEFT)
        self.comp_ip_var = tk.StringVar(value=self.companion_ip)
        ttk.Entry(row1, textvariable=self.comp_ip_var, width=14).pack(side=tk.LEFT, padx=(2, 4))
        ttk.Label(row1, text="Port:").pack(side=tk.LEFT)
        self.comp_port_var = tk.StringVar(value=self.companion_port)
        ttk.Entry(row1, textvariable=self.comp_port_var, width=6).pack(side=tk.LEFT, padx=(2, 8))

        btn_save_dd = ttk.Button(row1, text="Save", command=self._on_save_config)
        btn_save_dd.pack(side=tk.LEFT, padx=(4, 4))

        # Row 2
        row2 = ttk.Frame(top)
        row2.pack(side=tk.TOP, fill=tk.X, pady=2)
        self.run_status_var = tk.StringVar(value="Running (F2/F3)")
        ttk.Label(row2, textvariable=self.run_status_var).pack(side=tk.LEFT, padx=4)

        self.vmix_time_var = tk.StringVar(value="vMix: --:--:--")
        ttk.Label(row2, textvariable=self.vmix_time_var, font=("Segoe UI", 12, "bold")).pack(side=tk.LEFT, padx=8)

        # Status lights
        self.vmix_status_var = tk.StringVar(value=f"vMix {self._status_symbol}")
        self.comp_status_var = tk.StringVar(value=f"Companion {self._status_symbol}")
        self.fetch_status_var = tk.StringVar(value=f"Fetch {self._status_symbol}")
        self.vmix_status_label = ttk.Label(row2, textvariable=self.vmix_status_var, foreground="red")
        self.comp_status_label = ttk.Label(row2, textvariable=self.comp_status_var, foreground="red")
        self.fetch_status_label = ttk.Label(row2, textvariable=self.fetch_status_var, foreground="red")
        self.vmix_status_label.pack(side=tk.LEFT, padx=(12, 6))
        self.comp_status_label.pack(side=tk.LEFT, padx=(6, 6))
        self.fetch_status_label.pack(side=tk.LEFT, padx=(6, 6))

        # Middle: events table
        mid = ttk.Frame(self)
        mid.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=5, pady=5)

        cols = ("time", "remain", "kind", "bcode", "label", "status")
        self.tree = ttk.Treeview(
            mid, columns=cols, show="headings", selectmode="browse", height=20
        )
        self.tree.heading("time", text="Time")
        self.tree.heading("remain", text="Remain")
        self.tree.heading("kind", text="Kind")
        self.tree.heading("bcode", text="BCode")
        self.tree.heading("label", text="Label")
        self.tree.heading("status", text="Status")

        self.tree.column("time", width=90, anchor=tk.CENTER)
        self.tree.column("remain", width=90, anchor=tk.CENTER)
        self.tree.column("kind", width=80, anchor=tk.CENTER)
        self.tree.column("bcode", width=60, anchor=tk.CENTER)
        self.tree.column("label", width=260, anchor=tk.W)
        self.tree.column("status", width=70, anchor=tk.CENTER)
        # Tag styles
        font_default = tkfont.nametofont("TkDefaultFont")
        font_bold = font_default.copy()
        font_bold.configure(weight="bold")
        self.tree.tag_configure("done", background="#d1ffd1")
        self.tree.tag_configure("fail", background="#ffd1d1")
        self.tree.tag_configure("soon", foreground="red", font=font_bold)
        self.tree.tag_configure("donepast", foreground="gray")

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        vsb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.configure(yscrollcommand=vsb.set)

        # Bottom: log
        bottom = ttk.Frame(self)
        bottom.pack(side=tk.TOP, fill=tk.BOTH, expand=False, padx=5, pady=(0, 5))

        ttk.Label(bottom, text="Log:").pack(anchor="w")
        self.txt_log = tk.Text(bottom, height=8, state="disabled")
        self.txt_log.pack(fill=tk.BOTH, expand=True)

    def _on_save_config(self) -> None:
        try:
            v = int(self.daily_diff_var.get().strip() or "0")
        except ValueError:
            messagebox.showerror("Error", "Daily Diff must be an integer.")
            return
        self.daily_diff_seconds = v
        self.cfg["daily_diff_seconds"] = v
        # offset is read-only from cfg for now

        self.vmix_ip = self.vmix_ip_var.get().strip()
        self.vmix_port = self.vmix_port_var.get().strip() or "8088"
        self.cfg["vmix_ip"] = self.vmix_ip
        self.cfg["vmix_port"] = self.vmix_port

        self.companion_ip = self.comp_ip_var.get().strip()
        self.companion_port = self.comp_port_var.get().strip() or "8000"
        self.cfg["companion_ip"] = self.companion_ip
        self.cfg["companion_port"] = self.companion_port

        ggm_io.save_config(self.cfg)
        self.log(f"Config saved (daily_diff={v}, vmix={self.vmix_ip}:{self.vmix_port}, companion={self.companion_ip}:{self.companion_port})")

    # ------------------------------------------------------------------
    # Logging / state display
    # ------------------------------------------------------------------
    def log(self, msg: str) -> None:
        """Log to console and UI."""
        ggm_io.log(msg)
        self.txt_log.configure(state="normal")
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.configure(state="disabled")

    def _quantize(self, sec: float) -> float:
        try:
            return round(sec / self.QUANT_STEP) * self.QUANT_STEP
        except Exception:
            return sec

    def set_running(self, running: bool) -> None:
        self.running = running
        self.run_status_var.set("Running (F2/F3)" if running else "Stopped (F2/F3)")
        self.log(f"Run = {running}")

    # ------------------------------------------------------------------
    # Polling / timeline refresh
    # ------------------------------------------------------------------
    def poll_once(self) -> None:
        if self._polling_in_progress:
            return
        self._polling_in_progress = True

        def job():
            try:
                rows = ggm_io.fetch_serialize_rows(self.cfg, quiet=True)
                offset = self.daily_diff_seconds + self.serialize_time_offset_seconds
                events, deleted_keys = ggm_logic.build_timeline_from_rows(
                    rows, offset
                )
                self.last_rows = rows
                self._schedule_on_main(lambda: self._update_fetch_state(True))
            except Exception as exc:
                self._schedule_on_main(lambda: self._update_fetch_state(False))
                self._polling_in_progress = False
                return

            def apply():
                try:
                    # Delete=1 블록의 executed_keys 제거 (재실행 가능하도록)
                    if deleted_keys:
                        keys_to_remove = set()
                        for exec_key in self.executed_keys:
                            # exec_key: (time_sec, kind, label) 등의 튜플
                            # deleted_keys: "Hand_CommandType" 형식의 문자열 리스트
                            for del_key in deleted_keys:
                                # exec_key의 kind가 deleted_keys의 CommandType과 매칭되는지 확인
                                if len(exec_key) >= 2 and del_key.endswith(f"_{exec_key[1]}"):
                                    keys_to_remove.add(exec_key)
                        self.executed_keys -= keys_to_remove
                    self._update_events(events)
                    # first GTO-W hand -> ensure CSV prepared once
                    if not self.initial_csv_triggered:
                        if any(ev.kind == "GTO-W" for ev in events):
                            dummy = ggm_logic.Event(
                                time_sec=0.0,
                                kind="gtow_csv_init",
                                bcode=None,
                                label="",
                                meta={},
                            )
                            self._enqueue_worker(self._do_gtow_csv_update, EventState(event=dummy), {})
                            self.initial_csv_triggered = True
                    self._update_fetch_state(True)
                except Exception:
                    self._update_fetch_state(False)
                finally:
                    self._polling_in_progress = False

            self._schedule_on_main(apply)

        threading.Thread(target=job, name="poll", daemon=True).start()
        self.after(self.POLL_INTERVAL_MS, self.poll_once)

    def _format_time(self, sec: float) -> str:
        sec = ggm_logic._quantize(float(sec)) % 86_400
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{s:04.1f}"

    def _format_remain(self, target_sec: float, now_sec: Optional[float]) -> str:
        if now_sec is None:
            return "-"
        diff = float(target_sec - now_sec)
        sign = "-" if diff < 0 else ""
        diff = abs(diff)
        h, rem = divmod(diff, 3600)
        m, s = divmod(rem, 60)
        return f"{sign}{int(h):02d}:{int(m):02d}:{s:04.1f}"

    def _make_key(self, ev: ggm_logic.Event) -> tuple:
        return (
            ev.kind,
            ev.bcode,
            ev.label,
            ev.meta.get("sheet"),
            ev.meta.get("row"),
            round(ev.time_sec, 1),
        )

    def _pick_next_mh_sequence_block(self) -> Optional[EventState]:
        """
        Choose the next MH block's sequence to send.
        Rules:
          - Group MH events (MysteryHands/mh_sequence) by contiguous blocks (spacer splits).
          - Only after all earlier MH blocks are completed (executed or failed), send the first
            block that still has pending events and has an mh_sequence not sent/executed/failed.
        """
        blocks: List[List[EventState]] = []
        cur: List[EventState] = []
        for st in self.events:
            if st.event.kind == "spacer":
                if cur:
                    blocks.append(cur)
                    cur = []
                continue
            if st.event.kind in ("MysteryHands", "mh_sequence"):
                cur.append(st)
        if cur:
            blocks.append(cur)

        # iterate blocks in order
        all_prev_complete = True
        for blk in blocks:
            complete = all(s.executed or s.failed for s in blk)
            seq_candidates = [s for s in blk if s.event.kind == "mh_sequence"]

            if not all_prev_complete:
                # earlier block not done -> hold off
                continue

            if complete:
                # this block done; move to next
                continue

            # this is the first incomplete block
            for st in seq_candidates:
                if not st.executed and not st.failed and not st.sending:
                    return st
            # if no sequence found or already processed, stop searching
            break

        return None

    def _update_events(self, new_events: List[ggm_logic.Event]) -> None:
        """Replace timeline with new list."""
        now_sec = self._current_clock_sec()

        self.events.clear()
        self.tree.delete(*self.tree.get_children())

        for ev in new_events:
            key = self._make_key(ev)
            executed = key in self.executed_keys
            failed = key in self.failed_keys
            executed_at = self.executed_at_map.get(key)
            time_str = self._format_time(ev.time_sec)
            remain_str = self._format_remain(ev.time_sec, now_sec)
            st = "done" if executed else ("fail" if failed else "pending")
            if ev.kind == "spacer":
                tag = "sep"
                st = ""
                remain_str = ""
            else:
                tag = ""
                if failed:
                    tag = "fail"
                elif executed:
                    # keep gray for past-done, green only for recent
                    is_recent = executed_at is not None and (time.time() - executed_at) <= 5.0
                    if is_recent:
                        tag = "done"
                    elif now_sec is not None and ev.time_sec < now_sec:
                        tag = "donepast"
                    else:
                        tag = "done"

            if ev.kind == "spacer":
                vals_tuple = ("", "", "", "", "", "")
                tag_tuple = ()
            else:
                vals_tuple = (
                    time_str,
                    remain_str,
                    ev.kind,
                    ev.bcode if ev.bcode is not None else "",
                    ev.label,
                    st,
                )
                tag_tuple = (tag,) if tag else ()

            tree_id = self.tree.insert("", tk.END, values=vals_tuple, tags=tag_tuple)

            self.events.append(
                EventState(
                    event=ev,
                    enabled=(ev.kind != "spacer"),
                    executed=executed,
                    failed=failed,
                    executed_at=executed_at,
                    tree_id=tree_id,
                )
            )

        # fire any immediately due events (to avoid first-event miss)
        try:
            self._check_and_fire_events()
        except Exception as exc:
            self.log(f"[ui] ERROR during immediate fire: {exc}")

        # send earliest pending MH sequence immediately (not time-based)
        next_mh_seq = self._pick_next_mh_sequence_block()
        if next_mh_seq:
            k0 = self._make_key(next_mh_seq.event)
            if k0 not in self.sent_mh_seq:
                next_mh_seq.sending = True
                self.sent_mh_seq.add(k0)
                self._enqueue_worker(self._do_mh_sequence, next_mh_seq, next_mh_seq.event.meta)

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------
    def _run_loop(self) -> None:
        try:
            if self.running:
                self._check_and_fire_events()
            self._refresh_remaining()
        except Exception as exc:
            self.log(f"[run] ERROR: {exc}")
        finally:
            self.after(self.RUN_INTERVAL_MS, self._run_loop)

    def _current_clock_sec(self) -> Optional[float]:
        if self.vmix_replay_sec is not None:
            return self._quantize(float(self.vmix_replay_sec))
        # Fallback: wall clock (seconds-of-day) to avoid stuck pending if vMix unavailable
        lt = time.localtime()
        return self._quantize(float(lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec))

    def _refresh_remaining(self) -> None:
        now_sec = self._current_clock_sec()
        for st in self.events:
            if st.tree_id is None:
                continue
            if st.event.kind == "spacer":
                continue
            remain = self._format_remain(st.event.time_sec, now_sec)
            vals = list(self.tree.item(st.tree_id, "values"))
            if len(vals) >= 6:
                vals[1] = remain
                vals[5] = "done" if st.executed else ("fail" if st.failed else "pending")
                tag = ""
                if st.failed:
                    tag = "fail"
                elif st.executed:
                    recent = False
                    if st.executed_at is not None:
                        recent = (time.time() - st.executed_at) <= 5.0
                    if recent:
                        tag = "done"  # keep green flash for 5s
                    elif now_sec is not None and st.event.time_sec < now_sec:
                        tag = "donepast"
                    else:
                        tag = "done"
                else:
                    try:
                        rem_val = float(st.event.time_sec - (now_sec or 0))
                    except Exception:
                        rem_val = 999
                    if rem_val <= 10:
                        tag = "soon"
                self.tree.item(st.tree_id, values=vals, tags=(tag,) if tag else ())

    def _check_and_fire_events(self) -> None:
        now_sec = self._current_clock_sec()
        if now_sec is None:
            return
        tol = 0.6  # tolerance window (wider to avoid misses; 3x quant step)
        catchup_tol = 5.0  # allow late catch-up for missed events (startup)
        for st in self.events:
            if st.executed or st.sending or not st.enabled:
                continue
            delta = st.event.time_sec - now_sec
            if abs(delta) > tol and not (delta < 0 and abs(delta) <= catchup_tol):
                continue  # outside window, wait

            st.sending = True
            self._execute_event(st)
            # if GTO-W End (8/17), schedule CSV update after 5s
            if st.event.kind == "GTO-W" and st.event.bcode in (8, 17):
                self.after(
                    5000,
                    lambda st=st: self._enqueue_worker(
                        self._do_gtow_csv_update, st, {}
                    ),
                )

    def _execute_event(self, st: EventState) -> None:
        ev = st.event

        if ev.kind in ("GTO-W", "MysteryHands", "BlindsUp", "BreakSkip", "bcode") and ev.bcode is not None:
            self._enqueue_worker(self._do_bcode, st, ev.bcode, ev.meta)
        elif ev.kind == "gtow_csv_update":
            self._enqueue_worker(self._do_gtow_csv_update, st, ev.meta)
        elif ev.kind == "mh_prepare":
            self._enqueue_worker(self._do_mh_prepare, st, ev.meta)
        elif ev.kind == "mh_sequence":
            self._enqueue_worker(self._do_mh_sequence, st, ev.meta)

    # ------------------------------------------------------------------
    # Worker / jobs
    # ------------------------------------------------------------------
    def _enqueue_worker(self, func, *args, **kwargs) -> None:
        self.worker_q.put((func, args, kwargs))

    def _worker_loop(self) -> None:
        while True:
            func, args, kwargs = self.worker_q.get()
            try:
                func(*args, **kwargs)
            except Exception as exc:
                msg = f"[worker] ERROR: {exc}"
                self._schedule_on_main(lambda m=msg: self.log(m))

    def _schedule_on_main(self, func) -> None:
        """Run func on Tk main thread and log errors."""

        def wrapper():
            try:
                func()
            except Exception as exc:
                self.log(f"[ui] ERROR: {exc}")

        self.after(0, wrapper)

    def _mark_fail(self, st: EventState, msg: str) -> None:
        st.failed = True
        key = self._make_key(st.event)
        self.failed_keys.add(key)
        self.log(msg)
        if st.tree_id is not None:
            vals = list(self.tree.item(st.tree_id, "values"))
            if len(vals) >= 6:
                vals[5] = "fail"
            self.tree.item(st.tree_id, values=vals, tags=("fail",))

    # ------------------------------------------------------------------
    # Companion / CSV / MH
    # ------------------------------------------------------------------
    def _do_bcode(self, st: EventState, bcode: int, meta: Dict) -> None:
        ip = str(self.cfg.get("companion_ip") or "").strip()
        port = str(self.cfg.get("companion_port") or "").strip()
        if not ip or not port:
            self._schedule_on_main(
                lambda: self.log(f"[BCode] Companion IP/port not set (b={bcode})")
            )
            st.sending = False
            return

        try:
            b_int = int(bcode)
        except Exception:
            self._schedule_on_main(lambda: self.log(f"[BCode] invalid code: {bcode}"))
            st.sending = False
            return

        page, btn = divmod(b_int - 1, 32)
        url = f"http://{ip}:{port}/press/bank/{page + 1}/{btn + 1}"
        seat_raw = meta.get("seat") if isinstance(meta, dict) else None
        seat_mapped = None
        if isinstance(meta, dict):
            seat_mapped = meta.get("seat_mapped")
        try:
            if seat_mapped is None and seat_raw is not None and str(seat_raw).isdigit():
                seat_mapped = ggm_logic_csv.map_seatindex_to_table(int(seat_raw))
        except Exception:
            seat_mapped = None
        try:
            with urllib.request.urlopen(url, timeout=0.8) as resp:
                resp.read(1)
            def on_ok():
                st.executed = True
                st.sending = False
                st.failed = False
                key = self._make_key(st.event)
                self.executed_keys.add(key)
                if key in self.failed_keys:
                    self.failed_keys.discard(key)
                st.executed_at = time.time()
                self.executed_at_map[key] = st.executed_at
                seat_disp = seat_mapped if seat_mapped is not None else seat_raw
                msg = f"[BCode] ok b={b_int} seat={seat_disp}"
                self.log(msg)
                if st.tree_id is not None:
                    vals = list(self.tree.item(st.tree_id, "values"))
                    if len(vals) >= 6:
                        vals[5] = "done"
                    self.tree.item(st.tree_id, values=vals, tags=("done",))
            self._schedule_on_main(on_ok)
        except urllib.error.URLError as e:
            err = str(e)
            self._schedule_on_main(
                lambda err=err, url=url: self._mark_fail(st, f"[BCode] fail b={b_int} seat={seat_mapped or seat_raw} err={err}")
            )
        except Exception as e:
            err = str(e)
            self._schedule_on_main(
                lambda err=err, url=url: self._mark_fail(st, f"[BCode] fail b={b_int} seat={seat_mapped or seat_raw} err={err}")
            )
        finally:
            if not st.executed:
                st.sending = False

    def _do_gtow_csv_update(self, st: EventState, meta: Dict) -> None:
        try:
            res = None
            target_group: list[EventState] | None = None
            block_key: tuple | None = None
            block_idx: int | None = None

            # Choose the earliest GTO-W block whose events are 모두 미실행/미실패 상태
            def _pick_next_gtow_block() -> list[EventState] | None:
                groups: list[list[EventState]] = []
                cur: list[EventState] = []
                for evs in self.events:
                    if evs.event.kind == "spacer":
                        if cur:
                            groups.append(cur)
                            cur = []
                        continue
                    if evs.event.kind == "GTO-W":
                        cur.append(evs)
                if cur:
                    groups.append(cur)

                candidates: list[tuple[float, tuple, list[EventState], set[tuple[str, str]]]] = []
                for grp in groups:
                    if not grp:
                        continue
                    times = [g.event.time_sec for g in grp if g.event.time_sec is not None]
                    if not times:
                        continue
                    start_t = min(times)

                    # Block identity: block_index if present, else sheet/row pairs, else start time
                    blk_idx = None
                    if grp:
                        try:
                            blk_idx = int(grp[0].event.meta.get("block_index"))
                        except Exception:
                            blk_idx = None
                    row_pairs: set[tuple[str, str]] = set()
                    for g in grp:
                        sh = str(g.event.meta.get("sheet") or "").strip()
                        rw = str(g.event.meta.get("row") or "").strip()
                        if sh or rw:
                            row_pairs.add((sh, rw))
                    if blk_idx is not None:
                        blk_key: tuple = ("blk", blk_idx)
                    elif row_pairs:
                        blk_key = tuple(sorted(row_pairs))
                    else:
                        blk_key = (start_t,)

                    # Skip if already handled
                    if blk_key in self.gtow_csv_done:
                        continue

                    # Only blocks where 아무 이벤트도 실행/실패되지 않은 경우만 대상
                    if not all((not g.executed and not g.failed) for g in grp):
                        continue

                    candidates.append((start_t, blk_key, grp, row_pairs))

                if not candidates:
                    return None
                candidates.sort(key=lambda x: x[0])
                return candidates[0][1:]

            picked = _pick_next_gtow_block()
            if picked:
                block_key = picked[0]
                target_group = picked[1]
                target_rows = picked[2]
                try:
                    block_idx = int(target_group[0].event.meta.get("block_index"))
                except Exception:
                    block_idx = None
            else:
                target_group = None
                target_rows = set()

            if self.last_rows and target_group:
                offset = self.daily_diff_seconds + self.serialize_time_offset_seconds
                row_filter: set[tuple[str, str]] = set()
                row_filter.update(target_rows)

                hero_slot = vill_slot = hero_csv = vill_csv = None
                try:
                    hero_slot, vill_slot, hero_csv, vill_csv = ggm_logic_csv.build_gtow_csv_from_rows(
                        self.last_rows, offset, row_filter if row_filter else None, block_index=block_idx
                    )
                except Exception as e:
                    self._schedule_on_main(lambda m=f"[GTO-W CSV] serialize-based failed: {e}": self.log(m))
                if hero_slot and vill_slot and hero_csv is not None and vill_csv is not None:
                    cfg = self.cfg
                    csv_dir = Path(cfg.get("csv_dir") or ggm_io.DEFAULT_CONFIG["csv_dir"])
                    rows = int(cfg.get("rows") or 10)
                    res = ggm_io.write_all(csv_dir, rows, hero_slot, vill_slot, hero_csv, vill_csv, cfg)
                    # try to update position CSV via WebApp (same as CSVd)
                    gs_url = cfg.get("gto_csv_url") or ""
                    if gs_url:
                        try:
                            jpos = ggm_io.fetch_next_gto_block(gs_url)
                            posinfo = ggm_io.write_positions(
                                csv_dir,
                                jpos.get("csvPos") or {},
                                hero_slot=hero_slot,
                                vill_slot=vill_slot,
                            )
                            if posinfo:
                                self._schedule_on_main(lambda: self.log("[GTO-W CSV] pos saved"))
                        except Exception as e:
                            self._schedule_on_main(lambda m=f"[GTO-W CSV] pos fetch failed: {e}": self.log(m))
            if res is None and target_group is None:
                self._schedule_on_main(
                    lambda: self.log("[GTO-W CSV] skipped: no unexecuted GTO-W block")
                )
                st.sending = False
                return
            if res is None:
                res = ggm_io.run_gtow_csv_update(self.cfg)
            if res:
                def on_ok():
                    self.log(
                        f"[GTO-W CSV] hero={res.get('hero')} "
                        f"villain={res.get('villain')}"
                    )
                    if block_key is not None:
                        self.gtow_csv_done.add(block_key)
                    st.executed = True
                    st.sending = False
                    st.failed = False
                    if st.event.kind != "gtow_csv_init":
                        key = self._make_key(st.event)
                        self.executed_keys.add(key)
                        if key in self.failed_keys:
                            self.failed_keys.discard(key)
                        st.executed_at = time.time()
                        self.executed_at_map[key] = st.executed_at
                        if st.tree_id is not None:
                            vals = list(self.tree.item(st.tree_id, "values"))
                            if len(vals) >= 6:
                                vals[5] = "done"
                            self.tree.item(st.tree_id, values=vals, tags=("done",))
                self._schedule_on_main(on_ok)
        except Exception as exc:
            # Log but do not mark as fail to avoid false red status; keep pending
            self._schedule_on_main(
                lambda m=f"[GTO-W CSV] ERROR: {exc}": self.log(m)
            )
            st.sending = False
            st.failed = True

    def _do_mh_prepare(self, st: EventState, meta: Dict) -> None:
        plan = meta.get("plan") or {}
        try:
            ggm_io.send_mh_plan(plan, self.cfg)
            def on_ok():
                self.log(
                    f"[MH] plan sent "
                    f"orange={plan.get('orange_sequence')} "
                    f"open={plan.get('initial_open_count')}"
                )
                st.executed = True
                st.sending = False
                st.failed = False
                key = self._make_key(st.event)
                self.executed_keys.add(key)
                if key in self.failed_keys:
                    self.failed_keys.discard(key)
                st.executed_at = time.time()
                self.executed_at_map[key] = st.executed_at
                seat_disp = seat_mapped if seat_mapped is not None else seat_raw
                lbl = meta.get("label_short") or meta.get("label")
                msg = f"[BCode] ok b={b_int} seat={seat_disp} label={lbl}"
                self.log(msg)
                if st.tree_id is not None:
                    vals = list(self.tree.item(st.tree_id, "values"))
                    if len(vals) >= 6:
                        vals[5] = "done"
                    self.tree.item(st.tree_id, values=vals, tags=("done",))
            self._schedule_on_main(on_ok)
        except Exception as exc:
            self._schedule_on_main(
                lambda m=f"[MH] ERROR: {exc}": self._mark_fail(st, m)
            )
            st.sending = False

    def _do_mh_sequence(self, st: EventState, meta: Dict) -> None:
        seq = meta.get("mh_sequence") or []
        if not seq:
            st.sending = False
            return
        try:
            payload = {
                "orange_sequence": seq,
                "initial_open_count": 0,
                "players_count": len(seq),
                "always_open_seat": None,
            }
            ggm_io.send_mh_plan(payload, self.cfg)
            def on_ok():
                self.log(f"[MH] sequence sent: {seq}")
                st.executed = True
                st.sending = False
                st.failed = False
                key = self._make_key(st.event)
                self.executed_keys.add(key)
                if key in self.sent_mh_seq:
                    self.sent_mh_seq.discard(key)
                st.executed_at = time.time()
                self.executed_at_map[key] = st.executed_at
                if st.tree_id is not None:
                    vals = list(self.tree.item(st.tree_id, "values"))
                    if len(vals) >= 6:
                        vals[5] = "done"
                    self.tree.item(st.tree_id, values=vals, tags=("done",))
            self._schedule_on_main(on_ok)
        except Exception as exc:
            self._schedule_on_main(
                lambda m=f"[MH] seq ERROR: {exc}": self._mark_fail(st, m)
            )
            st.sending = False
            key = self._make_key(st.event)
            if key in self.sent_mh_seq:
                self.sent_mh_seq.discard(key)

    # ------------------------------------------------------------------
    # vMix polling
    # ------------------------------------------------------------------
    def _poll_vmix_loop(self) -> None:
        def job():
            ip = self.vmix_ip
            port = self.vmix_port or "8088"
            if not ip:
                self._schedule_on_main(lambda: self._update_vmix_state(None, ""))
                return

            url = f"http://{ip}:{port}/api/"
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:
                    data = resp.read()
                sec, raw = self._parse_vmix_timecode(data)
                self._schedule_on_main(lambda: self._update_vmix_state(sec, raw))
            except Exception as exc:
                err = str(exc)
                self._schedule_on_main(lambda err=err: self._update_vmix_state(None, err))

        threading.Thread(target=job, name="vmix-poll", daemon=True).start()
        self.after(self.VMIX_POLL_INTERVAL_MS, self._poll_vmix_loop)

    def _poll_status_loop(self) -> None:
        def job():
            try:
                comp_ok = False
                if self.companion_ip and self.companion_port:
                    try:
                        url = f"http://{self.companion_ip}:{self.companion_port}/"
                        with urllib.request.urlopen(url, timeout=0.5) as resp:
                            comp_ok = (resp.status == 200)
                    except Exception:
                        comp_ok = False

                vmix_ok = self.vmix_replay_sec is not None
                if vmix_ok and self._vmix_last_tick is not None:
                    if (time.time() - self._vmix_last_tick) > 3.0:
                        vmix_ok = False

                def apply():
                    self.status_comp_ok = comp_ok
                    self.status_vmix_ok = vmix_ok
                    self.comp_status_label.configure(foreground=("green" if comp_ok else "red"))
                    self.vmix_status_label.configure(foreground=("green" if vmix_ok else "red"))
                    self.comp_status_var.set(f"Companion {self._status_symbol}")
                    self.vmix_status_var.set(f"vMix {self._status_symbol}")

                self._schedule_on_main(apply)
            except Exception:
                # keep loop alive even if status check fails
                self._schedule_on_main(lambda: None)

        threading.Thread(target=job, name="status-poll", daemon=True).start()
        self.after(self.STATUS_POLL_MS, self._poll_status_loop)

    def _parse_vmix_timecode(self, xml_bytes: bytes) -> tuple[Optional[float], Optional[str]]:
        try:
            root = ET.fromstring(xml_bytes)
        except Exception:
            return None, None
        node = root.find(".//replay/timecode")
        if node is None or node.text is None:
            return None, None
        txt = node.text.strip()
        if not txt:
            return None, None

        # Remove date prefix and trailing fractional/zone
        if "T" in txt:
            txt = txt.split("T", 1)[1]
        txt = txt.rstrip("Z")
        parts = txt.replace(";", ":").split(":")
        if len(parts) < 3:
            return None, None
        try:
            h, m = int(parts[0]), int(parts[1])
            s_part = parts[2]
            s = float(s_part)
        except ValueError:
            return None, None
        sec = self._quantize(h * 3600 + m * 60 + s)
        return sec, f"{h:02d}:{m:02d}:{s:05.1f}"

    def _update_vmix_state(self, sec: Optional[float], raw_str: Optional[str]) -> None:
        self.vmix_replay_sec = sec if sec is None else float(sec)
        if sec is None:
            self.vmix_time_var.set("vMix: --:--:--")
            self._vmix_last_error = raw_str or None
            self.vmix_status_label.configure(foreground="red")
            self._vmix_last_tick = None
            self.vmix_status_var.set(f"vMix {self._status_symbol}")
        else:
            self.vmix_replay_str = self._format_time(sec)
            self.vmix_time_var.set(f"vMix: {self.vmix_replay_str}")
            self._vmix_last_error = None
            self.vmix_status_label.configure(foreground="green")
            self._vmix_last_tick = time.time()
            self.vmix_status_var.set(f"vMix {self._status_symbol}")

    def _update_fetch_state(self, ok: bool) -> None:
        self.status_fetch_ok = ok
        self.fetch_status_label.configure(foreground=("green" if ok else "red"))
        self.fetch_status_var.set(f"Fetch {self._status_symbol}")


def main() -> None:
    app = TimelineApp()
    app.geometry("820x540")
    app.mainloop()


if __name__ == "__main__":
    main()
