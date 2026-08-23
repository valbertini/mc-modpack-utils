"""Modelos internos do domínio.

O parser converte o `.mrpack` para cá e o builder consome daqui — nenhum dos
dois fala com o outro. O que o domínio sabe do CurseForge é só o vocabulário
(que pastas têm seção equivalente lá, e o nome dela na URL); heurística nenhuma,
que essa mora inteira em `services/matcher.py`.
"""

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from mrpack2curseforge.constants import CURSEFORGE_CLASSES, DEFAULT_SECTION


class MinecraftInfo(BaseModel):
    version: str
    loader: str
    loader_version: str


class PackFile(BaseModel):
    """Um arquivo listado no índice do modpack (mod, resourcepack, shader...)."""

    file_name: str
    file_path: str  # caminho relativo dentro do pack (ex: "mods/sodium.jar")
    download_url: str | None = None
    sha1: str | None = None
    sha512: str | None = None
    file_size: int | None = None
    # `env` do índice do Modrinth (client/server), preservado ao atualizar o pack
    env: dict[str, str] | None = None
    # o arquivo já vinha dentro do `overrides/` do mrpack, não do índice
    from_overrides: bool = False

    @property
    def folder(self) -> str:
        """Primeira pasta do caminho: `mods`, `resourcepacks`, `shaderpacks`...

        É ela que decide em que seção do CurseForge procurar o arquivo.
        """

        head, _, tail = self.file_path.partition("/")
        return head if tail else ""

    @property
    def is_mod(self) -> bool:
        return self.folder == "mods"

    @property
    def disabled(self) -> bool:
        """Mod desligado no pack de origem (vira entrada opcional no manifest)."""
        return self.file_name.endswith(".disabled")

    @property
    def override_path(self) -> str:
        """Onde o arquivo fica quando vai para `overrides/`."""
        return f"{self.folder}/{self.file_name}" if self.folder else self.file_name

    @property
    def clean_file_name(self) -> str:
        """Nome do arquivo sem o sufixo `.disabled`."""
        return self.file_name.removesuffix(".disabled")


class ModrinthProject(BaseModel):
    """Metadados do projeto obtidos na API do Modrinth."""

    project_id: str
    slug: str | None = None
    title: str | None = None
    version_number: str | None = None
    icon_url: str | None = None

    @property
    def url(self) -> str | None:
        return f"https://modrinth.com/mod/{self.slug}" if self.slug else None


class MatchStrategy(str, Enum):
    """Como o mod foi localizado no CurseForge (ordem de tentativa)."""

    MODRINTH_SLUG = "modrinth-slug"
    MODRINTH_TITLE = "modrinth-title"
    # outra grafia do mesmo nome: "Extended AE" -> "ExtendedAE"
    MODRINTH_VARIANT = "modrinth-variant"
    # nome + loader do modpack: "Things" -> "Things fabric"
    MODRINTH_LOADER = "modrinth-loader"
    FILENAME_REGEX = "filename-regex"
    FILENAME_SIMPLE = "filename-simple"
    # escolhido à mão pelo usuário na interface web
    MANUAL = "manual"
    UNMATCHED = "unmatched"


class MissingReason(str, Enum):
    """Por que um mod não virou entrada no manifest."""

    # o projeto existe no CurseForge, mas essa versão específica não está lá
    VERSION_UNAVAILABLE = "version-unavailable"
    # nenhum projeto parecido o suficiente foi encontrado
    NOT_ON_CURSEFORGE = "not-on-curseforge"
    # não foi possível diagnosticar (erro de rede, download, etc.)
    UNKNOWN = "unknown"


class Diagnosis(BaseModel):
    """Resultado da investigação feita quando o arquivo exato não foi achado.

    Compara os nomes dos arquivos mais recentes do projeto no Modrinth com os
    arquivos mais recentes dos candidatos do CurseForge.
    """

    reason: MissingReason = MissingReason.NOT_ON_CURSEFORGE
    similarity: float = 0.0
    project_id: int | None = None
    project_name: str | None = None
    project_slug: str | None = None
    closest_file_id: int | None = None
    closest_file_name: str | None = None
    # seção do site onde o projeto vive (`mc-mods`, `texture-packs`, `shaders`)
    section: str = DEFAULT_SECTION
    # qual arquivo do Modrinth produziu a maior similaridade
    matched_reference: str | None = None
    modrinth_files_checked: int = 0

    @property
    def curseforge_url(self) -> str | None:
        if not self.project_slug:
            return None
        return (
            f"https://www.curseforge.com/minecraft/{self.section}/{self.project_slug}"
        )


class MatchResult(BaseModel):
    mod: PackFile
    strategy: MatchStrategy = MatchStrategy.UNMATCHED
    project_id: int | None = None
    file_id: int | None = None
    project_name: str | None = None
    project_slug: str | None = None
    project_author: str | None = None
    modrinth: ModrinthProject | None = None
    queries_tried: list[str] = Field(default_factory=list)
    diagnosis: Diagnosis | None = None
    error: str | None = None

    @property
    def matched(self) -> bool:
        return self.project_id is not None and self.file_id is not None

    @property
    def status(self) -> str:
        """Situação final do mod, usada no relatório e no registro."""

        if self.matched:
            return "curseforge"
        if self.error:
            return "failed"
        if self.diagnosis:
            return self.diagnosis.reason.value

        return MissingReason.UNKNOWN.value


class UpdateStatus(str, Enum):
    """O que aconteceu com cada arquivo do pack ao atualizar."""

    UPDATED = "updated"            # veio versão mais nova
    UNCHANGED = "unchanged"        # já estava na mais recente
    INCOMPATIBLE = "incompatible"  # o projeto não publicou nada para o alvo
    UNKNOWN = "unknown"            # não identificado no Modrinth
    MANUAL = "manual"              # versão escolhida à mão pelo usuário


class UpdateResult(BaseModel):
    mod: PackFile
    status: UpdateStatus
    modrinth: ModrinthProject | None = None
    from_version: str | None = None
    to_version: str | None = None
    version_type: str | None = None
    new_file: PackFile | None = None
    # o usuário decidiu manter a versão atual mesmo havendo uma nova
    skipped: bool = False
    # o arquivo não entra no pack novo
    excluded: bool = False

    # o que a análise automática tinha decidido, para desfazer escolhas manuais
    auto_status: UpdateStatus | None = None
    auto_new_file: PackFile | None = None
    auto_to_version: str | None = None
    auto_version_type: str | None = None
    auto_modrinth: ModrinthProject | None = None

    def remember_auto(self) -> "UpdateResult":
        """Guarda o resultado automático antes de qualquer escolha manual."""

        self.auto_status = self.status
        self.auto_new_file = self.new_file
        self.auto_to_version = self.to_version
        self.auto_version_type = self.version_type
        # o projeto também: dá para escolher a versão de outro projeto
        self.auto_modrinth = self.modrinth
        return self

    def restore_auto(self) -> None:
        self.status = self.auto_status or UpdateStatus.UNKNOWN
        self.new_file = self.auto_new_file
        self.to_version = self.auto_to_version
        self.version_type = self.auto_version_type
        self.modrinth = self.auto_modrinth

    @property
    def has_version(self) -> bool:
        """Existe versão para o Minecraft alvo (automática ou escolhida)."""
        return self.status not in (UpdateStatus.INCOMPATIBLE, UpdateStatus.UNKNOWN)

    @property
    def changes(self) -> bool:
        """Vai mesmo trocar de arquivo quando o pack for gerado."""
        return self.new_file is not None and not self.skipped

    @property
    def final_file(self) -> PackFile:
        return self.new_file if self.changes else self.mod


class Modpack(BaseModel):
    name: str
    version: str
    summary: str | None = None
    minecraft: MinecraftInfo

    mods: list[PackFile] = Field(default_factory=list)
    extra_files: list[PackFile] = Field(default_factory=list)
    override_paths: list[Path] = Field(default_factory=list)
    # quanto `overrides/` ocupa dentro do mrpack (comprimido), para estimar o zip
    override_bytes: int = 0
    # arquivos que já vinham em `overrides/` e podem existir no CurseForge
    override_candidates: list[PackFile] = Field(default_factory=list)

    @property
    def convertible(self) -> list[PackFile]:
        """Arquivos que o matcher tenta encontrar no CurseForge.

        Só as pastas com seção equivalente lá (mods, resourcepacks, shaderpacks):
        um `config/x.json` do índice continua indo direto para `overrides/`.
        """

        todos = [*self.mods, *self.extra_files, *self.override_candidates]
        return [f for f in todos if f.folder in CURSEFORGE_CLASSES]

    @property
    def plain_extras(self) -> list[PackFile]:
        """Arquivos do índice que nem chegam a ser procurados no CurseForge.

        `config/`, `datapacks/` e afins: vão direto para `overrides/`.
        """

        return [f for f in self.extra_files if f.folder not in CURSEFORGE_CLASSES]

    @property
    def loader_id(self) -> str:
        """Id do loader no formato esperado pelo manifest do CurseForge."""
        return f"{self.minecraft.loader}-{self.minecraft.loader_version}"
