# CCA-EOB — exception nominative des deux ajouts

## Verdict et périmètre

**READY FOR INDEPENDENT DELTA REVIEW** — préparation locale, pas une autorisation
ni une déclaration de disponibilité pour l'apply en production.

Ce rapport remplace le blocage initial documenté dans
[le rapport archivé](cca-eob-additive-baseline-blockers.md). Le demandeur a fourni
les PK et la vérification read-only des audits métier manquantes. Aucun accès
production, SSH, Neon ou base partagée n'a été réalisé ici. Aucun commit, push,
merge, déploiement ou changement d'ACL système.

## Cause et résolution

La comparaison intégrale de la correction courante avec le plan historique
refusait les deux mesures légitimement ajoutées après sa création. La solution
n'ignore ni les nouvelles dates ni les mesures hors source en général : elle
reconnaît uniquement les deux identités suivantes et contrôle leur audit.

| PK | Boîte | Semaine ISO | Création UTC | Séquence |
|---|---|---|---|---|
| 104688 | 1836 | 2026-10-19 | 2026-10-21T09:37:07.809036Z | 25 |
| 104689 | 1837 | 2026-10-19 | 2026-10-21T09:44:03.511877Z | 25 |

Pour ces deux mesures uniquement, l'origine doit être `direct_entry`, la précision
`exact_date`, l'observateur connu et identique à l'auteur de création, l'importateur
absent, le format d'import vide, sans doublon, source environnementale explicite
ou association thermique historique. La date d'observation doit appartenir à la
semaine stockée; elle n'est jamais déduite du lundi ISO.

La projection de comparaison enlève ces deux lignes et leurs entrées seulement,
ramène `OUT_OF_SOURCE` de 6 à 4, restitue les deux baselines de révision 25 à 24
et compare tout le reste au plan authentifié. **Cette projection est uniquement
en mémoire : aucune ligne ni révision persistée n'est supprimée ou rétrogradée.**
Les comptes exigés sont exactement 72 réattributions et 8 correspondances, avant
comme après, avec quatre puis six hors source. Les 72 PK, destinations, valeurs,
contextes scientifiques et ordre de mouvement restent strictement identiques.

## Authentification historique complète

L'identité historique demeure :

`7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8`

Python impose désormais cette valeur fixe sur toute acceptation du témoin, y
compris le chemin d'égalité historique sans delta. Il recalcule l'empreinte du
**document complet** selon le contrat historique existant :
`json.dumps(original_document, sort_keys=True, default=str).encode()`.
Le document protégé, son schéma de témoin v2 et son contenu ne sont pas modifiés.

Le nouveau plan de production ajoute `plan.historical_document_text`, contenant
les octets de cette même sérialisation sous forme de chaîne JSON. Ce n'est ni un
nouveau témoin ni un autre hash. SQL calcule indépendamment
`encode(sha256(convert_to(text, 'UTF8')), 'hex')`, exige l'identité fixe et exige
que le texte reparsé soit égal au document historique complet embarqué. Cela
évite de hasher une resérialisation JSONB différente de la sérialisation Python,
tout en empêchant la substitution du document ou de sa seule correction interne.
Ce champ entre dans le nouveau hash complet soumis aux décisions administratives.

Les cibles et évidences anciennes restent dans le document original authentifié;
elles ne sont pas remplacées par les paramètres courants.

## Audit et valeurs scientifiques des ajouts

Le reader retourne désormais `addition_audits` : toutes les lignes complètes
nommant l'une des deux PK via `metadata.measurement_id`, sans filtrer par action
ou organisation. Un audit étranger, une correction ou un autre événement ne peut
donc pas être caché par le filtre de lecture.

Python et SQL exigent exactement un audit par mesure, et exactement deux audits
au total, selon le contrat existant du service de saisie :

- `organization_id=1`, même utilisateur que la mesure et appartenance à
  l'organisation, `action=entry`, `object_type=box`, code exact de la boîte;
- métadonnées exactement composées de `measurement_id` et `valeurs`;
- `valeurs` correspond à `date`, `polypes`, `ephyrules`, `strobiles`,
  `salinite_psu`, `statut_culture`, `a_verifier`, `note`, avec les noms et les
  encodages du service métier existant;
- aucune édition : `edited_at` et `edited_by_id` NULL, `original_values={}`;
- audit daté au plus tôt à la création de la mesure, sans inventer une égalité
  exacte entre les deux horodatages générés séparément.

**Aucune valeur scientifique manquante n'a été inventée ou codée en dur.** Les
valeurs courantes sont contrôlées contre l'audit de création, puis toutes les
colonnes de la mesure et de l'audit sont incluses dans le snapshot complet du
nouveau plan privé à revoir et approuver. La lecture sous verrous doit retrouver
exactement ce snapshot. Le writer compare ensuite toutes les colonnes de toutes
les mesures de la portée : seuls les `box_id` des 72 mouvements peuvent changer.
Les deux ajouts restent entièrement inchangés, y compris boîte et provenance.

Dans ce contrat, un export privé supplémentaire à intégrer au dépôt n'est pas
nécessaire. Cela ne transforme pas la vérification communiquée en preuve locale :
le JSON privé d'origine et les vraies lignes de production n'ont pas été relus
ici. La collecte et la revue du nouveau plan complet restent obligatoires via la
procédure existante, dans un canal privé et après autorisation distincte.

## Baselines, révisions et protections transactionnelles

Le delta impose exactement 24 → 25 pour les boîtes 1836 et 1837, avec séquence 25
pour chacun des deux ajouts. Aucun incrément supplémentaire n'est absorbé.
Les révisions finales de correction sont recalculées selon la règle existante :
maximum entre révision courante augmentée du nombre de mouvements touchant la
boîte et séquences finales des mesures qui y appartiendront. Ce n'est pas une
augmentation arbitraire de chaque ancienne valeur finale.

Les deux implémentations lient aussi la correction normalisée au snapshot brut
complet. En SQL, `jsonb_populate_record` applique les types réels des tables pour
comparer toutes les colonnes et les ensembles de clés, sans confondre zéro et
NULL. Les tableaux complets de mesures, boîtes, emplacements et mouvements,
ainsi que zones, liens externes et historiques, doivent correspondre.
Une troisième ligne ou une révision 26 ne peut donc pas être dissimulée dans le
snapshot brut derrière une baseline candidate prétendant encore contenir 25.

Le helper de lock v2 reste inchangé. Le writer conserve les verrous Box-first,
`READ COMMITTED`, `SHARE ROW EXCLUSIVE` sur la biologie et `SHARE` sur les lectures
thermiques. Il prend en plus `SHARE` sur `audit_auditlog` avant de relire le
snapshot : éditions et nouveaux événements concurrents sont exclus jusqu'au
commit. Les audits de la correction sont insérés dans cette même transaction.
Ce verrou de table peut attendre un écrivain d'audit d'une autre activité;
les timeouts existants bornent l'attente et toute erreur annule le lot entier.

Les gardes thermiques, institutions, permissions serveur, identités fixes,
immuabilité des preuves, sauvegarde réellement restaurée, release indépendante,
approbation administrative et autorisation finale courte restent obligatoires.
Aucun plan ancien, approbation consommée ou autorisation expirée n'est renouvelé.

## Transition SQL exacte

Reader **et** writer changent : le premier expose les audits nécessaires; le
second les vérifie, authentifie le document complet et contrôle le delta.
Le lock, le rôle, la table et les lignes d'approbation, propriétaires, ACL et
triggers ne doivent pas changer pendant l'installation.

Artefact : `deploy/scripts/cca_eob_transition_v2_additive.sql`

- Taille : **202557 octets**.
- SHA-256 :
  `42d0f96b5b99977784e37a87c209ccda6b1200694c2fc3a0dba42c74b783d435`.
- UTF-8/LF, deux générations identiques; fichier marqué `-text` pour préserver
  ses octets lors des checkouts. Un test compare le fichier à l'émetteur.

L'émetteur utilise le contrat v2 gelé avant modification, vérifie son empreinte
hors ligne, puis produit le préflight et la transaction fail-closed. Avant tout
remplacement, il authentifie les définitions exactes des trois helpers, leurs
métadonnées, propriétaires et ACL, le rôle et la table d'approbation. Il remplace
uniquement les corps reader/writer nécessaires, vérifie le nouveau contrat et
compare tout `pg_proc` avant/après hormis `prosrc`. Toute erreur rollbacke les
remplacements. Une définition ancienne différente ou une transition déjà
installée est refusée, sans adoption ni reprovisionnement destructif.

L'artefact n'a **pas** été exécuté sur production. Les tests DDL exécutent son
corps sans le préflight de cible production sur une instance locale synthétique;
la sortie complète conservant ce préflight est vérifiée hors ligne.
Les conditions et la génération sont détaillées dans
[le handoff de transition et permissions](cca-eob-v2-additive-transition-runtime.md).

## Lecture Python persistante du runtime

Le workflow appelle désormais un contrôle exact de permissions après
synchronisation des dépendances si le compte dédié ou son manifeste existe.
Le contrôle ne répare pas aveuglément les droits : il exige un inventaire revu et
root-contrôlé du code, ressources, interpréteur, stdlib et dépendances, avec
empreintes de fichiers, droits nominatifs `r`/`rx`, traversée des ancêtres et
sondes privées dont la lecture doit être refusée. Il refuse l'écriture effective,
la propriété des fichiers/ancêtres par le runtime, les liens aliasés et les ACL
par défaut. Aucun groupe partagé ni lecture de `.env` n'est ajouté.

**Prérequis opérationnel restant** : inventorier les chemins Debian réels,
compléter et faire revoir la fermeture d'import, puis faire installer les seules
ACL nominatives nécessaires et le manifeste sous une autorisation séparée.
Tester les imports sans DB sur une installation isolée représentative. Aucun
inventaire live ni test Linux de droits effectifs n'a été effectué ici. Un
manifeste absent ou obsolète bloque désormais la validation prépublication du
workflow lorsqu'un runtime dédié est présent; prévoir ce changement dans la
revue de release. Ce lot fournit le garde persistant, pas une preuve que les
droits actuels de la VM sont corrects.

## Fichiers de la livraison

- `backend/config/cca_eob_additive.py` : contrôles Python, projection et contrôles
  SQL indépendants de baseline/delta.
- `backend/config/cca_eob_database_role.py` : empreinte fixe, audits du reader,
  authentification et verrouillage du writer.
- `backend/apps/cultures/historical_2026/cca_eob_production.py` : snapshot soumis
  au validateur et sérialisation complète authentifiable par SQL.
- `deploy/scripts/cca_eob_role_sql.py`,
  `deploy/scripts/cca_eob_v2_contract_snapshot.py`,
  `deploy/scripts/cca_eob_transition_v2_additive.sql`, `.gitattributes` : génération,
  contrat ancien gelé, transition exacte et conservation de ses octets.
- `deploy/scripts/cca_eob_runtime_permissions.py`,
  `deploy/scripts/deploy_release.sh` : inventaire/contrôle étroit et persistance.
- `backend/apps/cultures/test_cca_eob_additive.py`,
  `scripts/test_cca_eob_additive_postgres.py` : tests synthétiques et clusters neufs.
- `backend/apps/cultures/test_cca_eob_role_provisioning.py`,
  `backend/apps/cultures/test_cca_eob_production.py`,
  `deploy/scripts/test_cca_eob_runtime_permissions.py` : tests de transition,
  adaptation explicite des empreintes de fixtures synthétiques et politique ACL.
- Ce rapport, le handoff transition/runtime, le complément du handoff thermique
  et la note de remplacement dans le rapport de blocage initial.

## Validation finale ciblée

| Contrôle | Résultat final |
|---|---|
| Nouveau module additif sur PostgreSQL 18 jetable | **46 passés**, zéro erreur, zéro skip |
| Transition réelle depuis les helpers v2 gelés sur PostgreSQL 18 jetable | **3 passés** |
| Émetteur/artefact hors ligne | **5 passés** |
| Authentification historique, fixtures synthétiques sans DB | **12 passés** |
| Politique de permissions hors ligne | **8 passés** |
| Syntaxe shell du workflow et whitespace Git | OK |

Les 46 tests couvrent l'acceptation par Python et directement par SQL des deux
ajouts, les 72 mouvements, conservation intégrale des ajouts/audits, troisième
ligne, suppression et modification historique, provenance et audits altérés,
zéro/NULL, révisions, liens thermiques, ordre/destinations, enveloppes candidates
divergentes, restauration atomique après échec tardif, autorisation finale et
concurrence des mises à jour/phantoms d'audit. Les tests de transition couvrent
les anciennes définitions incorrectes, métadonnées/ACL, préservation de l'OID et
des objets inchangés, ainsi que rollback des deux remplacements sur postcheck
forcé en échec.

La validation additive a été découpée en lots bornés. Des essais groupés/parallèles
ont atteint leur échéance sans résultat complet; ils ne sont pas comptés comme
succès. Tous les cas des lots interrompus ont ensuite terminé avec succès dans
des lots plus petits, sans augmenter la limite de temps. Les clusters concernés
étaient arrêtés et ont été supprimés. Aucune suite générale n'a été exécutée.

La revue indépendante read-only du delta avait relevé des possibilités de
baseline/snapshot divergents, de substitution du témoin et de propriété de code
par le runtime. Elles ont été corrigées et couvertes par régressions; la seconde
revue n'a pas identifié de blocage concret résiduel. Elle ne remplace ni la revue
humaine finale ni une preuve de production.

## Conditions de reprise opérateur

Pas de décision scientifique nouvelle nécessaire au vu des preuves communiquées.
Restent obligatoires avant toute exécution : revue indépendante du delta et des
octets SQL, inventaire/permissions runtime privés, autorisations distinctes de
release et d'installation SQL, plan complet frais, revue privée du snapshot,
sauvegarde/restauration vérifiées, approbation et autorisation finale selon la
procédure existante. Aucun de ces accords n'est accordé par ce rapport.
