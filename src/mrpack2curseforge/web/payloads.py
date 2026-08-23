"""Formatos que a interface consome, longe do FastAPI.

Traduzem dados brutos — um projeto do CurseForge, um `.mrpack` no disco, uma
linha de log com marcação do `rich` — para o dicionário que a tela espera.
Testável sem subir servidor, e é por isso que estas funções não moram junto de
quem as chama (`routes/`, `jobs.py`): lá elas viriam com um servidor a tiracolo.
"""

import json
import re
from pathlib import Path
from typing import Any

from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.records import list_records
from mrpack2curseforge.updater import default_excluded


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


# ============================================================= log do trabalho
# só as tags que o projeto usa: assim um nome de arquivo com colchetes
# (ex.: "mod[1.20].jar") não é mutilado no log
MARKUP = re.compile(r"\[/?(?:red|green|yellow|cyan|blue|dim|bold)\]", re.IGNORECASE)
LEVEL_BY_TAG = {
    "red": "error",
    "yellow": "warn",
    "green": "ok",
    "cyan": "cyan",
    # `dim` é o rótulo secundário ("projeto no CurseForge:"), o texto sem
    # marcação é o conteúdo em si — precisam ser distinguíveis
    "dim": "dim",
}


def log_segments(message: str) -> list[dict[str, str]]:
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


def log_plain(message: str) -> tuple[str, str]:
    """Texto sem marcação + cor de base da linha.

    Regra: linha que começa com `[bold]` é título/resumo e fica neutra (ela mistura
    verde, amarelo e vermelho e não é um erro); nas demais vale a primeira cor
    encontrada, mesmo que venha depois da indentação.
    """

    parts = log_segments(message)
    text = "".join(part["text"] for part in parts)

    if message.lstrip().lower().startswith("[bold]"):
        return text, "info"

    level = next((part["level"] for part in parts if part["level"] != "info"), "info")

    return text, level


# ================================================================ atualização
def file_payload(result, decisions) -> dict[str, Any]:
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
        "excluded": decided_exclusion(result, decisions),
    }


def decided_exclusion(result, decisions) -> bool:
    """Decisão do usuário quando existe; senão, o padrão do atualizador."""

    caminho = result.mod.file_path

    if caminho in decisions.exclude:
        return True
    if caminho in decisions.include:
        return False

    return default_excluded(result)


def update_payload(outcome, decisions) -> dict[str, Any]:
    """Resultado de uma atualização, do jeito que a interface consome."""

    return {
        "packaged": outcome.packaged,
        # com versão para o alvo (dá para trocar a versão de qualquer um)
        "with_version": [
            file_payload(result, decisions) for result in outcome.with_version
        ],
        # sem versão para o alvo: entram como estão ou ficam de fora
        "without_version": [
            file_payload(result, decisions) for result in outcome.without_version
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
