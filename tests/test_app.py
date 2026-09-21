"""Tests du dashboard Streamlit : rendu (AppTest) et helpers de présentation."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit.testing.v1 import AppTest

from utils.data import (
    filter_jobs,
    Filters,
    effective_score,
    is_reranked,
    source_distribution,
    group_jobs_by_source,
    DISPLAY_GROUPED,
    QUALIFIED_SCORE,
    tone_class,
)
from utils.styles import (
    _CSS_TEMPLATE,
    _token_context,
    _theme_type,
)
from utils.components import (
    job_card_html,
    score_alignment,
    clean_text,
    excerpt,
    EXCERPT_LENGTH,
    relative_date,
    parse_timestamp,
    detected_technologies,
    contract_label,
)
from src.constants import (
    STATUS_NEW,
    STATUS_APPLIED,
    STATUS_ORDER,
    TIER_1,
    TIER_NEUTRAL,
    TIER_ESN,
    VERDICT_EXCELLENT,
    VERDICT_GOOD,
    VERDICT_MIXED,
    VERDICT_OFF_TOPIC,
    RUN_OK,
    RUN_PARTIAL,
    SOURCE_COLORS,
)
from pages.pipeline import PIPELINE_ACTIONS
from pages.statistiques import (
    _telemetry_runs_table,
    _telemetry_passes_table,
    _counters_strip,
    _collection_counters_table,
    _objectives_table,
    _refusals_table,
)
import app
import streamlit as st
from src.config import load_config
from src.storage.database import Database

# Emojis décoratifs bannis de l'interface (refonte « outil d'ingénierie »).
BANNED_EMOJI = (
    "🔍", "📄", "🏢", "🔗", "🚀", "👉", "🤖", "🎯", "🌟", "✅", "⚠️", "🚫", "⚖️", "🎛️", "📍",
)

# Contenu provenant des plateformes : titres, métadonnées et extraits de fiche.
_OFFER_CONTENT_PATTERNS = (
    r'<p class="sc-excerpt">.*?</p>',
    r'<h3 class="sc-card-title">.*?</h3>',
    r'<div class="sc-card-meta">.*?</div>',
    r'<span class="sc-badge[^"]*sc-subscore[^"]*">.*?</span>',
    r'<div class="sc-subscore-strip">.*?</div>',
    r'<div class="sc-alert[^"]*">.*?</div>',
)


def ui_labels_only(markup: str) -> str:
    """Retire le texte des offres pour ne garder que les libellés de l'interface."""
    cleaned = markup
    for pattern in _OFFER_CONTENT_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.DOTALL)
    return cleaned

_DESCRIPTION = "Mission de recherche : modélisation PyTorch et Graph Neural Networks. " * 12

# Offre évaluée par l'étape 1 uniquement (bi-encoder).
JOB = {
    "id": "offre-1",
    "title": "STAGE - Ingénieur.e Recherche Machine Learning (F/H)",
    "company": "Dassault Systèmes",
    "location": "Valbonne, France",
    "url": "https://example.com/offre?a=1&b=2",
    "description": None,
    "source": "jobteaser",
    "company_tier": 2,
    "semantic_score": 52.26,
    "final_score": 46.36,
    "status": STATUS_NEW,
    "created_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=5),
    "rerank_score": None,
    "verdict": None,
    "match_reasons": [],
    "red_flags": [],
    "tech_stack": [],
}

# Offre évaluée par le juge LLM (étape 2) : tier 1, verdict EXCELLENT.
RERANKED = {
    **JOB,
    "id": "offre-2",
    "company": "Mistral AI",
    "company_tier": TIER_1,
    "rerank_score": 88.0,
    "verdict": VERDICT_EXCELLENT,
    "match_reasons": ["Modélisation PyTorch avancée", "Équipe de recherche reconnue"],
    "red_flags": ["Périmètre susceptible d'évoluer"],
    "tech_stack": ["PyTorch", "GNN"],
    "description": _DESCRIPTION,
    "status": STATUS_APPLIED,
}


def _open_database() -> Database:
    """Base SQLite déclarée dans config.yaml (lecture du contenu réel)."""
    return Database(load_config()["database"]["path"])


def _safe_token(text: str) -> str | None:
    """Extrait un mot-clé alphanumérique utilisable comme recherche plein texte."""
    for token in re.findall(r"[A-Za-z]{5,}", text or ""):
        if token.lower() not in {"stage", "recherche", "ingenieur"}:
            return token
    return None


def _cards(at: AppTest) -> list[str]:
    """Cartes d'offres rendues dans la zone principale (fragment HTML dédié)."""
    return [element.value for element in at.markdown if '<div class="sc-card">' in element.value]


def test_helpers_score_et_alignement() -> None:
    """Le score effectif privilégie le rerank LLM ; l'alignement est gradué."""
    assert effective_score(JOB) == 46.36
    assert effective_score(RERANKED) == 88.0
    assert is_reranked(JOB) is False
    assert is_reranked(RERANKED) is True
    assert score_alignment(85)[0] == "Cœur de cible"
    assert score_alignment(65)[0] == "Pertinent"
    assert score_alignment(45)[0] == "Secondaire"
    assert score_alignment(10)[0] == "Hors périmètre"
    assert tone_class(score_alignment(45)[1]) == "sc-tone-warn"
    print("  Helpers : score effectif et paliers d'alignement OK")


def test_helpers_texte_dates_et_technos() -> None:
    """Nettoyage de fiche, dates relatives, technologies clés, type de contrat."""
    assert clean_text("  a  b \n\n\n c ") == "a b \n c"
    long_text, truncated = excerpt("mot " * 200)
    assert truncated is True
    assert len(long_text) <= EXCERPT_LENGTH + 2
    assert excerpt("court") == ("court", False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert relative_date(now - timedelta(seconds=30), now) == "À l'instant"
    assert relative_date(now - timedelta(minutes=10), now) == "Il y a 10 min"
    assert relative_date(now - timedelta(hours=5), now) == "Il y a 5 h"
    assert relative_date(now - timedelta(days=1), now) == "Hier"
    assert relative_date(now - timedelta(days=3), now) == "Il y a 3 j"
    assert relative_date(now - timedelta(days=90), now) == "Il y a 3 mois"
    assert relative_date(None) == "Date inconnue"
    assert parse_timestamp("2026-09-13T16:44:02.358898") is not None

    assert detected_technologies(RERANKED, ("PyTorch", "GNN", "SQL")) == ["PyTorch", "GNN"]
    assert detected_technologies(JOB, ()) == []
    assert detected_technologies({"title": "Stage NLP", "description": None}, ("NLP",)) == ["NLP"]
    assert contract_label(JOB) == "Stage"
    assert contract_label({"title": "Contrat d'apprentissage data"}) == "Alternance"
    assert contract_label({"title": "Data analyst"}) is None
    print("  Helpers : texte, dates relatives, technologies et contrat OK")


def test_filtres_et_repartition() -> None:
    """Filtrage combiné, vue focus, recherche et répartition par plateforme."""
    jobs = [JOB, RERANKED]
    assert len(filter_jobs(jobs, Filters(statuses=STATUS_ORDER))) == 2
    assert len(filter_jobs(jobs, Filters(min_score=60))) == 1
    assert len(filter_jobs(jobs, Filters(llm_only=True))) == 1
    assert len(filter_jobs(jobs, Filters(sources=("jobteaser",)))) == 2
    assert filter_jobs(jobs, Filters(sources=("linkedin",))) == []
    assert len(filter_jobs(jobs, Filters(statuses=(STATUS_APPLIED,)))) == 1
    assert len(filter_jobs(jobs, Filters(tiers=(TIER_1,)))) == 1
    assert filter_jobs(jobs, Filters(exclude_esn=True, tiers=(TIER_ESN,))) == []
    assert len(filter_jobs(jobs, Filters(limit=1))) == 1
    assert len(filter_jobs(jobs, Filters(query="mistral pytorch"))) == 1
    assert filter_jobs(jobs, Filters(query="zz-introuvable")) == []

    # Filtrage par entreprise : exclusion Dassault, exclusion/ciblage d'entreprises.
    assert [j["id"] for j in filter_jobs(jobs, Filters(exclude_dassault=True))] == ["offre-2"]
    assert [j["id"] for j in filter_jobs(jobs, Filters(exclude_companies=("Mistral AI",)))] == ["offre-1"]
    assert [j["id"] for j in filter_jobs(jobs, Filters(selected_companies=("Mistral AI",)))] == ["offre-2"]

    # La vue focus (masquer les offres traitées) prime sur la sélection de statuts.
    focus = Filters(statuses=(STATUS_APPLIED,), hide_processed=True)
    assert [job["id"] for job in filter_jobs(jobs, focus)] == ["offre-1"]

    assert Filters().is_default() is True
    assert Filters(min_score=10).is_default() is False
    assert Filters(exclude_dassault=True).is_default() is False
    assert Filters(exclude_companies=("Dassault",)).is_default() is False
    assert Filters(selected_companies=("Mistral",)).is_default() is False
    assert Filters(group_by_source=True).is_default() is True, "Le mode d'affichage n'est pas un filtre."
    assert source_distribution(jobs) == [("JobTeaser", 2, SOURCE_COLORS["jobteaser"])]
    assert [label for label, _ in group_jobs_by_source(jobs)] == ["JobTeaser"]
    print("  Filtres : score, statuts, typologie, plateformes, entreprises, recherche et répartition OK")


def test_filtres_entreprises_avance() -> None:
    """Vérifie le filtrage multi-entreprises et le cas spécifique Dassault."""
    j1 = {**JOB, "id": "1", "company": "Dassault Systèmes"}
    j2 = {**JOB, "id": "2", "company": "DASSAULT AVIATION"}
    j3 = {**JOB, "id": "3", "company": "CEA"}
    j4 = {**JOB, "id": "4", "company": "Bpifrance"}
    j5 = {**JOB, "id": "5", "company": "Sopra Steria"}
    sample = [j1, j2, j3, j4, j5]

    # 1. Sans Dassault : élimine toutes les variantes de Dassault
    sans_dassault = filter_jobs(sample, Filters(exclude_dassault=True))
    assert [j["id"] for j in sans_dassault] == ["3", "4", "5"]

    # 2. Exclusion arbitraire multi-entreprises
    sans_cea_sopra = filter_jobs(sample, Filters(exclude_companies=("CEA", "Sopra Steria")))
    assert [j["id"] for j in sans_cea_sopra] == ["1", "2", "4"]

    # 3. Ciblage exclusif
    seulement_bpi_cea = filter_jobs(sample, Filters(selected_companies=("Bpifrance", "CEA")))
    assert [j["id"] for j in seulement_bpi_cea] == ["3", "4"]

    # 4. Combinaison sans Dassault + ciblage d'un ensemble incluant Dassault
    combo = filter_jobs(sample, Filters(exclude_dassault=True, selected_companies=("Dassault Systèmes", "CEA")))
    assert [j["id"] for j in combo] == ["3"]
    print("  Filtres entreprises : exclusion Dassault et multi-entreprises OK")


def test_carte_html() -> None:
    """La carte d'offre est un fragment HTML autonome, échappé et complet."""
    card = job_card_html(RERANKED, ("PyTorch", "GNN"))
    assert card.startswith('<div class="sc-card">') and card.endswith("</div>")
    assert card.count('<div class="sc-card">') == 1
    assert "Détails &amp; évaluation" in card, "L'accordéon doit être libellé explicitement."
    assert "Points forts" in card and "Points d'attention" in card
    assert "Modélisation PyTorch avancée" in card
    assert "Lire la fiche complète" in card
    assert 'class="sc-chip">PyTorch<' in card
    assert "Cœur de cible" in card and "sc-tone-positive" in card
    assert "Excellent" in card

    unreanked = job_card_html(JOB, ())
    assert "Verdict du juge LLM" in unreanked and "run_pipeline.py" in unreanked
    assert "Fiche non fournie" in unreanked
    assert "Secondaire" in unreanked and "sc-tone-warn" in unreanked, "46/100 doit tomber en « Secondaire »."
    print("  Carte : en-tête, jauge de score, badges, accordéon et CTA OK")


def test_interface_streamlit() -> None:
    """L'app se rend sans exception et les filtres pilotent réellement le flux."""
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=240)
    at.run()
    assert not at.exception, f"Exception dans l'app : {at.exception}"

    # 1. Barre latérale : filtres compacts attendus.
    assert [widget.label for widget in at.sidebar.text_input] == ["Recherche"]
    assert [widget.label for widget in at.sidebar.multiselect] == [
        "Plateformes",
        "Statut de candidature",
        "Typologie d'entreprise",
        "Exclure des entreprises",
        "Cibler des entreprises",
    ]
    assert [widget.label for widget in at.sidebar.slider] == ["Score R&D minimal"]
    assert [widget.label for widget in at.sidebar.toggle] == [
        "Verdict LLM uniquement",
        "Masquer les offres traitées",
        "Exclure les ESN",
        "Exclure Dassault",
    ]
    assert [widget.label for widget in at.sidebar.selectbox] == ["Mode de flux", "Offres affichées"]

    scripts = {action.key: action.script for action in PIPELINE_ACTIONS}
    assert scripts["collect"] == "run_scrapers.py"
    assert scripts["descriptions"] == "backfill_descriptions.py"
    collect = next(a for a in PIPELINE_ACTIONS if a.key == "collect")
    assert "--no-scoring" in collect.args, collect
    rerank = next(a for a in PIPELINE_ACTIONS if a.key == "rerank")
    assert "--no-collect" in rerank.args and "--trigger-rerank" in rerank.args, rerank

    # 2. Bandeau KPI, en-tête et cartes d'offres rendus.
    markup = " ".join(element.value for element in at.markdown)
    assert 'class="sc-kpis"' in markup, "Le bandeau KPI doit être rendu."
    assert 'class="sc-stream"' in markup, "L'en-tête de flux doit être rendu."
    assert 'class="sc-title"' in markup, "L'en-tête applicatif doit être rendu."

    db = _open_database()
    total = db.count_jobs()
    top = db.get_jobs(limit=1)
    db.engine.dispose()
    cards = _cards(at)
    expected = min(total, 50)  # 50 = valeur par défaut de « Offres affichées »
    assert len(cards) == expected, f"{len(cards)} carte(s) rendue(s) pour {total} offre(s) en base"
    print(f"  Interface : {total} offre(s) en base, {len(cards)} carte(s) rendue(s) OK")

    # 3. Aucun emoji décoratif dans les libellés d'INTERFACE.
    rendered = ui_labels_only(" ".join([markup] + [element.value for element in at.caption]))
    for emoji in BANNED_EMOJI:
        assert emoji not in rendered, f"Emoji décoratif détecté : {emoji}"

    sample = job_card_html({**JOB, "description": "Annonce rédigée avec 🔍 et 🚀"}, ())
    assert "🚀" in sample, "Le contenu d'une offre doit être rendu tel quel (aucune censure)."
    assert "🚀" not in ui_labels_only(sample) and "🔍" not in ui_labels_only(sample)
    assert "Détails &amp; évaluation" in ui_labels_only(sample), "Les libellés restent contrôlés."
    print("  Interface : aucun emoji décoratif dans les libellés OK (contenu des offres exclu)")

    # 4. Recherche infructueuse : état vide explicite, aucune carte.
    at.sidebar.text_input[0].set_value("zz-introuvable-zz").run()
    assert not at.exception, at.exception
    assert _cards(at) == []
    assert any('<div class="sc-empty">' in element.value for element in at.markdown)
    print("  Interface : recherche sans résultat -> état vide OK")

    # 5. Recherche ciblée : seules les offres correspondantes sont affichées.
    token = _safe_token(top[0]["title"]) if top else None
    if token:
        at.sidebar.text_input[0].set_value(token).run()
        assert not at.exception, at.exception
        cards = _cards(at)
        assert cards and all(token.lower() in card.lower() for card in cards), token
        print(f"  Interface : recherche « {token} » -> {len(cards)} carte(s) cohérente(s) OK")
    else:
        print("  Interface : recherche ciblée ignorée (base vide)")

    # 6. Vue groupée par plateforme.
    at.sidebar.text_input[0].set_value("").run()
    at.sidebar.selectbox[0].select(DISPLAY_GROUPED).run()
    assert not at.exception, at.exception
    markup = " ".join(element.value for element in at.markdown)
    assert '<div class="sc-group">' in markup, "Les entêtes de groupe doivent apparaître."
    print("  Interface : affichage groupé par plateforme OK")

    # 7. Vue focus : masquer les offres traitées.
    at.sidebar.toggle[1].set_value(True).run()
    assert not at.exception, at.exception
    assert at.sidebar.multiselect[1].disabled is True, "Le filtre de statut cède la main à la vue focus."
    print("  Interface : bascule « Masquer les offres traitées » OK")


def test_palette_sombre_claire_et_repli() -> None:
    """La feuille de style couvre les deux thèmes natifs et le repli « auto »."""
    styles = {
        theme: _CSS_TEMPLATE.substitute(_token_context(theme))
        for theme in ("dark", "light", "auto")
    }
    for theme, css in styles.items():
        assert css.startswith("<style>") and css.endswith("</style>"), theme
        assert "$" not in css, f"Jeton non substitué dans la palette {theme}."
        assert ".sc-card" in css and ".sc-badge" in css and ".sc-kpis" in css, theme
    assert styles["dark"] != styles["light"], "Les palettes sombre et claire doivent différer."
    print("  Palette : jetons complets pour les thèmes sombre, clair et « auto » OK")

    assert _theme_type() == "auto"


def test_grille_sous_scores() -> None:
    """Mini-indicateurs de sous-scores, verrou bloquant et raisonnement du juge."""
    scored = {
        **RERANKED,
        "sub_scores": {
            "modeling_depth": 5,
            "mentorship_team": 4,
            "career_leverage": 4,
            "pfe_compatibility": 5,
        },
        "hard_cap_triggered": None,
        "reasoning": "Calendrier aligné, mission de modélisation réelle, encadrement senior.",
    }
    markup = job_card_html(scored, ())

    assert "📐 Modélisation 5/5" in markup
    assert "👥 Encadrement 4/5" in markup
    assert "🚀 Carrière 4/5" in markup
    assert "📅 Calendrier PFE 5/5" in markup

    assert "📐 Modélisation : <b>5/5</b>" in markup, markup
    assert "👥 Équipe : <b>4/5</b>" in markup
    assert "🚀 Carrière : <b>4/5</b>" in markup
    assert "📅 PFE : <b>5/5</b>" in markup
    assert markup.index("sc-subscore-strip") < markup.index("<details"), (
        "Les mini-indicateurs doivent précéder l'accordéon (visibles d'emblée)."
    )

    assert "Analyse du juge (raisonnement)" in markup
    assert scored["reasoning"] in markup

    assert "Verrou bloquant" not in markup

    capped = {**scored, "hard_cap_triggered": "Reporting / dashboards BI"}
    capped_markup = job_card_html(capped, ())
    assert "Verrou bloquant" in capped_markup
    assert "Reporting / dashboards BI" in capped_markup
    assert "sc-alert" in capped_markup and "sc-tone-alert" in capped_markup
    assert capped_markup.index("sc-alert") < capped_markup.index("<details"), (
        "Le bandeau de verrou doit être visible en tête de carte."
    )

    plain = job_card_html(JOB, ())
    assert "Grille d'évaluation" not in plain
    assert "sc-subscore-strip" not in plain
    assert "sc-alert" not in plain
    print("  Carte : mini-indicateurs (/5), verrou bloquant et raisonnement OK")


def test_telemetrie_panneau() -> None:
    """Le panneau de télémétrie restitue les runs et les raisons d'arrêt."""
    runs = [
        {
            "started_at": datetime(2026, 9, 16, 21, 21, 51),
            "status": RUN_OK,
            "sources": "linkedin,jobteaser",
            "total_found": 30,
            "total_validated": 24,
            "total_inserted": 24,
            "total_duplicates": 6,
            "notes": None,
        },
        {
            "started_at": datetime(2026, 9, 16, 20, 0, 0),
            "status": RUN_PARTIAL,
            "sources": "linkedin",
            "total_found": 17,
            "total_validated": 17,
            "total_inserted": 17,
            "total_duplicates": 0,
            "notes": "max_pages",
        },
    ]
    runs_table = _telemetry_runs_table(runs)
    assert "16/09 21:21" in runs_table
    assert "Terminé" in runs_table and "Partiel (flux tronqué)" in runs_table
    assert "max_pages" in runs_table

    passes = [
        {
            "source": "linkedin",
            "query": "Stage Machine Learning",
            "mode": "freshness",
            "started_at": datetime(2026, 9, 16, 21, 21, 20),
            "finished_at": datetime(2026, 9, 16, 21, 21, 51),
            "pages_fetched": 1,
            "cards_seen": 10,
            "jobs_kept": 0,
            "jobs_known": 10,
            "stop_reason": "duplicate_page",
        },
        {
            "source": "linkedin",
            "query": "Stage Data Scientist",
            "mode": "relevance",
            "started_at": datetime(2026, 9, 16, 21, 20, 0),
            "finished_at": datetime(2026, 9, 16, 21, 20, 40),
            "pages_fetched": 6,
            "cards_seen": 60,
            "jobs_kept": 17,
            "jobs_known": 30,
            "stop_reason": "max_pages",
        },
    ]
    passes_table = _telemetry_passes_table(passes)
    assert "Fraîcheur (tri par date)" in passes_table
    assert "Rattrapage (tri par pertinence)" in passes_table
    assert "Page déjà vue (pagination stagnante)" in passes_table
    assert "Plafond de pages atteint (flux potentiellement tronqué)" in passes_table
    assert "sc-tone-alert" in passes_table

    assert _telemetry_runs_table([]) == "" and _telemetry_passes_table([]) == ""
    print("  Télémétrie : tables des runs et des raisons d'arrêt OK")


def test_onglet_telemetrie_interface() -> None:
    """La page de télémétrie est rendue dans l'application."""
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "statistiques.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    markup = " ".join(element.value for element in at.markdown)
    assert "Runs de collecte" in markup, "Le tableau des runs doit être rendu."
    assert "Dernières passes (raison d'arrêt)" in markup
    db = _open_database()
    has_runs, has_seen = db.count_runs() > 0, db.count_seen_jobs() > 0
    db.engine.dispose()
    if has_runs:
        assert "Compteurs par source" in markup, "Les compteurs par source doivent être rendus."
        assert "Objectifs de collecte" in markup, "L'état des objectifs doit être rendu."
    if has_seen:
        assert "Ce qui est refusé" in markup, "La répartition des refus doit être rendue."
    print("  Interface : page de télémétrie OK")


def test_compteurs_de_collecte_et_refus() -> None:
    """Le panneau expose cherchées / refusées / déjà vues / acceptées et l'état des objectifs."""
    counters = [
        {
            "run_id": "run-1",
            "source": "linkedin",
            "pages": 7,
            "http_requests": 6,
            "cards_seen": 100,
            "jobs_kept": 25,
            "jobs_known": 15,
            "jobs_duplicate": 10,
            "jobs_rejected": 50,
            "jobs_out_of_window": 0,
            "already_seen": 25,
            "refused": 50,
        },
        {
            "run_id": "run-1",
            "source": "jobteaser",
            "cards_seen": 0,
            "jobs_kept": 0,
            "jobs_known": 0,
            "jobs_duplicate": 0,
            "jobs_rejected": 0,
            "jobs_out_of_window": 0,
            "already_seen": 0,
            "refused": 0,
        },
    ]
    stamps = {"run-1": "16/09 23:21"}

    strip = _counters_strip(
        {"cards_seen": 100, "refused": 50, "already_seen": 25, "jobs_kept": 25}
    )
    assert 'class="sc-kpis"' in strip, strip
    for label in ("Cherchées", "Refusées", "Déjà vues", "Acceptées"):
        assert label in strip, label
    assert "100" in strip and "25" in strip

    table = _collection_counters_table(counters, stamps)
    assert "16/09 23:21" in table and "linkedin" in table and "jobteaser" in table
    assert "50 hors sujet" in table, table
    assert "15 en base" in table and "10 doublons du run" in table, table
    assert _collection_counters_table([], stamps) == ""

    objectives = [
        {
            "run_id": "run-1",
            "source": "linkedin",
            "mode": "freshness",
            "pages": 12,
            "cards_seen": 120,
            "jobs_kept": 40,
            "target_new": 40,
            "reached": True,
            "incomplete": False,
        },
        {
            "run_id": "run-1",
            "source": "linkedin",
            "mode": "relevance",
            "pages": 3,
            "cards_seen": 30,
            "jobs_kept": 6,
            "target_new": 10,
            "reached": False,
            "incomplete": False,
        },
    ]
    objectives_table = _objectives_table(objectives, stamps)
    assert "objectif atteint (40/40)" in objectives_table, objectives_table
    assert "6/10 — vivier épuisé" in objectives_table, objectives_table
    assert tone_class("positive") in objectives_table

    truncated = _objectives_table(
        [{**objectives[1], "incomplete": True}], stamps
    )
    assert "6/10 — flux tronqué (à relancer)" in truncated, truncated
    assert tone_class("alert") in truncated

    refusals = _refusals_table(
        [
            {"decision": "REJECTED_BI", "rejection_reason": "« power bi »", "total": 22},
            {"decision": "KNOWN", "rejection_reason": None, "total": 9},
        ]
    )
    assert "Refusée — hors sujet" in refusals, refusals
    assert "Déjà connue" in refusals and "« power bi »" in refusals
    assert _refusals_table([]) == ""
    print("  Télémétrie : compteurs (cherchées/refusées/déjà vues/acceptées) et objectifs OK")


def test_kanban_interface() -> None:
    """La page Kanban de suivi des candidatures est rendue sans erreur."""
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "kanban.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    markup = " ".join(element.value for element in at.markdown)
    assert "Tableau Kanban" in markup, "Le titre Kanban doit être rendu."
    print("  Interface : page Kanban OK")


def test_parametres_interface() -> None:
    """La page Paramètres (configuration, sources, notation) est rendue sans erreur."""
    at = AppTest.from_file(str(PROJECT_ROOT / "pages" / "parametres.py"), default_timeout=60).run()
    assert not at.exception, at.exception
    markup = " ".join(element.value for element in at.markdown)
    assert "Paramètres &amp; Profil" in markup or "Paramètres" in markup
    # Vérifie que le sélecteur radio de notation est bien présent
    assert len(at.radio) >= 1
    # Vérifie que les options incluent l'évaluation des offres non notées
    radio_options = at.radio[0].options
    assert any("non notées" in opt for opt in radio_options)
    print("  Interface : page Paramètres OK")


def main() -> None:
    """Exécute l'ensemble des tests du dashboard."""
    test_helpers_score_et_alignement()
    test_helpers_texte_dates_et_technos()
    test_filtres_et_repartition()
    test_carte_html()
    test_grille_sous_scores()
    test_telemetrie_panneau()
    test_compteurs_de_collecte_et_refus()
    test_palette_sombre_claire_et_repli()
    test_onglet_telemetrie_interface()
    test_kanban_interface()
    test_parametres_interface()
    test_interface_streamlit()
    print("TOUS LES TESTS PASSENT")


if __name__ == "__main__":
    main()
