"""Cliente da API do Modrinth.

Serve a dois usos:

* **conversão** — descobrir o nome real de cada mod a partir do `.jar`
  (hash SHA1 -> versão -> projeto), porque o nome do arquivo é uma consulta ruim;
* **atualização** — achar a versão mais recente de cada projeto para uma versão
  do Minecraft e um loader.
"""

import json
import re
import threading
import time
from collections import deque
from typing import Any, Iterable

import httpx

from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import MODRINTH_API, USER_AGENT
from mrpack2curseforge.domain import ModrinthProject, PackFile
from mrpack2curseforge.exceptions import ApiError
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.http import VAZIO, fetch_json

CDN_PROJECT_RE = re.compile(r"cdn\.modrinth\.com/data/([A-Za-z0-9]+)/", re.IGNORECASE)

BATCH_SIZE = 100

# a API do Modrinth permite 300 requisições por minuto; ficamos abaixo disso de
# propósito, já que a atualização faz uma consulta por projeto
RATE_LIMIT_PER_MINUTE = 240

# release primeiro; beta e alpha só se não houver release
VERSION_TYPE_ORDER = {"release": 0, "beta": 1, "alpha": 2}


def _chunks(items: list[Any], size: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


class ModrinthClient:
    def __init__(self, cache: SimpleCache):
        self.cache = cache
        self._lock = threading.Lock()
        self._recent: deque[float] = deque()
        self.client = httpx.Client(
            base_url=MODRINTH_API,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=Config.HTTP_TIMEOUT,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "ModrinthClient":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    # ------------------------------------------------------------ transporte
    def _wait_for_slot(self) -> None:
        """Segura a vazão abaixo do limite da API (a atualização é intensa)."""

        while True:
            with self._lock:
                agora = time.monotonic()

                while self._recent and agora - self._recent[0] > 60:
                    self._recent.popleft()

                if len(self._recent) < RATE_LIMIT_PER_MINUTE:
                    self._recent.append(agora)
                    return

                espera = 60 - (agora - self._recent[0])

            time.sleep(max(espera, 0.05))

    def _request(self, method: str, url: str, **kwargs) -> Any:
        """Uma requisição na API. Devolve `None` para "não deu".

        Aqui desistir e não existir dão no mesmo: nenhuma chamada do Modrinth é
        obrigatória para a conversão seguir — sem o nome do projeto o matcher
        cai para as estratégias que só olham o nome do arquivo, e a atualização
        marca o mod como não identificado.
        """

        self._wait_for_slot()

        try:
            data = fetch_json(self.client, method, url, label="Modrinth ", **kwargs)
        except ApiError:
            return None

        return None if data is VAZIO else data

    # ------------------------------------------------------------- pipeline
    def resolve_projects(self, mods: list[PackFile]) -> dict[str, ModrinthProject]:
        """Mapeia `file_path` -> metadados do projeto no Modrinth.

        Nunca levanta exceção: mods que não puderem ser resolvidos simplesmente
        não aparecem no dicionário (o matcher cai no fallback por regex).
        """

        versions = self._resolve_versions(mods)

        # project_id por mod (via hash ou via URL do CDN)
        project_by_mod: dict[str, str] = {}
        version_number: dict[str, str] = {}

        for mod in mods:
            version = versions.get((mod.sha1 or "").lower())

            if version and version.get("project_id"):
                project_by_mod[mod.file_path] = version["project_id"]
                if version.get("version_number"):
                    version_number[mod.file_path] = version["version_number"]
                continue

            if mod.download_url:
                found = CDN_PROJECT_RE.search(mod.download_url)
                if found:
                    project_by_mod[mod.file_path] = found.group(1)

        projects = self._resolve_project_metadata(set(project_by_mod.values()))

        resolved: dict[str, ModrinthProject] = {}

        for file_path, project_id in project_by_mod.items():
            meta = projects.get(project_id, {})
            resolved[file_path] = ModrinthProject(
                project_id=project_id,
                slug=meta.get("slug"),
                title=meta.get("title"),
                icon_url=meta.get("icon_url"),
                version_number=version_number.get(file_path),
            )

        return resolved

    # ------------------------------------------------- arquivos mais recentes
    def recent_file_names(self, project_id: str, limit: int = 10) -> list[str]:
        """Nomes dos arquivos das `limit` versões mais recentes do projeto.

        Usado no diagnóstico: comparar esses nomes com os arquivos recentes do
        candidato no CurseForge diz se o mod existe lá (e só a versão mudou) ou
        se ele realmente não está na plataforma.
        """

        cache_key = f"{project_id}|{limit}"
        cached = self.cache.get("mr_recent", cache_key)
        if cached is not None:
            return cached

        data = self._request("GET", f"/project/{project_id}/version")

        names: list[str] = []

        if isinstance(data, list):
            versions = sorted(
                data,
                key=lambda v: v.get("date_published") or "",
                reverse=True,
            )

            for version in versions:
                files = version.get("files") or []
                primary = next(
                    (f for f in files if f.get("primary")),
                    files[0] if files else None,
                )

                if primary and primary.get("filename"):
                    names.append(primary["filename"])

                if len(names) >= limit:
                    break

        self.cache.set("mr_recent", cache_key, names)
        return names

    # ------------------------------------------------------ atualização
    def game_versions(self, releases_only: bool = True) -> list[str]:
        """Versões do Minecraft conhecidas pelo Modrinth, da mais nova para a
        mais antiga."""

        chave = "release" if releases_only else "all"
        cached = self.cache.get("mr_game_versions", chave)
        if cached is not None:
            return cached

        data = self._request("GET", "/tag/game_version")

        versions = [
            item["version"]
            for item in (data or [])
            if item.get("version")
            and (not releases_only or item.get("version_type") == "release")
        ]

        self.cache.set(
            "mr_game_versions", "release" if releases_only else "all", versions
        )
        return versions

    def latest_version(
        self, project_id: str, game_version: str, loader: str | None = None
    ) -> dict[str, Any] | None:
        """Versão mais recente do projeto para essa versão do Minecraft.

        Prefere `release`; só cai para `beta`/`alpha` se não houver release.
        Devolve só o que o índice do `.mrpack` precisa.
        """

        cache_key = f"{project_id}|{game_version}|{loader or ''}"
        cached = self.cache.get("mr_latest", cache_key)
        if cached is not None:
            return cached or None

        params: dict[str, str] = {"game_versions": json.dumps([game_version])}
        if loader:
            params["loaders"] = json.dumps([loader])

        data = self._request("GET", f"/project/{project_id}/version", params=params)

        melhor = self._pick_version(data if isinstance(data, list) else [])

        # grava inclusive a ausência de versão, para não reconsultar
        self.cache.set("mr_latest", cache_key, melhor or {})
        return melhor

    def search_projects(
        self, query: str, loader: str | None = None, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Busca por nome, para quando o mod certo é outro projeto.

        É o mesmo papel do "procurar outro projeto" da aba de conflitos: às vezes
        o mod foi renomeado, virou fork ou o pack traz um jar de outra origem.
        """

        query = (query or "").strip()
        if not query:
            return []

        cache_key = f"{query.lower()}|{loader or ''}|{limit}"
        cached = self.cache.get("mr_search", cache_key)
        if cached is not None:
            return cached

        params: dict[str, Any] = {"query": query, "limit": limit}
        if loader:
            params["facets"] = json.dumps([[f"categories:{loader}"]])

        data = self._request("GET", "/search", params=params)
        hits = (data or {}).get("hits") or []

        results = [
            {
                "project_id": hit.get("project_id"),
                "slug": hit.get("slug"),
                "title": hit.get("title"),
                "description": hit.get("description"),
                "icon": hit.get("icon_url"),
                "downloads": hit.get("downloads") or 0,
                "author": hit.get("author"),
                "project_type": hit.get("project_type"),
                "game_versions": hit.get("versions") or [],
                "url": (
                    f"https://modrinth.com/{hit.get('project_type') or 'mod'}/"
                    f"{hit.get('slug')}"
                    if hit.get("slug")
                    else None
                ),
            }
            for hit in hits
            if hit.get("project_id")
        ]

        self.cache.set("mr_search", cache_key, results)
        return results

    def project_info(self, project_id: str) -> dict[str, Any] | None:
        """Metadados de um projeto (cabeçalho do seletor de versões)."""

        cached = self.cache.get("mr_project_info", project_id)
        if cached is not None:
            return cached or None

        data = self._request("GET", f"/project/{project_id}")

        info = (
            {
                "project_id": data.get("id"),
                "slug": data.get("slug"),
                "title": data.get("title"),
                "description": data.get("description"),
                "icon": data.get("icon_url"),
                "downloads": data.get("downloads") or 0,
                "project_type": data.get("project_type"),
                "url": (
                    f"https://modrinth.com/{data.get('project_type') or 'mod'}/"
                    f"{data.get('slug')}"
                    if data.get("slug")
                    else None
                ),
            }
            if isinstance(data, dict) and data.get("id")
            else None
        )

        self.cache.set("mr_project_info", project_id, info or {})
        return info

    def project_versions(
        self, project_id: str, loader: str | None = None, limit: int = 60
    ) -> list[dict[str, Any]]:
        """Versões publicadas do projeto, da mais recente para a mais antiga.

        Sem filtro de versão do Minecraft: serve justamente para o usuário ver o
        que existe quando não há nada para o alvo escolhido.
        """

        cache_key = f"{project_id}|{loader or ''}|{limit}"
        cached = self.cache.get("mr_versions", cache_key)
        if cached is not None:
            return cached

        params = {"loaders": json.dumps([loader])} if loader else {}
        data = self._request("GET", f"/project/{project_id}/version", params=params)

        versions = [
            item
            for item in (self._slim_version(v) for v in (data or []))
            if item
        ]
        versions.sort(key=lambda v: v["date_published"], reverse=True)
        versions = versions[:limit]

        self.cache.set("mr_versions", cache_key, versions)
        return versions

    def version(self, version_id: str) -> dict[str, Any] | None:
        """Uma versão específica (usada ao aplicar uma escolha manual)."""

        cached = self.cache.get("mr_version_by_id", version_id)
        if cached is not None:
            return cached or None

        data = self._request("GET", f"/version/{version_id}")
        slim = self._slim_version(data) if isinstance(data, dict) else None

        self.cache.set("mr_version_by_id", version_id, slim or {})
        return slim

    @staticmethod
    def _slim_version(version: dict[str, Any] | None) -> dict[str, Any] | None:
        """Só o que o índice do `.mrpack` e a interface precisam."""

        if not version:
            return None

        files = version.get("files") or []
        primary = next(
            (f for f in files if f.get("primary")), files[0] if files else None
        )

        if not primary or not primary.get("url"):
            return None

        return {
            "id": version.get("id"),
            # a interface precisa saber de que projeto veio a escolha manual
            "project_id": version.get("project_id"),
            "version_number": version.get("version_number"),
            "version_type": version.get("version_type") or "release",
            "date_published": version.get("date_published") or "",
            "game_versions": version.get("game_versions") or [],
            "loaders": version.get("loaders") or [],
            "file": {
                "filename": primary.get("filename"),
                "url": primary.get("url"),
                "size": primary.get("size"),
                "sha1": (primary.get("hashes") or {}).get("sha1"),
                "sha512": (primary.get("hashes") or {}).get("sha512"),
            },
        }

    @staticmethod
    def _pick_version(versions: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidatos = [
            slim
            for slim in (ModrinthClient._slim_version(v) for v in versions)
            if slim
        ]

        if not candidatos:
            return None

        # ordenação estável: primeiro a data (mais nova antes), depois o tipo —
        # o resultado é "release mais recente, senão beta mais recente…"
        candidatos.sort(key=lambda v: v["date_published"], reverse=True)
        candidatos.sort(key=lambda v: VERSION_TYPE_ORDER.get(v["version_type"], 3))

        return candidatos[0]

    # -------------------------------------------------------------- versões
    def _resolve_versions(self, mods: list[PackFile]) -> dict[str, dict[str, Any]]:
        hashes = [m.sha1.lower() for m in mods if m.sha1]
        if not hashes:
            return {}

        found: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        for sha1 in hashes:
            cached = self.cache.get("mr_version", sha1)
            if cached is not None:
                found[sha1] = cached
            else:
                missing.append(sha1)

        for batch in _chunks(sorted(set(missing)), BATCH_SIZE):
            data = self._request(
                "POST",
                "/version_files",
                json={"hashes": batch, "algorithm": "sha1"},
            )

            if not isinstance(data, dict):
                continue

            for sha1, version in data.items():
                slim = {
                    "project_id": version.get("project_id"),
                    "version_number": version.get("version_number"),
                }
                self.cache.set("mr_version", sha1.lower(), slim)
                found[sha1.lower()] = slim

        return found

    # ------------------------------------------------------------- projetos
    def _resolve_project_metadata(self, ids: set[str]) -> dict[str, dict[str, Any]]:
        if not ids:
            return {}

        found: dict[str, dict[str, Any]] = {}
        missing: list[str] = []

        # `mr_project2`: a versão anterior não guardava o ícone; trocar o namespace
        # revalida os projetos já em cache sem precisar de migração
        for project_id in sorted(ids):
            cached = self.cache.get("mr_project2", project_id)
            if cached is not None:
                found[project_id] = cached
            else:
                missing.append(project_id)

        for batch in _chunks(missing, BATCH_SIZE):
            data = self._request(
                "GET", "/projects", params={"ids": json.dumps(batch)}
            )

            if not isinstance(data, list):
                continue

            for project in data:
                slim = {
                    "slug": project.get("slug"),
                    "title": project.get("title"),
                    "icon_url": project.get("icon_url"),
                }
                project_id = project.get("id")
                if project_id:
                    self.cache.set("mr_project2", project_id, slim)
                    found[project_id] = slim

        return found
