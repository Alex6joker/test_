from __future__ import annotations


class BacktestSignalMixin:
    """Signal evaluation and position-sizing logic."""

    def _calculate_position_size(self, bar_index: int) -> int:
        current_free_funds = self.virtual_cash
        loss_per_contract_rub = float(self.params.sl) * float(self.params.real_mult)
        max_rub_to_risk = current_free_funds * float(self.params.risk)
        size_by_risk = int(max_rub_to_risk / loss_per_contract_rub)

        cost_margin_per_contract = (
            float(self.params.real_margin) * float(self.params.safety_factor)
        )
        max_size_by_margin = int(
            (current_free_funds + 1e-5) // cost_margin_per_contract
        )

        # Same liquidity rule as live engine: previous available candle volume.
        bar_volume = int(self.data.volume[-1])
        max_size_by_liquidity = max(1, int(bar_volume * 0.05))

        dynamic_size = min(
            size_by_risk,
            max_size_by_margin,
            max_size_by_liquidity,
        )

        self.logger.debug_event(
            "POSITION_SIZE",
            bar_index=bar_index,
            current_free_funds=current_free_funds,
            max_rub_to_risk=max_rub_to_risk,
            loss_per_contract_rub=loss_per_contract_rub,
            size_by_risk=size_by_risk,
            cost_margin_per_contract=cost_margin_per_contract,
            max_size_by_margin=max_size_by_margin,
            bar_volume=bar_volume,
            max_size_by_liquidity=max_size_by_liquidity,
            dynamic_size=dynamic_size,
        )

        return dynamic_size

    def next(self):
        current_bar_index = len(self.data)

        self.logger.debug_event(
            "BAR",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            open=float(self.data.open[0]),
            high=float(self.data.high[0]),
            low=float(self.data.low[0]),
            close=float(self.data.close[0]),
            volume=float(self.data.volume[0]),
            position_size=self.virtual_position_size,
            entry_price=self.virtual_entry_price,
            tp_level=self.tp_level,
            sl_level=self.sl_level,
            current_trail_step=self.current_trail_step,
        )

        self._log_virtual_portfolio(current_bar_index)

        # The strategy needs one previous available candle, and no more.
        if current_bar_index < 2:
            self.logger.debug_event(
                "SIGNAL_EVALUATION_SKIPPED",
                bar_index=current_bar_index,
                reason="NO_PREVIOUS_AVAILABLE_BAR",
            )
            return

        previous_open = float(self.data.open[-1])
        previous_high = float(self.data.high[-1])
        previous_low = float(self.data.low[-1])
        previous_close = float(self.data.close[-1])
        previous_volume = int(self.data.volume[-1])

        previous_range_raw = previous_high - previous_low
        previous_range = self._price(previous_range_raw)
        previous_bullish = previous_close > previous_open
        previous_bearish = previous_close < previous_open
        range_ok = previous_range >= float(self.params.trigger)

        long_signal_value = bool(range_ok and previous_bullish)
        short_signal_value = bool(range_ok and previous_bearish)

        # ATR is intentionally not calculated as a Backtrader indicator.
        atr_diagnostic = None

        self.logger.debug_event(
            "SIGNAL_EVALUATION",
            bar_index=current_bar_index,
            datetime=self.data.datetime.datetime(0),
            previous_datetime=self.data.datetime.datetime(-1),
            current_open=float(self.data.open[0]),
            current_high=float(self.data.high[0]),
            current_low=float(self.data.low[0]),
            current_close=float(self.data.close[0]),
            previous_open=previous_open,
            previous_high=previous_high,
            previous_low=previous_low,
            previous_close=previous_close,
            previous_volume=previous_volume,
            previous_range_raw=previous_range_raw,
            previous_range=previous_range,
            trigger=self.params.trigger,
            range_ok=range_ok,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
            long_signal=long_signal_value,
            short_signal=short_signal_value,
            atr=atr_diagnostic,
            position_size=self.virtual_position_size,
            main_order_ref=None,
        )

        if self.virtual_position_size:
            self._process_open_position_bar(current_bar_index)
            return

        selected_signal = (
            1 if long_signal_value else (-1 if short_signal_value else 0)
        )

        self.logger.debug_event(
            "SIGNAL_DECISION",
            bar_index=current_bar_index,
            long_signal=long_signal_value,
            short_signal=short_signal_value,
            selected_signal=selected_signal,
            previous_range=previous_range,
            trigger=self.params.trigger,
            previous_bullish=previous_bullish,
            previous_bearish=previous_bearish,
        )

        if selected_signal == 0:
            return

        dynamic_size = self._calculate_position_size(current_bar_index)
        if dynamic_size < 1:
            self.logger.warning(
                f"ENTRY_SKIPPED bar_index = {current_bar_index}; "
                f"reason = POSITION_SIZE_ZERO; signal = {selected_signal}"
            )
            return

        self._open_virtual_position(
            signal=selected_signal,
            size=dynamic_size,
            bar_index=current_bar_index,
        )

        # Entry occurs at current Open, so the position must be exposed to
        # the remainder of the same candle.
        self._process_open_position_bar(current_bar_index)
