# CCA-EOB — reprise gardée et manifeste source-only

> **Procédure close — runtime dédié retiré le 2026-11-04 (rapporté par l'opérateur).**
> Compte et groupe Linux dédiés supprimés, ACL dédiées retirées, manifeste actif
> archivé; garde de déploiement rapporté `CCA_EOB_DEPLOY_GUARD_CLEAR`. Tout le
> contenu ci-dessous, y compris les états « rapportés », les consignes de conserver
> le compte dédié ou le garde et les étapes de reprise, est **historique** et
> n'est plus une procédure à exécuter. **Ne jamais recréer le compte dédié ni le
> manifeste simplement pour satisfaire le garde de déploiement** : leur réapparition
> réactive le contrôle et bloque la release. État courant et limites :
> [`POLYPBASE_CURRENT_STATE.md`](../POLYPBASE_CURRENT_STATE.md); garde :
> [`deploiement_vm.md`](deploiement_vm.md#garde-cca-eob-du-runtime-dédié).

## Statut et périmètre

**Procédure opérateur future, non autorisée et non exécutée par ce correctif.**
Chaque mutation ci-dessous exige un accord explicite immédiatement avant sa
phase. Aucune commande ne constitue une autorisation d'ACL, migration, SQL,
plan/apply scientifique ou déploiement. Aucun script automatique de réparation
n'est fourni. Contrat et limites :
[protection bytecode](cca-eob-v2-additive-transition-runtime.md#protection-bytecode-du-runtime-cca).

État **rapporté par l'opérateur**, à vérifier à nouveau avant toute reprise :

- checkout `4866520eae2b30f676ab96365fd806486948e832`, release arrêtée au garde
  `CCA-EOB runtime read permissions`, ancien manifeste encore actif;
- candidat archivé de 951 entrées pour l'ancien contrat, **non admissible** ici;
- nouveau lanceur absent; deux migrations de production en attente, non
  appliquées : `accounts.0008_interface_language_japanese` et
  `measurements.0015_iminilide_probe_collection`.

Les preuves anciennes `-I -B` ne prouvent pas l'absence de consommation des
caches. Ne pas réinstaller le candidat 951 entrées, supprimer les caches, enlever
le compte dédié, désactiver le garde ou poursuivre les étapes de release à la main.

### Revue Debian 13 de production et défaut des ressources TARGET

Preuves **rapportées par l'opérateur** pour la tentative précédente (outil de
permissions `831d9ef8…`), ancien manifeste actif inchangé : trace v2 validée,
inventaire de 706 entrées, bridge de 951 entrées avec contrôle ACL live réussi,
candidate de 953 entrées, contrôle de données candidate réussi, 14/14
bibliothèques natives contrôlées. Ce candidate **n'est pas approuvé** et ne doit
jamais être installé.

Défaut bloquant : le collecteur classe les fichiers non `.py` de `APP/backend`
en `resource` et les hache sur le **live**; l'ancien `build` recopiait ces
entrées telles quelles. Le candidate portait donc le hash LIVE de
`backend/locale/fr/LC_MESSAGES/django.mo`, alors que TARGET contient d'autres
octets : le garde de release, exécuté après fast-forward et `uv sync`, avant
migrations, aurait refusé TARGET. L'ancien contrôle de données exigeait ces
entrées égales à l'inventaire et ne pouvait pas détecter le défaut.

Correction : `build` dérive désormais le hash de chaque ressource inventoriée des
octets suivis par Git dans `--root` (étape 4). Comme TARGET change, tout l'ancien
jeu de preuves (trace, inventaire, bridge, candidate) reste archivé mais n'est
pas réutilisé : nouvelle séquence complète depuis l'étape 0 avec le nouveau
TARGET, le nouveau bundle et la nouvelle empreinte de l'outil de permissions.
Ne jamais corriger un hash à la main dans un manifeste : un manifeste édité
manuellement n'est jamais une solution de contournement acceptable.

### Pourquoi la séquence n'est pas circulaire

1. Un bundle de sources du **futur commit cible intégré et revu** est authentifié
   indépendamment du checkout live. Un seul lanceur est publié hors checkout,
   sous contrôle root, avant l'achèvement de la release.
2. Ce lanceur produit une trace v2 avec le venv live, sans DB. Un manifeste frais
   **bridge**, construit avec les sources live, vérifie maintenant les dépendances,
   ACL et sondes privées. Il n'est jamais installé comme manifeste actif cible.
3. Le manifeste **candidate** utilise les sources et ressources backend exactes de
   la cible et exactement le même inventaire externe (dépendances, interpréteur,
   stdlib). Ses sources et ressources sont vérifiées contre le bundle indépendant,
   pas contre des fichiers futurs inexistants sur le live.
4. Après revue/autorisation, seul candidate remplace la politique active. Le garde
   de release reste au même emplacement : après checkout/sync, avant Django et
   migrations. Il doit vérifier les vrais fichiers cibles et leurs ACL.

## 0. Identités à figer hors production

Après revue, intégration et CI du correctif (aucune de ces actions n'est autorisée
ici), relever le SHA complet `TARGET` de `main`, égal à `origin/main`. Ne pas
utiliser la branche locale non committée comme source de production. Depuis ce
commit propre, produire hors production un bundle avec les blobs Git, donc les
fins de ligne Linux, sans `.env`, `.git` ou données live :

```sh
git archive --format=tar --output=cca-eob-reviewed.tar TARGET backend deploy/scripts/cca_eob_runtime_inventory.py deploy/scripts/cca_eob_runtime_permissions.py
sha256sum cca-eob-reviewed.tar
git archive --format=tar --output=cca-eob-live-backend.tar 4866520eae2b30f676ab96365fd806486948e832 backend
sha256sum cca-eob-live-backend.tar
```

`TARGET` dans cette commande est à remplacer par le vrai SHA de 40 caractères.
Le second bundle contient le `backend` du commit live rapporté; il sert seulement
de référence **LIVE authentifiée** pour les octets des ressources du bridge
(étape 5). Si le HEAD live constaté à l'étape 1 diffère de `4866520e`, STOP :
nouvelle revue et nouveau bundle LIVE.
Le relecteur indépendant conserve ces SHA, les SHA-256 des deux archives, leurs
listes de membres, le diff runtime et les résultats Linux 3.13. Les valeurs
attendues ne proviennent pas d'un fichier de checksums produit sur le live par
`polypbase`.

Empreintes des sources **LF** du présent correctif (à retrouver dans les blobs
Git de TARGET; toute différence exige nouvelle revue et nouvelles empreintes).
Le lanceur ci-dessous inclut le refus de `--app` hors trace : cette révision est
une candidate en attente de revue de sécurité indépendante, pas un artefact
approuvé pour la production. L'empreinte du collecteur inclut la correction de
classification du seul `APP/.venv/pyvenv.cfg` attendu : son observation en ouverture
pendant `site.main()` ne change pas son rôle de démarrage (`interpreter`, lecture
seule), et son absence de la liste `opened` conserve le même inventaire.

Le collecteur exige que `python.prefix` soit exactement `APP/.venv`, dérivé de
l'APP autorisée et validée par `safe_path`, différent de `base_prefix`. Il refuse
les valeurs absentes, mal formées, non canoniques et les alias, sans normaliser
le préfixe fourni. Le répertoire attendu et ses ancêtres doivent être réels, sans
symlink, même avec `--accept-alias`.

Avant de fusionner les classifications, le collecteur examine la liste originale
`mapped` issue de `/proc/self/maps` : toute entrée résolue vers le fichier attendu
est un STOP, même si celui-ci figure aussi dans `opened` ou `modules`, ou si un
alias est utilisé. Ni `--extra` ni `--exclude` ne contournent ce refus. La résolution
sert seulement à comparer les chemins; aucun événement de trace n'est réécrit,
normalisé ou retiré par ces contrôles. Les autres contrôles de chemins, liens,
provenance, RECORD, propriété et permissions restent requis.

Les empreintes du lanceur et du collecteur restent inchangées. L'outil de
permissions change : son `build` hache désormais les ressources depuis `--root`.
L'ancienne empreinte `831d9ef85b2e4a26266a0ba07bc556e0428cf393e85f17f0968af3dbbd637ac5`
est **révoquée** pour toute nouvelle construction; un bridge ou candidate produit
par elle n'est pas admissible.

```text
0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42  backend/cca_eob_runtime.py
bf9f59d689036b38d47f5688a32d8faa903efab37b139f3c92c76ba6f1f517e4  deploy/scripts/cca_eob_runtime_inventory.py
8ce7fcb443978a8b32ddfdff4fa8b383f262ce2d89b41f2971c7dc2b86cd30c4  deploy/scripts/cca_eob_runtime_permissions.py
```

Procédure d'empreinte de l'outil de permissions : hors production, depuis le
clone propre où TARGET a été relevé, calculer l'empreinte du **blob Git** (LF),
jamais celle d'une copie de travail Windows (CRLF) :

```sh
git show TARGET:deploy/scripts/cca_eob_runtime_permissions.py | sha256sum
tar -xOf cca-eob-reviewed.tar deploy/scripts/cca_eob_runtime_permissions.py | sha256sum
```

Les deux valeurs doivent être égales entre elles et à la ligne ci-dessus; à
l'étape 2, la copie publiée dans B doit avoir la même empreinte, avant et après
usage. Toute différence = STOP, nouvelle revue et nouvelle empreinte consignée
dans ce document par un correctif revu, jamais par édition sur la VM.

**Diff runtime depuis `4866520e` — STOP par défaut.** Le relecteur établit hors
production `git diff --name-status 4866520e TARGET -- backend pyproject.toml uv.lock`
et classe chaque fichier. STOP sauf si **toutes** ces conditions sont réunies :

1. `pyproject.toml` et `uv.lock` sont identiques : l'inventaire externe
   (`dependency`, `interpreter`, `stdlib`) collecté sur le live est réutilisé
   sans aucune modification par bridge et candidate.
2. Chaque source `.py` modifiée ou ajoutée est une entrée `tracked_code` hachée
   depuis TARGET; chaque fichier non `.py` modifié **et présent dans l'inventaire**
   est une entrée `resource` hachée depuis TARGET (étape 4) et apparaît dans la
   sortie `CHANGED_RESOURCE` du contrôle de l'étape 5. Aucune autre ressource
   n'est ajoutée.
3. La fermeture runtime de TARGET (modules, ressources et bibliothèques chargés
   par `cca_eob_production`) est la même que celle tracée sur le live, ou la
   différence est couverte par la preuve d'installation TARGET ci-dessous. La
   trace live exécute les sources live : elle ne voit ni un nouvel import, ni une
   nouvelle ressource, ni une nouvelle bibliothèque de TARGET. Ne pas extrapoler.

Au relevé de ce correctif, le diff runtime depuis `4866520e` dépasse le lanceur
et le garde des settings : sources `accounts` (middleware, modèle, migration
`0008_interface_language_japanese`), `measurements` (`iminilide.py`, migration
`0015_iminilide_probe_collection`), `config/settings.py`,
`config/database_backup.py`, catalogues `locale` `en`/`fr` modifiés et `ja`
ajouté. `pyproject.toml` et `uv.lock` sont inchangés. À revalider sur le TARGET
réel. Dans l'inventaire live examiné, la seule ressource backend modifiée connue
est `backend/locale/fr/LC_MESSAGES/django.mo` (langue `LANGUAGE_CODE` du runtime);
sa différence est **attendue** et doit être vérifiée indépendamment (étape 5).

**Preuve d'installation TARGET authentifiée indépendamment**, exigée pour la
condition 3. Elle doit :

- provenir d'une installation isolée complète construite depuis le bundle TARGET
  authentifié (même SHA-256), avec le même `uv.lock` et `uv sync --frozen`, sans
  donnée live, `.env`, Neon ou production;
- exécuter le même lanceur, les mêmes drapeaux, le compte dédié non root,
  NoNewPrivs et le contrat `cca_eob_source_only_v1`, puis le même collecteur;
- être conservée avec ses hashes, l'OS et la version Python, et revue par un
  relecteur distinct de son auteur;
- établir que les chemins `resource` (relatifs à APP), les fichiers de
  distributions du venv et les modules stdlib chargés par TARGET sont ceux de
  l'inventaire live; toute ressource, tout module ou toute distribution
  supplémentaire = STOP et nouvelle décision.

Limites : une QA isolée Debian 12 ne reproduit pas le runtime de production
Debian 13 complet (paquets et chemins de l'interpréteur système, stdlib, loader
et bibliothèques natives, ACL, comptes et ancêtres réels). Elle peut seulement
étayer la forme de la fermeture côté backend et venv; elle ne fournit **aucun**
hash `interpreter`, `stdlib` ou bibliothèque native, qui proviennent uniquement
de la trace et de la collecte Debian 13. Les preuves de QA et de clôture
connues sont résumées dans « Limite de confiance et critères de clôture ».

## 1. Préconditions live — inspection séparément autorisée

Les commandes suivantes sont des exemples de session **Bash opérateur**, pas un
script à lancer en bloc. Arrêter après toute erreur ou résultat inattendu.
Définir littéralement les chemins privés et SHA approuvés; aucun de leurs
contenus n'est à publier. Variables employées dans toute la suite :

- `APP=/srv/polypbase/app`;
- `TARGET` = SHA intégré approuvé; `REVIEW` = référence de revue indépendante;
- `R=/root/cca-eob-runtime/TARGET` : archive, extraction `R/source`, anciennes
  politiques et preuves privées, root:root 0700; remplacer TARGET dans le chemin;
- `B=/etc/polypbase/cca-eob-bootstrap/TARGET` : outils et seules politiques
  temporaires nommées ci-dessous (jamais trace brute ni secret), sous
  root, répertoires réels root:root 0711, sans ACL par défaut ou écriture
  nommée; remplacer TARGET dans le chemin;
- `W` = nouveau répertoire de travail 0700 appartenant à `polypbase`, hors APP.
  Chaque nouvelle tentative utilise un autre W et une autre archive de preuves.

Vérifier HEAD/statut live, absence de release concurrente et de runtime CCA,
UID/GID et groupes des comptes, NoNewPrivs disponible (`setpriv`), `runuser`,
`getfacl`, Python système ≥ 3.9, venv CPython 3.13 et son interpréteur réel.
Le compte `polypbase_cca_eob` n'est ni root, ni membre de `polypbase`/`postgres`,
ni doté de sudo/capacités. Relever le hash de l'ancien manifeste et son contrôle
root, et le conserver dans R **avant** tout remplacement. L'ancien manifeste
reste actif pendant la collecte et la revue. Confirmer séparément l'état des
migrations par les preuves opérateur autorisées; cette procédure ne sonde pas DB.

Revoir les ancêtres et ACL effectives, pas seulement `ls -l`. Choisir des sondes
privées existantes, dont `APP/backend/.env`, sans afficher leur contenu. Les
sources, `.pth`, `_virtualenv.py`, interpréteur, stdlib et extensions doivent
avoir une provenance approuvée **avant** les imports : la collecte n'authentifie
pas rétroactivement du code exécuté. STOP si cette confiance est indisponible.

## 2. Publier le bundle d'amorçage — APPROBATION BOOTSTRAP

Après accord explicite pour cette seule écriture de fichiers opérateur : fournir
l'archive par un canal opérateur approuvé, comparer son digest au digest indépendant
de l'étape 0, puis examiner ses membres (aucun chemin absolu, `..`, lien ou fichier
inattendu) et l'extraire comme **données** dans `R/source`. Traiter de même le
bundle LIVE de l'étape 0, extrait comme données dans `R/live-source`; il n'est
jamais publié dans B ni exécuté. Ne jamais exécuter de
backend, venv ou script non vérifié en root. R et ses ancêtres restent root-only.

Les seules copies exécutables Python à publier dans B sont :

| Source authentifiée dans R/source | Destination dans B | Propriétaire et mode |
|---|---|---|
| `backend/cca_eob_runtime.py` | `cca_eob_runtime.py` | root:polypbase_cca_eob 0640 |
| `deploy/scripts/cca_eob_runtime_inventory.py` | `cca_eob_runtime_inventory.py` | root:polypbase 0640 |
| `deploy/scripts/cca_eob_runtime_permissions.py` | `cca_eob_runtime_permissions.py` | root:polypbase 0640 |

L'opérateur peut utiliser `install -m 0640 -o root -g GROUPE SOURCE DESTINATION`
**pour chacun de ces trois fichiers nommés seulement**, après son accord. Créer
les deux répertoires B nécessaires avec `install -d -m 0711 -o root -g root`,
sans modifier les droits existants de `/etc` ou `/etc/polypbase`. Pas de boucle
d'ACL, droit global, groupe partagé, copie de venv ou copie dans APP.

Contrôler `namei -l`, `stat`, `getfacl -cpn` sur les trois fichiers et chaque
ancêtre. Attendus : fichiers réguliers, liens uniques, pas de symlink, aucun
droit d'écriture effectif pour les deux comptes, ni ACL par défaut. Le runtime
lit seulement le lanceur; `polypbase` lit les deux outils et peut stat/traverser
le chemin du lanceur, sans avoir besoin de lire son contenu. Vérifier `test -r`
et `test -w` sous chaque identité; aucun test d'écriture réelle. Les traces et rapports privés restent dans R/W, pas dans B. Les deux politiques
temporaires explicitement nommées plus bas gardent les mêmes lecteurs que la
politique active (root et groupe polypbase), jamais le groupe runtime. Comparer en root les trois SHA-256 aux
empreintes indépendantes de l'étape 0, **avant et après** leur utilisation.

B n'a pas de venv. Seul le mode `trace --app APP` peut employer ce lanceur externe;
plan/apply utilisent uniquement le lanceur livré après la release. Le code n'est
jamais exécuté comme root : le privilège est abandonné avant l'interpréteur.

## 3. Trace v2 sans DB — APPROBATION TRACE

Créer W comme `polypbase` (`mktemp -d /tmp/cca-eob-evidence.XXXXXXXX`, relever son
chemin et mode 0700). Après récupération de `origin/main` par la voie opérateur
approuvée, sans avancer APP, cloner les sources sous `W/src` :

```sh
sudo -u polypbase git clone --quiet --no-hardlinks --no-checkout "$APP" "$W/src"
sudo -u polypbase git -C "$W/src" -c advice.detachedHead=false checkout --quiet --detach "$TARGET"
sudo -u polypbase git -C "$W/src" rev-parse HEAD
sudo -u polypbase git -C "$W/src" status --porcelain
```

Exiger HEAD = TARGET, statut vide. La vérification privilégiée de l'étape 5
compare **chaque source runtime** du clone aux blobs authentifiés de `R/source`
avant toute acceptation du candidat. Aucun import applicatif depuis W ni droit
runtime sur W n'est nécessaire. Ne pas exécuter les anciens outils du live.

Depuis `/`, en Bash, après accord pour les imports runtime :

```sh
sudo -u polypbase cat "$B/cca_eob_runtime_inventory.py" \
  | sudo runuser -u polypbase_cca_eob -- setpriv --no-new-privs -- env -i \
      "$APP/.venv/bin/python" -I -S -B -X pycache_prefix=/dev/null \
      "$B/cca_eob_runtime.py" trace --app "$APP" --deny-read "$APP/backend/.env" \
  | sudo -u polypbase sh -c 'umask 077; set -C; cat > "$1"' sh "$W/trace-v2.json"
printf '%s\n' "${PIPESTATUS[@]}"
```

Attendu : **trois zéros**, JSON complet, schéma `cca_eob_runtime_trace_v2`, UID
non root du compte dédié, `no_new_privs=true`, sondes privées toutes refusées et
contrat `cca_eob_source_only_v1`, CPython `[3,13]`, `isolated=1`, `no_site=1`,
`dont_write_bytecode=true`, `pycache_prefix=/dev/null`, `site_initialized=true`.
Le lanceur capture les événements avant site/`.pth`; il refuse ZIP/sourceless et
chargeurs non supportés. `connection.connection` doit rester `None`.

Tout code retour non nul, sortie non JSON (y compris un `.pth` qui écrit sur
stdout), répertoire requis illisible, sonde lisible ou contrat différent = STOP.
La trace écrite après un échec n'est pas utilisable. Ne pas modifier les droits
ou retirer `-S` pour faire passer la trace; diagnostic et toute ACL nominative
éventuelle exigent une nouvelle décision séparée. Les imports prévus et la
provenance doivent être revus même si la trace finit en succès.

## 4. Collecte et deux manifestes distincts — aucun changement actif

Après revue de la trace, comme `polypbase`, avec le Python système sans site :

```sh
sudo -u polypbase env -i /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
  "$B/cca_eob_runtime_inventory.py" collect --app "$APP" \
  --trace "$W/trace-v2.json" --output "$W/inventory.json" --report "$W/report.txt" \
  --scope-review "$REVIEW target=$TARGET live=4866520e trace-v2" \
  --accept-alias "$APP/.venv/bin/python" --exclude "$B/cca_eob_runtime.py"
```

L'exclusion concerne **seulement** la copie temporaire root déjà authentifiée :
`__main__.__file__` la fait apparaître dans la trace; le collecteur doit pouvoir
stat son chemin avant exclusion. Le lanceur futur sous `APP/backend` est inclus
normalement par build. Aucun autre fichier exécutable ne peut être exclu pour
contourner un refus. Alias système, ressources supplémentaires et distributions
paresseuses exigent leurs décisions explicites `--accept-alias`, `--extra` ou
`--include-distribution`; jamais tous les packages automatiquement. Les refus
private/mixed, RECORD, runtime owner, bytecode ou source backend non suivie ne
sont pas des exceptions acceptables. Les `.pth` et `_virtualenv.py` sans hash
RECORD exigent une preuve indépendante de provenance.

Construire les deux politiques avec **le même inventory.json**, sorties nouvelles :

```sh
sudo -u polypbase sh -c 'umask 077; set -C; env -i /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null "$1/cca_eob_runtime_permissions.py" build --root "$2" --app "$2" --inventory "$3/inventory.json" > "$3/bridge.json"' sh "$B" "$APP" "$W"
sudo -u polypbase sh -c 'umask 077; set -C; env -i /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null "$1/cca_eob_runtime_permissions.py" build --root "$2/src" --app "$3" --inventory "$2/inventory.json" > "$2/candidate.json"' sh "$B" "$W" "$APP"
```

Exiger les deux codes retour 0. **Bridge est une nouvelle preuve du contrat
source-only sur les sources actuelles, pas le candidat archivé de 951 entrées.**
Candidate contient les sources de TARGET, dont le nouveau lanceur et les settings
modifiés. Il ne doit pas être contrôlé comme s'il correspondait déjà au live ancien.

Dérivation des entrées par `build` (outil à l'empreinte de l'étape 0) :

- `tracked_code` : toutes les sources `.py` suivies sous `backend` de `--root`,
  hachées depuis `--root` (inchangé).
- `resource` : **seulement** les entrées déjà présentes dans l'inventaire revu,
  dans le même ordre. Pour chacune, `build` valide le chemin absolu canonique
  (`safe_path`), exige qu'il soit strictement sous `APP/backend`, en dérive le
  chemin relatif, exige qu'il soit suivi par Git dans `--root` comme blob
  régulier (mode `100644`/`100755`, ni lien symbolique ni gitlink), refuse tout
  lien symbolique sur chaque composant et tout chemin réel sortant de `--root`,
  exige un fichier régulier, puis calcule le SHA-256 des octets de `--root`.
  Chemin, accès et origine de l'inventaire sont conservés; seul le hash vient de
  `--root`. Ressource absente, non suivie, liée, irrégulière ou illisible : échec,
  aucun manifeste.
- `dependency`, `interpreter`, `stdlib` : recopiées **exactement** depuis
  l'inventaire (chemin, hash, origine, accès). Aucun joker, aucune découverte de
  dépendance, aucune exception automatique, aucune ACL.

Ainsi bridge (`--root "$APP"`) porte le hash LIVE de chaque ressource et
candidate (`--root "$W/src"`) le hash TARGET. Une différence entre les deux
est attendue seulement pour une ressource modifiée entre live et TARGET, et
doit être prouvée à l'étape 5. L'inventaire et la trace ne sont pas modifiés.

## 5. Vérification indépendante — APPROBATION PREUVES, pas installation active

Copier trace, inventory, report, bridge et candidate dans R sous root:root 0600
après accord de conservation des preuves. Comparer leurs hashes aux sorties W;
revoir ces **copies figées**, pas des fichiers encore modifiables dans W. Conserver
la liste des hashes, versions Python/uv, commit live/TARGET, heures et décisions.
Ne pas exécuter de code backend en root pour vérifier ces documents.

Le relecteur indépendant exige :

1. Hashes des deux bundles et des trois outils égaux aux preuves de l'étape 0;
   clone TARGET égal aux sources authentifiées; delta runtime classé et admis
   selon l'étape 0, avec sa preuve d'installation TARGET si requise.
2. JSON bien formés, aucun doublon de chemin, schéma/identité/app attendus,
   `scope_review` référençant TARGET et le hash canonique de trace v2, `deny_read`
   exacts. `build` du nouvel outil a validé la structure des deux manifestes.
3. Ensemble **exact** des sources `.py` du bundle TARGET sous `backend`, hors
   composants de chemin commençant par `test`, égal aux entrées `tracked_code`
   de candidate; SHA-256 de chaque fichier égal aux octets LF indépendants.
   L'entrée `APP/backend/cca_eob_runtime.py` porte le hash du lanceur ci-dessus.
4. Invariant bridge/candidate :
   - A. entrées `dependency`, `interpreter` et `stdlib` **identiques** (chemin,
     hash, origine, accès) dans inventory, bridge et candidate;
   - B. ensemble des chemins `resource`, avec accès et origine, identique dans
     inventory, bridge et candidate; aucune ressource ajoutée ou retirée;
   - C. hash de chaque ressource de bridge (et d'inventory) égal aux octets du
     bundle LIVE authentifié;
   - D. hash de chaque ressource de candidate égal aux octets du bundle TARGET
     authentifié, y compris pour chaque ressource modifiée;
   - E. clone TARGET ayant servi au build identique au bundle TARGET authentifié,
     sources et ressources;
   - F. `deny_read`, `scope_review` et lien au hash de trace v2 identiques.
   Seuls les hashes des ressources modifiées entre LIVE et TARGET peuvent
   différer entre bridge et candidate. Chaque ligne `CHANGED_RESOURCE` doit
   correspondre à un fichier du diff runtime revu à l'étape 0, introduit par un
   commit intégré et revu entre `4866520e` et TARGET (`git log 4866520e..TARGET --
   CHEMIN`), dont la revue explique ce changement LIVE vers TARGET. Une ressource
   modifiée sans cette provenance = STOP. Pour un catalogue `.mo`, la cohérence
   avec son `.po` et la provenance de sa compilation sont des points de revue
   distincts : un `.mo` peut être recompilé depuis un `.po` inchangé, et un `.po`
   modifié ne prouve pas à lui seul que le `.mo` a été compilé correctement.
5. Rapport entier : démarrage effectif, sources chargées, `.pth`, fichiers sans
   RECORD, alias, modules paresseux/non chargés, ressources, bibliothèques natives,
   subprocessus et répertoires listables. La trace n'exécute jamais apply;
   `pg_restore` et la fermeture paresseuse restent soumis à la revue du contrat.

Le contrôle suivant est exécuté par le **relecteur privilégié**, avec le Python
système de confiance, sur R figé. Il n'importe aucun module de R, B, W ou APP;
il traite sources et ressources comme octets. Il est indépendant du résultat de
build. `W` est fourni seulement pour comparer le clone ayant servi au build;
`R/live-source` est le bundle LIVE authentifié de l'étape 0, extrait à l'étape 2.

```sh
sudo /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null - "$R/source" "$R" "$TARGET" "$W/src" "$R/live-source" <<'PY'
# CCA_EOB_CANDIDATE_DATA_CHECK
import hashlib
import json
from pathlib import Path
import sys


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field: " + key)
        result[key] = value
    return result


def load(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def entries(document):
    result = {}
    for entry in document["files"]:
        assert set(entry) == {"path", "sha256", "access", "origin"}
        assert entry["path"] not in result, "Duplicate manifest path"
        result[entry["path"]] = entry
    return result


def read_regular(root, relative):
    """Bytes of root/relative, refusing aliases on every path component."""
    current = root
    for part in relative.split("/"):
        assert part not in ("", ".", ".."), "Non-canonical resource path: " + relative
        current = current / part
        assert not current.is_symlink(), "Symlink in compared tree: " + relative
    assert current.is_file(), "Missing regular file: " + relative
    return current.read_bytes()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


source, evidence, target, clone = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
live = Path(sys.argv[5])
assert len(target) == 40 and all(c in "0123456789abcdef" for c in target)
trace = load(evidence / "trace-v2.json")
inventory = load(evidence / "inventory.json")
bridge = load(evidence / "bridge.json")
candidate = load(evidence / "candidate.json")
app = "/srv/polypbase/app"
assert trace["schema"] == "cca_eob_runtime_trace_v2" and trace["app"] == app
assert trace["runtime"] == "polypbase_cca_eob" and type(trace["uid"]) is int and trace["uid"] > 0
assert trace["no_new_privs"] is True and not trace["readable_bytecode"]
assert trace["startup"] == {"contract": "cca_eob_source_only_v1", "isolated": 1, "no_site": 1,
    "dont_write_bytecode": True, "pycache_prefix": "/dev/null", "site_initialized": True,
    "implementation": "cpython", "version": [3, 13]}
assert trace["deny_read"] and all(value is True for value in trace["deny_read"].values())
assert set(inventory) == {"files", "deny_read", "scope_review"}
assert inventory["deny_read"] == sorted(trace["deny_read"])
trace_hash = hashlib.sha256(json.dumps(trace, sort_keys=True).encode()).hexdigest()
assert "trace_sha256=" + trace_hash in inventory["scope_review"]
assert "target=" + target in inventory["scope_review"]
EXTERNAL = ("dependency", "interpreter", "stdlib")
reviewed = entries(inventory)
assert all(e["origin"] in EXTERNAL + ("resource",) for e in reviewed.values())
external = {p: e for p, e in reviewed.items() if e["origin"] in EXTERNAL}
resources = {p: e for p, e in reviewed.items() if e["origin"] == "resource"}
shape = {p: (e["access"], e["origin"]) for p, e in resources.items()}
built = {}
for name, manifest in (("bridge", bridge), ("candidate", candidate)):
    assert set(manifest) == {"schema", "runtime", "app", "files", "deny_read", "scope_review"}
    assert manifest["schema"] == "cca_eob_runtime_permissions_v1"
    assert manifest["runtime"] == "polypbase_cca_eob" and manifest["app"] == app
    # F: private probes and trace linkage are carried unchanged.
    assert manifest["deny_read"] == inventory["deny_read"]
    assert manifest["scope_review"] == inventory["scope_review"]
    actual = entries(manifest)
    assert all(e["origin"] in EXTERNAL + ("resource", "tracked_code") for e in actual.values())
    # A: external entries are exactly the reviewed inventory.
    assert {p: e for p, e in actual.items() if e["origin"] in EXTERNAL} == external, name + ": external drift"
    # B: same resource paths, access and origin; nothing added or removed.
    assert {p: (e["access"], e["origin"]) for p, e in actual.items()
            if e["origin"] == "resource"} == shape, name + ": resource set drift"
    built[name] = actual
changed = []
for path in sorted(resources):
    assert path.startswith(app + "/backend/"), "Resource outside backend: " + path
    relative = path[len(app) + 1:]
    live_hash = sha256(read_regular(live, relative))
    target_bytes = read_regular(source, relative)
    target_hash = sha256(target_bytes)
    # C: bridge (and the collected inventory) carry authenticated LIVE bytes.
    assert resources[path]["sha256"] == live_hash, "Inventory resource differs from LIVE: " + relative
    assert built["bridge"][path]["sha256"] == live_hash, "Bridge resource differs from LIVE: " + relative
    # D: candidate carries authenticated TARGET bytes, changed resources included.
    assert built["candidate"][path]["sha256"] == target_hash, "Candidate resource differs from TARGET: " + relative
    # E: the clone used by build is the authenticated TARGET bundle.
    assert read_regular(clone, relative) == target_bytes, "Clone differs from authenticated source: " + relative
    if live_hash != target_hash:
        changed.append("CHANGED_RESOURCE " + relative + " live=" + live_hash + " target=" + target_hash)
expected = {}
for path in (source / "backend").rglob("*.py"):
    relative = path.relative_to(source)
    if any(part.startswith("test") for part in relative.parts):
        continue
    assert path.is_file() and not path.is_symlink()
    built_from = clone / relative
    assert built_from.is_file() and not built_from.is_symlink()
    assert built_from.read_bytes() == path.read_bytes(), "Clone differs from authenticated source"
    name = app + "/" + relative.as_posix()
    expected[name] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "access": "r", "origin": "tracked_code"}
assert app + "/backend/cca_eob_runtime.py" in expected
assert {p: e for p, e in entries(candidate).items() if e["origin"] == "tracked_code"} == expected
print("\n".join(changed + ["CCA_EOB_CANDIDATE_DATA_OK"]))
PY
```

Exiger ce marqueur **et code 0**, puis conserver le résultat et les hashes revus.
Chaque ligne `CHANGED_RESOURCE` doit avoir la provenance exigée au point 4; avec
l'inventaire live examiné, au moins `backend/locale/fr/LC_MESSAGES/django.mo`
est attendue. Une ligne inattendue, ou l'absence d'une ressource modifiée
attendue dans le diff runtime, = STOP. Un candidate dont une ressource porte
encore le hash LIVE alors que TARGET diffère est refusé (défaut corrigé).
Ce contrôle de données ne remplace ni la provenance du bundle ni le contrôle
live/ACL suivant. Un échec ne se résout pas en modifiant le script de vérification
ni en éditant un hash du manifeste.

Puis publier **bridge seulement comme preuve temporaire** dans `B/bridge.json`,
root:polypbase 0640, SHA approuvé, mêmes contrôles d'ancêtres/ACL sans écriture.
Ce fichier n'est PAS le chemin de politique actif. Le nouvel outil peut ainsi
exécuter son contrôle complet existant maintenant, sous `polypbase` :

```sh
sudo -u polypbase env -i /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
  "$B/cca_eob_runtime_permissions.py" check --root "$APP" --manifest "$B/bridge.json"
```

Exiger `CCA_EOB_RUNTIME_PERMISSIONS_OK` et code 0 : sources et ressources
**live** exactes, dépendances exactes, ACL effectives, refus de propriété runtime, traversée sans
écriture, sondes privées refusées et politique root protégée. Ce contrôle
n'importe ni l'application ni DB et ne répare rien. Il ne prouve pas encore les
ACL du futur lanceur sous APP. Toute erreur = STOP, décision nominative séparée,
nouvelle trace/revue si l'environnement a changé. L'ancien manifeste actif
n'a toujours pas été remplacé. Conserver bridge et candidate distinctement.

## 6. Installer candidate — APPROBATION MANIFESTE distincte

Présenter digest exact de `R/candidate.json`, revue signée/référencée, résultat
bridge, hash source du lanceur, état live inchangé et plan de reprise/retour.
**Seulement après autorisation explicite de ce remplacement protégé** :

```sh
sudo test ! -e "$B/active-install.pending.json"
sudo test ! -L "$B/active-install.pending.json"
sudo install -m 0640 -o root -g polypbase "$R/candidate.json" "$B/active-install.pending.json"
sudo sha256sum "$B/active-install.pending.json"
sudo getfacl -cpn "$B/active-install.pending.json"
```

Exiger code 0, digest approuvé, fichier réel root:polypbase 0640, lien unique et
ACL sans écriture non propriétaire, ancêtres protégés. B et le manifeste actif
doivent être sur le **même système de fichiers** (vérifier `stat -c %d`); sinon
STOP. Seulement après ces vérifications, publication par renommage atomique :

```sh
sudo mv -T -- "$B/active-install.pending.json" /etc/polypbase/cca-eob-runtime-permissions-v1.json
sudo sha256sum /etc/polypbase/cca-eob-runtime-permissions-v1.json
```

Comparer au digest approuvé, contrôler fichier réel/liens uniques, propriétaire
et ACL effectives ainsi que tous les ancêtres root. Aucun changement d'ACL ou
de compte n'est implicite dans cet accord. Ne pas remplacer avec bridge ou le
candidat ancien. L'installation ne déploie rien, ne redémarre rien et ne permet
aucune migration. Avant le checkout cible, un check contre candidate **doit
refuser** les anciennes sources : c'est attendu, pas une raison de l'affaiblir.

## 7. Reprise par l'orchestration uniquement — APPROBATION RELEASE distincte

Avant la reprise, obtenir les validations Linux 3.13 isolées, vérifier à nouveau
TARGET = `origin/main`, CI officielle réussie sur ce SHA exact, état live propre,
absence de concurrence et dépendances inchangées. Faire revoir la release et
son plan de migrations, **dont les deux migrations encore non appliquées
`accounts.0008_interface_language_japanese` et
`measurements.0015_iminilide_probe_collection`**. Ce document les consigne
seulement; il ne les implémente ni ne les autorise. Un
succès du garde autorise le script à continuer vers les étapes ordinaires de
migration : **ne pas relancer tant que cette poursuite n'est pas explicitement
autorisée**. L'accord manifeste ne vaut pas accord release/migration ou SQL CCA.

La reprise passe par `deploy/deploy_vm.ps1 -UseVerifiedGitHubCi` avec les accords
habituels et, si requis par le plan, `-ReviewedMigrationApproval COMMIT:SHA256`.
Ne pas appeler manuellement `migrate`, modifier SQL, copier des sources dans APP,
faire un checkout à la main ou déplacer le garde. Aucune commande de release
n'est exécutée par la préparation de ce document.

Le script fait son fast-forward vers TARGET et son sync verrouillé, puis le
**même garde strict** contrôle candidate contre les vrais fichiers. Il passe
uniquement si sources, ressources, dépendances, chemins et ACL correspondent
tous. Un hash
ou une permission manquante reste un échec avant migrations/redémarrage.

### Échecs et reprise sans contournement

- **Avant installation active :** garder l'ancien manifeste; abandonner les
  sorties partielles et recommencer dans un nouveau W après diagnostic approuvé.
  Ne pas réutiliser une trace en erreur ou écraser les preuves d'une tentative.
- **Installation active interrompue ou contrôle post-installation en échec :**
  aucune release. Identifier par hash/propriétaire/ACL quelle politique est
  réellement active et si le pending existe; conserver les deux états. Avant le
  renommage réussi, l'ancienne politique reste active. Après renommage, ne pas
  présumer un rollback. Toute nouvelle publication ou restauration exige son
  accord distinct; restaurer l'ancienne copie n'est envisageable qu'après preuve
  que checkout **et dépendances** correspondent encore à son état. Sinon rester
  bloqué et régénérer une politique valide. Ne jamais lancer la release pour
  « tester » un fichier incomplet ou non vérifié.
- **Nouveau lanceur sans lecture runtime après checkout :** STOP au garde.
  Vérifier son hash cible et ses ancêtres; une éventuelle ACL de lecture sur
  **ce seul fichier nommé**, sans écriture, récursion, défaut ou élargissement
  implicite du masque, exige un accord opérateur distinct. Rien ne l'applique
  automatiquement. Tout autre refus se traite fichier par fichier, avec revue.
- **Sync ou environnement différent :** ne pas retoucher les hashes pour accepter
  la dérive. Refaire trace/collect/build sur l'état constaté, revue indépendante
  et nouvel accord manifeste. Le lanceur externe B permet encore la trace;
  après checkout TARGET, les builds utilisent alors ces sources TARGET réelles.
- **Après correction nominative autorisée :** exécuter uniquement le check en
  lecture seule avec le checker cible, APP et manifeste actif. Exiger code 0;
  puis nouvelle autorisation avant toute reprise par l'orchestration ordinaire.
- **Échec ultérieur de release/migration :** suivre sa procédure d'incident,
  conserver logs/backup/état. Pas de rollback de sources/DB ni restauration
  automatique de l'ancien manifeste : il ne correspond pas à TARGET.

Après release approuvée, refaire la trace v2 avec
`APP/backend/cca_eob_runtime.py` livré, mêmes flags/identité/NoNewPrivs/sondes,
puis collecter **sans exclure ce lanceur livré**. Exiger le contrat identique,
aucune dérive de contenu et le garde réussi. Archiver ces preuves avant toute
décision séparée de retrait des copies temporaires; aucun nettoyage automatique.

## Limite de confiance et critères de clôture

Le compte `polypbase` contrôle toujours checkout et venv. Ces vérifications
ponctuelles ne protègent pas contre sa compromission ni contre une modification
concurrente après contrôle. Le bundle root authentifie les outils d'amorçage,
pas l'immuabilité de l'ensemble applicatif. Aucune protection H2 n'est revendiquée.

Preuves de QA et de clôture existantes, et leurs limites :

- Tests hors ligne des outils (`deploy/scripts/test_cca_eob_runtime_*.py`),
  dont dérivation des ressources depuis `--root` avec de vrais dépôts Git
  temporaires et ce contrôle de données sur bundles synthétiques. Ils ne
  prouvent ni les ACL, ni l'identité, ni la fermeture réelle sur la VM.
- Validation locale Windows CPython 3.13 décrite dans
  [la note runtime](cca-eob-v2-additive-transition-runtime.md#protection-bytecode-du-runtime-cca) :
  un succès Windows ne vaut pas preuve Linux.
- QA isolée Debian 12 : preuve hors dépôt, à présenter avec ses hashes au
  relecteur. Elle ne reproduit pas le runtime Debian 13 complet de production
  (voir les limites de l'étape 0) et ne remplace ni la trace ni la collecte
  Debian 13.
- Revue Debian 13 de production rapportée par l'opérateur (« Revue Debian 13 de
  production » ci-dessus) : obtenue avec l'outil révoqué `831d9ef8…`, elle a
  révélé le défaut des ressources TARGET; elle n'est pas une preuve pour le
  nouvel outil ni pour le nouveau TARGET.

Clôture du blocage documentaire : source/hash, publication root et droits effectifs,
trace v2, bridge live, candidate TARGET, revue, autorisations et échecs sont
spécifiés. **La production reste bloquée** sans les résultats Linux 3.13, la
provenance/fermeture réelle, les preuves opérateur fraîches et les accords distincts
manifeste, droits éventuels et release/migrations. Ne pas confondre revue du patch
et feu vert de production.
