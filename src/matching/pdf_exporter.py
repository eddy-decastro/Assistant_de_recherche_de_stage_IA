"""Générateur de PDF pour les lettres de motivation (ReportLab).

Produit un document PDF A4 élégant, sobre et professionnel (1 page forcée jusqu'à 9 pt,
pagination dynamique NumberedCanvas au-delà, en-tête typographique soigné et date localisée).
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate

from src.candidate import get_candidate_info
from src.matching.cover_letter import is_english_text

# Palette sobre et élégante
COLOR_PRIMARY = colors.HexColor("#1E3A8A")     # Bleu marine profond
COLOR_TEXT = colors.HexColor("#1E293B")        # Gris ardoise foncé
COLOR_MUTED = colors.HexColor("#64748B")       # Gris moyen
COLOR_DIVIDER = colors.HexColor("#CBD5E1")     # Gris clair pour filet de séparation

DEFAULT_HEADER_TITLE = (
    "Élève-ingénieur IMT Mines Alès, double diplôme M2 Mathématiques en Action (Mines Saint-Étienne)"
)

_FR_MONTHS = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

_EN_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def format_letter_date(
    d: date | None = None,
    location: str = "Paris, France",
    *,
    is_english: bool = False,
) -> str:
    """Formate la date selon la langue (« Paris, le 3 novembre 2026 » ou « Paris, November 3, 2026 »)."""
    target_date = d or date.today()
    city = location.split(",")[0].strip() if location else ""
    if is_english:
        month_en = _EN_MONTHS[target_date.month - 1]
        return (
            f"{city}, {month_en} {target_date.day}, {target_date.year}"
            if city
            else f"{month_en} {target_date.day}, {target_date.year}"
        )
    month_fr = _FR_MONTHS[target_date.month - 1]
    day_str = "1er" if target_date.day == 1 else str(target_date.day)
    return (
        f"{city}, le {day_str} {month_fr} {target_date.year}"
        if city
        else f"Le {day_str} {month_fr} {target_date.year}"
    )


class NumberedCanvas(canvas.Canvas):
    """Canvas à deux passes pour numéroter dynamiquement 'Page X / Y' avec le nom du candidat."""

    def __init__(self, *args: Any, candidate_name: str = "Eddy DE CASTRO", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []
        self.candidate_name = candidate_name or "Eddy DE CASTRO"
        self.page_count: int = 0

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        self.page_count = num_pages
        for state in self._saved_page_states:
            self.__dict__.update(state)
            if num_pages > 1:
                self.saveState()
                self.setFont("Helvetica", 8)
                self.setFillColor(COLOR_MUTED)
                self.drawString(50, 28, f"{self.candidate_name} — Lettre de motivation")
                page_text = f"Page {self._pageNumber} / {num_pages}"
                self.drawRightString(A4[0] - 50, 28, page_text)
                self.restoreState()
            super().showPage()
        super().save()


def _clean_text_for_xml(text: str) -> str:
    """Échappe les caractères spéciaux pour le parser XML de ReportLab."""
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


def _build_pdf_attempt(
    *,
    letter_text: str,
    name: str,
    title: str,
    contact_parts: list[str],
    date_line: str,
    body_font_size: float,
    body_leading: float,
    body_space_after: float,
    top_margin: float,
    bottom_margin: float,
) -> tuple[bytes, int]:
    """Construit un essai de rendu PDF et retourne ``(pdf_bytes, page_count)``."""
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=50,
        rightMargin=50,
        topMargin=top_margin,
        bottomMargin=bottom_margin,
    )

    styles = getSampleStyleSheet()

    header_name_style = ParagraphStyle(
        "HeaderName",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14.0,
        leading=16.5,
        textColor=COLOR_PRIMARY,
        spaceAfter=2,
        keepWithNext=True,
    )

    header_title_style = ParagraphStyle(
        "HeaderTitle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=11.5,
        textColor=COLOR_MUTED,
        spaceAfter=3,
        keepWithNext=True,
    )

    header_contact_style = ParagraphStyle(
        "HeaderContact",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10.5,
        textColor=COLOR_MUTED,
        spaceAfter=5,
        keepWithNext=True,
    )

    date_style = ParagraphStyle(
        "DateStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=COLOR_MUTED,
        alignment=TA_RIGHT,
        spaceAfter=body_space_after + 1,
        keepWithNext=True,
    )

    object_style = ParagraphStyle(
        "ObjectStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=body_font_size,
        leading=body_leading,
        textColor=COLOR_PRIMARY,
        spaceAfter=body_space_after + 1,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=body_font_size,
        leading=body_leading,
        textColor=COLOR_TEXT,
        alignment=TA_JUSTIFY,
        spaceAfter=body_space_after,
    )

    signature_style = ParagraphStyle(
        "SignatureStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=body_font_size,
        leading=body_leading,
        textColor=COLOR_TEXT,
        spaceBefore=4,
        spaceAfter=2,
        keepWithNext=True,
    )

    story: list[Any] = []

    story.append(Paragraph(_clean_text_for_xml(name.upper()), header_name_style))
    if title:
        story.append(Paragraph(_clean_text_for_xml(title), header_title_style))

    if contact_parts:
        contact_line = "  •  ".join(contact_parts)
        story.append(Paragraph(_clean_text_for_xml(contact_line), header_contact_style))

    story.append(
        HRFlowable(
            width="100%",
            thickness=0.75,
            color=COLOR_DIVIDER,
            spaceBefore=2,
            spaceAfter=6,
        )
    )

    story.append(Paragraph(_clean_text_for_xml(date_line), date_style))

    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", letter_text) if p.strip()]
    if len(raw_paragraphs) <= 2 and "\n" in letter_text:
        raw_paragraphs = [p.strip() for p in letter_text.splitlines() if p.strip()]

    for i, para in enumerate(raw_paragraphs):
        clean_p = _clean_text_for_xml(para).replace("\n", "<br/>")
        low_p = para.lstrip().lower()

        if low_p.startswith("objet") or low_p.startswith("subject"):
            story.append(Paragraph(clean_p, object_style))
        elif i == len(raw_paragraphs) - 1 and (
            name.lower() in low_p or "eddy" in low_p
        ):
            story.append(Paragraph(clean_p, signature_style))
        else:
            story.append(Paragraph(clean_p, body_style))

    holder: dict[str, NumberedCanvas] = {}

    def _make_canvas(*args: Any, **kwargs: Any) -> NumberedCanvas:
        c = NumberedCanvas(*args, candidate_name=name, **kwargs)
        holder["canvas"] = c
        return c

    doc.build(story, canvasmaker=_make_canvas)
    page_count = holder["canvas"].page_count if "canvas" in holder else 1
    return buffer.getvalue(), page_count


def generate_cover_letter_pdf(
    letter_text: str,
    candidate_info: dict[str, str] | None = None,
    job: dict[str, Any] | None = None,
) -> bytes:
    """Génère un PDF A4 en mémoire à partir du texte de la lettre de motivation.

    Réduit progressivement l'interligne puis la taille de police (jusqu'à 9 pt minimum)
    si le rendu dépasse une page. Au-delà, accepte 2 pages.
    """
    _ = job
    candidate = candidate_info or get_candidate_info()
    name = (candidate.get("name") or "Eddy DE CASTRO").strip()
    raw_title = (candidate.get("title") or "").strip()
    if not raw_title or raw_title == "Mines de Saint-Étienne":
        title = DEFAULT_HEADER_TITLE
    else:
        title = raw_title
    phone = candidate.get("phone", "")
    email = candidate.get("email", "")
    linkedin = candidate.get("linkedin", "linkedin.com/in/eddy-de-castro")
    github = candidate.get("github", "github.com/eddy-decastro")
    location = candidate.get("location", "Paris, France")

    contact_parts = [p for p in [location, phone, email, linkedin, github] if p]
    is_en = is_english_text(letter_text)
    date_line = format_letter_date(date.today(), location, is_english=is_en)

    # Séquence d'ajustement progressif : interligne d'abord, puis police (minimum 9.0 pt)
    attempts = [
        (9.5, 14.0, 8.0, 42.0, 45.0),
        (9.5, 13.0, 6.5, 38.0, 40.0),
        (9.5, 12.0, 5.5, 35.0, 36.0),
        (9.2, 11.5, 5.0, 33.0, 34.0),
        (9.0, 11.0, 4.5, 30.0, 32.0),
    ]

    last_pdf = b""
    for font_size, leading, space_after, top_m, bottom_m in attempts:
        pdf_bytes, pages = _build_pdf_attempt(
            letter_text=letter_text,
            name=name,
            title=title,
            contact_parts=contact_parts,
            date_line=date_line,
            body_font_size=font_size,
            body_leading=leading,
            body_space_after=space_after,
            top_margin=top_m,
            bottom_margin=bottom_m,
        )
        last_pdf = pdf_bytes
        if pages <= 1:
            return pdf_bytes

    return last_pdf
