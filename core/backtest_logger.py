"""Режимы и транспорт диагностического логирования бэктестера."""

from __future__ import annotations

import os
import time
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


class _BufferedLogWriter:
    """Быстрый файловый транспорт с крупным буфером и пакетной записью."""

    _BUFFER_SIZE = 1024 * 1024

    def __init__(self, path: str, *, reset: bool):
        file_mode = "w" if reset else "a"
        self._file = open(
            path,
            mode=file_mode,
            encoding="utf-8",
            buffering=self._BUFFER_SIZE,
        )
        self._cached_second: int | None = None
        self._cached_timestamp: str = ""

    def _timestamp(self) -> str:
        now_ns = time.time_ns()
        second = now_ns // 1_000_000_000
        if second != self._cached_second:
            self._cached_second = second
            self._cached_timestamp = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(second),
            )
        milliseconds = (now_ns // 1_000_000) % 1000
        return f"{self._cached_timestamp}.{milliseconds:03d}"

    def write(self, level: str, message: str) -> None:
        self._file.write(f"{self._timestamp()} | {level:<7} | {message}\n")

    def write_many(self, level: str, messages: list[str]) -> None:
        if not messages:
            return
        timestamp = self._timestamp
        lines = [
            f"{timestamp()} | {level:<7} | {message}\n"
            for message in messages
        ]
        self._file.write("".join(lines))

    def flush(self) -> None:
        self._file.flush()

    def close(self) -> None:
        self._file.close()


class BacktestLogger:
    """Единый файловый логгер с режимами NONE/MINIMAL/FAST/DIAGNOSTIC."""

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
        self._writer: _BufferedLogWriter | None = None

        if self.mode is BacktestLogMode.NONE:
            return

        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        self._writer = _BufferedLogWriter(self.log_path, reset=reset)

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
        if self.mode is BacktestLogMode.DIAGNOSTIC and self._writer is not None:
            self._writer.write("DEBUG", message)

    def info(self, message: str) -> None:
        if self.mode is not BacktestLogMode.NONE and self._writer is not None:
            self._writer.write("INFO", message)

    def trade(self, message: str) -> None:
        if self.wants_trade() and self._writer is not None:
            self._writer.write("INFO", f"TRADE | {message}")

    def warning(self, message: str) -> None:
        if self.wants_warning_or_error() and self._writer is not None:
            self._writer.write("WARNING", message)

    def error(self, message: str) -> None:
        if self.wants_warning_or_error() and self._writer is not None:
            self._writer.write("ERROR", message)

    def section(self, name: str) -> None:
        if not self.wants_section(name) or self._writer is None:
            return
        self._writer.write_many("INFO", [
            "=" * 72,
            f"[{name}]",
            "=" * 72,
        ])

    def event(self, name: str, **variables: Any) -> None:
        if not self.wants_event(name) or self._writer is None:
            return
        messages = [f"[{name}]"]
        messages.extend(
            f"    {variable_name} = {self._format_value(value)}"
            for variable_name, value in variables.items()
        )
        self._writer.write_many("INFO", messages)

    def debug_event(self, name: str, **variables: Any) -> None:
        if not self.wants_debug_event(name) or self._writer is None:
            return
        messages = [f"[{name}]"]
        messages.extend(
            f"    {variable_name} = {self._format_value(value)}"
            for variable_name, value in variables.items()
        )
        self._writer.write_many("DEBUG", messages)

    def variables(self, variables: Mapping[str, Any], *, level: str = "debug") -> None:
        if self.mode is not BacktestLogMode.DIAGNOSTIC or self._writer is None:
            return
        log_level = level.lower()
        if log_level == "debug":
            level_name = "DEBUG"
        elif log_level == "info":
            level_name = "INFO"
        elif log_level == "warning":
            level_name = "WARNING"
        elif log_level == "error":
            level_name = "ERROR"
        else:
            level_name = "DEBUG"
        messages = [
            f"{variable_name} = {self._format_value(value)}"
            for variable_name, value in variables.items()
        ]
        self._writer.write_many(level_name, messages)

    def close(self) -> None:
        if self._writer is None:
            return
        self._writer.flush()
        self._writer.close()
        self._writer = None
