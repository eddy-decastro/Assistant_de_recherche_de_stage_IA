"""Index des offres déjà connues : référence de l'arrêt anticipé et déduplication.

Ce module est **volontairement sans dépendance** à SQLite ou SQLAlchemy : les
scrapers reçoivent un ``KnownIndex`` (protocole) et le pipeline leur injecte
l'implémentation persistante (``src.ingestion.known_index.DatabaseKnownIndex``).
Les tests, eux, utilisent l'index mémoire ci-dessous — aucune base n'est requise
pour vérifier la logique d'arrêt anticipé.

L'index répond selon deux identités complémentaires :

* ``(source, external_key)`` — identifiant métier de la plateforme (id LinkedIn,
  UUID JobTeaser) ;
* l'URL canonique — indispensable pour l'historique, collecté avant l'ajout de
  la colonne ``id_externe``.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import canonical_url


@runtime_checkable
class KnownIndex(Protocol):
    """Contrat minimal attendu par le moteur de collecte."""

    def is_known(self, source: str, external_key: str, url: str = "") -> bool:
        """L'offre est-elle déjà connue (base ou run en cours) ?"""
        ...

    def remember(self, source: str, external_key: str, url: str = "") -> None:
        """Mémorise une offre croisée pour le reste du run."""
        ...


class NullKnownIndex:
    """Index vide : rien n'est connu (première collecte, ``--dry-run``, tests).

    Utilisé aussi quand la télémétrie est désactivée : la collecte reste
    fonctionnelle, seuls l'arrêt anticipé et la déduplication par clé sont perdus.
    """

    def is_known(self, source: str, external_key: str, url: str = "") -> bool:
        return False

    def remember(self, source: str, external_key: str, url: str = "") -> None:
        return None


class InMemoryKnownIndex:
    """Index en mémoire : préchargé depuis la base, enrichi pendant le run.

    La mise à jour immédiate lors d'un ``remember`` est ce qui garantit
    l'invariant de **déduplication transverse** : une offre collectée par la passe
    « Fraîcheur » est immédiatement « connue » pour la passe « Rattrapage » du même
    run, qui ne la retraitera donc ni ne la comptera.
    """

    def __init__(
        self,
        pairs: object | None = None,
        urls: object | None = None,
    ) -> None:
        self._pairs: set[tuple[str, str]] = {
            (str(source), str(key)) for source, key in (pairs or ()) if key  # type: ignore[misc]
        }
        self._urls: set[str] = {str(url) for url in (urls or ()) if url}  # type: ignore[union-attr]
        #: Statistiques d'usage (affichées dans le résumé du pipeline).
        self.hits = 0
        self.remembered = 0

    def is_known(self, source: str, external_key: str, url: str = "") -> bool:
        """Vrai si l'identifiant plateforme OU l'URL canonique est déjà connu."""
        if external_key and (str(source), str(external_key)) in self._pairs:
            self.hits += 1
            return True
        canon = canonical_url(url)
        if canon and canon in self._urls:
            self.hits += 1
            return True
        return False

    def remember(self, source: str, external_key: str, url: str = "") -> None:
        """Ajoute l'offre aux deux ensembles d'identités (idempotent)."""
        if external_key:
            self._pairs.add((str(source), str(external_key)))
        canon = canonical_url(url)
        if canon:
            self._urls.add(canon)
        self.remembered += 1

    def __len__(self) -> int:
        """Nombre de clés mémorisées (identifiants plateforme + URLs canoniques)."""
        return len(self._pairs) + len(self._urls)

    def stats(self) -> dict[str, int]:
        """Compteurs d'usage de l'index (observabilité de la collecte)."""
        return {
            "pairs": len(self._pairs),
            "urls": len(self._urls),
            "hits": self.hits,
            "remembered": self.remembered,
        }
