"""Leitura do `.mrpack` e conversão para os modelos internos.

O parser não conhece o CurseForge.
"""

import zipfile
from pathlib import Path
from urllib.parse import unquote

from mrpack2curseforge.constants import CURSEFORGE_CLASSES, MODRINTH_INDEX_FILE
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

# Extensões que valem uma consulta ao CurseForge. `.rpo`, `.txt` e companhia são
# resourcepacks desligados pelo launcher: ninguém publica um arquivo com esse
# nome, então procurar por eles só gastaria requisição.
PACKABLE_SUFFIXES = (".jar", ".zip")


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

        entradas = self._parse_overrides()
        override_paths = [path for path, _ in entradas]
        tamanhos = {path.as_posix(): tamanho for path, tamanho in entradas}
        indexed = {f.override_path for f in mods + extras}

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
            override_paths=override_paths,
            override_bytes=sum(tamanhos.values()),
            override_candidates=self._override_candidates(
                override_paths, indexed, tamanhos
            ),
        )

    # -------------------------------------------------- candidatos de overrides
    @staticmethod
    def _override_candidates(
        paths: list[Path], indexed: set[str], sizes: dict[str, int]
    ) -> list[PackFile]:
        """Arquivos de `overrides/` que também podem existir no CurseForge.

        O export do CurseForge lista no manifest até os mods que o Modrinth não
        hospeda (e que por isso viajam dentro do `overrides/` do mrpack). Aqui
        eles viram candidatos: se o CurseForge tiver um arquivo com exatamente o
        mesmo nome, entram no manifest; senão ficam onde estavam, em silêncio.
        """

        candidates: list[PackFile] = []

        for path in paths:
            parts = path.parts
            if len(parts) != 2 or parts[0] not in CURSEFORGE_CLASSES:
                continue

            name = parts[1]
            if not name.removesuffix(".disabled").lower().endswith(PACKABLE_SUFFIXES):
                continue

            relative = f"{parts[0]}/{name}"
            if relative in indexed:
                continue

            candidates.append(
                PackFile(
                    file_name=name,
                    file_path=relative,
                    from_overrides=True,
                    file_size=sizes.get(relative),
                )
            )

        return candidates

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
    def _parse_overrides(self) -> list[tuple[Path, int]]:
        """Os arquivos de `overrides/` (e `client-overrides/`) e o peso de cada um.

        O tamanho é o **comprimido** (`compress_size`), e não o do arquivo
        aberto: é ele que diz quanto aquele arquivo ocupa dentro de um `.zip`, e
        é isso que a tela precisa para estimar o pacote final. Para `.jar` e
        `.zip` — já comprimidos — os dois valores são praticamente o mesmo; para
        os `config/*.json` a diferença é de várias vezes.
        """

        overrides: list[tuple[Path, int]] = []

        with zipfile.ZipFile(self.file) as z:
            for info in z.infolist():
                if info.is_dir():
                    continue

                for prefix in ("overrides/", "client-overrides/"):
                    if info.filename.startswith(prefix):
                        caminho = Path(info.filename[len(prefix):])
                        overrides.append((caminho, info.compress_size))
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
