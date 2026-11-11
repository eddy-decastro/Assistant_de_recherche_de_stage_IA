"""Doublons jugés par des agents (Antigravity, Claude Code), entreprise par entreprise.

Le code ne sait trancher que les republications IDENTIQUES (``tools/dedupe_reposts.py``).
Pour le reste (même stage republié sous un titre un peu différent, en anglais et en
français, sur LinkedIn et sur Workday…), on prépare des groupes de candidats et des
agents décident.

    python tools/agents_doublons.py export [--max-par-lot 40] [--dir data/antigravity/doublons]
        Lecture seule sur la base. Écrit dans --dir :
        - MISSION.txt : consignes pour les agents (un agent = un lot) ;
        - in/lot_XXX.json : groupes de candidats, entreprise par entreprise.

    python tools/agents_doublons.py ingest [--dry-run] [--dir ...]
        Lit out/lot_XXX_out.json, sauvegarde la vraie base dans data/backups/, puis passe
        chaque offre « retirée » en IGNORÉ (raison « doublon ») avec une note.

Comment les groupes sont faits (pour que rien ne passe entre deux lots) :
1. les offres sont rangées par entreprise : premier mot du nom normalisé (« Airbus
   Defence and Space » va avec « Airbus »). Regrouper trop large ne coûte que des
   comparaisons en plus : les titres doivent encore se ressembler ;
2. dans une entreprise, deux offres vont dans le même groupe si leurs titres se
   ressemblent (mots communs, sans « stage », « H/F »…) ou si leur texte est identique ;
3. un groupe n'est JAMAIS coupé entre deux lots. Une grosse entreprise (Thales, plus de
   300 offres) est répartie sur plusieurs lots, groupe par groupe ; les offres sans
   aucun voisin ne sont pas envoyées (elles ne peuvent pas être un doublon).

Garde-fous à l'ingestion : une décision d'Eddy (Postulé, Entretien, Refus, Ignoré à la
main) n'est jamais modifiée ; elle peut seulement servir d'offre « gardée ». Une offre
n'est retirée que si l'offre gardée est dans le même groupe du lot d'origine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.constants import (  # noqa: E402
    AUTO_IGNORE_REASONS,
    DUPLICATE_REASON,
    STATUS_EXCLUDED,
    STATUS_IGNORED,
    STATUS_NEW,
)
from src.storage.cleanup import normalize_company  # noqa: E402
from src.storage.database import Database  # noqa: E402

REAL_DB = PROJECT_ROOT / "data" / "stage_copilot.db"
WORK_DIR = PROJECT_ROOT / "data" / "antigravity" / "doublons"
TITLE_SIMILARITY = 0.5
# Un groupe plus gros est re-découpé avec un seuil plus strict (évite l'effet chaîne :
# « Data Scientist » ~ « Ingénieur Data Scientist » ~ « Ingénieur Data » ~ …).
MAX_GROUP = 20
INGESTED_MARKER = ".ingere"

# Mots qui ne disent rien du poste : ignorés pour comparer les titres.
_TITLE_STOPWORDS = frozenset(
    """stage stagiaire stages internship intern interns trainee h f m w x hf fh mf
    de du des la le les l d en et pour a au aux un une sur avec dans of the and for in on to
    with pfe fin etude etudes end study studies mois month months 6 5 4 2026 2027 cdd
    alternance apprentissage""".split()
)


# --------------------------------------------------------------------------- #
# Règles partagées
# --------------------------------------------------------------------------- #
def is_eddy_decision(job: dict[str, Any]) -> bool:
    """Postulé, Entretien, Refus ou Ignoré à la main : jamais modifiée."""
    status = job.get("status")
    if status in (STATUS_NEW, STATUS_EXCLUDED):
        return False
    if status == STATUS_IGNORED:
        return str(job.get("rejection_reason") or "") not in AUTO_IGNORE_REASONS
    return True


def is_candidate(job: dict[str, Any]) -> bool:
    """Offres utiles à comparer : vivantes (à trier, exclues) ou décidées par Eddy.

    Les offres déjà fermées, archivées ou marquées doublon sont hors jeu.
    """
    return job.get("status") in (STATUS_NEW, STATUS_EXCLUDED) or is_eddy_decision(job)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def title_tokens(title: str | None) -> frozenset[str]:
    words = re.split(r"[^a-z0-9]+", _strip_accents((title or "").casefold()))
    return frozenset(w for w in words if w and w not in _TITLE_STOPWORDS and len(w) > 1)


def titles_close(a: frozenset[str], b: frozenset[str], threshold: float = TITLE_SIMILARITY) -> bool:
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


def _text_key(description: str | None) -> str:
    norm = re.sub(r"\W+", " ", (description or "").casefold()).strip()[:800]
    return hashlib.sha1(norm.encode("utf-8")).hexdigest() if len(norm) >= 200 else ""


def company_key(company: str | None) -> str:
    """Clé de regroupement : premier mot du nom normalisé (« airbus », « thales »)."""
    norm = normalize_company(str(company or ""))
    return norm.split()[0] if norm else ""


def _cluster(members: list[dict[str, Any]], threshold: float) -> list[list[dict[str, Any]]]:
    """Groupes (2 offres ou plus) de titres proches ou de textes identiques.

    Un groupe de plus de ``MAX_GROUP`` offres est re-découpé avec un seuil plus strict.
    """
    parent = list(range(len(members)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    tokens = [title_tokens(j.get("title")) for j in members]
    texts = [_text_key(j.get("description")) for j in members]
    for i in range(len(members)):
        for k in range(i + 1, len(members)):
            same_text = bool(texts[i]) and texts[i] == texts[k]
            if same_text or titles_close(tokens[i], tokens[k], threshold):
                parent[find(i)] = find(k)
    clusters: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for i, job in enumerate(members):
        clusters[find(i)].append(job)
    groups: list[list[dict[str, Any]]] = []
    for cluster in clusters.values():
        if len(cluster) < 2:
            continue
        if len(cluster) > MAX_GROUP and threshold < 0.95:
            groups.extend(_cluster(cluster, round(threshold + 0.1, 2)))
        else:
            groups.append(cluster)
    return groups


def build_groups(jobs: list[dict[str, Any]]) -> dict[str, list[list[dict[str, Any]]]]:
    """``{entreprise normalisée: [groupe, ...]}`` ; seuls les groupes de 2 offres ou plus."""
    by_company: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        if not is_candidate(job):
            continue
        key = company_key(job.get("company"))
        if key:
            by_company[key].append(job)

    result: dict[str, list[list[dict[str, Any]]]] = {}
    for company, members in by_company.items():
        if len(members) < 2:
            continue
        groups = _cluster(members, TITLE_SIMILARITY)
        # Un groupe fait uniquement de décisions d'Eddy n'a rien à retirer.
        groups = [g for g in groups if any(not is_eddy_decision(j) for j in g)]
        if groups:
            result[company] = sorted(groups, key=lambda g: -len(g))
    return result


def _record(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "url": job.get("url") or "",
        "source": job.get("source") or "",
        "published_at": str(job.get("published_at") or job.get("created_at") or ""),
        "status": job.get("status"),
        "decision_eddy": is_eddy_decision(job),
        "note_v4": job.get("v4_display_score"),
        "description": (job.get("description") or "").strip()[:2500],
    }


def make_lots(groups: dict[str, list[list[dict[str, Any]]]], max_per_lot: int) -> list[dict[str, Any]]:
    """Range les groupes dans des lots d'au plus ``max_per_lot`` offres, sans couper un groupe.

    Les entreprises sont prises de la plus grosse à la plus petite ; un groupe plus gros
    que ``max_per_lot`` occupe un lot à lui seul.
    """
    lots: list[dict[str, Any]] = []
    current: dict[str, Any] = {"groupes": []}
    size = 0
    counter = 0
    ordered = sorted(groups.items(), key=lambda kv: -sum(len(g) for g in kv[1]))
    for company, company_groups in ordered:
        for group in company_groups:
            if size and size + len(group) > max_per_lot:
                lots.append(current)
                current, size = {"groupes": []}, 0
            counter += 1
            current["groupes"].append({
                "groupe": f"g{counter:04d}",
                "entreprise": group[0].get("company") or company,
                "offres": [_record(j) for j in sorted(group, key=lambda j: str(j.get("published_at") or ""))],
            })
            size += len(group)
    if current["groupes"]:
        lots.append(current)
    return lots


MISSION = """MISSION : trouver les DOUBLONS d'offres de stage, groupe par groupe.

Tu reçois un lot (in/lot_XXX.json). Il contient des « groupes » : des offres d'une même
entreprise dont les titres se ressemblent. Ton travail : dire, DANS CHAQUE GROUPE, quelles
offres sont le MÊME stage publié plusieurs fois.

C'est un doublon si c'est le même poste : même sujet, même équipe ou même lieu, même
mission décrite (republication, version anglaise et française, LinkedIn + site carrière,
titre légèrement reformulé, même référence d'offre).
Ce n'est PAS un doublon : sujets différents (ex. « NLP » et « vision »), villes différentes
avec des textes différents, deux stages distincts de la même équipe. Dans le doute : PAS doublon.

Pour chaque ensemble de doublons, choisis l'offre à GARDER :
1. une offre où "decision_eddy" vaut true (il a déjà postulé ou décidé) ;
2. sinon celle qui a une note ("note_v4") ;
3. sinon la description la plus complète ; à égalité, la plus ancienne.
Ne mets JAMAIS dans "retirer" une offre où "decision_eddy" vaut true.
Tu peux ouvrir les URL si le texte ne suffit pas, mais n'ouvre pas des dizaines de pages
LinkedIn d'affilée (blocage) : le texte fourni suffit presque toujours.

Écris out/lot_XXX_out.json (même numéro que le lot), au format exact :
{
  "groupes": [
    {"groupe": "g0001",
     "doublons": [
        {"garder": "<id>", "retirer": ["<id>", "<id>"], "raison": "même offre republiée, texte identique"}
     ]}
  ]
}
Un groupe sans doublon : "doublons": []. Tous les groupes du lot doivent apparaître.
Utilise uniquement les "id" présents dans le groupe. Ne modifie pas les fichiers in/.
"""


# --------------------------------------------------------------------------- #
# Export / ingestion
# --------------------------------------------------------------------------- #
def _copy(db_path: Path, target: Path) -> Path:
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    return target


def export(max_per_lot: int = 40, db_path: Path = REAL_DB, out_dir: Path = WORK_DIR) -> dict[str, int]:
    copy = _copy(db_path, Path(tempfile.mkdtemp(prefix="doublons_")) / "copie.db")
    jobs = Database(str(copy)).get_jobs(limit=100000)
    groups = build_groups(jobs)
    lots = make_lots(groups, max_per_lot)

    in_dir = out_dir / "in"
    in_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "out").mkdir(parents=True, exist_ok=True)
    for old in in_dir.glob("lot_*.json"):
        old.unlink()
    (out_dir / "MISSION.txt").write_text(MISSION, encoding="utf-8")
    for n, lot in enumerate(lots, start=1):
        (in_dir / f"lot_{n:03d}.json").write_text(json.dumps(lot, ensure_ascii=False, indent=2), encoding="utf-8")

    stats = {
        "entreprises": len(groups),
        "groupes": sum(len(g) for g in groups.values()),
        "offres": sum(len(x) for g in groups.values() for x in g),
        "lots": len(lots),
    }
    print(f"Export doublons : {json.dumps(stats, ensure_ascii=False)} -> {in_dir}")
    biggest = sorted(groups.items(), key=lambda kv: -sum(len(g) for g in kv[1]))[:5]
    for company, gs in biggest:
        print(f"  {company:30} {sum(len(g) for g in gs):4} offres en {len(gs)} groupes")
    return stats


def _load_groups(in_dir: Path) -> dict[str, dict[str, set[str]]]:
    """``{lot: {groupe: ids}}`` : la vérité sur ce qui a été envoyé aux agents."""
    sent: dict[str, dict[str, set[str]]] = {}
    for path in sorted(in_dir.glob("lot_*.json")):
        lot = json.loads(path.read_text(encoding="utf-8"))
        sent[path.stem] = {g["groupe"]: {o["id"] for o in g["offres"]} for g in lot.get("groupes", [])}
    return sent


def ingest(dry_run: bool, db_path: Path = REAL_DB, work_dir: Path = WORK_DIR) -> dict[str, int]:
    sent = _load_groups(work_dir / "in")
    if dry_run:
        target = _copy(db_path, Path(tempfile.mkdtemp(prefix="doublons_")) / "copie.db")
        print(f"Essai à blanc sur une copie : {target}")
    else:
        backup_dir = db_path.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = _copy(db_path, backup_dir / f"stage_copilot_avant_doublons_agents_{datetime.now():%Y%m%d_%H%M%S}.db")
        print(f"Sauvegarde : {backup}")
        target = db_path

    db = Database(str(target))
    jobs = {j["id"]: j for j in db.get_jobs(limit=100000)}
    stats = {"retirees": 0, "refusees_decision_eddy": 0, "refusees_hors_groupe": 0,
             "deja_hors_jeu": 0, "lots_lus": 0}
    for path in sorted((work_dir / "out").glob("lot_*_out.json")):
        lot_name = path.stem.removesuffix("_out")
        groups_sent = sent.get(lot_name, {})
        stats["lots_lus"] += 1
        payload = json.loads(path.read_text(encoding="utf-8"))
        for entry in payload.get("groupes", []):
            members = groups_sent.get(str(entry.get("groupe")), set())
            for dup in entry.get("doublons") or []:
                keep = str(dup.get("garder") or "")
                if keep not in members or keep not in jobs:
                    stats["refusees_hors_groupe"] += len(dup.get("retirer") or [])
                    continue
                reason = str(dup.get("raison") or "même offre")[:160]
                for rid in dup.get("retirer") or []:
                    rid = str(rid)
                    job = jobs.get(rid)
                    if rid == keep or rid not in members or job is None:
                        stats["refusees_hors_groupe"] += 1
                        continue
                    if is_eddy_decision(job):
                        stats["refusees_decision_eddy"] += 1
                        continue
                    if job.get("status") not in (STATUS_NEW, STATUS_EXCLUDED):
                        stats["deja_hors_jeu"] += 1
                        continue
                    db.update_status(rid, STATUS_IGNORED, rejection_reason=DUPLICATE_REASON)
                    note = f"[Agents] doublon de {keep[:10]} : {reason}"
                    current = (job.get("user_notes") or "").strip()
                    db.update_user_notes(rid, f"{current}\n{note}".strip() if current else note)
                    job["status"] = STATUS_IGNORED
                    stats["retirees"] += 1
    if not dry_run:
        (work_dir / "out" / INGESTED_MARKER).write_text(datetime.now().isoformat(), encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--max-par-lot", type=int, default=40, dest="max_per_lot",
                       help="Offres au plus par lot (un groupe n'est jamais coupé).")
    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("--dry-run", action="store_true")
    for p in (p_exp, p_ing):
        p.add_argument("--dir", type=Path, default=WORK_DIR)
    args = parser.parse_args(argv)
    if args.cmd == "export":
        export(args.max_per_lot, out_dir=args.dir)
    else:
        ingest(args.dry_run, work_dir=args.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
