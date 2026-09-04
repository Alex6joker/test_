from __future__ import annotations

import ast
import math
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

EPS = 1e-9
FIELD_RE = re.compile(r"\|\s+(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s=\s(?P<value>.*)$")
EVENT_RE = re.compile(r"\[(?P<event>[A-Z0-9_]+)\]")


def scalar(value: str) -> Any:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    if value in ("True", "False"):
        return value == "True"
    if value in ("None", "NoneType"):
        return None
    try:
        if any(ch in value for ch in ".eE"):
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_field_line(line: str):
    # BacktestLogger emits: ... |     key = value
    marker = "|     "
    pos = line.find(marker)
    if pos < 0:
        return None
    payload = line[pos + len(marker):]
    sep = payload.find(" = ")
    if sep < 0:
        return None
    return payload[:sep], scalar(payload[sep + 3:])


def same(a: float, b: float) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-8)


def crosses(start: float, end: float, level: float, direction: str) -> bool:
    if direction == "UP":
        return start - EPS <= level <= end + EPS
    if direction == "DOWN":
        return start + EPS >= level >= end - EPS
    return same(start, level)


def parse_trade(line: str):
    payload = line.split(" | TRADE | ", 1)[1].strip()
    event, _, rest = payload.partition(" ")
    fields = {}
    for item in rest.split("; "):
        if " = " in item:
            k, v = item.split(" = ", 1)
            fields[k.strip()] = scalar(v)
    return event, fields


@dataclass
class Phase:
    trade: int
    bar: int
    phase: int
    start: float
    end: float
    direction: str
    entry: float
    tp: float
    sl: float
    step: int
    seq: int


@dataclass
class Trail:
    trade: int
    step: int
    trigger_pct: float
    stop_pct: float
    old_sl: float
    new_sl: float
    trigger: float
    seq: int
    phase: Optional[Phase]


@dataclass
class Crossing:
    trade: int
    bar: int
    phase: int
    reason: str
    price: float
    sl: float
    tp: float
    slippage: float
    seq: int
    phase_obj: Optional[Phase]


@dataclass
class Entry:
    trade: int
    direction: str
    price: float
    size: int
    commission: float
    seq: int


@dataclass
class ExitSignal:
    trade: int
    reason: str
    bar: int
    phase: int
    detected: float
    level: float
    target: float
    seq: int


@dataclass
class ExitExecuted:
    trade: int
    reason: str
    price: float
    target: float
    slippage: float
    size: int
    seq: int


@dataclass
class Closed:
    trade: int
    direction: str
    size: int
    entry: float
    exit: float
    gross: float
    entry_commission: float
    exit_commission: float
    net: float
    reason: str
    seq: int


class AuditData:
    def __init__(self):
        self.params = {}
        self.phases = []
        self.trails = []
        self.crossings = []
        self.entries = []
        self.exit_signals = []
        self.executed = []
        self.closed = []
        self.result = {}
        self.trail_params = []


def load_log(path: str) -> AuditData:
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Файл не найден: {path}")

    d = AuditData()
    current_phase = None
    seq = 0

    with open(path, "r", encoding="utf-8", errors="replace", buffering=1024 * 1024) as f:
        pending_event = None
        pending = {}
        pending_seq = 0

        def process(event, fields, event_seq):
            nonlocal current_phase
            if event == "STRATEGY_INIT":
                d.params.update(fields)
                raw_steps = fields.get("dynamic_trail_steps")
                if isinstance(raw_steps, str):
                    try:
                        raw_steps = ast.literal_eval(raw_steps)
                    except (ValueError, SyntaxError):
                        raw_steps = []
                if isinstance(raw_steps, (list, tuple)):
                    d.trail_params = [(float(a), float(b)) for a, b in raw_steps]
                return
            if event == "INTRABAR_PHASE":
                req = ("trade_id","bar_index","phase_index","start_price","end_price","direction","entry_price","tp_level","sl_level","current_trail_step")
                missing = [x for x in req if x not in fields]
                if missing:
                    raise ValueError(f"INTRABAR_PHASE: отсутствуют поля {missing}")
                current_phase = Phase(
                    int(fields["trade_id"]), int(fields["bar_index"]), int(fields["phase_index"]),
                    float(fields["start_price"]), float(fields["end_price"]), str(fields["direction"]),
                    float(fields["entry_price"]), float(fields["tp_level"]), float(fields["sl_level"]),
                    int(fields["current_trail_step"]), event_seq)
                d.phases.append(current_phase)
                return
            if event == "EXIT_CROSSING":
                req = ("trade_id","bar_index","phase_index","event_type","crossing_price","sl_level","tp_level","slippage")
                missing = [x for x in req if x not in fields]
                if missing:
                    raise ValueError(f"EXIT_CROSSING: отсутствуют поля {missing}")
                d.crossings.append(Crossing(
                    int(fields["trade_id"]), int(fields["bar_index"]), int(fields["phase_index"]),
                    str(fields["event_type"]), float(fields["crossing_price"]), float(fields["sl_level"]),
                    float(fields["tp_level"]), float(fields["slippage"]), event_seq, current_phase))

        for raw in f:
            seq += 1
            line = raw.rstrip("\n")

            # TRADE records may immediately follow a debug block. Flush that block
            # first so TRAIL_UPDATE/EXIT records inherit the exact phase that
            # preceded them.
            if " | TRADE | " in line:
                if pending_event is not None:
                    process(pending_event, pending, pending_seq)
                    pending_event = None
                    pending = {}
                event, x = parse_trade(line)
                if event == "ENTRY_EXECUTED":
                    d.entries.append(Entry(int(x["trade_id"]), str(x["direction"]), float(x["execution_price"]), int(x["executed_size"]), float(x["entry_commission"]), seq))
                elif event == "TRAIL_UPDATE":
                    d.trails.append(Trail(int(x["trade_id"]), int(x["step_idx"]), float(x["trigger_pct"]), float(x["stop_pct"]), float(x["old_sl_level"]), float(x["new_sl_level"]), float(x["trigger_cross_price"]), seq, current_phase))
                elif event == "EXIT_SIGNAL":
                    d.exit_signals.append(ExitSignal(int(x["trade_id"]), str(x["reason"]), int(x["bar_index"]), int(x["phase_index"]), float(x["detected_price"]), float(x["level"]), float(x["target_exec_price"]), seq))
                elif event == "EXIT_EXECUTED":
                    d.executed.append(ExitExecuted(int(x["trade_id"]), str(x["reason"]), float(x["execution_price"]), float(x["target_exec_price"]), float(x["exit_slippage"]), int(x["executed_size"]), seq))
                elif event == "TRADE_CLOSED":
                    d.closed.append(Closed(int(x["trade_id"]), str(x["direction"]), int(x["size"]), float(x["entry_price"]), float(x["exit_price"]), float(x["gross_pnl"]), float(x["entry_commission"]), float(x["exit_commission"]), float(x["net_pnl"]), str(x["reason"]), seq))
                continue

            # Only these debug event headers matter for the forensic audit.
            if "[INTRABAR_PHASE]" in line or "[EXIT_CROSSING]" in line or "[STRATEGY_INIT]" in line:
                if pending_event is not None:
                    process(pending_event, pending, pending_seq)
                pending_event = line[line.find("[") + 1:line.find("]")]
                pending = {}
                pending_seq = seq
                continue

            if pending_event is not None:
                parsed = parse_field_line(line)
                if parsed is not None:
                    k, v = parsed
                    pending[k] = v
                    continue
                process(pending_event, pending, pending_seq)
                pending_event = None
                pending = {}

        if pending_event is not None:
            process(pending_event, pending, pending_seq)

    return d


def result(name, checked, errors):
    return {"name": name, "checked": checked, "errors": errors}


def audit(data: AuditData):
    entries = {x.trade: x for x in data.entries}
    trails_by_trade = defaultdict(list)
    for x in data.trails:
        trails_by_trade[x.trade].append(x)
    crossings_by_trade = defaultdict(list)
    for x in data.crossings:
        crossings_by_trade[x.trade].append(x)

    errors = []
    checked = 0
    trail_errors = []
    multi_errors = []
    retro_errors = []

    for trade, trails in trails_by_trade.items():
        trails.sort(key=lambda x: x.seq)
        entry = entries.get(trade)
        previous_step = -1
        previous_sl = None
        for t in trails:
            checked += 1
            p = t.phase
            if not entry:
                trail_errors.append(f"trade_id={trade}: TRAIL_UPDATE без ENTRY_EXECUTED")
                continue
            if p is None or p.trade != trade:
                trail_errors.append(f"trade_id={trade} step={t.step}: TRAIL_UPDATE не привязан к правильной INTRABAR_PHASE")
                continue
            if t.step != previous_step + 1:
                trail_errors.append(f"trade_id={trade}: step={t.step}, ожидался {previous_step + 1}")
            previous_step = t.step
            if previous_sl is not None and not same(t.old_sl, previous_sl):
                trail_errors.append(f"trade_id={trade} step={t.step}: old_sl={t.old_sl} != previous_new_sl={previous_sl}")
            if not crosses(p.start, p.end, t.trigger, p.direction):
                trail_errors.append(f"trade_id={trade} step={t.step}: trigger={t.trigger} не пересечён {p.start}->{p.end} ({p.direction})")
            tp_distance = float(data.params.get("tp", abs(entry.price - p.tp)))
            expected_trigger = entry.price - tp_distance * t.trigger_pct if entry.direction == "SHORT" else entry.price + tp_distance * t.trigger_pct
            expected_trigger = round(expected_trigger, 2)
            if abs(t.trigger - expected_trigger) > 0.011:
                trail_errors.append(f"trade_id={trade} step={t.step}: trigger={t.trigger}, expected={expected_trigger}")
            expected_sl = entry.price - tp_distance * t.stop_pct if entry.direction == "SHORT" else entry.price + tp_distance * t.stop_pct
            expected_sl = round(expected_sl, 2)
            if not same(t.new_sl, expected_sl):
                trail_errors.append(f"trade_id={trade} step={t.step}: new_sl={t.new_sl}, expected={expected_sl}")
            if entry.direction == "LONG" and t.new_sl + EPS < t.old_sl:
                trail_errors.append(f"trade_id={trade} step={t.step}: LONG SL ухудшен")
            if entry.direction == "SHORT" and t.new_sl - EPS > t.old_sl:
                trail_errors.append(f"trade_id={trade} step={t.step}: SHORT SL ухудшен")
            for c in crossings_by_trade[trade]:
                if c.seq < t.seq and c.bar == p.bar and c.phase == p.phase and c.reason == "STOP_LOSS" and same(c.sl, t.new_sl):
                    retro_errors.append(f"trade_id={trade} bar={p.bar} phase={p.phase} step={t.step}: новый SL использован до TRAIL_UPDATE")
            previous_sl = t.new_sl

    # Multiple trail steps: verify both step order and physical crossing order.
    by_phase = defaultdict(list)
    for t in data.trails:
        if t.phase:
            by_phase[(t.trade, t.phase.bar, t.phase.phase)].append(t)
    for key, ts in by_phase.items():
        if len(ts) < 2:
            continue
        ts.sort(key=lambda x: x.seq)
        steps = [x.step for x in ts]
        if steps != list(range(steps[0], steps[0] + len(steps))):
            multi_errors.append(f"trade_id={key[0]} bar={key[1]} phase={key[2]}: steps={steps}")
        prices = [x.trigger for x in ts]
        p = ts[0].phase
        if p.direction == "DOWN" and any(prices[i] < prices[i+1] - EPS for i in range(len(prices)-1)):
            multi_errors.append(f"trade_id={key[0]} bar={key[1]} phase={key[2]}: trigger order против DOWN path: {prices}")
        if p.direction == "UP" and any(prices[i] > prices[i+1] + EPS for i in range(len(prices)-1)):
            multi_errors.append(f"trade_id={key[0]} bar={key[1]} phase={key[2]}: trigger order против UP path: {prices}")

    exit_errors = []
    for c in data.crossings:
        if c.phase_obj is None:
            exit_errors.append(f"trade_id={c.trade}: EXIT_CROSSING без INTRABAR_PHASE")
            continue
        p = c.phase_obj
        level = c.sl if c.reason == "STOP_LOSS" else c.tp if c.reason == "TAKE_PROFIT" else None
        if level is None:
            exit_errors.append(f"trade_id={c.trade}: неизвестный reason={c.reason}")
            continue
        if not same(c.price, level):
            exit_errors.append(f"trade_id={c.trade}: crossing_price={c.price} != level={level}")
        if not crosses(p.start, p.end, level, p.direction):
            exit_errors.append(f"trade_id={c.trade} bar={c.bar} phase={c.phase}: {c.reason} level={level} не пересечён {p.start}->{p.end}")

    exec_errors = []
    signals = defaultdict(list)
    executed = defaultdict(list)
    for x in data.exit_signals: signals[x.trade].append(x)
    for x in data.executed: executed[x.trade].append(x)
    for trade, ss in signals.items():
        if len(ss) != 1 or len(executed[trade]) != 1:
            exec_errors.append(f"trade_id={trade}: EXIT_SIGNAL={len(ss)}, EXIT_EXECUTED={len(executed[trade])}")
            continue
        s, e = ss[0], executed[trade][0]
        if e.seq <= s.seq: exec_errors.append(f"trade_id={trade}: EXIT_EXECUTED не после EXIT_SIGNAL")
        if s.reason != e.reason: exec_errors.append(f"trade_id={trade}: reason mismatch")
        crossing = next((c for c in data.crossings if c.trade == trade and c.bar == s.bar and c.phase == s.phase), None)
        if crossing is None:
            exec_errors.append(f"trade_id={trade}: EXIT_SIGNAL без EXIT_CROSSING")
        else:
            direction = entries[trade].direction
            expected_target = crossing.price + crossing.slippage if direction == "SHORT" else crossing.price - crossing.slippage
            expected_target = round(expected_target, 2)
            if not same(s.target, expected_target): exec_errors.append(f"trade_id={trade}: target_exec={s.target}, expected={expected_target}")
            if not same(e.slippage, crossing.slippage): exec_errors.append(f"trade_id={trade}: exit_slippage mismatch")
        if not same(e.price, e.target): exec_errors.append(f"trade_id={trade}: execution_price={e.price} != target_exec_price={e.target}")

    lifecycle_errors = []
    closed = {x.trade: x for x in data.closed}
    for trade, e in entries.items():
        if trade in closed:
            c = closed[trade]
            if c.seq <= e.seq: lifecycle_errors.append(f"trade_id={trade}: close before entry")
            if c.direction != e.direction or c.size != e.size or not same(c.entry, e.price): lifecycle_errors.append(f"trade_id={trade}: entry/close mismatch")
    for trade in set(closed) - set(entries): lifecycle_errors.append(f"trade_id={trade}: close without entry")


    return [
        result("TRAIL_CAUSALITY", checked, trail_errors),
        result("MULTIPLE_TRAIL_STEPS", len(by_phase), multi_errors),
        result("EXIT_CROSSING_CAUSALITY", len(data.crossings), exit_errors),
        result("NO_RETROACTIVE_SL", len(data.trails), retro_errors),
        result("EXIT_EXECUTION", len(data.exit_signals), exec_errors),
        result("TRADE_LIFECYCLE", len(data.entries), lifecycle_errors),
    ]


def run_causal_audit(log_path: str = os.path.join("logs", "backtest_diagnostic.log")) -> bool:
    print(f"[CAUSAL AUDIT] Чтение лога: {log_path}")
    d = load_log(log_path)
    missing = []
    if not d.params: missing.append("STRATEGY_INIT")
    if not d.phases: missing.append("INTRABAR_PHASE")
    if not d.trails: missing.append("TRAIL_UPDATE")
    if not d.crossings: missing.append("EXIT_CROSSING")
    if not d.entries: missing.append("ENTRY_EXECUTED")
    if missing:
        raise ValueError("В логе отсутствуют необходимые данные: " + ", ".join(missing))

    results = audit(d)
    errors = sum(len(x["errors"]) for x in results)
    print()
    print("=" * 68)
    print("CAUSAL AUDIT — TRAIL / SL / TP")
    print("=" * 68)
    print(f"Trades checked:          {len(d.entries)}")
    print(f"Closed trades checked:   {len(d.closed)}")
    print(f"Trail updates checked:   {len(d.trails)}")
    print(f"Exit crossings checked:  {len(d.crossings)}")
    print()
    for r in results:
        print(f"{r['name']:<28} {'PASS' if not r['errors'] else 'FAIL':<5} checked={r['checked']:<6} errors={len(r['errors'])}")
    print()
    if errors == 0:
        print("CAUSAL AUDIT: PASS")
        return True
    print(f"CAUSAL AUDIT: FAIL   errors={errors}")
    shown = 0
    for r in results:
        for e in r["errors"]:
            shown += 1
            print(f"ERROR #{shown}: {e}")
            if shown >= 100:
                print(f"... ещё ошибок: {errors - shown}")
                return False
    return False


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join("logs", "backtest_diagnostic.log")
    try:
        ok = run_causal_audit(path)
    except Exception as exc:
        print(f"[CAUSAL AUDIT] FAIL: {exc}")
        raise SystemExit(2)
    raise SystemExit(0 if ok else 1)
