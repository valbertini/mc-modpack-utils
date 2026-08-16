"""Consultas de catálogo: Minecraft, loaders, Modrinth e CurseForge."""

from typing import Any

from fastapi import APIRouter, HTTPException

from mrpack2curseforge.builders.mrpack import LOADER_DEPENDENCY
from mrpack2curseforge.config import Config
from mrpack2curseforge.exceptions import ApiError
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.loaders import loader_versions
from mrpack2curseforge.services.matcher import rank_projects
from mrpack2curseforge.web.context import AppContext
from mrpack2curseforge.web.payloads import project_payload


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.get("/api/minecraft-versions")
    def minecraft_versions() -> dict[str, Any]:
        """Versões do Minecraft que podem ser escolhidas como alvo."""

        with ctx.modrinth() as modrinth:
            return {"versions": modrinth.game_versions()}

    @api.get("/api/loaders")
    def loaders() -> dict[str, Any]:
        """Modloaders que podem ser escolhidos como destino."""

        return {"loaders": list(LOADER_DEPENDENCY)}

    @api.get("/api/loaders/{loader}/versions")
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

    @api.get("/api/modrinth/search")
    def modrinth_search(q: str, loader: str | None = None) -> dict[str, Any]:
        """Procura outro projeto no Modrinth (o mod certo pode ser outro)."""

        with ctx.modrinth() as modrinth:
            return {"results": modrinth.search_projects(q, loader)}

    @api.get("/api/modrinth/projects/{project_id}")
    def modrinth_project(project_id: str) -> dict[str, Any]:
        """Metadados de um projeto do Modrinth."""

        with ctx.modrinth() as modrinth:
            info = modrinth.project_info(project_id)

        if not info:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")

        return info

    @api.get("/api/modrinth/projects/{project_id}/versions")
    def project_versions(project_id: str, loader: str | None = None) -> dict[str, Any]:
        """Versões publicadas de um projeto, para escolher à mão."""

        with ctx.modrinth() as modrinth:
            return {"versions": modrinth.project_versions(project_id, loader)}

    @api.get("/api/curseforge/search")
    def search(q: str) -> dict[str, Any]:
        if not q.strip():
            return {"results": []}

        try:
            # 3 páginas: buscas comuns ("Better Combat") passam de 100 resultados
            # e o projeto certo pode estar na terceira
            found = ctx.curseforge().search(query=q.strip(), pages=3)
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        # a ordenação da API é fraca; reaproveitamos o ranking do matcher
        found = rank_projects(found, q.strip())

        results = [project_payload(project) for project in found if project.get("id")]

        return {"results": results[:40]}

    @api.get("/api/curseforge/projects/{project_id}")
    def project_info(project_id: int) -> dict[str, Any]:
        try:
            project = ctx.curseforge().get_mod(project_id)
        except ApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc))

        if not project:
            raise HTTPException(status_code=404, detail="Projeto não encontrado")

        return project_payload(project)

    @api.get("/api/curseforge/projects/{project_id}/files")
    def project_files(
        project_id: int, game_version: str | None = None, pages: int = 4
    ) -> dict[str, Any]:
        try:
            files = ctx.curseforge().get_files(
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

    return api
