from __future__ import annotations


class BacktestTrailingMixin:
    """Dynamic trailing-stop calculations and updates."""

    def _trail_trigger_levels(self, direction: int):
        entry = self.state.virtual_entry_price
        if entry is None:
            return []

        direction = 1 if direction > 0 else -1
        cache_entry = getattr(self, "_trail_levels_cache_entry", None)
        cache_direction = getattr(self, "_trail_levels_cache_direction", None)
        levels = getattr(self, "_trail_levels_cache", None)

        if cache_entry != entry or cache_direction != direction or levels is None:
            tp_distance = self._price(self.params.tp)
            levels = []

            for step_idx, (trigger_pct, stop_pct) in enumerate(
                self.params.dynamic_trail_steps
            ):
                trigger_distance = self._price(tp_distance * trigger_pct)
                if direction > 0:
                    trigger_price = self._price(entry + trigger_distance)
                    new_sl = self._price(entry + tp_distance * stop_pct)
                else:
                    trigger_price = self._price(entry - trigger_distance)
                    new_sl = self._price(entry - tp_distance * stop_pct)

                levels.append((step_idx, trigger_price, new_sl))

            self._trail_levels_cache_entry = entry
            self._trail_levels_cache_direction = direction
            self._trail_levels_cache = levels

        current_step = self.state.current_trail_step
        if current_step < 0:
            return levels.copy()
        return levels[current_step + 1 :].copy()

    def _apply_trail_step(self, step_idx: int, new_sl: float, current_price: float):
        old_sl = self.state.sl_level
        if old_sl is None:
            return

        if self.state.virtual_position_size > 0:
            if new_sl <= old_sl:
                self.state.current_trail_step = max(self.state.current_trail_step, step_idx)
                return
        else:
            if new_sl >= old_sl:
                self.state.current_trail_step = max(self.state.current_trail_step, step_idx)
                return

        self.state.sl_level = new_sl
        self.state.current_trail_step = step_idx
        if self.logger.wants_trade():
            self.logger.trade(
                f"TRAIL_UPDATE trade_id = {self.state.trade_id}; "
                f"step_idx = {step_idx}; "
                f"trigger_pct = {self.params.dynamic_trail_steps[step_idx][0]}; "
                f"stop_pct = {self.params.dynamic_trail_steps[step_idx][1]}; "
                f"old_sl_level = {old_sl}; new_sl_level = {new_sl}; "
                f"trigger_cross_price = {current_price}"
            )
