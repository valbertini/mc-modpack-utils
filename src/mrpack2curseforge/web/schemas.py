"""Corpos das requisições que a interface envia."""

from pydantic import BaseModel


class SettingsRequest(BaseModel):
    """Só as chaves que a tela conhece são gravadas (o resto é ignorado)."""

    values: dict[str, str] = {}


class ConvertRequest(BaseModel):
    file: str
    workers: int | None = None


class ResolveRequest(BaseModel):
    file_name: str
    project_id: int
    file_id: int
    project_name: str | None = None
    project_slug: str | None = None
    curseforge_file_name: str | None = None


class UpdateRequest(BaseModel):
    file: str
    minecraft: str
    loader_version: str | None = None
    # trocar de modloader (fabric -> neoforge…); vazio mantém o do pack
    loader: str | None = None
    workers: int | None = None


class UpdateChoice(BaseModel):
    file_path: str
    version_id: str
    # só para a interface mostrar a escolha sem reconsultar a API
    version_number: str | None = None
    file_name: str | None = None
    project_id: str | None = None
    project_title: str | None = None


class UpdateResolutionsRequest(BaseModel):
    """Decisões da revisão da atualização."""

    # versões escolhidas à mão
    choices: list[UpdateChoice] = []
    # manter a versão atual, mesmo havendo uma nova
    keep: list[str] = []
    # não levar o arquivo para o pack novo
    exclude: list[str] = []
    # levar mesmo sem versão para o alvo
    include: list[str] = []


class ResolutionsRequest(BaseModel):
    """Salva de uma vez todas as escolhas feitas na aba de conflitos."""

    resolutions: list[ResolveRequest] = []
