"""Режимы и транспорт диагностического логирования бэктестера."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from enum import Enum
from typing import Any, Mapping


class BacktestLogMode(str, Enum):
    NONE = "NONE"
    MINIMAL = "MINIMAL"
    FAST = "FAST"
    DIAGNOSTIC = "DIAGNOSTIC"

    @classmethod
    def normalize(cls, value: str | "BacktestLogMode" | None) -> "BacktestLogMode":
        if isinstance(value, cls):
            return value
        normalized = str(value or cls.DIAGNOSTIC.value).strip().upper()
        try:
            return cls(normalized)
        except ValueError as exc:
            raise ValueError(
                f"Unknown backtest log mode: {value!r}. "
                f"Expected one of: {', '.join(mode.value for mode in cls)}"
            ) from exc


class BacktestLogger:
    """Единый файловый логгер с режимами NONE/MINIMAL/FAST/DIAGNOSTIC."""

    _configured_files: set[str] = set()
    _FAST_EVENTS = frozenset({
        "STRATEGY_INIT",
        "ACCOUNTING_CHECK",
        "BACKTEST_RESULT",
    })

    def __init__(
        self,
        log_path: str = "logs/backtest_diagnostic.log",
        *,
        reset: bool = False,
        mode: str | BacktestLogMode = BacktestLogMode.DIAGNOSTIC,
    ):
        self.mode = BacktestLogMode.normalize(mode)
        self.log_path = os.path.abspath(log_path)
        self._logger: logging.Logger | None = None

        if self.mode is BacktestLogMode.NONE:
            return

        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        logger_name = f"backtest.{self.log_path}"
        self._logger = logging.getLogger(logger_name)
        self._logger.setLevel(logging.DEBUG)
        self._logger.propagate = False
        if self.log_path not in self._configured_files:
            file_mode = "w" if reset else "a"
            handler = logging.FileHandler(
                self.log_path, mode=file_mode, encoding="utf-8"
            )
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s.%(msecs)03d | %(levelname)-7s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            ))
            self._logger.addHandler(handler)
            self._configured_files.add(self.log_path)

    @property
    def is_none(self) -> bool:
        return self.mode is BacktestLogMode.NONE

    @property
    def is_diagnostic(self) -> bool:
        return self.mode is BacktestLogMode.DIAGNOSTIC

    def wants_event(self, name: str) -> bool:
        if self.mode is BacktestLogMode.DIAGNOSTIC:
            return True
        if self.mode is BacktestLogMode.FAST:
            return name in self._FAST_EVENTS
        if self.mode is BacktestLogMode.MINIMAL:
            return name == "BACKTEST_RESULT"
        return False

    def wants_debug_event(self, name: str) -> bool:
        return self.mode is BacktestLogMode.DIAGNOSTIC

    def wants_trade(self) -> bool:
        return self.mode in (BacktestLogMode.DIAGNOSTIC, BacktestLogMode.FAST)

    def wants_warning_or_error(self) -> bool:
        return self.mode in (BacktestLogMode.DIAGNOSTIC, BacktestLogMode.FAST)

    def wants_section(self, name: str) -> bool:
        if self.mode is BacktestLogMode.DIAGNOSTIC:
            return True
        if self.mode is BacktestLogMode.FAST:
            return name.startswith("BACKTEST START:") or name == "BACKTEST END"
        return False

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, datetime):
            return value.isoformat(sep=" ")
        return repr(value)

    def debug(self, message: str) -> None:
        if self.mode is BacktestLogMode.DIAGNOSTIC and self._logger is not None:
            self._logger.debug(message)

    def info(self, message: str) -> None:
        if self.mode is not BacktestLogMode.NONE and self._logger is not None:
            self._logger.info(message)

    def trade(self, message: str) -> None:
        if self.wants_trade() and self._logger is not None:
            self._logger.info(f"TRADE | {message}")

    def warning(self, message: str) -> None:
        if self.wants_warning_or_error() and self._logger is not None:
            self._logger.warning(message)

    def error(self, message: str) -> None:
        if self.wants_warning_or_error() and self._logger is not None:
            self._logger.error(message)

    def section(self, name: str) -> None:
        if not self.wants_section(name) or self._logger is None:
            return
        self._logger.info("=" * 72)
        self._logger.info(f"[{name}]")
        self._logger.info("=" * 72)

    def event(self, name: str, **variables: Any) -> None:
        if not self.wants_event(name) or self._logger is None:
            return
        self._logger.info(f"[{name}]")
        for variable_name, value in variables.items():
            self._logger.info(f"    {variable_name} = {self._format_value(value)}")

    def debug_event(self, name: str, **variables: Any) -> None:
        if not self.wants_debug_event(name) or self._logger is None:
            return
        self._logger.debug(f"[{name}]")
        for variable_name, value in variables.items():
            self._logger.debug(f"    {variable_name} = {self._format_value(value)}")

    def variables(self, variables: Mapping[str, Any], *, level: str = "debug") -> None:
        if self.mode is not BacktestLogMode.DIAGNOSTIC:
            return
        log_method = getattr(self, level.lower(), self.debug)
        for variable_name, value in variables.items():
            log_method(f"{variable_name} = {self._format_value(value)}")

    def close(self) -> None:
        if self._logger is None:
            return
        for handler in list(self._logger.handlers):
            handler.flush()
            handler.close()
            self._logger.removeHandler(handler)
        self._configured_files.discard(self.log_path)
