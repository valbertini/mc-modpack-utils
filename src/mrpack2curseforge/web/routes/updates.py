"""Packs já atualizados que estão na pasta de saída."""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from mrpack2curseforge.web.context import AppContext


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.get("/api/updates")
    def list_updates() -> dict[str, Any]:
        """Packs atualizados que estão na pasta de saída, com o que foi decidido."""

        atualizacoes = []

        for report in sorted(ctx.output_dir.glob("*-update.json")):
            try:
                dados = json.loads(report.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue

            arquivo = ctx.output_dir / (dados.get("output") or "")

            atualizacoes.append(
                {
                    "name": arquivo.name,
                    "available": arquivo.is_file(),
                    "size_mb": (
                        round(arquivo.stat().st_size / (1024 * 1024), 1)
                        if arquivo.is_file()
                        else None
                    ),
                    "modified": (
                        arquivo.stat().st_mtime if arquivo.is_file() else None
                    ),
                    "pack": dados.get("pack", {}),
                    "from_minecraft": dados.get("from_minecraft"),
                    "to_minecraft": dados.get("to_minecraft"),
                    "loader": dados.get("loader"),
                    # sem isto a lista não mostra a troca de loader, só a de MC
                    "from_loader": dados.get("from_loader"),
                    "summary": dados.get("summary", {}),
                }
            )

        atualizacoes.sort(key=lambda item: item["modified"] or 0, reverse=True)
        return {"updates": atualizacoes}

    @api.get("/api/updates/{name}")
    def get_update(name: str) -> dict[str, Any]:
        report = ctx.output_dir / f"{Path(name).stem}-update.json"

        if not report.is_file():
            raise HTTPException(status_code=404, detail="Atualização não encontrada")

        dados = json.loads(report.read_text(encoding="utf-8"))
        dados["available"] = (ctx.output_dir / Path(name).name).is_file()
        return dados

    @api.get("/api/updates/{name}/download")
    def download_update(name: str) -> FileResponse:
        arquivo = ctx.saved_update(name)

        return FileResponse(
            arquivo, media_type="application/octet-stream", filename=arquivo.name
        )

    @api.post("/api/updates/{name}/to-input")
    def update_to_input(name: str) -> dict[str, Any]:
        return ctx.copy_to_input(ctx.saved_update(name))

    @api.delete("/api/updates/{name}")
    def delete_update(name: str) -> dict[str, bool]:
        alvo = ctx.output_dir / Path(name).name
        report = ctx.output_dir / f"{alvo.stem}-update.json"

        removidos = False

        for caminho in (alvo, report):
            if caminho.is_file():
                caminho.unlink()
                removidos = True

        if not removidos:
            raise HTTPException(status_code=404, detail="Atualização não encontrada")

        return {"deleted": True}

    return api
