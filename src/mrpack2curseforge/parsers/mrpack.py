"""Leitura do `.mrpack` e conversão para os modelos internos.

O parser não conhece o CurseForge.
"""

import zipfile
from pathlib import Path
from urllib.parse import unquote

from mrpack2curseforge.constants import MODRINTH_INDEX_FILE
from mrpack2curseforge.domain import MinecraftInfo, Modpack, PackFile
from mrpack2curseforge.exceptions import InvalidMrpackError
from mrpack2curseforge.schemas.modrinth import ModrinthFile, ModrinthIndex

# Ordem importa: `neoforge` precisa ser testado antes de `forge`.
LOADER_KEYS = (
    ("neoforge", "neoforge"),
    ("fabric-loader", "fabric"),
    ("quilt-loader", "quilt"),
    ("forge", "forge"),
)


class MrpackParser:
    def __init__(self, file: Path):
        self.file = Path(file)

    # ------------------------------------------------------------ validação
    def validate(self) -> None:
        if not self.file.exists():
            raise FileNotFoundError(self.file)

        if not zipfile.is_zipfile(self.file):
            raise InvalidMrpackError(f"{self.file.name} não é um ZIP válido")

        with zipfile.ZipFile(self.file) as z:
            if MODRINTH_INDEX_FILE not in z.namelist():
                raise InvalidMrpackError(
                    f"{self.file.name} não contém {MODRINTH_INDEX_FILE}"
                )

    # ------------------------------------------------------- índice Modrinth
    def read_index(self) -> ModrinthIndex:
        with zipfile.ZipFile(self.file) as z:
            with z.open(MODRINTH_INDEX_FILE) as f:
                return ModrinthIndex.model_validate_json(f.read())

    # ----------------------------------------------------------- domínio
    def parse(self) -> Modpack:
        index = self.read_index()

        minecraft_version = index.dependencies.get("minecraft")
        if not minecraft_version:
            raise InvalidMrpackError("Versão do Minecraft ausente no índice")

        loader, loader_version = self._detect_loader(index.dependencies)

        mods: list[PackFile] = []
        extras: list[PackFile] = []

        for pack_file in self._parse_files(index.files):
            (mods if pack_file.is_mod else extras).append(pack_file)

        return Modpack(
            name=index.name,
            version=index.versionId,
            summary=index.summary,
            minecraft=MinecraftInfo(
                version=minecraft_version,
                loader=loader,
                loader_version=loader_version,
            ),
            mods=mods,
            extra_files=extras,
            override_paths=self._parse_overrides(),
        )

    # ------------------------------------------------------------- arquivos
    def _parse_files(self, files: list[ModrinthFile]) -> list[PackFile]:
        parsed: list[PackFile] = []

        for f in files:
            path = f.path.replace("\\", "/").lstrip("/")
            if not path:
                continue

            download_url = f.downloads[0] if f.downloads else None

            parsed.append(
                PackFile(
                    file_name=unquote(path.split("/")[-1]),
                    file_path=path,
                    download_url=download_url,
                    sha1=f.hashes.sha1,
                    sha512=f.hashes.sha512,
                    file_size=f.fileSize,
                    env=f.env,
                )
            )

        return parsed

    # --------------------------------------------------------------- loader
    def _detect_loader(self, deps: dict[str, str]) -> tuple[str, str]:
        for key, loader_name in LOADER_KEYS:
            if key in deps:
                return loader_name, deps[key]

        raise InvalidMrpackError(
            f"Nenhum mod loader reconhecido em dependencies={list(deps)}"
        )

    # ------------------------------------------------------------ overrides
    def _parse_overrides(self) -> list[Path]:
        """Lista os arquivos dentro de `overrides/` (e `client-overrides/`)."""

        overrides: list[Path] = []

        with zipfile.ZipFile(self.file) as z:
            for name in z.namelist():
                if name.endswith("/"):
                    continue

                for prefix in ("overrides/", "client-overrides/"):
                    if name.startswith(prefix):
                        overrides.append(Path(name[len(prefix):]))
                        break

        return overrides

    # -------------------------------------------------------- extração raw
    def extract_overrides(self, destination: Path) -> int:
        """Extrai o conteúdo de `overrides/` do mrpack para `destination`.

        `client-overrides/` é mesclado no mesmo destino (o CurseForge não tem
        equivalente separado para client/server).

        Retorna a quantidade de arquivos extraídos.
        """

        destination.mkdir(parents=True, exist_ok=True)
        extracted = 0

        with zipfile.ZipFile(self.file) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue

                relative: str | None = None
                for prefix in ("overrides/", "client-overrides/"):
                    if info.filename.startswith(prefix):
                        relative = info.filename[len(prefix):]
                        break

                if not relative:
                    continue

                target = (destination / relative).resolve()

                # proteção contra zip-slip
                if not str(target).startswith(str(destination.resolve())):
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, open(target, "wb") as dst:
                    dst.write(src.read())

                extracted += 1

        return extracted
