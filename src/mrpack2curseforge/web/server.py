"""Servidor web local (FastAPI).

Tudo é servido de `127.0.0.1`: nenhum CDN, nenhuma fonte externa, nenhuma
telemetria. As únicas chamadas externas são as APIs do Modrinth e do CurseForge
feitas pelo próprio conversor.

As rotas moram em `web/routes/`, um módulo por assunto. Cada um expõe
`router(ctx)` e devolve um `APIRouter`: o contexto entra por fecho em vez de um
global de módulo porque os testes criam várias aplicações lado a lado, cada uma
com as suas pastas.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from contextlib import asynccontextmanager

from mrpack2curseforge.config import Config
from mrpack2curseforge.web.context import AppContext
from mrpack2curseforge.web.routes import (
    catalog,
    jobs,
    packs,
    records,
    system,
    updates,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# A aplicação é servida inteiramente da máquina local: HTML, CSS e JS saem daqui,
# nunca de um CDN (a página abre mesmo sem internet). Imagens externas são
# permitidas porque os ícones dos projetos do CurseForge ajudam muito na hora de
# escolher entre mods de nome parecido — se estiverem offline, degradam sozinhos.
CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "form-action 'self'; "
    "base-uri 'self'"
)


class SecurityHeadersMiddleware:
    """Adiciona os cabeçalhos de segurança sem tocar no corpo da resposta.

    Isto é um middleware ASGI puro de propósito. Com `BaseHTTPMiddleware`
    (o decorator `@app.middleware("http")`), o corpo é reencaminhado pedaço a
    pedaço pelo middleware; se o `.zip` mudar de tamanho enquanto está sendo
    baixado — regenerar o modpack durante um download, por exemplo — o h11
    aborta com "Too much data for declared Content-Length". Aqui só o cabeçalho
    é alterado; o arquivo é transmitido direto pelo `FileResponse`.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"

            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_app(
    input_dir: Path | None = None, output_dir: Path | None = None
) -> FastAPI:
    ctx = AppContext(
        input_dir=Path(input_dir or Config.INPUT_DIR),
        output_dir=Path(output_dir or Config.OUTPUT_DIR),
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        ctx.shutdown()

    app = FastAPI(
        title="mrpack2curseforge", docs_url=None, redoc_url=None, lifespan=lifespan
    )

    # exposto para testes e depuração
    app.state.ctx = ctx
    app.state.jobs = ctx.jobs
    app.state.input_dir = ctx.input_dir
    app.state.output_dir = ctx.output_dir

    app.add_middleware(SecurityHeadersMiddleware)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico")
    def favicon() -> FileResponse:
        # 204 não pode ter corpo: `JSONResponse(status_code=204, content=None)`
        # serializava "null" e o h11 abortava com
        # "Too much data for declared Content-Length" a cada carregamento da página
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    for modulo in (packs, jobs, updates, records, catalog, system):
        app.include_router(modulo.router(ctx))

    return app


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    input_dir: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    import uvicorn

    app = create_app(input_dir=input_dir, output_dir=output_dir)
    server = uvicorn.Server(
        uvicorn.Config(app, host=host, port=port, log_level="warning")
    )

    # o botão "Encerrar" da interface precisa de quem desligar: guardar o
    # servidor aqui é o único jeito de sair limpo (no Windows, mandar sinal
    # para o próprio processo não encerra o uvicorn de forma confiável)
    app.state.server = server

    server.run()
