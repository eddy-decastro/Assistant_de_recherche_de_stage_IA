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

import app
from src.config import load_config
from src.storage.database import Database

# Emojis décoratifs bannis de l'interface (refonte « outil d'ingénierie »).
BANNED_EMOJI = (
    "🔍", "📄", "🏢", "🔗", "🚀", "👉", "🤖", "🎯", "🌟", "✅", "⚠️", "🚫", "⚖️", "🎛️", "📍",
)

# Contenu provenant des plateformes : titres, métadonnées et extraits de fiche.
# Le contrôle d'emoji porte sur la micro-copie du dashboard, pas sur les annonces
# (15 descriptions en contiennent après le rattrapage des fiches de poste).
_OFFER_CONTENT_PATTERNS = (
    r'<p class="sc-excerpt">.*?</p>',
    r'<h3 class="sc-card-title">.*?</h3>',
    r'<div class="sc-card-meta">.*?</div>',
    # Les badges de la grille d'évaluation portent des icônes demandées par le
    # produit (📐 👥 🚀 📅) : ils relèvent du contenu, pas de la micro-copie.
    r'<span class="sc-badge[^"]*sc-subscore[^"]*">.*?</span>',
    # Idem pour la ligne de mini-indicateurs affichée sur la carte.
    r'<div class="sc-subscore-strip">.*?</div>',
    # Bandeaux d'alerte (verrou bloquant, état de la télémétrie) : icônes
    # fonctionnelles demandées par le produit (⚠️ / ✓).
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
    "status": app.STATUS_NEW,
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
    "company_tier": app.TIER_1,
    "rerank_score": 88.0,
    "verdict": app.VERDICT_EXCELLENT,
    "match_reasons": ["Modélisation PyTorch avancée", "Équipe de recherche reconnue"],
    "red_flags": ["Périmètre susceptible d'évoluer"],
    "tech_stack": ["PyTorch", "GNN"],
    "description": _DESCRIPTION,
    "status": app.STATUS_APPLIED,
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
    assert app.effective_score(JOB) == 46.36
    assert app.effective_score(RERANKED) == 88.0
    assert app.is_reranked(JOB) is False
    assert app.is_reranked(RERANKED) is True
    assert app.score_alignment(85)[0] == "Cœur de cible"
    assert app.score_alignment(65)[0] == "Pertinent"
    assert app.score_alignment(45)[0] == "Secondaire"
    assert app.score_alignment(10)[0] == "Hors périmètre"
    assert app.tone_class(app.score_alignment(45)[1]) == "sc-tone-warn"
    print("  Helpers : score effectif et paliers d'alignement OK")


def test_helpers_texte_dates_et_technos() -> None:
    """Nettoyage de fiche, dates relatives, technologies clés, type de contrat."""
    assert app.clean_text("  a  b \n\n\n c ") == "a b \n c"
    long_text, truncated = app.excerpt("mot " * 200)
    assert truncated is True
    assert len(long_text) <= app.EXCERPT_LENGTH + 2
    assert app.excerpt("court") == ("court", False)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert app.relative_date(now - timedelta(seconds=30), now) == "À l'instant"
    assert app.relative_date(now - timedelta(minutes=10), now) == "Il y a 10 min"
    assert app.relative_date(now - timedelta(hours=5), now) == "Il y a 5 h"
    assert app.relative_date(now - timedelta(days=1), now) == "Hier"
    assert app.relative_date(now - timedelta(days=3), now) == "Il y a 3 j"
    assert app.relative_date(now - timedelta(days=90), now) == "Il y a 3 mois"
    assert app.relative_date(None) == "Date inconnue"
    assert app.parse_timestamp("2026-09-13T16:44:02.358898") is not None

    assert app.detected_technologies(RERANKED, ("PyTorch", "GNN", "SQL")) == ["PyTorch", "GNN"]
    assert app.detected_technologies(JOB, ()) == []
    assert app.detected_technologies({"title": "Stage NLP", "description": None}, ("NLP",)) == ["NLP"]
    assert app.contract_label(JOB) == "Stage"
    assert app.contract_label({"title": "Contrat d'apprentissage data"}) == "Alternance"
    assert app.contract_label({"title": "Data analyst"}) is None
    print("  Helpers : texte, dates relatives, technologies et contrat OK")


def test_filtres_et_repartition() -> None:
    """Filtrage combiné, vue focus, recherche et répartition par plateforme."""
    jobs = [JOB, RERANKED]
    assert len(app.filter_jobs(jobs, app.Filters(statuses=app.STATUS_ORDER))) == 2
    assert len(app.filter_jobs(jobs, app.Filters(min_score=60))) == 1
    assert len(app.filter_jobs(jobs, app.Filters(llm_only=True))) == 1
    assert len(app.filter_jobs(jobs, app.Filters(sources=("jobteaser",)))) == 2
    assert app.filter_jobs(jobs, app.Filters(sources=("linkedin",))) == []
    assert len(app.filter_jobs(jobs, app.Filters(statuses=(app.STATUS_APPLIED,)))) == 1
    assert len(app.filter_jobs(jobs, app.Filters(tiers=(app.TIER_1,)))) == 1
    assert app.filter_jobs(jobs, app.Filters(exclude_esn=True, tiers=(app.TIER_ESN,))) == []
    assert len(app.filter_jobs(jobs, app.Filters(limit=1))) == 1
    assert len(app.filter_jobs(jobs, app.Filters(query="mistral pytorch"))) == 1
    assert app.filter_jobs(jobs, app.Filters(query="zz-introuvable")) == []

    # La vue focus (masquer les offres traitées) prime sur la sélection de statuts.
    focus = app.Filters(statuses=(app.STATUS_APPLIED,), hide_processed=True)
    assert [job["id"] for job in app.filter_jobs(jobs, focus)] == ["offre-1"]

    assert app.Filters().is_default() is True
    assert app.Filters(min_score=10).is_default() is False
    assert app.Filters(group_by_source=True).is_default() is True, "Le mode d'affichage n'est pas un filtre."
    assert app.source_distribution(jobs) == [("JobTeaser", 2, app.SOURCE_COLORS["jobteaser"])]
    assert [label for label, _ in app.group_jobs_by_source(jobs)] == ["JobTeaser"]
    print("  Filtres : score, statuts, typologie, plateformes, recherche et répartition OK")


def test_carte_html() -> None:
    """La carte d'offre est un fragment HTML autonome, échappé et complet."""
    card = app.job_card_html(RERANKED, ("PyTorch", "GNN"))
    assert card.startswith('<div class="sc-card">') and card.endswith("</div>")
    assert card.count('<div class="sc-card">') == 1
    assert "Détails &amp; évaluation" in card, "L'accordéon doit être libellé explicitement."
    assert "Points forts" in card and "Points d'attention" in card
    assert "Modélisation PyTorch avancée" in card
    assert "Lire la fiche complète" in card
    assert 'href="https://example.com/offre?a=1&amp;b=2"' in card, "L'URL doit être échappée."
    assert 'class="sc-chip">PyTorch<' in card
    assert "Cœur de cible" in card and "sc-tone-positive" in card
    assert "Excellent" in card

    unreanked = app.job_card_html(JOB, ())
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
    ]
    assert [widget.label for widget in at.sidebar.slider] == ["Score R&D minimal"]
    assert [widget.label for widget in at.sidebar.toggle] == [
        "Verdict LLM uniquement",
        "Masquer les offres traitées",
        "Exclure les ESN",
    ]
    assert [widget.label for widget in at.sidebar.selectbox] == ["Mode de flux", "Offres affichées"]
    assert [widget.label for widget in at.sidebar.button] == [
        "Actualiser la vue",
        "Relancer collecte & scoring",
    ]

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

    # 3. Aucun emoji décoratif dans les libellés d'INTERFACE (le contenu des offres
    #    est exclu : une annonce peut légitimement en contenir).
    rendered = ui_labels_only(" ".join([markup] + [element.value for element in at.caption]))
    for emoji in BANNED_EMOJI:
        assert emoji not in rendered, f"Emoji décoratif détecté : {emoji}"

    sample = app.job_card_html({**JOB, "description": "Annonce rédigée avec 🔍 et 🚀"}, ())
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
    at.sidebar.selectbox[0].select(app.DISPLAY_GROUPED).run()
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
        theme: app._CSS_TEMPLATE.substitute(app._token_context(theme))
        for theme in ("dark", "light", "auto")
    }
    for theme, css in styles.items():
        assert css.startswith("<style>") and css.endswith("</style>"), theme
        assert "$" not in css, f"Jeton non substitué dans la palette {theme}."
        assert ".sc-card" in css and ".sc-badge" in css and ".sc-kpis" in css, theme
    assert styles["dark"] != styles["light"], "Les palettes sombre et claire doivent différer."
    print("  Palette : jetons complets pour les thèmes sombre, clair et « auto » OK")

    # Hors session Streamlit, la détection retombe proprement sur « auto ».
    assert app._theme_type() == "auto"

    # En session, le type de thème natif est repris tel quel.
    class _Theme:
        type = "dark"

    class _Context:
        theme = _Theme()

    class _StreamlitStub:
        context = _Context()

    original = app.st
    app.st = _StreamlitStub()  # type: ignore[assignment]
    try:
        assert app._theme_type() == "dark"
        _Theme.type = "light"
        assert app._theme_type() == "light"
        _Theme.type = None
        assert app._theme_type() == "auto"
    finally:
        app.st = original  # type: ignore[assignment]
    print("  Palette : détection du thème natif Streamlit OK")


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
    markup = app.job_card_html(scored, ())

    # 1. Détail (accordéon) : libellés longs + icônes.
    assert "📐 Modélisation 5/5" in markup
    assert "👥 Encadrement 4/5" in markup
    assert "🚀 Carrière 4/5" in markup
    assert "📅 Calendrier PFE 5/5" in markup

    # 2. Mini-indicateurs compacts, VISIBLES sans ouvrir l'accordéon.
    assert "📐 Modélisation : <b>5/5</b>" in markup, markup
    assert "👥 Équipe : <b>4/5</b>" in markup
    assert "🚀 Carrière : <b>4/5</b>" in markup
    assert "📅 PFE : <b>5/5</b>" in markup
    assert markup.index("sc-subscore-strip") < markup.index("<details"), (
        "Les mini-indicateurs doivent précéder l'accordéon (visibles d'emblée)."
    )

    # 3. Raisonnement du juge (produit AVANT le score) affiché dans l'accordéon.
    assert "Analyse du juge (raisonnement)" in markup
    assert scored["reasoning"] in markup

    # 4. Aucun verrou : ni bandeau, ni mention.
    assert "Verrou bloquant" not in markup

    # 5. Verrou déclenché : bandeau d'alerte en tête de carte.
    capped = {**scored, "hard_cap_triggered": "Reporting / dashboards BI"}
    capped_markup = app.job_card_html(capped, ())
    assert "Verrou bloquant" in capped_markup
    assert "Reporting / dashboards BI" in capped_markup
    assert "sc-alert" in capped_markup and "sc-tone-alert" in capped_markup
    assert capped_markup.index("sc-alert") < capped_markup.index("<details"), (
        "Le bandeau de verrou doit être visible en tête de carte."
    )

    # 6. Une offre non évaluée n'affiche ni grille, ni mini-indicateurs, ni bandeau.
    plain = app.job_card_html(JOB, ())
    assert "Grille d'évaluation" not in plain
    assert "sc-subscore-strip" not in plain
    assert "sc-alert" not in plain
    print("  Carte : mini-indicateurs (/5), verrou bloquant et raisonnement OK")


def test_telemetrie_panneau() -> None:
    """Le panneau de télémétrie restitue les runs et les raisons d'arrêt."""
    runs = [
        {
            "started_at": datetime(2026, 9, 16, 21, 21, 51),
            "status": app.RUN_OK,
            "sources": "linkedin,jobteaser",
            "total_found": 30,
            "total_validated": 24,
            "total_inserted": 24,
            "total_duplicates": 6,
            "notes": None,
        },
        {
            "started_at": datetime(2026, 9, 16, 20, 0, 0),
            "status": app.RUN_PARTIAL,
            "sources": "linkedin",
            "total_found": 17,
            "total_validated": 17,
            "total_inserted": 17,
            "total_duplicates": 0,
            "notes": "max_pages",
        },
    ]
    runs_table = app._telemetry_runs_table(runs)
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
    passes_table = app._telemetry_passes_table(passes)
    assert "Fraîcheur (tri par date)" in passes_table
    assert "Rattrapage (tri par pertinence)" in passes_table
    assert "Page déjà vue (pagination stagnante)" in passes_table
    assert "Plafond de pages atteint (flux potentiellement tronqué)" in passes_table
    # Une passe tronquée est signalée comme telle (tone d'alerte).
    assert "sc-tone-alert" in passes_table

    assert app._telemetry_runs_table([]) == "" and app._telemetry_passes_table([]) == ""
    print("  Télémétrie : tables des runs et des raisons d'arrêt OK")


def test_onglet_telemetrie_interface() -> None:
    """L'onglet « Télémétrie des collectes » est rendu dans l'application."""
    at = AppTest.from_file(str(app.__file__), default_timeout=60).run()
    assert not at.exception, at.exception
    labels = [tab.label for tab in at.tabs]
    assert "Télémétrie des collectes" in labels, labels
    markup = " ".join(element.value for element in at.markdown)
    assert "Runs de collecte" in markup, "Le tableau des runs doit être rendu."
    assert "Dernières passes (raison d'arrêt)" in markup
    print(f"  Interface : onglets {labels} et panneau de télémétrie OK")


def main() -> None:
    """Exécute l'ensemble des tests du dashboard."""
    test_helpers_score_et_alignement()
    test_helpers_texte_dates_et_technos()
    test_filtres_et_repartition()
    test_carte_html()
    test_grille_sous_scores()
    test_telemetrie_panneau()
    test_palette_sombre_claire_et_repli()
    test_onglet_telemetrie_interface()
    test_interface_streamlit()
    print("TOUS LES TESTS PASSENT")


if __name__ == "__main__":
    main()
