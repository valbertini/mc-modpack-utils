"""Estado da tela, envio e leitura dos `.mrpack` de entrada."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile

from mrpack2curseforge import __version__
from mrpack2curseforge.builders.package import safe_name
from mrpack2curseforge.config import Config
from mrpack2curseforge.parsers.mrpack import MrpackParser
from mrpack2curseforge.records import list_records
from mrpack2curseforge.web.context import AppContext
from mrpack2curseforge.web.payloads import last_used, pack_meta


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.get("/api/state")
    def get_state(request: Request) -> dict[str, Any]:
        packs = []
        usados = last_used(ctx.output_dir)

        for path in sorted(ctx.input_dir.glob("*.mrpack")):
            stat = path.stat()
            packs.append(
                {
                    "name": path.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 1),
                    "modified": stat.st_mtime,
                    # quando este pack foi convertido/atualizado pela última vez
                    # (a interface põe os mais recentes no topo)
                    "last_used": usados.get(path.name),
                    # ler o índice é barato e fica em cache pelo mtime: sem isto
                    # a lista não diz para qual Minecraft/loader é cada pack
                    **pack_meta(path, stat.st_mtime, stat.st_size),
                }
            )

        current = ctx.jobs.current("conversion")
        current_update = ctx.jobs.current("update")

        def resumo(job):
            if not job:
                return None
            return {
                "id": job.id,
                "kind": job.kind,
                "source": job.source.name,
                "status": job.status,
            }

        return {
            # a página compara com a `<meta>` dela: se ficou para trás, avisa
            "version": __version__,
            "input_dir": str(ctx.input_dir),
            "output_dir": str(ctx.output_dir),
            "api_key_configured": bool(Config.CURSEFORGE_API_KEY),
            # rodando por fora do comando `web` não há servidor para desligar
            "can_quit": getattr(request.app.state, "server", None) is not None,
            "packs": packs,
            "records": list_records(ctx.output_dir, ctx.input_dir),
            "current_job": resumo(current),
            "current_update": resumo(current_update),
        }

    @api.post("/api/upload")
    async def upload(file: UploadFile) -> dict[str, Any]:
        original = Path(file.filename or "modpack.mrpack").name

        if not original.lower().endswith(".mrpack"):
            raise HTTPException(status_code=400, detail="Envie um arquivo .mrpack")

        stem = safe_name(original[: -len(".mrpack")])
        destination = ctx.input_dir / f"{stem}.mrpack"

        counter = 1
        while destination.exists():
            destination = ctx.input_dir / f"{stem} ({counter}).mrpack"
            counter += 1

        with open(destination, "wb") as handle:
            while chunk := await file.read(1 << 20):
                handle.write(chunk)

        try:
            MrpackParser(destination).validate()
        except Exception as exc:  # noqa: BLE001
            destination.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Arquivo inválido: {exc}")

        return {
            "name": destination.name,
            "size_mb": round(destination.stat().st_size / (1024 * 1024), 1),
        }

    @api.delete("/api/packs/{name}")
    def delete_pack(name: str) -> dict[str, Any]:
        """Apaga um `.mrpack` da entrada.

        O pack de um trabalho aberto fica: a conversão ainda vai lê-lo para
        montar o `overrides/`.
        """

        path = ctx.input_pack(name)

        for job in (ctx.jobs.current("conversion"), ctx.jobs.current("update")):
            if job is not None and job.source.name == path.name:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{path.name} está em um trabalho aberto. "
                        "Feche-o antes de apagar o arquivo."
                    ),
                )

        tamanho = path.stat().st_size
        path.unlink()

        return {"deleted": True, "freed_mb": round(tamanho / (1024 * 1024), 1)}

    @api.get("/api/packs/{name}/inspect")
    def inspect_pack(name: str) -> dict[str, Any]:
        path = ctx.input_pack(name)

        parser = MrpackParser(path)
        parser.validate()
        pack = parser.parse()

        extras: dict[str, int] = {}
        for extra in pack.extra_files:
            folder = extra.file_path.split("/")[0]
            extras[folder] = extras.get(folder, 0) + 1

        return {
            "file": path.name,
            "size_mb": round(path.stat().st_size / (1024 * 1024), 1),
            "name": pack.name,
            "version": pack.version,
            "summary": pack.summary,
            "minecraft": pack.minecraft.version,
            "loader": pack.loader_id,
            "mods": len(pack.mods),
            "extra_files": len(pack.extra_files),
            "extra_by_folder": extras,
            "override_files": len(pack.override_paths),
            "mod_files": [mod.file_name for mod in pack.mods],
        }

    @api.get("/api/packs/{name}/modrinth")
    def pack_modrinth(name: str) -> dict[str, Any]:
        """Nomes reais dos mods, consultados na API do Modrinth (em lote)."""

        parser = MrpackParser(ctx.input_pack(name))
        parser.validate()
        pack = parser.parse()

        with ctx.modrinth() as modrinth:
            resolved = modrinth.resolve_projects(pack.mods)

        mods = []
        for mod in pack.mods:
            project = resolved.get(mod.file_path)
            mods.append(
                {
                    "file_name": mod.file_name,
                    "title": project.title if project else None,
                    "slug": project.slug if project else None,
                    "version": project.version_number if project else None,
                    "url": (
                        f"https://modrinth.com/mod/{project.slug}"
                        if project and project.slug
                        else None
                    ),
                }
            )

        return {"mods": mods, "identified": sum(1 for m in mods if m["title"])}

    return api
