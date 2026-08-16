"""Schemas do formato `.mrpack` (modrinth.index.json).

Propositalmente tolerantes: packs gerados por ferramentas diferentes costumam
omitir campos opcionais.
"""

from pydantic import BaseModel, Field


class ModrinthHashes(BaseModel):
    sha1: str | None = None
    sha512: str | None = None


class ModrinthFile(BaseModel):
    path: str
    hashes: ModrinthHashes = Field(default_factory=ModrinthHashes)
    downloads: list[str] = Field(default_factory=list)
    fileSize: int | None = None
    # client/server: preservado quando o pack é atualizado
    env: dict[str, str] | None = None


class ModrinthIndex(BaseModel):
    formatVersion: int = 1
    game: str = "minecraft"
    versionId: str = "1.0.0"
    name: str = "Modpack"
    summary: str | None = None
    files: list[ModrinthFile] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)
