# Tests automatisés — backend POLYPBASE

Récapitulatif lisible des **139 tests** de la suite Django, avec ce que chacun
vérifie. Les tests couvrent l'API REST, les permissions par rôle et par
organisation, le cycle de vie des boîtes (relevés, repiquage, déplacement,
lignée), les QR codes, la gestion des comptes, les exports, les écrans
d'administration (armoires, sondes, structures, transferts) et la salinité.

- **Fichiers source** : `backend/apps/<app>/tests*.py`
- **Lancer toute la suite** : `python manage.py test` (depuis `backend/`)
- **CI** : la suite tourne automatiquement sur chaque push/PR
  (`.github/workflows/ci.yml`), avec `makemigrations --check` pour bloquer une
  migration oubliée.
- **Note base de données** : sans `POSTGRES_DB`, `settings.py` bascule sur
  SQLite — c'est ce que fait la CI (aucun service Postgres requis). En local,
  si l'utilisateur PostgreSQL n'a pas le droit `CREATEDB`, faire pareil.

| Domaine | Fichier | Nombre |
|---|---|---|
| Cultures (boîtes, relevés, lignée, QR) | `apps/cultures/tests.py` | 46 |
| Administration (armoires, sondes, structures, transferts) | `apps/cultures/tests_admin_api.py` | 27 |
| Comptes (auth, rôles, membres) | `apps/accounts/tests.py` | 37 |
| Audit et relevés en lot | `apps/accounts/tests_audit_measurement.py` | 10 |
| Relevés (édition + salinité) | `apps/measurements/tests.py` | 8 |
| Exports (CSV) | `apps/exports/tests.py` | 6 |
| Référentiel taxonomique | `apps/taxonomy/tests.py` | 5 |
| **Total** | | **139** |

### Modules ciblés ajoutés depuis cet inventaire historique

Ces modules spécialisés ne sont pas inclus dans le tableau de comptage ci-dessus :

| Domaine | Fichier | Portée |
|---|---|---|
| Propriété des Strain et import transfer v1 | `apps/cultures/test_transfer_strain_ownership.py` | propriété destination, conflits, atomicité, replay et périmètre d'organisation. |
| Concurrence transfer v1 | `apps/cultures/test_transfer_v1_concurrency.py` | rejeu concurrent et contrainte d'unicité; conclusions de verrouillage uniquement sur PostgreSQL. |
| Identité Species/local Strain | `apps/taxonomy/test_strain_species_concurrency.py` | cohérence PATCH/LocalStrainIdentity et contention réelle des verrous PostgreSQL. |

---

## Contrat courant : Alertes abandonnées

Les Alertes ne sont plus une fonctionnalité active. Les tests qui attendaient
leur génération sur une baisse de polypes ou un écart de température, leur
synchronisation ou leur résolution automatique/manuelle décrivent un ancien
comportement, pas un contrat à préserver. Les nombres et listes ci-dessous ne
constituent pas une attestation de validation de cette décision.

Le contrat de non-régression couvre :

- des mesures et agrégats factuels, sans avertissement opérationnel ou biologique
  inféré, en conservant la distinction entre `0`, `null` et absence de relevé;
- aucune création, mise à jour ou résolution d'`Alert` lors des créations et
  corrections de relevés, des saisies de température ou des autres mutations
  métier;
- aucun parcours API actif de résolution d'alerte ni exposition d'alertes actives
  dans les réponses de fiches boîtes ou d'emplacements;
- les permissions, le cloisonnement par organisation, les contraintes et la
  concurrence, sans dépendance à une synchronisation d'alertes;
- le rollback de la mesure ou de l'agrégat si l'écriture `AuditLog` échoue;
- la préservation d'Actions, des entrées `AuditLog` historiques et de leur
  présentation sûre, y compris celles concernant les anciennes alertes;
- la conservation dormante du modèle, de la table, des données et des migrations
  `Alert`, sans suppression ni réécriture liée à l'abandon produit.

Les tests de présentation d'une ancienne action liée à une alerte utilisent une
entrée d'audit historique, sans dépendre d'un endpoint de résolution actif.

| Fichier | Test | Vérifie que… |
|---|---|---|
| `apps/cultures/tests.py` | `test_polyp_drop_zero_and_recovery_leave_historical_alerts_unchanged` | les comptages restent factuels et audités, sans création ni modification des anciennes alertes. |
| `apps/cultures/tests.py` | `test_alerts_are_not_exposed_in_dashboard_or_box_payloads` | le dashboard et les réponses de boîtes n'exposent plus d'alertes actives ni de compteurs d'alertes. |
| `apps/cultures/tests.py` | `test_alert_resolution_route_and_admin_are_removed` | la route de résolution et l'administration Django d'`Alert` sont retirées, tandis qu'`AuditLog` et les données historiques sont préservés. |
| `apps/cultures/tests.py` | `test_manual_temperature_deviation_zero_and_recovery_do_not_sync_alerts` | les températures, y compris zéro, alimentent les agrégats et l'audit sans modifier les anciennes alertes. |
| `apps/cultures/tests.py` | `test_demo_seed_preserves_dormant_alerts` | le chargement répété des données de démonstration laisse le stockage dormant inchangé. |
| `apps/measurements/tests.py` | `test_measurement_corrections_to_zero_and_recovery_do_not_sync_alerts` | les corrections de comptages ne synchronisent pas d'alertes. |
| `apps/measurements/tests.py` | `test_measurement_rolls_back_when_audit_fails` / `test_measurement_update_rolls_back_when_audit_fails` | la création ou la correction du relevé est annulée si l'audit échoue. |
| `apps/accounts/tests_actions_api.py` | `test_personal_alert_actions_expose_no_database_identifier` | une ancienne entrée d'audit d'alerte reste lisible dans Actions sans exposer son identifiant technique. |

Ces descriptions ne constituent pas un résultat d'exécution des tests. Voir
[`context/measurements-integrity.md`](context/measurements-integrity.md) et
[`context/product-architecture.md`](context/product-architecture.md).

## 1. Cultures — `apps/cultures/tests.py` (46 tests)

### Accès & périmètre (scoping)

| Test | Vérifie que… |
|---|---|
| `test_health_endpoint_is_public` | l'endpoint `/health` répond sans authentification. |
| `test_legacy_french_api_routes_are_removed` | les anciennes routes API françaises ont bien été supprimées (404). |
| `test_drf_box_list_is_paginated_and_scoped` | la liste des boîtes est paginée et limitée aux organisations de l'utilisateur. |
| `test_drf_box_list_allows_read_only_users_to_consult_their_organization` | un compte en lecture seule peut consulter les boîtes de son organisation. |
| `test_box_accesses_are_saved_for_the_current_account_only` | l'enregistrement d'un accès à une boîte est propre au compte courant. |

### Fiche boîte & relevés

| Test | Vérifie que… |
|---|---|
| `test_drf_box_detail_returns_measurement_history` | la fiche renvoie l'historique des relevés de la boîte. |
| `test_drf_measurement_endpoint_creates_a_measurement` | l'API crée bien un relevé biologique. |
| `test_drf_measurement_endpoint_blocks_read_only_users` | un lecteur seul ne peut pas créer de relevé (403). |
| `test_drf_thermal_zones_include_probes_and_latest_readings` | les zones thermiques renvoient leurs sondes et les dernières mesures (température/salinité). |
| `test_drf_profile_endpoint_updates_interface_language` | le profil met à jour la langue d'interface. |

### Lignée (parenté)

| Test | Vérifie que… |
|---|---|
| `test_drf_box_detail_returns_parent_and_child_lineage` | la fiche renvoie la lignée parents et enfants. |
| `test_drf_box_detail_hides_lineage_from_another_organization` | la lignée appartenant à une autre organisation est masquée. |
| `test_drf_lineage_graph_returns_all_accessible_generations` | le graphe de lignée renvoie toutes les générations accessibles. |
| `test_drf_lineage_graph_excludes_other_organizations` | le graphe exclut les boîtes d'autres organisations. |

### Repiquage (subculture)

| Test | Vérifie que… |
|---|---|
| `test_drf_subculture_endpoint_creates_multiple_child_boxes` | un repiquage crée plusieurs boîtes filles. |
| `test_drf_subculture_endpoint_blocks_read_only_users` | un lecteur seul ne peut pas repiquer (403). |
| `test_drf_subculture_endpoint_allows_organization_admins` | un administrateur d'organisation peut repiquer. |
| `test_drf_subculture_endpoint_rejects_a_zone_from_another_organization` | le repiquage refuse une zone d'une autre organisation. |
| `test_subculture_transaction_rolls_back_if_lineage_creation_fails` | le repiquage est transactionnel : rollback complet si la création de lignée échoue. |

### Déplacement (move)

| Test | Vérifie que… |
|---|---|
| `test_drf_move_endpoint_moves_box_and_keeps_location_history` | le déplacement change la zone et conserve l'historique des emplacements. |
| `test_drf_move_endpoint_blocks_read_only_users` | un lecteur seul ne peut pas déplacer (403). |
| `test_drf_move_endpoint_rejects_zone_from_another_organization` | le déplacement refuse une zone d'une autre organisation. |

### QR codes & scan

| Test | Vérifie que… |
|---|---|
| `test_box_detail_api_exposes_qr_urls` | la fiche expose les URLs QR (scan + image). |
| `test_scan_redirects_to_detail_and_logs_scan` | un scan QR redirige vers la fiche et journalise le scan. |
| `test_scan_requires_login` | le scan nécessite d'être connecté. |
| `test_scan_is_scoped_to_authorized_boxes` | le scan est limité aux boîtes autorisées. |
| `test_qr_endpoint_returns_svg_for_the_current_public_app_address` | l'endpoint QR renvoie un SVG pointant vers l'adresse publique de l'app. |
| `test_qr_endpoint_is_scoped_to_authorized_boxes` | l'endpoint QR est limité aux boîtes autorisées. |

---

## 2. Comptes — `apps/accounts/tests.py` (37 tests)

### Préférences & session

| Test | Vérifie que… |
|---|---|
| `test_account_settings_defaults_to_french` | la langue par défaut d'un compte est le français. |
| `test_account_settings_updates_interface_language` | la langue d'interface se met à jour. |
| `test_legacy_account_preferences_api_is_removed` | l'ancienne API de préférences a été supprimée. |
| `test_session_login_sets_an_authenticated_session` | la connexion crée une session authentifiée. |
| `test_session_login_rejects_invalid_credentials` | des identifiants invalides sont rejetés. |
| `test_session_logout_clears_the_current_session` | la déconnexion vide la session. |

### Gestion des membres (rôles & organisations)

| Test | Vérifie que… |
|---|---|
| `test_admin_lists_only_managed_org_members` | un admin ne voit que les membres des organisations qu'il administre. |
| `test_viewer_cannot_access_member_management` | un lecteur ne peut pas accéder à la gestion des membres. |
| `test_admin_creates_new_member` | un admin crée un nouveau membre. |
| `test_admin_create_requires_email_for_invitation` | une adresse email est requise pour envoyer le lien d’activation. |
| `test_admin_cannot_create_in_unmanaged_org` | un admin ne peut pas créer un membre dans une organisation qu'il ne gère pas. |
| `test_admin_changes_member_role` | un admin peut modifier le rôle d'un membre. |
| `test_admin_cannot_change_own_role` | un admin ne peut pas modifier son propre rôle. |

---

## 3. Administration — `apps/cultures/tests_admin_api.py` (27 tests)

Endpoints de création réservés aux administrateurs. Le fil rouge : **qui a le
droit de créer quoi**, et **on ne peut jamais atteindre une autre structure**.

### Armoires thermiques

| Test | Vérifie que… |
|---|---|
| `test_admin_creates_a_thermal_zone` | un admin crée une armoire dans sa structure. |
| `test_lab_technician_cannot_create_a_thermal_zone` | un technicien ne peut pas (403). |
| `test_admin_cannot_create_a_zone_in_another_organization` | un admin ne peut pas créer dans une structure qu'il n'administre pas (403). |
| `test_duplicate_zone_name_in_the_same_organization_is_rejected` | deux armoires ne peuvent pas porter le même nom dans une structure (400). |

### Sondes

| Test | Vérifie que… |
|---|---|
| `test_admin_creates_a_probe_inheriting_the_zone_organization` | la sonde **hérite** de la structure de son armoire (jamais envoyée par le client). |
| `test_admin_cannot_add_a_probe_to_another_organization_zone` | impossible d'ajouter une sonde à l'armoire d'une autre structure (403). |
| `test_duplicate_probe_code_in_the_same_organization_is_rejected` | code de sonde unique par structure (400). |

### Structures

| Test | Vérifie que… |
|---|---|
| `test_superuser_creates_an_organization` | seul un super-administrateur crée une structure. |
| `test_organization_admin_cannot_create_an_organization` | même un admin de structure ne peut pas (403). |
| `test_duplicate_organization_name_is_rejected_case_insensitively` | nom de structure unique, casse ignorée (400). |

### Transferts de boîtes

| Test | Vérifie que… |
|---|---|
| `test_admin_records_a_planned_transfer_without_reassigning_the_box` | le transfert **enregistre l'intention** (statut « Prévu ») **sans changer le propriétaire** de la boîte. |
| `test_transfer_to_the_same_organization_is_rejected` | on ne transfère pas vers sa propre structure (400). |
| `test_lab_technician_cannot_transfer_a_box` | un technicien ne peut pas transférer (403). |

---

## 4. Relevés — `apps/measurements/tests.py` (8 tests)

Édition d'un relevé existant (action « Modifier ») et **salinité par boîte**.

| Test | Vérifie que… |
|---|---|
| `test_measurement_can_be_created_with_a_salinity` | un relevé accepte une salinité (PSU). |
| `test_technician_updates_a_measurement_and_untouched_fields_are_kept` | le PATCH est **partiel** : un champ non envoyé n'est pas écrasé. |
| `test_read_only_user_cannot_update_a_measurement` | un lecteur seul ne peut pas modifier (403). |
| `test_a_measurement_cannot_be_updated_through_another_box` | on ne modifie pas le relevé d'une boîte via une autre (404). |
| `test_updating_a_measurement_of_another_organization_is_refused` | cloisonnement entre structures (404). |
| `test_latest_salinity_survives_a_newer_measurement_without_salinity` | **régression signalée** : la salinité affichée ne disparaît plus quand un relevé plus récent n'en contient pas. |
| `test_box_list_also_exposes_the_last_recorded_salinity` | idem côté liste des boîtes (via annotation, sans N+1). |
| `test_box_without_any_salinity_reports_none` | une boîte sans aucune salinité renvoie bien `null`. |

---

## 5. Exports — `apps/exports/tests.py` (6 tests)

| Test | Vérifie que… |
|---|---|
| `test_export_options_are_scoped_to_accessible_organizations` | les options d'export sont limitées aux organisations accessibles. |
| `test_weekly_csv_matches_the_historical_wide_structure` | le CSV hebdomadaire respecte la structure historique (format « large »). |
| `test_preview_returns_aggregated_values_without_recording_an_export` | l'aperçu renvoie des valeurs agrégées sans enregistrer d'export. |
| `test_csv_filters_are_cumulative` | les filtres du CSV se cumulent (espèce + souche + zone…). |
| `test_csv_rejects_an_unauthorized_organization` | l'export CSV refuse une organisation non autorisée. |

---

## Couverture — points forts

- **Sécurité multi-organisations** : la quasi-totalité des endpoints est testée
  pour le cloisonnement entre organisations (une organisation ne voit jamais les
  données d'une autre).
- **Permissions par rôle** : lecture seule vs technicien vs administrateur
  (création de relevés, repiquage, déplacement, gestion des comptes).
- **Intégrité transactionnelle** : le repiquage est vérifié pour son rollback en
  cas d'échec partiel.
- **Traçabilité** : scans QR et accès aux boîtes sont journalisés et testés.
- **Non-régression** : les bugs signalés par les utilisateurs sont figés en test
  (ex. la salinité qui disparaissait après un relevé sans salinité).

> Les modèles d'audit et les organisations restent couverts depuis les suites
> comptes et administration. Le référentiel taxonomique dispose de sa propre
> suite dédiée.
