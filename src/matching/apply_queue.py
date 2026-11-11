"""File d'attente des candidatures pour « Claude in Chrome » (plan C1, C2, C3).

Eddy sélectionne des offres (Pack Claude) : chacune entre dans la file avec son
dossier déjà préparé (lettre, accroche, chemins des PDF). Claude in Chrome ouvre la
page ``/file``, traite l'offre courante, puis renvoie un compte rendu « caveman »
de 2 phrases au plus : l'offre passe POSTULÉ, ou reste dans la file en échec avec
la raison.

La file vit dans une table à part (``apply_queue``), créée ici avec
``CREATE TABLE IF NOT EXISTS`` sur le moteur de l'objet ``Database`` : le schéma de
``jobs`` n'est pas touché. La table a une clé primaire, donc la fusion R2
(``src.storage.db_merge``) la synchronise comme les autres.

Aucun appel LLM à la lecture : le dossier est construit une seule fois, à l'ajout.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from sqlalchemy import text

from src.constants import STATUS_APPLIED, STATUS_INTERVIEW

QUEUE_URL = "http://localhost:3000/file"

STATE_PENDING = "pending"
STATE_FAILED = "failed"
STATE_APPLIED = "applied"
QUEUE_STATES = (STATE_PENDING, STATE_FAILED, STATE_APPLIED)

OUTCOME_APPLIED = "applied"
OUTCOME_FAILED = "failed"
OUTCOMES = (OUTCOME_APPLIED, OUTCOME_FAILED)

REPORT_MAX_CHARS = 300
REPORT_MAX_SENTENCES = 2

# Statuts où l'offre est déjà candidatée : inutile de la remettre dans la file.
_ALREADY_APPLIED = frozenset({STATUS_APPLIED, STATUS_INTERVIEW})

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS apply_queue (
    job_id VARCHAR(64) PRIMARY KEY,
    position INTEGER NOT NULL DEFAULT 0,
    state VARCHAR(20) NOT NULL DEFAULT 'pending',
    report TEXT,
    method VARCHAR(40),
    needs_account INTEGER NOT NULL DEFAULT 0,
    auto_submit INTEGER NOT NULL DEFAULT 0,
    bundle_json TEXT,
    created_at VARCHAR(32),
    updated_at VARCHAR(32)
)
"""
# Colonnes ajoutées après coup sur une table existante (migration idempotente).
_EXPECTED_COLUMNS: dict[str, str] = {
    "report": "TEXT",
    "method": "VARCHAR(40)",
    "needs_account": "INTEGER NOT NULL DEFAULT 0",
    "auto_submit": "INTEGER NOT NULL DEFAULT 0",
    "bundle_json": "TEXT",
    "created_at": "VARCHAR(32)",
    "updated_at": "VARCHAR(32)",
}


# --------------------------------------------------------------------------- #
# C3 — Méthode de candidature
# --------------------------------------------------------------------------- #
APPLY_METHODS: dict[str, dict[str, Any]] = {
    "wttj": {
        "label": "Welcome to the Jungle",
        "needs_account": False,
        "hint": "Bouton « Postuler » de WTTJ avec la session déjà ouverte. "
        "S'il renvoie vers le site de l'entreprise, suis le lien et applique les mêmes règles.",
    },
    "linkedin_easy": {
        "label": "LinkedIn (candidature simplifiée)",
        "needs_account": False,
        "hint": "Bouton « Candidature simplifiée » avec la session LinkedIn déjà ouverte.",
    },
    "linkedin_externe": {
        "label": "LinkedIn (site de l'entreprise)",
        "needs_account": False,
        "hint": "Clique « Postuler » puis suis le lien vers le site de l'entreprise. "
        "Si ce site demande un compte, applique la règle compte.",
    },
    "lever": {
        "label": "Lever",
        "needs_account": False,
        "hint": "Formulaire Lever sur une seule page, sans compte.",
    },
    "greenhouse": {
        "label": "Greenhouse",
        "needs_account": False,
        "hint": "Formulaire Greenhouse sur une seule page, sans compte.",
    },
    "ashby": {
        "label": "Ashby",
        "needs_account": False,
        "hint": "Formulaire Ashby (onglet « Application »), sans compte.",
    },
    "smartrecruiters": {
        "label": "SmartRecruiters",
        "needs_account": False,
        "hint": "Formulaire SmartRecruiters, en général sans compte.",
    },
    "workday": {
        "label": "Workday (compte requis)",
        "needs_account": True,
        "hint": "Workday exige un compte candidat : remplis seulement si la session d'Eddy est déjà ouverte.",
    },
    "taleo": {
        "label": "Taleo (compte requis)",
        "needs_account": True,
        "hint": "Taleo exige un compte candidat : remplis seulement si la session d'Eddy est déjà ouverte.",
    },
    "successfactors": {
        "label": "SuccessFactors (compte requis)",
        "needs_account": True,
        "hint": "SuccessFactors exige un compte candidat : remplis seulement si la session d'Eddy est déjà ouverte.",
    },
    "jobteaser": {
        "label": "JobTeaser (session école)",
        "needs_account": False,
        "hint": "Session JobTeaser de l'école déjà ouverte. Le CV y est souvent obligatoire.",
    },
    "email": {
        "label": "Candidature par e-mail",
        "needs_account": False,
        "hint": "Pas de formulaire. N'envoie aucun e-mail : marque « Échec » en donnant "
        "l'adresse, Eddy enverra lui-même.",
    },
    "inconnu": {
        "label": "Méthode inconnue",
        "needs_account": False,
        "hint": "Méthode non reconnue : ouvre la page et cherche le bouton « Postuler ».",
    },
}

# Ordre important : les ATS à compte passent avant les règles plus larges.
_HOST_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("workday", re.compile(r"myworkdayjobs\.com|myworkdaysite\.com|\.workday\.com", re.I)),
    ("taleo", re.compile(r"taleo\.net", re.I)),
    ("successfactors", re.compile(r"successfactors\.(?:com|eu)|jobs\.sap\.com|sapsf\.(?:com|eu)", re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.(?:eu\.)?lever\.co", re.I)),
    ("greenhouse", re.compile(r"greenhouse\.io", re.I)),
    ("ashby", re.compile(r"ashbyhq\.com", re.I)),
    ("smartrecruiters", re.compile(r"smartrecruiters\.com", re.I)),
    ("wttj", re.compile(r"welcometothejungle\.com|wttj\.co", re.I)),
    ("jobteaser", re.compile(r"jobteaser\.com", re.I)),
    ("linkedin", re.compile(r"linkedin\.com", re.I)),
)
_SOURCE_RULES: tuple[tuple[str, str], ...] = (
    ("workday", "workday"),
    ("taleo", "taleo"),
    ("successfactors", "successfactors"),
    ("lever", "lever"),
    ("greenhouse", "greenhouse"),
    ("ashby", "ashby"),
    ("smartrecruiters", "smartrecruiters"),
    ("wttj", "wttj"),
    ("welcome", "wttj"),
    ("jobteaser", "jobteaser"),
    ("linkedin", "linkedin"),
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_EASY_APPLY_HINTS = ("candidature simplifiée", "easy apply")


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "oui", "yes")


def _linkedin_variant(job: Mapping[str, Any]) -> str:
    easy = job.get("easy_apply")
    if easy is not None:
        return "linkedin_easy" if _is_true(easy) else "linkedin_externe"
    apply_url = str(job.get("apply_url") or "")
    if apply_url.startswith("http") and "linkedin.com" not in apply_url.lower():
        return "linkedin_externe"
    description = str(job.get("description") or "").lower()
    if any(hint in description for hint in _EASY_APPLY_HINTS):
        return "linkedin_easy"
    # Par défaut : la plupart des stages renvoient vers le site de l'entreprise.
    return "linkedin_externe"


def detect_apply_method(job: Mapping[str, Any]) -> dict[str, Any]:
    """Déduit la méthode de candidature d'une offre depuis ses URL et sa source.

    Retourne ``{"method", "label", "needs_account", "hint", "contact_email"}``.
    ``method`` vaut une clé de ``APPLY_METHODS``.
    """
    urls = [str(job.get(key) or "").strip() for key in ("apply_url", "url", "canonical_url")]
    urls = [u for u in urls if u]
    method = ""
    contact_email = ""

    mailto = next((u for u in urls if u.lower().startswith("mailto:")), "")
    if mailto:
        method = "email"
        contact_email = mailto[7:].split("?", 1)[0]

    if not method:
        for url in urls:
            for name, pattern in _HOST_RULES:
                if pattern.search(url):
                    method = name
                    break
            if method:
                break

    if not method:
        source = str(job.get("source") or "").lower()
        method = next((name for needle, name in _SOURCE_RULES if needle in source), "")

    if not method:
        found = _EMAIL_RE.search(str(job.get("description") or ""))
        if found and not any(u.startswith("http") for u in urls):
            method = "email"
            contact_email = found.group(0)

    if method == "linkedin":
        method = _linkedin_variant(job)
    if method not in APPLY_METHODS:
        method = "inconnu"

    spec = APPLY_METHODS[method]
    return {
        "method": method,
        "label": spec["label"],
        "needs_account": bool(spec["needs_account"]),
        "hint": spec["hint"] + (f" Adresse : {contact_email}." if contact_email else ""),
        "contact_email": contact_email,
    }


# --------------------------------------------------------------------------- #
# C1 — Compte rendu caveman
# --------------------------------------------------------------------------- #
_SENTENCE_END = re.compile(r"[.!?…]+(?=\s|$)")


def count_sentences(report: str) -> int:
    """Nombre de phrases : une phrase se termine par . ! ? … suivi d'un blanc ou de la fin."""
    parts = [p for p in _SENTENCE_END.split(report.strip()) if p.strip()]
    return len(parts)


def validate_report(report: Any) -> str:
    """Valide le compte rendu de Claude : non vide, ≤ 2 phrases, ≤ 300 caractères.

    Retourne le texte nettoyé (espaces multiples réduits) ou lève ``ValueError``.
    """
    cleaned = re.sub(r"\s+", " ", str(report or "")).strip()
    if not cleaned:
        raise ValueError("Compte rendu vide : écris 1 ou 2 phrases courtes.")
    if len(cleaned) > REPORT_MAX_CHARS:
        raise ValueError(
            f"Compte rendu trop long ({len(cleaned)} caractères, maximum {REPORT_MAX_CHARS})."
        )
    sentences = count_sentences(cleaned)
    if sentences > REPORT_MAX_SENTENCES:
        raise ValueError(
            f"Compte rendu trop long ({sentences} phrases, maximum {REPORT_MAX_SENTENCES})."
        )
    return cleaned


def _report_needs_eddy(report: str) -> bool:
    lowered = report.lower()
    return "eddy" in lowered or ("cv" in lowered and "joindre" in lowered)


# --------------------------------------------------------------------------- #
# Stockage
# --------------------------------------------------------------------------- #
def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")


def _engine(db: Any) -> Any:
    engine = getattr(db, "engine", None)
    if engine is None:
        raise TypeError("La file d'attente exige un objet Database (attribut 'engine').")
    return engine


def ensure_queue_table(db: Any) -> None:
    """Crée la table ``apply_queue`` si besoin et ajoute les colonnes manquantes."""
    with _engine(db).begin() as conn:
        conn.execute(text(_TABLE_SQL))
        present = {row[1] for row in conn.execute(text("PRAGMA table_info(apply_queue)"))}
        for column, ddl in _EXPECTED_COLUMNS.items():
            if column not in present:
                conn.execute(text(f"ALTER TABLE apply_queue ADD COLUMN {column} {ddl}"))


def _load_bundle(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _job_status(conn: Any, job_id: str) -> str | None:
    row = conn.execute(text("SELECT status FROM jobs WHERE id = :id"), {"id": job_id}).fetchone()
    return row[0] if row else None


def enqueue_bundles(
    db: Any,
    bundles: Iterable[Mapping[str, Any]],
    *,
    auto_submit: bool = False,
) -> dict[str, Any]:
    """Ajoute des dossiers déjà préparés (``prepare_job_application_bundle``) à la file.

    Une offre déjà dans la file en attente ou en échec repasse en attente, en fin de
    file, avec son dossier mis à jour (le dernier compte rendu est conservé). Une offre
    déjà postulée (file ou statut POSTULÉ / ENTRETIEN) est ignorée.
    Retourne ``{"added": [ids], "requeued": [ids], "skipped": [ids]}``.
    """
    ensure_queue_table(db)
    added: list[str] = []
    requeued: list[str] = []
    skipped: list[str] = []
    stamp = _now()
    with _engine(db).begin() as conn:
        next_pos = conn.execute(text("SELECT COALESCE(MAX(position), 0) FROM apply_queue")).scalar() or 0
        for bundle in bundles:
            job_id = str(bundle.get("id") or "").strip()
            if not job_id:
                continue
            existing = conn.execute(
                text("SELECT state FROM apply_queue WHERE job_id = :id"), {"id": job_id}
            ).fetchone()
            if (existing and existing[0] == STATE_APPLIED) or _job_status(conn, job_id) in _ALREADY_APPLIED:
                skipped.append(job_id)
                continue
            method = bundle.get("apply_method") or detect_apply_method(bundle)
            next_pos += 1
            params = {
                "id": job_id,
                "position": next_pos,
                "state": STATE_PENDING,
                "method": method["method"],
                "needs_account": 1 if method["needs_account"] else 0,
                "auto_submit": 1 if auto_submit else 0,
                "bundle": json.dumps(dict(bundle), ensure_ascii=False, default=str),
                "stamp": stamp,
            }
            if existing:
                conn.execute(
                    text(
                        "UPDATE apply_queue SET position = :position, state = :state, method = :method, "
                        "needs_account = :needs_account, auto_submit = :auto_submit, "
                        "bundle_json = :bundle, updated_at = :stamp WHERE job_id = :id"
                    ),
                    params,
                )
                requeued.append(job_id)
            else:
                conn.execute(
                    text(
                        "INSERT INTO apply_queue (job_id, position, state, report, method, needs_account, "
                        "auto_submit, bundle_json, created_at, updated_at) VALUES (:id, :position, :state, "
                        "NULL, :method, :needs_account, :auto_submit, :bundle, :stamp, :stamp)"
                    ),
                    params,
                )
                added.append(job_id)
    return {"added": added, "requeued": requeued, "skipped": skipped}


def add_jobs_to_queue(
    db: Any,
    jobs: Iterable[Mapping[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    output_dir: Any = None,
    generate_missing_letters: bool = True,
    export_pdfs: bool = True,
    auto_submit: bool = False,
) -> dict[str, Any]:
    """Prépare le dossier de chaque offre puis l'ajoute à la file.

    Une offre dont le dossier échoue (lettre impossible à générer) n'empêche pas les
    autres : son erreur est rendue dans ``errors``. Retourne le résultat
    d'``enqueue_bundles`` complété de ``errors`` (``{job_id: message}``).
    """
    from src.config import load_config
    from src.matching.claude_pack import prepare_job_application_bundle

    cfg = config or load_config()
    bundles: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for job in jobs:
        job_id = str(job.get("id") or "")
        try:
            bundles.append(
                prepare_job_application_bundle(
                    dict(job),
                    db=db,
                    config=cfg,
                    output_dir=output_dir,
                    generate_missing_letter=generate_missing_letters,
                    export_pdf=export_pdfs,
                )
            )
        except RuntimeError as exc:
            errors[job_id] = str(exc)
    result = enqueue_bundles(db, bundles, auto_submit=auto_submit)
    result["errors"] = errors
    return result


def add_ids_to_queue(db: Any, job_ids: Iterable[str], **kwargs: Any) -> dict[str, Any]:
    """Comme ``add_jobs_to_queue`` à partir d'identifiants (offres lues dans ``db``).

    Les identifiants inconnus sont rendus dans ``missing``.
    """
    jobs: list[dict[str, Any]] = []
    missing: list[str] = []
    for job_id in job_ids:
        job = db.get_job(str(job_id))
        if job is None:
            missing.append(str(job_id))
        else:
            jobs.append(job)
    result = add_jobs_to_queue(db, jobs, **kwargs)
    result["missing"] = missing
    return result


def remove_from_queue(db: Any, job_id: str) -> bool:
    """Retire une offre de la file (sans toucher à son statut). False si absente."""
    ensure_queue_table(db)
    with _engine(db).begin() as conn:
        result = conn.execute(text("DELETE FROM apply_queue WHERE job_id = :id"), {"id": str(job_id)})
    return bool(result.rowcount)


_SELECT_ITEMS = (
    "SELECT q.job_id, q.position, q.state, q.report, q.method, q.needs_account, q.auto_submit, "
    "q.bundle_json, q.created_at, q.updated_at, j.status, j.applied_at "
    "FROM apply_queue q LEFT JOIN jobs j ON j.id = q.job_id"
)
# Les offres sans compte d'abord, puis l'ordre d'ajout.
_ORDER = " ORDER BY q.needs_account ASC, q.position ASC"


def _summary(row: Any) -> dict[str, Any]:
    bundle = _load_bundle(row.bundle_json)
    spec = APPLY_METHODS.get(row.method or "", APPLY_METHODS["inconnu"])
    report = row.report or ""
    applied_at = row.applied_at
    if isinstance(applied_at, datetime):
        applied_at = applied_at.isoformat(timespec="seconds")
    return {
        "job_id": row.job_id,
        "position": int(row.position or 0),
        "state": row.state,
        "report": report,
        "method": row.method or "inconnu",
        "method_label": spec["label"],
        "needs_account": bool(row.needs_account),
        "auto_submit": bool(row.auto_submit),
        "title": str(bundle.get("title") or ""),
        "company": str(bundle.get("company") or ""),
        "location": str(bundle.get("location") or ""),
        "url": str(bundle.get("url") or ""),
        "status": row.status,
        "applied_at": str(applied_at) if applied_at else None,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "to_finish": row.state == STATE_APPLIED and (not row.auto_submit or _report_needs_eddy(report)),
    }


def get_queue(db: Any, *, include_applied: bool = True) -> dict[str, Any]:
    """Liste la file (offres sans compte d'abord) et compte les offres par état.

    Retourne ``{"url", "items": [résumé…], "counts": {"pending", "failed", "applied"}}``.
    """
    ensure_queue_table(db)
    with _engine(db).connect() as conn:
        rows = conn.execute(text(_SELECT_ITEMS + _ORDER)).fetchall()
    items = [_summary(row) for row in rows]
    counts = {state: sum(1 for i in items if i["state"] == state) for state in QUEUE_STATES}
    if not include_applied:
        items = [i for i in items if i["state"] != STATE_APPLIED]
    return {"url": QUEUE_URL, "items": items, "counts": counts}


def build_standard_answers(bundle: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, str]:
    """Réponses types aux questions fréquentes des formulaires (aucun fait inventé)."""
    company = str(bundle.get("company") or "l'entreprise")
    reasons = [str(r) for r in (bundle.get("match_reasons") or []) if str(r).strip()][:2]
    techs = [str(t) for t in (bundle.get("tech_stack") or []) if str(t).strip()][:5]
    answers = {
        "Disponibilité": str(candidate.get("start_date") or ""),
        "Durée du stage": str(candidate.get("internship_duration") or ""),
        "Type de contrat": str(candidate.get("contract_type") or ""),
        "Niveau d'études": str(candidate.get("degree_level") or ""),
        "École / formation": str(candidate.get("school") or ""),
        "Autorisation de travail / sponsorship": str(candidate.get("work_authorization") or ""),
        "Prétentions salariales": "Gratification de stage selon la grille de l'entreprise.",
        "Comment avez-vous connu l'offre ?": str(bundle.get("source") or "Site d'offres d'emploi"),
        f"Pourquoi {company} ?": str(bundle.get("short_motivation") or bundle.get("short_pitch") or ""),
    }
    if reasons:
        answers["Points forts pour ce poste"] = " ; ".join(reasons)
    if techs:
        answers["Compétences techniques"] = ", ".join(techs)
    return {k: v for k, v in answers.items() if v}


def _method_hint(item: Mapping[str, Any], bundle: Mapping[str, Any]) -> str:
    stored = bundle.get("apply_method") or {}
    if stored.get("method") == item.get("method") and stored.get("hint"):
        return str(stored["hint"])
    return APPLY_METHODS.get(str(item.get("method") or ""), APPLY_METHODS["inconnu"])["hint"]


def offer_instructions(item: Mapping[str, Any], bundle: Mapping[str, Any]) -> list[str]:
    """Consignes propres à l'offre courante, affichées telles quelles sur ``/file``."""
    lines = [f"Méthode : {item.get('method_label') or ''}. {_method_hint(item, bundle)}".strip()]
    if item.get("needs_account"):
        lines.append(
            "Compte requis : si Eddy est déjà connecté (session ouverte sur ce site), remplis "
            "normalement. Si une page de connexion ou de création de compte s'affiche, ne crée "
            "aucun compte et ne saisis aucun mot de passe : clique « Échec » avec le compte rendu "
            "« Échec : compte requis, Eddy se connecte. » et passe à la suite."
        )
    lines.append(
        "Lettre : colle le texte de la lettre dans le champ prévu. Tu ne peux joindre aucun PDF."
    )
    lines.append(
        "CV : si un CV est obligatoire et non joint, remplis tout le reste, ne soumets pas, "
        "laisse l'onglet ouvert et écris « CV à joindre » dans le compte rendu."
    )
    if item.get("auto_submit"):
        lines.append("Envoi : si rien d'obligatoire ne manque, vérifie chaque champ puis clique Envoyer.")
    else:
        lines.append("Envoi : ne clique jamais sur Envoyer / Submit. Laisse l'onglet prêt, Eddy validera.")
    lines.append(
        "Compte rendu : 2 phrases maximum, 300 caractères maximum, style caveman "
        "(ex. « Moi remplir formulaire Thales. CV pas mis, Eddy mettre CV puis cliquer Envoyer. »)."
    )
    return lines


def _full_item(row: Any, *, config: dict[str, Any] | None, remaining: int) -> dict[str, Any]:
    from src.matching.claude_pack import build_candidate_form_data

    item = _summary(row)
    bundle = _load_bundle(row.bundle_json)
    candidate = build_candidate_form_data(config)
    cv_pdf = str(bundle.get("cv_pdf_path") or candidate.get("cv_pdf_path") or "")
    method_info = bundle.get("apply_method") or {}
    item.update(
        {
            "method_hint": _method_hint(item, bundle),
            "contact_email": str(method_info.get("contact_email") or ""),
            "candidate": candidate,
            "short_pitch": str(bundle.get("pitch_300") or ""),
            "short_motivation": str(bundle.get("short_motivation") or ""),
            "cover_letter": str(bundle.get("cover_letter") or ""),
            "answers": build_standard_answers(bundle, candidate),
            "files": {
                "cover_letter_pdf": str(bundle.get("cover_letter_pdf_path") or ""),
                "cv_pdf": cv_pdf,
                "offer_dir": str(bundle.get("offer_dir") or ""),
            },
            "instructions": offer_instructions(item, bundle),
            "remaining": remaining,
        }
    )
    return item


def get_queue_item(db: Any, job_id: str, *, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Dossier complet d'une offre de la file (même format que ``get_current``)."""
    ensure_queue_table(db)
    with _engine(db).connect() as conn:
        row = conn.execute(text(_SELECT_ITEMS + " WHERE q.job_id = :id"), {"id": str(job_id)}).fetchone()
        remaining = conn.execute(
            text("SELECT COUNT(*) FROM apply_queue WHERE state = :s"), {"s": STATE_PENDING}
        ).scalar()
    return _full_item(row, config=config, remaining=int(remaining or 0)) if row else None


def get_current(db: Any, *, config: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Offre courante : la première en attente (sans compte d'abord). ``None`` si file vide.

    Les offres en échec restent visibles dans ``get_queue`` mais ne sont plus proposées
    tant qu'Eddy ne les a pas remises dans la file.
    """
    ensure_queue_table(db)
    with _engine(db).connect() as conn:
        rows = conn.execute(
            text(_SELECT_ITEMS + " WHERE q.state = :s" + _ORDER), {"s": STATE_PENDING}
        ).fetchall()
    if not rows:
        return None
    return _full_item(rows[0], config=config, remaining=len(rows))


def record_result(db: Any, job_id: str, outcome: str, report: Any) -> dict[str, Any]:
    """Enregistre le retour de Claude sur une offre de la file.

    - ``outcome="applied"`` : statut POSTULÉ (``applied_at`` renseigné par la base),
      sauf si l'offre est déjà POSTULÉ ou ENTRETIEN (jamais de rétrogradation).
    - ``outcome="failed"`` : l'offre reste dans la file, en échec, avec la raison ;
      son statut n'est pas modifié.

    Lève ``ValueError`` (issue ou compte rendu invalide) ou ``LookupError`` (offre hors
    de la file). Retourne le résumé à jour de l'offre.
    """
    if outcome not in OUTCOMES:
        raise ValueError(f"Issue invalide : {outcome!r} (attendu : {', '.join(OUTCOMES)}).")
    cleaned = validate_report(report)
    ensure_queue_table(db)
    job_id = str(job_id)
    with _engine(db).begin() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM apply_queue WHERE job_id = :id"), {"id": job_id}
        ).fetchone()
        if not exists:
            raise LookupError(f"Offre {job_id!r} absente de la file.")
        conn.execute(
            text("UPDATE apply_queue SET state = :state, report = :report, updated_at = :stamp WHERE job_id = :id"),
            {
                "state": STATE_APPLIED if outcome == OUTCOME_APPLIED else STATE_FAILED,
                "report": cleaned,
                "stamp": _now(),
                "id": job_id,
            },
        )
        current_status = _job_status(conn, job_id)
    if outcome == OUTCOME_APPLIED and current_status is not None and current_status not in _ALREADY_APPLIED:
        db.update_status(job_id, STATUS_APPLIED)
    with _engine(db).connect() as conn:
        row = conn.execute(text(_SELECT_ITEMS + " WHERE q.job_id = :id"), {"id": job_id}).fetchone()
    return _summary(row)
