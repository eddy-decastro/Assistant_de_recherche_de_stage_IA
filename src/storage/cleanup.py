"""Hygiène de la base : dédoublonnage des offres collectées.

Les plateformes exposent souvent la même offre sous plusieurs URLs (paramètres de
suivi, rediffusion, slug différent d'un run à l'autre, publication croisée entre
LinkedIn, JobTeaser, Welcome to the Jungle, etc.). Ce module identifie ces
groupes et désigne la fiche à conserver — la plus complète — sans rien connaître de
la base : les fonctions sont pures, donc directement testables.

Règles de regroupement (au choix, cumulatives) :
  1. même **URL canonique** (minuscules, sans query/fragment ni slash final) ;
  2. même **entreprise normalisée** (ou marque mère / filiale correspondante) ET titres **équivalents** (voir ``title_similarity``).

La comparaison de titres est *ensembliste* (tokens porteurs de sens, Jaccard) et non
caractère à caractère : c'est indispensable ici, « (F/H) » et « (H/F) » ne diffèrent
que par l'ordre de deux lettres, et les mentions d'études ou de durée (« PFE »,
« 6 mois », « Bac+5 ») sont neutralisées.
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

# Seuil de similarité (0-1) au-delà duquel deux offres d'une même entreprise (ou groupe)
# sont considérées comme la même annonce. Calibré à 0.70 pour autoriser les variations
# d'intitulé multi-plateformes (ex. ajout de "IA Générative", "Deep Learning", mentions PFE/césure).
TITLE_SIMILARITY_THRESHOLD = 0.70

# Tokens qui ne distinguent PAS deux offres : type de contrat, niveau de diplôme,
# mentions de genre/diversité, mots de liaison et durées.
GENERIC_TOKENS: frozenset[str] = frozenset(
    {
        # contrat / statut / cursus
        "stage", "stagiaire", "intern", "internship", "alternance", "apprenti",
        "apprentissage", "contrat", "cdi", "cdd", "pfe", "fin", "etudes", "cesure",
        # diplôme / niveau
        "master", "bac", "bac5", "bac+5", "m1", "m2",
        # fonction et genre / équité
        "ingenieur", "ingenieure", "engineer", "e", "h", "f", "hf", "fh", "x",
        "m", "w", "d", "mwd", "hfx", "fhx",
        # mots de liaison
        "de", "du", "des", "d", "en", "et", "la", "le", "les", "un", "une", "pour",
        "sur", "au", "aux", "par", "avec", "dans", "chez", "a", "l",
        # durées & chiffres récurrents
        "mois", "an", "ans", "6", "5", "4",
    }
)

# Suffixes et termes d'entreprise parasites :
# Formes juridiques, structures de groupe, divisions fonctionnelles ou géographiques.
COMPANY_PARASITE_TOKENS: frozenset[str] = frozenset(
    {
        # Formes juridiques (France & international)
        "sa", "sas", "sasu", "sarl", "snc", "sci", "scop", "eurl", "ei",
        "inc", "corp", "corporation", "ltd", "limited", "llc", "gmbh", "ag",
        "plc", "bv", "nv", "spa", "holding", "holdings", "cie", "co",
        # Groupes & périmètre géographique
        "group", "groupe", "france", "europe", "emea", "apac", "international",
        "global", "corporate", "worldwide", "national",
        # Divisions, branches & filiales génériques
        "cib", "dis", "services", "service", "solutions", "solution",
        "systems", "systemes", "system", "systeme",
        "technologies", "technology", "tech", "consulting", "advisory",
        "digital", "innovation", "innovations", "lab", "labs", "rd",
        # Filiales industrielles spécifiques et divisions métier
        "helicopters", "helicopteres", "aerospace", "defence", "defense", "space",
        "aviation", "energy", "energies", "mobility", "transport", "security",
        "securite",
        # Mots de liaison d'entreprise
        "and", "et",
    }
)

# Alias et correspondances filiales / marques mères directes
KNOWN_COMPANY_ALIASES: dict[str, str] = {
    # Crédit Agricole
    "credit agricole cib": "credit agricole",
    "credit agricole corporate and investment bank": "credit agricole",
    "groupe credit agricole": "credit agricole",
    # Thales
    "thales dis": "thales",
    "thales alenia space": "thales",
    "thales group": "thales",
    "groupe thales": "thales",
    # Airbus
    "airbus helicopters": "airbus",
    "airbus defence and space": "airbus",
    "airbus defense and space": "airbus",
    "airbus commercial aircraft": "airbus",
    "airbus group": "airbus",
    "groupe airbus": "airbus",
    # BNP Paribas
    "bnp paribas cib": "bnp paribas",
    "bnp paribas cardif": "bnp paribas",
    "bnp paribas personal finance": "bnp paribas",
    "groupe bnp paribas": "bnp paribas",
    # Société Générale
    "societe generale cib": "societe generale",
    "sg cib": "societe generale",
    "groupe societe generale": "societe generale",
    "societe generale corporate and investment banking": "societe generale",
    # TotalEnergies / Total
    "totalenergies": "total",
    "total energies": "total",
    "groupe total": "total",
    # SNCF
    "sncf voyageurs": "sncf",
    "sncf connect": "sncf",
    "sncf reseau": "sncf",
    "groupe sncf": "sncf",
}

_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def normalize_text(text: str) -> str:
    """Minuscules, sans accents ni ponctuation, espaces normalisés."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    ascii_only = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD_RE.sub(" ", ascii_only).strip()


def normalize_company(company: str) -> str:
    """Normalisation avancée du nom d'entreprise pour le dédoublonnage multi-plateformes.

    1. Suppression de la casse, des accents et de la ponctuation (via ``normalize_text``) ;
    2. Retrait des suffixes juridiques, termes géographiques et divisions parasites ;
    3. Rattachement des filiales aux marques mères communes.
    """
    raw = normalize_text(company)
    if not raw:
        return ""

    if raw in KNOWN_COMPANY_ALIASES:
        return KNOWN_COMPANY_ALIASES[raw]

    tokens = [t for t in raw.split() if t not in COMPANY_PARASITE_TOKENS]

    # Repli si tous les tokens ont été filtrés (ex: "Services") ou s'il ne reste que des chiffres
    if not tokens or all(t.isdigit() for t in tokens):
        cleaned = raw
    else:
        cleaned = " ".join(tokens).strip()

    if cleaned in KNOWN_COMPANY_ALIASES:
        return KNOWN_COMPANY_ALIASES[cleaned]

    return cleaned


def companies_match(comp_a: str, comp_b: str) -> bool:
    """Vérifie si deux entreprises correspondent (exactement ou par inclusion de marque)."""
    c1 = normalize_company(comp_a)
    c2 = normalize_company(comp_b)
    if not c1 or not c2:
        return False
    if c1 == c2:
        return True

    # Inclusion de marque : la marque la plus courte forme le préfixe de mots de la plus longue
    t1 = c1.split()
    t2 = c2.split()
    if len(t1) < len(t2):
        shorter, longer = t1, t2
    elif len(t2) < len(t1):
        shorter, longer = t2, t1
    else:
        return False

    # Le préfixe coïncide mot pour mot et totalise au moins 3 caractères
    if longer[:len(shorter)] == shorter:
        if sum(len(w) for w in shorter) >= 3:
            return True

    return False


def significant_tokens(title: str) -> frozenset[str]:
    """Tokens porteurs de sens d'un titre (contrat, genre, liaisons, durées retirés)."""
    return frozenset(
        token for token in normalize_text(title).split() if token not in GENERIC_TOKENS
    )


def title_similarity(left: str, right: str) -> float:
    """Similarité [0, 1] de deux titres — insensible à l'ordre des mots et au genre.

    Indice de Jaccard sur les tokens porteurs de sens : « (F/H) » et « (H/F) »,
    « Stage - Data Scientist » et « Data Scientist » valent 1,0, alors que
    « … Deep Learning (F/H) » et « … Machine learning (F/H) » tombent à 0,50.
    """
    first, second = significant_tokens(left), significant_tokens(right)
    if not first or not second:
        return 0.0
    if first == second:
        return 1.0
    return len(first & second) / len(first | second)


def completeness(job: dict[str, Any]) -> tuple[int, int, float, float]:
    """Clé de choix de « la fiche la plus complète » (description, LLM, score R&D).

    Ordre de priorité :
      1. Longueur de la description textuelle (la plus riche / documentée) ;
      2. Présence d'une évaluation par le juge LLM (1 si rerank_score != None, sinon 0) ;
      3. Score R&D effectif (rerank_score si disponible, sinon final_score) ;
      4. Score initial final_score en départage.
    """
    rerank = job.get("rerank_score")
    has_rerank = 1 if rerank is not None else 0
    final_score = float(job.get("final_score") or 0.0)
    rd_score = float(rerank) if rerank is not None else final_score
    desc_len = len((job.get("description") or "").strip())
    return (desc_len, has_rerank, rd_score, final_score)


def choose_keeper(
    group: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Retourne ``(fiche conservée, fiches à supprimer)`` pour un groupe de doublons.

    La fiche conservée est la plus complète selon ``completeness``.
    Si le keeper n'a pas été évalué par le juge LLM alors qu'un doublon l'a été,
    ses attributs qualitatifs sont enrichis à partir de ce dernier pour éviter
    toute perte d'information.
    """
    ordered = sorted(group, key=completeness, reverse=True)
    keeper = dict(ordered[0])
    duplicates = ordered[1:]

    # Préservation de l'évaluation LLM si le keeper en est dépourvu
    if keeper.get("rerank_score") is None:
        for dup in duplicates:
            if dup.get("rerank_score") is not None:
                for key in (
                    "rerank_score",
                    "verdict",
                    "match_reasons",
                    "red_flags",
                    "tech_stack",
                    "sub_scores",
                    "hard_cap_triggered",
                    "reasoning",
                ):
                    if key in dup and dup[key] is not None:
                        keeper[key] = dup[key]
                break

    return keeper, duplicates


def find_duplicate_groups(
    jobs: Iterable[dict[str, Any]], threshold: float = TITLE_SIMILARITY_THRESHOLD
) -> list[list[dict[str, Any]]]:
    """Groupes de doublons (≥ 2 offres) parmi ``jobs``.

    Regroupe les offres qui partagent :
      1. la même URL canonique ;
      2. OU la même entreprise (ou groupe/marque mère) ET un titre équivalent
         (similarité de Jaccard >= threshold).
    """
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
        # 1. Regroupement par URL canonique
        url = canonical_url(str(job.get("url") or ""))
        if url:
            if url in by_url:
                union(index, by_url[url])
            else:
                by_url[url] = index

        # 2. Regroupement par entreprise (normalisée / marque mère) et similarité de titre
        company_raw = str(job.get("company") or "")
        company_norm = normalize_company(company_raw)
        if not company_norm:
            continue

        title = str(job.get("title") or "")

        # Recherche de toutes les offres d'entreprises correspondantes (exact ou inclusion de marque)
        for other_comp, indices in by_company.items():
            if companies_match(company_norm, other_comp):
                for other in indices:
                    if find(index) == find(other):
                        continue
                    other_title = str(job_list[other].get("title") or "")
                    if title_similarity(title, other_title) >= threshold:
                        union(index, other)

        by_company.setdefault(company_norm, []).append(index)

    groups: dict[int, list[dict[str, Any]]] = {}
    for index, job in enumerate(job_list):
        groups.setdefault(find(index), []).append(job)
    return [group for group in groups.values() if len(group) > 1]
