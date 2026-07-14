# PR-RH-01 — Gestion du temps de présence

| | |
|---|---|
| **Référence** | PR-RH-01 |
| **Version** | 1.0 |
| **Date d'application** | *(à compléter)* |
| **Rédigé par** | *(à compléter — Responsable SI RH)* |
| **Vérifié par** | *(à compléter — Responsable RH)* |
| **Approuvé par** | *(à compléter — Direction)* |
| **Processus** | Ressources Humaines — Gestion du temps de présence |
| **Référentiel** | ISO 9001:2015 |

> **Note de rédaction.** Les champs *(à compléter)* relèvent d'une décision de la
> Direction et ne peuvent pas être présumés. Une procédure non approuvée n'a pas
> de valeur en audit : la revue et l'approbation sont une exigence du §7.5.2.

---

## 1. Objet

Définir les règles de collecte, de calcul, de contrôle et de validation du temps
de présence des salariés, depuis le pointage sur la pointeuse biométrique jusqu'à
la transmission des données à la paie.

Cette procédure vise à garantir que **toute heure payée repose sur une mesure
fiable, tracée et vérifiable**, et que toute anomalie est détectée, traitée et
analysée.

## 2. Domaine d'application

S'applique à **l'ensemble des salariés** soumis au pointage, sur tous les sites
équipés d'une pointeuse (Siège, Agence, Entrepôt, Aéroport), pour tous les
horaires (journée, nuit, samedi).

Support technique : module Odoo `attendance_devices`, pointeuses ZK connectées en
réseau.

## 3. Définitions

| Terme | Définition |
|---|---|
| **Pointage** | Événement horodaté produit par la pointeuse au passage d'un badge. |
| **Journée de shift** | Journée de travail au sens du planning, et non journée calendaire. Un pointage antérieur au *seuil de nuit* (par défaut **06:00**) est rattaché à la **veille**. |
| **Enregistrement de présence** | Ligne consolidée (employé, journée de shift) : entrée, sortie, pause, heures nettes, statuts. C'est **l'élément de sortie** du processus. |
| **Anomalie** | Enregistrement de présence non exploitable en l'état (voir §7). |
| **Dérive d'horloge** | Écart entre l'horloge interne de la pointeuse et l'heure de référence du serveur. |
| **Dérogation** | Acceptation explicite d'un enregistrement non conforme, décidée par une autorité identifiée. |

## 4. Rôles, responsabilités et autorités (ISO 9001 §5.3)

| Activité | Salarié | Manager | Gestionnaire RH | Administrateur SI | Direction |
|---|:--:|:--:|:--:|:--:|:--:|
| Pointer entrée / sortie / pause | **R** | | | | |
| Signaler un oubli de badgeage | **R** | C | I | | |
| Justifier une absence / un retard | **R** | **A** | I | | |
| Traiter les anomalies (motif + décision) | | C | **R/A** | | |
| Modifier une heure d'entrée / sortie | | | **R** *(après « Résolut »)* | **R** | |
| Paramétrer les horaires et les seuils | | | | **R** | **A** |
| Maintenir les pointeuses / traiter une dérive d'horloge | | | I | **R/A** | |
| Rattacher un badge à un salarié | | | **R** | C | |
| Valider la période avant paie | | C | **R** | | **A** |
| Revue mensuelle des causes / actions correctives | | C | **R** | C | **A** |

*R = Réalise · A = Approuve · C = Consulté · I = Informé*

**Autorités exclusives :**
- Seul l'**Administrateur SI** (groupe *Attendance Devices / Administrator*) peut
  modifier les paramètres de connexion des pointeuses et les seuils de shift.
- Le **Gestionnaire RH** (groupe *Attendance Devices / HR*) ne peut modifier une
  heure **qu'après avoir déclaré son intervention** en cochant « Résolut » et en
  renseignant un motif. Ce verrou est appliqué par l'outil, pas par la consigne.

## 5. Règles de badgeage (à porter à la connaissance des salariés — §7.3)

Le calcul des pauses repose sur une **alternance stricte** entrée / sortie.
Chaque salarié doit donc badger :

1. à son **arrivée** ;
2. à son **départ en pause** ;
3. à son **retour de pause** ;
4. à son **départ en fin de journée**.

**Un badgeage manquant fausse le calcul** de la journée et génère une anomalie.
En cas d'oubli, le salarié doit le signaler **le jour même** à son manager.

> *Mesure de maîtrise automatique :* deux passages de badge espacés de moins de
> **60 secondes** sont fusionnés par l'outil (protection contre le double-tap).

## 6. Description du processus

### 6.1 Enrôlement d'un salarié

1. Le Gestionnaire RH crée le salarié et lui affecte un **numéro de badge**
   (`badge_id`), un **département** et un **shift**.
2. Le shift détermine automatiquement les critères applicables (§6.4).
3. Le badge est enrôlé sur la ou les pointeuses du site.

> **Contrôle :** un badge qui pointe sans salarié rattaché est signalé dans le
> journal de synchronisation (« badges non rattachés ») et doit être régularisé
> **sous 24 h** — chaque pointage concerné est une donnée perdue.

### 6.2 Collecte automatique des pointages

| Traitement | Fréquence | Objet |
|---|---|---|
| **Synchronisation des pointeuses** | **toutes les 10 min** | Vérification de l'horloge, lecture des pointages, création/mise à jour des enregistrements, génération des absences, auto-contrôle |
| **Rafraîchissement des statuts** | toutes les 15 min | Fait apparaître les statuts qui dépendent du temps écoulé (absence après-midi, retard de pause) |
| **Clôture des absences** | toutes les heures | Clôt les enregistrements d'absence en fin de shift |
| **Clôture des sorties manquantes** | quotidien (minuit) | Clôt les enregistrements restés ouverts au-delà du délai autorisé |

La synchronisation est **sérialisée** (verrou applicatif) : deux traitements ne
peuvent jamais s'exécuter simultanément et créer des doublons.

**En cas d'échec de lecture d'une pointeuse**, les pointages **ne sont pas
perdus** : la date de dernière synchronisation n'est pas avancée, et les
pointages effectués pendant la panne sont récupérés au cycle suivant. L'incident
est enregistré dans le journal de synchronisation.

### 6.3 Calcul de la journée

- **Heures nettes** = (sortie − entrée) − durée de pause.
- Les pauses sont déduites des écarts entre pointages **supérieurs à 60 s**.
- La pause principale n'est retenue comme telle que si elle tombe dans la
  **fenêtre de pause** définie au shift.
- Les enregistrements sans session de travail réelle (ouverts, ou sortie =
  entrée) ne peuvent porter aucune pause.

### 6.4 Critères d'acceptation (paramétrés par shift)

Ces valeurs sont les **critères du processus** au sens du §8.1. Elles sont
paramétrées dans *Configuration des shifts* et **ne peuvent être modifiées que
par l'Administrateur SI, après approbation de la Direction**.

| Critère | Valeur par défaut | Effet |
|---|---|---|
| Tolérance de retard | **30 min** | Au-delà de « début + 30 min » → **Retard** |
| Délai d'absence matinale | **2 h 30** | Arrivée au-delà de « début + 2 h 30 » → **Absence matinale** |
| Durée maximale de pause | **1 h 15** | Pause plus longue → **Retard de pause** (s'il revient) ou **Absence après-midi** (s'il ne revient pas) |
| Grâce d'absence après-midi | **2 h** | Départ non suivi d'un retour dans les 2 h, shift encore en cours → **Absence après-midi** |
| Heures supplémentaires max | **2 h** | Au-delà de « fin de shift + 2 h » sans sortie → clôture en **sortie manquante** |
| Seuil de nuit | **06:00** | Pointage antérieur → rattaché à la veille |
| Antériorité de détection des absences | **7 jours** | Profondeur de scan des jours manquants |
| Tolérance de dérive d'horloge | **60 s** | Au-delà → pointeuse non conforme (§8) |

**Jours non travaillés :** aucune absence n'est générée le **dimanche**, ni le
**samedi** pour les shifts qui ne déclarent pas d'horaire samedi. Les **congés
validés** sont pris en compte : la journée est classée *Congé*, jamais *Absence*.

## 7. Classification des non-conformités (ISO 9001 §8.7.1)

Tout enregistrement de présence reçoit automatiquement un ou plusieurs statuts.
**Les statuts sont additifs et permanents** : une correction ultérieure peut en
ajouter, jamais en supprimer. L'historique de la non-conformité est donc
inaltérable.

| Statut | Signification | Traitement attendu |
|---|---|---|
| **À l'heure** | Conforme | Aucun |
| **Retard** | Arrivée au-delà de la tolérance | Justification auprès du manager |
| **Absence matinale** | Arrivée très tardive | Justification obligatoire |
| **Absence après-midi** | Départ sans retour, shift encore en cours | Justification obligatoire |
| **Absence** | Aucun pointage sur la journée | Justification obligatoire |
| **Congé** | Absence couverte par un congé validé | Aucun |
| **Retard de pause** | Pause au-delà de la durée maximale | Justification auprès du manager |
| **Missing Checkout** | Sortie non badgée | Régularisation RH |
| **Anomalie** | Enregistrement inexploitable (pointage unique en fin de shift, ou absence + sortie manquante) | Régularisation RH obligatoire |

## 8. Maîtrise de l'instrument de mesure (ISO 9001 §7.1.5)

La pointeuse est un **instrument de mesure** : son horloge détermine les retards
et les heures payées.

### 8.1 Identification

Chaque pointeuse est identifiée dans l'outil par : **nom, numéro de série,
modèle, emplacement, responsable désigné, état** (en service / en maintenance /
hors service).

### 8.2 Vérification

L'horloge est **comparée automatiquement à l'heure de référence du serveur à
chaque cycle de synchronisation** (toutes les 10 min). L'écart mesuré et
l'horodatage de la vérification sont conservés dans le journal.

### 8.3 Conduite à tenir en cas de dérive hors tolérance

Lorsque l'écart dépasse **60 secondes**, la pointeuse est déclarée
**non conforme** et l'Administrateur SI doit :

1. **Resynchroniser** l'horloge de la pointeuse (NTP) ;
2. **Déterminer depuis quand** la dérive existe, à partir de l'historique du
   journal de synchronisation ;
3. **Statuer sur la validité des enregistrements produits pendant cette période**
   *(exigence §7.1.5.2)* et, le cas échéant, les faire requalifier par le
   Gestionnaire RH ;
4. **Enregistrer** l'incident et l'action menée.

> Une pointeuse déclarée **hors service** n'est plus interrogée ; la génération
> des absences se poursuit pour son département.

### 8.4 Protection

L'accès physique aux pointeuses et l'accès aux paramètres de connexion sont
réservés à l'Administrateur SI.

## 9. Traitement des anomalies (ISO 9001 §8.7)

### 9.1 Circuit

1. Le Gestionnaire RH consulte quotidiennement les enregistrements en anomalie.
2. Il recueille la justification du salarié / du manager.
3. Il **coche « Résolut »** et renseigne **obligatoirement** :
   - le **motif** (catégorie imposée, voir §9.2) ;
   - un **commentaire** si le motif est « Autre » ;
   - l'outil enregistre automatiquement **qui** a résolu et **quand**.
4. S'il doit corriger une heure, l'outil ne l'y autorise **qu'après** cette
   déclaration.
5. Le statut est **recalculé automatiquement après correction** — la conformité
   est ainsi vérifiée *(exigence §8.7.1)*.

**Délai cible de traitement : 3 jours ouvrés.**

### 9.2 Motifs de résolution (catégories imposées)

| Motif | Usage |
|---|---|
| Oubli de badge | Le salarié était présent mais n'a pas badgé |
| Mission externe / déplacement | Absence physique justifiée par le travail |
| Panne ou indisponibilité de la pointeuse | Défaillance de l'instrument |
| Congé ou autorisation non saisi | Défaut de saisie en amont |
| Absence justifiée | Certificat médical, événement familial… |
| **Acceptation par dérogation** | L'enregistrement est accepté **en l'état**, sur décision explicite *(§8.7.1)* |
| Autre | **Commentaire obligatoire** |

> Le motif est une **catégorie fermée, et non un texte libre** : c'est ce qui
> rend possible l'analyse des causes récurrentes du §10.

### 9.3 Verrouillage avant paie

**Aucune période ne peut être transmise à la paie tant qu'elle comporte des
enregistrements en anomalie non traités** — chaque anomalie doit être soit
corrigée, soit acceptée par dérogation.

*(Point de vigilance : ce verrouillage n'est pas encore implémenté dans l'outil.
Il est à ce jour assuré par un **contrôle manuel du Gestionnaire RH** avant
clôture de période. Voir le plan d'amélioration en annexe B.)*

## 10. Surveillance, indicateurs et actions correctives (§9.1 / §10.2)

### 10.1 Preuves de surveillance conservées

Chaque cycle de synchronisation produit un enregistrement permanent dans le
**Journal de synchronisation** : pointages lus, badges vus, badges non rattachés,
écarts détectés par l'auto-contrôle, dérive d'horloge, erreurs.

Ce journal est **en lecture seule** pour les RH et les administrateurs du module :
la preuve ne peut pas être modifiée après coup *(§7.5.3.2)*.

### 10.2 Indicateurs

| Indicateur | Formule | Fréquence | Cible | Responsable |
|---|---|---|---|---|
| Taux d'absentéisme | Absences / jours travaillés théoriques | Mensuelle | *(à définir)* | RH |
| Taux de retard | Retards / pointages | Mensuelle | *(à définir)* | RH |
| **Taux de pointages non conformes** | (Sorties manquantes + Anomalies) / total | Mensuelle | *(à définir)* | RH |
| **Délai moyen de traitement des anomalies** | Moyenne (date de résolution − pointage) | Mensuelle | **≤ 3 jours** | RH |
| **Taux de disponibilité des pointeuses** | Syncs réussis / syncs tentés | Mensuelle | **≥ 99 %** | Admin SI |
| **Dérive d'horloge maximale** | Max de l'écart constaté par pointeuse | Mensuelle | **≤ 60 s** | Admin SI |
| Badges non rattachés | Nombre sur la période | Mensuelle | **0** | RH |

### 10.3 Revue mensuelle et actions correctives

Une **revue mensuelle** réunit le Gestionnaire RH et l'Administrateur SI. Elle :

1. examine les indicateurs ci-dessus ;
2. **analyse la répartition des motifs de résolution** pour identifier les causes
   dominantes ;
3. recherche si des non-conformités **similaires** existent ailleurs *(§10.2.1 b)* ;
4. décide, le cas échéant, d'une **action corrective sur la cause**, et non
   seulement sur le cas ;
5. **vérifie à M+1 l'efficacité** des actions décidées le mois précédent.

La revue fait l'objet d'un **compte rendu conservé**.

| Cause dominante observée | Action corrective type | Contrôle d'efficacité |
|---|---|---|
| Oubli de badge majoritaire | Sensibilisation, affichage, rappel managers | Baisse du taux d'oubli à M+1 |
| Panne pointeuse concentrée sur un site | Maintenance / remplacement du lecteur | Taux de disponibilité de la pointeuse |
| Congé non saisi récurrent | Correction du **processus de saisie des congés** en amont | Nombre d'absences requalifiées |
| Dérive d'horloge répétée | Synchronisation NTP, remplacement si récidive | Dérive maximale ≤ 60 s |

> **Distinction essentielle.** Corriger un pointage relève du §8.7. Éliminer la
> cause pour qu'il ne se reproduise plus relève du §10.2. Corriger indéfiniment
> les mêmes anomalies sans en traiter la cause est une non-conformité.

## 11. Informations documentées (ISO 9001 §7.5)

| Information | Nature | Support | Accès | Conservation |
|---|---|---|---|---|
| Enregistrements de présence | Preuve | Odoo `hr.attendance` | RH (lecture/écriture encadrée), Salarié (ses propres données) | *(à définir — cf. Code du travail et loi 09-08)* |
| Journal de synchronisation | Preuve | Odoo `attendance.device.sync.log` | RH & Admin : **lecture seule** | *(à définir)* |
| Fiche des pointeuses | Document | Odoo `attendance.device` | Admin SI | Durée de vie de l'équipement |
| Paramètres de shift | Document | Odoo `attendance.shift.config` | Admin SI (modification), tous (lecture) | Version courante |
| Comptes rendus de revue mensuelle | Preuve | *(à définir)* | RH, Direction | 3 ans |
| Sensibilisation au badgeage | Preuve | Feuille d'émargement | RH | 3 ans |

**Données personnelles.** Les enregistrements de présence sont des données à
caractère personnel. Leur accès est limité au strict nécessaire, et leur durée de
conservation doit être **définie et appliquée** (loi 09-08).

## 12. Risques et mesures de maîtrise (ISO 9001 §6.1)

| Risque | Conséquence | Mesure de maîtrise | Type |
|---|---|---|---|
| Dérive de l'horloge de la pointeuse | Retards et heures payées faux, non détectés | Vérification automatique à chaque sync + tolérance 60 s | Automatique |
| Pointeuse injoignable | Perte de pointages | Date de dernier sync non avancée ; récupération au cycle suivant | Automatique |
| Double passage de badge | Pause faussée | Fusion des pointages < 60 s | Automatique |
| Synchronisations concurrentes | Doublons d'enregistrements | Verrou applicatif + index unique + savepoints | Automatique |
| Oubli de badgeage | Journée inexploitable | Sensibilisation + traitement d'anomalie sous 3 j | Organisationnel |
| Badge non rattaché à un salarié | Pointage perdu | Signalement dans le journal, régularisation sous 24 h | Automatique + organisationnel |
| **Badge partagé entre deux salariés de départements différents** | **Pointage attribué au mauvais salarié** | **Aucune** — voir annexe B | ⚠️ **Non maîtrisé** |
| **Correction d'une heure sans trace** | **Perte de la valeur brute de la pointeuse** | **Aucune** — voir annexe B | ⚠️ **Non maîtrisé** |

## 13. Historique des révisions

| Version | Date | Objet | Rédacteur |
|---|---|---|---|
| 1.0 | *(à compléter)* | Création | *(à compléter)* |

---

## Annexe A — Correspondance avec les exigences ISO 9001:2015

| Clause | Exigence | Où la preuve se trouve |
|---|---|---|
| §5.3 | Rôles, responsabilités, autorités | §4 (matrice RACI), groupes de sécurité Odoo |
| §6.1 | Actions face aux risques | §12 |
| §7.1.5.1 | Ressources de surveillance adaptées et maintenues | §8.1, fiche pointeuse |
| §7.1.5.2 | Traçabilité de la mesure, vérification, conduite en cas de dérive | §8.2, §8.3, journal de synchronisation |
| §7.2 / §7.3 | Compétence et sensibilisation | §5, feuille d'émargement |
| §7.5.2 | Création, revue et approbation des documents | Cartouche d'approbation en tête |
| §7.5.3.2 | Accès, protection contre l'altération, conservation | §11, journal en lecture seule, statuts inaltérables |
| §8.1 / §8.5.1 | Critères définis du processus | §6.4 |
| §8.5.2 | Identification et traçabilité | §6.1 (badge), §7 (état de l'enregistrement) |
| §8.5.4 | Préservation des éléments de sortie | §6.2 (non-perte des pointages en cas de panne) |
| §8.7.1 | Maîtrise des éléments non conformes, vérification après correction | §7, §9.1 |
| §8.7.2 | Documentation de la NC, de l'action, de la dérogation, de l'autorité | §9.1, §9.2 |
| §9.1.1 | Quoi / comment / quand surveiller, preuves conservées | §6.2, §10.1 |
| §9.1.3 | Analyse et évaluation | §10.2, §10.3 |
| §10.2 | Non-conformité et action corrective | §10.3 |

## Annexe B — Écarts connus et plan d'amélioration

Ces écarts sont **identifiés et assumés**. Les déclarer est préférable à les
laisser découvrir en audit.

| N° | Écart | Clause | Mesure palliative actuelle | Action prévue | Échéance |
|---|---|---|---|---|---|
| 1 | La correction manuelle d'une heure **écrase la valeur brute** de la pointeuse, sans trace ni historique | §7.5.3.2 | Le RH doit déclarer son intervention (« Résolut » + motif) avant toute modification | Conserver l'heure d'origine dans un champ immuable + historique des modifications (chatter) | *(à définir)* |
| 2 | Le numéro de badge n'est unique **que par département** | §8.5.2 | Chaque pointeuse est filtrée sur son département | Rendre l'unicité **globale** | *(à définir)* |
| 3 | Aucun **verrouillage de période** : rien n'empêche techniquement une anomalie de partir en paie | §8.7.1 | Contrôle manuel du RH avant clôture (§9.3) | Verrou de clôture bloquant | *(à définir)* |
| 4 | Les modifications des **seuils de shift** ne sont pas historisées | §7.5.2 | Accès restreint à l'Administrateur SI | Historisation des modifications | *(à définir)* |
| 5 | **Durée de conservation** des données non définie | §7.5.3.2 | — | Définir et appliquer une politique de rétention | *(à définir)* |
| 6 | Les enregistrements résolus **avant** la version 1.0 n'ont pas de motif | §8.7.2 | — | Requalification ou acceptation documentée du stock existant | *(à définir)* |
