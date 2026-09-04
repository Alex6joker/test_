from __future__ import annotations


class BacktestTrailingMixin:
    """Dynamic trailing-stop calculations and updates."""

    def _trail_trigger_levels(self, direction: int):
        tp_distance = self._price(self.params.tp)
        entry = self.virtual_entry_price
        if entry is None:
            return []

        result = []
        for step_idx, (trigger_pct, stop_pct) in enumerate(
            self.params.dynamic_trail_steps
        ):
            if step_idx <= self.current_trail_step:
                continue

            trigger_distance = self._price(tp_distance * trigger_pct)
            if direction > 0:
                trigger_price = self._price(entry + trigger_distance)
                new_sl = self._price(entry + tp_distance * stop_pct)
            else:
                trigger_price = self._price(entry - trigger_distance)
                new_sl = self._price(entry - tp_distance * stop_pct)

            result.append((step_idx, trigger_price, new_sl))
        return result

    def _apply_trail_step(self, step_idx: int, new_sl: float, current_price: float):
        old_sl = self.sl_level
        if old_sl is None:
            return

        if self.virtual_position_size > 0:
            if new_sl <= old_sl:
                self.current_trail_step = max(self.current_trail_step, step_idx)
                return
        else:
            if new_sl >= old_sl:
                self.current_trail_step = max(self.current_trail_step, step_idx)
                return

        self.sl_level = new_sl
        self.current_trail_step = step_idx
        self.logger.trade(
            f"TRAIL_UPDATE trade_id = {self.trade_id}; "
            f"step_idx = {step_idx}; "
            f"trigger_pct = {self.params.dynamic_trail_steps[step_idx][0]}; "
            f"stop_pct = {self.params.dynamic_trail_steps[step_idx][1]}; "
            f"old_sl_level = {old_sl}; new_sl_level = {new_sl}; "
            f"trigger_cross_price = {current_price}"
        )
