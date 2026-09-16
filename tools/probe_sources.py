"""Sonde (lecture seule) : capacités de tri, de filtre temporel et de pagination.

Script d'exploration de la phase 0 du plan de collecte hybride. Il ne touche NI
la base SQLite NI aucun état : uniquement des requêtes ``GET`` et l'impression
des observations brutes, pour décider des paramètres réels de chaque plateforme.

Observations visées :

* **LinkedIn** (mode invité) : ``sortBy=DD`` (date) vs absence de ``sortBy``
  (pertinence par défaut) et effet du filtre temporel ``f_TPR``
  (``r604800`` = 7 jours, ``r86400`` = 24 h) sur les dates des cartes ;
* **JobTeaser** : paramètres de tri et de pagination réellement présents dans la
  page ``/fr/job-offers`` (extraits des liens HTML et des query strings), avec
  les cookies lus depuis ``.env``.

Usage:
    python tools/probe_sources.py --source linkedin --query "Stage Machine Learning"
    python tools/probe_sources.py --source jobteaser
    python tools/probe_sources.py --source all
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402

from scrapers.base import load_env_file  # noqa: E402
from scrapers.linkedin import GUEST_ENDPOINT, LinkedInGuestScraper  # noqa: E402
from scrapers.models import DEFAULT_USER_AGENT  # noqa: E402

LOCATION = "France"
# Pause entre deux requêtes : la sonde doit rester inoffensive (anti-429).
SLEEP_SECONDS = 2.5

LINKEDIN_VARIANTS: tuple[tuple[str, dict[str, str]], ...] = (
    ("date_7j (sortBy=DD + f_TPR=r604800)", {"sortBy": "DD", "f_TPR": "r604800"}),
    ("date_24h (sortBy=DD + f_TPR=r86400)", {"sortBy": "DD", "f_TPR": "r86400"}),
    ("date_sans_filtre (sortBy=DD)", {"sortBy": "DD"}),
    ("relevance (sortBy omis)", {}),
)


def probe_linkedin(query: str) -> None:
    """Compare les variantes de tri / filtre temporel sur la première page."""
    print(f"\n=== LinkedIn (invité) — query={query!r}, location={LOCATION!r} ===")
    print(
        "Les cartes invitées n'exposent pas la description : le titre et la date\n"
        "sont les seuls signaux observables ici.\n"
    )
    sequences: dict[str, list[str]] = {}
    with httpx.Client(
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9"},
        timeout=30.0,
        follow_redirects=True,
    ) as client:
        for label, extra in LINKEDIN_VARIANTS:
            params = {"keywords": query, "location": LOCATION, "f_JT": "I", "start": 0}
            params.update(extra)
            try:
                response = client.get(GUEST_ENDPOINT, params=params)
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                print(f"[{label}] HTTP {exc.response.status_code} — variante non concluante.")
                time.sleep(SLEEP_SECONDS)
                continue
            except httpx.RequestError as exc:
                print(f"[{label}] erreur réseau : {exc}")
                break

            jobs, keys = LinkedInGuestScraper._parse_cards_page(response.text)
            sequences[label] = keys
            dates = sorted(job.published_at for job in jobs if job.published_at)
            print(f"[{label}] cartes={len(keys)} datables={len(dates)} params={extra or '{}'}")
            if dates:
                print(f"    plus récente={dates[-1].date()} | plus ancienne={dates[0].date()}")
            for job in jobs[:5]:
                stamp = job.published_at.date() if job.published_at else "sans date"
                print(f"    - {stamp} | {job.title[:58]} | {job.company[:24]}")
            if extra.get("f_TPR"):
                print("    => si les dates restent anciennes, le filtre f_TPR est ignore.")
            time.sleep(SLEEP_SECONDS)

    reference = sequences.get("date_sans_filtre (sortBy=DD)")
    if reference:
        for label, keys in sequences.items():
            if label == "date_sans_filtre (sortBy=DD)":
                continue
            identical = keys[:5] == reference[:5]
            print(
                f"ordre {label!r} vs 'date_sans_filtre' : 5 premières identiques = {identical}"
                + ("  => TRI NON SERVEUR (vigilance early stopping)" if identical else "")
            )


def probe_jobteaser(query: str) -> None:
    """Repère les paramètres de tri / pagination présents dans la page HTML."""
    load_env_file()
    from scrapers.jobteaser import JobTeaserScraper  # import local : curl_cffi optionnel

    scraper = JobTeaserScraper()
    try:
        if not scraper._has_auth:  # sonde : accès à l'état interne volontaire
            print(
                "\n=== JobTeaser ===\n"
                "Cookies absents (JOBTEASER_COOKIES / JOBTEASER_SESSION dans .env) : "
                "sonde impossible, scraper inactif de toute façon."
            )
            return

        params = dict(scraper.base_params)
        params["q"] = query
        status, html = scraper._fetch_html(scraper.offers_url, params)  # sonde volontaire
        print(
            f"\n=== JobTeaser — {scraper.offers_url} q={query!r} ===\n"
            f"HTTP {status} | {len(html)} caractères | cartes 'jobad-card' = {html.count('jobad-card')}"
        )
        if status >= 400:
            print("Réponse en erreur : rafraîchir les cookies avant toute conclusion.")
            return

        hrefs = sorted(set(re.findall(r'href="([^"]*job-offers[^"]*)"', html)))
        query_strings = sorted({href.split("?", 1)[1] for href in hrefs if "?" in href})
        print(
            f"liens job-offers distincts={len(hrefs)} | query strings distinctes={len(query_strings)}"
        )
        for query_string in query_strings[:20]:
            print(f"    ?{query_string[:150]}")

        for token in ("sort", "tri", "order", "relevance", "date", "page"):
            hits = sorted(set(re.findall(rf"[?&\"]\w*{token}\w*=", html, flags=re.IGNORECASE)))
            if hits:
                print(f"paramètres évoquant {token!r} : {hits[:12]}")

        for marker in ("page=", "pagination", "Pagination", "load-more", "LoadMore"):
            if marker in html:
                print(f"marqueur de pagination détecté : {marker!r}")
    finally:
        scraper.close()


def probe_linkedin_pagination(query: str, pages: int = 2) -> None:
    """Vérifie la monotonie des dates sur plusieurs pages (validité de l'early stopping).

    L'arrêt anticipé n'est correct que si le flux est réellement antéchronologique
    **au fil des pages**. Toute inversion (offre sponsorisée mise en avant) est
    signalée explicitement.
    """
    print(f"\n--- LinkedIn : monotonie des dates sur {pages} pages (sortBy=DD + f_TPR=7j) ---")
    params = {"keywords": query, "location": LOCATION, "f_JT": "I", "sortBy": "DD", "f_TPR": "r604800"}
    start = 0
    previous: object = None
    inversions = 0
    with httpx.Client(headers={"User-Agent": DEFAULT_USER_AGENT}, timeout=30.0) as client:
        for page in range(pages):
            page_params = dict(params, start=start)
            try:
                response = client.get(GUEST_ENDPOINT, params=page_params)
                response.raise_for_status()
            except (httpx.HTTPStatusError, httpx.RequestError) as exc:
                print(f"page {page}: appel interrompu ({exc})")
                break
            jobs, keys = LinkedInGuestScraper._parse_cards_page(response.text)
            dates = [job.published_at for job in jobs if job.published_at]
            print(f"page {page} (start={start}) : cartes={len(keys)} dates={[d.date().isoformat() for d in dates]}")
            for stamp in dates:
                if previous is not None and stamp > previous:
                    inversions += 1
                previous = stamp
            start += len(keys)
            if not keys:
                break
            time.sleep(SLEEP_SECONDS)
    print(
        f"inversions de date détectées : {inversions} "
        + ("=> flux antéchronologique (early stopping fiable)" if inversions == 0 else "=> ATTENTION flux non strictement trié")
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments de la sonde."""
    parser = argparse.ArgumentParser(description="Sonde lecture seule tri/pagination des sources.")
    parser.add_argument("--source", choices=("linkedin", "jobteaser", "all"), default="all")
    parser.add_argument("--query", default="Stage Machine Learning", help="Requête cible testée.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Lance la ou les sondes demandées."""
    # La console Windows est en cp1252 : sans cela, un caractère non encodable
    # (flèche, exposant…) interrompt la sonde en plein milieu.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    args = parse_args(argv)
    if args.source in ("linkedin", "all"):
        probe_linkedin(args.query)
        probe_linkedin_pagination(args.query)
    if args.source in ("jobteaser", "all"):
        probe_jobteaser(args.query)
    print("\nSonde terminée : aucune écriture (ni base, ni fichier).")


if __name__ == "__main__":
    main()
