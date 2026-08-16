"""Cliente HTTP da API do CurseForge.

Responsável apenas por falar com a API (busca, listagem de arquivos), cache e
retries. Nenhuma heurística de matching mora aqui.
"""

import time
from typing import Any

import httpx

from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import (
    CURSEFORGE_API,
    CURSEFORGE_CLASS_MODS,
    CURSEFORGE_GAME_ID,
    CURSEFORGE_PAGE_SIZE,
    USER_AGENT,
)
from mrpack2curseforge.exceptions import ApiError
from mrpack2curseforge.services.cache import SimpleCache


def slim_file(file: dict[str, Any]) -> dict[str, Any]:
    """Só os campos de um arquivo que o projeto usa.

    A resposta da API traz hashes, dependências, módulos e changelog — nada disso
    é usado, e era o que fazia o cache passar de 40 MB.
    """

    return {
        "id": file.get("id"),
        "fileName": file.get("fileName"),
        "displayName": file.get("displayName"),
        "fileDate": file.get("fileDate"),
        "fileLength": file.get("fileLength"),
        "releaseType": file.get("releaseType"),
        "gameVersions": file.get("gameVersions") or [],
    }


def slim_project(project: dict[str, Any]) -> dict[str, Any]:
    """Só os campos de um projeto que o matcher e a interface usam."""

    logo = project.get("logo") or {}

    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "slug": project.get("slug"),
        "summary": project.get("summary"),
        "downloadCount": project.get("downloadCount"),
        "logo": {
            "thumbnailUrl": logo.get("thumbnailUrl"),
            "url": logo.get("url"),
        },
        "authors": [
            {"name": author.get("name")}
            for author in (project.get("authors") or [])
            if author.get("name")
        ],
        "links": {"websiteUrl": (project.get("links") or {}).get("websiteUrl")},
        "latestFiles": [slim_file(f) for f in (project.get("latestFiles") or [])],
    }


class CurseForgeClient:
    def __init__(self, cache: SimpleCache, api_key: str | None = None):
        self.cache = cache
        self.client = httpx.Client(
            base_url=CURSEFORGE_API,
            headers={
                "x-api-key": api_key or Config.require_api_key(),
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
            timeout=Config.HTTP_TIMEOUT,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "CurseForgeClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------ transporte
    def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(Config.HTTP_RETRIES):
            try:
                response = self.client.get(url, params=params)

                if response.status_code == 429:
                    wait = float(response.headers.get("Retry-After", 2 ** attempt))
                    time.sleep(min(wait, 30))
                    continue

                if response.status_code in (404, 400):
                    return {}

                response.raise_for_status()
                return response.json()

            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))

        raise ApiError(f"CurseForge {url} falhou: {last_error}")

    # ---------------------------------------------------------------- busca
    def search(
        self,
        query: str | None = None,
        slug: str | None = None,
        pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Busca projetos. `slug` faz lookup exato; `query` faz busca textual."""

        if not query and not slug:
            return []

        pages = pages if pages is not None else Config.SEARCH_PAGES
        cache_key = f"{slug or ''}|{query or ''}|{pages}"

        cached = self.cache.get("search", cache_key)
        if cached is not None:
            return cached

        results: list[dict[str, Any]] = []

        for page in range(pages):
            params: dict[str, Any] = {
                "gameId": CURSEFORGE_GAME_ID,
                "classId": CURSEFORGE_CLASS_MODS,
                "pageSize": CURSEFORGE_PAGE_SIZE,
                "index": page * CURSEFORGE_PAGE_SIZE,
            }

            if slug:
                params["slug"] = slug
            if query:
                params["searchFilter"] = query

            data = self._get("/mods/search", params).get("data") or []
            results.extend(slim_project(project) for project in data)

            if len(data) < CURSEFORGE_PAGE_SIZE:
                break

            # lookup por slug retorna no máximo um punhado de projetos
            if slug:
                break

        self.cache.set("search", cache_key, results)
        return results

    # -------------------------------------------------------------- arquivos
    def get_files(
        self,
        mod_id: int,
        game_version: str | None = None,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Lista arquivos de um projeto (paginado, com limite de segurança)."""

        max_pages = max_pages if max_pages is not None else Config.FILE_PAGES
        cache_key = f"{mod_id}|{game_version or ''}|{max_pages}"

        cached = self.cache.get("files", cache_key)
        if cached is not None:
            return cached

        files: list[dict[str, Any]] = []

        for page in range(max_pages):
            params: dict[str, Any] = {
                "pageSize": CURSEFORGE_PAGE_SIZE,
                "index": page * CURSEFORGE_PAGE_SIZE,
            }
            if game_version:
                params["gameVersion"] = game_version

            data = self._get(f"/mods/{mod_id}/files", params).get("data") or []
            files.extend(slim_file(file) for file in data)

            if len(data) < CURSEFORGE_PAGE_SIZE:
                break

        self.cache.set("files", cache_key, files)
        return files

    # ------------------------------------------------------------- projetos
    def get_mod(self, mod_id: int) -> dict[str, Any]:
        cached = self.cache.get("mod", str(mod_id))
        if cached is not None:
            return cached

        data = self._get(f"/mods/{mod_id}", {}).get("data") or {}
        data = slim_project(data) if data else {}

        self.cache.set("mod", str(mod_id), data)
        return data
