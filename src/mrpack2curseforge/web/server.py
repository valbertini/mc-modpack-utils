"""Servidor web local (FastAPI).

Tudo é servido de `127.0.0.1`: nenhum CDN, nenhuma fonte externa, nenhuma
telemetria. As únicas chamadas externas são as APIs do Modrinth e do CurseForge
feitas pelo próprio conversor.
"""

import json
import shutil
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders

from mrpack2curseforge.builders.mrpack import LOADER_DEPENDENCY
from mrpack2curseforge.builders.package import safe_name
from mrpack2curseforge.config import Config
from mrpack2curseforge.converter import Resolution
from mrpack2curseforge.exceptions import ApiError
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge import settings
from mrpack2curseforge.records import delete_record, list_records, load_record
from mrpack2curseforge.services.cache import SimpleCache, cache_stats, clear_cache
from mrpack2curseforge.services.curseforge import CurseForgeClient
from mrpack2curseforge.services.loaders import loader_versions
from mrpack2curseforge.services.matcher import rank_projects
from mrpack2curseforge.services.modrinth import ModrinthClient
from mrpack2curseforge.updater import ManualPick, UpdateDecisions
from mrpack2curseforge.web.jobs import Job, JobManager

STATIC_DIR = Path(__file__).resolve().parent / "static"

# A aplicação é servida inteiramente da máquina local: HTML, CSS e JS saem daqui,
# nunca de um CDN (a página abre mesmo sem internet). Imagens externas são
# permitidas porque os ícones dos projetos do CurseForge ajudam muito na hora de
# escolher entre mods de nome parecido — se estiverem offline, degradam sozinhos.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware:
    """Adiciona os cabeçalhos de segurança sem tocar no corpo da resposta.

    Isto é um middleware ASGI puro de propósito. Com `BaseHTTPMiddleware`
    (o decorator `@app.middleware("http")`), o corpo é reencaminhado pedaço a
    pedaço pelo middleware; se o `.zip` mudar de tamanho enquanto está sendo
    baixado — regenerar o modpack durante um download, por exemplo — o h11
    aborta com "Too much data for declared Content-Length". Aqui só o cabeçalho
    é alterado; o arquivo é transmitido direto pelo `FileResponse`.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"

            await send(message)

        await self.app(scope, receive, send_with_headers)


# --------------------------------------------------------------------------- #
# Payloads
# --------------------------------------------------------------------------- #
def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
    """Campos de um projeto do CurseForge que a interface usa."""

    logo = project.get("logo") or {}

    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "slug": project.get("slug"),
        "summary": project.get("summary"),
        "downloads": project.get("downloadCount"),
        "url": (project.get("links") or {}).get("websiteUrl"),
        "logo": logo.get("thumbnailUrl") or logo.get("url"),
        "authors": [
            author.get("name")
            for author in (project.get("authors") or [])
            if author.get("name")
        ],
    }


_META_CACHE: dict[str, dict[str, Any]] = {}


def _pack_meta(path: Path, mtime: float, size: int) -> dict[str, Any]:
    """Minecraft, loader e nº de mods de um `.mrpack`, lidos do índice.

    O `/api/state` é consultado a cada 600 ms; a chave inclui mtime e tamanho,
    então o zip só é aberto quando o arquivo muda de verdade.
    """

    chave = f"{path}|{mtime}|{size}"
    cached = _META_CACHE.get(chave)
    if cached is not None:
        return cached

    try:
        parser = MrpackParser(path)
        parser.validate()
        pack = parser.parse()
        meta = {
            "minecraft": pack.minecraft.version,
            "loader": pack.minecraft.loader,
            "loader_version": pack.minecraft.loader_version,
            "mods": len(pack.mods),
        }
    except Exception:  # noqa: BLE001 - um pack ilegível não derruba a lista
        meta = {
            "minecraft": None,
            "loader": None,
            "loader_version": None,
            "mods": None,
        }

    # o cache é por arquivo: guardar as versões antigas não serve para nada
    for antigo in [k for k in _META_CACHE if k.startswith(f"{path}|")]:
        _META_CACHE.pop(antigo, None)

    _META_CACHE[chave] = meta
    return meta


def _last_used(output_dir: Path) -> dict[str, float]:
    """Último trabalho feito com cada `.mrpack` de entrada, por nome.

    Junta os dois lados: registros de conversão (`conversions/*.json`) e
    relatórios de atualização (`*-update.json`). É o que faz a lista mostrar
    primeiro o que você mexeu por último.
    """

    quando: dict[str, float] = {}

    def marcar(origem: str | None, momento: float | None) -> None:
        if not origem or not momento:
            return
        if momento > quando.get(origem, 0):
            quando[origem] = momento

    for registro in list_records(output_dir):
        marcar(registro.get("source"), registro.get("updated_at"))

    for relatorio in output_dir.glob("*-update.json"):
        try:
            dados = json.loads(relatorio.read_text(encoding="utf-8"))
            marcar(dados.get("source"), relatorio.stat().st_mtime)
        except (json.JSONDecodeError, OSError):
            continue

    return quando


class SettingsRequest(BaseModel):
    """Só as chaves que a tela conhece são gravadas (o resto é ignorado)."""

    values: dict[str, str] = {}


class ConvertRequest(BaseModel):
    file: str
    workers: int | None = None


class ResolveRequest(BaseModel):
    file_name: str
    project_id: int
    file_id: int
    project_name: str | None = None
    project_slug: str | None = None
    curseforge_file_name: str | None = None


class UpdateRequest(BaseModel):
    file: str
    minecraft: str
    loader_version: str | None = None
    # trocar de modloader (fabric -> neoforge…); vazio mantém o do pack
    loader: str | None = None
    workers: int | None = None


class UpdateChoice(BaseModel):
    file_path: str
    version_id: str
    # só para a interface mostrar a escolha sem reconsultar a API
    version_number: str | None = None
    file_name: str | None = None
    project_id: str | None = None
    project_title: str | None = None


class UpdateResolutionsRequest(BaseModel):
    """Decisões da revisão da atualização."""

    # versões escolhidas à mão
    choices: list[UpdateChoice] = []
    # manter a versão atual, mesmo havendo uma nova
    keep: list[str] = []
    # não levar o arquivo para o pack novo
    exclude: list[str] = []
    # levar mesmo sem versão para o alvo
    include: list[str] = []


class ResolutionsRequest(BaseModel):
    """Salva de uma vez todas as escolhas feitas na aba de conflitos."""

    resolutions: list[ResolveRequest] = []


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #
def create_app(
    input_dir: Path | None = None, output_dir: Path | None = None
) -> FastAPI:
    input_path = Path(input_dir or Config.INPUT_DIR)
    output_path = Path(output_dir or Config.OUTPUT_DIR)

    input_path.mkdir(parents=True, exist_ok=True)
    output_path.mkdir(parents=True, exist_ok=True)

    # cliente compartilhado, criado sob demanda (a chave só é exigida no uso)
    state: dict[str, Any] = {"curseforge": None}

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield

        client = state.get("curseforge")
        if client is not None:
            client.close()

        # remove as pastas de trabalho deixadas para trás
        work = output_path / ".work"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

    app = FastAPI(
        title="mrpack2curseforge", docs_url=None, redoc_url=None, lifespan=lifespan
    )
    manager = JobManager(output_dir=output_path)

    # exposto para testes e depuração
    app.state.jobs = manager
    app.state.input_dir = input_path
    app.state.output_dir = output_path

    def curseforge() -> CurseForgeClient:
        if state["curseforge"] is None:
            if not Config.CURSEFORGE_API_KEY:
                raise HTTPException(
                    status_code=400,
                    detail="CURSEFORGE_API_KEY não configurada no arquivo .env",
                )
            state["curseforge"] = CurseForgeClient(SimpleCache(Config.CACHE_PATH))
        return state["curseforge"]

    @contextmanager
    def modrinth_client():
        """Cliente do Modrinth com o cache fechado no fim.

        O cache precisa ser fechado junto: uma conexão SQLite deixada aberta
        trava o arquivo no Windows, e o "Limpar cache" não conseguia apagá-lo.
        """

        with SimpleCache(Config.CACHE_PATH) as cache, ModrinthClient(cache) as client:
            yield client

    def saved_update(name: str) -> Path:
        """O `.mrpack` de uma atualização salva. `Path(name).name` corta o
        caminho: o nome vem da URL e não pode escapar da pasta de saída."""

        arquivo = output_path / Path(name).name

        if not arquivo.is_file():
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")

        return arquivo

    def copy_to_input(origem: Path) -> dict[str, Any]:
        destino = input_path / origem.name
        shutil.copy2(origem, destino)
        return {"name": destino.name}

    def require_job(job_id: str) -> Job:
        job = manager.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job não encontrado")
        return job

    app.add_middleware(SecurityHeadersMiddleware)

    # ----------------------------------------------------------------- página
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        # 204 não pode ter corpo: `JSONResponse(status_code=204, content=None)`
        # serializava "null" e o h11 abortava com
        # "Too much data for declared Content-Length" a cada carregamento da página
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    # ------------------------------------------------------------------ estado
    @app.get("/api/state")
    def get_state() -> dict[str, Any]:
        packs = []
        usados = _last_used(output_path)

        for path in sorted(input_path.glob("*.mrpack")):
            stat = path.stat()
            packs.append(
                {
                    "name": path.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 1),
                    "modified": stat.st_mtime,
                    # quando este pack foi convertido/atualizado pela última vez
                    # (a interface põe os mais recentes no topo)
                    "last_used": usados.get(path.name),
                    # ler o índice é barato e fica em cache pelo mtime: sem isto
                    # a lista não diz para qual Minecraft/loader é cada pack
                    **_pack_meta(path, stat.st_mtime, stat.st_size),
                }
            )

        current = manager.current("conversion")
        current_update = manager.current("update")

        def resumo(job):
            if not job:
                return None
            return {
                "id": job.id,
                "kind": job.kind,
                "source": job.source.name,
                "status": job.status,
            }

        return {
            "input_dir": str(input_path),
            "output_dir": str(output_path),
            "api_key_configured": bool(Config.CURSEFORGE_API_KEY),
            # rodando por fora do comando `web` não há servidor para desligar
            "can_quit": getattr(app.state, "server", None) is not None,
            "packs": packs,
            "records": list_records(output_path, input_path),
            "current_job": resumo(current),
            "current_update": resumo(current_update),
        }

    # ------------------------------------------------------------------ upload
    @app.post("/api/upload")
    async def upload(file: UploadFile) -> dict[str, Any]:
        original = Path(file.filename or "modpack.mrpack").name

        if not original.lower().endswith(".mrpack"):
            raise HTTPException(status_code=400, detail="Envie um arquivo .mrpack")

        stem = safe_name(original[: -len(".mrpack")])
        destination = input_path / f"{stem}.mrpack"

        counter = 1
        while destination.exists():
            destination = input_path / f"{stem} ({counter}).mrpack"
            counter += 1

        with open(destination, "wb") as handle:
            while chunk := await file.read(1 << 20):
                handle.write(chunk)

        try:
            MrpackParser(destination).validate()
        except Exception as exc:  # noqa: BLE001
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Arquivo inválido: {exc}")

        return {
            "name": destination.name,
            "size_mb": round(destination.stat().st_size / (1024 * 1024), 1),
        }

    # -------------------------------------------------------------- inspeção
    @app.get("/api/packs/{name}/inspect")
    def inspect_pack(name: str) -> dict[str, Any]:
        path = input_path / Path(name).name

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Modpack não encontrado")

        parser = MrpackParser(path)
        parser.validate()
        pack = parser.parse()

        extras: dict[str, int] = {}
        for extra in pack.extra_files:
            folder = extra.file_path.split("/")[0]
            extras[folder] = extras.get(folder, 0) + 1

        return {
            "file": path.name,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            "name": pack.name,
            "version": pack.version,
            "summary": pack.summary,
            "minecraft": pack.minecraft.version,
            "loader": pack.loader_id,
            "mods": len(pack.mods),
            "extra_files": len(pack.extra_files),
            "extra_by_folder": extras,
            "override_files": len(pack.override_paths),
            "mod_files": [mod.file_name for mod in pack.mods],
        }

    @app.get("/api/packs/{name}/modrinth")
    def pack_modrinth(name: str) -> dict[str, Any]:
        """Nomes reais dos mods, consultados na API do Modrinth (em lote)."""

        path = input_path / Path(name).name

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Modpack não encontrado")

        parser = MrpackParser(path)
        parser.validate()
        pack = parser.parse()

        with modrinth_client() as modrinth:
            resolved = modrinth.resolve_projects(pack.mods)

        mods = []
        for mod in pack.mods:
            project = resolved.get(mod.file_path)
            mods.append(
                {
                    "file_name": mod.file_name,
                    "title": project.title if project else None,
                    "slug": project.slug if project else None,
                    "version": project.version_number if project else None,
                    "url": (
                        f"https://modrinth.com/mod/{project.slug}"
                        if project and project.slug
                        else None
                    ),
                }
            )

        return {"mods": mods, "identified": sum(1 for m in mods if m["title"])}

    # ------------------------------------------------------------------- jobs
    @app.post("/api/convert")
    def convert(payload: ConvertRequest) -> dict[str, Any]:
        path = input_path / Path(payload.file).name

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Modpack não encontrado")

        if not Config.CURSEFORGE_API_KEY:
            raise HTTPException(
                status_code=400,
                detail="CURSEFORGE_API_KEY não configurada no arquivo .env",
            )

        current = manager.current("conversion")
        if current is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Já existe uma conversão aberta ({current.source.name}). "
                    "Feche-a antes de iniciar outra."
                ),
            )

        job = manager.start_conversion(path, workers=payload.workers)
        return job.snapshot()

    # ------------------------------------------------------------ atualização
    @app.get("/api/minecraft-versions")
    def minecraft_versions() -> dict[str, Any]:
        """Versões do Minecraft que podem ser escolhidas como alvo."""

        with modrinth_client() as modrinth:
            return {"versions": modrinth.game_versions()}

    @app.post("/api/update")
    def update(payload: UpdateRequest) -> dict[str, Any]:
        path = input_path / Path(payload.file).name

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Modpack não encontrado")

        if not payload.minecraft.strip():
            raise HTTPException(status_code=400, detail="Escolha a versão do Minecraft")

        current = manager.current("update")
        if current is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Já existe uma atualização aberta ({current.source.name}). "
                    "Feche-a antes de iniciar outra."
                ),
            )

        job = manager.start_update(
            path,
            payload.minecraft.strip(),
            payload.loader_version or None,
            payload.workers,
            (payload.loader or "").strip().lower() or None,
        )
        return job.snapshot()

    # ---------------------------------------------------------- configurações
    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """Os campos do `.env`, com a chave da API já mascarada."""

        aberto = manager.current("conversion") or manager.current("update")

        return {
            **settings.estado(),
            # a interface desabilita os campos em vez de deixar você digitar
            # para levar 409 no fim
            "locked_by": aberto.source.name if aberto else None,
        }

    def settings_livre() -> None:
        """Recusa mexer nas configurações com trabalho aberto.

        Metade delas (workers, timeout, limites de página) é lida enquanto o
        trabalho roda: trocar no meio daria um resultado que não corresponde
        nem ao valor antigo nem ao novo.
        """

        aberto = manager.current("conversion") or manager.current("update")

        if aberto is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Há um trabalho aberto ({aberto.source.name}). "
                    "Feche-o antes de mexer nas configurações."
                ),
            )

    @app.put("/api/settings")
    def put_settings(payload: SettingsRequest) -> dict[str, Any]:
        settings_livre()
        resultado = settings.gravar(payload.values)

        if not resultado["ok"]:
            raise HTTPException(status_code=400, detail="; ".join(resultado["erros"]))

        # pastas e cache são resolvidos na importação: só valem no próximo boot
        resultado["restart_needed"] = sorted(
            set(payload.values) & settings.PRECISA_REINICIAR
        )
        resultado["state"] = settings.estado()
        return resultado

    @app.post("/api/settings/reset")
    def reset_settings() -> dict[str, Any]:
        """Volta ao padrão tudo o que a tela edita — menos a chave da API."""

        settings_livre()
        resultado = settings.restaurar_padrao()
        resultado["state"] = settings.estado()
        return resultado

    @app.post("/api/settings/forget-key")
    def forget_key() -> dict[str, Any]:
        """Apaga só a chave da API; o resto das configurações fica como está."""

        settings_livre()
        resultado = settings.apagar_segredos()
        resultado["state"] = settings.estado()
        return resultado

    # ------------------------------------------------------------------- cache
    @app.get("/api/cache")
    def cache_info() -> dict[str, Any]:
        return cache_stats(Config.CACHE_PATH)

    @app.delete("/api/cache")
    def wipe_cache() -> dict[str, Any]:
        """Apaga o cache das consultas ao Modrinth e ao CurseForge.

        Fecha antes o cliente compartilhado do CurseForge: no Windows um arquivo
        SQLite aberto não pode ser removido.
        """

        client = state.get("curseforge")
        if client is not None:
            client.close()
            state["curseforge"] = None

        _META_CACHE.clear()

        return clear_cache(Config.CACHE_PATH)

    @app.post("/api/shutdown")
    def shutdown() -> dict[str, Any]:
        """Encerra o servidor (o botão "Encerrar" da interface).

        Um trabalho em andamento é cancelado antes: as threads são `daemon`,
        então sair no meio de um download deixaria arquivos `.part` para trás.
        """

        abertos = [job for job in manager.jobs.values() if job.busy]

        for job in abertos:
            manager.cancel(job)

        servidor = getattr(app.state, "server", None)
        if servidor is None:
            raise HTTPException(
                status_code=501,
                detail="Este servidor não foi iniciado pelo comando `web`.",
            )

        servidor.should_exit = True
        return {"cancelled": [job.source.name for job in abertos]}

    @app.get("/api/loaders")
    def loaders() -> dict[str, Any]:
        """Modloaders que podem ser escolhidos como destino."""

        return {"loaders": list(LOADER_DEPENDENCY)}

    @app.get("/api/loaders/{loader}/versions")
    def versoes_do_loader(loader: str, minecraft: str) -> dict[str, Any]:
        """Versões daquele loader que servem na versão do Minecraft escolhida.

        Lista vazia não é erro: se o serviço do loader estiver fora, a interface
        deixa digitar à mão em vez de travar a atualização.
        """

        with SimpleCache(Config.CACHE_PATH) as cache:
            versoes = loader_versions(loader, minecraft, cache)

        return {
            "versions": versoes,
            # a mais nova estável, ou simplesmente a mais nova
            "latest": next(
                (v["version"] for v in versoes if v["stable"]),
                versoes[0]["version"] if versoes else None,
            ),
        }

    @app.get("/api/modrinth/search")
    def modrinth_search(q: str, loader: str | None = None) -> dict[str, Any]:
        """Procura outro projeto no Modrinth (o mod certo pode ser outro)."""

        with modrinth_client() as modrinth:
            return {"results": modrinth.search_projects(q, loader)}

    @app.get("/api/modrinth/projects/{project_id}")
    def modrinth_project(project_id: str) -> dict[str, Any]:
        """Metadados de um projeto do Modrinth."""

        with modrinth_client() as modrinth:
            info = modrinth.project_info(project_id)

        if not info:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")

        return info

    @app.get("/api/modrinth/projects/{project_id}/versions")
    def project_versions(project_id: str, loader: str | None = None) -> dict[str, Any]:
        """Versões publicadas de um projeto, para escolher à mão."""

        with modrinth_client() as modrinth:
            return {"versions": modrinth.project_versions(project_id, loader)}

    @app.put("/api/jobs/{job_id}/update-resolutions")
    def save_update_resolutions(
        job_id: str, payload: UpdateResolutionsRequest
    ) -> dict[str, Any]:
        job = require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        conhecidos = {result.mod.file_path for result in job.outcome.results}
        desconhecidos = [
            item.file_path
            for item in payload.choices
            if item.file_path not in conhecidos
        ]

        if desconhecidos:
            raise HTTPException(
                status_code=404, detail=f"Fora do pack: {', '.join(desconhecidos)}"
            )

        def validos(caminhos: list[str]) -> set[str]:
            return {caminho for caminho in caminhos if caminho in conhecidos}

        job.decisions = UpdateDecisions(
            versions={
                item.file_path: ManualPick(
                    version_id=item.version_id,
                    version_number=item.version_number,
                    file_name=item.file_name,
                    project_id=item.project_id,
                    project_title=item.project_title,
                )
                for item in payload.choices
            },
            keep=validos(payload.keep),
            exclude=validos(payload.exclude),
            include=validos(payload.include),
        )
        job.dirty = True

        return {
            "versions": len(job.decisions.versions),
            "keep": len(job.decisions.keep),
            "exclude": len(job.decisions.exclude),
            "include": len(job.decisions.include),
        }

    @app.post("/api/jobs/{job_id}/reapply")
    def reapply_update(job_id: str) -> dict[str, Any]:
        job = require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        if job.busy:
            raise HTTPException(status_code=409, detail="Trabalho ocupado")

        # cancelada ou com erro não volta a gerar: só revisão pendente ou
        # regeração de uma que já deu certo
        if job.status not in ("awaiting_review", "done"):
            raise HTTPException(
                status_code=409,
                detail=f"Atualização em estado '{job.status}': não dá para aplicar",
            )

        manager.start_update_reapply(job)
        return job.snapshot()

    @app.get("/api/updates")
    def list_updates() -> dict[str, Any]:
        """Packs atualizados que estão na pasta de saída, com o que foi decidido."""

        atualizacoes = []

        for report in sorted(output_path.glob("*-update.json")):
            try:
                dados = json.loads(report.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            arquivo = output_path / (dados.get("output") or "")

            atualizacoes.append(
                {
                    "name": arquivo.name,
                    "available": arquivo.is_file(),
                    "size_mb": (
                        round(arquivo.stat().st_size / (1024 * 1024), 1)
                        if arquivo.is_file()
                        else None
                    ),
                    "modified": (
                        arquivo.stat().st_mtime if arquivo.is_file() else None
                    ),
                    "pack": dados.get("pack", {}),
                    "from_minecraft": dados.get("from_minecraft"),
                    "to_minecraft": dados.get("to_minecraft"),
                    "loader": dados.get("loader"),
                    # sem isto a lista não mostra a troca de loader, só a de MC
                    "from_loader": dados.get("from_loader"),
                    "summary": dados.get("summary", {}),
                }
            )

        atualizacoes.sort(key=lambda item: item["modified"] or 0, reverse=True)
        return {"updates": atualizacoes}

    @app.get("/api/updates/{name}")
    def get_update(name: str) -> dict[str, Any]:
        report = output_path / f"{Path(name).stem}-update.json"

        if not report.is_file():
            raise HTTPException(status_code=404, detail="Atualização não encontrada")

        dados = json.loads(report.read_text(encoding="utf-8"))
        dados["available"] = (output_path / Path(name).name).is_file()
        return dados

    @app.get("/api/updates/{name}/download")
    def download_update(name: str) -> FileResponse:
        arquivo = saved_update(name)

        return FileResponse(
            arquivo, media_type="application/octet-stream", filename=arquivo.name
        )

    @app.post("/api/updates/{name}/to-input")
    def update_to_input(name: str) -> dict[str, Any]:
        return copy_to_input(saved_update(name))

    @app.delete("/api/updates/{name}")
    def delete_update(name: str) -> dict[str, bool]:
        alvo = output_path / Path(name).name
        report = output_path / f"{alvo.stem}-update.json"

        removidos = False

        for caminho in (alvo, report):
            if caminho.is_file():
                caminho.unlink()
                removidos = True

        if not removidos:
            raise HTTPException(status_code=404, detail="Atualização não encontrada")

        return {"deleted": True}

    @app.post("/api/jobs/{job_id}/to-input")
    def send_to_input(job_id: str) -> dict[str, Any]:
        """Copia o pack atualizado para `input_modpacks/`, pronto para converter."""

        job = require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        if not job.outcome.output.is_file():
            raise HTTPException(status_code=404, detail="O .mrpack não está mais lá")

        return copy_to_input(job.outcome.output)

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, log_offset: int = 0) -> dict[str, Any]:
        return require_job(job_id).snapshot(log_offset=log_offset)

    @app.get("/api/jobs/{job_id}/conflicts")
    def get_conflicts(job_id: str) -> dict[str, Any]:
        job = require_job(job_id)
        return {"conflicts": job.conflicts(), "status": job.status}

    @app.put("/api/jobs/{job_id}/resolutions")
    def save_resolutions(job_id: str, payload: ResolutionsRequest) -> dict[str, Any]:
        """Substitui todas as escolhas manuais do job de uma vez."""

        job = require_job(job_id)

        results = job.outcome.results if job.outcome else []
        known = {result.mod.file_name for result in results}

        unknown = [r.file_name for r in payload.resolutions if r.file_name not in known]
        if unknown:
            raise HTTPException(
                status_code=404, detail=f"Mods fora do job: {', '.join(unknown)}"
            )

        job.dirty = True
        job.resolutions = {
            item.file_name: Resolution(
                project_id=item.project_id,
                file_id=item.file_id,
                project_name=item.project_name,
                project_slug=item.project_slug,
                file_name=item.curseforge_file_name,
            )
            for item in payload.resolutions
        }

        return {"conflicts": job.conflicts(), "saved": len(job.resolutions)}

    @app.post("/api/jobs/{job_id}/apply")
    def apply_changes(job_id: str) -> dict[str, Any]:
        """Segue com os downloads e a geração do `.zip`."""

        job = require_job(job_id)

        if job.outcome is None:
            raise HTTPException(status_code=400, detail="A análise ainda não terminou")

        if job.busy:
            raise HTTPException(status_code=409, detail="Conversão ocupada")

        if job.status not in ("awaiting_conflicts", "done"):
            raise HTTPException(
                status_code=409,
                detail=f"Conversão em estado '{job.status}': não dá para aplicar",
            )

        manager.start_finish(job)
        return job.snapshot()

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = require_job(job_id)
        manager.cancel(job)
        return job.snapshot()

    @app.post("/api/jobs/{job_id}/close")
    def close_job(job_id: str) -> dict[str, bool]:
        """Fecha a conversão e libera a vaga para a próxima."""

        manager.close(require_job(job_id))
        return {"closed": True}

    @app.get("/api/jobs/{job_id}/download")
    def download(job_id: str) -> FileResponse:
        job = require_job(job_id)

        if job.outcome is None or not job.outcome.output.exists():
            raise HTTPException(status_code=404, detail="Modpack ainda não foi gerado")

        return FileResponse(
            job.outcome.output,
            media_type="application/zip",
            filename=job.outcome.output.name,
        )

    @app.get("/api/jobs/{job_id}/report")
    def download_report(job_id: str) -> FileResponse:
        """Registro em JSON, decisão a decisão.

        Serve as duas ferramentas: a conversão guarda em `record_path`, a
        atualização em `report_path`.
        """

        job = require_job(job_id)
        caminho = None

        if job.outcome is not None:
            caminho = getattr(job.outcome, "record_path", None) or getattr(
                job.outcome, "report_path", None
            )

        if caminho is None or not caminho.exists():
            raise HTTPException(status_code=404, detail="Registro indisponível")

        return FileResponse(
            caminho, media_type="application/json", filename=caminho.name
        )

    # --------------------------------------------------------- conversões salvas
    @app.get("/api/records")
    def get_records() -> dict[str, Any]:
        return {"records": list_records(output_path, input_path)}

    @app.get("/api/records/{record_id}")
    def get_record(record_id: str) -> dict[str, Any]:
        record = load_record(output_path, record_id)

        if not record:
            raise HTTPException(status_code=404, detail="Conversão não encontrada")

        source = input_path / Path(record.get("source") or "").name
        record["source_available"] = source.is_file()

        return record

    @app.post("/api/records/{record_id}/generate")
    def generate_from_record(record_id: str) -> dict[str, Any]:
        """Regera o `.zip` a partir do registro (sem consultar o CurseForge)."""

        record = load_record(output_path, record_id)

        if not record:
            raise HTTPException(status_code=404, detail="Conversão não encontrada")

        source = input_path / Path(record.get("source") or "").name

        if not source.is_file():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"O arquivo de origem '{record.get('source')}' não está mais em "
                    "input_modpacks/ — sem ele não dá para remontar o modpack."
                ),
            )

        if manager.current("conversion") is not None:
            raise HTTPException(
                status_code=409,
                detail="Feche a conversão aberta antes de gerar outro modpack.",
            )

        return manager.start_rebuild(record, source).snapshot()

    @app.delete("/api/records/{record_id}")
    def remove_record(record_id: str) -> dict[str, bool]:
        if delete_record(output_path, record_id):
            return {"deleted": True}

        raise HTTPException(status_code=404, detail="Conversão não encontrada")

    # -------------------------------------------------------- CurseForge (UI)
    @app.get("/api/curseforge/search")
    def search(q: str) -> dict[str, Any]:
        if not q.strip():
            return {"results": []}

        try:
            # 3 páginas: buscas comuns ("Better Combat") passam de 100 resultados
            # e o projeto certo pode estar na terceira
            found = curseforge().search(query=q.strip(), pages=3)
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # a ordenação da API é fraca; reaproveitamos o ranking do matcher
        found = rank_projects(found, q.strip())

        results = [_project_payload(project) for project in found if project.get("id")]

        return {"results": results[:40]}

    @app.get("/api/curseforge/projects/{project_id}")
    def project_info(project_id: int) -> dict[str, Any]:
        try:
            project = curseforge().get_mod(project_id)
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        if not project:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")

        return _project_payload(project)

    @app.get("/api/curseforge/projects/{project_id}/files")
    def project_files(
        project_id: int, game_version: str | None = None, pages: int = 4
    ) -> dict[str, Any]:
        try:
            files = curseforge().get_files(
                project_id,
                game_version=game_version or None,
                max_pages=max(1, min(pages, 10)),
            )
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # a data só serve para ordenar aqui; não vai no payload
        files = sorted(files, key=lambda f: f.get("fileDate") or "", reverse=True)

        return {
            "files": [
                {
                    "id": file.get("id"),
                    "file_name": file.get("fileName"),
                    "size_mb": round(
                        (file.get("fileLength") or 0) / (1024 * 1024), 2
                    ),
                    "game_versions": [
                        version
                        for version in (file.get("gameVersions") or [])
                        if version
                    ],
                    "release_type": {1: "release", 2: "beta", 3: "alpha"}.get(
                        file.get("releaseType"), "?"
                    ),
                }
                for file in files
                if file.get("id")
            ]
        }

    return app


# --------------------------------------------------------------------------- #
# Execução
# --------------------------------------------------------------------------- #
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    import uvicorn

    app = create_app(input_dir=input_dir, output_dir=output_dir)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )

    # o botão "Encerrar" da interface precisa de quem desligar: guardar o
    # servidor aqui é o único jeito de sair limpo (no Windows, mandar sinal
    # para o próprio processo não encerra o uvicorn de forma confiável)
    app.state.server = server

    server.run()
