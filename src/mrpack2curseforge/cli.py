"""Interface de linha de comando.

Uso normal (o que 99% das vezes você quer):

    uv run mrpack2curseforge

Isso converte todos os `.mrpack` de `input_modpacks/` e escreve os `.zip`
prontos para o CurseForge em `output_modpacks/`.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console

from mrpack2curseforge.config import Config
from mrpack2curseforge.exceptions import Mrpack2CurseForgeError
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.progress import ConsoleReporter
from mrpack2curseforge.reporting import render_summary, render_update_summary

app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    help="Converte modpacks Modrinth (.mrpack) para o formato do CurseForge.",
)


def _force_utf8() -> None:
    """Evita UnicodeEncodeError no console do Windows (cp1252).

    Vários projetos do CurseForge têm emoji no nome (ex.: "Jade 🔍").
    """

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


_force_utf8()

console = Console()


# --------------------------------------------------------------------------- #
# Comando padrão (sem subcomando): converte tudo que está em input_modpacks/
# --------------------------------------------------------------------------- #
@app.callback()
def main(
    ctx: typer.Context,
    input_dir: Path = typer.Option(
        None, "--input", "-i", help="Pasta com os .mrpack de entrada."
    ),
    output_dir: Path = typer.Option(
        None, "--output", "-o", help="Pasta onde os modpacks convertidos são salvos."
    ),
    workers: int = typer.Option(
        None, "--workers", "-w", help="Mods processados em paralelo."
    ),
    no_cache: bool = typer.Option(
        False, "--no-cache", help="Ignora o cache local das APIs."
    ),
    keep_work: bool = typer.Option(
        False, "--keep-work", help="Mantém a pasta temporária usada no empacotamento."
    ),
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    source_dir = Path(input_dir or Config.INPUT_DIR)
    target_dir = Path(output_dir or Config.OUTPUT_DIR)

    source_dir.mkdir(parents=True, exist_ok=True)
    target_dir.mkdir(parents=True, exist_ok=True)

    packs = sorted(
        p for p in source_dir.glob("*.mrpack") if p.is_file()
    )

    if not packs:
        console.print(
            f"[yellow]Nenhum .mrpack encontrado em[/yellow] {source_dir}\n"
            "Coloque o arquivo do modpack nessa pasta e rode o comando de novo."
        )
        raise typer.Exit(code=0)

    console.print(
        f"[bold]{len(packs)}[/bold] modpack(s) na fila · "
        f"saída em [bold]{target_dir}[/bold]"
    )

    failures = 0

    for pack_path in packs:
        try:
            outcome = _convert_one(
                pack_path,
                output_dir=target_dir,
                workers=workers,
                use_cache=not no_cache,
                keep_work=keep_work,
            )
            render_summary(console, outcome.report)

        except (Mrpack2CurseForgeError, RuntimeError, FileNotFoundError) as exc:
            failures += 1
            console.print(f"[red]Erro ao converter {pack_path.name}:[/red] {exc}")

        except Exception as exc:  # noqa: BLE001
            failures += 1
            console.print(
                f"[red]Erro inesperado ao converter {pack_path.name}:[/red] {exc}"
            )
            console.print_exception(max_frames=6)

    if failures:
        console.print(f"[red]{failures} modpack(s) falharam.[/red]")
        raise typer.Exit(code=1)

    console.print("[bold green]Tudo pronto![/bold green]")


# --------------------------------------------------------------------------- #
# Subcomandos
# --------------------------------------------------------------------------- #
@app.command()
def convert(
    file: Path = typer.Argument(..., help="Caminho de um .mrpack específico."),
    output_dir: Path = typer.Option(None, "--output", "-o"),
    workers: int = typer.Option(None, "--workers", "-w"),
    no_cache: bool = typer.Option(False, "--no-cache"),
    keep_work: bool = typer.Option(False, "--keep-work"),
) -> None:
    """Converte um único arquivo .mrpack."""

    try:
        outcome = _convert_one(
            file,
            output_dir=Path(output_dir or Config.OUTPUT_DIR),
            workers=workers,
            use_cache=not no_cache,
            keep_work=keep_work,
        )
    except (Mrpack2CurseForgeError, RuntimeError, FileNotFoundError) as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)

    render_summary(console, outcome.report)


@app.command()
def update(
    file: Path = typer.Argument(..., help="Caminho de um .mrpack."),
    minecraft: str = typer.Option(
        ..., "--minecraft", "-m", help="Versão do Minecraft de destino (ex.: 1.21.1)."
    ),
    loader: str = typer.Option(
        None,
        "--loader",
        help="Trocar de modloader (fabric, forge, neoforge, quilt). "
        "Exige --loader-version.",
    ),
    loader_version: str = typer.Option(
        None, "--loader-version", help="Versão do loader (padrão: a mesma do pack)."
    ),
    output_dir: Path = typer.Option(None, "--output", "-o"),
    workers: int = typer.Option(None, "--workers", "-w"),
    no_cache: bool = typer.Option(False, "--no-cache"),
) -> None:
    """Atualiza os mods de um .mrpack para outra versão do Minecraft."""

    from mrpack2curseforge.updater import Updater

    updater = Updater(
        output_dir=Path(output_dir or Config.OUTPUT_DIR),
        workers=workers,
        use_cache=not no_cache,
        reporter=ConsoleReporter(console),
    )

    try:
        outcome = updater.update(file, minecraft, loader_version, loader)
    except (Mrpack2CurseForgeError, FileNotFoundError) as exc:
        console.print(f"[red]Erro:[/red] {exc}")
        raise typer.Exit(code=1)

    render_update_summary(console, outcome)


@app.command()
def versions(limit: int = typer.Option(20, help="Quantas versões listar.")) -> None:
    """Lista as versões do Minecraft aceitas em `update`."""

    from mrpack2curseforge.services.cache import SimpleCache
    from mrpack2curseforge.services.modrinth import ModrinthClient

    with SimpleCache(Config.CACHE_PATH) as cache, ModrinthClient(cache) as modrinth:
        disponiveis = modrinth.game_versions()

    console.print(" · ".join(disponiveis[:limit]))
    console.print(f"[dim]{len(disponiveis)} versões de release conhecidas[/dim]")


@app.command()
def inspect(file: Path = typer.Argument(..., help="Caminho de um .mrpack.")) -> None:
    """Mostra o conteúdo de um .mrpack sem converter nada (não usa rede)."""

    parser = MrpackParser(file)
    parser.validate()
    pack = parser.parse()

    console.rule(f"[bold]{pack.name} {pack.version}")
    console.print(f"Minecraft: {pack.minecraft.version}")
    console.print(f"Loader: {pack.loader_id}")
    console.print(f"Mods: {len(pack.mods)}")
    console.print(f"Outros arquivos do índice: {len(pack.extra_files)}")
    console.print(f"Arquivos em overrides/: {len(pack.override_paths)}")

    for mod in pack.mods:
        console.print(f"  · {mod.file_name}", style="dim")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", help="Endereço de escuta (local)."),
    port: int = typer.Option(8000, help="Porta do servidor."),
    input_dir: Path = typer.Option(None, "--input", "-i"),
    output_dir: Path = typer.Option(None, "--output", "-o"),
    open_browser: bool = typer.Option(
        True, "--open/--no-open", help="Abre o navegador."
    ),
) -> None:
    """Sobe a interface web local (converter, resolver conflitos e baixar)."""

    try:
        from mrpack2curseforge.web.server import serve
    except ImportError as exc:
        console.print(
            "[red]Dependências da interface web ausentes.[/red] "
            "Rode: [bold]uv sync[/bold]"
        )
        raise typer.Exit(code=1) from exc

    url = f"http://{host}:{port}"

    console.print(f"[bold green]Interface web em[/bold green] {url}")
    console.print("[dim]Tudo roda localmente. Ctrl+C para encerrar.[/dim]")

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    serve(
        host=host,
        port=port,
        input_dir=Path(input_dir) if input_dir else None,
        output_dir=Path(output_dir) if output_dir else None,
    )


@app.command("clear-cache")
def clear_cache() -> None:
    """Apaga o cache local das consultas às APIs."""

    from mrpack2curseforge.services.cache import clear_cache as limpar

    resultado = limpar(Config.CACHE_PATH)

    if resultado["removed"]:
        console.print(
            f"[green]Cache removido[/green] ({resultado['freed_mb']} MB): "
            + ", ".join(resultado["removed"])
        )
    else:
        console.print("Nenhum cache para remover.")

    if resultado["locked"]:
        console.print(
            "[yellow]Em uso, não removidos:[/yellow] "
            + ", ".join(resultado["locked"])
            + " — feche a interface web e tente de novo."
        )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _convert_one(
    file: Path,
    output_dir: Path,
    workers: int | None,
    use_cache: bool,
    keep_work: bool,
):
    from mrpack2curseforge.converter import Converter

    converter = Converter(
        output_dir=output_dir,
        workers=workers,
        use_cache=use_cache,
        keep_work=keep_work,
        reporter=ConsoleReporter(console),
    )
    return converter.convert(file)


if __name__ == "__main__":
    app()
