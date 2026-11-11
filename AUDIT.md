# Audit du 11/11/2026 : postuler plus vite et plus simplement

Audit en lecture seule du code (API, file de candidature, lettres, front Next.js, base, CI).
Aucun fichier de code n'a été modifié. Les chiffres viennent d'une copie temporaire de
`data/stage_copilot.db` (fichier principal, sans le journal WAL : ils peuvent avoir quelques heures de retard).

Audits précédents : [docs/AUDIT_2026-11-04.md](docs/AUDIT_2026-11-04.md) (notation, scraping, interface)
et [docs/PLAN_AMELIORATION_2026-11-10.md](docs/PLAN_AMELIORATION_2026-11-10.md).

Statut : ⏳ à faire · ✅ fait · 🧑 action d'Eddy.

## 1. Le constat principal

La collecte et la notation marchent. C'est la candidature qui bloque.

| Mesure | Valeur |
|---|---|
| Offres à trier | 1 113 |
| dont notées 80 ou plus (cœur de cible) | 76 |
| dont notées 60 à 79 | 281 |
| dont pas encore notées | 274 |
| Candidatures envoyées (POSTULÉ + ENTRETIEN) | 46 |
| Candidatures depuis début octobre (5 semaines) | 1 |
| Offres dans la file Claude (`apply_queue`) | 0, la file n'a jamais servi |
| Lettres de motivation en base | 1 |
| CV PDF dans `data/` | aucun, et `candidate.cv_pdf_path` absent de `config.yaml` |

76 offres au cœur de cible attendent, et l'outil de candidature construit le 04/11
(Pack Claude, page `/file`) n'a jamais traité une seule offre. La suite explique pourquoi
et propose quoi changer, du plus rentable au moins rentable.

## 2. Problèmes bloquants (ils empêchent de postuler vite)

### B1. Préparer un dossier est lent et fragile
- Chaque offre demande jusqu'à 4 appels LLM à la suite : 2 pour la lettre
  (`CoverLetterGenerator.generate`, un essai puis une correction) et 2 pour la motivation courte
  (`generate_short_motivation`). Chaque appel peut durer 35 s.
- Tout se fait dans une seule requête HTTP, offre après offre
  ([claude_pack.py:246](src/matching/claude_pack.py:246), [api/main.py:712](api/main.py:712)).
  Un pack de 10 offres peut prendre plusieurs minutes, avec un simple message « Préparation… ».
- Le front passe par le relais de Next.js, qui coupe par défaut une requête au bout de 30 s.
  Non testé en vrai : à vérifier, mais un pack de plusieurs offres a toutes les chances d'échouer.
- Dans `build_claude_pack`, une seule lettre en échec fait échouer tout le pack (erreur 503).
  `add_jobs_to_queue` gère bien ce cas, offre par offre ; les deux chemins ne sont pas alignés.
- La motivation courte n'est pas enregistrée en base : elle est refaite (et repayée) à chaque pack.
- Cocher « envoi automatique » dans le pack refait tout le dossier de chaque offre
  ([PackModal.tsx:60](web/src/components/PackModal.tsx:60) appelle `queueAdd`, qui régénère).

### B2. La page `/file` est faite pour un robot, pas pour toi
- Impossible de marquer une offre « postulé » sans écrire un compte rendu de 2 phrases
  ([file/page.tsx:85](web/src/app/file/page.tsx:85)). Utile pour Claude, pénible pour un humain.
- Aucun bouton « Passer », « Retirer de la file » ou « Réessayer ». La route `DELETE /api/queue/{id}`
  existe, mais `api.queueRemove` n'est appelé nulle part dans le front.
- Une offre en échec reste dans la liste sans aucune action possible. Pour la relancer,
  il faut retourner au tri, la retrouver et refaire un Pack.
- Une seule offre visible à la fois, dans l'ordre imposé.

### B3. « Postulé » ne veut pas dire « envoyé »
- Quand Claude clique « Marquer postulé », l'offre passe au statut POSTULÉ avec une date
  ([apply_queue.py:647](src/matching/apply_queue.py:647)), alors que par défaut il n'a pas le droit
  de cliquer Envoyer. Le formulaire est juste rempli dans un onglet.
- Si l'onglet est fermé ou oublié, l'offre est comptée comme candidature alors que rien n'est parti.
  La date de relance (14 jours) est fausse aussi.
- Le badge « À finir par toi » existe, mais aucune liste ne regroupe ces offres.

### B4. Le CV PDF reste un geste manuel à chaque offre
- L'audit du 04/11 notait « Claude in Chrome ne téléverse pas de PDF ». L'extension a aujourd'hui
  un outil de téléversement de fichier (visible dans ma session). Non testé sur un vrai formulaire.
- Aucun CV PDF n'est rangé dans `data/` et le chemin n'est pas configuré : la page `/file`
  affiche « Aucun CV PDF configuré ».

### B5. Le profil candidat n'est modifiable que dans l'ancienne interface
- La page Paramètres (coordonnées, CV) n'existe que dans Streamlit, qui est déprécié.
  Le front Next.js n'a que deux pages : le tri et la file.
- Si l'e-mail ou le téléphone manque, le dossier contient le texte « [À compléter dans Paramètres] »
  ([claude_pack.py:69](src/matching/claude_pack.py:69)), que Claude peut coller tel quel dans un formulaire.
- École, niveau, durée, date de début, type de contrat et autorisation de travail sont écrits en dur
  dans le code ([claude_pack.py:74](src/matching/claude_pack.py:74)), comme l'accroche courte
  (ligne 91) et le nom du PDF ([api/main.py:699](api/main.py:699)). Changer ta date de début
  demande de modifier du Python.

## 3. Problèmes majeurs

### M1. Pas de lien direct vers le formulaire de candidature
- Aucun scraper ne collecte `apply_url` ni `easy_apply`, et la table `jobs` n'a pas ces colonnes.
  `detect_apply_method` les lit quand même : il devine depuis l'URL de l'annonce et la source.
- Conséquence : toute offre LinkedIn est classée « site de l'entreprise », jamais « candidature simplifiée ».
  « Ouvrir l'offre » mène à l'annonce, pas au formulaire : un ou deux clics de plus à chaque fois.
- LinkedIn pèse 406 des 1 113 offres à trier.

### M2. Le tri n'aide pas à choisir les candidatures faciles
- La méthode de candidature (sans compte, compte requis, e-mail) n'apparaît qu'une fois l'offre
  dans la file. Dans le tri, rien ne dit « celle-ci se fait en 2 minutes ».
- Pas de filtre ni de tri « sans compte d'abord ».
- Le raccourci `P` marque « postulé » sans ouvrir l'offre. Il n'existe pas de geste unique
  « ouvrir l'offre et préparer ce qu'il faut coller ».

### M3. Les relances ne sont qu'une étiquette
- Le tableau affiche « À relancer : postulé il y a N jours », sans action.
- `generate_followup_message` existe dans [cover_letter.py:1000](src/matching/cover_letter.py:1000),
  mais n'est branché que dans Streamlit.
- Rien n'enregistre qu'une relance a été faite, ni la date d'un entretien.

### M4. Réponses types trop courtes
- `build_standard_answers` couvre 9 questions. Il manque les classiques des formulaires :
  niveau d'anglais et autres langues, mobilité, permis, « comment avez-vous connu l'entreprise »,
  questions de diversité (à laisser « ne souhaite pas répondre »), lien vers un portfolio.
- Les réponses ne sont pas modifiables sans toucher au code.
- Aucune version anglaise des réponses, alors que les lettres savent détecter une offre en anglais.

### M5. L'API locale est ouverte à tous les sites web
- `allow_origins=["*"]` et aucune authentification ([api/main.py:178](api/main.py:178)).
  N'importe quelle page ouverte dans ton navigateur peut appeler `http://127.0.0.1:8000` :
  lire tes coordonnées (`/api/queue/current`), changer des statuts, lancer des notations payantes.
- Le risque est réel parce que Claude in Chrome visite des sites inconnus pendant que l'API tourne.
- Correction simple : n'autoriser que `http://localhost:3000`.

### M6. Chaque action recharge beaucoup
- Chaque changement de statut relance `/api/stats` plus 4 requêtes de comptage, chacune filtrant
  toute la base en Python. Le plan du 10/11 note déjà environ 20 s au premier chargement.
- Ça ralentit le tri en rafale au clavier, le geste le plus fréquent.

## 4. Problèmes mineurs

- « Copier » de la lettre n'a pas de gestion d'erreur ([LetterModal.tsx:64](web/src/components/LetterModal.tsx:64)).
- La CI ne vérifie pas le front (ni `tsc`, ni `next build`) et le front n'a aucun test.
- Le workflow `daily_scraper.yml` tourne encore chaque jour à 06:00 UTC alors que le run GitHub est abandonné.
- L'API dépend du code Streamlit (`components/job_feed/serialize.py`, `utils/data.py`) :
  impossible de supprimer Streamlit sans déplacer ces deux fichiers. Environ 6 000 lignes d'ancienne interface restent maintenues.
- `src/storage/database.py` fait 2 055 lignes (déjà noté D4).
- `datetime.utcnow()` est déprécié depuis Python 3.12.
- `data/` reste encombré (`.bak`, sauvegarde du 30/09, `jobs.db` vide) : déjà noté le 10/11.
- Le tableau des candidatures n'a pas de glisser-déposer (menu « Déplacer vers… » à la place).

## 5. Améliorations proposées

Classées par gain pour toi. « Effort » : S = une heure ou deux, M = une demi-journée, L = une journée ou plus.

### Lot 1 : postuler toi-même en un geste (le plus rentable)
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P1 | Bouton « Postuler » dans le détail (raccourci `A`) : ouvre l'offre dans un onglet et affiche un panneau « kit » avec un bouton Copier par champ (coordonnées, accroche, lettre, réponses types). C'est le contenu de `/file`, sans passer par la file | M | ⏳ |
| P2 | Dans ce panneau, deux boutons : « C'est envoyé » (POSTULÉ, sans compte rendu) et « Plus tard » | S | ⏳ |
| P3 | La lettre se rédige en arrière-plan dès l'ouverture du panneau ; le reste du kit est utilisable tout de suite | M | ⏳ |
| P4 | Compteur du jour dans la barre du haut (« 3 candidatures aujourd'hui, objectif 5 ») | S | ⏳ |

### Lot 2 : dossiers prêts d'avance
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P5 | Préparation en arrière-plan : « Ajouter à la file » répond tout de suite, l'offre passe par un état « en préparation ». `utils/task_manager.py` fait déjà ce travail pour Streamlit | M | ⏳ |
| P6 | Commande `python tools/prepare_top.py --n 20` : prépare les lettres des 20 meilleures offres pendant que tu fais autre chose (essai à blanc par défaut, sauvegarde avant écriture) | M | ⏳ |
| P7 | Enregistrer la motivation courte en base au lieu de la refaire à chaque pack | S | ⏳ |
| P8 | Une lettre en échec n'arrête plus le pack ; « envoi automatique » ne régénère plus rien | S | ⏳ |

### Lot 3 : une file utilisable par toi et par Claude
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P9 | Nouvel état « À finaliser » : Claude a rempli, tu dois cliquer Envoyer. L'offre ne passe POSTULÉ qu'après ton clic « Envoyé » | M | ⏳ |
| P10 | Liste « À finir par moi » en haut de `/file`, avec le lien de l'offre et le bouton « Envoyé » | S | ⏳ |
| P11 | Boutons « Passer », « Retirer » et « Réessayer » sur chaque ligne de la file | S | ⏳ |
| P12 | Compte rendu facultatif quand c'est toi qui cliques | S | ⏳ |

### Lot 4 : ton profil au même endroit
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P13 | Sortir du code l'école, le niveau, les dates, la durée, l'accroche : tout dans `data/candidate.local.yaml` (déjà ignoré par Git) | S | ⏳ |
| P14 | Banque de réponses modifiable dans le même fichier, en français et en anglais | M | ⏳ |
| P15 | Page `/profil` dans le front Next.js pour modifier ces champs et voir ce qui manque | M | ⏳ |
| P16 | Refuser de préparer un dossier si l'e-mail, le téléphone ou le CV PDF manque, avec un message clair | S | ⏳ |
| P17 | 🧑 Déposer ton CV dans `data/` et renseigner `candidate.cv_pdf_path` | 5 min | 🧑 |

### Lot 5 : le bon lien et la bonne méthode
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P18 | Collecter le lien de candidature et « candidature simplifiée » sur LinkedIn et WTTJ (deux colonnes à ajouter, migration) | L | ⏳ |
| P19 | Afficher la méthode dans le tri (pastille « sans compte », « compte requis », « e-mail ») | S | ⏳ |
| P20 | Filtre et tri « candidatures faciles d'abord » | S | ⏳ |
| P21 | Offres par e-mail : bouton qui ouvre ta messagerie avec destinataire, objet et lettre pré-remplis. Tu envoies toi-même | S | ⏳ |

### Lot 6 : Claude fait plus
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P22 | Tester le téléversement du CV par Claude in Chrome sur un formulaire Lever ou Greenhouse. Si ça marche, retirer la règle « tu ne peux joindre aucun PDF » du prompt et de `/file` | S | ⏳ |
| P23 | Piloter la file depuis Claude Code (ou l'app Claude) au lieu de coller un prompt : Claude lit `/api/queue/current` directement | M | ⏳ |

Limites qui restent : Claude ne crée pas de compte et ne saisit pas de mot de passe. Les sites
Workday, Taleo et SuccessFactors (234 offres Workday à trier) demandent donc un compte que tu crées
toi-même, une fois par entreprise.

### Lot 7 : suivi après candidature
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P24 | Bouton « Relancer » sur les cartes de plus de 14 jours : message généré (le code existe), bouton Copier, date de relance enregistrée | M | ⏳ |
| P25 | Date d'entretien sur la carte, affichée en tête de colonne | S | ⏳ |

### Lot 8 : technique
| # | Proposition | Effort | Statut |
|---|---|---|---|
| P26 | CORS limité à `http://localhost:3000` | S | ⏳ |
| P27 | Compteurs des onglets renvoyés par un seul appel au lieu de 5 | M | ⏳ |
| P28 | CI : ajouter `npx tsc --noEmit` et `npm run build` pour le front | S | ⏳ |
| P29 | Désactiver le cron de `daily_scraper.yml` (garder le lancement manuel) | S | ⏳ |
| P30 | Déplacer `serialize.py` et `utils/data.py` hors de Streamlit, puis décider du sort de Streamlit (branche + PR : suppression massive) | L | ⏳ |

## 6. Par où commencer

1. P17 (5 minutes, toi) puis les lots 1 et 4 : tu peux postuler toi-même en 2 ou 3 minutes par offre, sans dépendre de Claude in Chrome.
2. Lot 2 : les lettres sont prêtes avant que tu t'assoies.
3. Lots 3 et 6 : Claude remplit, tu valides.
4. Lots 5, 7 et 8 ensuite.

## 7. Vérifications

- `pytest` : 533 tests verts en 1 min 48 (journal dans `runs/audit_pytest.log`).
- `ruff check .` : aucune erreur.
- `npx tsc --noEmit` dans `web/` : aucune erreur de type.
- Non vérifié : la coupure à 30 s du relais Next.js (B1), le téléversement de fichier par Claude in Chrome (B4),
  l'application en fonctionnement (je ne l'ai pas lancée).
