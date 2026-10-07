"""Supporting-document generation (synthetic PDFs) and extraction.

Documents are rendered as labelled fields plus structured line items:
    Key: Value
    LINE: description | code | qty | rate | amount
Extraction reads the PDF text layer with pypdf. In LLM mode the Intake agent sends the same text to an
extractor model as quoted data; the deterministic parser below is the offline path and the cross-check.
"""
from __future__ import annotations

import hashlib
import re
import textwrap
import warnings
from pathlib import Path

warnings.filterwarnings("ignore", message=".*ARC4 has been moved.*")

from pypdf import PdfReader  # noqa: E402
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

# Phrases that indicate text trying to instruct the system rather than describe the claim.
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior)", r"system (note|prompt|instruction)", r"approve (the )?full amount",
    r"disregard", r"you are (an?|the) (ai|assistant|agent)", r"skip (the )?(policy|checks|verification)",
]


def write_pdf(path: Path, title: str, kind: str, fields: dict, lines: list[tuple] | None = None,
              body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=A4)
    y = 800
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, title)
    y -= 22
    c.setFont("Helvetica", 10)
    c.drawString(50, y, f"DOCUMENT: {kind}")
    y -= 18
    for k, v in fields.items():
        c.drawString(50, y, f"{k}: {v}")
        y -= 15
    if lines:
        y -= 6
        c.drawString(50, y, "ITEMS (description | code | qty | rate | amount):")
        y -= 15
        for ln in lines:
            c.drawString(60, y, "LINE: " + " | ".join(str(x) for x in ln))
            y -= 15
    if body:
        y -= 8
        for para in body.split("\n"):
            for chunk in textwrap.wrap(para, 95) or [""]:
                c.drawString(50, y, chunk)
                y -= 14
    c.save()
    return path


def pdf_text(path: str | Path) -> str:
    reader = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def _key(k: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", k.strip().lower()).strip("_")


def to_number(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = re.sub(r"[^\d.\-]", "", str(v))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def parse_text(text: str) -> dict:
    kind, fields, lines, body = "unknown", {}, [], []
    raw_lines = text.splitlines()
    for raw in raw_lines[1:]:  # first line is the document title
        line = raw.strip()
        if not line:
            continue
        if line.startswith("DOCUMENT:"):
            kind = line.split(":", 1)[1].strip()
        elif line.startswith("LINE:"):
            parts = [p.strip() for p in line[5:].split("|")]
            if len(parts) >= 5:
                lines.append({"description": parts[0], "code": parts[1], "qty": to_number(parts[2]),
                              "rate": to_number(parts[3]), "amount": to_number(parts[4])})
        elif line.startswith("ITEMS"):
            continue
        else:
            m = re.match(r"^([A-Za-z][A-Za-z0-9 /\-]{1,40}):\s*(.*)$", line)
            if m and not body:
                fields[_key(m.group(1))] = m.group(2).strip()
            else:
                body.append(line)
    title = text.splitlines()[0].strip() if text.strip() else ""
    return {"kind": kind, "title": title, "fields": fields, "lines": lines, "text": " ".join(body)}


def scan_for_injection(text: str) -> list[str]:
    hits = []
    for pat in INJECTION_PATTERNS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            hits.append(m.group(0))
    return hits


def file_hash(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:16]
