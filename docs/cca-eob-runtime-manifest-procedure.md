# CCA-EOB — reprise gardée et manifeste source-only

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
- nouveau lanceur absent; `measurements.0015` non appliquée.

Les preuves anciennes `-I -B` ne prouvent pas l'absence de consommation des
caches. Ne pas réinstaller le candidat 951 entrées, supprimer les caches, enlever
le compte dédié, désactiver le garde ou poursuivre les étapes de release à la main.

### Pourquoi la séquence n'est pas circulaire

1. Un bundle de sources du **futur commit cible intégré et revu** est authentifié
   indépendamment du checkout live. Un seul lanceur est publié hors checkout,
   sous contrôle root, avant l'achèvement de la release.
2. Ce lanceur produit une trace v2 avec le venv live, sans DB. Un manifeste frais
   **bridge**, construit avec les sources live, vérifie maintenant les dépendances,
   ACL et sondes privées. Il n'est jamais installé comme manifeste actif cible.
3. Le manifeste **candidate** utilise les sources exactes de la cible et exactement
   le même inventaire de dépendances. Ses sources sont vérifiées contre le bundle
   indépendant, pas contre des fichiers futurs inexistants sur le live.
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
```

`TARGET` dans cette commande est à remplacer par le vrai SHA de 40 caractères.
Le relecteur indépendant conserve ce SHA, le SHA-256 de l'archive, sa liste de
membres, le diff runtime et les résultats Linux 3.13. Les valeurs attendues ne
proviennent pas d'un fichier de checksums produit sur le live par `polypbase`.

Empreintes des sources **LF** du présent correctif (à retrouver dans les blobs
Git de TARGET; toute différence exige nouvelle revue et nouvelles empreintes).
Le lanceur ci-dessous inclut le refus de `--app` hors trace : cette révision est
une candidate en attente de revue de sécurité indépendante, pas un artefact
approuvé pour la production. Les empreintes des deux outils restent inchangées :

```text
0d125888059a97a7e8641f041ee10422aebec91258e43306ebd135b5f2daea42  backend/cca_eob_runtime.py
aba1abdc0b2dd8d41f424ba4e3a295f272334804a55a37da947a3fc43d1a9653  deploy/scripts/cca_eob_runtime_inventory.py
831d9ef85b2e4a26266a0ba07bc556e0428cf393e85f17f0968af3dbbd637ac5  deploy/scripts/cca_eob_runtime_permissions.py
```

**STOP** sauf si le diff runtime depuis `4866520e` se limite au nouveau lanceur
et au garde de `backend/config/cca_eob_production_settings.py`. Les tests/docs
peuvent changer, pas le reste de la fermeture runtime. `pyproject.toml`, `uv.lock`
et les sources métier doivent être identiques. Sinon produire d'abord une preuve
sur une installation isolée complète de TARGET; ne pas extrapoler la trace live.

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
inattendu) et l'extraire comme **données** dans `R/source`. Ne jamais exécuter de
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

## 5. Vérification indépendante — APPROBATION PREUVES, pas installation active

Copier trace, inventory, report, bridge et candidate dans R sous root:root 0600
après accord de conservation des preuves. Comparer leurs hashes aux sorties W;
revoir ces **copies figées**, pas des fichiers encore modifiables dans W. Conserver
la liste des hashes, versions Python/uv, commit live/TARGET, heures et décisions.
Ne pas exécuter de code backend en root pour vérifier ces documents.

Le relecteur indépendant exige :

1. Hashes du bundle et des trois outils égaux aux preuves de l'étape 0; clone
   TARGET égal aux sources authentifiées; delta runtime limité à l'étape 0.
2. JSON bien formés, aucun doublon de chemin, schéma/identité/app attendus,
   `scope_review` référençant TARGET et le hash canonique de trace v2, `deny_read`
   exacts. `build` du nouvel outil a validé la structure des deux manifestes.
3. Ensemble **exact** des sources `.py` du bundle TARGET sous `backend`, hors
   composants de chemin commençant par `test`, égal aux entrées `tracked_code`
   de candidate; SHA-256 de chaque fichier égal aux octets LF indépendants.
   L'entrée `APP/backend/cca_eob_runtime.py` porte le hash du lanceur ci-dessus.
4. Entrées non `tracked_code` de bridge et candidate **identiques**, chemin,
   hash, origine et accès; exactement celles d'inventory. Aucune substitution
   de dépendance, de `deny_read` ou d'accès entre les deux builds.
5. Rapport entier : démarrage effectif, sources chargées, `.pth`, fichiers sans
   RECORD, alias, modules paresseux/non chargés, ressources, bibliothèques natives,
   subprocessus et répertoires listables. La trace n'exécute jamais apply;
   `pg_restore` et la fermeture paresseuse restent soumis à la revue du contrat.

Le contrôle suivant est exécuté par le **relecteur privilégié**, avec le Python
système de confiance, sur R figé. Il n'importe aucun module de R, B, W ou APP;
il traite les sources comme octets. Il est indépendant du résultat de build.
`W` est fourni seulement pour comparer les sources du clone ayant servi au build.

```sh
sudo /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null - "$R/source" "$R" "$TARGET" "$W/src" <<'PY'
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


source, evidence, target, clone = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], Path(sys.argv[4])
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
dependencies = entries(inventory)
assert all(e["origin"] != "tracked_code" for e in dependencies.values())
for manifest in (bridge, candidate):
    assert set(manifest) == {"schema", "runtime", "app", "files", "deny_read", "scope_review"}
    assert manifest["schema"] == "cca_eob_runtime_permissions_v1"
    assert manifest["runtime"] == "polypbase_cca_eob" and manifest["app"] == app
    assert manifest["deny_read"] == inventory["deny_read"]
    assert manifest["scope_review"] == inventory["scope_review"]
    actual = entries(manifest)
    assert {p: e for p, e in actual.items() if e["origin"] != "tracked_code"} == dependencies
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
print("CCA_EOB_CANDIDATE_DATA_OK")
PY
```

Exiger ce marqueur **et code 0**, puis conserver le résultat et les hashes revus.
Ce contrôle de données ne remplace ni la provenance du bundle ni le contrôle
live/ACL suivant. Un échec ne se résout pas en modifiant le script de vérification.

Puis publier **bridge seulement comme preuve temporaire** dans `B/bridge.json`,
root:polypbase 0640, SHA approuvé, mêmes contrôles d'ancêtres/ACL sans écriture.
Ce fichier n'est PAS le chemin de politique actif. Le nouvel outil peut ainsi
exécuter son contrôle complet existant maintenant, sous `polypbase` :

```sh
sudo -u polypbase env -i /usr/bin/python3 -I -S -B -X pycache_prefix=/dev/null \
  "$B/cca_eob_runtime_permissions.py" check --root "$APP" --manifest "$B/bridge.json"
```

Exiger `CCA_EOB_RUNTIME_PERMISSIONS_OK` et code 0 : sources **live** exactes,
dépendances exactes, ACL effectives, refus de propriété runtime, traversée sans
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
son plan de migrations, **dont `measurements.0015` encore non appliquée**. Un
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
uniquement si sources, dépendances, chemins et ACL correspondent tous. Un hash
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

Clôture du blocage documentaire : source/hash, publication root et droits effectifs,
trace v2, bridge live, candidate TARGET, revue, autorisations et échecs sont
spécifiés. **La production reste bloquée** sans les résultats Linux 3.13, la
provenance/fermeture réelle, les preuves opérateur fraîches et les accords distincts
manifeste, droits éventuels et release/migrations. Ne pas confondre revue du patch
et feu vert de production.
