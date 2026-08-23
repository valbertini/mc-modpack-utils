"""A política de rede que os dois clientes de API compartilham.

CurseForge e Modrinth querem exatamente a mesma coisa: repetir quando a rede
falha, esperar o que o `429` mandar esperar, e desistir depois de N tentativas.
Estava escrita duas vezes, quase igual — e retentativa duplicada é retentativa
que diverge, sempre no cliente que ninguém olhou.

O que muda entre os dois fica nos parâmetros: quais status significam "isto não
existe" (o CurseForge responde `400` para id inválido, o Modrinth não) e o que
fazer quando as tentativas acabam (aqui é sempre `ApiError`; o Modrinth prefere
tratar como "sem dados" e captura).
"""

import time
from typing import Any

import httpx

from mrpack2curseforge.config import Config
from mrpack2curseforge.exceptions import ApiError

# "a API respondeu, e a resposta é *não existe*" — diferente de ter desistido,
# e é essa diferença que o `None` de antes não sabia contar
VAZIO: Any = object()

# o `Retry-After` vem do servidor e já veio absurdo; e o backoff não precisa
# passar de dez segundos para deixar a API respirar
MAX_RETRY_AFTER = 30
MAX_BACKOFF = 10


def fetch_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    empty_on: tuple[int, ...] = (404,),
    label: str = "",
    **kwargs: Any,
) -> Any:
    """JSON da resposta, ou `VAZIO` para os status listados em `empty_on`.

    Levanta `ApiError` quando as tentativas acabam.
    """

    motivo = "nenhuma tentativa"

    for attempt in range(max(1, Config.HTTP_RETRIES)):
        try:
            response = client.request(method, url, **kwargs)

            if response.status_code == 429:
                espera = float(response.headers.get("Retry-After", 2**attempt))
                motivo = "limite de requisições (429)"
                time.sleep(min(espera, MAX_RETRY_AFTER))
                continue

            if response.status_code in empty_on:
                return VAZIO

            # o 5xx cai no `except` abaixo e é retentado: erro de servidor
            # costuma passar sozinho
            response.raise_for_status()
            return response.json()

        except (httpx.HTTPError, ValueError) as exc:
            motivo = str(exc)
            time.sleep(min(2**attempt, MAX_BACKOFF))

    raise ApiError(f"{label}{url} falhou: {motivo}")
