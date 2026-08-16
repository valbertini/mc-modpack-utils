"""Configurações, cache e desligamento — o que é do app, não dos packs."""

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from mrpack2curseforge import settings
from mrpack2curseforge.config import Config
from mrpack2curseforge.services.cache import cache_stats, clear_cache
from mrpack2curseforge.web.context import AppContext
from mrpack2curseforge.web.payloads import clear_pack_meta
from mrpack2curseforge.web.schemas import SettingsRequest


def router(ctx: AppContext) -> APIRouter:
    api = APIRouter()

    @api.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        """Os campos do `.env`, com a chave da API já mascarada."""

        aberto = ctx.open_job()

        return {
            **settings.state(),
            # a interface desabilita os campos em vez de deixar você digitar
            # para levar 409 no fim
            "locked_by": aberto.source.name if aberto else None,
        }

    def settings_livre() -> None:
        """Recusa mexer nas configurações com trabalho aberto.

        Metade delas (workers, timeout, limites de página) é lida enquanto o
        trabalho roda: trocar no meio daria um resultado que não corresponde
        nem ao valor antigo nem ao novo.
        """

        aberto = ctx.open_job()

        if aberto is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Há um trabalho aberto ({aberto.source.name}). "
                    "Feche-o antes de mexer nas configurações."
                ),
            )

    @api.put("/api/settings")
    def put_settings(payload: SettingsRequest) -> dict[str, Any]:
        settings_livre()
        resultado = settings.write(payload.values)

        if not resultado["ok"]:
            raise HTTPException(status_code=400, detail="; ".join(resultado["errors"]))

        # pastas e cache são resolvidos na importação: só valem no próximo boot
        resultado["restart_needed"] = sorted(
            set(payload.values) & settings.NEEDS_RESTART
        )
        resultado["state"] = settings.state()
        return resultado

    @api.post("/api/settings/reset")
    def reset_settings() -> dict[str, Any]:
        """Volta ao padrão tudo o que a tela edita — menos a chave da API."""

        settings_livre()
        resultado = settings.reset_defaults()
        resultado["state"] = settings.state()
        return resultado

    @api.post("/api/settings/forget-key")
    def forget_key() -> dict[str, Any]:
        """Apaga só a chave da API; o resto das configurações fica como está."""

        settings_livre()
        resultado = settings.forget_secrets()
        resultado["state"] = settings.state()
        return resultado

    # ------------------------------------------------------------------- cache
    @api.get("/api/cache")
    def cache_info() -> dict[str, Any]:
        return cache_stats(Config.CACHE_PATH)

    @api.delete("/api/cache")
    def wipe_cache() -> dict[str, Any]:
        """Apaga o cache das consultas ao Modrinth e ao CurseForge.

        Fecha antes o cliente compartilhado do CurseForge: no Windows um arquivo
        SQLite aberto não pode ser removido.
        """

        ctx.close_curseforge()
        clear_pack_meta()

        return clear_cache(Config.CACHE_PATH)

    @api.post("/api/shutdown")
    def shutdown(request: Request) -> dict[str, Any]:
        """Encerra o servidor (o botão "Encerrar" da interface).

        Um trabalho em andamento é cancelado antes: as threads são `daemon`,
        então sair no meio de um download deixaria arquivos `.part` para trás.
        """

        abertos = [job for job in ctx.jobs.jobs.values() if job.busy]

        for job in abertos:
            ctx.jobs.cancel(job)

        servidor = getattr(request.app.state, "server", None)
        if servidor is None:
            raise HTTPException(
                status_code=501,
                detail="Este servidor não foi iniciado pelo comando `web`.",
            )

        servidor.should_exit = True
        return {"cancelled": [job.source.name for job in abertos]}

    return api
