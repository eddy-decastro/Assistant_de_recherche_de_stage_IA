"""Hygiène de la base : dédoublonnage des offres collectées.

Les plateformes exposent souvent la même offre sous plusieurs URLs (paramètres de
suivi, rediffusion, slug différent d'un run à l'autre). Ce module identifie ces
groupes et désigne la fiche à conserver — la plus complète — sans rien connaître de
la base : les fonctions sont pures, donc directement testables.

Règles de regroupement (au choix, cumulatives) :
  1. même **URL canonique** (minuscules, sans query/fragment ni slash final) ;
  2. même **entreprise normalisée** ET titres **équivalents** (voir ``title_similarity``).

La comparaison de titres est *ensembliste* (tokens porteurs de sens, Jaccard) et non
caractère à caractère : c'est indispensable ici, « (F/H) » et « (H/F) » ne diffèrent
que par l'ordre de deux lettres (0,92 en comparaison brute, 1,0 après normalisation).
"""
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from typing import Any

# Normalisation d'URL : implémentation unique définie par le module de scraping
# (``scrapers.models``), ré-exportée ici pour les besoins de la base et du
# dashboard. La déduplication de collecte et celle d'ingestion ne peuvent donc
# pas diverger.
from scrapers.models import canonical_url  # noqa: F401  (ré-export volontaire)

# Seuil de similarité (0-1) au-delà duquel deux offres d'une même entreprise sont
# considérées comme la même annonce. Calibré sur la base réelle : « … Deep Learning
# (F/H) » et « … Machine learning (F/H) » — deux offres distinctes — tombent à 0,33,
# tandis que la même offre dont le titre perd son suffixe atteint 1,0.
TITLE_SIMILARITY_THRESHOLD = 0.95

# Tokens qui ne distinguent PAS deux offres : type de contrat, mentions de genre,
# mots de liaison et durées. Les retirer fait converger « (F/H) » et « (H/F) »,
# « Stage - Data Scientist » et « Data Scientist », sans rapprocher deux sujets
# différents (« Deep Learning » vs « Machine learning »).
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        # contrat / statut
        "stage", "stagiaire", "intern", "internship", "alternance", "apprenti",
        "apprentissage", "contrat", "cdi", "cdd",
        # fonction et genre
        "ingenieur", "ingenieure", "engineer", "e", "h", "f", "hf", "fh", "x",
        # mots de liaison
        "de", "du", "des", "d", "en", "et", "la", "le", "les", "un", "une", "pour",
        "sur", "au", "aux", "par", "avec", "dans", "chez", "a", "l",
        # durées
        "mois", "an", "ans",
    }
)

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str) -> str:
    """Minuscules, sans accents ni ponctuation, espaces normalisés."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD_RE.sub(" ", ascii_only).strip()


def significant_tokens(title: str) -> frozenset[str]:
    """Tokens porteurs de sens d'un titre (contrat, genre, liaisons, durées retirés)."""
    return frozenset(
        token for token in normalize_text(title).split() if token not in GENERIC_TOKENS
    )


def title_similarity(left: str, right: str) -> float:
    """Similarité [0, 1] de deux titres — insensible à l'ordre des mots et au genre.

    Indice de Jaccard sur les tokens porteurs de sens : « (F/H) » et « (H/F) »,
    « Stage - Data Scientist » et « Data Scientist » valent 1,0, alors que
    « … Deep Learning (F/H) » et « … Machine learning (F/H) » tombent à 0,33.
    """
    first, second = significant_tokens(left), significant_tokens(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    return len(first & second) / len(first | second)


def completeness(job: dict[str, Any]) -> tuple[int, int, float]:
    """Clé de choix de « la fiche la plus complète » (description, LLM, score)."""
    return (
        len((job.get("description") or "").strip()),
        1 if job.get("rerank_score") is not None else 0,
        float(job.get("final_score") or 0.0),
    )


def find_duplicate_groups(
    jobs: Iterable[dict[str, Any]], threshold: float = TITLE_SIMILARITY_THRESHOLD
) -> list[list[dict[str, Any]]]:
    """Groupes de doublons (≥ 2 offres) parmi ``jobs``."""
    job_list = list(jobs)
    parent = list(range(len(job_list)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    by_url: dict[str, int] = {}
    by_company: dict[str, list[int]] = {}
    for index, job in enumerate(job_list):
        url = canonical_url(str(job.get("url") or ""))
        if url:
            if url in by_url:
                union(index, by_url[url])
            else:
                by_url[url] = index

        company = normalize_text(str(job.get("company") or ""))
        if not company:
            continue
        title = str(job.get("title") or "")
        for other in by_company.get(company, []):
            if title_similarity(title, str(job_list[other].get("title") or "")) >= threshold:
                union(index, other)
        by_company.setdefault(company, []).append(index)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, job in enumerate(job_list):
        groups.setdefault(find(index), []).append(job)
    return [group for group in groups.values() if len(group) > 1]


def choose_keeper(
    group: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retourne ``(fiche conservée, fiches à supprimer)`` pour un groupe de doublons."""
    ordered = sorted(group, key=completeness, reverse=True)
    return ordered[0], ordered[1:]
