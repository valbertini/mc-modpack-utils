"""Conversões salvas: reler o registro e regerar o `.zip` sem a API."""

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from mrpack2curseforge.records import delete_record, list_records, load_record
from mrpack2curseforge.web.context import AppContext


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.get("/api/records")
    def get_records() -> dict[str, Any]:
        return {"records": list_records(ctx.output_dir, ctx.input_dir)}

    @api.get("/api/records/{record_id}")
    def get_record(record_id: str) -> dict[str, Any]:
        record = load_record(ctx.output_dir, record_id)

        if not record:
            raise HTTPException(status_code=404, detail="Conversão não encontrada")

        source = ctx.input_dir / Path(record.get("source") or "").name
        record["source_available"] = source.is_file()

        return record

    @api.post("/api/records/{record_id}/generate")
    def generate_from_record(record_id: str) -> dict[str, Any]:
        """Regera o `.zip` a partir do registro (sem consultar o CurseForge)."""

        record = load_record(ctx.output_dir, record_id)

        if not record:
            raise HTTPException(status_code=404, detail="Conversão não encontrada")

        source = ctx.input_dir / Path(record.get("source") or "").name

        if not source.is_file():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"O arquivo de origem '{record.get('source')}' não está mais em "
                    "input_modpacks/ — sem ele não dá para remontar o modpack."
                ),
            )

        if ctx.jobs.current("conversion") is not None:
            raise HTTPException(
                status_code=409,
                detail="Feche a conversão aberta antes de gerar outro modpack.",
            )

        return ctx.jobs.start_rebuild(record, source).snapshot()

    @api.delete("/api/records/{record_id}")
    def remove_record(record_id: str) -> dict[str, bool]:
        if delete_record(ctx.output_dir, record_id):
            return {"deleted": True}

        raise HTTPException(status_code=404, detail="Conversão não encontrada")

    return api
