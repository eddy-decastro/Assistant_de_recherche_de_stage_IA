"""Générateur de PDF pour les lettres de motivation (ReportLab).

Produit un document PDF A4 élégant, sobre et professionnel (1 à 2 pages avec
pagination dynamique NumberedCanvas, en-tête typographique soigné et gestion
anti-orpheline).
"""
from __future__ import annotations

import io
import re
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

from src.matching.cover_letter import get_candidate_info

# Palette sobre et élégante
COLOR_PRIMARY = colors.HexColor("#1E3A8A")     # Bleu marine profond
COLOR_TEXT = colors.HexColor("#1E293B")        # Gris ardoise foncé
COLOR_MUTED = colors.HexColor("#64748B")       # Gris moyen
COLOR_DIVIDER = colors.HexColor("#CBD5E1")     # Gris clair pour filet de séparation


class NumberedCanvas(canvas.Canvas):
    """Canvas à deux passes pour numéroter dynamiquement 'Page X / Y'."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            if num_pages > 1:
                self.saveState()
                self.setFont("Helvetica", 8)
                self.setFillColor(COLOR_MUTED)
                # Bas de page : Nom du candidat à gauche, Page X / Y à droite
                self.drawString(50, 28, "Eddy DE CASTRO — Lettre de motivation")
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


def generate_cover_letter_pdf(
    letter_text: str,
    candidate_info: dict[str, str] | None = None,
    job: dict[str, Any] | None = None,
) -> bytes:
    """Génère un PDF A4 en mémoire à partir du texte de la lettre de motivation.

    Parameters
    ----------
    letter_text : str
        Le texte complet de la lettre de motivation.
    candidate_info : dict[str, str], optional
        Informations du candidat (name, title, phone, email, linkedin, github, location).
    job : dict[str, Any], optional
        Informations sur le poste (title, company, etc.).

    Returns
    -------
    bytes
        Le flux binaire du fichier PDF généré.
    """
    candidate = candidate_info or get_candidate_info()
    name = candidate.get("name", "Eddy DE CASTRO")
    title = candidate.get("title", "Élève-ingénieur Mines de Saint-Étienne — Double diplôme M2 Mathématiques en Action")
    phone = candidate.get("phone", "06 98 82 44 85")
    email = candidate.get("email", "eddyprepa123@gmail.com")
    linkedin = candidate.get("linkedin", "linkedin.com/in/eddy-de-castro")
    github = candidate.get("github", "github.com/eddy-decastro")
    location = candidate.get("location", "Paris, France")

    buffer = io.BytesIO()

    # Document A4 avec marges ajustées pour 1 à 2 pages
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=50,
        rightMargin=50,
        topMargin=42,
        bottomMargin=45,
    )

    styles = getSampleStyleSheet()

    header_name_style = ParagraphStyle(
        "HeaderName",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=14.5,
        leading=17,
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
        spaceAfter=6,
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
        spaceAfter=10,
        keepWithNext=True,
    )

    object_style = ParagraphStyle(
        "ObjectStyle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9.5,
        leading=13,
        textColor=COLOR_PRIMARY,
        spaceAfter=10,
        keepWithNext=True,
    )

    body_style = ParagraphStyle(
        "BodyStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=14,
        textColor=COLOR_TEXT,
        alignment=TA_JUSTIFY,
        spaceAfter=8,
    )

    signature_style = ParagraphStyle(
        "SignatureStyle",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9.5,
        leading=13.5,
        textColor=COLOR_TEXT,
        spaceBefore=6,
        spaceAfter=4,
        keepWithNext=True,
    )

    story: list[Any] = []

    # 1. En-tête candidat
    story.append(Paragraph(_clean_text_for_xml(name.upper()), header_name_style))
    if title:
        story.append(Paragraph(_clean_text_for_xml(title), header_title_style))

    contact_parts = [p for p in [location, phone, email, linkedin, github] if p]
    if contact_parts:
        contact_line = "  •  ".join(contact_parts)
        story.append(Paragraph(_clean_text_for_xml(contact_line), header_contact_style))

    story.append(
        HRFlowable(
            width="100%",
            thickness=0.75,
            color=COLOR_DIVIDER,
            spaceBefore=2,
            spaceAfter=8,
        )
    )

    # Date du jour
    today_fr = date.today().strftime("%d/%m/%Y")
    loc_prefix = f"{location.split(',')[0].strip()}, le " if location else "Le "
    story.append(Paragraph(_clean_text_for_xml(f"{loc_prefix}{today_fr}"), date_style))

    # 2. Parsing du corps de la lettre
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", letter_text) if p.strip()]

    for i, para in enumerate(raw_paragraphs):
        clean_p = _clean_text_for_xml(para).replace("\n", "<br/>")

        # Détection Objet
        if para.lower().startswith("objet") or "objet :" in para.lower():
            story.append(Paragraph(clean_p, object_style))
        # Détection Signature finale
        elif i == len(raw_paragraphs) - 1 and (
            name.lower() in para.lower() or "eddy" in para.lower()
        ):
            story.append(Paragraph(clean_p, signature_style))
        else:
            story.append(Paragraph(clean_p, body_style))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
