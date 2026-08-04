"""Estrazione assistita delle rettifiche nette dai cedolini italiani.

Il modulo non decide mai in modo definitivo cosa salvare: produce candidati
revisionabili dalla UI. Le regole privilegiano precisione e trasparenza rispetto
al tentativo di ricostruire automaticamente l'intero cedolino.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import re
import unicodedata
from typing import Iterable, Mapping


MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

POSITIVE_PATTERNS = (
    ("edr accredito integrativo", "Accredito integrativo"),
    ("accredito integrativo", "Accredito integrativo"),
    ("rimborso 730", "730 / conguaglio fiscale"),
    ("730 rimborso", "730 / conguaglio fiscale"),
    ("conguaglio a credito", "Conguaglio a credito"),
    ("credito irpef", "Credito fiscale"),
    ("credito fiscale", "Credito fiscale"),
    ("rimborso", "Rimborso"),
    ("arretrat", "Arretrati"),
    ("una tantum", "Una tantum"),
    ("premio", "Premio"),
)

POSSIBLY_TAXABLE_PATTERNS = (
    "edr accredito integrativo",
    "accredito integrativo",
    "arretrat",
    "una tantum",
    "premio",
)

NEGATIVE_PATTERNS = (
    ("addizionale regionale", "Addizionale regionale"),
    ("add regionale", "Addizionale regionale"),
    ("addizionale comunale", "Addizionale comunale"),
    ("add comunale", "Addizionale comunale"),
    ("acconto addizionale", "Acconto addizionale"),
    ("saldo addizionale", "Saldo addizionale"),
    ("conguaglio a debito", "Conguaglio a debito"),
    ("730 trattenuta", "730 / conguaglio fiscale"),
    ("trattenuta 730", "730 / conguaglio fiscale"),
    ("recupero", "Recupero / trattenuta"),
    ("trattenuta", "Trattenuta"),
)

# Queste voci sono già assorbite dal fisso netto o dal coefficiente delle
# variabili. Includerle nella rettifica produrrebbe un doppio conteggio.
EXCLUDED_PATTERNS = (
    "paga base",
    "retribuzione ordinaria",
    "minimo tabellare",
    "contingenza",
    "superminimo",
    "scatti anzianita",
    "maggiorazione",
    "straordinario",
    "indennita turno",
    "buono pasto",
    "ticket",
    "imponibile",
    "contributi inps",
    "contributo inps",
    "ritenuta irpef",
    "irpef netta",
    "detrazione",
    "netto del mese",
    "netto pagare",
    "totale competenze",
    "totale trattenute",
)

AMOUNT_RE = re.compile(
    r"(?<![\w/])[-+]?\s*(?:\d{1,3}(?:[. ]\d{3})+|\d+)[,.]\d{2}(?!\d)"
)


@dataclass(frozen=True)
class PayslipCandidate:
    description: str
    category: str
    amount: float
    sign: int
    confidence: float
    source_line: str
    include: bool = True

    @property
    def signed_amount(self) -> float:
        return abs(self.amount) * (1 if self.sign >= 0 else -1)


def normalize_label(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", ascii_value.lower())).strip()


def label_signature(value: str) -> str:
    """Firma testuale stabile anche se quantità e importi cambiano."""
    return " ".join(token for token in normalize_label(value).split() if not token.isdigit())


def parse_italian_amount(value: str) -> float:
    clean = str(value or "").strip().replace("€", "").replace(" ", "")
    if not clean:
        raise ValueError("Importo vuoto")
    if "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif clean.count(".") > 1:
        clean = clean.replace(".", "")
    return float(clean)


def extract_payslip_month(text: str, filename: str = "") -> str | None:
    searchable = normalize_label(f"{text[:5000]} {filename}")
    for month_name, month_number in MONTHS.items():
        match = re.search(rf"\b{month_name}\s+(20\d{{2}})\b", searchable)
        if match:
            return f"{int(match.group(1)):04d}-{month_number:02d}"
        match = re.search(rf"\b(20\d{{2}})\s+{month_name}\b", searchable)
        if match:
            return f"{int(match.group(1)):04d}-{month_number:02d}"
    numeric = re.search(
        r"(?<!\d)(20\d{2})[-_/ .](0?[1-9]|1[0-2])(?!\d)",
        f"{filename} {text[:2000]}",
    )
    if numeric:
        return f"{int(numeric.group(1)):04d}-{int(numeric.group(2)):02d}"
    return None


def extract_pdf_text(pdf_bytes: bytes) -> str:
    if not pdf_bytes:
        raise ValueError("Il PDF è vuoto.")
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dipende dal deploy
        raise RuntimeError("Lettore PDF non installato.") from exc
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise ValueError("Il PDF non è leggibile o è protetto.") from exc
    if len(text.strip()) < 20:
        raise ValueError(
            "Il PDF sembra una scansione senza testo. Per ora serve il PDF digitale originale."
        )
    return text


def _learned_match(
    normalized_line: str,
    learned_labels: Mapping[str, Mapping[str, object]] | None,
) -> tuple[int, str, bool] | None:
    if not learned_labels:
        return None
    line_signature = label_signature(normalized_line)
    for label, settings in learned_labels.items():
        normalized_label = label_signature(label)
        if normalized_label and normalized_label in line_signature:
            sign = 1 if int(settings.get("sign", 1)) >= 0 else -1
            include = bool(settings.get("include", True))
            category = str(settings.get("category", "Voce già confermata"))
            return sign, category, include
    return None


def find_adjustment_candidates(
    text: str,
    learned_labels: Mapping[str, Mapping[str, object]] | None = None,
) -> list[PayslipCandidate]:
    candidates: list[PayslipCandidate] = []
    seen: set[tuple[str, float, int]] = set()
    for raw_line in str(text or "").splitlines():
        line = " ".join(raw_line.split())
        normalized = normalize_label(line)
        if not normalized:
            continue
        learned = _learned_match(normalized, learned_labels)
        if learned is None and any(pattern in normalized for pattern in EXCLUDED_PATTERNS):
            continue
        amounts = AMOUNT_RE.findall(line)
        if not amounts:
            continue

        if learned:
            sign, category, include = learned
            confidence = 0.98
        else:
            match = next(
                ((pattern, category, 1) for pattern, category in POSITIVE_PATTERNS if pattern in normalized),
                None,
            )
            if match is None:
                match = next(
                    ((pattern, category, -1) for pattern, category in NEGATIVE_PATTERNS if pattern in normalized),
                    None,
                )
            if match is None:
                continue
            matched_pattern, category, sign = match
            possibly_taxable = sign > 0 and any(
                pattern in normalized for pattern in POSSIBLY_TAXABLE_PATTERNS
            )
            include = not possibly_taxable
            if possibly_taxable:
                category = f"{category} · possibile importo lordo"
                confidence = 0.55
            else:
                confidence = 0.90 if len(amounts) == 1 else 0.72

        amount = abs(parse_italian_amount(amounts[-1]))
        if amount <= 0 or amount > 10000:
            continue
        label_without_amounts = AMOUNT_RE.sub(" ", line)
        description = " ".join(label_without_amounts.split()).strip(" -:;|") or category
        key = (normalize_label(description), round(amount, 2), sign)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            PayslipCandidate(
                description=description,
                category=category,
                amount=amount,
                sign=sign,
                confidence=confidence,
                source_line=line[:300],
                include=include,
            )
        )
    return candidates


def candidates_total(candidates: Iterable[PayslipCandidate]) -> float:
    return round(sum(item.signed_amount for item in candidates if item.include), 2)
