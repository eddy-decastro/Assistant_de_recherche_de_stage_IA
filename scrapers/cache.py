"""Cache disque minimaliste pour les appels réseau de scraping.

But : **ne jamais refaire deux fois le même appel**. Les descriptions d'offres
sont stables (elles ne changent pas d'une minute à l'autre), un rattrapage
relancé après un échec réseau ou un rate limit doit donc être gratuit.

Chaque entrée est un fichier texte sous ``data/cache/<namespace>/``. Le nom de
fichier combine une forme lisible (slug tronqué) et un hachage court de la clé :
une URL complète contient des caractères interdits sous Windows (``:``, ``?``,
``/``) et ne peut donc pas servir de nom de fichier directement.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

DEFAULT_CACHE_DIR = "data/cache"

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SLUG_MAX_LENGTH = 60


class DiskCache:
    """Cache texte indexé par un couple ``(namespace, clé)``."""

    def __init__(self, root: str | Path = DEFAULT_CACHE_DIR) -> None:
        self.root = Path(root)
        # Statistiques d'usage (affichées dans le rapport du rattrapage).
        self.hits = 0
        self.misses = 0

    def path_for(self, namespace: str, key: str) -> Path:
        """Chemin du fichier de cache (déterministe et sûr sur tous les OS)."""
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
        slug = _SLUG_RE.sub("-", (key or "").strip())[:_SLUG_MAX_LENGTH].strip("-")
        return self.root / namespace / f"{slug or 'entree'}-{digest}.txt"

    def get(self, namespace: str, key: str) -> str | None:
        """Contenu en cache, ou ``None`` si absent."""
        path = self.path_for(namespace, key)
        if path.exists():
            self.hits += 1
            return path.read_text(encoding="utf-8")
        self.misses += 1
        return None

    def set(self, namespace: str, key: str, value: str) -> Path:
        """Écrit une entrée de cache et retourne le chemin créé."""
        path = self.path_for(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def count(self, namespace: str | None = None) -> int:
        """Nombre d'entrées en cache (tous namespaces confondus si ``None``)."""
        base = self.root / namespace if namespace else self.root
        if not base.exists():
            return 0
        return sum(1 for path in base.rglob("*.txt") if path.is_file())
