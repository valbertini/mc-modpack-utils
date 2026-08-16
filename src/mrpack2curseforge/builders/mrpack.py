"""Escrita de um `.mrpack` novo a partir de um índice atualizado.

Nada é baixado: o índice do Modrinth guarda URL, tamanho e hashes de cada
arquivo, e o `overrides/` é copiado entrada por entrada do pack original.
"""

import json
import os
import time
import zipfile
from pathlib import Path

from mrpack2curseforge.constants import MODRINTH_INDEX_FILE
from mrpack2curseforge.domain import Modpack, UpdateResult
from mrpack2curseforge.exceptions import Mrpack2CurseForgeError

LOADER_DEPENDENCY = {
    "fabric": "fabric-loader",
    "quilt": "quilt-loader",
    "forge": "forge",
    "neoforge": "neoforge",
}


def build_index(
    pack: Modpack,
    results: list[UpdateResult],
    minecraft_version: str,
    loader_version: str | None = None,
    loader: str | None = None,
) -> dict:
    """Monta o `modrinth.index.json` do pack atualizado.

    `loader` troca o modloader do pack (fabric -> neoforge, por exemplo); sem ele
    fica o mesmo do pack de origem.
    """

    files = []

    for result in results:
        if result.excluded:
            # o usuário decidiu não levar este arquivo para o pack novo
            continue

        item = result.final_file

        if not item.download_url:
            # sem URL não dá para listar no índice; o arquivo se perderia
            continue

        entry: dict = {
            "path": item.file_path,
            "hashes": {
                key: value
                for key, value in (("sha1", item.sha1), ("sha512", item.sha512))
                if value
            },
            "downloads": [item.download_url],
        }

        if item.file_size is not None:
            entry["fileSize"] = item.file_size
        if item.env:
            entry["env"] = item.env

        files.append(entry)

    alvo_loader = (loader or pack.minecraft.loader).lower()
    loader_key = LOADER_DEPENDENCY.get(alvo_loader, alvo_loader)

    version_id = f"{pack.version}+mc{minecraft_version}"
    if alvo_loader != pack.minecraft.loader:
        version_id += f"+{alvo_loader}"

    return {
        "formatVersion": 1,
        "game": "minecraft",
        "versionId": version_id,
        "name": pack.name,
        "summary": pack.summary or "",
        "files": files,
        "dependencies": {
            "minecraft": minecraft_version,
            loader_key: loader_version or pack.minecraft.loader_version,
        },
    }


def build_mrpack(source: Path, index: dict, destination: Path) -> Path:
    """Gera o `.mrpack`: índice novo + o `overrides/` do pack de origem.

    A escrita é atômica (`.part` + `os.replace`), como no `.zip` do conversor.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)

    with zipfile.ZipFile(source) as origem, zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as novo:
        novo.writestr(
            MODRINTH_INDEX_FILE, json.dumps(index, indent=2, ensure_ascii=False)
        )

        for info in origem.infolist():
            if info.is_dir() or info.filename == MODRINTH_INDEX_FILE:
                continue

            if info.filename.startswith(("overrides/", "client-overrides/",
                                         "server-overrides/")):
                novo.writestr(info, origem.read(info.filename))

    for tentativa in range(5):
        try:
            os.replace(temporary, destination)
            return destination
        except OSError:
            time.sleep(0.4 * (tentativa + 1))

    temporary.unlink(missing_ok=True)
    raise Mrpack2CurseForgeError(
        f"Não foi possível gravar {destination.name}: o arquivo está em uso."
    )
