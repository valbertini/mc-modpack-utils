"""Início, revisão e encerramento das conversões e atualizações."""

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from mrpack2curseforge.converter import Resolution
from mrpack2curseforge.updater import ManualPick, UpdateDecisions
from mrpack2curseforge.web.context import AppContext
from mrpack2curseforge.web.schemas import (
    ConvertRequest,
    ResolutionsRequest,
    UpdateRequest,
    UpdateResolutionsRequest,
)


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.post("/api/convert")
    def convert(payload: ConvertRequest) -> dict[str, Any]:
        path = ctx.input_pack(payload.file)
        ctx.require_api_key()
        ctx.require_free("conversion", "conversão")

        job = ctx.jobs.start_conversion(path, workers=payload.workers)
        return job.snapshot()

    @api.post("/api/update")
    def update(payload: UpdateRequest) -> dict[str, Any]:
        path = ctx.input_pack(payload.file)

        if not payload.minecraft.strip():
            raise HTTPException(status_code=400, detail="Escolha a versão do Minecraft")

        ctx.require_free("update", "atualização")

        job = ctx.jobs.start_update(
            path,
            payload.minecraft.strip(),
            payload.loader_version or None,
            payload.workers,
            (payload.loader or "").strip().lower() or None,
        )
        return job.snapshot()

    @api.put("/api/jobs/{job_id}/update-resolutions")
    def save_update_resolutions(
        job_id: str, payload: UpdateResolutionsRequest
    ) -> dict[str, Any]:
        job = ctx.require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        conhecidos = {result.mod.file_path for result in job.outcome.results}
        desconhecidos = [
            item.file_path
            for item in payload.choices
            if item.file_path not in conhecidos
        ]

        if desconhecidos:
            raise HTTPException(
                status_code=404, detail=f"Fora do pack: {', '.join(desconhecidos)}"
            )

        def validos(caminhos: list[str]) -> set[str]:
            return {caminho for caminho in caminhos if caminho in conhecidos}

        job.decisions = UpdateDecisions(
            versions={
                item.file_path: ManualPick(
                    version_id=item.version_id,
                    version_number=item.version_number,
                    file_name=item.file_name,
                    project_id=item.project_id,
                    project_title=item.project_title,
                )
                for item in payload.choices
            },
            keep=validos(payload.keep),
            exclude=validos(payload.exclude),
            include=validos(payload.include),
        )
        job.dirty = True

        return {
            "versions": len(job.decisions.versions),
            "keep": len(job.decisions.keep),
            "exclude": len(job.decisions.exclude),
            "include": len(job.decisions.include),
        }

    @api.post("/api/jobs/{job_id}/reapply")
    def reapply_update(job_id: str) -> dict[str, Any]:
        job = ctx.require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        if job.busy:
            raise HTTPException(status_code=409, detail="Trabalho ocupado")

        # cancelada ou com erro não volta a gerar: só revisão pendente ou
        # regeração de uma que já deu certo
        if job.status not in ("awaiting_review", "done"):
            raise HTTPException(
                status_code=409,
                detail=f"Atualização em estado '{job.status}': não dá para aplicar",
            )

        ctx.jobs.start_update_reapply(job)
        return job.snapshot()

    @api.post("/api/jobs/{job_id}/to-input")
    def send_to_input(job_id: str) -> dict[str, Any]:
        """Copia o pack atualizado para `input_modpacks/`, pronto para converter."""

        job = ctx.require_job(job_id)

        if job.kind != "update" or job.outcome is None:
            raise HTTPException(status_code=400, detail="Isso não é uma atualização")

        if not job.outcome.output.is_file():
            raise HTTPException(status_code=404, detail="O .mrpack não está mais lá")

        return ctx.copy_to_input(job.outcome.output)

    @api.get("/api/jobs/{job_id}")
    def get_job(job_id: str, log_offset: int = 0) -> dict[str, Any]:
        return ctx.require_job(job_id).snapshot(log_offset=log_offset)

    @api.get("/api/jobs/{job_id}/conflicts")
    def get_conflicts(job_id: str) -> dict[str, Any]:
        job = ctx.require_job(job_id)
        return {"conflicts": job.conflicts(), "status": job.status}

    @api.put("/api/jobs/{job_id}/resolutions")
    def save_resolutions(job_id: str, payload: ResolutionsRequest) -> dict[str, Any]:
        """Substitui todas as escolhas manuais do job de uma vez."""

        job = ctx.require_job(job_id)

        results = job.outcome.results if job.outcome else []
        known = {result.mod.file_name for result in results}

        unknown = [r.file_name for r in payload.resolutions if r.file_name not in known]
        if unknown:
            raise HTTPException(
                status_code=404, detail=f"Mods fora do job: {', '.join(unknown)}"
            )

        job.dirty = True
        job.resolutions = {
            item.file_name: Resolution(
                project_id=item.project_id,
                file_id=item.file_id,
                project_name=item.project_name,
                project_slug=item.project_slug,
                file_name=item.curseforge_file_name,
            )
            for item in payload.resolutions
        }

        return {"conflicts": job.conflicts(), "saved": len(job.resolutions)}

    @api.post("/api/jobs/{job_id}/apply")
    def apply_changes(job_id: str) -> dict[str, Any]:
        """Segue com os downloads e a geração do `.zip`."""

        job = ctx.require_job(job_id)

        if job.outcome is None:
            raise HTTPException(status_code=400, detail="A análise ainda não terminou")

        if job.busy:
            raise HTTPException(status_code=409, detail="Conversão ocupada")

        if job.status not in ("awaiting_conflicts", "done"):
            raise HTTPException(
                status_code=409,
                detail=f"Conversão em estado '{job.status}': não dá para aplicar",
            )

        ctx.jobs.start_finish(job)
        return job.snapshot()

    @api.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        job = ctx.require_job(job_id)
        ctx.jobs.cancel(job)
        return job.snapshot()

    @api.post("/api/jobs/{job_id}/close")
    def close_job(job_id: str) -> dict[str, bool]:
        """Fecha a conversão e libera a vaga para a próxima."""

        ctx.jobs.close(ctx.require_job(job_id))
        return {"closed": True}

    @api.get("/api/jobs/{job_id}/download")
    def download(job_id: str) -> FileResponse:
        job = ctx.require_job(job_id)

        if job.outcome is None or not job.outcome.output.exists():
            raise HTTPException(status_code=404, detail="Modpack ainda não foi gerado")

        return FileResponse(
            job.outcome.output,
            media_type="application/zip",
            filename=job.outcome.output.name,
        )

    @api.get("/api/jobs/{job_id}/report")
    def download_report(job_id: str) -> FileResponse:
        """Registro em JSON, decisão a decisão.

        Serve as duas ferramentas: a conversão guarda em `record_path`, a
        atualização em `report_path`.
        """

        job = ctx.require_job(job_id)
        caminho = None

        if job.outcome is not None:
            caminho = getattr(job.outcome, "record_path", None) or getattr(
                job.outcome, "report_path", None
            )

        if caminho is None or not caminho.exists():
            raise HTTPException(status_code=404, detail="Registro indisponível")

        return FileResponse(
            caminho, media_type="application/json", filename=caminho.name
        )

    return api
