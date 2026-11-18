# Transfer v2 — Phase 6, jalon 2 : proposition de don de Box entière

État : backend uniquement, **non revu**, sans UI, sans acceptation. Une
proposition n'est pas un transfert. Fondations : [`TRANSFER_V2_BOX_IDENTITY.md`](TRANSFER_V2_BOX_IDENTITY.md).

## Ce que fait une proposition (et ce qu'elle ne fait pas)

Un Admin actif de l'institution source propose le don d'une Box entière à une
autre institution enregistrée dans POLYPBASE, après avoir confirmé avoir vérifié
l'historique de la Box.

Aucun effet sur : propriété (`BoxOwnershipEpisode` reste courant côté source),
`Box.organization`, statut, emplacement, relevés, mouvements, lignée, stock,
`TransferEnvelope`/`TransferItem`, Box destination. La source garde le contrôle
opérationnel : relever, déplacer, corriger, repiquer restent possibles. La
destination n'obtient **aucun** accès à l'historique scientifique source.

## Persistance

`BoxDonationProposal` (`apps/cultures/models.py`, migration
`0020_box_donation_proposal`, additive, retour arrière refusé si une
proposition existe).

| Champ | Sens |
|---|---|
| `proposal_id` | UUID public de la proposition |
| `source_organization`, `destination_organization` | institutions (FK `PROTECT`, distinctes en base) |
| `source_box`, `identity`, `source_episode`, `source_ownership_sequence` | Box locale, `GlobalBoxIdentity`, épisode courant au moment de l'émission |
| `source_institution_name`, `destination_institution_name`, `source_box_code`, `source_strain_code`, `species_scientific_name`, `global_strain_id` | instantanés figés |
| `history_verified_by`, `history_verified_at` | acteur et date de la confirmation |
| `history_snapshot_version`, `history_sha256` | référence d'intégrité de l'historique vérifié |
| `source_polyp_state_revision` | compteur `Box.polyp_state_revision` à l'émission (informatif) |
| `state` (`pending`, `cancelled`), `revision` | cycle de vie et contrôle de fraîcheur |
| `created_by`, `created_at`, `request_id`, `request_fingerprint` | auteur et idempotence |
| `cancelled_by`, `cancelled_at` | annulation |

Contraintes : `box_donation_single_pending` (au plus une proposition `pending`
par identité biologique), `box_donation_request_unique`
(`source_organization, created_by, request_id`),
`box_donation_distinct_institutions`, `box_donation_positive_counters`,
`box_donation_coherent_state`. `save()` n'autorise que la transition
`pending -> cancelled` (révision + 1); `update()`/`delete()` sont refusés, y
compris via les managers liés et `bulk_update()` (Django 5.2 le termine par
`QuerySet.update()` sur la même classe protégée). `_base_manager`, `bulk_create()`
et le SQL direct restent hors garantie, comme pour les épisodes de propriété.

Pourquoi pas `TransferEnvelope`/`TransferItem` : `TransferItem` impose une
`declared_polyp_quantity` et les protocoles 2.0/2.1 n'ont pas de sémantique
« Box entière ». En créer un maintenant inventerait une quantité ou modifierait
silencieusement le protocole. `BoxTransfer` (v1, nombre de polypes, `CASCADE`)
n'est pas réutilisé non plus. La provenance Transfer v2 sera créée à
l'acceptation, avec une extension de protocole approuvée.

## API (session, `X-Organization-Id` obligatoire, Admin actif, `no-store`)

| Méthode | URL | Rôle |
|---|---|---|
| POST | `/api/box-donations/preflight/` | éligibilité + référence d'historique, sans écriture |
| GET | `/api/box-donations/outgoing/` | propositions émises par l'institution active |
| POST | `/api/box-donations/outgoing/` | émission (201, ou 200 pour un rejeu identique) |
| GET | `/api/box-donations/incoming/` | propositions reçues par l'institution active (résumé) |
| GET | `/api/box-donations/<proposal_id>/` | détail source (complet) ou destination (résumé) |
| POST | `/api/box-donations/<proposal_id>/cancel/` | annulation par la source (`expected_revision`) |

Corps de préflight : `source_box_id` (entier), `destination_institution_id`
(UUID `Organization.portable_id`). Création : idem + `request_id` (UUID),
`history_verified` (`true` JSON strict), `history_sha256` (renvoyé par le
préflight). Types JSON exacts, champs inconnus refusés. Erreurs
`{detail, code}` : `donation_admin_required` (403), `box_not_found`,
`destination_not_found`, `proposal_not_found` (404), `destination_is_source`,
`invalid_request`, `history_verification_required` (400),
`box_status_not_eligible`, `box_identity_provenance_unresolved`,
`strain_global_identity_required`, `box_not_current_owner`,
`box_not_projectable`, `donation_already_pending`, `source_history_changed`,
`proposal_request_conflict`, `proposal_not_pending`, `stale_proposal_revision`,
`proposal_conflict` (409). Pas de texte i18n côté backend : le futur frontend
traduit les codes.

La vue destination ne contient ni comptes source, ni identifiant local de Box,
ni empreinte, ni relevé. Aucun endpoint d'acceptation.

## Autorité et isolation

- Membership Admin actif dans l'institution active, sans raccourci
  superutilisateur (acte institutionnel sortant, comme le partage).
- Écritures : verrous `Organization -> OrganizationMembership -> User -> Box
  (-> GlobalBoxIdentity) -> BoxDonationProposal`; l'autorité est relue sous
  verrou, une rétrogradation concurrente est donc observée.
- Box rechargée dans l'institution source, hors alias fusionné : étrangère,
  absente et fusionnée sont indistinguables (404).
- Listes filtrées en base par `source_organization` ou
  `destination_organization`; le détail n'est trouvé que pour l'une des deux.
- Audit uniquement dans l'institution source (pas d'écriture dans l'audit de
  la destination, qui exposerait l'identité d'un compte d'une autre institution).

## Destination

Désignée par `Organization.portable_id`, existante, active et distincte de la
source, vérifiée côté serveur. Jamais un e-mail. Seul son nom est renvoyé; ni
contact, ni membres. **Il n'existe pas d'annuaire d'institutions autorisé** :
aucun endpoint ne liste les institutions ni n'expose `portable_id`. La façon
dont la source obtient l'identifiant de la destination est une politique
d'annuaire à décider (voir plus bas).

## Vérification de l'historique

La référence est le SHA-256 du `canonical_json` du snapshot scientifique
versionné existant (`polypbase.scientific-snapshot` 1.0, via
`project_verified_source_box_snapshot`), avec tous les sélecteurs sauf le nom
et l'identifiant portable de l'institution source. Elle couvre les relevés
canoniques (temps, valeurs dont `0` distinct de `null`, provenance), le code de
Box et l'identité de souche. Elle ne couvre pas emplacements, mouvements,
lignée, notes, comptes ni températures de zone.

1. Le préflight calcule la référence (lecture seule).
2. La création recalcule sous verrou de Box (que prennent les écrivains de
   relevés) et refuse si elle diffère (`source_history_changed`) : la
   confirmation ne vaut que pour l'état revu.
3. Toute modification ultérieure du snapshot (relevé ajouté, corrigé ou
   supprimé, code, identité de souche) rend la vérification périmée sans geler
   la Box. Le détail source expose `history_verification.current` (observation
   non verrouillée). L'acceptation future devra recalculer sous verrou et
   exiger une nouvelle vérification si elle diffère.

La confirmation n'atteste pas l'exactitude scientifique des mesures.

## Enrôlement de l'identité

`enroll_origin_box_as_admin` (`box_ownership.py`) : appelé seulement dans la
transaction de création, après autorité verrouillée; revérifie le membership
Admin; réutilise l'identité existante (aucune nouvelle allocation, pas
d'audit); sinon enrôle et écrit l'audit `global_box_identity` (`creation`).
Le caractère « nouveau » est déterminé par `_enroll_origin_box` sous le verrou
de la Box, jamais par une lecture préalable : deux appels concurrents
produisent une seule identité et un seul audit d'enrôlement.
Les refus de `enroll_origin_box` (alias fusionné, import v1, autre
institution) et la protection contre la fusion PCA sont inchangés : une Box
proposée a un épisode et ne peut plus devenir un alias.

## Idempotence et concurrence

- Clé `(institution source, acteur, request_id)` + empreinte normalisée du
  corps : rejeu identique -> même proposition (quel que soit son état), corps
  différent -> 409. L'autorité est revérifiée avant tout rejeu.
- Deux propositions concurrentes pour une Box : sérialisées par le verrou de
  Box, la seconde reçoit `donation_already_pending`; l'index partiel est le
  filet de sécurité en base.
- Annulation : verrou de la proposition, `expected_revision`; une seule
  annulation concurrente réussit.
- Enrôlement, proposition et audits sont dans une seule transaction (READ
  COMMITTED, sans changement de niveau d'isolation) : un échec d'audit annule
  tout.

## Données à préserver pour les décisions futures du receveur

Sans créer d'autorisation : identité biologique (`identity`), épisode et
séquence source, institutions source et destination, référence d'historique
vérifiée. La chaîne d'épisodes permet de retrouver les institutions
productrices antérieures. Les options futures (suivre les observations
futures de la Box, puis sa descendance récursive, inclure les institutions
précédentes) ne sont pas implémentées et ne sont pas stockées.

## Décisions ouvertes

- Éligibilité des Boxes `inactive` et `pending_review` (refusées
  provisoirement, `ELIGIBLE_BOX_STATUSES`).
- Politique d'annuaire : comment une institution découvre l'identifiant
  d'une autre, et si les institutions inactives ou sans Admin sont proposables.
- Divulgation à la destination avant acceptation (aucune aujourd'hui).
- Expiration, relance, refus par la destination, notification.
- Les modifications hors snapshot (emplacement, lignée, notes) doivent-elles
  aussi invalider la vérification ?
- Extension de protocole « Box entière » (2.2) et création de la provenance.

## Prérequis de l'acceptation

1. Décision et implémentation de la divulgation pré-acceptation.
2. Extension de protocole approuvée (Box entière, `global_box_id`,
   `ownership_sequence`), sans quantité inventée.
3. Dans une transaction, sous verrou de l'identité puis de la proposition :
   autorité Admin destination, proposition `pending` à la révision attendue,
   épisode source toujours courant et égal à `source_episode`, référence
   d'historique recalculée identique, Box source toujours éligible.
4. Choix de l'emplacement local destination, allocation du code, Box locale
   sans relevé fabriqué, fermeture/ouverture d'épisode, arête portable
   `transfer`, audits des deux côtés, état terminal de la proposition (à
   ajouter avec sa contrainte).
5. Décisions sur le statut de la Box source après don et sa lecture seule.

## Tests

- `apps.cultures.test_box_donation` (SQLite) : autorité, isolation, contexte,
  destination, éligibilité, vérification/périmé, idempotence, conflits,
  annulation, échecs d'audit, gardes du modèle.
- `apps.cultures.test_box_donation_migrations` : migration additive et garde
  de retour arrière.
- `apps.cultures.test_box_donation_postgres` : concurrence réelle
  (propositions concurrentes, requêtes identiques, annulations, rétrogradation
  et relevé concurrents) sur un cluster PostgreSQL jetable vide
  (`config.sharing_pgqa_settings`, schéma issu des modèles); ignoré sous SQLite.
