"""Números da conversão + resumo no terminal.

A persistência em disco é feita por `records.py` — o registro é um superconjunto
deste relatório e ainda permite regerar o modpack.
"""

from pathlib import Path

from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from mrpack2curseforge.domain import MatchResult, MissingReason, Modpack


class ConversionReport(BaseModel):
    source: str
    output: str | None = None
    pack_name: str
    pack_version: str
    minecraft_version: str
    loader: str

    total_mods: int = 0
    matched: int = 0
    overrides: int = 0
    version_unavailable: int = 0
    not_on_curseforge: int = 0
    failed: int = 0
    extra_files: int = 0
    override_files: int = 0

    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        return (self.matched / self.total_mods * 100) if self.total_mods else 0.0


def build_report(
    pack: Modpack, results: list[MatchResult], source: Path
) -> ConversionReport:
    """Conta os resultados. O detalhe mod a mod vive no registro."""

    status = [result.status for result in results]

    return ConversionReport(
        source=str(source),
        pack_name=pack.name,
        pack_version=pack.version,
        minecraft_version=pack.minecraft.version,
        loader=pack.loader_id,
        total_mods=len(results),
        extra_files=len(pack.extra_files),
        override_files=len(pack.override_paths),
        matched=status.count("curseforge"),
        version_unavailable=status.count(MissingReason.VERSION_UNAVAILABLE.value),
        not_on_curseforge=status.count(MissingReason.NOT_ON_CURSEFORGE.value),
        failed=status.count("failed"),
        overrides=sum(1 for s in status if s not in ("curseforge", "failed")),
    )


def render_update_summary(console: Console, outcome) -> None:
    """Tabela final do `update` (o detalhe mod a mod já saiu no plano)."""

    summary = outcome.summary
    pack = outcome.pack

    table = Table(title=f"Atualização — {pack.name} {pack.version}")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", justify="right", style="bold")

    table.add_row(
        "Minecraft",
        f"{pack.minecraft.version} → {outcome.minecraft_version}",
    )
    table.add_row("Loader", f"{pack.minecraft.loader}-{outcome.loader_version}")
    table.add_row("Arquivos no índice", str(summary["total"]))
    table.add_row(
        "Trocados de versão" if outcome.downgrade else "Atualizados",
        f"[green]{summary['updated']}[/green]",
    )
    table.add_row("Já na versão mais recente", f"[dim]{summary['unchanged']}[/dim]")
    table.add_row(
        "Sem versão para o alvo", f"[yellow]{summary['incompatible']}[/yellow]"
    )
    table.add_row("Não identificados", f"[red]{summary['unknown']}[/red]")
    table.add_row(
        "Deixados de fora do pack novo", f"[yellow]{summary['excluded']}[/yellow]"
    )

    if summary["manual"]:
        table.add_row("Versão escolhida à mão", f"[cyan]{summary['manual']}[/cyan]")

    if summary["unlisted"]:
        table.add_row("Fora do índice novo", f"[red]{summary['unlisted']}[/red]")

    table.add_row("Duração", f"{outcome.duration_seconds:.1f}s")

    console.print(table)
    console.print(f"[green]Pack atualizado:[/green] {outcome.output}")
    console.print(f"[dim]Relatório: {outcome.report_path.name}[/dim]")


def render_summary(console: Console, report: ConversionReport) -> None:
    """Tabela final do CLI. O detalhamento mod a mod já saiu no resumo da análise."""

    table = Table(title=f"Resumo — {report.pack_name} {report.pack_version}")
    table.add_column("Métrica", style="cyan")
    table.add_column("Valor", justify="right", style="bold")

    table.add_row("Minecraft", f"{report.minecraft_version} ({report.loader})")
    table.add_row("Arquivos procurados lá", str(report.total_mods))
    table.add_row("Encontrados no CurseForge", f"[green]{report.matched}[/green]")
    table.add_row("Enviados para overrides", f"[yellow]{report.overrides}[/yellow]")
    table.add_row(
        "  · versão indisponível no CurseForge",
        f"[yellow]{report.version_unavailable}[/yellow]",
    )
    table.add_row(
        "  · projeto não existe no CurseForge",
        f"[red]{report.not_on_curseforge}[/red]",
    )
    if report.failed:
        table.add_row("Falhas de download", f"[red]{report.failed}[/red]")
    table.add_row("Taxa de conversão", f"{report.success_rate:.1f}%")
    table.add_row("Arquivos extras (não-mods)", str(report.extra_files))
    table.add_row("Arquivos de overrides do mrpack", str(report.override_files))
    table.add_row("Duração", f"{report.duration_seconds:.1f}s")

    console.print(table)
