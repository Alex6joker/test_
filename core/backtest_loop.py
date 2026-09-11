from __future__ import annotations

from typing import Protocol

from .backtest_bar import Bar


class BacktestRuntime(Protocol):
    """Framework-independent runtime boundary for the backtest loop."""

    market: object
    state: object
    logger: object
    _signal_engine: object
    _execution_engine: object
    _execution_context: object

    def _log_virtual_portfolio(self, bar_index: int) -> None: ...

    def _open_virtual_position(self, signal: int, size: int, bar_index: int) -> None: ...


class BacktestEngine:
    """Common per-bar orchestration for all virtual-backtest frontends.

    This class deliberately contains no Backtrader dependency. A frontend is
    responsible only for converting its input into ``Bar`` and providing the
    runtime boundary required by the already extracted virtual components.
    """

    def __init__(self, runtime: BacktestRuntime) -> None:
        self.runtime = runtime

    def process_bar(self, bar: Bar) -> None:
        runtime = self.runtime
        market = runtime.market
        state = runtime.state
        logger = runtime.logger

        market.observe(bar)
        current_bar_index = market.bar_index

        if logger.wants_debug_event("BAR"):
            logger.debug_event(
                "BAR",
                bar_index=current_bar_index,
                datetime=bar.datetime,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                position_size=state.virtual_position_size,
                entry_price=state.virtual_entry_price,
                tp_level=state.tp_level,
                sl_level=state.sl_level,
                current_trail_step=state.current_trail_step,
            )

        if logger.wants_debug_event("PORTFOLIO_STATE"):
            runtime._log_virtual_portfolio(current_bar_index)

        if current_bar_index < 2:
            if logger.wants_debug_event("SIGNAL_EVALUATION_SKIPPED"):
                logger.debug_event(
                    "SIGNAL_EVALUATION_SKIPPED",
                    bar_index=current_bar_index,
                    reason="NO_PREVIOUS_AVAILABLE_BAR",
                )
            return

        previous_bar = market.previous_bar
        if previous_bar is None:
            raise RuntimeError("Market.previous_bar is required for signal evaluation")

        intent = runtime._signal_engine.evaluate(
            current_bar=bar,
            previous_bar=previous_bar,
            bar_index=current_bar_index,
            position_size=state.virtual_position_size,
            virtual_cash=state.virtual_cash,
        )

        if state.virtual_position_size:
            runtime._execution_engine.process_bar(
                context=runtime._execution_context,
                bar=bar,
                bar_index=current_bar_index,
            )
            return

        if intent is None:
            return

        if intent.size < 1:
            if logger.wants_warning_or_error():
                logger.warning(
                    f"ENTRY_SKIPPED bar_index = {current_bar_index}; "
                    f"reason = POSITION_SIZE_ZERO; signal = {intent.direction}"
                )
            return

        runtime._open_virtual_position(
            signal=intent.direction,
            size=intent.size,
            bar_index=intent.signal_bar_index,
        )
        runtime._execution_engine.process_bar(
            context=runtime._execution_context,
            bar=bar,
            bar_index=current_bar_index,
        )
