"""Règles d'éligibilité d'une offre, partagées par la collecte et la notation.

Deux critères écartent une offre avant tout jugement :

* elle n'est pas un stage (aucune mention de stage dans le titre ni dans le texte) ;
* elle exige de l'expérience (titre senior, ou plusieurs années demandées).

Fonctions pures, sans accès réseau ni base : chacune renvoie un motif de rejet, ou
``""`` quand l'offre reste éligible.
"""
from __future__ import annotations

import re
import unicodedata

# Texte plus court : pas assez d'information pour affirmer qu'aucun stage n'est cité.
MIN_DESCRIPTION_CHARS = 150

# Plage d'années d'expérience jugée plausible comme exigence : au-delà, c'est
# l'ancienneté de l'entreprise (« 130 ans d'expérience »), pas une exigence.
MIN_REQUIRED_YEARS = 2
MAX_REQUIRED_YEARS = 12

_INTERNSHIP_RE = re.compile(
    r"\b(?:stages?|stagiaires?|interns?|internships?|interships?|pfe|pfmp|"
    r"praktikant\w*|werkstudent\w*|fin d[ -]etudes?|end[ -]of[ -]studies|final[ -]year|"
    r"gap[ -]year|off[ -]cycle)\b"
)

_SENIOR_TITLE_RE = re.compile(
    r"\b(?:senior|sr|confirmee?s?|experimentee?s?|lead|principal|staff|"
    r"manager|head of|directeur|directrice|architect)\b"
)

# Pas collé à un autre chiffre, ni borne haute d'une fourchette (« 0-2 ans » = junior).
_YEARS = r"(?<!\d)(?<!\d-)(\d{1,2})(?!\d)"
_YEARS_UNIT = r"(?:ans?|years?|yrs?)"
_EXPERIENCE = r"(?:d |of |de )?(?:experience|exp)\b"
_EXPERIENCE_YEARS_RES = (
    # « 5 ans d'expérience », « 3+ years of experience », « 3 a 5 ans d'experience »
    re.compile(
        rf"{_YEARS}\s*\+?\s*(?:(?:a|to|ou|-)\s*\d{{1,2}}\s*)?{_YEARS_UNIT}\s*(?:minimum\s*)?{_EXPERIENCE}"
    ),
    # « expérience de 4 ans minimum », « experience of 3 years »
    re.compile(rf"experience\s+(?:de |d |of )?(?:minimum\s+|au moins\s+)?{_YEARS}\s*{_YEARS_UNIT}"),
)


def _normalize(text: str | None) -> str:
    """Minuscules, sans accents ; la ponctuation devient espace, sauf le tiret."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9+-]+", " ", plain).strip()


def mentions_internship(text: str | None) -> bool:
    return bool(_INTERNSHIP_RE.search(_normalize(text)))


def not_internship_reason(title: str | None, description: str | None) -> str:
    """Motif de rejet quand aucune mention de stage n'apparaît dans une fiche complète."""
    if len((description or "").strip()) < MIN_DESCRIPTION_CHARS:
        return ""
    if mentions_internship(title) or mentions_internship(description):
        return ""
    return "pas un stage (aucune mention de stage dans le titre ni la description)"


def required_years(description: str | None) -> int | None:
    """Plus petit nombre d'années d'expérience exigé dans le texte, s'il y en a un."""
    text = _normalize(description)
    found: list[int] = []
    for pattern in _EXPERIENCE_YEARS_RES:
        for match in pattern.finditer(text):
            years = int(match.group(1))
            if MIN_REQUIRED_YEARS <= years <= MAX_REQUIRED_YEARS:
                found.append(years)
    return min(found) if found else None


def experience_reason(title: str | None, description: str | None) -> str:
    """Motif de rejet pour un poste senior ou exigeant plusieurs années d'expérience.

    Une offre dont le titre annonce un stage n'est jamais écartée ici.
    """
    if mentions_internship(title):
        return ""
    if _SENIOR_TITLE_RE.search(_normalize(title)):
        return "poste senior (titre) : expérience exigée"
    years = required_years(description)
    if years is not None:
        return f"expérience exigée : {years} ans minimum"
    return ""
