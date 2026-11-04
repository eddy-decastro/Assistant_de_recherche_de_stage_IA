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
| N1 | Une seule note : v4 principale, v3 retirée de l'affichage et du tri ; v4 activée dans le pipeline | ✅ La v4 est activée, notée au fil de l'eau (offres NOUVEAU seulement) et sert au tri partout : API, front, Streamlit et notifications ; le juge v3 ne tourne plus que si `scoring_v3.rerank_enabled` vaut true. |
| N2 | `contract_quote` obligatoire ; sans citation → « contrat à vérifier » ; exclusions dans un onglet à part | ✅ Sans citation vérifiée, l'offre est notée avec le badge « Contrat à vérifier » au lieu d'être exclue ; l'onglet Exclues du front montre le motif et les étoiles brutes. |
| N3 | Raison en un clic quand on ignore une offre → exemples injectés dans le prompt + jeu d'évaluation | ✅ Le menu « Ignorer… » propose 10 raisons et un commentaire, les décisions alimentent le bloc « DÉCISIONS RÉCENTES D'EDDY » du prompt, et `tools/eval_feedback.py` mesure l'AUC (v4 0,69 contre 0,74 pour la v3 au 04/11). |
| N4 | Supprimer `quote: "reputation"` ; la réputation passe uniquement par `reputation_points` | ✅ L'exception est retirée du prompt et du code ; elle ne s'appliquera aux 424 notes existantes qu'après un `--recompute-scores-v4` (voir « Reste à faire »). |
| N5 | Dédoublonner le tag Thèse / CIFRE ; séparer les questions d'entretien du `reasoning` | ✅ `cifre_these` est ramené à `these_cifre` à la lecture, et les questions d'entretien vont dans `questions_entretien`, plus dans `reasoning`. |
| N6 | Eddy corrige la note de l'IA (classe + note + « car … ») ; ces corrections sont injectées dans le prompt v4 | ✅ Le bloc « Ma note » du détail enregistre la correction (`user_v4_*`, protégé à la sync R2), qui est injectée en tête du prompt sous « CORRECTIONS D'EDDY » ; `eval_feedback` calcule l'écart IA / Eddy. |

### Scraping
| # | Action | Statut |
|---|---|---|
| S1 | Réparer et activer `ats` et `academic` ; une source en panne ne bloque rien, la cause est visible | ✅ Les 27 boards ATS, Inria et CEA sont « ok » au 04/11 (CNRS retiré) ; un board en panne donne une alerte, pas un arrêt, et `/api/sources/health` en expose la cause. |
| S2 | Ajouter un scraper Workday (API JSON publique) | ✅ 11 grands groupes (Thales, Airbus, Renault, Sanofi…), stages France seulement, activé dans `enabled_sources` et dans la page Paramètres. |
| S3 | Sources recherche (GdR, ABG…) : Eddy s'en charge avec Claude in Chrome | ❌ (hors code) |
| S4 | Remplacer l'archivage par âge par une détection « offre fermée » (HTTP + texte « n'accepte plus de candidatures ») | ✅ L'archivage par âge est coupé ; seules les offres NOUVEAU dont la page prouve la fermeture passent en IGNORÉ (`offre_fermee`), et un 429 ou une page anti-robot ne concluent rien. Le rendement réel reste à observer au prochain run. |
| S5 | Filtre pays France sur WTTJ | ✅ `scrapers.wttj_country: FR` (333 stages, tous en France, au lieu de 415 dont 33 % à l'étranger). |
| S6 | Diagnostiquer le faible volume LinkedIn (429, arrêt anticipé) | ✅ Ce n'était ni un 429 ni un arrêt anticipé : LinkedIn invité ignore `f_JT=I`, donc le quota se remplissait de CDI exclus ensuite. Les requêtes reçoivent maintenant des mots-clés de stage (17 stages sur 20 au lieu de 0). |

### Interface
| # | Action | Statut |
|---|---|---|
| U1 | Une seule note affichée, avec la classe | ✅ Une pastille « B 82 », « non notée » ou « exclue » dans la liste et les cartes, tri par défaut « Meilleure note » ; l'ancienne grille v3 est repliée en archive. |
| U2 | Regrouper les offres d'une même entreprise | ✅ Une offre par entreprise en tête avec « N autres offres chez X », dépliable ; la clé `company_key` fusionne les variantes (« Naval Group [FR] »). |
| U3 | Vraie date de publication | ✅ La date affichée et l'âge partent de la date de publication, et « Collectée il y a… » s'affiche en italique quand cette date est inconnue. |
| U4 | Raccourcis clavier (J/K, P, I) avec aide visible | ✅ J/K et flèches, O, P, I (choix de raison au chiffre), L et ?, avec une ligne d'aide toujours visible sous la liste. |
| U5 | Chargement au défilement (plus de limite à 250) | ✅ Pages de 100 avec `offset` / `has_more`, chargées au défilement, sans plafond. |
| U6 | Libellés de classe dans les filtres du haut | ✅ Les boutons de classe affichent la lettre, le libellé court et le compte (« A Recherche 34 »), avec le libellé complet au survol. |

### Candidature avec Claude in Chrome
| # | Action | Statut |
|---|---|---|
| C1 | Page file d'attente `/file` + endpoint « marquer postulé » ; Claude laisse un compte rendu caveman de 2 phrases | ✅ Le Pack Claude remplit la file, et `/file` montre l'offre courante avec les boutons « Marquer postulé » / « Échec » et un compte rendu de 2 phrases au plus, vérifié côté page et côté API. Testée en vrai seulement sur une file vide. |
| C2 | Le CV PDF est téléversé à la main : Claude s'arrête sur ce champ et le signale | ✅ Le prompt et la page disent de ne pas soumettre sans CV et d'écrire « CV à joindre » ; il manque encore le chemin du CV PDF dans la config (voir « Reste à faire »). |
| C3 | Étiqueter chaque offre par méthode de candidature (sans compte d'abord) | ✅ 13 méthodes reconnues (WTTJ, LinkedIn, Greenhouse, Workday, JobTeaser…), « compte requis » signalé, offres sans compte en tête de file. LinkedIn simplifiée ou externe reste incertain. |

### Autres
| # | Action | Statut |
|---|---|---|
| A1 | Commit direct sur `main` (règle du projet alignée sur la règle globale) | ✅ |
| A2 | Supprimer les fichiers anciens et inutiles | ⏳ Pas encore traité. |
| A3 | Garde-fous de tests : aucun appel LLM réel, aucune ouverture de la vraie base | ✅ `tests/conftest.py` vide les clés LLM, bloque les hôtes LLM et redirige toute `Database` ouverte sur `data/stage_copilot.db` ou `data/jobs.db` vers une base jetable (la page Candidatures testée ouvrait la vraie base). |

## Reste à faire

1. **Recalculer les notes v4 existantes** (à lancer par Eddy, ça écrit dans la vraie base). D'abord copier `data/stage_copilot.db` dans `data/backups/`, puis lancer `python run_pipeline.py --recompute-scores-v4`. Effets attendus : N2 et N4 s'appliquent aux 424 notes existantes, 227 axes débouché cités « reputation » perdent une étoile, et les CDI notés sans citation passent en « contrat à vérifier ».
2. **CV PDF** (C2) : renseigner `candidate.cv_pdf_path: data/<CV>.pdf` dans `config.yaml`, sinon la page `/file` affiche « Aucun CV PDF configuré ».
3. **981 offres archivées par âge** (raison `auto_archive`) : elles restent IGNORÉ. Les remettre en NOUVEAU pour que la détection d'offre fermée les trie demande un script ponctuel sur la vraie base, après sauvegarde. C'est à Eddy de décider.
4. **Observer le prochain run** : le rendement de la détection d'offre fermée (S4, WTTJ coupe en anti-robot après une rafale) et le volume de stages LinkedIn (S6).
5. **Boucle de retour (N3, N6)** : aucune des 40 offres ignorées n'a encore de raison, et aucune note n'est corrigée. Il faut qu'Eddy s'en serve, puis relancer `tools/eval_feedback.py` pour voir si l'AUC v4 dépasse la v3.
6. **Petits manques du front** :
   - l'onglet « 60 et plus » inclut le cœur de cible, faute de `max_score` dans l'API ;
   - annuler un « ignorer » laisse le commentaire en base ;
   - pas d'action « Remettre à trier » dans l'onglet Exclues.
7. **Sources absentes** : Safran, Schneider (iCIMS), Stellantis, EDF et TotalEnergies ne sont pas sur Workday. S3 (GdR, ABG) reste hors code.
8. **A2** : faire le ménage des fichiers anciens à la racine, en vérifiant d'abord qu'ils ne sont plus utilisés.
