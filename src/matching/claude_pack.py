"""Générateur du « Pack Claude » (Claude in Chrome + Projet Claude).

Prépare pour une ou plusieurs offres sélectionnées :
1. Les lettres de motivation sur mesure (texte + PDF dans data/ready_to_apply/)
2. Les réponses types aux questions de formulaires (Workday, Lever, Greenhouse, WTTJ...)
3. La file d'attente ``/file`` (``src.matching.apply_queue``) et un prompt court et
   stable pour « Claude in Chrome », plus un export JSON pour « Projet Claude ».
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.candidate import get_candidate_info, get_cv_path, get_cv_text
from src.config import PROJECT_ROOT, load_config
from src.matching.apply_queue import QUEUE_URL, detect_apply_method, enqueue_bundles
from src.matching.cover_letter import CoverLetterGenerator
from src.matching.pdf_exporter import generate_cover_letter_pdf

READY_DIR = PROJECT_ROOT / "data" / "ready_to_apply"


def _safe_slug(text: str, max_len: int = 32) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(text or "").strip()).strip("_")
    return cleaned[:max_len] or "Entreprise"


def _split_name(full_name: str) -> tuple[str, str]:
    parts = (full_name or "Eddy DE CASTRO").strip().split()
    if not parts:
        return "Eddy", "DE CASTRO"
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def get_cv_pdf_path(config: dict[str, Any] | None = None) -> str:
    """Chemin absolu du CV PDF à joindre à la main, ou "" s'il est introuvable.

    Cherche ``candidate.cv_pdf_path`` puis ``scoring.cv_pdf_path`` dans la config,
    sinon le premier ``data/*.pdf`` dont le nom contient « cv ».
    """
    cfg = config or load_config()
    configured = (cfg.get("candidate") or {}).get("cv_pdf_path") or (cfg.get("scoring") or {}).get("cv_pdf_path")
    if configured:
        path = Path(str(configured))
        path = path if path.is_absolute() else PROJECT_ROOT / path
        return str(path.resolve()) if path.exists() else ""
    data_dir = PROJECT_ROOT / "data"
    if data_dir.is_dir():
        for pdf in sorted(data_dir.glob("*.pdf")):
            if "cv" in pdf.name.lower():
                return str(pdf.resolve())
    return ""


def build_candidate_form_data(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Infos candidat prêtes à saisir dans les formulaires (aucun appel LLM)."""
    cfg = config or load_config()
    candidate = get_candidate_info(cfg)
    cv_path = get_cv_path(cfg)
    first_name, last_name = _split_name(candidate.get("name", "Eddy DE CASTRO"))
    return {
        "first_name": first_name,
        "last_name": last_name,
        "full_name": candidate.get("name") or "Eddy DE CASTRO",
        "email": candidate.get("email") or "[À compléter dans Paramètres]",
        "phone": candidate.get("phone") or "[À compléter dans Paramètres]",
        "city": candidate.get("location") or "Paris, France",
        "linkedin": candidate.get("linkedin") or "",
        "github": candidate.get("github") or "",
        "school": "IMT Mines Alès & Mines Saint-Étienne (Double diplôme M2 Mathématiques en Action)",
        "degree_level": "Bac+5 / Master 2 / Diplôme d'ingénieur (PFE)",
        "internship_duration": "6 mois",
        "start_date": "Avril 2027 (flexible février-mai 2027)",
        "contract_type": "Stage de fin d'études (PFE) conventionné — Pas d'alternance",
        "work_authorization": "Oui (citoyen UE / autorisé à travailler en France sans sponsorship)",
        "cv_path": str(cv_path.resolve()) if cv_path.exists() else "data/cv_eddy.txt",
        "cv_pdf_path": get_cv_pdf_path(cfg),
    }


def build_short_pitch(job: dict[str, Any], candidate: dict[str, str]) -> str:
    """Construit un message d'accroche court (< 300 caractères) pour le champ 'Message au recruteur'."""
    company = (job.get("company") or "votre équipe").strip()
    title = (job.get("title") or "ce stage PFE").strip()
    techs = [str(t) for t in (job.get("tech_stack") or []) if str(t).strip()][:3]
    tech_part = f" ({', '.join(techs)})" if techs else ""
    pitch = (
        f"Élève-ingénieur IMT Mines Alès en double diplôme M2 Maths Appliquées (Mines Saint-Étienne), "
        f"je candidate avec enthousiasme au stage « {title} » chez {company}{tech_part}, "
        f"disponible dès avril 2027 pour 6 mois."
    )
    return pitch[:295] + ("…" if len(pitch) > 295 else "")


def prepare_job_application_bundle(
    job: dict[str, Any],
    *,
    db: Any = None,
    config: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    generate_missing_letter: bool = True,
    export_pdf: bool = True,
) -> dict[str, Any]:
    """Prépare le dossier complet d'une offre (lettre.txt, motivation_courte.txt, PDF local)."""
    cfg = config or load_config()
    candidate = get_candidate_info(cfg)
    cv_path = get_cv_path(cfg)
    target_dir = Path(output_dir) if output_dir is not None else READY_DIR
    generator = CoverLetterGenerator(config=cfg)

    letter_text = str(job.get("cover_letter") or "").strip()
    if (not letter_text or letter_text.startswith("⚠️")) and generate_missing_letter:
        letter_text = generator.generate(job, allow_fallback=False)
        if not letter_text or letter_text.startswith("⚠️"):
            raise RuntimeError(
                letter_text
                or generator.last_error
                or "Échec de génération de la lettre : DeepSeek et Gemini ont échoué."
            )
        if db is not None and hasattr(db, "update_cover_letter"):
            try:
                db.update_cover_letter(str(job.get("id") or ""), letter_text)
            except Exception:
                pass

    short_motivation = generator.generate_short_motivation(job, letter_text or None)

    company_raw = (job.get("company") or "Entreprise").strip()
    company_slug = _safe_slug(company_raw)
    clean_company = "".join(c for c in company_raw if c.isalnum() or c in ("-", "_", " ")).strip() or "Entreprise"
    job_id_short = str(job.get("id") or "job")[:8]
    offer_dir = target_dir / f"{company_slug}_{job_id_short}"

    pdf_path_str = ""
    letter_txt_path_str = ""
    short_txt_path_str = ""
    if letter_text:
        offer_dir.mkdir(parents=True, exist_ok=True)
        letter_file = offer_dir / "lettre.txt"
        letter_file.write_text(letter_text, encoding="utf-8")
        letter_txt_path_str = str(letter_file.resolve())

        short_file = offer_dir / "motivation_courte.txt"
        short_file.write_text(short_motivation, encoding="utf-8")
        short_txt_path_str = str(short_file.resolve())

        if export_pdf:
            pdf_file = offer_dir / f"Lettre de motivation Eddy De Castro - {clean_company}.pdf"
            try:
                pdf_bytes = generate_cover_letter_pdf(letter_text, candidate_info=candidate, job=job)
                pdf_file.write_bytes(pdf_bytes)
                pdf_path_str = str(pdf_file.resolve())
            except Exception:
                pdf_path_str = ""

    v4_score = job.get("v4_display_score")
    v3_score = job.get("rerank_score") if job.get("rerank_score") is not None else job.get("final_score")

    return {
        "id": str(job.get("id") or ""),
        "title": str(job.get("title") or ""),
        "company": str(job.get("company") or ""),
        "location": str(job.get("location") or ""),
        "url": str(job.get("url") or ""),
        "source": str(job.get("source") or ""),
        "v4_class": job.get("v4_class"),
        "v4_display_score": round(float(v4_score), 1) if v4_score is not None else None,
        "v3_score": round(float(v3_score), 1) if v3_score is not None else None,
        "short_pitch": short_motivation,
        "short_motivation": short_motivation,
        "match_reasons": list(job.get("match_reasons") or []),
        "tech_stack": list(job.get("tech_stack") or []),
        "cover_letter": letter_text,
        "offer_dir": str(offer_dir.resolve()) if letter_text else "",
        "letter_txt_path": letter_txt_path_str,
        "short_motivation_path": short_txt_path_str,
        "cover_letter_pdf_path": pdf_path_str,
        "pdf_path": pdf_path_str,
        "cv_file_path": str(cv_path.resolve()) if cv_path.exists() else "",
        # C2 : PDF à joindre à la main par Eddy (Claude in Chrome ne téléverse rien).
        "cv_pdf_path": get_cv_pdf_path(cfg),
        "pitch_300": build_short_pitch(job, candidate),
        # C3 : méthode de candidature détectée depuis l'URL et la source.
        "apply_method": detect_apply_method(job),
    }


def build_queue_prompt(*, queue_url: str = QUEUE_URL, auto_submit: bool = False) -> str:
    """Prompt court et stable pour Claude in Chrome : il dépile la file ``/file``."""
    submit_rule = (
        "Si rien d'obligatoire ne manque, vérifie chaque champ puis clique Envoyer ; sinon ne soumets pas."
        if auto_submit
        else "Ne clique jamais sur le bouton final Envoyer / Submit : laisse l'onglet prêt, je validerai moi-même."
    )
    lines = [
        "Tu postules à mes stages pour moi, une offre à la fois, depuis ma file d'attente.",
        "",
        f"1. Ouvre {queue_url} et prends l'offre courante (la première de la file).",
        "2. La page donne tout : URL, méthode de candidature, mes infos, accroche courte, "
        "lettre en texte, réponses types et consignes de l'offre. N'invente rien d'autre.",
        "3. Ouvre l'URL de l'offre dans un nouvel onglet et remplis le formulaire.",
        "4. Règles strictes :",
        "   - Ne crée jamais de compte et ne saisis jamais de mot de passe. Si un compte est requis, "
        "reviens sur la file, écris « Échec : compte requis. » et clique « Échec ».",
        "   - Tu ne peux pas joindre de PDF : colle la lettre en texte et remplis tout le reste. "
        "Si un CV est obligatoire et non joint, ne soumets pas, laisse l'onglet ouvert et "
        "écris « CV à joindre » dans le compte rendu.",
        f"   - {submit_rule}",
        "5. Reviens sur la file et écris le compte rendu en mode caveman : 2 phrases maximum, "
        "300 caractères maximum. Exemple : « Moi remplir formulaire Thales. CV pas mis, Eddy "
        "mettre CV puis cliquer Envoyer. »",
        "6. Clique « Marquer postulé » si le formulaire est rempli, sinon « Échec » avec la raison.",
        "7. Recommence à l'étape 1 jusqu'à ce que la file soit vide. Ne ferme pas les onglets de candidature.",
    ]
    return "\n".join(lines)


def build_claude_pack(
    jobs: list[dict[str, Any]],
    *,
    db: Any = None,
    config: dict[str, Any] | None = None,
    output_dir: Path | None = None,
    generate_missing_letters: bool = True,
    export_pdfs: bool = True,
    save_pdfs: bool | None = None,
    auto_submit: bool = False,
    enqueue: bool = True,
    queue_url: str = QUEUE_URL,
) -> dict[str, Any]:
    """Génère le Pack Claude : dossiers des offres, file ``/file`` et prompt court.

    Avec une vraie ``Database`` et ``enqueue=True``, les offres sont ajoutées à la file
    d'attente (``apply_queue``) ; ``chrome_prompt`` dit alors à Claude in Chrome de la
    dépiler. Les clés historiques (``bundles``, ``json_export``...) sont conservées.
    """
    cfg = config or load_config()
    cv_text = get_cv_text(cfg)
    target_dir = Path(output_dir) if output_dir is not None else READY_DIR
    do_export_pdf = export_pdfs if save_pdfs is None else bool(save_pdfs)

    bundles = [
        prepare_job_application_bundle(
            job,
            db=db,
            config=cfg,
            output_dir=target_dir,
            generate_missing_letter=generate_missing_letters,
            export_pdf=do_export_pdf,
        )
        for job in jobs
    ]
    candidate_form_data = build_candidate_form_data(cfg)

    queue_result: dict[str, Any] = {"added": [], "requeued": [], "skipped": [], "enabled": False}
    if enqueue and db is not None and getattr(db, "engine", None) is not None:
        queue_result = {**enqueue_bundles(db, bundles, auto_submit=auto_submit), "enabled": True}

    queue_prompt = build_queue_prompt(queue_url=queue_url, auto_submit=auto_submit)
    recap = ["", "---", f"Pour info, {len(bundles)} offre(s) dans ce pack :"]
    for b in bundles:
        method = b.get("apply_method") or {}
        recap.append(f"- {b['company']} : {b['title']} ({method.get('label', 'Méthode inconnue')})")
    recap.append(
        f"Si la page {queue_url} ne s'ouvre pas, les dossiers (lettres, PDF) sont dans {target_dir.resolve()}."
    )
    chrome_prompt = queue_prompt + "\n" + "\n".join(recap)

    json_str = json.dumps(
        {"candidate": candidate_form_data, "jobs": bundles},
        ensure_ascii=False,
        indent=2,
    )

    return {
        "candidate": candidate_form_data,
        "cv_excerpt": (cv_text or "")[:3000],
        "count": len(bundles),
        "output_dir": str(target_dir.resolve()),
        "jobs": bundles,
        "bundles": bundles,
        "queue_prompt": queue_prompt,
        "queue_url": queue_url,
        "queue": queue_result,
        "chrome_prompt": chrome_prompt,
        "json_export": json_str,
        "claude_project_json": json_str,
    }


