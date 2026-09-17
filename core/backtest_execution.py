from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .backtest_bar import Bar


@dataclass(frozen=True, slots=True)
class TrailLevel:
    step_idx: int
    trigger_price: float
    new_sl: float


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    """Immutable execution inputs for one currently-open virtual position."""

    position_size: int
    entry_price: float | None
    sl_level: float | None
    tp_level: float | None
    current_trail_step: int
    trail_levels: tuple[TrailLevel, ...]
    slippage: float


@dataclass(frozen=True, slots=True)
class ExecutionExit:
    """Terminal fill decision produced by the pure execution model."""

    event_type: str
    bar_index: int
    phase_index: int
    direction: int
    size: int
    detected_price: float
    execution_price: float
    slippage: float
    reason: str


# Backward-compatible public name used by existing callers/tests.
ExecutionResult = ExecutionExit


@dataclass(frozen=True, slots=True)
class ExecutionPhase:
    """Result of one deterministic monotonic intrabar segment."""

    phase_index: int
    start_price: float
    end_price: float
    sl_level: float | None = None
    trail_step: int = -1
    trail_updates: tuple[TrailLevel, ...] = ()
    exit: ExecutionExit | None = None
    resulting_sl_level: float | None = None
    resulting_trail_step: int = -1


@dataclass(frozen=True, slots=True)
class ExecutionBarResult:
    """All execution decisions for one bar, in causal order."""

    phases: tuple[ExecutionPhase, ...]
    exit: ExecutionExit | None = None


class ExecutionContext(Protocol):
    """Narrow host adapter used to feed/apply the execution model."""

    def execution_snapshot(self) -> ExecutionSnapshot: ...

    def apply_trail_update(self, update: TrailLevel, current_price: float) -> None: ...

    def apply_execution_exit(self, result: ExecutionExit) -> None: ...

    def price(self, value: float) -> float: ...

    @property
    def trade_id(self) -> int: ...

    @property
    def debug_enabled(self) -> bool: ...

    def wants_debug_event(self, event_name: str) -> bool: ...

    def debug_event(self, event_name: str, **kwargs) -> None: ...


class ExecutionEngine:
    """Pure deterministic virtual intrabar execution model.

    The engine decides *what would execute*; it does not mutate BacktestState,
    TradeLedger, accounting, or logging. A host adapter applies the returned
    trail/exit decisions in the exact order in which they were discovered.
    """

    @staticmethod
    def get_backtest_dynamic_slippage(size: int) -> float:
        if size <= 5:
            return 0.02
        if size <= 15:
            return 0.04
        if size <= 30:
            return 0.07
        return 0.15

    @staticmethod
    def _process_segment(
        snapshot: ExecutionSnapshot,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
    ) -> ExecutionPhase | None:
        position_size = snapshot.position_size
        if not position_size or start_price == end_price:
            return None

        direction = 1 if position_size > 0 else -1
        moving_up = end_price > start_price
        favorable = (direction > 0 and moving_up) or (direction < 0 and not moving_up)

        current_sl = snapshot.sl_level
        tp_level = snapshot.tp_level
        current_step = snapshot.current_trail_step
        events = []

        if favorable:
            for level in snapshot.trail_levels:
                if direction > 0 and start_price < level.trigger_price <= end_price:
                    events.append(("TRAIL", level.trigger_price, level))
                elif direction < 0 and end_price <= level.trigger_price < start_price:
                    events.append(("TRAIL", level.trigger_price, level))

        if current_sl is not None:
            if direction > 0:
                if not moving_up and end_price <= current_sl < start_price:
                    events.append(("STOP_LOSS", current_sl, None))
                elif moving_up and start_price <= current_sl <= end_price:
                    events.append(("STOP_LOSS", current_sl, None))
            else:
                if moving_up and start_price <= current_sl <= end_price:
                    events.append(("STOP_LOSS", current_sl, None))
                elif not moving_up and end_price <= current_sl <= start_price:
                    events.append(("STOP_LOSS", current_sl, None))

        if tp_level is not None:
            if direction > 0 and moving_up and start_price <= tp_level <= end_price:
                events.append(("TAKE_PROFIT", tp_level, None))
            elif direction < 0 and not moving_up and end_price <= tp_level <= start_price:
                events.append(("TAKE_PROFIT", tp_level, None))

        if moving_up:
            events.sort(key=lambda e: (e[1], 0 if e[0] != "TRAIL" else 1))
        else:
            events.sort(key=lambda e: (-e[1], 0 if e[0] != "TRAIL" else 1))

        current_price = start_price
        trail_updates = []
        exit_result = None

        for event_type, event_price, level in events:
            if moving_up and event_price < current_price:
                continue
            if not moving_up and event_price > current_price:
                continue

            current_price = event_price

            if event_type == "TRAIL":
                assert level is not None
                # The historical strategy contract only advances to a trail
                # level when it improves the active stop.
                if direction > 0 and (current_sl is not None and level.new_sl <= current_sl):
                    current_step = max(current_step, level.step_idx)
                    continue
                if direction < 0 and (current_sl is not None and level.new_sl >= current_sl):
                    current_step = max(current_step, level.step_idx)
                    continue
                current_sl = level.new_sl
                current_step = level.step_idx
                trail_updates.append(level)
                continue

            size = abs(position_size)
            slippage = snapshot.slippage
            if event_type == "STOP_LOSS":
                target = current_sl
            else:
                target = tp_level
            if target is None:
                continue

            if direction > 0:
                target_exec_price = target - slippage
            else:
                target_exec_price = target + slippage

            exit_result = ExecutionExit(
                event_type=event_type,
                bar_index=bar_index,
                phase_index=phase_index,
                direction=direction,
                size=size,
                detected_price=current_price,
                execution_price=target_exec_price,
                slippage=slippage,
                reason=event_type,
            )
            break

        return ExecutionPhase(
            phase_index=phase_index,
            start_price=start_price,
            end_price=end_price,
            sl_level=snapshot.sl_level,
            trail_step=snapshot.current_trail_step,
            trail_updates=tuple(trail_updates),
            exit=exit_result,
            resulting_sl_level=current_sl,
            resulting_trail_step=current_step,
        )

    @staticmethod
    def _process_segment_fast(
        snapshot: ExecutionSnapshot,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
    ) -> tuple[tuple[TrailLevel, ...], ExecutionExit | None, float | None, int]:
        """Hot-path segment evaluator without diagnostic phase allocation."""
        position_size = snapshot.position_size
        if not position_size or start_price == end_price:
            return (), None, snapshot.sl_level, snapshot.current_trail_step

        direction = 1 if position_size > 0 else -1
        moving_up = end_price > start_price
        current_sl = snapshot.sl_level
        current_step = snapshot.current_trail_step
        tp_level = snapshot.tp_level

        # Keep the same event ordering as _process_segment, but only allocate
        # the tiny candidate list when a segment actually crosses something.
        candidates = []
        if direction > 0:
            if moving_up:
                for level in snapshot.trail_levels:
                    if start_price < level.trigger_price <= end_price:
                        candidates.append((level.trigger_price, 1, level))
                if current_sl is not None and start_price <= current_sl <= end_price:
                    candidates.append((current_sl, 0, None))
                if tp_level is not None and start_price <= tp_level <= end_price:
                    candidates.append((tp_level, 0, None))
            else:
                if current_sl is not None and end_price <= current_sl < start_price:
                    candidates.append((current_sl, 0, None))
        else:
            if moving_up:
                if current_sl is not None and start_price <= current_sl <= end_price:
                    candidates.append((current_sl, 0, None))
            else:
                for level in snapshot.trail_levels:
                    if end_price <= level.trigger_price < start_price:
                        candidates.append((level.trigger_price, 1, level))
                if current_sl is not None and end_price <= current_sl <= start_price:
                    candidates.append((current_sl, 0, None))
                if tp_level is not None and end_price <= tp_level <= start_price:
                    candidates.append((tp_level, 0, None))

        if not candidates:
            return (), None, current_sl, current_step

        if moving_up:
            candidates.sort(key=lambda e: (e[0], e[1]))
        else:
            candidates.sort(key=lambda e: (-e[0], e[1]))

        current_price = start_price
        trail_updates = []
        slippage = snapshot.slippage

        for event_price, event_kind, level in candidates:
            if moving_up and event_price < current_price:
                continue
            if not moving_up and event_price > current_price:
                continue
            current_price = event_price

            if event_kind == 1:
                if direction > 0:
                    if current_sl is not None and level.new_sl <= current_sl:
                        current_step = max(current_step, level.step_idx)
                        continue
                else:
                    if current_sl is not None and level.new_sl >= current_sl:
                        current_step = max(current_step, level.step_idx)
                        continue
                current_sl = level.new_sl
                current_step = level.step_idx
                trail_updates.append(level)
                continue

            # Both SL and TP candidates have kind 0. Re-identify the crossed
            # level using the same conditions as _process_segment.
            is_sl = current_sl is not None and event_price == current_sl
            is_tp = tp_level is not None and event_price == tp_level
            if not (is_sl or is_tp):
                continue

            event_type = "STOP_LOSS" if is_sl else "TAKE_PROFIT"
            target = current_sl if is_sl else tp_level
            if direction > 0:
                target_exec_price = target - slippage
            else:
                target_exec_price = target + slippage

            exit_result = ExecutionExit(
                event_type=event_type,
                bar_index=bar_index,
                phase_index=phase_index,
                direction=direction,
                size=abs(position_size),
                detected_price=current_price,
                execution_price=target_exec_price,
                slippage=slippage,
                reason=event_type,
            )
            return tuple(trail_updates), exit_result, current_sl, current_step

        return tuple(trail_updates), None, current_sl, current_step

    def process_fast(self, snapshot: ExecutionSnapshot, bar: Bar, bar_index: int) -> tuple[tuple[TrailLevel, ...], ExecutionExit | None]:
        """Hot execution path for non-diagnostic modes.

        It preserves the exact causal phase order but avoids constructing
        ExecutionPhase/ExecutionBarResult for every intrabar phase.
        """
        if not snapshot.position_size:
            return (), None

        b_open = bar.open
        b_high = bar.high
        b_low = bar.low
        b_close = bar.close
        if b_close >= b_open:
            p0, p1, p2, p3 = b_open, b_low, b_high, b_close
        else:
            p0, p1, p2, p3 = b_open, b_high, b_low, b_close

        working = snapshot
        all_updates = []
        for phase_index, (start_price, end_price) in enumerate(((p0, p1), (p1, p2), (p2, p3))):
            updates, exit_result, resulting_sl, resulting_step = self._process_segment_fast(
                working, start_price, end_price, bar_index, phase_index
            )
            if updates:
                all_updates.extend(updates)
                working = ExecutionSnapshot(
                    position_size=working.position_size,
                    entry_price=working.entry_price,
                    sl_level=resulting_sl,
                    tp_level=working.tp_level,
                    current_trail_step=resulting_step,
                    trail_levels=working.trail_levels,
                    slippage=working.slippage,
                )
            if exit_result is not None:
                return tuple(all_updates), exit_result

        return tuple(all_updates), None

    def process(self, snapshot: ExecutionSnapshot, bar: Bar, bar_index: int) -> ExecutionBarResult:
        """Return execution decisions without mutating the host runtime."""
        if not snapshot.position_size:
            return ExecutionBarResult(())

        b_open = bar.open
        b_high = bar.high
        b_low = bar.low
        b_close = bar.close
        bullish_or_doji = b_close >= b_open
        points = (
            (b_open, b_low, b_high, b_close)
            if bullish_or_doji
            else (b_open, b_high, b_low, b_close)
        )

        phases = []
        working = snapshot
        for phase_index in range(len(points) - 1):
            if not working.position_size:
                break
            phase = self._process_segment(
                working, points[phase_index], points[phase_index + 1], bar_index, phase_index
            )
            if phase is None:
                phase = ExecutionPhase(
                    phase_index,
                    points[phase_index],
                    points[phase_index + 1],
                    sl_level=working.sl_level,
                    trail_step=working.current_trail_step,
                    resulting_sl_level=working.sl_level,
                    resulting_trail_step=working.current_trail_step,
                )
            phases.append(phase)

            if phase.trail_updates or phase.resulting_sl_level != working.sl_level or phase.resulting_trail_step != working.current_trail_step:
                working = ExecutionSnapshot(
                    position_size=working.position_size,
                    entry_price=working.entry_price,
                    sl_level=phase.resulting_sl_level,
                    tp_level=working.tp_level,
                    current_trail_step=phase.resulting_trail_step,
                    trail_levels=working.trail_levels,
                    slippage=working.slippage,
                )
            if phase.exit is not None:
                return ExecutionBarResult(tuple(phases), phase.exit)

        return ExecutionBarResult(tuple(phases), None)

    # Compatibility helper for the old mixin/tests. It now operates through a
    # snapshot and applies only the returned decisions via the host adapter.
    def _process_monotonic_segment(
        self,
        context: ExecutionContext,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
        prices_normalized: bool = False,
        position_size: int | None = None,
    ) -> ExecutionResult | None:
        snapshot = context.execution_snapshot()
        if position_size is not None and position_size != snapshot.position_size:
            snapshot = ExecutionSnapshot(
                position_size=position_size,
                entry_price=snapshot.entry_price,
                sl_level=snapshot.sl_level,
                tp_level=snapshot.tp_level,
                current_trail_step=snapshot.current_trail_step,
                trail_levels=snapshot.trail_levels,
                slippage=self.get_backtest_dynamic_slippage(abs(position_size)),
            )
        if not prices_normalized:
            start_price = context.price(start_price)
            end_price = context.price(end_price)
        phase = self._process_segment(snapshot, start_price, end_price, bar_index, phase_index)
        if phase is None:
            return None
        for update in phase.trail_updates:
            context.apply_trail_update(update, update.trigger_price)
        if phase.exit is not None:
            context.apply_execution_exit(phase.exit)
        return phase.exit

    def process_bar(self, context: ExecutionContext, bar: Bar, bar_index: int, segment_processor=None):
        """Process a bar and apply decisions through the narrow host adapter."""
        snapshot = context.execution_snapshot()
        if not snapshot.position_size:
            return None

        if segment_processor is not None:
            # Preserve the legacy test/adapter hook. Production code does not
            # use it; the native path goes through the pure ``process`` API.
            for phase_index in range(3):
                current = context.execution_snapshot()
                if not current.position_size:
                    break
                points = (bar.open, bar.low, bar.high, bar.close) if bar.close >= bar.open else (bar.open, bar.high, bar.low, bar.close)
                start_price, end_price = points[phase_index], points[phase_index + 1]
                if context.debug_enabled:
                    context.debug_event(
                        "INTRABAR_PHASE", trade_id=context.trade_id, bar_index=bar_index,
                        phase_index=phase_index, start_price=context.price(start_price),
                        end_price=context.price(end_price),
                        direction="UP" if end_price > start_price else ("DOWN" if end_price < start_price else "FLAT"),
                        entry_price=current.entry_price, tp_level=current.tp_level,
                        sl_level=current.sl_level, current_trail_step=current.current_trail_step,
                    )
                closed = segment_processor(start_price, end_price, bar_index, phase_index)
                current = context.execution_snapshot()
                if context.debug_enabled:
                    context.debug_event(
                        "TRAIL_EVALUATION", trade_id=context.trade_id, bar_index=bar_index,
                        phase_index=phase_index, position_size=current.position_size,
                        start_price=context.price(start_price), end_price=context.price(end_price),
                        entry_price=current.entry_price, tp_level=current.tp_level, sl_level=current.sl_level,
                        current_trail_step=current.current_trail_step,
                    )
                    context.debug_event(
                        "EXIT_EVALUATION", trade_id=context.trade_id, bar_index=bar_index,
                        phase_index=phase_index, position_size=current.position_size,
                        start_price=context.price(start_price), end_price=context.price(end_price),
                        tp_level=current.tp_level, sl_level=current.sl_level, closed=bool(closed),
                    )
                if closed:
                    return True
            return None

        # NONE/FAST are the production hot path. DIAGNOSTIC keeps the full
        # phase objects because their fields are part of the established log
        # contract.
        debug_enabled = context.debug_enabled
        if not debug_enabled:
            updates, exit_result = self.process_fast(snapshot, bar, bar_index)
            for update in updates:
                context.apply_trail_update(update, update.trigger_price)
            if exit_result is not None:
                context.apply_execution_exit(exit_result)
            return exit_result

        result = self.process(snapshot, bar, bar_index)
        for phase in result.phases:
            if debug_enabled:
                context.debug_event(
                    "INTRABAR_PHASE",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase.phase_index,
                    start_price=phase.start_price,
                    end_price=phase.end_price,
                    direction="UP" if phase.end_price > phase.start_price else (
                        "DOWN" if phase.end_price < phase.start_price else "FLAT"
                    ),
                    entry_price=snapshot.entry_price,
                    tp_level=snapshot.tp_level,
                    sl_level=phase.sl_level,
                    current_trail_step=phase.trail_step,
                )

            for update in phase.trail_updates:
                context.apply_trail_update(update, update.trigger_price)

            if phase.exit is not None:
                context.apply_execution_exit(phase.exit)

            if debug_enabled:
                context.debug_event(
                    "TRAIL_EVALUATION",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase.phase_index,
                    position_size=context.execution_snapshot().position_size,
                    start_price=phase.start_price,
                    end_price=phase.end_price,
                    entry_price=context.execution_snapshot().entry_price,
                    tp_level=context.execution_snapshot().tp_level,
                    sl_level=context.execution_snapshot().sl_level,
                    current_trail_step=context.execution_snapshot().current_trail_step,
                )
                context.debug_event(
                    "EXIT_EVALUATION",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase.phase_index,
                    position_size=context.execution_snapshot().position_size,
                    start_price=phase.start_price,
                    end_price=phase.end_price,
                    tp_level=context.execution_snapshot().tp_level,
                    sl_level=context.execution_snapshot().sl_level,
                    closed=phase.exit is not None,
                )
            if phase.exit is not None:
                return phase.exit
        return None


class BacktestExecutionContext:
    """Production adapter between the pure execution model and runtime state."""

    def __init__(self, strategy) -> None:
        self._strategy = strategy

    def execution_snapshot(self) -> ExecutionSnapshot:
        position_size = self.position_size
        direction = 1 if position_size > 0 else -1
        entry_price = self.entry_price
        cache_entry = getattr(self, "_execution_trail_cache_entry", None)
        cache_direction = getattr(self, "_execution_trail_cache_direction", None)
        cached_levels = getattr(self, "_execution_trail_cache_levels", None)

        if cached_levels is None or cache_entry != entry_price or cache_direction != direction:
            raw_levels = self._strategy._trail_trigger_levels(direction)
            cached_levels = tuple(
                TrailLevel(step_idx, trigger, new_sl)
                for step_idx, trigger, new_sl in raw_levels
            )
            self._execution_trail_cache_entry = entry_price
            self._execution_trail_cache_direction = direction
            self._execution_trail_cache_levels = cached_levels

        return ExecutionSnapshot(
            position_size=position_size,
            entry_price=entry_price,
            sl_level=self.sl_level,
            tp_level=self.tp_level,
            current_trail_step=self.current_trail_step,
            trail_levels=cached_levels,
            slippage=ExecutionEngine.get_backtest_dynamic_slippage(abs(position_size)),
        )

    @property
    def position_size(self) -> int:
        return self._strategy.state.virtual_position_size

    @property
    def entry_price(self) -> float | None:
        return self._strategy.state.virtual_entry_price

    @property
    def sl_level(self) -> float | None:
        return self._strategy.state.sl_level

    @property
    def tp_level(self) -> float | None:
        return self._strategy.state.tp_level

    @property
    def trade_id(self) -> int:
        return self._strategy._ensure_trade_ledger().trade_id

    @property
    def current_trail_step(self) -> int:
        return self._strategy.state.current_trail_step

    def price(self, value: float) -> float:
        return self._strategy._price(value)

    def apply_trail_update(self, update: TrailLevel, current_price: float) -> None:
        self._strategy._apply_trail_step(update.step_idx, update.new_sl, current_price)

    def apply_execution_exit(self, result: ExecutionExit) -> None:
        logger = self._strategy.logger
        if logger.wants_debug_event("EXIT_CROSSING"):
            logger.debug_event(
                "EXIT_CROSSING",
                trade_id=self.trade_id,
                bar_index=result.bar_index,
                phase_index=result.phase_index,
                event_type=result.event_type,
                crossing_price=result.detected_price,
                sl_level=self.sl_level,
                tp_level=self.tp_level,
                slippage=result.slippage,
            )
        self._strategy._close_virtual_position(
            reason=result.reason,
            target_exec_price=result.execution_price,
            detected_price=result.detected_price,
            bar_index=result.bar_index,
            phase_index=result.phase_index,
        )

    @property
    def debug_enabled(self) -> bool:
        logger = self._strategy.logger
        is_diagnostic = getattr(logger, "is_diagnostic", None)
        if is_diagnostic is not None:
            return bool(is_diagnostic)
        return logger.wants_debug_event("BAR")

    def wants_debug_event(self, event_name: str) -> bool:
        return self._strategy.logger.wants_debug_event(event_name)

    def debug_event(self, event_name: str, **kwargs) -> None:
        self._strategy.logger.debug_event(event_name, **kwargs)


class BacktestExecutionMixin:
    """Compatibility layer delegating execution to ExecutionEngine."""

    def _get_execution_engine(self) -> ExecutionEngine:
        engine = getattr(self, "_execution_engine", None)
        if engine is None:
            engine = ExecutionEngine()
            self._execution_engine = engine
        return engine

    def _get_execution_context(self) -> BacktestExecutionContext:
        context = getattr(self, "_execution_context", None)
        if context is None:
            context = BacktestExecutionContext(self)
            self._execution_context = context
        return context

    def get_backtest_dynamic_slippage(self, size: int) -> float:
        return ExecutionEngine.get_backtest_dynamic_slippage(size)

    def _process_monotonic_segment(self, start_price: float, end_price: float, bar_index: int, phase_index: int) -> bool:
        result = self._get_execution_engine()._process_monotonic_segment(
            context=self._get_execution_context(),
            start_price=start_price,
            end_price=end_price,
            bar_index=bar_index,
            phase_index=phase_index,
        )
        return result is not None

    def _process_open_position_bar(self, bar_index: int):
        market = getattr(self, "market", None)
        bar = market.current_bar if market is not None else None
        if bar is None:
            raise RuntimeError("Market.current_bar is required to process a position bar")
        return self._get_execution_engine().process_bar(
            context=self._get_execution_context(),
            bar=bar,
            bar_index=bar_index,
            segment_processor=self._process_monotonic_segment,
        )
