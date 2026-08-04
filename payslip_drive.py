"""Funzioni pure di supporto alla sincronizzazione dei cedolini su Drive."""

from __future__ import annotations

from pathlib import PurePath
import re
from typing import Iterable, Mapping


def safe_pdf_filename(filename: str) -> str:
    name = PurePath(str(filename or "cedolino.pdf")).name
    name = re.sub(r"[\x00-\x1f]+", "", name).strip() or "cedolino.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:180]


def unique_pdf_filename(filename: str, existing_names: Iterable[str], suffix: str) -> str:
    safe_name = safe_pdf_filename(filename)
    existing = {str(name).casefold() for name in existing_names}
    if safe_name.casefold() not in existing:
        return safe_name
    stem = safe_name[:-4]
    clean_suffix = re.sub(r"[^0-9A-Za-z_-]+", "-", str(suffix)).strip("-") or "copia"
    candidate = f"{stem}-{clean_suffix}.pdf"
    counter = 2
    while candidate.casefold() in existing:
        candidate = f"{stem}-{clean_suffix}-{counter}.pdf"
        counter += 1
    return candidate


def pending_drive_files(
    drive_files: Iterable[Mapping[str, object]],
    registry: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    pending = []
    for drive_file in drive_files:
        file_id = str(drive_file.get("id", "") or "")
        status = str(registry.get(file_id, {}).get("status", "") or "").lower()
        if file_id and status != "confermato":
            pending.append(dict(drive_file))
    return sorted(
        pending,
        key=lambda item: str(item.get("modifiedTime", "") or ""),
        reverse=True,
    )

