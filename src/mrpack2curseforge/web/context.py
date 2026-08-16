"""O que as rotas compartilham: pastas, trabalhos e clientes das APIs.

Um contexto por aplicação, e não um objeto global de módulo: os testes criam
vários `create_app()` apontando para pastas temporárias diferentes, e um global
faria um vazar no outro.
"""

import shutil
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from mrpack2curseforge.config import Config
from mrpack2curseforge.services.cache import SimpleCache
from mrpack2curseforge.services.curseforge import CurseForgeClient
from mrpack2curseforge.services.modrinth import ModrinthClient
from mrpack2curseforge.web.jobs import Job, JobManager

SEM_CHAVE = "CURSEFORGE_API_KEY não configurada no arquivo .env"


@dataclass
class AppContext:
    input_dir: Path
    output_dir: Path
    jobs: JobManager = field(init=False)

    # cliente compartilhado, criado sob demanda: a chave só é exigida no uso
    _curseforge: CurseForgeClient | None = field(default=None, init=False)

    def __post_init__(self) -> None:
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.jobs = JobManager(output_dir=self.output_dir)

    # ------------------------------------------------------------- clientes
    def curseforge(self) -> CurseForgeClient:
        if self._curseforge is None:
            self.require_api_key()
            self._curseforge = CurseForgeClient(SimpleCache(Config.CACHE_PATH))
        return self._curseforge

    def close_curseforge(self) -> None:
        """Solta o SQLite: no Windows um arquivo aberto não pode ser apagado."""

        if self._curseforge is not None:
            self._curseforge.close()
            self._curseforge = None

    @contextmanager
    def modrinth(self):
        """Cliente do Modrinth com o cache fechado no fim.

        O cache precisa ser fechado junto: uma conexão SQLite deixada aberta
        trava o arquivo no Windows, e o "Limpar cache" não conseguia apagá-lo.
        """

        with SimpleCache(Config.CACHE_PATH) as cache, ModrinthClient(cache) as client:
            yield client

    def shutdown(self) -> None:
        """Fim da vida da aplicação: fecha o cliente e limpa o rascunho."""

        self.close_curseforge()

        work = self.output_dir / ".work"
        if work.exists():
            shutil.rmtree(work, ignore_errors=True)

    # -------------------------------------------------------------- guardas
    def require_api_key(self) -> None:
        if not Config.CURSEFORGE_API_KEY:
            raise HTTPException(status_code=400, detail=SEM_CHAVE)

    def input_pack(self, name: str) -> Path:
        """Um `.mrpack` da pasta de entrada.

        `Path(name).name` corta o caminho: o nome vem da URL e não pode escapar
        da pasta.
        """

        path = self.input_dir / Path(name).name

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Modpack não encontrado")

        return path

    def saved_update(self, name: str) -> Path:
        """O `.mrpack` de uma atualização salva, com a mesma proteção."""

        arquivo = self.output_dir / Path(name).name

        if not arquivo.is_file():
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")

        return arquivo

    def require_job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)

        if job is None:
            raise HTTPException(status_code=404, detail="Job não encontrado")

        return job

    def require_free(self, kind: str, rotulo: str) -> None:
        """Recusa abrir um segundo trabalho do mesmo tipo."""

        atual = self.jobs.current(kind)

        if atual is not None:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Já existe uma {rotulo} aberta ({atual.source.name}). "
                    "Feche-a antes de iniciar outra."
                ),
            )

    def open_job(self) -> Job | None:
        """Qualquer trabalho aberto, de qualquer uma das duas ferramentas."""

        return self.jobs.current("conversion") or self.jobs.current("update")

    # --------------------------------------------------------------- ações
    def copy_to_input(self, origem: Path) -> dict[str, Any]:
        destino = self.input_dir / origem.name
        shutil.copy2(origem, destino)
        return {"name": destino.name}
