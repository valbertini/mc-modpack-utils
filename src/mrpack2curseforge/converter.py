"""Orquestração da conversão `.mrpack` -> modpack CurseForge (`.zip`).

A conversão tem duas fases, para que a interface possa parar no meio e deixar o
usuário resolver os conflitos antes de baixar qualquer coisa:

    analyze()  -> lê o pack e descobre o que existe no CurseForge (só consulta)
    finish()   -> aplica escolhas, baixa o que falta e gera o .zip
"""

import json
import shutil
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from mrpack2curseforge.builders.curseforge_manifest import CurseForgeManifestBuilder
from mrpack2curseforge.builders.package import build_zip, safe_name
from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import WORK_DIRNAME
from mrpack2curseforge.domain import (
    MatchResult,
    MatchStrategy,
    MissingReason,
    Modpack,
    PackFile,
)
from mrpack2curseforge.exceptions import ConversionCancelled
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.progress import ConsoleReporter, Reporter
from mrpack2curseforge.records import (
    build_record,
    record_path,
    results_from_record,
    save_record,
)
from mrpack2curseforge.reporting import ConversionReport, build_report
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.curseforge import CurseForgeClient
from mrpack2curseforge.services.downloader import Downloader
from mrpack2curseforge.services.matcher import CurseForgeMatcher
from mrpack2curseforge.services.modrinth import ModrinthClient


@dataclass
class Resolution:
    """Escolha manual feita pelo usuário na interface web."""

    project_id: int
    file_id: int
    project_name: str | None = None
    project_slug: str | None = None
    file_name: str | None = None


def _bytes(files: "list[PackFile]") -> int:
    """Soma tamanhos ignorando o que o índice não declarou."""

    return sum(f.file_size or 0 for f in files)


def _mb(size: int) -> float:
    return round(size / (1024 * 1024), 1)


@dataclass
class ConversionOutcome:
    """Tudo que a conversão produziu (permite reempacotar depois)."""

    source: Path
    pack: Modpack
    results: list[MatchResult]
    report: ConversionReport
    output: Path
    record_path: Path
    work_dir: Path | None = None
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    packaged: bool = False
    started_at: float = field(default_factory=time.perf_counter)

    # ------------------------------------------------------------- conveniência
    @property
    def pending(self) -> list[MatchResult]:
        """Mods que ainda não entraram no manifest."""
        return [result for result in self.results if not result.matched]

    def plan(self, resolutions: dict[str, "Resolution"] | None = None) -> dict:
        """Resumo do que o `finish()` vai fazer (mostrado antes de confirmar).

        `resolutions` são as escolhas ainda **não aplicadas**: sem elas o plano
        mostraria downloads que já não vão acontecer.

        `zip_mb` é o tamanho estimado do arquivo que vai nascer: o que fica em
        `overrides/` (peso comprimido, do zip de origem) menos o que subiu para
        o manifest, mais o que vai ser baixado para lá (`fileSize` do índice do
        Modrinth). É o único número que o usuário leva para o disco, e por isso
        o único tamanho do painel de confirmação.
        """

        resolutions = resolutions or {}
        manual = 0
        no_manifest: list[PackFile] = []
        pendentes: list[PackFile] = []

        for result in self.results:
            if result.mod.file_name in resolutions:
                manual += 1
                no_manifest.append(result.mod)
            elif result.matched and result.strategy is MatchStrategy.MANUAL:
                # escolha manual desfeita: volta para overrides
                pendentes.append(result.mod)
            elif result.matched:
                no_manifest.append(result.mod)
            else:
                pendentes.append(result.mod)

        # o que veio de overrides/ nunca é baixado: ele já está no disco.
        # Junto vai o que nem chega a ser procurado lá (config, datapack…)
        baixar = [f for f in pendentes if not f.from_overrides]
        baixar += self.pack.plain_extras

        # quem sai do overrides/ para o manifest deixa de pesar no zip
        vindos = [f for f in no_manifest if f.from_overrides]
        poupado = _bytes(vindos)
        baixado = _bytes(baixar)

        return {
            "manifest": len(no_manifest),
            "manual": manual,
            # quantos saem do overrides/ do mrpack direto para o manifest
            "from_overrides": len(vindos),
            "downloads": len(baixar),
            "download_mods": sum(1 for f in baixar if f.is_mod),
            "zip_mb": _mb(max(self.pack.override_bytes - poupado, 0) + baixado),
        }


class Converter:
    def __init__(
        self,
        output_dir: Path | None = None,
        workers: int | None = None,
        use_cache: bool = True,
        keep_work: bool = False,
        reporter: Reporter | None = None,
        cancel_event: threading.Event | None = None,
    ):
        self.output_dir = Path(output_dir or Config.OUTPUT_DIR)
        self.workers = max(1, workers or Config.WORKERS)
        self.use_cache = use_cache
        self.keep_work = keep_work
        self.reporter = reporter or ConsoleReporter()
        self.cancel_event = cancel_event

    # ---------------------------------------------------------- cancelamento
    @property
    def cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    def _check_cancel(self) -> None:
        if self.cancelled:
            raise ConversionCancelled("Conversão cancelada pelo usuário")

    # --------------------------------------------------------------- fase 1
    def analyze(self, mrpack_path: Path) -> ConversionOutcome:
        """Lê o pack e procura cada mod no CurseForge. Não baixa nem escreve nada."""

        started = time.perf_counter()
        reporter = self.reporter

        mrpack_path = Path(mrpack_path)
        parser = MrpackParser(mrpack_path)
        parser.validate()
        pack = parser.parse()

        reporter.info(
            f"[bold]{pack.name} {pack.version}[/bold] · Minecraft "
            f"{pack.minecraft.version} · loader {pack.loader_id} · "
            f"{len(pack.mods)} mods · {len(pack.extra_files)} arquivos extras · "
            f"{len(pack.override_paths)} arquivos em overrides/"
        )

        cache = SimpleCache(Config.CACHE_PATH, enabled=self.use_cache)
        # o cache entra no `with`: sem isso cada análise deixa uma conexão
        # SQLite aberta, e no Windows o arquivo não pode mais ser apagado

        with cache, ModrinthClient(cache) as modrinth, CurseForgeClient(
            cache
        ) as curseforge:
            self._check_cancel()

            indexed = [*pack.mods, *pack.extra_files]

            reporter.stage("Consultando o Modrinth para descobrir os nomes dos mods")
            # os candidatos de overrides/ ficam de fora: não têm hash no índice
            # (é por não estarem no Modrinth que viajam dentro do overrides/)
            modrinth_map = modrinth.resolve_projects(indexed)
            reporter.info(
                f"[dim]{len(modrinth_map)}/{len(indexed)} arquivos identificados "
                f"no Modrinth[/dim]"
            )

            results = self._match_files(pack, modrinth_map, curseforge, modrinth)

        cache.flush()

        self._log_analysis(results)

        base_name = self._base_name(pack)

        return ConversionOutcome(
            source=mrpack_path,
            pack=pack,
            results=results,
            report=build_report(pack, results, mrpack_path),
            output=self.output_dir / f"{base_name}.zip",
            record_path=record_path(self.output_dir, base_name),
            started_at=started,
        )

    # --------------------------------------------------------------- fase 2
    def finish(
        self,
        outcome: ConversionOutcome,
        resolutions: dict[str, Resolution] | None = None,
    ) -> ConversionOutcome:
        """Aplica as escolhas manuais, baixa o que falta e gera o `.zip`.

        Pode ser chamada de novo depois que o usuário mudar de ideia: a pasta de
        trabalho é reaproveitada, então nada é baixado duas vezes.
        """

        resolutions = dict(resolutions or {})
        self._apply_resolutions(outcome, resolutions)

        self.reporter.info("")
        self.reporter.info(
            "[bold]Aplicando as mudanças[/bold] — baixando o que falta e gerando o .zip"
        )
        if resolutions:
            self.reporter.info(
                f"[dim]{len(resolutions)} escolha(s) manual(is) "
                f"entram no manifest[/dim]"
            )
        self.reporter.info("")

        parser = MrpackParser(outcome.source)
        reuse = outcome.work_dir is not None and outcome.work_dir.exists()

        output, work_dir, failures = self._assemble(
            outcome.pack,
            outcome.results,
            parser,
            reuse=reuse,
            # regerar uma conversão antiga não pode renomeá-la
            base_name=outcome.output.stem,
        )

        report = build_report(outcome.pack, outcome.results, outcome.source)
        report.failed = failures
        report.output = str(output)
        report.duration_seconds = time.perf_counter() - outcome.started_at

        outcome.report = report
        outcome.output = output
        outcome.work_dir = work_dir
        outcome.resolutions = resolutions
        outcome.packaged = True

        # o registro é o que sobrevive: o .zip pode ser regerado a partir dele
        save_record(build_record(outcome, resolutions), self.output_dir)
        self.reporter.info(f"[dim]Registro salvo: {outcome.record_path.name}[/dim]")
        self.reporter.stop()

        return outcome

    # ----------------------------------------------------------- regeneração
    def rebuild(self, record: dict, source_path: Path) -> ConversionOutcome:
        """Remonta o `.zip` de uma conversão antiga, a partir do registro.

        Não consulta o CurseForge: os `(projectID, fileID)` já estão no registro.
        Só é preciso rebaixar os arquivos que vão para `overrides/`.
        """

        started = time.perf_counter()

        parser = MrpackParser(source_path)
        parser.validate()
        pack = parser.parse()

        results = results_from_record(record, pack)

        # o nome vem do próprio registro: regerar não pode renomear uma conversão
        # antiga (o registro é encontrado pelo `id`)
        base_name = record.get("id") or self._base_name(pack)

        resolutions = {
            file_name: Resolution(**data)
            for file_name, data in (record.get("resolutions") or {}).items()
        }

        outcome = ConversionOutcome(
            source=source_path,
            pack=pack,
            results=results,
            report=build_report(pack, results, source_path),
            output=self.output_dir / f"{base_name}.zip",
            record_path=record_path(self.output_dir, base_name),
            started_at=started,
        )

        self.reporter.info(
            f"[bold]{pack.name} {pack.version}[/bold] · regerando a partir do "
            f"registro ({len(results)} mods, sem consultar o CurseForge)"
        )

        return self.finish(outcome, resolutions)

    # ------------------------------------------------------------- fase 1 + 2
    def convert(self, mrpack_path: Path) -> ConversionOutcome:
        """Conversão completa e sem interação (usada pelo CLI)."""

        return self.finish(self.analyze(mrpack_path))

    # -------------------------------------------------------------- relatório
    @staticmethod
    def _result_line(result: MatchResult) -> str:
        """Uma linha por mod, no estilo do `terraform`, enquanto a busca roda."""

        name = result.mod.file_name

        if result.error:
            return f"[red]--[/red] {name}: erro ({result.error})"

        if result.matched:
            return f"[green]++[/green] {name} -> {result.project_name}"

        diagnosis = result.diagnosis

        if diagnosis and diagnosis.reason is MissingReason.VERSION_UNAVAILABLE:
            return (
                f"[yellow]--[/yellow] {name} -> sem essa versão "
                f"({diagnosis.project_name})"
            )

        return f"[red]--[/red] {name} -> sem projeto no CurseForge"

    def _log_analysis(self, results: list[MatchResult]) -> None:
        """Imprime o resultado da análise no estilo do `terraform plan`.

        Os que deram certo não são listados (num pack grande são centenas); o que
        importa é o que exige decisão, agrupado por motivo.
        """

        reporter = self.reporter

        # a classificação é a do `MatchResult.status`, a mesma que o relatório e
        # o registro usam: repetida aqui, ela divergiria na primeira mudança
        grupos: dict[str, list[MatchResult]] = defaultdict(list)
        for result in sorted(results, key=lambda r: r.mod.file_name.lower()):
            grupos[result.status].append(result)

        ok = grupos["curseforge"]
        failed = grupos["failed"]
        version_unavailable = grupos[MissingReason.VERSION_UNAVAILABLE.value]
        # sem projeto e sem diagnóstico caem no mesmo balde: não há o que dizer
        missing = grupos[MissingReason.NOT_ON_CURSEFORGE.value] + grupos[
            MissingReason.UNKNOWN.value
        ]

        # os que estavam em overrides/ e agora têm projeto: o pack encolhe
        promoted = [r for r in ok if r.mod.from_overrides]

        reporter.info("")
        reporter.info("[bold]Resultado da análise[/bold]")
        reporter.info("")

        if ok:
            reporter.info(
                f"[green]++[/green] {len(ok)} arquivo(s) encontrados no CurseForge "
                f"(não listados)"
            )

        if promoted:
            reporter.info(
                f"[green]++[/green] {len(promoted)} deles vinham do "
                f"[cyan]overrides/[/cyan] do mrpack e saem de lá"
            )

        if version_unavailable:
            reporter.info("")
            reporter.info(
                f"[yellow]--[/yellow] {len(version_unavailable)} arquivo(s) sem "
                f"essa versão no CurseForge (vão para overrides):"
            )
            for result in version_unavailable:
                diagnosis = result.diagnosis
                if diagnosis is None:  # a lista já foi filtrada; só para o type checker
                    continue

                reporter.info(f"     [yellow]--[/yellow] {result.mod.file_name}")
                # a semelhança fica na linha curta: assim o nome longo do arquivo
                # pode quebrar sem deixar o "(93%)" sozinho na linha seguinte
                reporter.info(
                    f"        [dim]no CurseForge:[/dim] "
                    f"[cyan]{diagnosis.project_name}[/cyan] "
                    f"[dim]({diagnosis.similarity:.0%})[/dim]"
                )
                reporter.info(
                    f"        [dim]versão lá:[/dim] "
                    f"[yellow]{diagnosis.closest_file_name}[/yellow]"
                )

        if missing:
            reporter.info("")
            reporter.info(
                f"[red]--[/red] {len(missing)} arquivo(s) sem projeto no "
                f"CurseForge (vão para overrides):"
            )
            for result in missing:
                reporter.info(f"     [red]--[/red] {result.mod.file_name}")
                if result.modrinth and result.modrinth.title:
                    reporter.info(
                        f"        [dim]no Modrinth:[/dim] "
                        f"[cyan]{result.modrinth.title}[/cyan]"
                    )

        if failed:
            reporter.info("")
            reporter.info(f"[red]--[/red] {len(failed)} arquivo(s) com erro:")
            for result in failed:
                reporter.info(
                    f"     [red]--[/red] {result.mod.file_name}: {result.error}"
                )

        reporter.info("")
        reporter.info(
            "[bold]Resumo:[/bold] "
            f"[green]{len(ok)}[/green] no manifest · "
            f"[yellow]{len(version_unavailable)}[/yellow] sem a versão · "
            f"[red]{len(missing)}[/red] sem projeto"
            + (f" · [red]{len(failed)}[/red] com erro" if failed else "")
        )
        reporter.info("")

    # ------------------------------------------------------------- resoluções
    @staticmethod
    def _apply_resolutions(
        outcome: ConversionOutcome, resolutions: dict[str, Resolution]
    ) -> None:
        for result in outcome.results:
            resolution = resolutions.get(result.mod.file_name)

            if resolution:
                result.project_id = resolution.project_id
                result.file_id = resolution.file_id
                result.project_name = resolution.project_name
                result.project_slug = resolution.project_slug
                result.strategy = MatchStrategy.MANUAL
                result.error = None

            elif result.strategy is MatchStrategy.MANUAL:
                # o usuário desfez a escolha: volta para overrides
                result.project_id = None
                result.file_id = None
                result.project_name = None
                result.project_slug = None
                result.strategy = MatchStrategy.UNMATCHED

    # ------------------------------------------------------------- matching
    def _match_files(
        self,
        pack: Modpack,
        modrinth_map: dict,
        curseforge: CurseForgeClient,
        modrinth: ModrinthClient,
    ) -> list[MatchResult]:
        alvos = pack.convertible
        order = {mod.file_path: index for index, mod in enumerate(alvos)}
        results: list[MatchResult] = []
        reporter = self.reporter

        reporter.stage("Procurando no CurseForge", total=len(alvos))

        matcher = CurseForgeMatcher(
            client=curseforge,
            minecraft_version=pack.minecraft.version,
            loader=pack.minecraft.loader,
            modrinth=modrinth,
        )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(
                    matcher.match,
                    mod,
                    modrinth_map.get(mod.file_path),
                    # não achar um arquivo que já está em overrides/ é o normal
                    # dele: não vira conflito, então o diagnóstico só gastaria
                    # requisição
                    not mod.from_overrides,
                ): mod
                for mod in alvos
            }

            for future in as_completed(futures):
                if self.cancelled:
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise ConversionCancelled("Conversão cancelada pelo usuário")

                mod = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001 - falha isolada por mod
                    result = MatchResult(mod=mod, error=str(exc))

                if mod.from_overrides and not result.matched:
                    # segue exatamente onde estava, sem aparecer em lugar nenhum
                    reporter.advance()
                    continue

                results.append(result)
                # sai daqui (thread única) e não do matcher: assim a ordem das
                # linhas acompanha a barra de progresso
                reporter.log(self._result_line(result))
                reporter.advance()

        results.sort(key=lambda r: order.get(r.mod.file_path, 0))
        return results

    # ----------------------------------------------------------- empacotamento
    def _assemble(
        self,
        pack: Modpack,
        results: list[MatchResult],
        parser: MrpackParser,
        reuse: bool = False,
        base_name: str | None = None,
    ) -> tuple[Path, Path | None, int]:
        reporter = self.reporter
        base_name = base_name or self._base_name(pack)

        work_dir = self.output_dir / WORK_DIRNAME / base_name

        # `reuse` mantém os arquivos já baixados (reempacotar fica quase instantâneo)
        if work_dir.exists() and not reuse:
            shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)

        overrides_dir = work_dir / "overrides"
        overrides_dir.mkdir(parents=True, exist_ok=True)

        self._check_cancel()

        reporter.stage("Copiando a pasta overrides/ do pack original")
        extracted = parser.extract_overrides(overrides_dir)
        if extracted:
            reporter.info(f"[dim]{extracted} arquivos copiados de overrides/[/dim]")

        self._drop_resolved_overrides(results, overrides_dir)

        # o que veio do índice e não entrou no manifest é baixado; o que já
        # estava em overrides/ (e portanto não tem URL) continua onde está
        pending = [
            result.mod
            for result in results
            if not result.matched and not result.mod.from_overrides
        ]
        # mais o que nem chega a ser procurado no CurseForge (config, datapack…)
        pending += pack.plain_extras

        failures = self._download_all(
            [(item, overrides_dir / item.override_path) for item in pending],
            results,
            sum(1 for item in pending if item.is_mod),
            sum(1 for item in pending if not item.is_mod),
        )

        self._check_cancel()

        builder = CurseForgeManifestBuilder()
        manifest = builder.build(pack, results)

        (work_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        (work_dir / "modlist.html").write_text(
            builder.build_modlist(results), encoding="utf-8"
        )

        destination = self.output_dir / f"{base_name}.zip"
        reporter.stage("Compactando modpack")
        build_zip(work_dir, destination)

        if not self.keep_work:
            shutil.rmtree(work_dir, ignore_errors=True)
            work_dir = None

        size_mb = destination.stat().st_size / (1024 * 1024)
        reporter.info(
            f"[green]++[/green] modpack gerado: {destination.name} ({size_mb:.1f} MB)"
        )

        return destination, work_dir, failures

    @staticmethod
    def _drop_resolved_overrides(
        results: list[MatchResult], overrides_dir: Path
    ) -> None:
        """Tira de `overrides/` tudo que agora entra no manifest.

        Vale também para o que já vinha dentro do `overrides/` do mrpack: uma vez
        no manifest, deixar o arquivo aqui instalaria o mesmo mod duas vezes.
        """

        for result in results:
            if not result.matched:
                continue

            arquivo = overrides_dir / result.mod.override_path
            if arquivo.exists():
                arquivo.unlink()

    # ------------------------------------------------------------- downloads
    def _download_all(
        self,
        pending: list[tuple[PackFile, Path]],
        results: list[MatchResult],
        mods_count: int = 0,
        extras_count: int = 0,
    ) -> int:
        if not pending:
            return 0

        by_path = {result.mod.file_path: result for result in results}
        failures = 0
        reporter = self.reporter

        parts = []
        if mods_count:
            parts.append(f"{mods_count} mod{'s' if mods_count > 1 else ''}")
        if extras_count:
            parts.append(
                f"{extras_count} arquivo{'s' if extras_count > 1 else ''} extra"
                f"{'s' if extras_count > 1 else ''}"
            )
        label = " e ".join(parts) if parts else f"{len(pending)} arquivos"

        reporter.stage(f"Baixando {label} para overrides", total=len(pending))

        with Downloader(cancelled=lambda: self.cancelled) as downloader:

            def run(item: tuple[PackFile, Path]) -> tuple[PackFile, str | None]:
                pack_file, destination = item

                if not pack_file.download_url:
                    return pack_file, "sem URL de download no mrpack"

                try:
                    downloader.download(
                        pack_file.download_url, destination, pack_file.sha1
                    )
                    return pack_file, None
                except ConversionCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001
                    return pack_file, str(exc)

            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = [pool.submit(run, item) for item in pending]

                for future in as_completed(futures):
                    if self.cancelled:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise ConversionCancelled("Conversão cancelada pelo usuário")

                    try:
                        pack_file, error = future.result()
                    except ConversionCancelled:
                        pool.shutdown(wait=False, cancel_futures=True)
                        raise

                    if error:
                        failures += 1
                        reporter.log(f"[red]--[/red] {pack_file.file_name}: {error}")
                        result = by_path.get(pack_file.file_path)
                        if result:
                            result.error = error

                    reporter.advance()

        return failures

    # ----------------------------------------------------------------- utils
    @staticmethod
    def _base_name(pack: Modpack) -> str:
        # o marcador entra depois do `safe_name`, que trocaria os colchetes por _
        base = safe_name(f"{pack.name} {pack.version}").replace(" ", "-")
        return f"{base}-[convertido]"
