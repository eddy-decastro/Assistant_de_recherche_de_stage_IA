"""Modèles de données et configuration du module de scraping unifié.

Ce module définit :
  - le modèle Pydantic ``RawJob`` (offre brute normalisée, indépendante de la source) ;
  - la configuration ``ScraperConfig`` (filtrage métier + paramètres réseau) ;
  - les listes de mots-clés d'exclusion / positifs et les requêtes cibles.
"""

from __future__ import annotations

import logging

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

# Identifiant de source normalisé (wttj | linkedin | jobteaser).
Source = Literal["wttj", "linkedin", "jobteaser"]

_logger = logging.getLogger("scrapers.models")

# --------------------------------------------------------------------------- #
# Filtrage métier
# --------------------------------------------------------------------------- #
# Mots-clés rédhibitoires : toute offre orientée BI / reporting / analyste
# classique doit être écartée (test effectué sur le titre ET le résumé).
EXCLUSION_KEYWORDS: list[str] = [
    "power bi",
    "tableau",
    "qlik",
    "vba",
    "excel reporting",
    "data analyst",
    "business intelligence",
    "bi analyst",
    "chargé de reporting",
    "stage bi",
]

# Requêtes principales envoyées aux sources.
TARGET_QUERIES: list[str] = [
    "Stage Data Scientist",
    "Stage Machine Learning",
    "Stage Recherche IA",
]

# Signaux positifs exigés pour considérer une offre comme "Data Science / ML".
POSITIVE_DS_ML_KEYWORDS: list[str] = [
    "data scientist",
    "data science",
    "machine learning",
    "deep learning",
    "ml engineer",
    "mlops",
    "ml ops",
    "intelligence artificielle",
    "artificial intelligence",
    "nlp",
    "computer vision",
    "vision par ordinateur",
    "pytorch",
    "tensorflow",
    "scikit-learn",
    "neural network",
    "réseau de neurones",
    "llm",
    "rag",
    "recherche",
    "research",
    "r&d",
    "data engineer",
    "moteur de recommandation",
]

# User-Agent moderne partagé par tous les scrapers.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def canonical_url(url: str) -> str:
    """URL canonique : minuscules, sans ``www.``, query, fragment ni slash final.

    Source **unique** de la normalisation d'URL du projet (``src.storage.cleanup``
    la ré-exporte) : la déduplication d'ingestion et celle de collecte ne peuvent
    donc pas diverger.

    Le schéma est retiré : deux URLs http/https de la même offre se rejoignent.
    """
    parts = urlsplit((url or "").strip())
    host = parts.netloc.casefold().removeprefix("www.")
    return f"{host}{parts.path.rstrip('/')}"


# --------------------------------------------------------------------------- #
# Collecte hybride : passes (fraîcheur / rattrapage), plans et rapports
# --------------------------------------------------------------------------- #
# ``freshness`` suit l'ordre temporel et s'arrête dès que le flux rejoint le
# scrape précédent ; ``relevance`` suit le classement algorithmique de la
# plateforme et va jusqu'au quota ou à l'épuisement de la pagination.
PASS_FRESHNESS: str = "freshness"
PASS_RELEVANCE: str = "relevance"
PassMode = Literal["freshness", "relevance"]
#: Ordre d'exécution des passes : le frais d'abord (postuler vite), puis le
#: rattrapage algorithmique.
PASS_MODES: tuple[str, ...] = (PASS_FRESHNESS, PASS_RELEVANCE)

#: Motif d'arrêt d'une passe. Chaque motif distingue « vivier épuisé » d'un
#: « flux perdu » : c'est la condition pour savoir si de la donnée a été ratée.
StopReason = Literal[
    "quota",
    "early_stop",
    "window_end",
    "stream_end",
    "max_pages",
    "duplicate_page",
    "rate_limit",
    "http_error",
    "network_error",
    "auth_missing",
    "unsupported",
    "disabled",
    "error",
]


#: Motifs d'arrêt signalant une **perte de flux** : la passe n'a pas épuisé le
#: vivier, elle a été interrompue (plateforme ou garde-fou). Le pipeline les
#: remonte en alerte, car ils indiquent de la donnée potentiellement ratée.
INCOMPLETE_STOP_REASONS: tuple[str, ...] = (
    "rate_limit",
    "http_error",
    "network_error",
    "max_pages",
    "error",
)


def is_incomplete_stop(reason: str | None) -> bool:
    """Le motif d'arrêt signale-t-il une perte de flux (et non un vivier épuisé) ?"""
    return str(reason or "") in INCOMPLETE_STOP_REASONS


# --- Décisions de la mémoire de collecte (table ``seen_jobs``) ----------------
# Vocabulaire partagé entre les scrapers (qui décident) et la persistance (qui
# archive) ; ``src.constants`` les ré-exporte pour le reste du projet.
SEEN_VALIDATED = "VALIDATED"                  # retenue : insérée dans ``jobs``
SEEN_REJECTED_BI = "REJECTED_BI"              # écartée par le filtre anti-BI / DS-ML
SEEN_REJECTED_CONTRACT = "REJECTED_CONTRACT"  # hors stage / contrat incompatible
SEEN_OUT_OF_WINDOW = "OUT_OF_WINDOW"          # antérieure à la fenêtre temporelle
SEEN_DUPLICATE = "DUPLICATE"                  # déjà croisée dans le même run
SEEN_KNOWN = "KNOWN"                          # déjà connue avant ce run

SEEN_DECISIONS: tuple[str, ...] = (
    SEEN_VALIDATED,
    SEEN_REJECTED_BI,
    SEEN_REJECTED_CONTRACT,
    SEEN_OUT_OF_WINDOW,
    SEEN_DUPLICATE,
    SEEN_KNOWN,
)


class SeenEntry(BaseModel):
    """Trace d'UNE carte croisée par un scraper (alimente la table ``seen_jobs``).

    Toute carte est mémorisée, y compris celles écartées par le filtre métier ou
    par la fenêtre temporelle : c'est ce qui permet à la collecte suivante de
    reconnaître le bruit déjà parcouru et de s'arrêter tôt.
    """

    model_config = ConfigDict(extra="ignore")

    source: Source
    external_key: str
    canonical_url: str = ""
    title: str = ""
    decision: str
    rejection_reason: str | None = None


class CardEntry(BaseModel):
    """Une carte du flux, dans l'ordre où la plateforme la renvoie.

    ``job`` vaut ``None`` quand la carte est inexploitable (titre ou entreprise
    manquants) : elle compte tout de même dans les compteurs et l'arrêt anticipé,
    sinon l'avancement de la pagination serait mal mesuré.
    """

    model_config = ConfigDict(extra="ignore")

    key: str
    job: "RawJob | None" = None


class PageResult(BaseModel):
    """Une page de résultats telle qu'une source la fournit."""

    model_config = ConfigDict(extra="ignore")

    entries: list[CardEntry] = Field(default_factory=list)
    #: Curseur de la page suivante (offset LinkedIn, numéro de page JobTeaser…).
    next_cursor: int | str | None = None
    #: ``True`` si la source déclare explicitement la fin du flux.
    exhausted: bool = False
    #: Nombre d'appels HTTP consommés pour obtenir cette page (observabilité).
    http_calls: int = 1


def _rebuild_page_models() -> None:
    """Résout les références avant ``RawJob`` (déclarations différées de Pydantic)."""
    CardEntry.model_rebuild()
    PageResult.model_rebuild()


class RawJob(BaseModel):
    """Offre brute normalisée, indépendante de la source de collecte."""

    model_config = ConfigDict(extra="ignore")

    id_externe: str
    source: Source
    title: str
    company: str
    location: str
    url: str
    description: str
    published_at: datetime | None = None
    is_internship: bool = True


# Les modèles de page référencent ``RawJob``, défini juste au-dessus : la
# résolution des annotations (différée par Pydantic) est déclenchée ici.
_rebuild_page_models()


class PassConfig(BaseModel):
    """Paramétrage d'UNE passe de collecte (piloté par ``config.yaml``)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    sort: Literal["date", "relevance"] = "date"
    #: Quota de **nouvelles** offres conservées, pour chaque requête cible.
    max_offers_per_query: int = 40
    #: Fenêtre temporelle en jours ; ``None`` = aucune limite de fraîcheur.
    window_days: float | None = None
    #: Nombre de cartes DÉJÀ CONNUES consécutives déclenchant l'arrêt anticipé
    #: (``0`` = jamais : c'est le réglage de la passe « Rattrapage »).
    early_stop_after_known: int = 0
    #: Pages minimales avant d'autoriser un arrêt anticipé (garde-fou : une
    #: première page entièrement connue ne suffit pas à conclure).
    early_stop_min_pages: int = 1
    #: Plafond de pages par requête (garde-fou anti-boucle et anti-429).
    max_pages_per_query: int = 12
    #: Arrêter la passe à la première offre antérieure à la fenêtre temporelle.
    #: N'a de sens que si l'ordre du flux est chronologique (``trust_source_order``).
    stop_when_older_than_window: bool = True
    #: Demander le filtrage temporel à la plateforme quand elle le permet
    #: (LinkedIn : ``f_TPR``). C'est la garantie de fraîcheur la plus fiable :
    #: elle borne le vivier indépendamment de l'ordre du flux.
    use_server_window_filter: bool = True
    #: Faire confiance à l'ordre renvoyé par la plateforme : active l'arrêt
    #: anticipé et l'arrêt sur fenêtre même si la source déclare son ordre non
    #: fiable. Réglage expert — par défaut, on s'en remet à la mesure.
    trust_source_order: bool = False

    @property
    def window_seconds(self) -> int | None:
        """Fenêtre temporelle en secondes (``None`` si désactivée)."""
        if not self.window_days or self.window_days <= 0:
            return None
        return int(self.window_days * 86400)

    def clamped_quota(self) -> int:
        """Quota par requête, toujours au moins 1."""
        return max(1, int(self.max_offers_per_query))

    @classmethod
    def only(cls, mode: str = PASS_RELEVANCE, **overrides: Any) -> dict[str, "PassConfig"]:
        """Jeu de passes réduit à un seul mode (tests, dépannage, ``--passes``).

        Exemple : ``PassConfig.only("relevance", max_offers_per_query=5)``.
        """
        defaults = default_passes()
        if mode not in defaults:
            raise ValueError(f"Mode de passe inconnu : {mode!r} (attendu : {sorted(defaults)})")
        return {mode: defaults[mode].model_copy(update=overrides)}


def default_passes() -> dict[str, PassConfig]:
    """Jeu de passes par défaut, surchargé par ``config.yaml → scrapers.passes``.

    * **Fraîcheur** : tri par date, fenêtre de 7 jours, arrêt anticipé après
      5 offres consécutives déjà connues, quota 40 par requête.
    * **Rattrapage** : tri par pertinence, quota 20 par requête et **aucun** arrêt
      anticipé — le classement n'étant pas temporel, offres connues et inédites
      s'entremêlent et seule la déduplication s'applique.
    """
    return {
        PASS_FRESHNESS: PassConfig(
            enabled=True,
            sort="date",
            max_offers_per_query=40,
            window_days=7.0,
            early_stop_after_known=5,
            early_stop_min_pages=1,
            max_pages_per_query=12,
            stop_when_older_than_window=True,
        ),
        PASS_RELEVANCE: PassConfig(
            enabled=True,
            sort="relevance",
            max_offers_per_query=20,
            window_days=None,
            early_stop_after_known=0,
            max_pages_per_query=12,
            stop_when_older_than_window=False,
        ),
    }


@dataclass(frozen=True)
class PassPlan:
    """Passe résolue : ce que le moteur et la source doivent réellement appliquer.

    Le calcul est centralisé (une seule fois par passe) pour que les règles de
    sécurité — « l'arrêt anticipé suppose un flux trié », « le filtre temporel
    serveur prime sur l'ordre du flux » — s'appliquent identiquement à toutes les
    sources, au lieu d'être réécrites dans chaque scraper.
    """

    mode: PassMode
    sort: str
    quota: int
    max_pages: int
    window_days: float | None
    window_deadline: datetime | None
    #: La passe applique-t-elle un filtre temporel côté plateforme ?
    use_server_window_filter: bool
    #: Seuil d'arrêt anticipé **effectif** (0 = désactivé).
    early_stop_threshold: int
    #: Arrêt à la première offre antérieure à la fenêtre ?
    stop_on_window: bool
    #: L'ordre du flux est-il jugé fiable (arrêt anticipé autorisé) ?
    trusted_order: bool
    #: Pages minimales avant arrêt anticipé.
    early_stop_min_pages: int
    #: Explication lisible des restrictions appliquées (reprise dans la télémétrie).
    notes: str = ""


class PassReport(BaseModel):
    """Télémétrie d'une passe : (source × requête × mode) → raison exacte d'arrêt."""

    model_config = ConfigDict(extra="ignore")

    source: Source
    query: str
    mode: str
    started_at: datetime
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    pages_fetched: int = 0
    http_requests: int = 0
    cards_seen: int = 0
    jobs_kept: int = 0
    jobs_known: int = 0
    jobs_duplicate: int = 0
    jobs_rejected: int = 0
    jobs_out_of_window: int = 0
    stop_reason: str = "stream_end"
    stop_detail: str = ""
    stop_page: int | None = None
    newest_published_at: datetime | None = None
    oldest_published_at: datetime | None = None
    error: str | None = None


class TelemetryConfig(BaseModel):
    """Réglages d'observabilité de la collecte (télémétrie + mémoire de collecte)."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    #: Rétention des lignes ``scrape_runs`` / ``scrape_query_stats`` (jours).
    retention_days: int = 180
    #: Élaguer aussi la mémoire de collecte (offres vues mais jamais retenues).
    prune_seen_jobs: bool = True


class ScraperConfig(BaseModel):
    """Configuration du module de scraping (filtrage métier + réseau)."""

    model_config = ConfigDict(extra="ignore")

    exclusion_keywords: list[str] = Field(default_factory=lambda: list(EXCLUSION_KEYWORDS))
    target_queries: list[str] = Field(default_factory=lambda: list(TARGET_QUERIES))
    positive_ds_ml_keywords: list[str] = Field(
        default_factory=lambda: list(POSITIVE_DS_ML_KEYWORDS)
    )
    request_timeout_seconds: float = 30.0
    user_agent: str = DEFAULT_USER_AGENT
    enabled_sources: list[Source] = Field(
        default_factory=lambda: ["wttj", "linkedin", "jobteaser"]
    )
    max_offers_per_source: int = 120
    # DÉPRÉCIÉ — quota « à plat » d'une collecte mono-passe. S'il est renseigné
    # sans section ``passes``, il alimente le quota des deux passes (compatibilité
    # des config.yaml antérieurs).
    max_offers_per_query: int | None = None

    # --- Collecte hybride : les passes « Fraîcheur » puis « Rattrapage » ---
    passes: dict[str, PassConfig] = Field(default_factory=default_passes)

    # --- Observabilité : télémétrie des runs et mémoire de collecte ---
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)

    @field_validator("passes", mode="before")
    @classmethod
    def _normalize_passes(cls, value: Any) -> Any:
        """Normalise la section YAML ``scrapers.passes`` (déclaration explicite).

        Règles :

        * section absente ⇒ les deux passes par défaut (fraîcheur + rattrapage) ;
        * section présente ⇒ **seuls les modes déclarés** sont exécutés : omettre
          ``relevance`` désactive la passe de rattrapage (comportement explicite,
          qui rend possible une collecte mono-passe pour les tests ou un run
          ciblé). Les champs omis d'un mode déclaré prennent leur valeur par
          défaut ;
        * un mode inconnu ou mal typé est ignoré avec un avertissement : une faute
          de frappe dans le YAML ne casse jamais le pipeline.
        """
        if value is None:
            return default_passes()
        if not isinstance(value, Mapping):
            _logger.warning("scrapers.passes : dictionnaire attendu ; valeurs par défaut utilisées.")
            return default_passes()
        normalized: dict[str, PassConfig] = {}
        defaults = default_passes()
        for mode, raw in value.items():
            if mode not in defaults:
                _logger.warning("scrapers.passes : mode inconnu ignoré (%r).", mode)
                continue
            if isinstance(raw, PassConfig):
                normalized[mode] = raw
                continue
            if not isinstance(raw, Mapping):
                _logger.warning("scrapers.passes.%s : dictionnaire attendu ; mode ignoré.", mode)
                continue
            try:
                normalized[mode] = PassConfig.model_validate(
                    {**defaults[mode].model_dump(), **raw}
                )
            except ValidationError as exc:
                _logger.warning("scrapers.passes.%s invalide (%s) ; mode ignoré.", mode, exc)
        return normalized or default_passes()

    @property
    def per_query_quota(self) -> int:
        """Quota historique par requête (compatibilité) — voir ``passes``."""
        if self.max_offers_per_query:
            return max(1, int(self.max_offers_per_query))
        return max(1, self.max_offers_per_source // max(1, len(self.target_queries)))

    def pass_config(self, mode: str) -> PassConfig:
        """Configuration d'un mode de collecte (repli sur les défauts si absent)."""
        config = self.passes.get(mode)
        if config is None:
            return default_passes()[mode if mode in default_passes() else PASS_FRESHNESS]
        return config

    def enabled_modes(self) -> list[str]:
        """Modes actifs, dans l'ordre d'exécution : fraîcheur, puis rattrapage.

        Seuls les modes **présents dans ``passes``** sont considérés : une
        configuration mono-passe (``PassConfig.only("relevance")``) n'est pas
        complétée par le mode absent — sinon le repli par défaut réactiverait
        silencieusement une passe que l'opérateur n'a pas demandée.
        """
        return [
            mode
            for mode in (PASS_FRESHNESS, PASS_RELEVANCE)
            if mode in self.passes and self.pass_config(mode).enabled
        ]

    def build_plan(
        self,
        mode: str,
        *,
        date_order_reliable: bool,
        server_window_filter: bool,
        now: datetime | None = None,
    ) -> PassPlan:
        """Résout une passe en plan d'exécution, en appliquant les garde-fous.

        Deux règles de sécurité, appliquées identiquement à toutes les sources :

        1. **L'arrêt anticipé n'a de sens que sur un flux trié.** Si la source
           déclare son ordre non chronologique (mesuré ou inconnu), le seuil est
           ramené à 0 et le coût de la passe est borné par le filtre temporel
           serveur (``f_TPR``) et le quota — la raison est consignée dans
           ``notes`` pour la télémétrie ;
        2. **Le filtre temporel serveur prime sur l'ordre du flux** : il borne le
           vivier indépendamment du tri renvoyé par la plateforme.
        """
        config = self.pass_config(mode)
        trusted = bool(date_order_reliable or config.trust_source_order)
        notes: list[str] = []

        threshold = 0
        if config.early_stop_after_known > 0:
            if trusted:
                threshold = int(config.early_stop_after_known)
            else:
                notes.append(
                    "arrêt anticipé désactivé (ordre du flux non chronologique) : "
                    "coût borné par la fenêtre et le quota"
                )

        stop_on_window = bool(config.stop_when_older_than_window and trusted)
        if config.stop_when_older_than_window and not stop_on_window:
            notes.append("arrêt sur fenêtre désactivé (ordre du flux non chronologique)")

        window_seconds = config.window_seconds
        use_server_window = bool(
            window_seconds and config.use_server_window_filter and server_window_filter
        )
        if window_seconds and not use_server_window:
            notes.append("filtre temporel serveur indisponible pour cette source")

        current = now or datetime.now(timezone.utc)
        deadline = (
            current - timedelta(seconds=window_seconds) if window_seconds else None
        )

        return PassPlan(
            mode=mode,  # type: ignore[arg-type]
            sort=config.sort,
            quota=config.clamped_quota(),
            max_pages=max(1, int(config.max_pages_per_query)),
            window_days=config.window_days,
            window_deadline=deadline,
            use_server_window_filter=use_server_window,
            early_stop_threshold=threshold,
            stop_on_window=stop_on_window,
            trusted_order=trusted,
            early_stop_min_pages=max(1, int(config.early_stop_min_pages)),
            notes=" ; ".join(notes),
        )


    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "ScraperConfig":
        """Construit la configuration depuis ``config.yaml`` (section ``scrapers``).

        Les clés absentes conservent les valeurs par défaut du modèle ; une section
        absente, mal typée ou invalide ne casse jamais le pipeline (défauts
        réappliqués + avertissement journalisé).

        Compatibilité : un ``max_offers_per_query`` « à plat » (configurations
        antérieures à la collecte hybride) alimente le quota des deux passes
        lorsqu'aucune section ``passes`` n'est fournie.
        """
        section = (config or {}).get("scrapers") or {}
        if not isinstance(section, dict):
            _logger.warning(
                "config.yaml, clé 'scrapers' : dictionnaire attendu ; valeurs par défaut utilisées."
            )
            return cls()
        known = {key: value for key, value in section.items() if key in cls.model_fields}
        if not known:
            return cls()
        try:
            built = cls(**known)
        except ValidationError as exc:
            _logger.warning(
                "config.yaml, clé 'scrapers' invalide (%s) ; valeurs par défaut utilisées.", exc
            )
            return cls()
        if "passes" not in known and known.get("max_offers_per_query"):
            fallback = max(1, int(known["max_offers_per_query"]))
            _logger.warning(
                "config.yaml : 'max_offers_per_query' est déprécié au profit de "
                "'passes.<mode>.max_offers_per_query' ; valeur %d appliquée aux deux passes.",
                fallback,
            )
            built.passes = {
                mode: pass_config.model_copy(update={"max_offers_per_query": fallback})
                for mode, pass_config in built.passes.items()
            }
        return built


class ScrapeResult(BaseModel):
    """Résultat d'un scraping : offres validées, compteurs et télémétrie des passes."""

    jobs: list[RawJob] = Field(default_factory=list)
    found: int = 0          # offres brutes récupérées (avant filtrage)
    rejected_bi: int = 0    # offres rejetées par le filtre anti-BI / DS-ML
    #: Une entrée de télémétrie par (source × requête cible × mode) : c'est elle
    #: qui porte la raison exacte d'arrêt de chaque passe.
    query_reports: list[PassReport] = Field(default_factory=list)
    #: Mémoire de collecte : TOUTES les cartes croisées, avec leur décision
    #: (retenue, rejetée BI, hors fenêtre, déjà connue…), à persister dans
    #: ``seen_jobs`` pour que la prochaine collecte s'arrête plus tôt.
    seen: list[SeenEntry] = Field(default_factory=list)

