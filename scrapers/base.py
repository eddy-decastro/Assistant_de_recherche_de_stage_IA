"""Classe de base des scrapers : client HTTP partagé, filtrage métier, moteur hybride.

Fournit :

* ``BaseScraper`` (classe abstraite) et son client ``httpx`` pré-configuré
  (timeout, User-Agent moderne, redirections) ;
* le filtrage métier ``is_valid_job`` / ``validate`` (exclusion stricte des offres
  BI/reporting + exigence d'un signal DS/ML) ;
* le **moteur de collecte hybride** (``collect``) : pour chaque source et chaque
  requête cible, une passe « Fraîcheur » (tri par date, filtre temporel serveur,
  arrêt anticipé) puis une passe « Rattrapage » (tri par pertinence, aucun arrêt
  anticipé). Chaque source ne fournit plus que ``_iter_pages`` : le quota, la
  déduplication transverse, la fenêtre temporelle, l'arrêt anticipé et la
  consignation de la **raison exacte d'arrêt** sont mutualisés ici — ils
  s'appliquent donc identiquement à toutes les plateformes.
"""

from __future__ import annotations

import logging
import os
import re
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Sequence

import httpx
from bs4 import BeautifulSoup

try:
    from curl_cffi.requests.errors import RequestsError as CurlRequestsError
except ImportError:
    CurlRequestsError = None  # curl_cffi absent : le guard ne sera jamais atteint

from .cache import DiskCache
from .http import RetryingClient
from .known import KnownIndex, NullKnownIndex
from .models import (
    MIN_WEAK_SIGNALS_IN_DESCRIPTION,
    PASS_FRESHNESS,
    SATURATION_RATIO,
    SEEN_DUPLICATE,
    SEEN_KNOWN,
    SEEN_OUT_OF_WINDOW,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_VALIDATED,
    CardEntry,
    PageResult,
    PassPlan,
    PassReport,
    RawJob,
    ScrapeResult,
    ScraperConfig,
    SeenEntry,
    Source,
    canonical_url,
    is_incomplete_stop,
)


def load_env_file(path: str | Path | None = None) -> None:
    """Charge les variables d'un fichier ``.env`` dans ``os.environ`` (sans écraser).

    Implémentation minimale et autonome (pas de dépendance à python-dotenv).
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _contains_keyword(text: str, keyword: str) -> bool:
    """Recherche un mot-clé de façon tolérante (frontières de mot pour les tokens simples).

    Les expressions multi-mots sont cherchées en sous-chaîne après normalisation
    des espaces ; les tokens simples (``ia``, ``ai``, ``vba``, ``qlik``…) utilisent
    une frontière de mot insensible aux accents et caractères alphanumériques
    pour éviter les faux positifs (ex. ``ia`` dans ``dialogue`` ou ``initial``).
    """
    kw = re.sub(r"\s+", " ", keyword.casefold()).strip()
    lowered = re.sub(r"\s+", " ", text.casefold())
    if " " in kw:
        return kw in lowered
    return re.search(rf"(?<![a-zA-Z0-9À-ÿ]){re.escape(kw)}(?![a-zA-Z0-9À-ÿ])", lowered) is not None


def markup_to_text(node: Any, separator: str = "\n") -> str:
    """Convertit un fragment HTML (chaîne ou balise BeautifulSoup) en texte lisible.

    Les ``<br>`` deviennent des sauts de ligne et les blocs (``<p>``, ``<li>``,
    ``<div>``…) sont séparés par ``separator`` ; les lignes vides consécutives
    sont supprimées. Indispensable en aval : la description part telle quelle
    vers le modèle d'embedding puis vers le juge LLM, elle doit rester structurée.
    """
    parsed = BeautifulSoup(node, "lxml") if isinstance(node, str) else node
    for br in parsed.find_all("br"):
        br.replace_with("\n")
    raw = parsed.get_text(separator, strip=True)
    lines = [line.strip() for line in raw.splitlines()]
    return separator.join(line for line in lines if line)


# --- Re-validation métier sur texte COMPLET (titre + description) ------------ #
# Marqueurs explicites de contrat NON-stage. Deux niveaux :
#   « durs »  : la mention suffit à écarter l'offre (freelance, alternance exclusive…) ;
#   « doux »  : la mention n'écarte que si le TITRE n'annonce pas déjà un stage
#               (une annonce « Stage … possibilité de CDI à l'issue » reste un stage).
NON_INTERNSHIP_HARD_MARKERS: tuple[str, ...] = (
    "freelance",
    "alternance uniquement",
    "uniquement en alternance",
    "alternance exclusivement",
    "apprentissage uniquement",
    "rythme 3j/2j",
    "rythme 2j/3j",
    "rythme 3 jours / 2 jours",
    "contrat cdi",
    "poste en cdi",
    "cdi uniquement",
)
NON_INTERNSHIP_SOFT_MARKERS: tuple[str, ...] = (
    "cdi",
    "cdd",
    "alternance",
    "apprentissage",
)
# Signaux de stage recherchés dans le TITRE (ce que le candidat lit en premier).
INTERNSHIP_TITLE_CUES: tuple[str, ...] = (
    "stage",
    "stagiaire",
    "intern",
    "internship",
    "pfmp",
)


def describe_rejection(title: str, description: str, config: ScraperConfig) -> str:
    """Motif d'exclusion d'une offre lue en entier (``""`` ⇒ offre retenue).

    Complète :func:`BaseScraper.is_valid_job` (qui ne voit, à la collecte, que le
    titre et le résumé de la page de liste) en appliquant les règles sur le texte
    COMPLET récupéré depuis les pages détail :

      1. contrat incompatible explicitement mentionné (freelance, CDI, alternance
         exclusive, rythme 3j/2j…) ;
      2. orientation BI / reporting détectée dans le corps de l'offre ;
      3. absence de tout signal Data Science / ML.
    """
    title_low = re.sub(r"\s+", " ", (title or "").casefold()).strip()
    description_low = re.sub(r"\s+", " ", (description or "").casefold()).strip()
    combined = f"{title_low}\n{description_low}"
    announced_as_internship = any(cue in title_low for cue in INTERNSHIP_TITLE_CUES)

    for marker in NON_INTERNSHIP_HARD_MARKERS:
        if marker in combined:
            return f"contrat incompatible (« {marker} »)"

    if not announced_as_internship:
        for marker in NON_INTERNSHIP_SOFT_MARKERS:
            if _contains_keyword(combined, marker):
                return f"contrat incompatible (« {marker} »), non annoncé comme un stage"

    return _business_rejection(title_low, description_low, config, where="la fiche")


def _split_positive_keywords(config: ScraperConfig) -> tuple[list[str], list[str]]:
    """Sépare les mots-clés positifs en signaux ``(forts, faibles)``."""
    weak_set = {keyword.casefold().strip() for keyword in config.weak_ds_ml_keywords}
    strong: list[str] = []
    weak: list[str] = []
    for keyword in config.positive_ds_ml_keywords:
        (weak if keyword.casefold().strip() in weak_set else strong).append(keyword)
    return strong, weak


def _business_rejection(
    title_low: str, description_low: str, config: ScraperConfig, *, where: str
) -> str:
    """Règles métier communes (exclusions puis signal DS/ML) sur texte normalisé.

    1. **Exclusion dans le titre** : rédhibitoire (« Stage Data Analyst Power BI ») ;
    2. **Exclusion dans la description** : rédhibitoire seulement si le vocabulaire
       exclu y est *au moins aussi présent* que les signaux DS/ML forts (titre +
       description). Une offre de Data Scientist qui cite « Tableau » dans sa stack
       ou « nos data analysts » reste retenue ; une offre dont le corps ne parle que
       de Power BI et de reporting est écartée ;
    3. **Signal DS/ML** : un signal fort n'importe où, OU un signal faible dans le
       titre, OU au moins ``MIN_WEAK_SIGNALS_IN_DESCRIPTION`` signaux faibles
       distincts dans la description.
    """
    for keyword in config.exclusion_keywords:
        if _contains_keyword(title_low, keyword):
            return f"orientation BI / reporting (« {keyword} »)"

    strong, weak = _split_positive_keywords(config)
    combined = f"{title_low}\n{description_low}".strip()
    strong_hits = [keyword for keyword in strong if _contains_keyword(combined, keyword)]

    if description_low:
        excluded_hits = [
            keyword
            for keyword in config.exclusion_keywords
            if _contains_keyword(description_low, keyword)
        ]
        if excluded_hits and len(excluded_hits) >= len(strong_hits):
            return (
                f"orientation BI / reporting (« {excluded_hits[0]} » dans la description, "
                f"{len(excluded_hits)} terme(s) exclu(s) pour {len(strong_hits)} signal(aux) "
                "DS/ML fort(s))"
            )

    if strong_hits:
        return ""
    if any(_contains_keyword(title_low, keyword) for keyword in weak):
        return ""
    weak_in_description = {
        keyword.casefold() for keyword in weak if _contains_keyword(description_low, keyword)
    }
    if len(weak_in_description) >= MIN_WEAK_SIGNALS_IN_DESCRIPTION:
        return ""
    return f"aucun signal Data Science / ML dans {where}"


def screen_rejection(title: str, description: str, config: ScraperConfig) -> str:
    """Motif de rejet d'une offre selon le filtre de collecte (``""`` = retenue).

    Applique les règles métier communes (voir :func:`_business_rejection`) et
    retourne le motif : c'est lui qui est archivé dans la mémoire de collecte
    (``seen_jobs.rejection_reason``) pour l'observabilité.

    À ne pas confondre avec :func:`describe_rejection`, plus strict (marqueurs de
    contrat), réservé à la re-validation sur fiche complète (``--revalidate``).
    """
    title_low = re.sub(r"\s+", " ", (title or "").casefold()).strip()
    description_low = re.sub(r"\s+", " ", (description or "").casefold()).strip()
    return _business_rejection(title_low, description_low, config, where="l'annonce")


def title_exclusion(title: str, config: ScraperConfig) -> str:
    """Pré-filtre sur le TITRE seul (``""`` = à approfondir).

    Utilisé pour les sources dont la page de liste n'expose pas la description
    (LinkedIn) : seules les exclusions explicites du titre écartent la carte avant
    la récupération de la fiche ; l'exigence d'un signal DS/ML est différée jusqu'à
    ce que la description soit connue (sinon un « Stagiaire – équipe Pricing » qui
    fait du ML serait perdu).
    """
    title_low = re.sub(r"\s+", " ", (title or "").casefold()).strip()
    for keyword in config.exclusion_keywords:
        if _contains_keyword(title_low, keyword):
            return f"orientation BI / reporting (« {keyword} »)"
    return ""


class BaseScraper(ABC):
    """Contrat commun à tous les scrapers : collecte hybride + filtrage métier.

    Une source concrète n'implémente que ``_iter_pages`` (une page de résultats
    pour une requête et un mode) et déclare deux capacités **mesurées** :

    * ``DATE_ORDER_RELIABLE`` — l'ordre du flux est-il chronologique ? C'est la
      condition de validité de l'arrêt anticipé ;
    * ``SERVER_WINDOW_FILTER`` — la plateforme sait-elle filtrer la fraîcheur
      côté serveur (LinkedIn : ``f_TPR``) ?
    """

    source: ClassVar[Source]
    #: Ordre chronologique garanti par la plateforme ? LinkedIn : NON (mesuré le
    #: 16/09/2026 — 7 inversions de date sur 2 pages consécutives, voir
    #: ``tools/probe_sources.py``). Par défaut, on suppose le pire cas.
    DATE_ORDER_RELIABLE: ClassVar[bool] = False
    #: Filtre temporel serveur disponible ? LinkedIn : OUI (``f_TPR=r604800``
    #: ramène 100 % de cartes de la semaine, vérifié par sonde).
    SERVER_WINDOW_FILTER: ClassVar[bool] = False
    #: La page de liste fournit-elle une description ? Sinon (LinkedIn), la carte
    #: n'est pré-filtrée que sur son titre, puis la fiche détail est récupérée
    #: (``_fetch_detail``) avant d'appliquer le filtre métier complet.
    LIST_HAS_DESCRIPTION: ClassVar[bool] = True
    #: Échecs consécutifs de fiche détail désactivant l'enrichissement du run.
    MAX_CONSECUTIVE_DETAIL_FAILURES: ClassVar[int] = 3

    def __init__(self, config: ScraperConfig | None = None) -> None:
        self.config = config or ScraperConfig()
        self._logger = logging.getLogger(f"scrapers.{self.source}")
        #: Cache disque des descriptions (injecté par l'orchestrateur ; ``None`` =
        #: aucun cache, comportement des tests).
        self.detail_cache: DiskCache | None = None
        self._detail_calls = 0
        self._detail_failures = 0
        self._detail_disabled_reason = ""
        self._filter_version = self.config.filter_fingerprint()
        self.client = RetryingClient(
            max_retries=self.config.http_max_retries,
            backoff_seconds=self.config.http_backoff_seconds,
            max_wait_seconds=self.config.http_max_wait_seconds,
            headers={
                "User-Agent": self.config.user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
            },
            timeout=self.config.request_timeout_seconds,
            follow_redirects=True,
        )

    # ------------------------------------------------------------------ #
    # Filtrage métier
    # ------------------------------------------------------------------ #
    def is_valid_job(self, title: str, description: str) -> bool:
        """Retourne ``False`` si l'offre est BI/reporting, ``True`` si DS/ML.

        Règles :
          1. rejet immédiat si un mot-clé d'exclusion est présent dans le titre
             OU le résumé (BI, Power BI, data analyst, reporting…) ;
          2. acceptation seulement si au moins un signal positif Data Science /
             Machine Learning est détecté dans le titre ou le résumé.
        """
        return screen_rejection(title, description, self.config) == ""

    def validation_reason(self, job: RawJob) -> str:
        """Motif d'exclusion d'une offre (``""`` si elle est retenue)."""
        if not job.is_internship:
            return "contrat incompatible (hors stage)"
        return screen_rejection(job.title, job.description, self.config)

    def validate(self, job: RawJob) -> bool:
        """Filtre complet : offre de stage + anti-BI + signal DS/ML."""
        return self.validation_reason(job) == ""

    # ------------------------------------------------------------------ #
    # Filtre en deux temps : titre, puis fiche détail (sources sans description)
    # ------------------------------------------------------------------ #
    def _fetch_detail(self, job: RawJob) -> tuple[str, int]:
        """Description complète d'une offre : ``(texte, appels HTTP réseau)``.

        À surcharger par les sources dont la liste n'expose pas la description.
        Les erreurs réseau/HTTP sont laissées remonter (voir ``_screen_job``).
        """
        return "", 0

    def _disable_detail(self, reason: str) -> None:
        if not self._detail_disabled_reason:
            self._detail_disabled_reason = reason
            self._logger.warning(
                "%s : enrichissement des descriptions suspendu pour ce run (%s) — "
                "les offres restantes sont filtrées sur leur titre seul.",
                self.source,
                reason,
            )

    def _try_fetch_detail(self, job: RawJob) -> tuple[str, int]:
        """Récupère la fiche détail sans jamais interrompre la passe."""
        if self._detail_disabled_reason:
            return "", 0
        if self._detail_calls >= self.config.max_detail_fetches_per_source:
            self._disable_detail(
                f"plafond de {self.config.max_detail_fetches_per_source} fiche(s) détail atteint"
            )
            return "", 0
        try:
            description, calls = self._fetch_detail(job)
        except httpx.HTTPStatusError as exc:
            status = getattr(exc.response, "status_code", 0)
            self._detail_calls += 1
            if status == 429:
                self._disable_detail("HTTP 429 sur les fiches détail")
                return "", 1
            self._detail_failures += 1
            self._logger.debug("%s : fiche détail %s en échec (HTTP %s).", self.source, job.url, status)
        except Exception as exc:  # noqa: BLE001 — une fiche ne doit jamais tuer la passe
            self._detail_calls += 1
            self._detail_failures += 1
            self._logger.debug("%s : fiche détail %s en échec (%s).", self.source, job.url, exc)
        else:
            self._detail_calls += calls
            self._detail_failures = 0
            return description, calls
        if self._detail_failures >= self.MAX_CONSECUTIVE_DETAIL_FAILURES:
            self._disable_detail(f"{self._detail_failures} échecs consécutifs")
        return "", 1

    def _screen_job(self, job: RawJob) -> tuple[str, RawJob, int, bool]:
        """Filtre métier d'une carte inédite : ``(motif, offre, appels HTTP, définitif)``.

        Pour une source sans description en liste, la carte est d'abord pré-filtrée
        sur son titre (exclusions explicites), puis la fiche détail est récupérée et
        le filtre complet est appliqué sur le texte entier. Si la fiche n'a pas pu
        être lue, le filtre retombe sur le titre seul et le rejet éventuel est
        marqué **non définitif** : il sera réévalué au run suivant.
        """
        if not job.is_internship:
            return "contrat incompatible (hors stage)", job, 0, True
        needs_detail = (
            not self.LIST_HAS_DESCRIPTION
            and self.config.enrich_descriptions
            and not (job.description or "").strip()
        )
        if not needs_detail:
            return screen_rejection(job.title, job.description, self.config), job, 0, True

        pre_rejection = title_exclusion(job.title, self.config)
        if pre_rejection:
            return pre_rejection, job, 0, True
        description, calls = self._try_fetch_detail(job)
        if description:
            job = job.model_copy(update={"description": description})
        rejection = screen_rejection(job.title, job.description, self.config)
        return rejection, job, calls, bool(description)

    # ------------------------------------------------------------------ #
    # Source : une page de résultats
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _iter_pages(self, query: str, mode: str, cursor: Any, plan: PassPlan) -> PageResult:
        """Récupère UNE page de résultats pour ``(query, mode)``.

        ``cursor`` est le curseur renvoyé par la page précédente (``None`` pour la
        première) ; ``plan`` porte les paramètres résolus de la passe (tri, fenêtre
        temporelle, filtre serveur…). Les erreurs réseau/HTTP sont laissées
        remonter : le moteur les traduit en motif d'arrêt.

        Aucun état interne : la source ne décide ni du quota, ni de l'arrêt.
        """

    def unavailable_reason(self) -> str:
        """Motif d'indisponibilité de la source (``""`` si elle est disponible).

        JobTeaser surcharge ce point : sans cookies, la source est inactive et
        chaque passe doit être consignée comme telle plutôt que silencieusement vide.
        """
        return ""

    # ------------------------------------------------------------------ #
    # Moteur de collecte hybride
    # ------------------------------------------------------------------ #
    def collect(
        self,
        known_index: KnownIndex | None = None,
        *,
        validate_jobs: bool = False,
        modes: Sequence[str] | None = None,
        queries: Sequence[str] | None = None,
        on_batch_collected: Any = None,
    ) -> ScrapeResult:
        """Exécute les passes demandées et retourne offres + télémétrie.

        ``validate_jobs`` applique le filtre métier à chaque carte retenue (chemin
        de production) ; à ``False`` (``fetch``), les offres sont renvoyées brutes.
        """
        index = known_index or NullKnownIndex()
        result = ScrapeResult()
        #: Clés croisées pendant CE run (toutes passes et toutes requêtes confondues).
        #: Ensemble intrinsèque au moteur : la déduplication transverse ne dépend donc
        #: pas de l'index injecté (un run sans mémoire reste correct).
        run_keys: set[str] = set()
        selected_modes = list(modes) if modes else self.config.enabled_modes()
        selected_queries = list(queries) if queries else list(self.config.target_queries)
        #: Nouvelles offres déjà retenues, par passe : avancement de l'objectif de
        #: source (« 40 dernières » en Fraîcheur, « 10 plus pertinentes » en
        #: Rattrapage). Atteint, il interrompt la passe sans lancer les requêtes
        #: restantes — chacune étant consignée en télémétrie.
        kept_per_mode: dict[str, int] = {mode: 0 for mode in selected_modes}
        reason = self.unavailable_reason()
        if reason:
            self._logger.warning(
                "%s : source indisponible pour ce run (%s) — passes consignées, pipeline non bloqué.",
                self.source,
                reason,
            )
        for mode_index, mode in enumerate(selected_modes):
            if not self.config.pass_config(mode).enabled:
                result.query_reports.extend(
                    self._skipped_reports(mode, selected_queries, "disabled", "passe désactivée")
                )
                continue
            if reason:
                result.query_reports.extend(
                    self._skipped_reports(mode, selected_queries, "auth_missing", reason)
                )
                continue
            plan = self.config.build_plan(
                mode,
                date_order_reliable=self.DATE_ORDER_RELIABLE,
                server_window_filter=self.SERVER_WINDOW_FILTER,
            )
            self._logger.info(
                "%s : passe « %s » — tri=%s, quota=%d/requête, objectif=%s, fenêtre=%s, "
                "arrêt anticipé=%s",
                self.source,
                mode,
                plan.sort,
                plan.quota,
                f"{plan.target_new} nouvelle(s)/source" if plan.target_new else "aucun",
                f"{plan.window_days:g} j" if plan.window_days else "aucune",
                plan.early_stop_threshold or "désactivé",
            )
            if plan.notes:
                self._logger.info("%s : garde-fous appliqués — %s", self.source, plan.notes)
            # Part du plafond de source RÉSERVÉE aux passes suivantes : la passe en
            # cours ne peut pas la consommer, sinon elle affamerait silencieusement
            # l'objectif de rattrapage (« les 10 plus pertinentes »).
            reserve = sum(
                self.config.pass_config(later).target_new
                for later in selected_modes[mode_index + 1 :]
            )
            for query in selected_queries:
                if plan.target_new and kept_per_mode[mode] >= plan.target_new:
                    # Objectif de la source atteint : les requêtes restantes ne sont pas
                    # lancées, mais elles sont CONSIGNÉES (jamais d'arrêt silencieux).
                    self._logger.info(
                        "%s : objectif de la passe « %s » atteint (%d/%d) — requête %r non lancée.",
                        self.source,
                        mode,
                        kept_per_mode[mode],
                        plan.target_new,
                        query,
                    )
                    result.query_reports.extend(
                        self._skipped_reports(
                            mode,
                            [query],
                            "quota",
                            f"objectif de {plan.target_new} nouvelle(s) pour la source déjà "
                            f"atteint ({kept_per_mode[mode]}) — requête non lancée",
                            target_new=plan.target_new,
                        )
                    )
                    continue
                # Budget restant de la source, réserve des passes suivantes déduite :
                # le plafond global est appliqué *pendant* la passe (sinon chaque
                # requête pourrait consommer son quota entier et le dépasser).
                budget = self.config.max_offers_per_source - len(result.jobs) - reserve
                if budget <= 0:
                    self._logger.info(
                        "%s : plafond de source atteint (%d, dont %d réservé(s)) — requête %r "
                        "non lancée.",
                        self.source,
                        self.config.max_offers_per_source,
                        reserve,
                        query,
                    )
                    result.query_reports.extend(
                        self._skipped_reports(
                            mode,
                            [query],
                            "quota",
                            f"plafond de source ({self.config.max_offers_per_source}) atteint ou "
                            f"réservé aux passes suivantes ({reserve}) — requête non lancée",
                            target_new=plan.target_new,
                        )
                    )
                    continue
                report, jobs, seen = self._collect_pass(
                    query, mode, plan, index, run_keys, budget, validate=validate_jobs
                )
                result.query_reports.append(report)
                result.seen.extend(seen)
                result.jobs.extend(jobs)
                result.found += report.cards_seen
                result.rejected_bi += report.jobs_rejected
                kept_per_mode[mode] += len(jobs)
                if on_batch_collected is not None and jobs:
                    try:
                        on_batch_collected(jobs)
                    except Exception as exc:  # pragma: no cover
                        self._logger.warning("Erreur dans on_batch_collected : %s", exc)
            if plan.target_new:
                reached = kept_per_mode[mode]
                self._logger.info(
                    "%s : passe « %s » — objectif de %d nouvelle(s) : %d retenue(s)%s",
                    self.source,
                    mode,
                    plan.target_new,
                    reached,
                    ""
                    if reached >= plan.target_new
                    else " (objectif non atteint : voir les motifs d'arrêt ci-dessus)",
                )
        self._log_reports(result.query_reports)
        if self._detail_calls:
            self._logger.info(
                "%s : %d fiche(s) détail récupérée(s) pour filtrer sur la description complète%s.",
                self.source,
                self._detail_calls,
                f" (enrichissement suspendu : {self._detail_disabled_reason})"
                if self._detail_disabled_reason
                else "",
            )
        return result

    def fetch(self, known_index: KnownIndex | None = None) -> ScrapeResult:
        """Collecte brute (sans filtre métier) — tests, sondes, inspection."""
        return self.collect(known_index, validate_jobs=False)

    def run(
        self,
        known_index: KnownIndex | None = None,
        *,
        modes: Sequence[str] | None = None,
        queries: Sequence[str] | None = None,
        on_batch_collected: Any = None,
    ) -> ScrapeResult:
        """Collecte hybride + filtre métier : chemin de production."""
        return self.collect(
            known_index,
            validate_jobs=True,
            modes=modes,
            queries=queries,
            on_batch_collected=on_batch_collected,
        )

    # ------------------------------------------------------------------ #
    # Une passe complète pour une requête cible
    # ------------------------------------------------------------------ #
    def _collect_pass(
        self,
        query: str,
        mode: str,
        plan: PassPlan,
        index: KnownIndex,
        run_keys: set[str],
        budget: int,
        *,
        validate: bool,
    ) -> tuple[PassReport, list[RawJob], list[SeenEntry]]:
        """Déroule une passe jusqu'à son motif d'arrêt, télémétrie à l'appui.

        Invariants garantis ici, pour toutes les sources :

        * une carte **déjà connue** ne consomme pas le quota, mais alimente le
          compteur d'arrêt anticipé (N connues **consécutives**) ;
        * toute carte inconnue remet ce compteur à zéro ;
        * une offre antérieure à la fenêtre temporelle est écartée — et arrête la
          passe uniquement si l'ordre du flux est jugé fiable ;
        * en passe « Fraîcheur », une page sans aucune carte inédite arrête la
          passe (duplicate_page : pagination stagnante) ; en passe « Rattrapage »
          (relevance), la passe poursuit sa pagination malgré des pages de doublons.
        """
        started = datetime.now(timezone.utc)
        counters = {
            "pages_fetched": 0,
            "http_requests": 0,
            "cards_seen": 0,
            "jobs_kept": 0,
            "jobs_known": 0,
            "jobs_duplicate": 0,
            "jobs_rejected": 0,
            "jobs_out_of_window": 0,
        }
        kept: list[RawJob] = []
        seen: list[SeenEntry] = []
        # Quota effectif : le plus contraignant entre le quota de la passe, l'OBJECTIF
        # de la source (« les 40 dernières » en Fraîcheur, « les 10 plus pertinentes »
        # en Rattrapage) et le budget restant de la source — réserve des passes
        # suivantes déjà déduite par ``collect``.
        effective_quota = (
            min(plan.quota, plan.target_new) if plan.target_new else plan.quota
        )
        budget_available = int(budget)
        limit = max(0, min(effective_quota, budget_available))
        if budget_available < effective_quota:
            limit_detail = (
                f"plafond de la source atteint ({limit} offre(s) disponible(s), "
                "réserve des passes suivantes déduite)"
            )
        elif plan.target_new and effective_quota == plan.target_new:
            limit_detail = (
                f"objectif de la passe atteint ({plan.target_new} nouvelle(s) pour la source)"
            )
        else:
            limit_detail = f"quota de la passe atteint ({plan.quota})"
        stop_reason = "stream_end"
        stop_detail = ""
        stop_page: int | None = None
        error: str | None = None
        streak = 0
        #: Ventilation de la série courante : la télémétrie doit dire si l'arrêt
        #: anticipe vient de la mémoire de collecte (jonction avec le scrape
        #: précédent) ou de doublons internes au run (pagination stagnante).
        streak_known = 0
        streak_duplicates = 0
        newest: datetime | None = None
        oldest: datetime | None = None
        cursor: Any = None
        page_number = 0
        halt = False
        #: Part d'offres déjà vues sur la dernière page traitée (voir SATURATION_RATIO).
        last_page_seen_ratio = 0.0

        while page_number < plan.max_pages:
            if len(kept) >= limit:  # limite atteinte (quota de passe ou de source)
                stop_reason = "quota"
                stop_detail = limit_detail
                break
            page_number += 1
            try:
                page = self._iter_pages(query, mode, cursor, plan)
            except httpx.HTTPStatusError as exc:
                status = getattr(exc.response, "status_code", 0)
                if status == 429:
                    stop_reason, stop_detail = "rate_limit", "HTTP 429 (quota plateforme)"
                else:
                    stop_reason, stop_detail = "http_error", f"HTTP {status}"
                error = stop_detail
                break
            except httpx.RequestError as exc:
                stop_reason, stop_detail = "network_error", type(exc).__name__
                error = str(exc)[:300]
                break
            except Exception as exc:  # noqa: BLE001 — aucune passe ne doit tuer le run
                if CurlRequestsError is not None and isinstance(exc, CurlRequestsError):
                    stop_reason, stop_detail = "network_error", type(exc).__name__
                else:
                    stop_reason, stop_detail = "error", f"{type(exc).__name__}: {exc}"[:300]
                error = str(exc)[:300]
                break

            counters["http_requests"] += max(0, int(page.http_calls))
            if not page.entries:
                stop_reason = "stream_end"
                stop_detail = f"plus de résultats (page {page_number})"
                break

            counters["pages_fetched"] += 1
            stop_page = page_number
            new_keys_in_page = 0
            duplicates_in_page = 0
            seen_in_page = 0

            for entry in page.entries:
                counters["cards_seen"] += 1
                job = entry.job
                key = entry.key or (job.id_externe if job else "") or (job.url if job else "")
                url = job.url if job else ""
                title = job.title if job else ""
                canon = canonical_url(url)
                published = job.published_at if job else None
                if published is not None:
                    newest = published if newest is None else max(newest, published)
                    oldest = published if oldest is None else min(oldest, published)

                # 1) Fenêtre temporelle : filet de sécurité local quand la
                #    plateforme ne filtre pas la fraîcheur côté serveur.
                if (
                    published is not None
                    and plan.window_deadline is not None
                    and published < plan.window_deadline
                ):
                    counters["jobs_out_of_window"] += 1
                    seen.append(
                        SeenEntry(
                            source=self.source,
                            external_key=str(key),
                            canonical_url=canon,
                            title=title,
                            decision=SEEN_OUT_OF_WINDOW,
                        )
                    )
                    if plan.stop_on_window:
                        stop_reason = "window_end"
                        stop_detail = (
                            f"offre publiée le {published.date().isoformat()} "
                            f"hors fenêtre de {plan.window_days:g} j"
                        )
                        halt = True
                        break
                    continue

                #    — c'est la jonction avec le scrape précédent.
                already_in_run = bool(key) and str(key) in run_keys
                if str(key) and (index.is_known(self.source, str(key), url) or already_in_run):
                    if already_in_run:
                        counters["jobs_duplicate"] += 1
                        streak_duplicates += 1
                        duplicates_in_page += 1
                    else:
                        counters["jobs_known"] += 1
                        streak_known += 1
                        seen.append(
                            SeenEntry(
                                source=self.source,
                                external_key=str(key),
                                canonical_url=canon,
                                title=title,
                                decision=SEEN_KNOWN,
                            )
                        )
                    streak += 1
                    seen_in_page += 1
                    if (
                        plan.early_stop_threshold
                        and counters["pages_fetched"] >= plan.early_stop_min_pages
                        and streak >= plan.early_stop_threshold
                    ):
                        stop_reason = "early_stop"
                        stop_detail = (
                            f"{streak} offre(s) consécutive(s) déjà vue(s) "
                            f"({streak_known} en mémoire de collecte, "
                            f"{streak_duplicates} doublon(s) du run) : le flux a rejoint "
                            f"ce qui est déjà connu "
                            f"(seuil {plan.early_stop_threshold}, page {page_number})"
                        )
                        halt = True
                        break
                    continue

                # 3) Carte inédite : elle interrompt la série de déjà-vues.
                streak = streak_known = streak_duplicates = 0
                if key:
                    run_keys.add(str(key))
                    index.remember(self.source, str(key), url)
                if job is None:  # carte inexploitable : comptée, non conservée
                    continue
                new_keys_in_page += 1

                rejection, final = "", True
                if validate:
                    rejection, job, detail_calls, final = self._screen_job(job)
                    counters["http_requests"] += detail_calls
                if rejection:
                    counters["jobs_rejected"] += 1
                    decision = (
                        SEEN_REJECTED_CONTRACT
                        if rejection.startswith("contrat incompatible")
                        else SEEN_REJECTED_BI
                    )
                    seen.append(
                        SeenEntry(
                            source=self.source,
                            external_key=str(key),
                            canonical_url=canon,
                            title=title,
                            decision=decision,
                            rejection_reason=rejection,
                            # Sans empreinte, le rejet sera réévalué au run suivant
                            # (fiche détail illisible : décision prise sur le titre).
                            filter_version=self._filter_version if final else None,
                        )
                    )
                    continue

                kept.append(job)
                counters["jobs_kept"] += 1
                seen.append(
                    SeenEntry(
                        source=self.source,
                        external_key=str(key),
                        canonical_url=canon,
                        title=title,
                        decision=SEEN_VALIDATED,
                    )
                )
                if len(kept) >= limit:
                    stop_reason = "quota"
                    stop_detail = limit_detail
                    halt = True
                    break

            if halt:
                break
            last_page_seen_ratio = seen_in_page / len(page.entries) if page.entries else 0.0
            if page.exhausted:
                stop_reason = "stream_end"
                stop_detail = f"flux épuisé (page {page_number})"
                break
            
            # Stagnation : la page ne contient *que* des cartes déjà croisées dans CE run.
            # L'API est probablement bloquée (paramètre de page ignoré).
            if len(page.entries) > 0 and duplicates_in_page == len(page.entries):
                stop_reason = "duplicate_page"
                stop_detail = f"stagnation détectée (page {page_number} identique)"
                break
                
            if mode == PASS_FRESHNESS and new_keys_in_page == 0:
                stop_reason = "duplicate_page"
                stop_detail = (
                    f"page {page_number} sans carte inédite "
                    "(toutes déjà connues, ou paramètre de page ignoré par la plateforme)"
                )
                break
            if page.next_cursor in (None, ""):
                stop_reason = "stream_end"
                stop_detail = "aucune page suivante"
                break
            cursor = page.next_cursor
        else:
            if last_page_seen_ratio >= SATURATION_RATIO:
                stop_reason = "max_pages_saturated"
                stop_detail = (
                    f"plafond de {plan.max_pages} page(s) atteint, dernière page déjà vue à "
                    f"{last_page_seen_ratio:.0%} : vivier de fait épuisé"
                )
            else:
                stop_reason = "max_pages"
                stop_detail = (
                    f"plafond de {plan.max_pages} page(s) atteint — flux potentiellement tronqué"
                )

        finished = datetime.now(timezone.utc)
        report = PassReport(
            source=self.source,
            query=query,
            mode=mode,
            started_at=started,
            finished_at=finished,
            duration_seconds=round((finished - started).total_seconds(), 3),
            pages_fetched=counters["pages_fetched"],
            http_requests=counters["http_requests"],
            cards_seen=counters["cards_seen"],
            jobs_kept=counters["jobs_kept"],
            jobs_known=counters["jobs_known"],
            jobs_duplicate=counters["jobs_duplicate"],
            jobs_rejected=counters["jobs_rejected"],
            jobs_out_of_window=counters["jobs_out_of_window"],
            target_new=plan.target_new,
            stop_reason=stop_reason,
            stop_detail=(f"{stop_detail} ; {plan.notes}" if plan.notes else stop_detail),
            stop_page=stop_page,
            newest_published_at=newest,
            oldest_published_at=oldest,
            error=error,
        )
        return report, kept, seen

    def _skipped_reports(
        self,
        mode: str,
        queries: Sequence[str],
        reason: str,
        detail: str,
        *,
        target_new: int = 0,
    ) -> list[PassReport]:
        """Télémétrie d'une passe ou d'une requête **non exécutée**.

        Trois situations, toutes consignées (jamais d'arrêt silencieux) : passe
        désactivée, source indisponible (cookies absents…), requête non lancée parce
        que l'objectif de la source était déjà atteint ou que le plafond de source
        était consommé/réservé. Sans cette trace, une collecte amputée serait
        indiscernable d'un vivier épuisé.
        """
        now = datetime.now(timezone.utc)
        return [
            PassReport(
                source=self.source,
                query=query,
                mode=mode,
                started_at=now,
                finished_at=now,
                duration_seconds=0.0,
                target_new=target_new,
                stop_reason=reason,
                stop_detail=detail,
            )
            for query in queries
        ]

    def _log_reports(self, reports: Sequence[PassReport]) -> None:
        """Consigne une ligne par passe et alerte sur les pertes de flux."""
        for report in reports:
            self._logger.info(
                "%s | %s | %s | %d page(s) | %d HTTP | %d vue(s) | %d gardée(s) | "
                "%d connue(s) | %d rejetée(s) | arrêt=%s (%s)",
                report.source,
                report.query,
                report.mode,
                report.pages_fetched,
                report.http_requests,
                report.cards_seen,
                report.jobs_kept,
                report.jobs_known,
                report.jobs_rejected,
                report.stop_reason,
                report.stop_detail or "—",
            )
            if is_incomplete_stop(report.stop_reason):
                self._logger.warning(
                    "%s | %s | %s : flux potentiellement perdu (%s : %s)",
                    report.source,
                    report.query,
                    report.mode,
                    report.stop_reason,
                    report.stop_detail or "—",
                )

    def close(self) -> None:
        """Libère le client HTTP sous-jacent."""
        self.client.close()

    def __enter__(self) -> "BaseScraper":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
