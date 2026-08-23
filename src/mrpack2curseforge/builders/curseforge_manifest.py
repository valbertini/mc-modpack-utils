"""Construção do `manifest.json` e do `modlist.html` do CurseForge."""

from html import escape

from mrpack2curseforge.constants import (
    CURSEFORGE_MANIFEST_TYPE,
    CURSEFORGE_MANIFEST_VERSION,
    CURSEFORGE_SECTIONS,
    DEFAULT_OVERRIDES,
    DEFAULT_SECTION,
)
from mrpack2curseforge.domain import MatchResult, Modpack


def project_url(result: MatchResult) -> str | None:
    """Endereço do projeto no site. A seção não é a mesma para todo tipo.

    Resourcepack mora em `/texture-packs/`, shader em `/shaders/`; só mod fica
    em `/mc-mods/`, que era o que o modlist.html usava para todo mundo.
    """

    if not result.project_slug:
        return None

    section = CURSEFORGE_SECTIONS.get(result.mod.folder, DEFAULT_SECTION)
    return f"https://www.curseforge.com/minecraft/{section}/{result.project_slug}"


class CurseForgeManifestBuilder:
    """Converte o domínio + resultados do matcher no manifest do CurseForge.

    Não faz rede: recebe tudo pronto.
    """

    def build(
        self, pack: Modpack, results: list[MatchResult], author: str = ""
    ) -> dict:
        files = []
        seen: set[tuple[int, int]] = set()

        for result in results:
            if not result.matched:
                continue

            key = (result.project_id, result.file_id)
            if key in seen:
                continue
            seen.add(key)

            files.append(
                {
                    "projectID": result.project_id,
                    "fileID": result.file_id,
                    # mod desligado no pack de origem vira entrada opcional: é
                    # assim que o launcher do CurseForge o instala `.disabled`
                    "required": not result.mod.disabled,
                    # o export do CurseForge sempre grava o campo; o launcher o
                    # usa para saber se pode trocar a versão sozinho
                    "isLocked": False,
                }
            )

        return {
            "minecraft": {
                "version": pack.minecraft.version,
                "modLoaders": [
                    {
                        "id": pack.loader_id,
                        "primary": True,
                    }
                ],
            },
            "manifestType": CURSEFORGE_MANIFEST_TYPE,
            "manifestVersion": CURSEFORGE_MANIFEST_VERSION,
            "name": pack.name,
            "version": pack.version,
            "author": author,
            "overrides": DEFAULT_OVERRIDES,
            "files": files,
        }

    def build_modlist(self, results: list[MatchResult]) -> str:
        lines = ["<ul>"]

        for result in sorted(
            results, key=lambda r: (r.project_name or r.mod.file_name).lower()
        ):
            url = project_url(result)
            name = escape(result.project_name or result.project_slug or "")

            # o CurseForge assina cada linha com o autor; sem ele a lista de um
            # pack grande vira um monte de nome parecido sem dono
            if result.project_author:
                name += f" (by {escape(result.project_author)})"

            if result.matched and url:
                lines.append(f'<li><a href="{url}">{name}</a></li>')
            elif result.matched:
                lines.append(f"<li>{name}</li>")
            else:
                lines.append(
                    f"<li>{escape(result.mod.clean_file_name)} (overrides)</li>"
                )

        lines.append("</ul>")
        return "\n".join(lines)
