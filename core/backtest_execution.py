from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .backtest_bar import Bar


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """Terminal execution event produced by ExecutionEngine."""

    event_type: str
    bar_index: int
    phase_index: int
    direction: int
    size: int
    detected_price: float
    execution_price: float
    slippage: float
    reason: str


class ExecutionContext(Protocol):
    """Narrow strategy boundary required by ExecutionEngine."""

    @property
    def position_size(self) -> int: ...

    @property
    def entry_price(self) -> float | None: ...

    @property
    def sl_level(self) -> float | None: ...

    @property
    def tp_level(self) -> float | None: ...

    @property
    def trade_id(self) -> int: ...

    @property
    def current_trail_step(self) -> int: ...

    def price(self, value: float) -> float: ...

    def trail_trigger_levels(self, direction: int): ...

    def apply_trail_step(
        self,
        step_idx: int,
        new_sl: float,
        current_price: float,
    ) -> None: ...

    def dynamic_slippage(self, size: int) -> float: ...

    def wants_debug_event(self, event_name: str) -> bool: ...

    def debug_event(self, event_name: str, **kwargs) -> None: ...

    def close_position(
        self,
        reason: str,
        target_exec_price: float,
        detected_price: float,
        bar_index: int,
        phase_index: int,
    ) -> None: ...


class ExecutionEngine:
    """Virtual intrabar execution model.

    The engine owns execution ordering and price-crossing decisions. It does
    not know Backtrader or BacktestState and does not mutate either directly.
    State changes are requested through the narrow ExecutionContext contract.
    """

    @staticmethod
    def get_backtest_dynamic_slippage(size: int) -> float:
        """Return execution slippage for the requested position size."""
        if size <= 5:
            return 0.02
        if size <= 15:
            return 0.04
        if size <= 30:
            return 0.07
        return 0.15

    def _process_monotonic_segment(
        self,
        context: ExecutionContext,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
    ) -> ExecutionResult | None:
        if not context.position_size:
            return None

        start = context.price(start_price)
        end = context.price(end_price)
        if start == end:
            return None

        direction = 1 if context.position_size > 0 else -1
        moving_up = end > start
        favorable = (direction > 0 and moving_up) or (direction < 0 and not moving_up)

        events = []

        if favorable:
            for step_idx, trigger_price, new_sl in context.trail_trigger_levels(direction):
                if direction > 0 and start < trigger_price <= end:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))
                elif direction < 0 and end <= trigger_price < start:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))

        if direction > 0:
            if not moving_up and end <= context.sl_level < start:
                events.append(("STOP_LOSS", context.sl_level, None, None))
            elif moving_up and start <= context.sl_level <= end:
                events.append(("STOP_LOSS", context.sl_level, None, None))

            if moving_up and start <= context.tp_level <= end:
                events.append(("TAKE_PROFIT", context.tp_level, None, None))
        else:
            if moving_up and start <= context.sl_level <= end:
                events.append(("STOP_LOSS", context.sl_level, None, None))
            elif not moving_up and end <= context.sl_level <= start:
                events.append(("STOP_LOSS", context.sl_level, None, None))

            if not moving_up and end <= context.tp_level <= start:
                events.append(("TAKE_PROFIT", context.tp_level, None, None))

        if moving_up:
            events.sort(key=lambda e: (e[1], 0 if e[0] != "TRAIL" else 1))
        else:
            events.sort(key=lambda e: (-e[1], 0 if e[0] != "TRAIL" else 1))

        current_price = start
        for event_type, event_price, step_idx, new_sl in events:
            if not context.position_size:
                return None

            if moving_up and event_price < current_price:
                continue
            if not moving_up and event_price > current_price:
                continue

            current_price = event_price

            if event_type == "TRAIL":
                context.apply_trail_step(step_idx, new_sl, current_price)
                continue

            size = abs(context.position_size)
            slippage = context.dynamic_slippage(size)

            if event_type == "STOP_LOSS":
                detected_price = current_price
                if context.position_size > 0:
                    target_exec_price = context.price(context.sl_level - slippage)
                else:
                    target_exec_price = context.price(context.sl_level + slippage)
            else:
                detected_price = current_price
                if context.position_size > 0:
                    target_exec_price = context.price(context.tp_level - slippage)
                else:
                    target_exec_price = context.price(context.tp_level + slippage)

            if context.wants_debug_event("EXIT_CROSSING"):
                context.debug_event(
                    "EXIT_CROSSING",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase_index,
                    event_type=event_type,
                    crossing_price=current_price,
                    sl_level=context.sl_level,
                    tp_level=context.tp_level,
                    slippage=slippage,
                )

            context.close_position(
                reason=event_type,
                target_exec_price=target_exec_price,
                detected_price=detected_price,
                bar_index=bar_index,
                phase_index=phase_index,
            )

            return ExecutionResult(
                event_type=event_type,
                bar_index=bar_index,
                phase_index=phase_index,
                direction=direction,
                size=size,
                detected_price=detected_price,
                execution_price=target_exec_price,
                slippage=slippage,
                reason=event_type,
            )

        return None

    def process_bar(
        self,
        context: ExecutionContext,
        bar: Bar,
        bar_index: int,
        segment_processor=None,
    ) -> ExecutionResult | None:
        """Process the deterministic intrabar path for one already-open position."""
        b_open = context.price(bar.open)
        b_high = context.price(bar.high)
        b_low = context.price(bar.low)
        b_close = context.price(bar.close)

        bullish_or_doji = b_close >= b_open
        if bullish_or_doji:
            points = [b_open, b_low, b_high, b_close]
        else:
            points = [b_open, b_high, b_low, b_close]

        for phase_index in range(len(points) - 1):
            if not context.position_size:
                break

            start_price = points[phase_index]
            end_price = points[phase_index + 1]

            if context.wants_debug_event("INTRABAR_PHASE"):
                context.debug_event(
                    "INTRABAR_PHASE",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase_index,
                    start_price=start_price,
                    end_price=end_price,
                    direction="UP" if end_price > start_price else (
                        "DOWN" if end_price < start_price else "FLAT"
                    ),
                    entry_price=context.entry_price,
                    tp_level=context.tp_level,
                    sl_level=context.sl_level,
                    current_trail_step=context.current_trail_step,
                )

            if segment_processor is None:
                result = self._process_monotonic_segment(
                    context=context,
                    start_price=start_price,
                    end_price=end_price,
                    bar_index=bar_index,
                    phase_index=phase_index,
                )
            else:
                result = segment_processor(
                    start_price,
                    end_price,
                    bar_index,
                    phase_index,
                )
                if result is False:
                    result = None

            if context.wants_debug_event("TRAIL_EVALUATION"):
                context.debug_event(
                    "TRAIL_EVALUATION",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase_index,
                    position_size=context.position_size,
                    start_price=start_price,
                    end_price=end_price,
                    entry_price=context.entry_price,
                    tp_level=context.tp_level,
                    sl_level=context.sl_level,
                    current_trail_step=context.current_trail_step,
                )

            if context.wants_debug_event("EXIT_EVALUATION"):
                context.debug_event(
                    "EXIT_EVALUATION",
                    trade_id=context.trade_id,
                    bar_index=bar_index,
                    phase_index=phase_index,
                    position_size=context.position_size,
                    start_price=start_price,
                    end_price=end_price,
                    tp_level=context.tp_level,
                    sl_level=context.sl_level,
                    closed=result is not None,
                )

            if result is not None:
                return result

        return None


class BacktestExecutionContext:
    """Production bridge from RealisticFuturesStrategy to ExecutionEngine."""

    def __init__(self, strategy) -> None:
        self._strategy = strategy

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
        return self._strategy.state.trade_id

    @property
    def current_trail_step(self) -> int:
        return self._strategy.state.current_trail_step

    def price(self, value: float) -> float:
        return self._strategy._price(value)

    def trail_trigger_levels(self, direction: int):
        return self._strategy._trail_trigger_levels(direction)

    def apply_trail_step(self, step_idx: int, new_sl: float, current_price: float) -> None:
        self._strategy._apply_trail_step(step_idx, new_sl, current_price)

    def dynamic_slippage(self, size: int) -> float:
        return ExecutionEngine.get_backtest_dynamic_slippage(size)

    def wants_debug_event(self, event_name: str) -> bool:
        return self._strategy.logger.wants_debug_event(event_name)

    def debug_event(self, event_name: str, **kwargs) -> None:
        self._strategy.logger.debug_event(event_name, **kwargs)

    def close_position(
        self,
        reason: str,
        target_exec_price: float,
        detected_price: float,
        bar_index: int,
        phase_index: int,
    ) -> None:
        self._strategy._close_virtual_position(
            reason=reason,
            target_exec_price=target_exec_price,
            detected_price=detected_price,
            bar_index=bar_index,
            phase_index=phase_index,
        )


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
        if size <= 5:
            return 0.02
        if size <= 15:
            return 0.04
        if size <= 30:
            return 0.07
        return 0.15

    def _process_monotonic_segment(
        self,
        start_price: float,
        end_price: float,
        bar_index: int,
        phase_index: int,
    ) -> bool:
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
            data = self.data
            bar = Bar(
                datetime=data.datetime.datetime(0),
                open=float(data.open[0]),
                high=float(data.high[0]),
                low=float(data.low[0]),
                close=float(data.close[0]),
                volume=int(data.volume[0]),
            )
        return self._get_execution_engine().process_bar(
            context=self._get_execution_context(),
            bar=bar,
            bar_index=bar_index,
            segment_processor=self._process_monotonic_segment,
        )
