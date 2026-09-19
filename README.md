# 🎯 Stage Copilot — Assistant & Pipeline Intelligent de Recherche de Stage

<div align="center">

![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue?logo=python&logoColor=white)
![Framework](https://img.shields.io/badge/UI-Streamlit-FF4B4B?logo=streamlit&logoColor=white)
![LLM](https://img.shields.io/badge/IA-Gemini%202.5%20Flash-4285F4?logo=google&logoColor=white)
![Database](https://img.shields.io/badge/Base-SQLite%20%2B%20SQLAlchemy-003B57?logo=sqlite&logoColor=white)
![Scraping](https://img.shields.io/badge/Collecte-LinkedIn%20%2B%20JobTeaser-0077B5?logo=linkedin&logoColor=white)
![Tests](https://img.shields.io/badge/Tests-88%2F88%20Passing-brightgreen?logo=pytest&logoColor=white)

**Une plateforme d'ingénierie tout-en-un pour sourcer, évaluer par IA et postuler stratégiquement aux meilleurs stages R&D / Data Science.**

[Fonctionnalités](#-fonctionnalités-clés) • [Architecture](#-architecture) • [Installation](#-installation--démarrage-rapide) • [Guide d'utilisation](#-guide-dutilisation) • [Télémétrie & Scraping](#-stratégie-de-collecte-hybride)

</div>

---

## 💡 Pourquoi Stage Copilot ?

Rechercher un stage de fin d'études (PFE) d'élite en Intelligence Artificielle et Data Science est souvent fastidieux : offres noyées dans le bruit (Business Intelligence, support, alternances déguisées), descriptions tronquées sur les agrégateurs et perte de temps sur des candidatures génériques.

**Stage Copilot** automatise l'intégralité de la chaîne de valeur :
1. **Collecte hybride et résiliente** des offres sur LinkedIn et JobTeaser (contournement Cloudflare, déduplication stricte et mémoire de collecte anti-doublon).
2. **Enrichissement systématique (Backfill)** des descriptions complètes depuis les pages détail.
3. **Scoring & Reranking 100% LLM** : un persona de *Head of Data* propulsé par **Gemini 2.5 Flash** évalue chaque mission sur une grille d'exigence stricte avec verrous bloquants (*hard caps*).
4. **Générateur instantané de lettres de motivation** ultra-ciblées au style académique formel.
5. **Console de pilotage Streamlit complète** avec vue flux, tableau **Kanban**, **Market Analytics** et panneau de **Paramètres No-Code** pour ajuster son profil et son scraping en direct.

---

## ✨ Fonctionnalités Clés

### 🧠 1. Évaluation & Reranking 100% LLM (Gemini 2.5 Flash)
- **Persona Head of Data** : Évaluation impitoyable de la substance mathématique et algorithmique de l'offre par rapport au profil candidat.
- **Grille de 4 sous-scores (1 à 5)** :
  - `modeling_depth` : Profondeur algorithmique (du simple SQL/BI jusqu'au Deep Learning, GNN et R&D de pointe).
  - `mentorship_team` : Encadrement (présence de PhD, Staff ML Engineers vs stagiaire isolé).
  - `career_leverage` : Tremplin de carrière (Scale-ups Tier 1, laboratoires de référence vs ESN généraliste).
  - `pfe_compatibility` : Adéquation calendrier et convention de stage 6 mois.
- **Verrous bloquants (*Hard Caps*)** : Plafonnement automatique et immédiat du score en cas d'alternance imposée (note $\le 15$), de mission axée reporting/BI ($\le 20$) ou d'IA superficielle/prompt engineering ($\le 40$).

### ✍️ 2. Générateur de Lettres de Motivation IA
- **À la demande en un clic** : Bouton `Lettre` disponible sur chaque carte et dans le Kanban.
- **Style académique & complet** : Lettre formelle d'une page respectant la structure classique (*Vous / Moi / Nous*), valorisant vos projets réels en regard des besoins de l'offre.
- **Détection de langue** : Rédige automatiquement en anglais professionnel si l'offre est en anglais, en français soutenu sinon.
- **Édition & Export** : Zone de texte modifiable pour prévisualiser la lettre et la télécharger instantanément en `.txt` ou `.md`.

### 📊 3. Tableau de Bord Multi-Pages (Streamlit)
- **Accueil (`app.py`)** : Bandeau KPI dynamique, flux de cartes enrichies, scores R&D, points forts, alertes et bouton direct `🚀 Postuler ↗`.
- **Kanban (`pages/kanban.py`)** : Suivi visuel de l'avancement de vos candidatures (*Nouveau*, *Postulé*, *Entretien*, *Refusé*, *Ignoré*) modifiable en 1 clic.
- **Télémétrie & Market Analytics (`pages/statistiques.py`)** : Graphique d'évolution temporelle des publications d'offres et observabilité complète des runs de collecte.
- **Pipeline (`pages/pipeline.py`)** : Lancement des collectes et du scoring avec streaming du journal en direct.
- **Paramètres No-Code (`pages/parametres.py`)** :
  - **Upload de CV** : Déposez directement votre CV en **PDF** ou **texte (.txt)** avec extraction automatique (`pypdf`) et sauvegarde sans toucher au code.
  - **Gestion du Scraping** : Personnalisez les mots-clés de recherche, plafonds et seuils de fraîcheur directement dans l'interface.
  - **Re-notation des offres** : Réévaluez le vivier d'offres d'un coup avec votre nouveau profil CV.

---

## 🏗️ Architecture

```
Assistant_recherche_de_stage/
├── app.py                      # Application Streamlit principale (Dashboard & flux d'offres)
├── run_pipeline.py             # Orchestrateur unifié : Collecte → Backfill → Reranking LLM
├── run_scrapers.py             # Moteur de collecte unifié avec arguments CLI
├── backfill_descriptions.py    # Rattrapage automatique des fiches détaillées (LinkedIn/JobTeaser)
├── config.yaml                 # Configuration centrale (scraping, quotas, verrous, base SQLite)
├── requirements.txt            # Dépendances du projet
│
├── pages/                      # Pages de l'interface Streamlit
│   ├── kanban.py               # Tableau Kanban de suivi des candidatures
│   ├── statistiques.py         # Observabilité, télémétrie des passes & Market Analytics
│   ├── pipeline.py             # Déclencheur des scripts de maintenance avec journalisation
│   └── parametres.py           # Options No-Code (Upload CV PDF/TXT, requêtes, re-notation)
│
├── data/
│   ├── cv_eddy.txt             # Profil candidat de référence (utilisé par le LLM)
│   ├── prompt_rerank.txt       # System instruction du juge LLM (grille et persona)
│   └── stage_copilot.db        # Base de données SQLite persistante
│
├── scrapers/                   # Module de scraping robuste
│   ├── base.py                 # Moteur générique de collecte hybride et déduplication
│   ├── manager.py              # Orchestration transverse des sources
│   ├── linkedin.py             # Collecte LinkedIn invité avec filtrage temporel
│   ├── jobteaser.py            # Collecte JobTeaser (impersonation TLS curl_cffi contre Cloudflare)
│   └── known.py                # Gestionnaire d'arrêt anticipé sur offres déjà vues
│
├── src/
│   ├── config.py               # Chargement et sauvegarde dynamique de config.yaml
│   ├── constants.py            # Statuts, seuils, constantes et libellés
│   ├── matching/
│   │   ├── llm_judge.py        # Juge d'évaluation et reranking (Google GenAI SDK)
│   │   └── cover_letter.py     # Générateur de lettres de motivation personnalisées
│   └── storage/
│       └── database.py         # ORM SQLAlchemy (jobs, seen_jobs, scrape_runs, télémétrie)
│
└── tests/                      # Suite de validation automatisée (88 tests)
```

---

## 🚀 Installation & Démarrage Rapide

### 1. Prérequis
- **Python 3.11 ou 3.12** recommandé.
- Une clé API gratuite [Google AI Studio](https://aistudio.google.com/) pour le modèle Gemini.

### 2. Cloner le projet & installer les dépendances
```bash
git clone https://github.com/votre-compte/Assistant_recherche_de_stage.git
cd Assistant_recherche_de_stage

# Création et activation de l'environnement virtuel
python -m venv .venv
# Sur Windows :
.\.venv\Scripts\activate
# Sur Linux/macOS :
source .venv/bin/activate

# Installation des paquets
pip install -r requirements.txt
```

### 3. Configurer les clés d'API (`.env`)
Créez un fichier `.env` à la racine du projet (en vous basant sur `.env.example`) :

```env
# Clé obligatoire pour le scoring des offres et les lettres de motivation
GEMINI_API_KEY=AIzaSy...

# Optionnel : cookies de session pour débloquer JobTeaser (contournement Cloudflare)
JOBTEASER_COOKIES="cf_clearance=...; remember_user_token=..."
```

---

## 💻 Guide d'utilisation

### 1. Lancer l'interface Web
```bash
streamlit run app.py
```
L'interface s'ouvre dans votre navigateur (`http://localhost:8501`). Vous pouvez :
- Consulter et trier vos offres par pertinence R&D.
- Postuler directement via `🚀 Postuler ↗`.
- Générer et exporter une lettre de motivation personnalisée via `✍️ Lettre`.
- Gérer vos statuts dans l'onglet **Kanban**.
- Uploader votre propre CV (PDF ou texte) dans l'onglet **Paramètres**.

### 2. Exécuter le Pipeline complet en une commande
Pour lancer automatiquement la collecte de nouvelles offres, l'enrichissement des descriptions et le scoring Gemini :

```bash
python run_pipeline.py
```
*(Vous pouvez également le déclencher depuis le bouton dédié dans l'onglet Streamlit **Pipeline**).*

---

## 🔄 Stratégie de Collecte Hybride

Chaque requête cible est collectée selon une stratégie à double passe :

| Passe | Tri | Fenêtre | Arrêt anticipé | Rôle |
|---|---|---|---|---|
| **Fraîcheur** | Par date (`sortBy=DD`, `f_TPR`) | 7 jours (configurable) | Oui, dès $N$ offres consécutives déjà en base | Capter les offres fraîchement publiées pour postuler en premier |
| **Rattrapage** | Par pertinence algorithmique | Aucune (historique) | Non (parcourt la pagination) | Récupérer les pépites toujours actives publiées plus tôt |

### Résilience et Anti-Blocage
- **Mémoire de collecte (`seen_jobs`)** : Mémorise l'ensemble des cartes croisées (y compris celles rejetées pour hors-sujet ou contrat invalide) pour garantir un arrêt anticipé fiable sans boucles infinies.
- **Impersonation TLS (`curl_cffi`)** : Contourne les challenges Cloudflare sur l'intranet JobTeaser grâce à l'émulation d'empreinte de navigateur Chrome.
- **Télémétrie complète** : Chaque passe consigne sa raison exacte d'arrêt (`early_stop`, `window_end`, `quota`, etc.) dans SQLite pour s'assurer qu'aucun flux d'offres n'est manqué.

---

## 🧪 Tests & Qualité de Code

Le projet dispose d'une suite de tests complète couvrant le scraping, la persistance base de données, la logique de reranking, le générateur de lettres de motivation et les composants Streamlit :

```bash
python -m pytest tests/
```
```text
======================== 88 passed in 8.52s ========================
```

---

## 📜 Licence & Auteur

Projet développé avec passion par **Eddy** (Élève-ingénieur aux Mines de Saint-Étienne).  
Distribué sous licence MIT. N'hésitez pas à forker et à adapter les filtres à votre profil !
