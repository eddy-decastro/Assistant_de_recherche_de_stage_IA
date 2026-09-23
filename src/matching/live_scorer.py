"""Worker de notation et d'évaluation LLM en temps réel (au fil de l'eau).

Ce module permet d'évaluer les offres au fur et à mesure de leur collecte
par les scrapers, sans attendre la fin du scraping complet.
"""
from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrapers.models import RawJob
from src.ingestion.bridge import ingest_raw_jobs, raw_job_to_dict
from src.matching.llm_judge import LLMJudge
from src.storage.database import Database, make_job_id

logger = logging.getLogger("src.matching.live_scorer")


class LiveRerankWorker:
    """Consomme les offres collectées au fur et à mesure et les évalue avec le juge LLM."""

    def __init__(
        self,
        db: Database,
        config: dict[str, Any],
        *,
        max_jobs: int = 30,
        concurrency: int = 1,
        backfill_missing: bool = False,
    ) -> None:
        self.db = db
        self.config = config
        self.max_jobs = max_jobs
        self.concurrency = max(1, concurrency)
        self.backfill_missing = backfill_missing

        self.judge = LLMJudge(config)
        cv_path = Path(config.get("scoring", {}).get("cv_path", "data/cv_eddy.txt"))
        self.cv_text = cv_path.read_text(encoding="utf-8") if cv_path.exists() else ""

        self.queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self._enqueued_keys: set[str] = set()
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._evaluated_count = 0
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        """Indique si le juge LLM est opérationnel (clé API configurée)."""
        return bool(self.judge.available)

    @property
    def evaluated_count(self) -> int:
        """Nombre d'offres notées avec succès."""
        with self._lock:
            return self._evaluated_count

    def start(self) -> None:
        """Démarre le ou les threads d'évaluation en arrière-plan."""
        if not self.available:
            logger.warning("Notation live ignorée : GEMINI_API_KEY absente (.env).")
            return
        self._stop_event.clear()
        for i in range(self.concurrency):
            t = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"LiveRerankWorker-{i+1}",
            )
            t.start()
            self._threads.append(t)
        logger.info(
            "🚀 Notation en direct activée : évaluation au fil de l'eau par Gemini (Top-%d max)",
            self.max_jobs,
        )

    def enqueue(self, jobs: list[dict[str, Any]]) -> int:
        """Ajoute des offres à la file d'évaluation."""
        if not self.available or self._stop_event.is_set():
            return 0
        added = 0
        for job in jobs:
            job_id = job.get("id") or make_job_id(
                job.get("title", ""), job.get("company", ""), job.get("url", "")
            )
            with self._lock:
                if (
                    not job_id
                    or job_id in self._enqueued_keys
                    or len(self._enqueued_keys) >= self.max_jobs
                ):
                    continue
                # Vérifier si l'offre a déjà été notée
                if job.get("rerank_score") is not None:
                    continue
                self._enqueued_keys.add(job_id)

            job_copy = dict(job)
            job_copy["id"] = job_id
            self.queue.put(job_copy)
            added += 1
        return added

    def _worker_loop(self) -> None:
        """Boucle d'exécution d'un thread consommateur."""
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                job = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                job_id = job["id"]
                # Vérifier si l'offre a déjà une note en base (ex: évaluée par un autre thread)
                existing = self.db.get_job_by_id(job_id)
                if existing and existing.get("rerank_score") is not None:
                    continue

                # Évaluation avec Gemini LLM
                result = self.judge.judge(job, cv_text=self.cv_text)
                if result.get("api_error") or result.get("rerank_score") is None:
                    err_msg = (result.get("red_flags") or ["Erreur API Gemini"])[0]
                    logger.warning(
                        "[Live LLM] Échec pour %s : %s",
                        (job.get("title") or "")[:40],
                        err_msg,
                    )
                    consecutive_errors += 1
                    if consecutive_errors >= 3 and ("quota" in err_msg.lower() or "429" in err_msg):
                        logger.error(
                            "🛑 [Live LLM] Quota Gemini atteint (3 échecs consécutifs). Suspension de la notation live."
                        )
                        break
                    continue

                consecutive_errors = 0

                # Sauvegarde en base SQLite
                self.db.update_rerank(
                    job_id,
                    result["rerank_score"],
                    verdict=result.get("verdict"),
                    match_reasons=result.get("match_reasons"),
                    red_flags=result.get("red_flags"),
                    tech_stack=result.get("tech_stack"),
                    sub_scores=result.get("sub_scores"),
                    hard_cap_triggered=result.get("hard_cap_triggered"),
                    reasoning=result.get("reasoning", ""),
                )

                with self._lock:
                    self._evaluated_count += 1
                    count = self._evaluated_count
                    total_enqueued = len(self._enqueued_keys)

                logger.info(
                    "  [Live LLM %d/%d] [%3d] %-10s %s (%s)",
                    count,
                    total_enqueued,
                    int(result["rerank_score"]),
                    result.get("verdict", "N/A"),
                    (job.get("title") or "")[:45],
                    (job.get("company") or "")[:20],
                )
            except Exception as exc:
                logger.debug("[Live LLM] Exception: %s", exc)
            finally:
                self.queue.task_done()

    def wait_completion(self, timeout: float = 30.0) -> int:
        """Attend que les offres en cours et en file soient traitées, puis arrête les workers."""
        try:
            self.queue.join()
        except Exception:
            pass
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=timeout)
        return self.evaluated_count


def create_batch_callback(
    db: Database,
    worker: LiveRerankWorker | None = None,
) -> Callable[[list[RawJob]], None]:
    """Fabrique un callback d'ingestion et de notation au fil de l'eau.

    À chaque lot d'offres valides renvoyé par un scraper :
    1. Ingestion immédiate dans SQLite (mode WAL, sans blocage) ;
    2. Envoi des nouvelles offres au worker LLM si actif.
    """
    def _on_batch(raw_jobs: list[RawJob]) -> None:
        if not raw_jobs:
            return
        # 1. Ingestion immédiate
        stats = ingest_raw_jobs(raw_jobs, db)
        # 2. Envoi au worker de notation live
        if worker is not None and worker.available:
            job_dicts = [raw_job_to_dict(rj) for rj in raw_jobs]
            worker.enqueue(job_dicts)

    return _on_batch
