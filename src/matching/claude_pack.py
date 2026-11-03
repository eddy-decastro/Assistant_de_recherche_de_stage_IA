"""Générateur du « Pack Claude » (Claude in Chrome + Projet Claude).

Prépare pour une ou plusieurs offres sélectionnées :
1. Les lettres de motivation sur mesure (texte + PDF dans data/ready_to_apply/)
2. Les réponses pré-formatées aux questions de formulaires ATS (Workday, Lever, Greenhouse, WTTJ...)
3. Un prompt clé-en-main pour « Claude in Chrome » et un dossier structuré pour « Projet Claude ».
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from src.candidate import get_candidate_info, get_cv_path, get_cv_text
from src.config import PROJECT_ROOT, load_config
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
    }


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
) -> dict[str, Any]:
    """Génère le Pack Claude complet (JSON + Prompt Claude in Chrome + Markdown Projet Claude)."""
    cfg = config or load_config()
    candidate = get_candidate_info(cfg)
    cv_text = get_cv_text(cfg)
    cv_path = get_cv_path(cfg)
    first_name, last_name = _split_name(candidate.get("name", "Eddy DE CASTRO"))
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

    candidate_form_data = {
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
    }

    submit_instruction = (
        "Soumets automatiquement le formulaire une fois tous les champs vérifiés."
        if auto_submit
        else "NE CLIQUE PAS sur le bouton final 'Envoyer / Submit' : laisse l'onglet prêt à valider pour que je vérifie en 5 secondes."
    )

    chrome_lines = [
        "# 🤖 Instructions pour Claude in Chrome — Session de Candidature Automatisée",
        "",
        f"Tu vas m'aider à postuler à **{len(bundles)} offre(s) de stage PFE**.",
        "",
        "## 1. Informations standards pour remplir les formulaires (ATS / WTTJ / LinkedIn / Lever / Greenhouse)",
        f"- **Prénom** : `{candidate_form_data['first_name']}`",
        f"- **Nom** : `{candidate_form_data['last_name']}`",
        f"- **Email** : `{candidate_form_data['email']}`",
        f"- **Téléphone** : `{candidate_form_data['phone']}`",
        f"- **Ville / Localisation** : `{candidate_form_data['city']}`",
        f"- **LinkedIn** : `{candidate_form_data['linkedin']}`",
        f"- **GitHub** : `{candidate_form_data['github']}`",
        f"- **École / Formation** : `{candidate_form_data['school']}`",
        f"- **Niveau d'études** : `{candidate_form_data['degree_level']}`",
        f"- **Disponibilité & Durée** : `{candidate_form_data['start_date']} ({candidate_form_data['internship_duration']})`",
        f"- **Fichier CV local** : `{candidate_form_data['cv_path']}`",
        "",
        "## 2. Consignes d'exécution dans le navigateur",
        "1. Pour chaque offre ci-dessous, ouvre l'URL de candidature.",
        "2. Si la page est un agrégateur (LinkedIn, WTTJ) avec un bouton « Postuler sur le site de l'entreprise », clique dessus pour atteindre le formulaire final.",
        "3. Remplis les champs d'identité, d'école, de dates et les liens LinkedIn/GitHub.",
        "4. Pour le champ « Message au recruteur » court (< 300 car.), utilise l'**Accroche courte** fournie.",
        "5. Pour la lettre de motivation : colle le texte de la lettre dans le champ dédié OU téléverse le PDF généré à l'emplacement indiqué.",
        "6. Si le formulaire pose une question ouverte spécifique (ex: *Why this company?*, *Key project?*), utilise les points forts listés et mon profil pour rédiger une réponse factuelle de 3-4 phrases sans crochets.",
        f"7. **Validation finale** : {submit_instruction}",
        "",
        "---",
        "## 3. File d'attente des offres à traiter",
        "",
    ]

    for idx, b in enumerate(bundles, start=1):
        score_badge = (
            f"v4: {b['v4_class']} · {b['v4_display_score']:.0f}/100"
            if b.get("v4_display_score") is not None
            else f"v3: {b['v3_score'] or 0:.0f}/100"
        )
        chrome_lines.extend(
            [
                f"### Offre #{idx} — {b['company']} : {b['title']} ({score_badge})",
                f"- **URL** : {b['url']}",
                f"- **Lieu** : {b['location'] or 'Non précisé'}",
                f"- **PDF Lettre prêt** : `{b['cover_letter_pdf_path'] or 'Non généré'}`",
                "- **Accroche courte (< 300 car.)** :",
                f"  > {b['short_pitch']}",
                "- **Technologies & Arguments clés** : "
                + (", ".join(b["tech_stack"][:6]) if b["tech_stack"] else "Deep Learning, ML, Python")
                + (" — " + " ; ".join(b["match_reasons"][:2]) if b["match_reasons"] else ""),
                "",
                "<details><summary>Lettre de motivation complète (texte à copier/coller)</summary>",
                "",
                "```text",
                b["cover_letter"] or "(Lettre non générée)",
                "```",
                "</details>",
                "",
                "---",
            ]
        )

    chrome_prompt = "\n".join(chrome_lines)
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
        "chrome_prompt": chrome_prompt,
        "json_export": json_str,
        "claude_project_json": json_str,
    }
