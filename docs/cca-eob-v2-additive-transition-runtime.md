# CCA-EOB — transition exacte v2 et lecture Python du runtime

Cette livraison est **locale uniquement** : aucune connexion de production/SSH,
base partagée, modification de droits système ou autorisation de déploiement.
Le document complète la [procédure gardée](cca-eob-production-execution.md), sans
remplacer ses protections ni autoriser leur exécution.

## Snapshot avant modification du contrat

`deploy/scripts/cca_eob_v2_contract_snapshot.py` est la copie intégrale du contrat
avant les helpers additifs. Ne pas le régénérer depuis le nouveau contrat.

- SHA-256 du fichier après normalisation CRLF → LF :
  `0aae8b7b03160fabc83112ec77438572efa12a8f1e6c3f46b878e240375b6974`.
- SHA-256 des tuples `helper_specs()` sérialisés par
  `json.dumps(specs, separators=(',', ':'), ensure_ascii=True).encode()` :
  `7264e2b4062e655513ec6d396e2ded22e3ca7e794cbabdf971e7775028b5481c`.

L'émetteur vérifie l'empreinte du fichier avant de charger ce module hors ligne.
Les contrôles des définitions attendues utilisent ce snapshot, pas les nouveaux
corps ni une extraction opportuniste de la base qui rendrait toute dérive acceptable.

L'original historique communiqué reste identifié par
`7be6a6e8674601e9f0b3429da0d1ec0f748a9443e9b959ab537dba5ff671e7c8`.
Il ne s'agit ni d'un nouveau hash de plan, ni d'une preuve privée reproduite ici.
Les deux ajouts communiqués sont PK 104688 / boîte 1836 et PK 104689 / boîte 1837,
semaine UTC du 2026-10-19, créés respectivement le
2026-10-21T09:37:07.809036Z et le 2026-10-21T09:44:03.511877Z,
`direct_entry`, `exact_date`, séquence 25, révision boîte 24 → 25,
avec chacun un unique audit d'entrée correspondant, sans correction ni lien
thermique. Ces éléments sont des preuves fournies par le demandeur, pas une
vérification de production effectuée par cet agent. Leur validation scientifique
Python/SQL appartient au nouveau contrat; l'émetteur ne réécrit aucun original.

## Générateur de transition

Après livraison et revue des nouveaux `contract.helper_specs()`, générer hors
ligne avec un interpréteur et une fermeture d'import de confiance :

```sh
python -B deploy/scripts/cca_eob_role_sql.py transition-v2-additive > transition-v2-additive.sql
```

Contrôler le code retour avant de considérer la sortie comme un artefact; une
redirection shell peut créer un fichier vide même si la commande refuse.
La sortie est UTF-8/LF déterministe pour une même révision complète. L'artefact
final est `deploy/scripts/cca_eob_transition_v2_additive.sql` (202557 octets),
SHA-256 `42d0f96b5b99977784e37a87c209ccda6b1200694c2fc3a0dba42c74b783d435`.
Deux générations indépendantes ont produit les mêmes octets; un test vérifie
l'égalité du fichier livré et de la sortie de l'émetteur. Le fichier est marqué
`-text` dans `.gitattributes` pour préserver ces octets sur chaque plateforme.
Le contrôle whitespace est désactivé uniquement pour cet artefact généré : les
espaces finaux contenus dans les anciens corps SQL authentifiés sont des octets
contractuels à conserver, pas du code source à reformater.
Si les corps n'ont pas changé, le générateur refuse de produire un no-op.

La structure relative du bundle vérifié doit inclure le snapshot avec l'émetteur
et `backend/config`, y compris `config/__init__.py`. Ne jamais exécuter ces imports
en root depuis le checkout live ou son venv; conserver les voies de génération
hors ligne/staging protégé de la procédure existante. Ne pas piper vers `psql`.
L'exécution future de l'artefact demande une revue et une autorisation distinctes.

### Contrat de la transition

1. Même préflight local PostgreSQL 18, base `polypbase`, port 5433, primaire,
   session/effective user `postgres`; mêmes timeouts et chaînes SQL standard.
2. Transaction unique; refus des sessions runtime existantes. Prévoir une fenêtre
   sans runtime ni autre administrateur modifiant simultanément ces objets :
   l'absence observée de session n'interdit pas à elle seule une nouvelle connexion.
3. Authentification des **trois** helpers anciens : corps v2 exacts, propriétaire,
   ACL et toutes les métadonnées couvertes par `helper_checks(specs=...)`, y
   compris refus d'overloads inconnus. Vérifications v2 du rôle, des privilèges
   effectifs et de la table d'approbation. Aucun état ancien inconnu n'est adopté.
4. Vérification que le lock reste strictement identique et que les signatures,
   arguments, types de retour, rôle et objets d'approbation ne changent pas.
5. `CREATE OR REPLACE FUNCTION` seulement pour chaque reader/writer dont le corps
   change. Aucun `ALTER OWNER`, `GRANT`, `REVOKE`, drop, reprovisionnement, changement
   de rôle, table, ligne d'approbation, trigger ou donnée métier.
6. Vérification complète du nouveau contrat avant commit; comparaison exacte des
   lignes `pg_proc` avant/après, hormis `prosrc`. OID, ACL brutes, propriétaires,
   paramètres et autres attributs doivent donc rester identiques.
7. Toute erreur avant commit annule les deux remplacements. Rejouer une transition
   déjà appliquée refuse les corps devenus nouveaux : ce n'est pas un mécanisme de
   reprovisionnement ni de rollback vers v2. Les actions existantes `provision`,
   `verify`, `rollback`, `approve`, `authorize-apply` conservent leur comportement.

La transition n'approuve aucun plan et ne renouvelle aucune décision antérieure.
Après installation future, produire un nouveau plan par la procédure gardée;
ne pas réutiliser un plan dont le snapshot des helpers n'est plus courant.

## Lecture Python : manifeste exact, pas de réparation aveugle

Le déploiement utilise `umask 027`, met à jour le checkout puis exécute
`uv sync --frozen`. Aucun inventaire Debian de ses ancêtres, ACL, cibles de liens,
interpréteur, stdlib ou packages n'a été effectué ici. **Aucun manifeste live ni
script d'application d'ACL n'est inventé à partir de suppositions.**

`deploy/scripts/cca_eob_runtime_permissions.py` propose une fermeture explicite :

- `build` est hors ligne, sans import applicatif/DB. Il inventorie les `.py`
  suivis sous `backend`, hors chemins de tests, et calcule leurs empreintes.
- L'inventaire indépendant doit lister chaque fichier réel de dépendance,
  interpréteur, stdlib et ressource nécessaire : chemin POSIX absolu canonique,
  SHA-256, accès `r` ou `rx`, origine `dependency`, `interpreter`, `stdlib` ou
  `resource`. Les ressources applicatives restent sous `backend`.
- Aucun glob, découverte récursive d'un venv/machine, répertoire lisible ajouté,
  lecture de `.env`, `.git`, backup, log, sortie privée ou groupe élargi.
- L'inventaire comporte `scope_review` non vide et `deny_read`, liste explicite de
  fichiers privés existants dont la lecture doit être refusée. Leur contenu n'est
  jamais ouvert par le contrôleur.
- Le contrôle ne prouve pas automatiquement la complétude sémantique de la fermeture
  d'import. Cette complétude doit être revue et testée sans connexion DB, sous le
  runtime dédié, sur une installation isolée représentative.

**Production de l'inventaire :** `deploy/scripts/cca_eob_runtime_inventory.py`
relève sur la VM la fermeture réellement chargée (`trace` sous le runtime dédié,
sans connexion DB) puis l'inventaire haché et vérifié contre les `RECORD`
(`collect` sous `polypbase`, jamais root). Il ne modifie aucune ACL et refuse
tout fichier non classé sans décision explicite du relecteur. Procédure
opérateur et points d'approbation :
[`cca-eob-runtime-manifest-procedure.md`](cca-eob-runtime-manifest-procedure.md).
Le garde ne contrôle que la traversée (`x`) des ancêtres : les répertoires que
le runtime liste (imports, `site.py`) exigent `r+x`, prouvés par la trace qui les
relit sous le runtime et s'arrête sinon. `CCA_EOB_RUNTIME_PERMISSIONS_OK` seul ne
prouve donc pas la complétude de ces droits.
La trace ne couvre que les imports et données chargés au démarrage de la
commande : imports paresseux hors `LAZY_MODULES`, fichiers de données lus
pendant `plan`/`apply` et exécutables externes (`pg_restore`) restent à la revue.

Forme de l'inventaire hors ligne : objet avec exactement `files`, `deny_read`,
`scope_review`. Chaque entrée `files` contient exactement `path`, `sha256`, `access`,
`origin`. Les empreintes et chemins doivent provenir de l'inventaire privé revu,
jamais d'exemples ou de données de production copiées dans Git.

```sh
python -B deploy/scripts/cca_eob_runtime_permissions.py build --root . --inventory reviewed-inventory.json > cca-eob-runtime-permissions-v1.json
```

L'outil refuse l'absence d'inventaire interpréteur/stdlib/dépendances ou de sondes
privées. Générer depuis la révision complète revue; traiter le manifeste comme
une politique à relire, non comme une attestation acquise. Les hashes portent
sur les octets réels : utiliser un checkout hors ligne dont les fins de ligne
correspondent à l'installation Linux, pas un checkout Windows converti en CRLF.
Inclure dans la revue les fichiers `.pth`, extensions natives, bibliothèques
partagées et ressources chargées dynamiquement; leur omission n'est pas réparée
par le contrôleur. Ne pas publier les chemins privés si leur contexte l'interdit.

### Intégration persistante au workflow

Après synchronisation des dépendances, avant validation Django, `deploy_release.sh`
exige `/etc/polypbase/cca-eob-runtime-permissions-v1.json` si le compte Linux
`polypbase_cca_eob` existe **ou** si ce manifeste est présent. Sans compte ni
manifeste, le workflow ordinaire reste inchangé. Le manifeste manquant, illisible,
obsolète ou invalide bloque : aucun fallback vers une ACL globale.

`check` fonctionne sous Linux, en lecture seule, avec `getfacl` disponible :

- politique et ancêtres réels sous contrôle root, sans écriture de groupe/autrui,
  ACL d'écriture non propriétaire ou ACL par défaut;
- égalité de l'inventaire du code suivi avec celui déployé et de toutes les
  empreintes, y compris dépendances après `uv sync`;
- évaluation des permissions POSIX effectives du compte dédié : UID, groupes,
  ACL nommées, masque, priorité propriétaire/utilisateur/groupe/autrui;
- lecture/exécution prévue des fichiers; traversée des ancêtres sans écriture;
  refus de propriété par l'UID runtime sur tout fichier inventorié et sur chacun
  de ses ancêtres, même en mode 0400 ou 0500 : le propriétaire pourrait rétablir
  l'écriture par `chmod`; refus de lecture des sondes privées;
- refus des liens symboliques, ancêtres aliasés et ACL par défaut. Un venv utilisant
  un lien d'interpréteur ou une stdlib aliasée exige donc une décision/inventaire
  supplémentaire; ne pas contourner le STOP en oubliant ce chemin d'entrée.

**Limite assumée : ce lot vérifie la persistance, il n'installe/répare pas les ACL.**
L'inventaire live et l'application nominative restent une opération ultérieure,
privée et explicitement autorisée. Une fois les fichiers inventoriés, accorder
uniquement lecture aux fichiers, exécution aux exécutables identifiés et traversée
aux ancêtres. Ne pas élargir silencieusement le masque ACL, qui pourrait réactiver
les droits d'autres entrées. Ne pas utiliser de `setfacl -R`, d'ACL par défaut sur
un arbre mixte, de groupe partagé, de `chmod` global, ou de copie de `.env`.

Tout ajout de code/package/ressource doit mettre à jour l'inventaire revu et les
permissions nominatives exactes avant la prochaine validation. Sans inventaire
complet valide, le déploiement d'un runtime dédié s'arrête après la mise à jour
et le sync, comme tout échec de validation prépublication; aucun service n'est
redémarré par ce contrôle. Cette modification du garde de déploiement doit faire
partie de la revue de release.

## Validation locale

- Tests PG18 exécutables dans `backend/apps/cultures/test_cca_eob_role_provisioning.py`
  : anciens corps et métadonnées/ACL exacts, préservation OID/propriétaire/ACL/lock,
  rôle, tables, triggers et approbation, refus des définitions altérées et rollback
  des deux remplacements sur postcheck forcé en échec.
- Les tests DDL exigent désormais les vrais nouveaux corps reader/writer et
  partent des définitions v2 gelées. Aucun fallback vers un corps synthétique
  n'est utilisé; l'absence de nouveaux helpers fait échouer la preuve. Ces tests
  vérifient la transition, pas l'ensemble de la logique scientifique additive.
- Tests hors ligne de la politique :
  `uv run python -B deploy/scripts/test_cca_eob_runtime_permissions.py`.
- Tests hors ligne du collecteur (système de fichiers synthétique) :
  `uv run python -B deploy/scripts/test_cca_eob_runtime_inventory.py`.
- Régressions du workflow :
  `uv run python -B -m unittest discover -s deploy/tests -v`.
- Aucun contrôle de permissions effectives Debian/runtime, inventaire live,
  application d'ACL ou déploiement n'a été exécuté ici.
