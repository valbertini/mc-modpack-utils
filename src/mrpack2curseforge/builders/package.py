"""Empacotamento final: gera o `.zip` importável pelo CurseForge App."""

import os
import re
import time
import zipfile
from pathlib import Path

from mrpack2curseforge.exceptions import Mrpack2CurseForgeError


def safe_name(name: str) -> str:
    """Nome de arquivo seguro para Windows/Linux."""

    cleaned = re.sub(r"[^\w\-. ]+", "_", name, flags=re.UNICODE)
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._")
    return cleaned or "modpack"


def build_zip(work_dir: Path, destination: Path) -> Path:
    """Compacta `work_dir` (manifest.json + overrides/) em `destination`.

    A escrita é atômica: o zip é montado num arquivo temporário e só depois
    ocupa o nome final. Sobrescrever no lugar fazia um download em andamento
    ver o arquivo mudar de tamanho no meio do caminho.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)

    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in sorted(work_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(work_dir).as_posix())

    _move_into_place(temporary, destination)
    return destination


def _move_into_place(temporary: Path, destination: Path, attempts: int = 5) -> None:
    """Move o temporário para o nome final, tolerando o arquivo estar em uso.

    No Windows, um `.zip` aberto por um download em andamento não pode ser
    substituído; em vez de corromper o download, esperamos um pouco e, se ainda
    assim não der, falhamos com uma mensagem clara.
    """

    last_error: OSError | None = None

    for attempt in range(attempts):
        try:
            os.replace(temporary, destination)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))

    temporary.unlink(missing_ok=True)

    raise Mrpack2CurseForgeError(
        f"Não foi possível gravar {destination.name}: o arquivo está em uso "
        f"(um download em andamento?). Tente de novo em instantes. [{last_error}]"
    )
