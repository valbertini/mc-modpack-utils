"""Construção do `manifest.json` e do `modlist.html` do CurseForge."""

from html import escape

from mrpack2curseforge.constants import (
    CURSEFORGE_MANIFEST_TYPE,
    CURSEFORGE_MANIFEST_VERSION,
    DEFAULT_OVERRIDES,
)
from mrpack2curseforge.domain import MatchResult, Modpack


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
                    "required": True,
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
            "files": files,
            "overrides": DEFAULT_OVERRIDES,
        }

    def build_modlist(self, results: list[MatchResult]) -> str:
        lines = ["<ul>"]

        for result in sorted(
            results, key=lambda r: (r.project_name or r.mod.file_name).lower()
        ):
            if result.matched and result.project_slug:
                url = (
                    "https://www.curseforge.com/minecraft/mc-mods/"
                    f"{result.project_slug}"
                )
                name = escape(result.project_name or result.project_slug)
                lines.append(f'<li><a href="{url}">{name}</a></li>')
            elif result.matched:
                lines.append(f"<li>{escape(result.project_name or '')}</li>")
            else:
                lines.append(
                    f"<li>{escape(result.mod.clean_file_name)} (overrides)</li>"
                )

        lines.append("</ul>")
        return "\n".join(lines)
