"""Registros de conversão: os metadados que sobrevivem entre sessões.

O `.zip` de um modpack é grande e **totalmente regenerável**: com o `.mrpack` de
origem e os `(projectID, fileID)` já decididos, dá para remontá-lo sem consultar o
CurseForge de novo. Por isso o que fica salvo em disco é só o registro:

    output_modpacks/conversions/<id>.json

Ele guarda o que foi decidido para cada mod (inclusive as escolhas manuais e o
diagnóstico), o que alimenta tanto a tela de detalhes quanto a regeneração.
"""

import json
import time
from pathlib import Path
from typing import Any

from mrpack2curseforge.domain import Diagnosis, MatchResult, MatchStrategy

RECORDS_DIRNAME = "conversions"
RECORD_VERSION = 1


def records_dir(output_dir: Path) -> Path:
    return Path(output_dir) / RECORDS_DIRNAME


def record_path(output_dir: Path, record_id: str) -> Path:
    return records_dir(output_dir) / f"{Path(record_id).name}.json"


# --------------------------------------------------------------------------- #
# Escrita
# --------------------------------------------------------------------------- #
def _mod_entry(result: MatchResult) -> dict[str, Any]:
    diagnosis = result.diagnosis

    return {
        "file_name": result.mod.file_name,
        "file_path": result.mod.file_path,
        "status": result.status,
        "strategy": result.strategy.value,
        "project_id": result.project_id,
        "file_id": result.file_id,
        "project_name": result.project_name,
        "project_slug": result.project_slug,
        "project_author": result.project_author,
        # o arquivo já vinha em overrides/; sem isso a regeração o trataria como
        # um item do índice e tentaria baixá-lo (ele não tem URL)
        "from_overrides": result.mod.from_overrides,
        "modrinth_title": result.modrinth.title if result.modrinth else None,
        "modrinth_slug": result.modrinth.slug if result.modrinth else None,
        "queries_tried": result.queries_tried,
        "error": result.error,
        "diagnosis": (
            {
                "reason": diagnosis.reason.value,
                "similarity": round(diagnosis.similarity, 3),
                "project_id": diagnosis.project_id,
                "project_name": diagnosis.project_name,
                "project_slug": diagnosis.project_slug,
                "closest_file_id": diagnosis.closest_file_id,
                "closest_file_name": diagnosis.closest_file_name,
                "matched_reference": diagnosis.matched_reference,
                "section": diagnosis.section,
            }
            if diagnosis
            else None
        ),
    }


def build_record(outcome, resolutions: dict | None = None) -> dict[str, Any]:
    """Monta o registro a partir de um `ConversionOutcome`."""

    report = outcome.report
    pack = outcome.pack

    return {
        # facilita migrar o formato depois sem quebrar registros antigos
        "record_version": RECORD_VERSION,
        "id": outcome.output.stem,
        "created_at": time.time(),
        "source": outcome.source.name,
        "zip_name": outcome.output.name,
        "pack": {
            "name": pack.name,
            "version": pack.version,
            "summary": pack.summary,
            "minecraft": pack.minecraft.version,
            "loader": pack.loader_id,
        },
        "summary": {
            "total_mods": report.total_mods,
            "matched": report.matched,
            "overrides": report.overrides,
            "version_unavailable": report.version_unavailable,
            "not_on_curseforge": report.not_on_curseforge,
            "failed": report.failed,
            "extra_files": report.extra_files,
            "override_files": report.override_files,
            "success_rate": round(report.success_rate, 1),
            "duration_seconds": round(report.duration_seconds, 1),
        },
        "resolutions": {
            file_name: {
                "project_id": resolution.project_id,
                "file_id": resolution.file_id,
                "project_name": resolution.project_name,
                "project_slug": resolution.project_slug,
                "file_name": resolution.file_name,
            }
            for file_name, resolution in (resolutions or {}).items()
        },
        "mods": [_mod_entry(result) for result in outcome.results],
    }


def save_record(record: dict[str, Any], output_dir: Path) -> Path:
    destination = record_path(output_dir, record["id"])
    destination.parent.mkdir(parents=True, exist_ok=True)

    previous = _read(destination)
    if previous:
        # reconversões do mesmo pack preservam a data original
        record["created_at"] = previous.get("created_at", record["created_at"])

    record["updated_at"] = time.time()
    destination.write_text(
        json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return destination


# --------------------------------------------------------------------------- #
# Leitura
# --------------------------------------------------------------------------- #
def _read(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def load_record(output_dir: Path, record_id: str) -> dict[str, Any] | None:
    return _read(record_path(output_dir, record_id))


def list_records(output_dir: Path, input_dir: Path | None = None) -> list[dict]:
    """Resumo de todos os registros, do mais recente para o mais antigo."""

    folder = records_dir(output_dir)
    if not folder.is_dir():
        return []

    rows = []

    for path in folder.glob("*.json"):
        record = _read(path)
        if not record:
            continue

        source = record.get("source")
        source_path = Path(input_dir or "") / source if (input_dir and source) else None

        # o .zip é apagado ao fechar a conversão; quando existe, a lista mostra
        # o tamanho igual à lista de entrada
        zip_path = output_dir / (record.get("zip_name") or "")
        tem_zip = bool(record.get("zip_name")) and zip_path.is_file()

        rows.append(
            {
                "id": record.get("id", path.stem),
                "source": source,
                "source_available": bool(source_path and source_path.is_file()),
                "size_mb": (
                    round(zip_path.stat().st_size / (1024 * 1024), 1)
                    if tem_zip
                    else None
                ),
                "pack": record.get("pack", {}),
                "summary": record.get("summary", {}),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at", record.get("created_at")),
                "manual_choices": len(record.get("resolutions") or {}),
            }
        )

    rows.sort(key=lambda row: row.get("updated_at") or 0, reverse=True)
    return rows


def delete_record(output_dir: Path, record_id: str) -> bool:
    path = record_path(output_dir, record_id)

    if path.is_file():
        path.unlink()
        return True

    return False


# --------------------------------------------------------------------------- #
# Reconstrução
# --------------------------------------------------------------------------- #
def results_from_record(record: dict[str, Any], pack) -> list[MatchResult]:
    """Recria os `MatchResult` de um registro, casando pelo caminho do arquivo."""

    by_path = {entry["file_path"]: entry for entry in record.get("mods", [])}
    results: list[MatchResult] = []

    for mod in pack.convertible:
        entry = by_path.get(mod.file_path)

        if not entry:
            if mod.from_overrides:
                # nunca esteve no manifest; continua em overrides/, sem virar
                # um "não convertido" que a tela mandaria baixar
                continue

            # o .mrpack mudou desde a conversão: trata como não convertido
            results.append(MatchResult(mod=mod))
            continue

        diagnosis_data = entry.get("diagnosis")

        results.append(
            MatchResult(
                mod=mod,
                strategy=MatchStrategy(entry.get("strategy", "unmatched")),
                project_id=entry.get("project_id"),
                file_id=entry.get("file_id"),
                project_name=entry.get("project_name"),
                project_slug=entry.get("project_slug"),
                project_author=entry.get("project_author"),
                queries_tried=entry.get("queries_tried") or [],
                diagnosis=Diagnosis(**diagnosis_data) if diagnosis_data else None,
            )
        )

    return results
