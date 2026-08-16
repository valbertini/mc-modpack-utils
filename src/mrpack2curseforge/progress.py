"""Abstração de progresso.

O `Converter` não sabe se está rodando no terminal ou na interface web: ele só
avisa o `Reporter` o que está acontecendo.
"""

from typing import Protocol

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


class Reporter(Protocol):
    def start(self) -> None: ...
    def stop(self) -> None: ...
    def stage(self, name: str, total: int | None = None) -> None: ...
    def advance(self, amount: int = 1) -> None: ...
    def log(self, message: str) -> None: ...
    def info(self, message: str) -> None: ...


class ConsoleReporter:
    """Barras de progresso e logs com `rich`, para o CLI."""

    def __init__(self, console: Console | None = None):
        self.console = console or Console()
        self.progress: Progress | None = None
        self.task = None

    def start(self) -> None:
        if self.progress is not None:
            return

        self.progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.console,
        )
        self.progress.start()

    def stop(self) -> None:
        if self.progress is not None:
            self.progress.stop()
            self.progress = None
            self.task = None

    def stage(self, name: str, total: int | None = None) -> None:
        if total is None:
            self.task = None
            self.info(f"[dim]{name}[/dim]")
            return

        self.start()
        assert self.progress is not None
        self.task = self.progress.add_task(name, total=total)

    def advance(self, amount: int = 1) -> None:
        if self.progress is not None and self.task is not None:
            self.progress.advance(self.task, amount)

    def log(self, message: str) -> None:
        # `console.log` anexa "arquivo:linha" a cada linha — com centenas de mods
        # isso é só ruído (e apontaria para este arquivo, não para quem chamou)
        self.info(message)

    def info(self, message: str) -> None:
        try:
            target = self.progress.console if self.progress else self.console
            target.print(message)
        except Exception:  # noqa: BLE001
            pass
