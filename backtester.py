import os
import sys
import uuid
import pandas as pd
import backtrader as bt

# Подключаем ядро стратегий из core
from core.backtest_engine import RealisticFuturesStrategy, ContractVolumeAnalyzer
from core.backtest_logger import BacktestLogger

def run_instrument_backtest(instrument_folder, cfg):
    """
    Запускает бэктест для конкретного инструмента.
    instrument_folder — имя папки (например, '01_SILVER')
    cfg — динамически подгруженный модуль config.py
    """
    # Динамически строим путь к исходному файлу данных внутри папки инструмента
    csv_path = os.path.join(instrument_folder, cfg.TEST_OPTIMIZE_CSV_PATH_4MONTH_PATH)
    unique_id = uuid.uuid4().hex[:8]
    processed_path = f"temp_bt_ready_{unique_id}.csv"
    logger = BacktestLogger(
        os.path.join(os.path.dirname(__file__), "logs", "backtest_diagnostic.log"),
        reset=True,
    )
    logger.section(f"BACKTEST START: {cfg.FUT_SEC_CODE}")
    logger.event(
        "INPUT",
        instrument_folder=instrument_folder,
        csv_path=csv_path,
        initial_cash=cfg.INITIAL_CASH,
        trigger=cfg.TRIGGER_SPREAD,
        take_profit=cfg.TAKE_PROFIT,
        stop_loss=cfg.STOP_LOSS,
        offer_risk=cfg.OFFER_RISK,
    )

    if not os.path.exists(csv_path):
        logger.error(f"DATA_FILE_NOT_FOUND csv_path = {csv_path}")
        logger.close()
        return
        
    try:
        # Подготовка датасета (приведение колонок к стандарту)
        raw_df = pd.read_csv(csv_path, sep=';', dtype=str)
        # Приводим названия колонок самого DataFrame к верхнему регистру
        raw_df.columns = [str(c).upper() for c in raw_df.columns]
        
        try:
            open_col_list = [c for c in raw_df.columns if 'OPEN' in c or 'ОТКР' in c]
            high_col_list = [c for c in raw_df.columns if 'HIGH' in c or 'МАКС' in c]
            if not open_col_list or not high_col_list:
                raise IndexError
            open_col = open_col_list[0]
            high_col = high_col_list[0]
            low_col = [c for c in raw_df.columns if 'LOW' in c or 'МИН' in c][0]
            close_col = [c for c in raw_df.columns if 'CLOSE' in c or 'ЗАКР' in c][0]
            vol_col = [c for c in raw_df.columns if 'VOL' in c or 'ОБЪЕМ' in c][0]
            date_col = [c for c in raw_df.columns if 'DATE' in c or 'ДАТА' in c][0]
            time_col = [c for c in raw_df.columns if 'TIME' in c or 'ВРЕМЯ' in c][0]
        except IndexError:
            logger.error(f"CSV_STRUCTURE_ERROR columns = {list(raw_df.columns)}")
            return
        
        raw_df['TIMESTRING'] = (
            raw_df[date_col].astype(str).str.replace('-', '', regex=False).str.replace('.', '', regex=False) + ' ' + 
            raw_df[time_col].astype(str).str.replace(':', '', regex=False).str.zfill(6)
        )
        
        ready_df = pd.DataFrame()
        ready_df['DateTime'] = raw_df['TIMESTRING']
        ready_df['Open'] = raw_df[open_col].astype(float)
        ready_df['High'] = raw_df[high_col].astype(float)
        ready_df['Low'] = raw_df[low_col].astype(float)
        ready_df['Close'] = raw_df[close_col].astype(float)
        ready_df['Volume'] = raw_df[vol_col].astype(float).round().astype(int)
        
        ready_df.to_csv(processed_path, index=False)
        
        # Настройка Cerebro
        cerebro = bt.Cerebro()
        
        # Передаем параметры динамического конфига напрямую в универсальную стратегию
        cerebro.addstrategy(
            RealisticFuturesStrategy,
            trigger=cfg.TRIGGER_SPREAD,
            tp=cfg.TAKE_PROFIT,
            sl=cfg.STOP_LOSS,
            risk=cfg.OFFER_RISK,
            real_mult=cfg.REAL_MULT,
            real_margin=cfg.REAL_MARGIN,
            safety_factor=cfg.SAFETY_FACTOR,
            precision_num=cfg.PRECISION_NUM,
            dynamic_trail_steps=cfg.DYNAMIC_TRAIL_STEPS,
            logger=logger
        )
        
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')
        cerebro.addanalyzer(ContractVolumeAnalyzer, _name='volume_tracker')
        cerebro.addanalyzer(bt.analyzers.drawdown.DrawDown, _name='drawdown_tracker')
        
        data = bt.feeds.GenericCSVData(
            dataname=processed_path, sep=',', dtformat='%Y%m%d %H%M%S',
            timeframe=bt.TimeFrame.Minutes, datetime=0, time=-1,
            open=1, high=2, low=3, close=4, volume=5, openinterest=-1, header=0
        )
        cerebro.adddata(data)
        
        cerebro.broker.setcash(cfg.INITIAL_CASH) 
        cerebro.broker.setcommission(
            commission=cfg.REAL_COMMISSION / 2,
            margin=cfg.REAL_MARGIN,
            mult=cfg.REAL_MULT,
            stocklike=False,
            commtype=bt.CommInfoBase.COMM_FIXED
        )
        
        logger.event("BROKER_START", cash=cerebro.broker.getcash(), value=cerebro.broker.getvalue(), commission_per_side=cfg.REAL_COMMISSION / 2, margin=cfg.REAL_MARGIN, mult=cfg.REAL_MULT)
        strategies = cerebro.run()
        first_strat = strategies[0]
        
        # Сбор и вывод статистики
        trade_info = first_strat.analyzers.trades.get_analysis()
        total_closed_trades = 0
        if 'total' in trade_info and 'closed' in trade_info['total']:
            total_closed_trades = trade_info['total']['closed']
            
        # Strategy execution is virtual: Backtrader is used for the data/indicator engine,
        # while fills and P&L are calculated by RealisticFuturesStrategy itself.
        final_portfolio_value = float(first_strat.final_virtual_equity)
        real_net_profit = final_portfolio_value - cfg.INITIAL_CASH
        total_closed_trades = int(first_strat.closed_trades)
        total_contracts = int(first_strat.total_contracts)
        total_commission = round(
            total_contracts * float(cfg.REAL_COMMISSION / 2),
            cfg.PRECISION_NUM_DEPO_RUB if hasattr(cfg, 'PRECISION_NUM_DEPO_RUB') else 2,
        )

        logger.event(
            "BACKTEST_RESULT",
            execution_model="VIRTUAL",
            final_portfolio_value=final_portfolio_value,
            real_net_profit=real_net_profit,
            total_closed_trades=total_closed_trades,
            total_contracts=total_contracts,
            total_commission=total_commission,
            open_position_size=first_strat.virtual_position_size,
            virtual_cash=first_strat.virtual_cash,
        )
        
    finally:
        if os.path.exists(processed_path): 
            os.remove(processed_path)
        logger.section("BACKTEST END")
        logger.close()
