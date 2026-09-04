"""Диагностическое логирование бэктестера.

Логгер намеренно не имеет ConsoleHandler: диагностический вывод бэктестера
пишется только в отдельный файл.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Mapping


class BacktestLogger:
    """Единый файловый логгер для полной диагностики бэктеста."""

    _configured_files: set[str] = set()

    def __init__(self, log_path: str = "logs/backtest_diagnostic.log", *, reset: bool = False):
        self.log_path = os.path.abspath(log_path)
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)

        logger_name = f"backtest.{self.log_path}"
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False

        if self.log_path not in self._configured_files:
            mode = "w" if reset else "a"
            handler = logging.FileHandler(self.log_path, mode=mode, encoding="utf-8")
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            self._logger.addHandler(handler)
            self._configured_files.add(self.log_path)

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        return repr(value)

    def debug(self, message: str) -> None:
        self._logger.debug(message)

    def info(self, message: str) -> None:
        self._logger.info(message)

    def trade(self, message: str) -> None:
        # TRADE — отдельный уровень смысла, технически записываем как INFO.
        self._logger.info(f"TRADE | {message}")

    def warning(self, message: str) -> None:
        self._logger.warning(message)

    def error(self, message: str) -> None:
        self._logger.error(message)

    def section(self, name: str) -> None:
        self._logger.info("=" * 72)
        self._logger.info(f"[{name}]")
        self._logger.info("=" * 72)

    def event(self, name: str, **variables: Any) -> None:
        """Записывает событие и его переменные в формате name = value."""
        self._logger.info(f"[{name}]")
        for variable_name, value in variables.items():
            self._logger.info(f"    {variable_name} = {self._format_value(value)}")

    def debug_event(self, name: str, **variables: Any) -> None:
        """Подробное диагностическое событие."""
        self._logger.debug(f"[{name}]")
        for variable_name, value in variables.items():
            self._logger.debug(f"    {variable_name} = {self._format_value(value)}")

    def variables(self, variables: Mapping[str, Any], *, level: str = "debug") -> None:
        """Удобная запись набора переменных с сохранением их имён."""
        log_method = getattr(self, level.lower(), self.debug)
        for variable_name, value in variables.items():
            log_method(f"{variable_name} = {self._format_value(value)}")

    def close(self) -> None:
        for handler in list(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)
        self._configured_files.discard(self.log_path)
