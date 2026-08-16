"""Atualização de um `.mrpack` para outra versão do Minecraft.

Para cada arquivo do índice, procura no Modrinth a versão mais recente que serve
para a versão do Minecraft escolhida (e para o loader do pack, no caso dos mods)
e monta um `.mrpack` novo.

Nada é baixado: o índice guarda URL, tamanho e hashes de cada arquivo.
"""

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from mrpack2curseforge.builders.mrpack import build_index, build_mrpack
from mrpack2curseforge.builders.package import safe_name
from mrpack2curseforge.config import Config
from mrpack2curseforge.domain import (
    Modpack,
    ModrinthProject,
    PackFile,
    UpdateResult,
    UpdateStatus,
)
from mrpack2curseforge.exceptions import (
    ConversionCancelled,
    Mrpack2CurseForgeError,
)
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.progress import ConsoleReporter, Reporter
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.modrinth import ModrinthClient


def default_excluded(result) -> bool:
    """Sem versão para o alvo, o arquivo fica de fora até você dizer o contrário.

    O padrão é conservador de propósito: entrar no pack é a decisão que quebra o
    jogo se estiver errada, então ela é sempre sua. Na revisão, escolher uma
    versão à mão resolve o arquivo, e há um botão para trazer de uma vez o que
    não é mod (resourcepack, shader, datapack costumam funcionar além da versão
    em que foram publicados — é o caso do Simply 3D).
    """

    return not result.has_version


def _is_excluded(result, decisions: "UpdateDecisions") -> bool:
    caminho = result.mod.file_path

    if caminho in decisions.exclude:
        return True
    if caminho in decisions.include:
        return False

    return default_excluded(result)


@dataclass
class ManualPick:
    """Uma versão escolhida à mão — pode ser de outro projeto que não o detectado.

    Só o `version_id` importa para gerar o pack; o resto é o que a interface
    mostra no card sem precisar consultar a API de novo.
    """

    version_id: str
    version_number: str | None = None
    file_name: str | None = None
    project_id: str | None = None
    project_title: str | None = None

    @classmethod
    def coerce(cls, value: "str | ManualPick | dict") -> "ManualPick":
        if isinstance(value, ManualPick):
            return value
        if isinstance(value, dict):
            return cls(**value)
        return cls(version_id=str(value))


@dataclass
class UpdateDecisions:
    """O que o usuário decidiu na revisão."""

    # caminho do arquivo -> versão escolhida à mão
    versions: dict[str, ManualPick] = field(default_factory=dict)
    # manter a versão atual, mesmo havendo uma nova
    keep: set[str] = field(default_factory=set)
    # não incluir o arquivo no pack novo
    exclude: set[str] = field(default_factory=set)
    # incluir mesmo sem versão para o alvo (o contrário do padrão, para mods)
    include: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        # aceita só o id da versão, que é tudo o que o CLI e os testes precisam
        self.versions = {
            caminho: ManualPick.coerce(escolha)
            for caminho, escolha in self.versions.items()
        }


def _version_key(version: str) -> tuple[int, ...]:
    """`1.21.10` -> (1, 21, 10). Serve para saber se o alvo é anterior ao pack."""

    return tuple(int(part) for part in re.findall(r"\d+", version or "")) or (0,)


@dataclass
class UpdateOutcome:
    source: Path
    pack: Modpack
    minecraft_version: str
    loader_version: str
    results: list[UpdateResult]
    output: Path
    report_path: Path
    duration_seconds: float = 0.0
    unlisted: list[str] = field(default_factory=list)
    # alvo anterior ao Minecraft do pack: os mods vão para versões mais antigas
    downgrade: bool = False
    # o .mrpack já foi gerado (a análise sozinha não escreve nada)
    packaged: bool = False
    # loader de destino; vazio = o mesmo do pack
    loader: str = ""

    @property
    def target_loader(self) -> str:
        return self.loader or self.pack.minecraft.loader

    @property
    def loader_changed(self) -> bool:
        return self.target_loader != self.pack.minecraft.loader

    # ------------------------------------------------------------- contagens
    def count(self, status: UpdateStatus) -> int:
        return sum(1 for result in self.results if result.status is status)

    @property
    def summary(self) -> dict[str, int]:
        ativos = [r for r in self.results if not r.excluded]

        return {
            "total": len(self.results),
            "updated": sum(
                1
                for r in ativos
                if r.status is UpdateStatus.UPDATED and not r.skipped
            ),
            "kept_by_choice": sum(
                1 for r in ativos if r.skipped and r.status is UpdateStatus.UPDATED
            ),
            "unchanged": sum(
                1 for r in ativos if r.status is UpdateStatus.UNCHANGED
            ),
            "incompatible": sum(
                1 for r in ativos if r.status is UpdateStatus.INCOMPATIBLE
            ),
            "unknown": sum(1 for r in ativos if r.status is UpdateStatus.UNKNOWN),
            "manual": sum(
                1 for r in ativos if r.status is UpdateStatus.MANUAL and not r.skipped
            ),
            "excluded": sum(1 for r in self.results if r.excluded),
            "unlisted": len(self.unlisted),
        }

    @property
    def with_version(self) -> list[UpdateResult]:
        """Arquivos que têm versão para o Minecraft alvo."""
        return [result for result in self.results if result.has_version]

    @property
    def without_version(self) -> list[UpdateResult]:
        """Arquivos sem versão para o alvo — entram como estão ou ficam de fora."""
        return [result for result in self.results if not result.has_version]


class Updater:
    def __init__(
        self,
        output_dir: Path | None = None,
        workers: int | None = None,
        use_cache: bool = True,
        reporter: Reporter | None = None,
        cancel_event=None,
    ):
        self.output_dir = Path(output_dir or Config.OUTPUT_DIR)
        self.workers = max(1, workers or Config.WORKERS)
        self.use_cache = use_cache
        self.reporter = reporter or ConsoleReporter()
        self.cancel_event = cancel_event

    @property
    def cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    # ------------------------------------------------------------- pipeline
    def update(
        self,
        mrpack_path: Path,
        minecraft_version: str,
        loader_version: str | None = None,
        loader: str | None = None,
    ) -> UpdateOutcome:
        """Analisa e já gera o pack (usado pelo CLI)."""

        return self.apply(
            self.analyze(mrpack_path, minecraft_version, loader_version, loader)
        )

    def analyze(
        self,
        mrpack_path: Path,
        minecraft_version: str,
        loader_version: str | None = None,
        loader: str | None = None,
    ) -> UpdateOutcome:
        started = time.perf_counter()
        reporter = self.reporter

        mrpack_path = Path(mrpack_path)
        parser = MrpackParser(mrpack_path)
        parser.validate()
        pack = parser.parse()

        alvo_loader = (loader or pack.minecraft.loader).lower()
        troca_loader = alvo_loader != pack.minecraft.loader

        if troca_loader and not loader_version:
            raise Mrpack2CurseForgeError(
                f"Trocar de {pack.minecraft.loader} para {alvo_loader} exige a "
                f"versão do loader: a versão do {pack.minecraft.loader} que está "
                "no pack não serve para o outro."
            )

        reporter.info(
            f"[bold]{pack.name} {pack.version}[/bold] · "
            f"Minecraft {pack.minecraft.version} [dim]->[/dim] "
            f"[cyan]{minecraft_version}[/cyan] · loader "
            + (
                f"[yellow]{pack.minecraft.loader} -> {alvo_loader}[/yellow]"
                if troca_loader
                else pack.minecraft.loader
            )
        )

        if troca_loader:
            reporter.info(
                f"[yellow]--[/yellow] trocar de loader é uma mudança grande: só "
                f"entra no pack o mod que tiver versão para {alvo_loader}, e o "
                "resto vai para a revisão."
            )

        downgrade = _version_key(minecraft_version) < _version_key(
            pack.minecraft.version
        )

        if downgrade:
            reporter.info(
                f"[yellow]--[/yellow] {minecraft_version} é anterior a "
                f"{pack.minecraft.version}: os mods vão para as versões mais "
                f"recentes *daquele* Minecraft, que podem ser mais antigas."
            )

        arquivos = [*pack.mods, *pack.extra_files]
        cache = SimpleCache(Config.CACHE_PATH, enabled=self.use_cache)

        # o cache também é fechado aqui: cada conexão SQLite deixada aberta
        # trava o arquivo no Windows (o "Limpar cache" não conseguia apagar)
        with cache, ModrinthClient(cache) as modrinth:
            reporter.stage("Identificando os mods no Modrinth")
            projetos = modrinth.resolve_projects(arquivos)
            reporter.info(
                f"[dim]{len(projetos)}/{len(arquivos)} arquivos identificados[/dim]"
            )

            results = self._resolve_updates(
                arquivos, projetos, modrinth, alvo_loader, minecraft_version
            )

        self._log_plan(results, minecraft_version)

        alvo = f"mc{minecraft_version}"
        if troca_loader:
            alvo += f" {alvo_loader}"

        # o marcador entra depois do `safe_name`, que trocaria os colchetes por _
        base = safe_name(f"{pack.name} {pack.version} {alvo}").replace(" ", "-")
        output = self.output_dir / f"{base}-[atualizado].mrpack"

        outcome = UpdateOutcome(
            source=mrpack_path,
            pack=pack,
            minecraft_version=minecraft_version,
            loader_version=loader_version or pack.minecraft.loader_version,
            loader=alvo_loader,
            results=results,
            output=output,
            report_path=output.with_name(f"{output.stem}-update.json"),
            downgrade=downgrade,
        )
        outcome.duration_seconds = time.perf_counter() - started

        reporter.stop()
        return outcome

    # --------------------------------------------------------------- aplicar
    def apply(
        self,
        outcome: UpdateOutcome,
        decisions: UpdateDecisions | None = None,
    ) -> UpdateOutcome:
        """Gera o `.mrpack` com as decisões tomadas na revisão."""

        started = time.perf_counter()
        reporter = self.reporter

        self._apply_choices(outcome, decisions or UpdateDecisions())

        index = build_index(
            outcome.pack,
            outcome.results,
            outcome.minecraft_version,
            outcome.loader_version,
            outcome.target_loader,
        )

        # o índice do Modrinth exige URL; sem ela o arquivo ficaria de fora
        outcome.unlisted = [
            result.mod.file_path
            for result in outcome.results
            if not result.excluded and not result.final_file.download_url
        ]

        reporter.stage("Montando o .mrpack atualizado")
        build_mrpack(outcome.source, index, outcome.output)

        outcome.packaged = True
        outcome.duration_seconds += time.perf_counter() - started

        self._write_report(outcome)

        size_mb = outcome.output.stat().st_size / (1024 * 1024)
        reporter.info(
            f"[green]++[/green] pack atualizado: {outcome.output.name} "
            f"({size_mb:.1f} MB)"
        )
        reporter.stop()

        return outcome

    def _apply_choices(
        self, outcome: UpdateOutcome, decisions: UpdateDecisions
    ) -> None:
        """Aplica as decisões: versões escolhidas, o que não muda e o que sai."""

        reporter = self.reporter
        resolutions = decisions.versions

        if resolutions:
            reporter.info("")
            reporter.info(
                f"[bold]Aplicando {len(resolutions)} escolha(s) manual(is)[/bold]"
            )

        cache = SimpleCache(Config.CACHE_PATH, enabled=self.use_cache)

        # o cache também é fechado aqui: cada conexão SQLite deixada aberta
        # trava o arquivo no Windows (o "Limpar cache" não conseguia apagar)
        with cache, ModrinthClient(cache) as modrinth:
            for result in outcome.results:
                caminho = result.mod.file_path
                result.skipped = caminho in decisions.keep

                escolha = resolutions.get(caminho)

                if not escolha:
                    if result.status is UpdateStatus.MANUAL:
                        # escolha desfeita: volta ao que a análise tinha decidido
                        result.restore_auto()

                    # a exclusão depende de haver versão, então vem depois
                    result.excluded = _is_excluded(result, decisions)
                    continue

                versao = modrinth.version(escolha.version_id)

                if not versao:
                    reporter.log(
                        f"[red]--[/red] {result.mod.file_name}: versão "
                        f"{escolha.version_id} não encontrada"
                    )
                    result.excluded = _is_excluded(result, decisions)
                    continue

                result.status = UpdateStatus.MANUAL
                result.new_file = self._to_pack_file(result.mod, versao)
                result.to_version = versao.get("version_number")
                result.version_type = versao.get("version_type")
                # trocar de projeto é permitido: o mod certo pode ser outro
                self._retag_project(result, versao, escolha, modrinth)
                # escolher uma versão à mão resolve o "sem versão"
                result.excluded = _is_excluded(result, decisions)

                reporter.log(
                    f"[green]++[/green] {result.mod.file_name} [dim]->[/dim] "
                    f"[cyan]{result.to_version}[/cyan] (escolha manual)"
                )

    @staticmethod
    def _retag_project(
        result: UpdateResult,
        versao: dict,
        escolha: ManualPick,
        modrinth: ModrinthClient,
    ) -> None:
        """Se a versão veio de outro projeto, o card passa a mostrar esse projeto."""

        novo_id = versao.get("project_id") or escolha.project_id
        atual = result.modrinth.project_id if result.modrinth else None

        if not novo_id or novo_id == atual:
            return

        titulo = escolha.project_title
        slug = None

        if not titulo:
            info = modrinth.project_info(novo_id) or {}
            titulo = info.get("title")
            slug = info.get("slug")

        result.modrinth = ModrinthProject(
            project_id=novo_id,
            slug=slug or (result.modrinth.slug if result.modrinth else None),
            title=titulo or novo_id,
        )

    # ------------------------------------------------------------- consultas
    def _resolve_updates(
        self,
        arquivos: list[PackFile],
        projetos: dict[str, ModrinthProject],
        modrinth: ModrinthClient,
        alvo_loader: str,
        minecraft_version: str,
    ) -> list[UpdateResult]:
        reporter = self.reporter
        ordem = {arquivo.file_path: i for i, arquivo in enumerate(arquivos)}
        results: list[UpdateResult] = []

        reporter.stage(
            f"Procurando versões para o Minecraft {minecraft_version}",
            total=len(arquivos),
        )

        def resolver(arquivo: PackFile) -> UpdateResult:
            projeto = projetos.get(arquivo.file_path)

            if not projeto:
                return UpdateResult(mod=arquivo, status=UpdateStatus.UNKNOWN)

            # o filtro de loader só vale para mods; resourcepack/shader não têm
            loader = alvo_loader if arquivo.is_mod else None
            versao = modrinth.latest_version(
                projeto.project_id, minecraft_version, loader
            )

            if not versao:
                return UpdateResult(
                    mod=arquivo,
                    status=UpdateStatus.INCOMPATIBLE,
                    modrinth=projeto,
                    from_version=projeto.version_number,
                )

            arquivo_novo = self._to_pack_file(arquivo, versao)
            mudou = (arquivo_novo.sha1 or "") != (arquivo.sha1 or "")

            return UpdateResult(
                mod=arquivo,
                status=UpdateStatus.UPDATED if mudou else UpdateStatus.UNCHANGED,
                modrinth=projeto,
                from_version=projeto.version_number,
                to_version=versao.get("version_number"),
                version_type=versao.get("version_type"),
                new_file=arquivo_novo if mudou else None,
            )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(resolver, item): item for item in arquivos}

            for future in as_completed(futures):
                if self.cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise ConversionCancelled("Atualização cancelada pelo usuário")

                arquivo = futures[future]

                try:
                    result = future.result()
                except Exception:  # noqa: BLE001 - um arquivo não derruba o resto
                    result = UpdateResult(mod=arquivo, status=UpdateStatus.UNKNOWN)

                results.append(result)
                reporter.log(self._result_line(result))
                reporter.advance()

        results.sort(key=lambda r: ordem.get(r.mod.file_path, 0))

        for result in results:
            # guarda o automático (para desfazer escolhas) e o padrão de inclusão
            result.remember_auto()
            result.excluded = default_excluded(result)

        return results

    @staticmethod
    def _to_pack_file(original: PackFile, versao: dict) -> PackFile:
        arquivo = versao.get("file") or {}
        nome = arquivo.get("filename") or original.file_name

        # um mod desativado continua desativado depois de atualizar
        if original.file_name.endswith(".disabled") and not nome.endswith(".disabled"):
            nome = f"{nome}.disabled"

        caminho = original.file_path
        pasta = caminho.rsplit("/", 1)[0] if "/" in caminho else ""

        return PackFile(
            file_name=nome,
            file_path=f"{pasta}/{nome}" if pasta else nome,
            download_url=arquivo.get("url"),
            sha1=arquivo.get("sha1"),
            sha512=arquivo.get("sha512"),
            file_size=arquivo.get("size"),
            env=original.env,
        )

    # -------------------------------------------------------------- relatório
    @staticmethod
    def _result_line(result: UpdateResult) -> str:
        nome = result.mod.file_name

        if result.status is UpdateStatus.UPDATED:
            return (
                f"[green]++[/green] {nome} [dim]->[/dim] "
                f"[cyan]{result.to_version}[/cyan]"
            )
        if result.status is UpdateStatus.UNCHANGED:
            return f"[dim]== {nome} (já está na versão mais recente)[/dim]"
        if result.status is UpdateStatus.INCOMPATIBLE:
            return f"[yellow]--[/yellow] {nome} (sem versão para o alvo)"

        return f"[red]--[/red] {nome} (não identificado no Modrinth)"

    def _log_plan(self, results: list[UpdateResult], minecraft_version: str) -> None:
        reporter = self.reporter

        def por_status(status: UpdateStatus) -> list[UpdateResult]:
            return sorted(
                (r for r in results if r.status is status),
                key=lambda r: r.mod.file_name.lower(),
            )

        atualizados = por_status(UpdateStatus.UPDATED)
        iguais = por_status(UpdateStatus.UNCHANGED)
        incompativeis = por_status(UpdateStatus.INCOMPATIBLE)
        desconhecidos = por_status(UpdateStatus.UNKNOWN)

        reporter.info("")
        reporter.info(f"[bold]Atualização para o Minecraft {minecraft_version}[/bold]")
        reporter.info("")

        if atualizados:
            reporter.info(f"[green]++[/green] {len(atualizados)} mod(s) atualizados:")
            for result in atualizados:
                reporter.info(f"     [green]++[/green] {result.mod.file_name}")
                marca = (
                    ""
                    if result.version_type == "release"
                    else f" [yellow]({result.version_type})[/yellow]"
                )
                reporter.info(
                    f"        [dim]{result.from_version or '?'} -> [/dim]"
                    f"[cyan]{result.to_version}[/cyan]{marca}"
                )

        if iguais:
            reporter.info("")
            reporter.info(
                f"[dim]== {len(iguais)} já estavam na versão mais recente "
                f"(não listados)[/dim]"
            )

        sem_versao = incompativeis + desconhecidos

        if sem_versao:
            reporter.info("")
            reporter.info(
                f"[yellow]--[/yellow] {len(sem_versao)} sem versão para o "
                f"Minecraft {minecraft_version}:"
            )
            for result in sorted(sem_versao, key=lambda r: r.mod.file_name.lower()):
                fora = default_excluded(result)
                cor = "red" if fora else "yellow"
                destino = "fica de fora" if fora else "entra como está"

                reporter.info(
                    f"     [{cor}]--[/{cor}] {result.mod.file_name} "
                    f"[dim]({destino})[/dim]"
                )
                if result.modrinth and result.modrinth.title:
                    reporter.info(
                        f"        [dim]no Modrinth:[/dim] "
                        f"[cyan]{result.modrinth.title}[/cyan]"
                    )

        reporter.info("")
        reporter.info(
            "[bold]Resumo:[/bold] "
            f"[green]{len(atualizados)}[/green] atualizados · "
            f"[dim]{len(iguais)}[/dim] já atuais · "
            f"[yellow]{len(incompativeis)}[/yellow] sem versão · "
            f"[red]{len(desconhecidos)}[/red] desconhecidos"
        )
        reporter.info("")

    @staticmethod
    def _write_report(outcome: UpdateOutcome) -> None:
        payload = {
            "source": outcome.source.name,
            "output": outcome.output.name,
            "pack": {"name": outcome.pack.name, "version": outcome.pack.version},
            "from_minecraft": outcome.pack.minecraft.version,
            "to_minecraft": outcome.minecraft_version,
            "loader": f"{outcome.target_loader}-{outcome.loader_version}",
            "from_loader": outcome.pack.minecraft.loader,
            "summary": outcome.summary,
            "duration_seconds": round(outcome.duration_seconds, 1),
            "files": [
                {
                    "file_name": result.mod.file_name,
                    "path": result.mod.file_path,
                    "status": result.status.value,
                    "modrinth_title": (
                        result.modrinth.title if result.modrinth else None
                    ),
                    "from_version": result.from_version,
                    "to_version": result.to_version,
                    "version_type": result.version_type,
                    "new_file_name": (
                        result.new_file.file_name if result.new_file else None
                    ),
                    # o que o usuário decidiu na revisão
                    "skipped": result.skipped,
                    "excluded": result.excluded,
                }
                for result in outcome.results
            ],
            "unlisted": outcome.unlisted,
        }

        outcome.report_path.parent.mkdir(parents=True, exist_ok=True)
        outcome.report_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
