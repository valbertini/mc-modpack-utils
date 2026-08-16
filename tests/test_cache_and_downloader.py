"""Cache (SQLite) e Downloader — dois caminhos sem rede real."""

import hashlib
import threading
from pathlib import Path

import httpx
import pytest

from mrpack2curseforge.config import Config
from mrpack2curseforge.exceptions import ConversionCancelled, DownloadError
from mrpack2curseforge.services.cache import (
    SimpleCache,
    cache_files,
    cache_stats,
    clear_cache,
)
from mrpack2curseforge.services.curseforge import slim_file, slim_project
from mrpack2curseforge.services.downloader import Downloader

CONTENT = b"conteudo-do-jar" * 100
SHA1 = hashlib.sha1(CONTENT).hexdigest()


# --------------------------------------------------------------------- cache
def test_cache_round_trip(tmp_path: Path):
    cache = SimpleCache(tmp_path / "c.sqlite3")

    assert cache.get("search", "sodium") is None

    cache.set("search", "sodium", [{"id": 1}])
    cache.set("files", "394468", [{"id": 2}])

    assert cache.get("search", "sodium") == [{"id": 1}]
    assert cache.get("files", "394468") == [{"id": 2}]
    # namespaces não se misturam
    assert cache.get("files", "sodium") is None
    assert cache.size() == 2


def test_limpar_o_cache_remove_tambem_as_sobras_do_sqlite(tmp_path: Path):
    """O `-wal` sozinho já reconstitui parte do cache: tem de sair junto."""

    caminho = tmp_path / "c.sqlite3"

    with SimpleCache(caminho) as cache:
        cache.set("search", "sodium", [{"id": 1}])

    caminho.with_suffix(".sqlite3-wal").write_bytes(b"sobra")
    (tmp_path / "antigo.json").write_text("{}", encoding="utf-8")

    assert cache_stats(caminho)["size_mb"] >= 0
    assert len(cache_files(caminho)) == 3

    resultado = clear_cache(caminho)

    assert sorted(resultado["removed"]) == [
        "antigo.json",
        "c.sqlite3",
        "c.sqlite3-wal",
    ]
    assert resultado["locked"] == []
    assert cache_files(caminho) == []

    # limpar de novo não é erro
    assert clear_cache(caminho)["removed"] == []


def test_cache_persists_between_instances(tmp_path: Path):
    path = tmp_path / "c.sqlite3"

    with SimpleCache(path) as first:
        first.set("mod", "1", {"name": "Sodium"})

    with SimpleCache(path) as second:
        assert second.get("mod", "1") == {"name": "Sodium"}


def test_cache_accepts_the_legacy_json_path(tmp_path: Path):
    """Quem tiver M2CF_CACHE apontando para .json continua funcionando."""

    cache = SimpleCache(tmp_path / "curseforge.json")

    assert cache.path.suffix == ".sqlite3"
    cache.set("mod", "1", 42)
    assert cache.get("mod", "1") == 42
    assert not (tmp_path / "curseforge.json").exists()


def test_disabled_cache_stores_nothing(tmp_path: Path):
    cache = SimpleCache(tmp_path / "c.sqlite3", enabled=False)

    cache.set("search", "x", [1])

    assert cache.get("search", "x") is None
    assert not (tmp_path / "c.sqlite3").exists()


def test_cache_degrades_instead_of_breaking(tmp_path: Path):
    """Falha ao abrir o banco vira 'sem cache', nunca uma exceção."""

    blocked = tmp_path / "arquivo"
    blocked.write_text("nao sou um banco")

    cache = SimpleCache(blocked / "sub" / "c.sqlite3")

    assert cache.enabled is False
    cache.set("search", "x", [1])
    assert cache.get("search", "x") is None


def test_cache_is_thread_safe(tmp_path: Path):
    cache = SimpleCache(tmp_path / "c.sqlite3")

    def work(index: int) -> None:
        for i in range(20):
            cache.set("search", f"{index}-{i}", {"i": i})
            cache.get("search", f"{index}-{i}")

    threads = [threading.Thread(target=work, args=(n,)) for n in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert cache.size() == 120


# ------------------------------------------------------------------ slimming
def test_slim_project_keeps_only_what_is_used():
    fat = {
        "id": 1,
        "name": "Sodium",
        "slug": "sodium",
        "summary": "rápido",
        "downloadCount": 10,
        "logo": {"thumbnailUrl": "http://x/t.png", "url": "http://x/f.png", "id": 9},
        "authors": [{"name": "JellySquid", "id": 5, "url": "http://x"}],
        "links": {"websiteUrl": "http://cf/sodium", "wikiUrl": "http://w"},
        "latestFiles": [
            {"id": 2, "fileName": "sodium.jar", "hashes": [1, 2, 3], "modules": [4, 5]}
        ],
        # campos pesados que não usamos
        "screenshots": [{"url": "x"} for _ in range(20)],
        "description": "x" * 5000,
        "categories": [{"id": i} for i in range(30)],
    }

    slim = slim_project(fat)

    assert set(slim) == {
        "id", "name", "slug", "summary", "downloadCount",
        "logo", "authors", "links", "latestFiles",
    }
    assert slim["authors"] == [{"name": "JellySquid"}]
    assert slim["links"] == {"websiteUrl": "http://cf/sodium"}
    assert "hashes" not in slim["latestFiles"][0]
    assert len(str(slim)) < len(str(fat)) / 5


def test_slim_file_keeps_what_the_ui_shows():
    slim = slim_file(
        {
            "id": 1,
            "fileName": "sodium.jar",
            "displayName": "Sodium 0.9",
            "fileDate": "2026-01-01",
            "fileLength": 100,
            "releaseType": 1,
            "gameVersions": ["1.21", "Fabric"],
            "dependencies": [{"modId": 9}],
            "changelog": "x" * 1000,
        }
    )

    assert "changelog" not in slim and "dependencies" not in slim
    assert slim["gameVersions"] == ["1.21", "Fabric"]


# ----------------------------------------------------------------- downloader
@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(Config, "HTTP_RETRIES", 2)
    monkeypatch.setattr(
        "mrpack2curseforge.services.downloader.time.sleep", lambda _s: None
    )


def make_downloader(handler, cancelled=None) -> Downloader:
    downloader = Downloader(cancelled=cancelled)
    downloader.client.close()
    downloader.client = httpx.Client(transport=httpx.MockTransport(handler))
    return downloader


def test_download_writes_the_file_and_checks_sha1(tmp_path: Path, fast_retries):
    downloader = make_downloader(lambda request: httpx.Response(200, content=CONTENT))
    destination = tmp_path / "mods" / "sodium.jar"

    downloader.download("http://x/sodium.jar", destination, SHA1)

    assert destination.read_bytes() == CONTENT
    assert not list(tmp_path.rglob("*.part"))  # nada de arquivo temporário


def test_download_rejects_a_corrupted_file(tmp_path: Path, fast_retries):
    downloader = make_downloader(lambda request: httpx.Response(200, content=b"errado"))
    destination = tmp_path / "sodium.jar"

    with pytest.raises(DownloadError):
        downloader.download("http://x/sodium.jar", destination, SHA1)

    assert not destination.exists()
    assert not list(tmp_path.rglob("*.part"))


def test_download_skips_a_file_that_is_already_correct(tmp_path: Path, fast_retries):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, content=CONTENT)

    destination = tmp_path / "sodium.jar"
    destination.write_bytes(CONTENT)

    make_downloader(handler).download("http://x/sodium.jar", destination, SHA1)

    assert calls == []  # não baixou de novo


def test_download_redownloads_when_the_hash_differs(tmp_path: Path, fast_retries):
    destination = tmp_path / "sodium.jar"
    destination.write_bytes(b"versao-antiga")

    make_downloader(lambda r: httpx.Response(200, content=CONTENT)).download(
        "http://x/sodium.jar", destination, SHA1
    )

    assert destination.read_bytes() == CONTENT


def test_download_aborts_midway_when_cancelled(tmp_path: Path, fast_retries):
    """Cancelar não espera o arquivo terminar (importante em packs de 500 MB)."""

    event = threading.Event()

    def handler(request):
        event.set()  # cancelado logo no primeiro chunk
        return httpx.Response(200, content=CONTENT)

    downloader = make_downloader(handler, cancelled=event.is_set)
    destination = tmp_path / "sodium.jar"

    with pytest.raises(ConversionCancelled):
        downloader.download("http://x/sodium.jar", destination, SHA1)

    assert not destination.exists()
    assert not list(tmp_path.rglob("*.part"))


def test_download_retries_on_server_error(tmp_path: Path, fast_retries):
    attempts = []

    def handler(request):
        attempts.append(1)
        if len(attempts) == 1:
            return httpx.Response(503)
        return httpx.Response(200, content=CONTENT)

    make_downloader(handler).download("http://x/a.jar", tmp_path / "a.jar", SHA1)

    assert len(attempts) == 2
