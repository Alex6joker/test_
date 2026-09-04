import backtrader as bt

# 1. Эмулируем типы данных интрабара, чтобы backtester.py успешно импортировал их
if not hasattr(bt.broker, 'IntrabarType'):
    class DummyIntrabarType:
        Ohlc = 'Ohlc'
        Tick = 'Tick'
    bt.broker.IntrabarType = DummyIntrabarType
    bt.broker.IntrarbarType = DummyIntrabarType

if not hasattr(bt.broker, 'SlippageOnIntrabar'):
    class DummySlippageOnIntrabar:
        def __init__(self, intrabar_type=None, check_proxs=True, slippage=0, slip_open=True, slip_suborders=True):
            self.slippage = slippage
            self.slip_open = slip_open
            self.slip_suborders = slip_suborders
    bt.broker.SlippageOnIntrabar = DummySlippageOnIntrabar

# 2. Перехватываем создание Cerebro для вживления адаптера проскальзывания
_original_init = bt.Cerebro.__init__

def patched_cerebro_init(self, *args, **kwargs):
    _original_init(self, *args, **kwargs)
    live_broker = self.broker
    
    def dynamic_set_strategy(strategy_obj):
        # Вытаскиваем параметры из объекта SlippageOnIntrabar
        slip_val = getattr(strategy_obj, 'slippage', 0.0)
        slip_open = getattr(strategy_obj, 'slip_open', True)
        slip_stop = getattr(strategy_obj, 'slip_suborders', True)
        
        # Переводим кастомные параметры на рельсы стандартного Backtrader
        live_broker.set_slippage_fixed(
            slip_val, 
            slip_open=slip_open,
            slip_limit=True,    # Включаем для лимитных Take-Profit
            slip_match=slip_stop, # slip_match в Backtrader отвечает за Стоп-ордера (Stop-Loss)
            slip_out=False
        )
        #print(f"[ПАТЧ ЯДРА] Настройки интрабар-проскальзывания успешно адаптированы.")
        #print(f"            Размер проскальзывания зафиксирован: {slip_val} руб.")

    # Вживляем методы в объект брокера (основной и вариант ncev с опечаткой)
    live_broker.set_intrabar_strategy = dynamic_set_strategy
    live_broker.set_intrarbar_strategy = dynamic_set_strategy

bt.Cerebro.__init__ = patched_cerebro_init
#print(">>> Локальный форк-патч Backtrader успешно инициализирован.")