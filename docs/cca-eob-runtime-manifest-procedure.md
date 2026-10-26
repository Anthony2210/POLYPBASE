# CCA-EOB — provisionner le manifeste runtime (opérateur)

Débloque le garde `CCA-EOB runtime read permissions` de `deploy_release.sh`
sans l'affaiblir. Contrat, limites et vérifications : voir
[`cca-eob-v2-additive-transition-runtime.md`](cca-eob-v2-additive-transition-runtime.md).
Cette procédure **ne modifie aucune ACL** et ne lance ni migration, ni correction,
ni connexion PostgreSQL. Les ACL déjà corrigées à la main ne sont pas rejouées.

Outils (dans le commit cible) :

- `deploy/scripts/cca_eob_runtime_inventory.py trace` : sous `polypbase_cca_eob`,
  `NoNewPrivs=1`, environnement vide; charge settings CCA, commande, backend
  PostgreSQL (sans connexion), fuseau et traductions; relève modules, fichiers
  ouverts, fichiers mappés (`/proc/self/maps` : extensions natives, bibliothèques,
  loader) et vérifie que les sondes privées sont illisibles.
- `... collect` : sous `polypbase`, jamais root, sans importer l'application;
  résout liens et cibles réelles, classe, hache, vérifie chaque fichier de
  dépendance contre le `RECORD` de sa distribution, ajoute `pyvenv.cfg` et les
  `.pth`. Tout fichier non classable, lien, chemin privé, source backend non
  suivie ou écart de `RECORD` **arrête** l'outil. Sorties exclusives 0600 :
  inventaire (`files`/`deny_read`/`scope_review`) et rapport de revue.
- `cca_eob_runtime_permissions.py build` puis `check` : contrats existants inchangés.

Décisions explicites possibles en revue, jamais implicites :
`--accept-alias CHEMIN`, `--extra ORIGINE:r|rx:/chemin/réel`, `--exclude CHEMIN`,
`--include-distribution NOM` (fermeture complète d'une distribution chargée
paresseusement).

## 0. Poste local (PowerShell) — commit cible

Après revue, fusion et push de cette branche, depuis la racine du dépôt sur `main` :

```powershell
git fetch --quiet origin main
$Target = (git rev-parse HEAD).Trim()
if ($Target -ne (git rev-parse origin/main).Trim()) { throw 'main differs from origin/main' }
git diff --name-only 26d2043070e8a221d4bf2705367cafd5e94b2ee2 $Target -- backend pyproject.toml uv.lock
uv run python -B deploy/scripts/test_cca_eob_runtime_permissions.py
uv run python -B deploy/scripts/test_cca_eob_runtime_inventory.py
$Target
```

**STOP si le `git diff` liste un fichier** : le checkout live (26d2043) ne
représenterait plus le code de la cible; déployer d'abord une cible sans
changement backend/dépendances ou refaire la procédure après mise à jour.

## 1. VM (PuTTY, `aquariumparis`) — preuves en lecture seule

Ne pas utiliser `set -e` dans la session interactive. Remplacer `TARGET`.

```sh
APP=/srv/polypbase/app
TARGET=<sha 40 caractères affiché à l'étape 0>
sudo -v
id polypbase_cca_eob
command -v getfacl setpriv runuser; /usr/bin/python3 --version
sudo -u polypbase git -C "$APP" rev-parse HEAD
sudo -u polypbase git -C "$APP" status --porcelain
sudo ls -ld /etc/polypbase; sudo getfacl -p /etc/polypbase
sudo ls -l /etc/polypbase/cca-eob-runtime-permissions-v1.json
sudo ls -l "$APP/backend/.env"
readlink -f "$APP/.venv/bin/python"
```

Attendu : compte présent, outils présents, Python système ≥ 3.9, HEAD
`26d2043070e8a221d4bf2705367cafd5e94b2ee2`, statut vide, manifeste absent.
`/etc/polypbase` doit exister, appartenir à `root`, sans écriture groupe/autres
ni ACL par défaut ou d'écriture nommée. S'il est absent, sa création
(`sudo install -d -m 0755 -o root -g root /etc/polypbase`) est une décision
séparée. Choisir les sondes `--deny-read` parmi des fichiers privés **réels**
(au minimum le `.env` effectivement utilisé); ne jamais afficher leur contenu.

## 2. Copie propre du commit cible (sous `polypbase`)

`git fetch` ne met à jour que `origin/main`, comme le préflight du déploiement.

```sh
sudo -u polypbase git -C "$APP" fetch --quiet origin main
sudo -u polypbase git -C "$APP" rev-parse origin/main
W=$(sudo -u polypbase mktemp -d /tmp/cca-eob-inventory.XXXXXXXX); echo "$W"
sudo -u polypbase git clone --quiet --no-hardlinks --no-checkout "$APP" "$W/src"
sudo -u polypbase git -C "$W/src" -c advice.detachedHead=false checkout --quiet --detach "$TARGET"
sudo -u polypbase git -C "$W/src" rev-parse HEAD
```

`origin/main` et `HEAD` du clone doivent valoir `$TARGET`; sinon STOP.

## 3. Trace sous le runtime dédié — ⛔ APPROBATION A

Exécute les imports applicatifs sous `polypbase_cca_eob`, sans connexion DB,
depuis le checkout et le venv live. Le script est passé sur l'entrée standard :
le runtime n'a besoin d'aucun accès à `$W`.

```sh
cd /
sudo -u polypbase cat "$W/src/deploy/scripts/cca_eob_runtime_inventory.py" \
  | sudo runuser -u polypbase_cca_eob -- setpriv --no-new-privs -- env -i \
      "$APP/.venv/bin/python" -I -B - trace --app "$APP" --deny-read "$APP/backend/.env" \
  | sudo -u polypbase tee "$W/trace.json" >/dev/null
echo "${PIPESTATUS[@]}"
```

Lancer `sudo -v` juste avant si le cache sudo a expiré (deux `sudo` du pipeline
ne doivent pas demander le mot de passe simultanément). Attendu : `0 0 0`. Un `PermissionError` désigne un accès runtime manquant :
**STOP**, aucune correction automatique; tout ajustement ACL est une décision
séparée, puis relancer la trace.

**Lecture des répertoires.** Les imports (`FileFinder`) et `site.py` *listent*
certains répertoires (`sys.path`, paquets, site-packages) et peuvent tolérer un
refus en silence (`.pth` ignorés, module introuvable). Ces répertoires exigent
`r+x`; les simples ancêtres des fichiers inventoriés n'exigent que `x`. La trace
relève chaque répertoire listé puis le **relit elle-même sous
`polypbase_cca_eob`** (`os.listdir`) : preuve des droits effectifs (UID, groupes,
ACL, masque), pas des déclarations. Si l'un est refusé, elle écrit la trace puis
sort avec le code `3` et `STOP: the runtime cannot list required directories`;
`collect` refuse aussi cette trace. **STOP.** Correction éventuelle =
⛔ **APPROBATION E** séparée : pour chaque répertoire nommé seulement, entrée
nominative non récursive `r-x`, sans ACL par défaut ni élargissement du masque
(`setfacl -n -m u:polypbase_cca_eob:r-x -- REPERTOIRE`), revue avant exécution.
Jamais de `chmod`/`setfacl` récursif, de droit `o+r`, de groupe partagé ni de
lecture accordée à un répertoire non listé par la trace. Lister un répertoire
révèle des noms, pas des contenus : les sondes `--deny-read` restent refusées.
Puis relancer la trace.

## 4. Inventaire et revue — ⛔ APPROBATION B

```sh
sudo -u polypbase env -i /usr/bin/python3 -I -S -B \
  "$W/src/deploy/scripts/cca_eob_runtime_inventory.py" collect --app "$APP" \
  --trace "$W/trace.json" --output "$W/inventory.json" --report "$W/report.txt" \
  --scope-review "CCA-EOB runtime closure for $TARGET, reviewed by Anthony $(date -u +%F)" \
  --accept-alias "$APP/.venv/bin/python"
```

En cas de refus, chaque ligne exige une décision justifiée, puis relance de la
même commande avec l'option correspondante. Cas attendus sous Debian (vérifiés
sur un Ubuntu jetable, à confirmer sur la VM) : lien
`/usr/lib/python3.X/sitecustomize.py` → `--accept-alias` + `--extra
stdlib:r:/etc/python3.X/sitecustomize.py`; `/usr/share/zoneinfo/Europe/Paris`
→ `--extra stdlib:r:...`; `/usr/lib/locale/C.utf8/LC_CTYPE` et
`.../gconv/gconv-modules.cache` → `--extra interpreter:r:...`.
Ne jamais accepter un refus `private or mixed path`, `runtime owns`,
`RECORD` ou `outside the tracked_code inventory` : STOP.

```sh
sudo -u polypbase less "$W/report.txt"
```

Relire **toutes** les sections `REVIEW`/`WARNING` : distributions utilisées et
non chargées (paresseuses ? `--include-distribution`), extras, alias acceptés,
fichiers sans hash `RECORD`, bibliothèques natives, sous-processus, caches
bytecode non root lisibles par le runtime (doivent être 0 ou justifiés; la
trace ne lance jamais `apply`, donc `/usr/lib/postgresql/18/bin/pg_restore`
n'y figure pas : voir le contrat runtime pour l'exécutable externe),
répertoires que le runtime doit lister (`r+x`, prouvés listables sous le runtime
par la trace; vérifier qu'aucun n'est inattendu).

## 5. Manifeste et contrôle à blanc — ⛔ APPROBATION C (écriture root dans `/etc`)

```sh
sudo -u polypbase sh -c 'env -i /usr/bin/python3 -I -S -B "$1/src/deploy/scripts/cca_eob_runtime_permissions.py" build --root "$1/src" --app /srv/polypbase/app --inventory "$1/inventory.json" > "$1/manifest.json"' sh "$W"; echo "build=$?"
sudo install -d -m 0700 -o root -g root /root/cca-eob-runtime
for f in trace.json inventory.json report.txt manifest.json; do sudo install -m 0600 -o root -g root "$W/$f" "/root/cca-eob-runtime/$TARGET-$f"; done
M="/root/cca-eob-runtime/$TARGET-manifest.json"
sudo sha256sum "$M"
sudo /usr/bin/python3 -I -S -B -c 'import collections, json, sys; m = json.load(open(sys.argv[1])); print(m["app"], m["deny_read"], m["scope_review"], sep="\n"); print(collections.Counter((e["origin"], e["access"]) for e in m["files"]))' "$M"
```

Attendu : `build=0`, `app` = `/srv/polypbase/app`, origines `tracked_code`,
`dependency`, `stdlib`, `interpreter` présentes. Après approbation du hash :

```sh
sudo install -m 0640 -o root -g polypbase "$M" /etc/polypbase/cca-eob-runtime-permissions-v1.json
cd / && sudo -u polypbase "$APP/.venv/bin/python" -B "$APP/deploy/scripts/cca_eob_runtime_permissions.py" \
  check --root "$APP" --manifest /etc/polypbase/cca-eob-runtime-permissions-v1.json
```

Attendu : `CCA_EOB_RUNTIME_PERMISSIONS_OK` (même commande que le garde du
déploiement). Tout autre résultat : STOP, le message nomme le fichier; aucune
réparation automatique. **Ce résultat seul ne prouve pas la complétude des droits
de lecture des répertoires** : le garde ne contrôle que `x` sur les ancêtres.
Cette preuve est la trace de l'étape 3 terminée avec `0 0 0` sur le même état. Pour revenir en arrière, supprimer le manifeste :
le garde bloque de nouveau (le compte existe), rien n'est affaibli.

## 6. Déploiement officiel (PowerShell) — ⛔ APPROBATION D

Depuis le même checkout qu'à l'étape 0 (`main`, `$Target` inchangé), avec `gh`
authentifié sur le compte de l'opérateur :

```powershell
if ((git rev-parse HEAD).Trim() -ne $Target) { throw 'Local HEAD is not the reviewed target' }
.\deploy\deploy_vm.ps1 -PreflightOnly -UseVerifiedGitHubCi
.\deploy\deploy_vm.ps1 -UseVerifiedGitHubCi
```

Le script exige `HEAD` = `origin/main` et exactement un run CI officiel `push`
sur `main` pour ce SHA exact, terminé en succès avec les jobs `Frontend checks`
et `Backend checks`; vérifier que le SHA affiché vaut `$Target`. Un refus est un
STOP : ne pas retirer l'option sans décision séparée. Ajouter
`-ReviewedMigrationApproval COMMIT:SHA256` seulement si le plan de migrations
affiché l'exige, après sa revue.

Après déploiement, `uv sync` a pu recréer des répertoires : sur la VM, relancer
la commande de l'étape 3 vers `"$W/trace-post.json"`; attendu `0 0 0`, sinon STOP
(approbation E). Ensuite : `sudo rm -rf -- "$W"` (les preuves restent dans
`/root/cca-eob-runtime`).
Toute release modifiant un `.py` backend, `uv.lock` ou l'interpréteur exige de
refaire les étapes 2 à 5 **avant** son déploiement.
