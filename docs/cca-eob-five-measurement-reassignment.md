# Runbook : réattribution CCA-EOB de cinq mesures 2026

Statut : implémenté et testé localement, **non appliqué, non commité**. Aucune autorisation de production n'est donnée par ce document.

## Périmètre

Organisation 1. Boîte source 1837 `CCA-EOB-2.006` (inactive), boîte cible 1835 `CCA-EOB-2.004` (active), souche 50, zone thermique 3.
Mesures exactes : 104358 (2026-10-07, W41, 50/0), 104529 (2026-10-14, W42, 40/0), 104689 (2026-10-21, W43, 69/0), 104849 (2026-10-28, W44, 75/0), 105036 (2026-11-04, W45, 75/0). Séquences 23, 24, 25, 66, 67. Seul `box_id` change, avec les révisions de boîte (`+5` chacune, au moins égales à la plus grande séquence portée) et les audits.

Décision : le chercheur a confirmé que ces cinq mesures appartiennent à `CCA-EOB-2.004`. Pour 104689, cette correction supersède **l'attribution de boîte seule** retenue par l'ancien contrat additif CCA (`config/cca_eob_additive.py`, documents `cca-eob-v2-additive-transition-runtime.md`, `cca-eob-additive-baseline-review.md`). L'ancien contrat, ses audits et ses documents ne sont pas modifiés; chaque audit et le reçu le référencent (`prior_contract`).

## Preuves requises avant approbation

1. Confirmation écrite du chercheur (référence à citer dans `--reason`).
2. Dry-run de production en lecture seule, `ready=true`, sans blocage, revu indépendamment.
3. Sauvegarde `pg_dump` vérifiée et restaurable, horodatée juste avant l'application.
4. Autorisation de déploiement explicite de la personne responsable (AGENTS.md : toute mutation de production l'exige).

## Privilèges

Compte Django actif, administrateur actif de l'organisation 1 (`--actor`). Rôle de base de données : le rôle applicatif ordinaire (UPDATE sur mesures, boîtes, INSERT sur audit), **pas** de rôle superuser. Les triggers ne sont jamais désactivés. La session doit être en `READ COMMITTED` (défaut Django); sinon l'opération s'arrête.

## Dry-run (défaut)

```text
uv run python manage.py reassign_cca_eob_2026_measurements
```

Sortie JSON `{plan_hash, plan}`. Elle ne modifie rien. Si `ready=false`, le message liste chaque blocage (collision de semaine, température historique liée, doublon entrant, dépendance de repiquage/lignée/transfert, différence de zone ou de température résolue, garde SQL absent, rejeu).

## Application

Après approbation, relancer le dry-run, vérifier que `plan_hash` est celui approuvé, puis :

```text
uv run python manage.py reassign_cca_eob_2026_measurements --apply --actor <username> --reason "<confirmation chercheur + référence>" --expected-count 5 --expected-plan-hash <hash approuvé>
```

Tout est recalculé sous verrous (boîtes, organisation, compte, mouvements, périodes, zone, mesures). Un écart d'empreinte, de compte, de raison ou de droits annule tout. Un rejeu est refusé (reçu existant et mesures déjà déplacées).

## Postconditions à contrôler

- Les cinq mesures sont sur 1835; aucune autre mesure, année 2025 incluse, n'a changé.
- Comptes, zéros, NULL, précision, auteurs, import, `created_at`, séquences inchangés.
- Révisions : 1835 et 1837 augmentées comme dans le plan; statuts, emplacements, mouvements, zones inchangés.
- Cinq audits `object_type=box` + un reçu `historical_correction / cca_eob_2026_five_measurement_reassignment_v1`.
- Nouveau dry-run : refus de rejeu. Export des semaines 41 à 45 sur 1835 cohérent avec la zone 3.
- Effet attendu et non bloquant : l'état courant de polypes de 1835 est recalculé selon la chronologie (la mesure la plus récente peut changer). Les alertes existantes ne sont pas régénérées.

## Hors périmètre

Unification PCA, nouvelles mesures, persistance par semaine seule, reconfirmation thermique, corrections de cycle de vie ou d'emplacement, API générique, toute opération de base de production.
