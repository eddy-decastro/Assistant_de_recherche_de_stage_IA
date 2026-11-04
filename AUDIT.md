# Audit Stage Copilot — 04/11/2026

Audit en lecture seule (API locale, code, base), puis plan de correction validé par Eddy.
Statut de chaque point : ⏳ à faire · ✅ fait · ❌ abandonné.

## Constats

### Notation
- Deux notes coexistent et se contredisent : la liste est triée par la note v3, la pastille affiche la v4.
  Exemples : UMA « Research Scientist » v3 90 / v4 E 0, Kering « Junior AI Scientist » 88 / E 0,
  Harmattan AI 82 / E 0. Ce sont des CDI mal étiquetés « stage » par LinkedIn ; la v3 affiche
  quand même « Stage de 6 mois à Paris ».
- La v4 classe en CDI sans citation (`quote` vide) : décision juste ici, mais invérifiable.
- Offre exclue : 0 étoile partout, mais justifications positives affichées à côté.
- 227 axes ont `quote: "reputation"`, une citation factice autorisée par le prompt.
- Les notes séparent mal ce sur quoi Eddy postule et ce qu'il ignore à la main
  (AUC ≈ 0,53 v3, 0,55 v4 ; petit échantillon : 26 ignorées à la main).
- Tag Thèse / CIFRE en double (`cifre_these` et `these_cifre`).
- Les « questions d'entretien » sont collées dans le champ `reasoning`.
- v4 désactivée dans le pipeline quotidien (`scoring_v4.enabled: false`) : 424 offres notées sur 2 337.

### Scraping
- Sources `ats` et `academic` codées mais absentes de `enabled_sources`. Sondes du 04/11 :
  CNRS RSS 302, CEA 404, Lever `mistral` vide, Greenhouse `huggingface` 404, SmartRecruiters `sanofi` vide.
  Seuls Ashby (`dust`) et le RSS Inria répondent.
- Pas de Workday (Thales, Safran, EDF, Schneider, Stellantis : classes A/B).
- Archivage automatique à 21 jours : 981 offres archivées, dont 92 notées ≥ 80 (NAVER Labs à 100).
- WTTJ : pas de filtre pays (environ 30 % de stages hors France, diagnostic du 30/09).
- À trier : 45 offres LinkedIn contre 274 WTTJ, alors que LinkedIn domine la base.

### Interface
- Deux notes affichées (grand chiffre v3, pastille v4).
- Doublons visuels par entreprise (HGH Infrared : 4 lignes quasi identiques).
- Dates arrondies (« Il y a 8 j / 20 j ») ; NAVER affiche 24 j pour une offre publiée il y a 135 j.
- Liste plafonnée à 250 offres sur 319.

### Candidature avec Claude in Chrome
- Le Pack Claude est un long prompt à coller ; rien ne revient dans l'app après candidature.
- Claude in Chrome ne téléverse pas de PDF, ne crée pas de compte, ne saisit pas de mot de passe.

### Autres
- Règle Git contradictoire entre `CLAUDE.md` du projet et global.
- Scripts ponctuels non suivis et fichiers de plan obsolètes à la racine.

## Plan validé

### Notation
| # | Action | Statut |
|---|---|---|
| N1 | Une seule note : v4 principale, v3 retirée de l'affichage et du tri ; v4 activée dans le pipeline | ⏳ |
| N2 | `contract_quote` obligatoire ; sans citation → « contrat à vérifier » ; exclusions dans un onglet à part | ⏳ |
| N3 | Raison en un clic quand on ignore une offre → exemples injectés dans le prompt + jeu d'évaluation | ⏳ |
| N4 | Supprimer `quote: "reputation"` ; la réputation passe uniquement par `reputation_points` | ⏳ |
| N5 | Dédoublonner le tag Thèse / CIFRE ; séparer les questions d'entretien du `reasoning` | ⏳ |

### Scraping
| # | Action | Statut |
|---|---|---|
| S1 | Réparer et activer `ats` et `academic` ; une source en panne ne bloque rien, la cause est visible | ⏳ |
| S2 | Ajouter un scraper Workday (API JSON publique) | ⏳ |
| S3 | Sources recherche (GdR, ABG…) : Eddy s'en charge avec Claude in Chrome | ❌ (hors code) |
| S4 | Remplacer l'archivage par âge par une détection « offre fermée » (HTTP + texte « n'accepte plus de candidatures ») | ⏳ |
| S5 | Filtre pays France sur WTTJ | ⏳ |
| S6 | Diagnostiquer le faible volume LinkedIn (429, arrêt anticipé) | ⏳ |

### Interface
| # | Action | Statut |
|---|---|---|
| U1 | Une seule note affichée, avec la classe | ⏳ |
| U2 | Regrouper les offres d'une même entreprise | ⏳ |
| U3 | Vraie date de publication | ⏳ |
| U4 | Raccourcis clavier (J/K, P, I) avec aide visible | ⏳ |
| U5 | Chargement au défilement (plus de limite à 250) | ⏳ |

### Candidature avec Claude in Chrome
| # | Action | Statut |
|---|---|---|
| C1 | Page file d'attente `/file` + endpoint « marquer postulé » ; Claude laisse un compte rendu caveman de 2 phrases | ⏳ |
| C2 | Le CV PDF est téléversé à la main : Claude s'arrête sur ce champ et le signale | ⏳ |
| C3 | Étiqueter chaque offre par méthode de candidature (sans compte d'abord) | ⏳ |

### Autres
| # | Action | Statut |
|---|---|---|
| A1 | Commit direct sur `main` (règle du projet alignée sur la règle globale) | ✅ |
| A2 | Supprimer les fichiers anciens et inutiles | ⏳ |
