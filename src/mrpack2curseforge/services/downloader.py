"""Download dos arquivos que vão para `overrides/`."""

import hashlib
import time
from pathlib import Path
from typing import Callable

import httpx

from mrpack2curseforge.config import Config
from mrpack2curseforge.constants import USER_AGENT
from mrpack2curseforge.exceptions import ConversionCancelled, DownloadError


class Downloader:
    def __init__(self, cancelled: Callable[[], bool] | None = None):
        # permite abortar no meio de um arquivo grande, entre um chunk e outro
        self.cancelled = cancelled or (lambda: False)
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=Config.HTTP_TIMEOUT,
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "Downloader":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def download(self, url: str, destination: Path, sha1: str | None = None) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists() and self._matches(destination, sha1):
            return destination

        last_error: Exception | None = None

        for attempt in range(Config.HTTP_RETRIES):
            try:
                digest = hashlib.sha1()
                temporary = destination.with_suffix(destination.suffix + ".part")

                with self.client.stream("GET", url) as response:
                    response.raise_for_status()

                    with open(temporary, "wb") as handle:
                        for chunk in response.iter_bytes(chunk_size=1 << 16):
                            if self.cancelled():
                                handle.close()
                                temporary.unlink(missing_ok=True)
                                raise ConversionCancelled("download cancelado")

                            handle.write(chunk)
                            digest.update(chunk)

                if sha1 and digest.hexdigest().lower() != sha1.lower():
                    temporary.unlink(missing_ok=True)
                    raise DownloadError(f"SHA1 divergente em {destination.name}")

                temporary.replace(destination)
                return destination

            except (httpx.HTTPError, OSError, DownloadError) as exc:
                last_error = exc
                time.sleep(min(2 ** attempt, 10))

        raise DownloadError(f"Falha ao baixar {url}: {last_error}")

    @staticmethod
    def _matches(path: Path, sha1: str | None) -> bool:
        if not sha1:
            return False

        digest = hashlib.sha1()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 16), b""):
                digest.update(chunk)

        return digest.hexdigest().lower() == sha1.lower()
