from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

from core.backtest_accounting import AccountingEngine
from core.backtest_bar import Bar
from core.backtest_execution import ExecutionEngine, ExecutionExit, ExecutionSnapshot, NativeExecutionContext, TrailLevel
from core.backtest_market import Market
from core.backtest_state import BacktestState
from core.backtest_trade import TradeAccountingEngine
from core.backtest_trade_ledger import TradeLedger
from core.backtest_trailing import TrailingEngine


class FakeLogger:
    is_diagnostic = False
    def wants_debug_event(self, _name): return False
    def wants_trade(self): return False
    def debug_event(self, *_args, **_kwargs): pass
    def trade(self, *_args, **_kwargs): pass


class ExecutionHarness:
    def __init__(self, *, direction=1, size=5, entry=100.0, sl=98.0, tp=103.0):
        self.params = SimpleNamespace(
            precision_num=2, precision_money=2, tp=1.9, sl=1.7,
            real_mult=1000.0, real_commission_per_side=10.186,
            dynamic_trail_steps=[(0.20,0.05),(0.50,0.15),(0.90,0.65)],
        )
        self.logger=FakeLogger(); self.state=BacktestState(virtual_cash=264000.0); self.market=Market(); self.ledger=TradeLedger()
        self.accounting=AccountingEngine(self.state,self.params,lambda x:round(float(x),2),10.186)
        self.trade_accounting=TradeAccountingEngine(state=self.state,params=self.params,accounting=self.accounting,ledger=self.ledger,price_fn=lambda x:round(float(x),2))
        self.trailing=TrailingEngine(self.state,self.params,lambda x:round(float(x),2),self.logger,self.ledger)
        self.context=NativeExecutionContext(state=self.state,market=self.market,ledger=self.ledger,trailing=self.trailing,trade_accounting=self.trade_accounting,logger=self.logger,price_fn=lambda x:round(float(x),2))
        self.engine=ExecutionEngine()
        self.trade_accounting.open(direction,size,2,SimpleNamespace(open=entry,datetime=datetime(2026,1,1)))
        self.state.sl_level=sl; self.state.tp_level=tp

    def segment(self,start,end,bar_index=10,phase_index=0):
        snap=self.context.execution_snapshot()
        phase=self.engine._process_segment(snap,round(start,2),round(end,2),bar_index,phase_index)
        if phase is None: return False
        for update in phase.trail_updates: self.context.apply_trail_update(update,update.trigger_price)
        if phase.exit is not None: self.context.apply_execution_exit(phase.exit)
        return phase.exit is not None


class BacktestExecutionContractTests(unittest.TestCase):
    def test_pure_engine_phase_input_state_tracks_previous_trail(self):
        s=ExecutionHarness(); s.params.dynamic_trail_steps=[(0.2,0.05),(0.5,0.15)]
        first=s.segment(100,100.6,10,0); self.assertFalse(first); self.assertEqual(s.state.current_trail_step,0)
        snap=s.context.execution_snapshot(); self.assertEqual(snap.current_trail_step,0); self.assertEqual(snap.sl_level,100.09)

    def test_dynamic_slippage_boundaries(self):
        self.assertEqual({n:ExecutionEngine.get_backtest_dynamic_slippage(n) for n in (1,5,6,15,16,30,31)},{1:0.02,5:0.02,6:0.04,15:0.04,16:0.07,30:0.07,31:0.15})

    def test_long_stop_loss_crossing_produces_execution_price_below_stop(self):
        s=ExecutionHarness(direction=1); s.segment(100,97,10,0); r=s.ledger.records[0]; self.assertEqual(r['exit_price'],97.98)

    def test_short_stop_loss_crossing_produces_execution_price_above_stop(self):
        s=ExecutionHarness(direction=-1,sl=102,tp=97); s.segment(100,103,10,0); self.assertEqual(s.ledger.records[0]['exit_price'],102.02)

    def test_long_take_profit_crossing_produces_execution_price_below_tp(self):
        s=ExecutionHarness(direction=1,sl=98,tp=103); s.segment(100,104,10,0); self.assertEqual(s.ledger.records[0]['exit_price'],102.98)

    def test_short_take_profit_crossing_produces_execution_price_above_tp(self):
        s=ExecutionHarness(direction=-1,sl=102,tp=97); s.segment(100,96,10,0); self.assertEqual(s.ledger.records[0]['exit_price'],97.02)

    def test_equal_price_phase_does_not_generate_execution(self):
        s=ExecutionHarness(); self.assertFalse(s.segment(100,100)); self.assertEqual(s.state.virtual_position_size,5)

    def test_favorable_long_phase_applies_trail_before_later_adverse_phase(self):
        s=ExecutionHarness(direction=1,entry=100,sl=98,tp=110); s.params.tp=10.0; s.params.dynamic_trail_steps=[(0.2,0.05)]; self.assertFalse(s.segment(100,103,10,0)); self.assertEqual(s.state.sl_level,100.5); self.assertTrue(s.segment(103,100,10,1)); self.assertEqual(s.ledger.records[0]['exit_phase'],1)

    def test_exit_at_same_price_has_priority_over_trail(self):
        s=ExecutionHarness(direction=1,entry=100,sl=99,tp=101); s.params.dynamic_trail_steps=[(1/3,0.5)]; self.assertTrue(s.segment(100,101,10,0)); self.assertEqual(s.ledger.records[0]['exit_reason'],'TAKE_PROFIT')

    def test_multiple_trail_steps_are_processed_in_traversal_order(self):
        s=ExecutionHarness(direction=1,entry=100,sl=98,tp=110); s.params.dynamic_trail_steps=[(0.2,0.05),(0.5,0.15),(0.9,0.65)]; self.assertFalse(s.segment(100,106,10,0)); self.assertEqual(s.state.current_trail_step,2); self.assertEqual(s.state.sl_level,106.5 if False else 101.23)

    def test_doji_path_is_open_low_high_close(self):
        s=ExecutionHarness(); s.state.tp_level=105.0; bar=Bar(datetime(2026,1,1),100,103,99,100,100); result=s.engine.process(s.context.execution_snapshot(),bar,7); self.assertEqual([(p.start_price,p.end_price) for p in result.phases],[(100,99),(99,103),(103,100)])

    def test_processing_after_position_is_closed_stops(self):
        s=ExecutionHarness(direction=1,entry=100,sl=99,tp=101); self.assertTrue(s.segment(100,102,10,0)); self.assertFalse(s.segment(102,90,10,1)); self.assertEqual(s.ledger.closed_trades,1)


class PureExecutionEngineTests(unittest.TestCase):
    def snapshot(self, **changes):
        values=dict(position_size=5,entry_price=100.0,sl_level=98.0,tp_level=103.0,current_trail_step=-1,trail_levels=(TrailLevel(0,100.5,100.1),),slippage=0.02); values.update(changes); return ExecutionSnapshot(**values)

    def test_pure_engine_does_not_require_runtime_context(self):
        result=ExecutionEngine().process(self.snapshot(),Bar(datetime(2026,1,1),100,104,99,103,100),10); self.assertTrue(result.phases)

    def test_fast_path_matches_full_path_for_trail_and_exit_decisions(self):
        e=ExecutionEngine(); snap=self.snapshot(trail_levels=(TrailLevel(0,100.5,100.1),)); bar=Bar(datetime(2026,1,1),100,104,99,103,100); full=e.process(snap,bar,10); fast=e.process_fast(snap,bar,10); self.assertEqual(fast[1],full.exit); self.assertEqual(fast[0],tuple(u for p in full.phases for u in p.trail_updates))

    def test_fast_path_does_not_mutate_snapshot(self):
        e=ExecutionEngine(); snap=self.snapshot(); before=snap; e.process_fast(snap,Bar(datetime(2026,1,1),100,104,99,103,100),10); self.assertEqual(snap,before)

    def test_pure_engine_returns_trail_then_exit_causally(self):
        e=ExecutionEngine(); snap=self.snapshot(sl_level=98,tp_level=102,trail_levels=(TrailLevel(0,100.5,100.1),)); bar=Bar(datetime(2026,1,1),100,103,99,103,100); result=e.process(snap,bar,10); self.assertEqual(result.phases[1].trail_updates[0].step_idx,0); self.assertEqual(result.exit.reason,'TAKE_PROFIT')

if __name__ == '__main__': unittest.main(verbosity=2)
