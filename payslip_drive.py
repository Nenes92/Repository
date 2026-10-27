"""Read-only Drive payslip import. No changes to PDF files or personal budgets."""
from dataclasses import dataclass, replace
from decimal import Decimal
from io import BytesIO
import re
from typing import Callable

from pypdf import PdfReader

MONTHS = {name: i for i, name in enumerate(
    ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
     "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"), 1)}
MONEY = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}"
API = "https://www.googleapis.com/drive/v3/files"


@dataclass(frozen=True)
class Payslip:
    month: str
    net: Decimal
    position: str
    file_id: str = ""
    name: str = ""


def parse_payslip_text(text: str, name: str) -> Payslip:
    """Accept the two observed payroll layouts; reject ambiguous month/amount."""
    text = text.replace("\r", "\n")
    periods = re.findall(
        r"Periodo\s+di\s+paga\s+(" + "|".join(MONTHS) + r")\s+(20\d{2})",
        text, flags=re.I)
    if not periods:
        # The older form has a MESE header and an Italian month/year in its body.
        periods = re.findall(r"\b(" + "|".join(MONTHS) + r")\s+(20\d{2})\b", text, flags=re.I)
    months = {f"{int(year):04d}-{MONTHS[month.lower()]:02d}" for month, year in periods}
    if len(months) != 1:
        raise ValueError("Mese del cedolino assente o ambiguo")
    month = months.pop()
    filename_period = re.search(r"(?<!\d)(20\d{2})[_-](\d{1,2})(?=[_-])", name)
    if filename_period:
        expected = f"{int(filename_period[1]):04d}-{int(filename_period[2]):02d}"
        if month != expected:
            raise ValueError("Mese nel PDF diverso dal nome del file")

    positions = set(re.findall(r"\bCid\.\s*(\d{13})\b", text, re.I))
    if not positions:
        # Old layout: company/employee position is printed before the personal data.
        positions = set(re.findall(r"(?<!\d)(\d{13})\s+X\s+I\b", text))
    if len(positions) != 1:
        raise ValueError("Posizione del cedolino assente o ambigua")

    # Never take the first amount after a distant or empty 'Netto a pagare' header.
    # It must immediately follow the label. Repeated identical page totals count once.
    amounts = re.findall(r"Netto\s+a\s+pagare\s+(" + MONEY + r")(?!\d)", text, re.I)
    if not amounts and "Periodo di paga" not in text and re.search(r"NETTO\s+A\s+PAGARE", text, re.I):
        # Old layout: IBAN + payment date + EURO amount + optional historical LIRE column.
        amounts = re.findall(
            r"\bIT\d{2}[A-Z0-9]{15,30}\s+\d{2}/\d{2}/\d{4}\s+(" + MONEY + r")(?!\d)",
            text, re.I)
    nets = {Decimal(value.replace(".", "").replace(",", ".")) for value in amounts}
    if len(nets) != 1:
        raise ValueError("Netto a pagare assente o ambiguo")
    net = nets.pop()
    if net <= 0:
        raise ValueError("Netto a pagare non positivo")
    return Payslip(month, net, positions.pop(), name=name)


def parse_payslip_pdf(data: bytes, name: str) -> Payslip:
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("PDF troppo grande")
    try:
        reader = PdfReader(BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ValueError("PDF protetto da password")
        if len(reader.pages) > 15:
            raise ValueError("PDF con troppe pagine")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except ValueError:
        raise
    except Exception:
        raise ValueError("PDF non leggibile") from None
    if not text.strip():
        raise ValueError("PDF scansionato senza testo leggibile")
    return parse_payslip_text(text, name)


def list_payslip_files(session, folder_id: str) -> list[dict]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", folder_id):
        raise ValueError("Cartella Drive non valida")
    # Check folder access: a successful empty files.list is not proof of access.
    check = session.get(f"{API}/{folder_id}",
                        params={"fields": "id,mimeType", "supportsAllDrives": "true"}, timeout=30)
    check.raise_for_status()
    if check.json().get("mimeType") != "application/vnd.google-apps.folder":
        raise ValueError("Il collegamento Drive non è una cartella")
    files, token = [], None
    while True:
        params = {
            "q": f"'{folder_id}' in parents and trashed=false and mimeType='application/pdf'",
            "fields": "files(id,name,mimeType,modifiedTime,md5Checksum,size),nextPageToken",
            "pageSize": 1000, "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        }
        if token:
            params["pageToken"] = token
        response = session.get(API, params=params, timeout=30)
        response.raise_for_status()
        page = response.json()
        files.extend(page.get("files", []))
        token = page.get("nextPageToken")
        if not token:
            return files


def download_payslip(session, file_id: str, name: str) -> Payslip:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", file_id):
        raise ValueError("File Drive non valido")
    # Stream with a size bound even when Google metadata omits the size.
    response = session.get(f"{API}/{file_id}",
                           params={"alt": "media", "supportsAllDrives": "true"},
                           timeout=30, stream=True)
    try:
        response.raise_for_status()
        parts, size = [], 0
        for chunk in response.iter_content(chunk_size=65536):
            size += len(chunk)
            if size > 15 * 1024 * 1024:
                raise ValueError("PDF troppo grande")
            parts.append(chunk)
        return replace(parse_payslip_pdf(b"".join(parts), name), file_id=file_id)
    finally:
        response.close()


def collect_payslips(files: list[dict], read: Callable[[dict], Payslip]) -> dict:
    positions, errors, rejected_months = {}, [], set()
    for item in files:
        name = item.get("name", "")
        if not re.search(r"CED_ORD", name, re.I):
            errors.append(f"{name}: non è un cedolino ordinario riconoscibile")
            continue
        try:
            slip = read(item)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, ValueError) else "lettura del PDF non riuscita"
            errors.append(f"{name}: {reason}")
            continue
        key = (slip.month, slip.position)
        previous = positions.get(key)
        if previous and previous.net != slip.net:
            rejected_months.add(slip.month)
            errors.append(f"{slip.month}: importi diversi per la stessa posizione; mese escluso")
        else:
            positions[key] = slip
    salaries = {}
    for (month, _), slip in positions.items():
        if month not in rejected_months:
            salaries[month] = salaries.get(month, Decimal(0)) + slip.net
    return {
        "salaries": {month: float(value) for month, value in sorted(salaries.items())},
        "files": len(files), "positions": len(positions), "errors": errors,
    }
