from __future__ import annotations


class BacktestExecutionMixin:
    """Intrabar execution and chronological crossing logic."""

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
        """
        Process one monotonic path segment in exact price-crossing order.

        Returns True if the position was closed.
        """
        if not self.virtual_position_size:
            return True

        start = self._price(start_price)
        end = self._price(end_price)
        if start == end:
            # A zero-length phase cannot cross a level.
            return False

        direction = 1 if self.virtual_position_size > 0 else -1
        moving_up = end > start

        # For a LONG, favorable movement is upward; for a SHORT, favorable
        # movement is downward. Only favorable movement can activate a trail.
        favorable = (direction > 0 and moving_up) or (direction < 0 and not moving_up)

        events = []

        if favorable:
            for step_idx, trigger_price, new_sl in self._trail_trigger_levels(direction):
                if direction > 0 and start < trigger_price <= end:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))
                elif direction < 0 and end <= trigger_price < start:
                    events.append(("TRAIL", trigger_price, step_idx, new_sl))

        # Exit levels are checked on the actual movement direction.
        if direction > 0:
            if not moving_up and end <= self.sl_level < start:
                events.append(("STOP_LOSS", self.sl_level, None, None))
            elif moving_up and start <= self.sl_level <= end:
                # This can only happen if a previously moved stop is already
                # at/above the current path start.
                events.append(("STOP_LOSS", self.sl_level, None, None))

            if moving_up and start <= self.tp_level <= end:
                events.append(("TAKE_PROFIT", self.tp_level, None, None))
        else:
            if moving_up and start <= self.sl_level <= end:
                events.append(("STOP_LOSS", self.sl_level, None, None))
            elif not moving_up and end <= self.sl_level <= start:
                events.append(("STOP_LOSS", self.sl_level, None, None))

            if not moving_up and end <= self.tp_level <= start:
                events.append(("TAKE_PROFIT", self.tp_level, None, None))

        # Sort by actual traversal order. At equal price, exits take priority
        # over trail activation so a level cannot retroactively protect itself.
        if moving_up:
            events.sort(key=lambda e: (e[1], 0 if e[0] != "TRAIL" else 1))
        else:
            events.sort(key=lambda e: (-e[1], 0 if e[0] != "TRAIL" else 1))

        current_price = start
        for event_type, event_price, step_idx, new_sl in events:
            if not self.virtual_position_size:
                return True

            # Ignore stale events made obsolete by an earlier event.
            if moving_up and event_price < current_price:
                continue
            if not moving_up and event_price > current_price:
                continue

            current_price = event_price

            if event_type == "TRAIL":
                self._apply_trail_step(
                    step_idx,
                    new_sl,
                    current_price,
                )
                continue

            size = abs(self.virtual_position_size)
            slippage = self.get_backtest_dynamic_slippage(size)

            if event_type == "STOP_LOSS":
                detected_price = current_price
                if self.virtual_position_size > 0:
                    target_exec_price = self._price(
                        self.sl_level - slippage
                    )
                else:
                    target_exec_price = self._price(
                        self.sl_level + slippage
                    )
            else:
                detected_price = current_price
                if self.virtual_position_size > 0:
                    target_exec_price = self._price(
                        self.tp_level - slippage
                    )
                else:
                    target_exec_price = self._price(
                        self.tp_level + slippage
                    )

            self.logger.debug_event(
                "EXIT_CROSSING",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                event_type=event_type,
                crossing_price=current_price,
                sl_level=self.sl_level,
                tp_level=self.tp_level,
                slippage=slippage,
            )

            self._close_virtual_position(
                reason=event_type,
                target_exec_price=target_exec_price,
                detected_price=detected_price,
                bar_index=bar_index,
                phase_index=phase_index,
            )
            return True

        return False

    def _process_open_position_bar(self, bar_index: int):
        b_open = self._price(self.data.open[0])
        b_high = self._price(self.data.high[0])
        b_low = self._price(self.data.low[0])
        b_close = self._price(self.data.close[0])

        # Doji is deliberately treated as bullish/deterministic:
        # Open -> Low -> High -> Close.
        bullish_or_doji = b_close >= b_open
        if bullish_or_doji:
            points = [b_open, b_low, b_high, b_close]
        else:
            points = [b_open, b_high, b_low, b_close]

        for phase_index in range(len(points) - 1):
            if not self.virtual_position_size:
                break

            start_price = points[phase_index]
            end_price = points[phase_index + 1]

            self.logger.debug_event(
                "INTRABAR_PHASE",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                start_price=start_price,
                end_price=end_price,
                direction="UP" if end_price > start_price else (
                    "DOWN" if end_price < start_price else "FLAT"
                ),
                entry_price=self.virtual_entry_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                current_trail_step=self.current_trail_step,
            )

            closed = self._process_monotonic_segment(
                start_price=start_price,
                end_price=end_price,
                bar_index=bar_index,
                phase_index=phase_index,
            )

            self.logger.debug_event(
                "TRAIL_EVALUATION",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                position_size=self.virtual_position_size,
                start_price=start_price,
                end_price=end_price,
                entry_price=self.virtual_entry_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                current_trail_step=self.current_trail_step,
            )

            self.logger.debug_event(
                "EXIT_EVALUATION",
                trade_id=self.trade_id,
                bar_index=bar_index,
                phase_index=phase_index,
                position_size=self.virtual_position_size,
                start_price=start_price,
                end_price=end_price,
                tp_level=self.tp_level,
                sl_level=self.sl_level,
                closed=closed,
            )

            if closed:
                break
