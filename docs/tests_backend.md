# Tests automatisés — backend Polypbase

Les modules de test et les workflows versionnés sont la source de vérité. Le
nombre de fichiers ou de fonctions `test_*` n’est pas un décompte de la suite
exécutée : découverte, prérequis, conditions d’ignorance et settings varient.

## Découverte et CI

Depuis `backend/`, Django découvre `test*.py` dans ses packages Python :
`tests.py`, `tests_*.py` et `test_*.py`, notamment sous `apps/` **et** `config/`.
Pour repérer les modules sans importer Django ni lire son environnement, depuis
la racine :

```sh
git ls-files 'backend/apps/**/test*.py' 'backend/config/**/test*.py'
```

Pour une modification ciblée, passer le label du module, de la classe ou de la
méthode à `manage.py test`. Exemple depuis `backend/`, dans la [session QA
isolée](context/development-deployment.md#backend-isolé) :

```sh
uv run --frozen python manage.py test apps.measurements.test_probe_collection --settings=config.test_settings
```

Le job **Backend checks** de [CI](../.github/workflows/ci.yml), sur push/PR vers
`main` ou déclenchement manuel, utilise `uv run --frozen python manage.py test
--settings=config.test_settings` **sans labels**, depuis `backend/`. Il fixe
`PYTHON_DOTENV_DISABLED=1`, `POLYPBASE_TEST_POSTGRES=0`, désactive la livraison de
courriel et utilise un backend mail en mémoire. Il contrôle aussi la configuration
et la dérive des migrations sous `config.preflight_settings`, et vérifie les
réglages de production avec `check --deploy` dans son environnement CI isolé.
Les contrôles d’outillage de traduction utilisent des commandes distinctes.

Le runner PowerShell local [mis à jour par PR1](../.agents/skills/polypbase-qa/SKILL.md)
ne remplace pas cette découverte : il sélectionne huit apps (dont `apps.sharing`)
et `config.test_api_defaults`, pas tous les modules `config.test_*`. Le préflight
de déploiement est encore une autre sélection. Commencer par les tests ciblés ;
ne pas lancer une suite large en dehors d’un environnement local vérifié.

### PostgreSQL et autres suites séparées

`config.test_settings` utilise SQLite en mémoire avec `POLYPBASE_TEST_POSTGRES=0` ;
`1` sélectionne l’endpoint QA local fixe. Ce choix concerne la connexion Django,
pas les prérequis propres à chaque test. Écarter les opt-ins de harnais hérités
et ne jamais tester sur Neon, une base partagée ou la production. Pour éprouver
verrous, contraintes ou rôles PostgreSQL, utiliser le harnais jetable prévu.

- [`ci.yml`](../.github/workflows/ci.yml) : job PostgreSQL 18 distinct pour Box 1902
  et la régression de retrait de la résolution d’espèce.
- [`historical-movements-postgres.yml`](../.github/workflows/historical-movements-postgres.yml)
  et [`history-revision-postgres.yml`](../.github/workflows/history-revision-postgres.yml) :
  contrats PostgreSQL des mouvements et révisions historiques.
- [`pca-unix.yml`](../.github/workflows/pca-unix.yml) : procédures et contrôles PCA sous Unix.
- `deploy/tests/` et les tests d’outillage sous `scripts/` sont hors de la
  découverte Django depuis `backend/`. Leurs commandes et prérequis sont dans
  les workflows ou le [guide de déploiement](deploiement_vm.md).

## Points d’entrée par domaine

| Domaine | Labels Django (exemples, non exhaustifs) |
|---|---|
| Comptes, organisations et audit | `apps.accounts.tests`, `apps.accounts.tests_audit_measurement`, `apps.accounts.tests_actions_api`, `apps.audit.tests` |
| Boîtes, administration et inventaire | `apps.cultures.tests`, `apps.cultures.tests_admin_api`, `apps.cultures.test_box_inventory_api` |
| Repiquage et mouvements | `apps.cultures.test_quantitative_subculture`, `apps.cultures.test_box_movement_week_integrity`, `apps.cultures.test_box_movement_corrections` |
| Relevés et import scientifique | `apps.measurements.tests`, `apps.measurements.test_write_services`, `apps.measurements.test_canonical_csv`, `apps.measurements.test_import_planner` |
| Collecte iMinilide et sondes | `apps.measurements.test_probe_collection`, `apps.measurements.test_probe_replacement`, `apps.measurements.test_probe_aggregation`, `apps.measurements.test_set_probe_sampling_interval` |
| Exports et transfert CSV v1 | `apps.exports.tests`, `apps.cultures.test_transfer_strain_ownership`, `apps.cultures.test_transfer_v1_concurrency` |
| Partage et permissions de lecture | `apps.sharing.test_admin_api`, `apps.sharing.test_recipient_api`, `apps.sharing.test_disclosure_modes` |
| Taxonomie et démo isolée | `apps.taxonomy.test_species_resolution_retirement`, `config.test_demo`, `apps.cultures.test_seed_demo_box_creation` |

## Contrat courant : Alertes abandonnées

Les tests ne doivent pas réintroduire la création ou la résolution d’alertes
lors d’une baisse de polypes, d’un écart thermique ou d’une autre mutation.
Les mesures et agrégats restent factuels : `0` est distinct de `null` et de
l’absence de relevé. Les données et migrations historiques `Alert` restent
dormantes ; les anciens audits restent consultables via Actions avec les
permissions habituelles. Un échec d’audit annule toujours l’écriture métier
concernée.

Les cas de référence se trouvent dans `apps.cultures.tests` (dashboards, fiches,
relevés et agrégats), `apps.measurements.tests` (corrections et rollback) et
`apps.accounts.tests_actions_api` (présentation sûre des anciennes actions).
