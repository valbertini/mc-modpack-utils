"""Formatos que a interface consome, longe do FastAPI.

Traduzem dados brutos — um projeto do CurseForge, um `.mrpack` no disco —
para o dicionário que a tela espera. Testável sem subir servidor.
"""

import json
from pathlib import Path
from typing import Any

from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.records import list_records


def project_payload(project: dict[str, Any]) -> dict[str, Any]:
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


def pack_meta(path: Path, mtime: float, size: int) -> dict[str, Any]:
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


def last_used(output_dir: Path) -> dict[str, float]:
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


def clear_pack_meta() -> None:
    """Esquece os índices já lidos (o "Limpar cache" da interface)."""

    _META_CACHE.clear()
