"""Jobs de conversão executados em background, para a interface web.

Ciclo de vida de um job (só existe **um** ativo por vez):

    running            procurando os mods no CurseForge
      │
      ├─ sem conflitos ─────────────► finishing ─► done
      │
      └─ com conflitos ─► awaiting_conflicts
                              │ (usuário resolve e manda aplicar)
                              └────► finishing ─► done

    cancelled / error são estados finais; `close()` libera a vaga.
"""

import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mrpack2curseforge.config import Config
from mrpack2curseforge.converter import ConversionOutcome, Converter, Resolution
from mrpack2curseforge.domain import MatchStrategy, MissingReason
from mrpack2curseforge.exceptions import ConversionCancelled
from mrpack2curseforge.updater import UpdateDecisions, Updater, default_excluded

# só as tags que o projeto usa: assim um nome de arquivo com colchetes
# (ex.: "mod[1.20].jar") não é mutilado no log
MARKUP = re.compile(r"\[/?(?:red|green|yellow|cyan|blue|dim|bold)\]", re.IGNORECASE)
MAX_LOGS = 2000

ACTIVE_STATUSES = (
    "queued",
    "running",
    "awaiting_conflicts",
    "awaiting_review",
    "finishing",
)
BUSY_STATUSES = ("queued", "running", "finishing")


LEVEL_BY_TAG = {
    "red": "error",
    "yellow": "warn",
    "green": "ok",
    "cyan": "cyan",
    # `dim` é o rótulo secundário ("projeto no CurseForge:"), o texto sem
    # marcação é o conteúdo em si — precisam ser distinguíveis
    "dim": "dim",
}


def _segments(message: str) -> list[dict[str, str]]:
    """Quebra a marcação do `rich` em trechos coloridos.

    É o que permite a linha de resumo ter cada número na sua cor
    (`45` verde, `4` amarelo, `0` vermelho) em vez de uma cor só para tudo.
    """

    parts: list[dict[str, str]] = []
    stack: list[str] = []
    position = 0

    def level() -> str:
        for tag in reversed(stack):
            if tag in LEVEL_BY_TAG:
                return LEVEL_BY_TAG[tag]
        return "info"

    for match in MARKUP.finditer(message):
        chunk = message[position : match.start()]
        if chunk:
            parts.append({"text": chunk, "level": level()})

        tag = match.group(0).strip("[]/").lower()

        if match.group(0).startswith("[/"):
            if tag in stack:
                stack.remove(tag)
        else:
            stack.append(tag)

        position = match.end()

    tail = message[position:]
    if tail:
        parts.append({"text": tail, "level": level()})

    # espaços à direita não importam; a indentação à esquerda sim
    while parts and not parts[-1]["text"].rstrip():
        parts.pop()
    if parts:
        parts[-1]["text"] = parts[-1]["text"].rstrip()

    return parts


def _plain(message: str) -> tuple[str, str]:
    """Texto sem marcação + cor de base da linha.

    Regra: linha que começa com `[bold]` é título/resumo e fica neutra (ela mistura
    verde, amarelo e vermelho e não é um erro); nas demais vale a primeira cor
    encontrada, mesmo que venha depois da indentação.
    """

    parts = _segments(message)
    text = "".join(part["text"] for part in parts)

    if message.lstrip().lower().startswith("[bold]"):
        return text, "info"

    level = next((part["level"] for part in parts if part["level"] != "info"), "info")

    return text, level


def _file_payload(result, decisions) -> dict[str, Any]:
    """Um arquivo do pack com o estado da decisão do usuário."""

    caminho = result.mod.file_path
    escolha = decisions.versions.get(caminho)
    # o projeto detectado na análise; a escolha manual pode ter trocado
    original = result.auto_modrinth or result.modrinth

    return {
        "file_path": caminho,
        "file_name": result.mod.file_name,
        "is_mod": result.mod.is_mod,
        "status": result.status.value,
        # a interface junta os dois grupos numa lista só; o card precisa saber
        "has_version": result.has_version,
        "project_id": original.project_id if original else None,
        "title": original.title if original else None,
        "icon": original.icon_url if original else None,
        "url": original.url if original else None,
        "from_version": result.from_version,
        "to_version": result.to_version,
        "version_type": result.version_type,
        "new_file_name": result.new_file.file_name if result.new_file else None,
        # decisões
        "manual": escolha is not None,
        "chosen": (
            {
                "version_id": escolha.version_id,
                "version_number": escolha.version_number,
                "file_name": escolha.file_name,
                "project_id": escolha.project_id,
                "project_title": escolha.project_title,
            }
            if escolha
            else None
        ),
        "skipped": caminho in decisions.keep,
        "excluded": _decided_exclusion(result, decisions),
    }


def _decided_exclusion(result, decisions) -> bool:
    """Decisão do usuário quando existe; senão, o padrão do atualizador."""

    caminho = result.mod.file_path

    if caminho in decisions.exclude:
        return True
    if caminho in decisions.include:
        return False

    return default_excluded(result)


def _update_payload(outcome, decisions) -> dict[str, Any]:
    """Resultado de uma atualização, do jeito que a interface consome."""

    return {
        "packaged": outcome.packaged,
        # com versão para o alvo (dá para trocar a versão de qualquer um)
        "with_version": [
            _file_payload(result, decisions) for result in outcome.with_version
        ],
        # sem versão para o alvo: entram como estão ou ficam de fora
        "without_version": [
            _file_payload(result, decisions) for result in outcome.without_version
        ],
        "from_minecraft": outcome.pack.minecraft.version,
        "to_minecraft": outcome.minecraft_version,
        "loader": f"{outcome.target_loader}-{outcome.loader_version}",
        "from_loader": outcome.pack.minecraft.loader,
        "to_loader": outcome.target_loader,
        "loader_changed": outcome.loader_changed,
        "downgrade": outcome.downgrade,
        "summary": outcome.summary,
    }


@dataclass
class Job:
    id: str
    source: Path
    status: str = "queued"
    stage: str = ""
    done: int = 0
    total: int = 0
    error: str | None = None
    outcome: ConversionOutcome | None = None
    logs: list[dict[str, str]] = field(default_factory=list)
    resolutions: dict[str, Resolution] = field(default_factory=dict)
    # atualizador: tudo o que o usuário decidiu na revisão
    decisions: UpdateDecisions = field(default_factory=UpdateDecisions)
    # escolhas salvas ainda não aplicadas ao .zip
    dirty: bool = False
    kind: str = "conversion"  # "conversion" | "rebuild" | "update"
    record: dict | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # ------------------------------------------------------------- progresso
    def set_stage(self, name: str, total: int | None = None) -> None:
        with self.lock:
            self.stage = name
            self.total = total or 0
            self.done = 0

    def advance(self, amount: int = 1) -> None:
        with self.lock:
            self.done += amount

    def add_log(self, message: str) -> None:
        # linhas vazias entram também: são os espaçadores do resumo da análise
        text, level = _plain(message)
        entry: dict[str, Any] = {"text": text, "level": level}

        parts = _segments(message)
        if len({part["level"] for part in parts}) > 1:
            # linha com mais de uma cor (o resumo): o front pinta trecho a trecho
            entry["parts"] = parts

        with self.lock:
            self.logs.append(entry)
            if len(self.logs) > MAX_LOGS:
                del self.logs[: len(self.logs) - MAX_LOGS]

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def busy(self) -> bool:
        return self.status in BUSY_STATUSES

    # ------------------------------------------------------- serialização
    def snapshot(self, log_offset: int = 0) -> dict[str, Any]:
        with self.lock:
            data: dict[str, Any] = {
                "id": self.id,
                # é o que diz à interface qual painel usar (conversão ou atualização)
                "kind": self.kind,
                "source": self.source.name,
                "status": self.status,
                "stage": self.stage,
                "done": self.done,
                "total": self.total,
                "error": self.error,
                "logs": self.logs[log_offset:],
                "log_count": len(self.logs),
                "resolved": len(self.resolutions),
            }

        outcome = self.outcome

        if outcome is not None and self.kind == "update":
            data["update"] = _update_payload(outcome, self.decisions)
            data["dirty"] = self.dirty

            if outcome.output.exists():
                data["output"] = {
                    "name": outcome.output.name,
                    "size_mb": round(outcome.output.stat().st_size / (1024 * 1024), 1),
                }

            return data

        if outcome is not None:
            report = outcome.report
            conflicts = self.conflicts()

            data["packaged"] = outcome.packaged
            data["plan"] = outcome.plan(self.resolutions)
            data["conflicts"] = len(conflicts)
            data["unresolved"] = sum(1 for c in conflicts if not c["resolution"])
            data["dirty"] = self.dirty
            data["report"] = {
                "minecraft_version": report.minecraft_version,
                "loader": report.loader,
                "total_mods": report.total_mods,
                "matched": report.matched,
                "overrides": report.overrides,
                "version_unavailable": report.version_unavailable,
                "not_on_curseforge": report.not_on_curseforge,
                "failed": report.failed,
                "extra_files": report.extra_files,
                "override_files": report.override_files,
                "success_rate": round(report.success_rate, 1),
            }
            # a lista mod a mod NÃO vai aqui: o polling é a cada 600 ms e num pack
            # de 400 mods isso seriam ~200 KB por requisição. Ela está no registro
            # (`/api/records/{id}`), que a interface busca uma vez só.

            if outcome.packaged:
                # o registro guarda o resumo completo, exibido no painel de detalhes
                data["record_id"] = outcome.record_path.stem

                if outcome.output.exists():
                    data["output"] = {
                        "name": outcome.output.name,
                        "size_mb": round(
                            outcome.output.stat().st_size / (1024 * 1024), 1
                        ),
                    }

        return data

    # -------------------------------------------------------------- conflitos
    def conflicts(self) -> list[dict[str, Any]]:
        """Mods que ainda precisam de decisão.

        Depois que o modpack é empacotado não há mais o que decidir: o que foi
        escolhido já está no manifest e o resto já foi para `overrides`. Aí a aba
        de conflitos fica vazia (o histórico completo vive no registro).
        """

        if self.kind == "update" or self.outcome is None or self.outcome.packaged:
            return []

        items: list[dict[str, Any]] = []

        for result in self.outcome.results:
            resolution = self.resolutions.get(result.mod.file_name)

            # mods achados automaticamente não são conflito; os resolvidos à mão
            # continuam na lista (mostrando o que foi escolhido)
            if result.matched and result.strategy is not MatchStrategy.MANUAL:
                continue

            diagnosis = result.diagnosis
            reason = (
                diagnosis.reason.value if diagnosis else MissingReason.UNKNOWN.value
            )

            items.append(
                {
                    "file_name": result.mod.file_name,
                    "reason": reason,
                    "similarity": round(diagnosis.similarity, 3) if diagnosis else None,
                    # o mod original, para comparar com os candidatos do CurseForge
                    "modrinth": (
                        {
                            "title": result.modrinth.title,
                            "slug": result.modrinth.slug,
                            "icon": result.modrinth.icon_url,
                            "url": result.modrinth.url,
                            "version": result.modrinth.version_number,
                        }
                        if result.modrinth
                        else None
                    ),
                    "modrinth_title": (
                        result.modrinth.title if result.modrinth else None
                    ),
                    "queries_tried": result.queries_tried,
                    "suggestion": {
                        "project_id": diagnosis.project_id,
                        "project_name": diagnosis.project_name,
                        "project_slug": diagnosis.project_slug,
                        "url": diagnosis.curseforge_url,
                        "closest_file_name": diagnosis.closest_file_name,
                    }
                    if diagnosis and diagnosis.project_id
                    else None,
                    "resolution": {
                        "project_id": resolution.project_id,
                        "file_id": resolution.file_id,
                        "project_name": resolution.project_name,
                        "project_slug": resolution.project_slug,
                        "file_name": resolution.file_name,
                    }
                    if resolution
                    else None,
                }
            )

        return items


class JobManager:
    """Registro em memória dos jobs. Só um job pode estar aberto por vez."""

    def __init__(self, output_dir: Path | None = None):
        self.output_dir = Path(output_dir or Config.OUTPUT_DIR)
        self.jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ api
    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self.jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

    def current(self, kind: str | None = None) -> Job | None:
        """O trabalho aberto **daquela ferramenta**.

        Conversão e atualização são independentes: uma rodando não impede a outra.
        """

        with self._lock:
            jobs = sorted(self.jobs.values(), key=lambda j: j.created_at, reverse=True)

        if kind == "update":
            jobs = [job for job in jobs if job.kind == "update"]
        elif kind is not None:
            jobs = [job for job in jobs if job.kind != "update"]

        return jobs[0] if jobs else None

    def close(self, job: Job) -> None:
        """Fecha a conversão, apaga os arquivos regeneráveis e libera a vaga.

        Fica só o registro em `conversions/`: o `.zip` e a pasta de trabalho são
        reconstruíveis a partir dele (num pack grande, isso são centenas de MB
        que não precisam ficar ocupando disco).
        """

        job.cancel_event.set()

        # o .mrpack de uma atualização é o próprio produto (e é pequeno): fica
        if job.outcome is not None and job.kind != "update":
            work_dir = job.outcome.work_dir
            if work_dir and work_dir.exists():
                shutil.rmtree(work_dir, ignore_errors=True)

            # só apaga o .zip se ESTE job o gerou: `analyze()` já calcula o caminho
            # de saída, e um job cancelado na análise não pode apagar o arquivo de
            # uma conversão anterior (ou do CLI) que por acaso tenha o mesmo nome
            if job.outcome.packaged and job.outcome.output.exists():
                try:
                    job.outcome.output.unlink()
                except OSError:
                    # no Windows o arquivo fica travado enquanto está sendo
                    # baixado; fechar não pode falhar por causa disso — o zip é
                    # regenerável e a próxima geração o substitui
                    pass

        with self._lock:
            self.jobs.pop(job.id, None)

    # -------------------------------------------------------------- disparo
    def start_conversion(self, source: Path, workers: int | None = None) -> Job:
        return self._spawn(self._new_job(source), self._run_analysis, workers)

    def start_finish(self, job: Job) -> Job:
        return self._spawn(job, self._finish)

    def start_update(
        self,
        source: Path,
        minecraft_version: str,
        loader_version: str | None = None,
        workers: int | None = None,
        loader: str | None = None,
    ) -> Job:
        """Atualiza os mods de um `.mrpack` para outra versão do Minecraft."""

        return self._spawn(
            self._new_job(source, kind="update"),
            self._run_update,
            minecraft_version,
            loader_version,
            workers,
            loader,
        )

    def start_update_reapply(self, job: Job) -> Job:
        """Regera o `.mrpack` com as versões escolhidas à mão."""

        return self._spawn(job, self._run_update_reapply)

    def start_rebuild(self, record: dict, source: Path) -> Job:
        """Regera o `.zip` de uma conversão salva, sem consultar o CurseForge."""

        job = self._new_job(source, kind="rebuild", record=record)
        return self._spawn(job, self._run_rebuild)

    def _new_job(self, source: Path, **campos) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], source=Path(source), **campos)

        with self._lock:
            self.jobs[job.id] = job

        return job

    def _spawn(self, job: Job, funcao, *args) -> Job:
        """Roda `funcao(job, *args)` numa thread, já com o tratamento de estado.

        Cancelamento e erro viram estado do job em um lugar só — cada runner
        novo herda isso de graça em vez de repetir o try/except.
        """

        def alvo() -> None:
            try:
                funcao(job, *args)
            except ConversionCancelled:
                self._mark_cancelled(job)
            except Exception as exc:  # noqa: BLE001 - erro vira estado do job
                self._mark_error(job, exc)

        threading.Thread(target=alvo, daemon=True).start()
        return job

    def cancel(self, job: Job) -> None:
        job.cancel_event.set()

        if job.busy:
            # a thread em execução vai perceber o evento e encerrar
            job.add_log("[yellow]--[/yellow] cancelamento solicitado…")
            return

        # pausado (ou já parado): não há ninguém para observar o evento
        if job.active:
            self._mark_cancelled(job)

    # ------------------------------------------------------------- execução
    def _converter(self, job: Job, workers: int | None = None) -> Converter:
        return Converter(
            output_dir=self.output_dir,
            workers=workers,
            # a interface precisa da pasta de trabalho para reempacotar rápido
            keep_work=True,
            reporter=JobReporter(job),
            cancel_event=job.cancel_event,
        )

    def _updater(self, job: Job, workers: int | None = None) -> Updater:
        return Updater(
            output_dir=self.output_dir,
            workers=workers,
            reporter=JobReporter(job),
            cancel_event=job.cancel_event,
        )

    def _run_analysis(self, job: Job, workers: int | None) -> None:
        job.status = "running"
        job.add_log(f"[green]++[/green] iniciando conversão de {job.source.name}")

        job.outcome = self._converter(job, workers).analyze(job.source)
        conflicts = job.conflicts()

        if not conflicts:
            job.add_log("nenhum conflito: seguindo direto para os downloads")
            self._finish(job)
            return

        job.status = "awaiting_conflicts"
        job.set_stage(f"{len(conflicts)} conflito(s) aguardando decisão")
        job.add_log(
            f"[yellow]--[/yellow] {len(conflicts)} mod(s) não entraram no "
            f"manifest. Resolva os conflitos (ou siga assim) e aplique as "
            f"mudanças para continuar."
        )

    def _run_update(
        self,
        job: Job,
        minecraft_version: str,
        loader_version: str | None,
        workers: int | None,
        loader: str | None = None,
    ) -> None:
        job.status = "running"
        job.add_log(
            f"[green]++[/green] analisando {job.source.name} para o "
            f"Minecraft {minecraft_version}"
            + (f" com {loader}" if loader else "")
        )

        job.outcome = self._updater(job, workers).analyze(
            job.source, minecraft_version, loader_version, loader
        )

        # a análise não escreve nada: quem decide o que aplicar é o usuário
        sem_versao = len(job.outcome.without_version)

        job.status = "awaiting_review"
        job.set_stage("Revise as mudanças e aplique")
        job.add_log(
            "[yellow]--[/yellow] nada foi gravado ainda: revise as três seções "
            f"({sem_versao} arquivo(s) sem versão para o alvo) e clique em "
            "Aplicar para gerar o .mrpack"
        )

    def _run_update_reapply(self, job: Job) -> None:
        if job.outcome is None:
            return

        job.status = "finishing"
        job.set_stage("Gerando o .mrpack com as suas decisões")

        job.outcome = self._updater(job).apply(job.outcome, job.decisions)
        resumo = job.outcome.summary

        self._mark_done(
            job,
            f".mrpack gerado · {resumo['updated']} trocaram de versão, "
            f"{resumo['manual']} escolhidos por você, "
            f"{resumo['excluded']} ficaram de fora",
        )

    def _run_rebuild(self, job: Job) -> None:
        job.status = "finishing"
        job.set_stage("Regerando modpack")
        job.add_log("[green]++[/green] regerando a partir do registro salvo")

        job.outcome = self._converter(job).rebuild(job.record, job.source)
        job.resolutions = dict(job.outcome.resolutions)

        self._mark_done(job, "modpack regerado")

    def _finish(self, job: Job) -> None:
        if job.outcome is None:
            return

        job.status = "finishing"
        job.error = None
        job.set_stage("Preparando arquivos")

        job.outcome = self._converter(job).finish(job.outcome, job.resolutions)

        self._mark_done(job, "conversão concluída")

    # --------------------------------------------------------------- estados
    @staticmethod
    def _mark_done(job: Job, mensagem: str) -> None:
        job.dirty = False
        job.status = "done"
        job.set_stage("Concluído")
        job.finished_at = time.time()
        job.add_log(f"[green]++[/green] {mensagem}")

    @staticmethod
    def _mark_cancelled(job: Job) -> None:
        job.status = "cancelled"
        job.set_stage("Cancelada")
        job.finished_at = time.time()
        job.add_log("[yellow]--[/yellow] conversão cancelada")

    @staticmethod
    def _mark_error(job: Job, exc: Exception) -> None:
        job.status = "error"
        job.error = str(exc)
        job.set_stage("Erro")
        job.finished_at = time.time()
        job.add_log(f"[red]--[/red] erro: {exc}")


class JobReporter:
    """`Reporter` que escreve no estado do job em vez do terminal."""

    def __init__(self, job: Job):
        self.job = job

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def stage(self, name: str, total: int | None = None) -> None:
        self.job.set_stage(name, total)
        self.job.add_log(f"{name}{f' ({total} itens)' if total else ''}")

    def advance(self, amount: int = 1) -> None:
        self.job.advance(amount)

    def log(self, message: str) -> None:
        self.job.add_log(message)

    def info(self, message: str) -> None:
        self.job.add_log(message)
