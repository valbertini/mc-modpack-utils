"""Cache persistente das respostas das APIs.

Guardado em SQLite. A versão anterior era um único JSON que precisava ser lido e
reescrito **inteiro** a cada gravação — com um pack grande ele chegou a 46 MB,
custando 0,6 s para abrir e 0,4 s + 46 MB de escrita por flush (e a interface web
abre um cache por requisição). Com SQLite, ler e gravar uma chave é O(1).

O cache é só otimização: qualquer falha aqui degrada para "sem cache" em vez de
derrubar a conversão.
"""

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class SimpleCache:
    def __init__(self, path: Path | str, enabled: bool = True):
        self.path = Path(path)

        # aceita o caminho antigo (.json) sem quebrar quem tem M2CF_CACHE definido
        if self.path.suffix != ".sqlite3":
            self.path = self.path.with_suffix(".sqlite3")

        self.enabled = enabled
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

        if self.enabled:
            self._connect()

    # ---------------------------------------------------------------- setup
    def _connect(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)

            connection = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None, timeout=10
            )
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "  key TEXT PRIMARY KEY,"
                "  value TEXT NOT NULL"
                ")"
            )
            self._conn = connection

        except (sqlite3.Error, OSError):
            # caminho inválido, disco cheio, permissão negada... segue sem cache
            self.enabled = False
            self._conn = None

    # ------------------------------------------------------------------ api
    def get(self, namespace: str, key: str) -> Any | None:
        if not self._conn:
            return None

        try:
            with self._lock:
                row = self._conn.execute(
                    "SELECT value FROM entries WHERE key = ?", (f"{namespace}:{key}",)
                ).fetchone()
        except sqlite3.Error:
            return None

        if not row:
            return None

        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return None

    def set(self, namespace: str, key: str, value: Any) -> None:
        if not self._conn:
            return

        try:
            payload = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError):
            return

        try:
            with self._lock:
                self._conn.execute(
                    "INSERT OR REPLACE INTO entries (key, value) VALUES (?, ?)",
                    (f"{namespace}:{key}", payload),
                )
        except sqlite3.Error:
            pass

    def flush(self) -> None:
        """Mantido por compatibilidade: o SQLite grava em autocommit."""

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    # junta o WAL no arquivo principal para não deixar sobras
                    self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except sqlite3.Error:
                    pass

                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # --------------------------------------------------------------- extras
    def size(self) -> int:
        """Quantidade de entradas guardadas (usado em testes e diagnóstico)."""

        if not self._conn:
            return 0

        try:
            with self._lock:
                return self._conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
        except sqlite3.Error:
            return 0

    def __enter__(self) -> "SimpleCache":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Limpeza (usada pelo `clear-cache` do CLI e pelo botão da interface)
# --------------------------------------------------------------------------- #
def cache_files(path: Path | str) -> list[Path]:
    """Os arquivos que compõem o cache, inclusive as sobras do SQLite."""

    base = Path(path)
    if base.suffix != ".sqlite3":
        base = base.with_suffix(".sqlite3")

    pasta = base.parent

    return [
        item
        for item in [
            base,
            base.with_suffix(".sqlite3-wal"),
            base.with_suffix(".sqlite3-shm"),
            # o cache antigo em JSON, de antes da migração para SQLite
            *(sorted(pasta.glob("*.json")) if pasta.is_dir() else []),
        ]
        if item.is_file()
    ]


def cache_stats(path: Path | str) -> dict[str, Any]:
    arquivos = cache_files(path)
    total = sum(item.stat().st_size for item in arquivos)

    return {
        "files": [item.name for item in arquivos],
        "size_mb": round(total / (1024 * 1024), 1),
    }


def clear_cache(path: Path | str) -> dict[str, Any]:
    """Apaga o cache. Um arquivo em uso é ignorado, não vira erro."""

    removidos: list[str] = []
    presos: list[str] = []
    total = 0

    for item in cache_files(path):
        size = item.stat().st_size
        try:
            item.unlink()
        except OSError:
            presos.append(item.name)
            continue

        removidos.append(item.name)
        total += size

    return {
        "removed": removidos,
        "locked": presos,
        "freed_mb": round(total / (1024 * 1024), 1),
    }
