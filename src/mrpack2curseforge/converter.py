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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from mrpack2curseforge.builders.curseforge_manifest import CurseForgeManifestBuilder
from mrpack2curseforge.builders.package import build_zip, safe_name
from mrpack2curseforge.config import Config
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

    def plan(
        self, resolutions: dict[str, "Resolution"] | None = None
    ) -> dict[str, int]:
        """Resumo do que o `finish()` vai fazer (mostrado antes de confirmar).

        `resolutions` são as escolhas ainda **não aplicadas**: sem elas o plano
        mostraria downloads que já não vão acontecer.
        """

        resolutions = resolutions or {}
        manifest = manual = downloads = 0

        for result in self.results:
            resolved = result.mod.file_name in resolutions

            if resolved:
                manifest += 1
                manual += 1
            elif result.matched and result.strategy is MatchStrategy.MANUAL:
                # escolha manual desfeita: volta para overrides
                downloads += 1
            elif result.matched:
                manifest += 1
            else:
                downloads += 1

        return {
            "manifest": manifest,
            "manual": manual,
            "downloads": downloads,
            "extra_files": len(self.pack.extra_files),
            "override_files": len(self.pack.override_paths),
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

            reporter.stage("Consultando o Modrinth para descobrir os nomes dos mods")
            modrinth_map = modrinth.resolve_projects(pack.mods)
            reporter.info(
                f"[dim]{len(modrinth_map)}/{len(pack.mods)} mods identificados "
                f"no Modrinth[/dim]"
            )

            results = self._match_mods(pack, modrinth_map, curseforge, modrinth)

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

        def sort_key(result: MatchResult) -> str:
            return result.mod.file_name.lower()

        ok = sorted((r for r in results if r.matched), key=sort_key)
        failed = sorted((r for r in results if r.error), key=sort_key)

        version_unavailable = sorted(
            (
                r
                for r in results
                if not r.matched
                and not r.error
                and r.diagnosis
                and r.diagnosis.reason is MissingReason.VERSION_UNAVAILABLE
            ),
            key=sort_key,
        )
        missing = sorted(
            (
                r
                for r in results
                if not r.matched
                and not r.error
                and (
                    not r.diagnosis
                    or r.diagnosis.reason is not MissingReason.VERSION_UNAVAILABLE
                )
            ),
            key=sort_key,
        )

        reporter.info("")
        reporter.info("[bold]Resultado da análise[/bold]")
        reporter.info("")

        if ok:
            reporter.info(
                f"[green]++[/green] {len(ok)} mod(s) encontrados no CurseForge "
                f"(não listados)"
            )

        if version_unavailable:
            reporter.info("")
            reporter.info(
                f"[yellow]--[/yellow] {len(version_unavailable)} mod(s) sem essa "
                f"versão no CurseForge (vão para overrides):"
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
                f"[red]--[/red] {len(missing)} mod(s) sem projeto no CurseForge "
                f"(vão para overrides):"
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
            reporter.info(f"[red]--[/red] {len(failed)} mod(s) com erro:")
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
    def _match_mods(
        self,
        pack: Modpack,
        modrinth_map: dict,
        curseforge: CurseForgeClient,
        modrinth: ModrinthClient,
    ) -> list[MatchResult]:
        order = {mod.file_path: index for index, mod in enumerate(pack.mods)}
        results: list[MatchResult] = []
        reporter = self.reporter

        reporter.stage("Procurando no CurseForge", total=len(pack.mods))

        matcher = CurseForgeMatcher(
            client=curseforge,
            minecraft_version=pack.minecraft.version,
            loader=pack.minecraft.loader,
            modrinth=modrinth,
        )

        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            futures = {
                pool.submit(matcher.match, mod, modrinth_map.get(mod.file_path)): mod
                for mod in pack.mods
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

        work_dir = self.output_dir / ".work" / base_name

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

        self._drop_resolved_overrides(pack, results, overrides_dir)

        mods_pending = [
            (result.mod, overrides_dir / "mods" / result.mod.file_name)
            for result in results
            if not result.matched
        ]
        extras_pending = [
            (extra, overrides_dir / extra.file_path) for extra in pack.extra_files
        ]

        failures = self._download_all(
            mods_pending + extras_pending,
            results,
            len(mods_pending),
            len(extras_pending),
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
        pack: Modpack, results: list[MatchResult], overrides_dir: Path
    ) -> None:
        """Remove de `overrides/mods` os jars de mods que agora entram no manifest.

        Arquivos que já vinham dentro do `overrides/` do mrpack original são
        preservados — eles não são responsabilidade do matcher.
        """

        from_mrpack = {path.as_posix() for path in pack.override_paths}

        for result in results:
            if not result.matched:
                continue

            relative = f"mods/{result.mod.file_name}"
            if relative in from_mrpack:
                continue

            jar = overrides_dir / "mods" / result.mod.file_name
            if jar.exists():
                jar.unlink()

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
