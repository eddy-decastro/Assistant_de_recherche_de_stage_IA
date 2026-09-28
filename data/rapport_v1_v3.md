# Rapport comparatif : Réforme du Scoring (v1 vs v3)

*Date d'évaluation : 2026-09-29*
*Échantillon analysé : 50 offres réelles de la base SQLite (`stage_copilot.db`)*

---

## 1. Métriques globales de transition

- **Corrélation de rang (Spearman) v1 vs v3** : `0.527`
- **Offres écartées / exclues (Score 0)** : `0 / 50 (0.0 %)`
- **Planchers déclenchés** : `35 offres`
- **Plafonds stricts déclenchés** : `3 offres`

### Distribution des planchers par catégorie
- **Plancher scale-up 70** : 3 offres
- **Plancher grand groupe R&D / labo privé 60** : 29 offres
- **Plancher labo public 50** : 3 offres

### Distribution des plafonds stricts (Hard Caps)
- **Plafond DEFENSE** : 3 offres

---

## 2. Plus grands écarts de classement (Gagnantes & Perdantes)

### 🚀 Plus fortes progressions (Gagnantes de la réforme v3)
Les offres bénéficiant des planchers de catégorie (Scale-up FT120/Next40, Grands Groupes R&D, Labos publics) et des bonus de signaux vérifiés :

| Entreprise | Intitulé | Note v1 | Note v3 (Qualité) | Gain | Motif principal |
| :--- | :--- | :---: | :---: | :---: | :--- |
| ChapsVision | Stage Data Scientist IA – Optimisation d | 72 | **70** (50) | +-2 | Plancher scale-up 70 |
| Dassault Systèmes | STAGE - Ingénieur Recherche Physics Mach | 80 | **71** (71) | +-9 | Plancher grand groupe R&D / labo privé 60 |
| Dassault Systèmes | STAGE - Ingénieur.e Recherche Deep Reinf | 80 | **71** (71) | +-9 | Plancher grand groupe R&D / labo privé 60 |
| Dassault Systèmes | STAGE Ingénieur.e Recherche IA pour la s | 80 | **71** (71) | +-9 | Plancher grand groupe R&D / labo privé 60 |
| Dassault Systèmes | STAGE Ingénieur.e Recherche IA générativ | 80 | **71** (71) | +-9 | Plancher grand groupe R&D / labo privé 60 |

### 🔻 Plus fortes régressions (Pénalisées ou Plafonnées par la réforme v3)
Les offres touchées par les exclusions contractuelles, les plafonds éthiques stricts ou l'absence de profondeur technique :

| Entreprise | Intitulé | Note v1 | Note v3 (Qualité) | Perte | Motif principal |
| :--- | :--- | :---: | :---: | :---: | :--- |
| Helsing | AI Research Engineer - Foundation Models | 95 | **10** (71) | -85 | DEFENSE |
| Mistral AI | Applied AI, Forward Deployed Machine Lea | 88 | **10** (71) | -78 | DEFENSE |
| Helsing | AI Research Intern (PhD) – 3D Computer V | 81 | **10** (71) | -71 | DEFENSE |
| Airbus Aircraft | STAGE 2027 - Scientific Machine Learning | 79 | **50** (50) | -29 | Baisse qualité |
| Pellenc ST | Stage ingénieur de recherche/ Intelligen | 79 | **50** (50) | -29 | Baisse qualité |

---

## 3. Analyse des Citations et Signaux Qualitatifs

- Le système de vérification des citations par fenêtre glissante (tolérance 80 %) a permis de valider systématiquement les preuves d'encadrement senior et de données réelles.
- **Principe de précaution validé** : Une citation non retrouvée annule le bonus (+6 ou +3) sans infliger de pénalité arbitraire sur la note intrinsèque.
- Les données de benchmark pur subissent un malus calibré de -5 uniquement en cas de preuve formelle.

---

## 4. Recommandation de calibrage des seuils de verdict

Sur la base de la distribution observée sur l'échantillon réel :
- **EXCELLENT (≥ 85)** : Cœur de cible absolu (Scale-ups IA avec encadrement vérifié, thèses CIFRE, Grands groupes R&D de pointe).
- **BON (≥ 70)** : Offres de haute qualité éligibles aux planchers Scale-up et R&D d'excellence.
- **MITIGÉ (≥ 50)** : Projets d'ingénierie standards, laboratoires sans débouché explicite ou startups précoces.
- **HORS_SUJET (< 50 ou EXCLU)** : Offres sans profondeur technique, ESN en régie, offres relevant de la Défense ou stages courts hors cursus.

*Conclusion : Les seuils configurés dans `config.yaml` reflètent fidèlement la nouvelle sélectivité de l'algorithme.*
