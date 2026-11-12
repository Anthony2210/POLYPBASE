# Déploiement de Polypbase sur la VM

Ce document décrit l’état vérifié du serveur Polypbase au 24 août 2026 et les
opérations restantes. Les compléments ultérieurs sont datés dans leur section. Les mots de passe, clés SSH, clés Django et identifiants
de base de données ne doivent jamais être ajoutés au dépôt.

## État actuel

| Élément | État |
|---|---|
| Système | Debian GNU/Linux 13, `aquariumparis01` |
| Dépôt | `/srv/polypbase/app`, branche `main` |
| Compte de service | `polypbase` |
| Backend | Django servi par Gunicorn sur `127.0.0.1:8000` |
| Frontend | Build Vite servi par Nginx |
| Nginx | Actif, ports publics 80 et 443 autorisés par UFW |
| PostgreSQL de production | Version 18, cluster `18/main`, port local 5433 |
| Base active | `polypbase`, rôle local `polypbase`, authentification `peer` |
| Ancien cluster local | PostgreSQL 17, vide, encore présent sur le port 5432 |
| Clients PostgreSQL | `/usr/bin/pg_dump`/`pg_restore` = `pg_wrapper` Debian; sans `--cluster`, `/usr/bin/pg_restore --version` sélectionne 17.10. Clients 18 : `/usr/lib/postgresql/18/bin/` (exigés explicitement par `backup_database.py` et le contrôle d'archive de release; CCA-EOB, retiré, les utilisait aussi) |
| Domaine | `polypbase.org` et `www.polypbase.org` dirigés vers la VM |
| HTTPS | Actif sur les deux noms, avec redirection HTTP vers HTTPS |
| Déploiement | Automatisé et contrôlé par `deploy/deploy_vm.ps1` |

Les services suivants sont installés, activés au démarrage et opérationnels :

```bash
systemctl is-active polypbase nginx postgresql
systemctl is-enabled polypbase nginx postgresql
```

Le frontend répond en HTTPS sur le domaine public. Gunicorn et les
deux clusters PostgreSQL n’écoutent que sur l’interface locale.

## Base historique nettoyée

Une sauvegarde complète de Neon a été créée avec PostgreSQL 18, contrôlée avec
`pg_restore --list`, puis restaurée dans une base isolée. Le nettoyage a été
appliqué uniquement à cette copie, jamais à Neon.

État final vérifié :

- 1 structure : Aquarium de Paris
- 2 comptes conservés : `admin` et `antho_ca`
- 6 zones thermiques
- 555 boîtes historiques
- 1 391 périodes d’emplacement historiques
- 36 779 relevés biologiques historiques
- aucune donnée de test postérieure à l’import du 3 juillet 2026
- aucun déplacement, repiquage, transfert, relevé de température ou alerte de test
- une entrée d’audit retraçant la restauration et l’empreinte de la sauvegarde source

Les scripts utilisés sont protégés : le nettoyage refuse de s’exécuter si le
nom de base ou les nombres attendus ne correspondent pas à la photographie
auditée.

```bash
cd /srv/polypbase/app/backend

../.venv/bin/python ../deploy/scripts/clean_staging_database.py
../.venv/bin/python ../deploy/scripts/verify_staging_database.py \
  --expected-database polypbase
```

Le premier appel est une simulation. L’option `--apply` ne doit être utilisée
que sur `polypbase_staging` après une nouvelle sauvegarde.

## Sauvegardes conservées

Les sauvegardes sont stockées dans `/srv/polypbase/backups`, avec des droits
réservés au compte `polypbase` :

- sauvegarde Neon originale
- copie de la base de préparation avant nettoyage
- sauvegarde propre ayant servi à créer la base de production
- sauvegardes quotidiennes de production

Les archives privées du broker de sauvegarde de déploiement sont distinctes :
`/var/lib/polypbase/deployment-backups` doit être sous un ancêtre root:root
0755, et le répertoire lui-même `polypbase_backup:polypbase_backup` 0700.
Elles ne doivent jamais être rendues lisibles par `polypbase`. Les sauvegardes
quotidiennes et leur rétention restent dans `/srv/polypbase/backups`.

Le minuteur `polypbase-backup.timer` exécute une sauvegarde vérifiée chaque nuit
vers 2 h 30 et conserve 14 jours de sauvegardes portant le préfixe `polypbase`.

Le script d'exploitation `deploy/scripts/backup_database.py` exige désormais
`/usr/lib/postgresql/18/bin/pg_dump` et `pg_restore`, sans repli vers `pg_wrapper`.
Les deux clients et leurs répertoires parents doivent appartenir à root, ne pas
être des liens symboliques ni être modifiables par le groupe ou les autres ; les
binaires doivent être exécutables et annoncer PostgreSQL 18. Tout refus arrête
la sauvegarde avant la création de l'archive et sans appliquer la rétention.
L'unité systemd existante utilise ce comportement dès la mise à jour du script,
sans modification de son `ExecStart`. Confirmer sur la VM la présence et les
droits des deux clients avant cette mise à jour. Les helpers PCA conservent
leurs clients par défaut historiques ; ce changement ne modifie pas leur contrat.

```bash
systemctl status polypbase-backup.timer --no-pager
systemctl list-timers polypbase-backup.timer --no-pager
journalctl -u polypbase-backup.service -n 50 --no-pager
```

Une sauvegarde manuelle peut être lancée avec :

```bash
cd /srv/polypbase/app/backend
sudo -u polypbase ../.venv/bin/python \
  ../deploy/scripts/backup_database.py --label manual
```

Une copie hors de la VM doit aussi être organisée : une sauvegarde située sur
le même disque ne protège pas contre une panne complète de la machine.

## Configuration Django

Le fichier `/srv/polypbase/app/backend/.env` appartient à `polypbase`, avec le
mode `600`. La configuration Neon précédente est conservée dans une copie
protégée sur le serveur pour permettre un retour arrière contrôlé.

La base locale utilise les paramètres suivants, sans mot de passe réseau :

```dotenv
POSTGRES_DB=polypbase
POSTGRES_USER=polypbase
POSTGRES_PASSWORD=
POSTGRES_HOST=/var/run/postgresql
POSTGRES_PORT=5433
POSTGRES_SSLMODE=disable
```

Cette authentification fonctionne parce que Django et PostgreSQL utilisent le
même compte local `polypbase`. PostgreSQL n’est pas accessible depuis Internet.

Les paramètres publics attendus sont :

```dotenv
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=polypbase.org,www.polypbase.org,<IP_VM>,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://polypbase.org,https://www.polypbase.org
PUBLIC_BASE_URL=https://polypbase.org
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SECURE_HSTS_SECONDS=86400
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_DELIVERY_ENABLED=1
DEFAULT_FROM_EMAIL=Polypbase <noreply@polypbase.org>
EMAIL_HOST=mail.gandi.net
EMAIL_PORT=587
EMAIL_HOST_USER=noreply@polypbase.org
EMAIL_HOST_PASSWORD=<mot_de_passe_de_la_boite>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
EMAIL_TIMEOUT=10
```

**Écart HSTS non résolu (2026-11-04).** La clôture CCA-EOB du 2026-11-03 rapporte
`HSTS_SECONDS=0` / `security.W004` en production, alors que la valeur attendue
ci-dessus et la valeur par défaut du code (`backend/config/settings.py`) sont
`86400`. Le gabarit `deploy/nginx/polypbase.conf` contient aussi
`add_header Strict-Transport-Security "max-age=86400" always;`. L'avertissement
Django décrit le réglage Django : il ne permet pas de conclure que l'en-tête HSTS
est absent de la réponse HTTPS publique, car Nginx peut l'ajouter. La configuration
HTTPS active et les en-têtes effectivement servis n'ont pas été revérifiés ici ;
le gabarit ne prouve pas leur état réel. Cause et décision non établies : aucune
modification de configuration n'est décidée ici. `check --deploy` le signale comme
avertissement, sans arrêter la release.

## Déploiement automatisé

Le déploiement normal se lance depuis PowerShell, à la racine d’une copie locale
du dépôt alignée avec `origin/main` :

```powershell
.\deploy\deploy_vm.ps1
```

La commande effectue elle-même les opérations suivantes :

1. vérification de la branche `main`, de l’état Git et du commit poussé ;
2. contrôles Django, migrations manquantes, tests backend et build frontend ;
3. vérification de l’empreinte SSH publique de la VM ;
4. transfert et contrôle SHA-256 de l’exécuteur Linux versionné ;
5. verrou empêchant deux déploiements simultanés ;
6. sauvegarde PostgreSQL au format custom et validation avec `pg_restore` ;
7. mise à jour Git strictement en fast-forward vers le commit demandé ;
8. installation des dépendances verrouillées et build frontend dans un dossier
   de préparation distinct ;
9. plan de migration, migrations, fichiers statiques et contrôles Django ;
10. publication du frontend, redémarrage de Gunicorn et validation de Nginx ;
11. contrôles de santé, des routes React, de HTTPS et du commit réellement servi.

Pour la sauvegarde avant mise à jour, l'exécuteur extrait les deux fichiers
`backend/config/database_backup.py` et `deploy/scripts/backup_database.py` du
commit cible déjà récupéré et contrôlé, dans le dossier de release. Il utilise
ce code avec la configuration Django de la copie encore en place : la première
release bénéficie donc des clients 18 sans avancer Git avant la sauvegarde.
Le second contrôle d'archive utilise également le helper extrait, avec
`client_major=18`, ses contrôles de provenance et son délai maximal. Ces
contrôles de lisibilité ne remplacent pas un test de restauration.

### Garde CCA-EOB du runtime dédié

Après le fast-forward et la synchronisation des dépendances verrouillées, avant
le build frontend, les contrôles Django et les migrations,
`deploy/scripts/deploy_release.sh` exécute l'étape `CCA-EOB runtime read
permissions`. Elle est active **uniquement si** l'une de ces conditions est vraie :

- le compte Linux `polypbase_cca_eob` existe (`id -u polypbase_cca_eob`);
- le chemin `/etc/polypbase/cca-eob-runtime-permissions-v1.json` existe, y
  compris sous forme de lien symbolique.

Elle lance alors `deploy/scripts/cca_eob_runtime_permissions.py check`, qui
exige un manifeste protégé et les droits exacts, ne modifie aucune ACL et arrête
la release en cas d'échec (dépôt déjà avancé, migrations non appliquées).

Le runtime dédié a été retiré le 2026-11-04 (rapporté par l'opérateur : compte
et groupe supprimés, manifeste actif archivé, `CCA_EOB_DEPLOY_GUARD_CLEAR`).
Les deux conditions étant absentes, l'étape est sautée et ne bloque plus la
release. Si le compte ou le manifeste réapparaît, le contrôle redevient actif et
échoue sans manifeste revu valide. **Ne jamais recréer le compte ou le manifeste,
ni modifier ce garde, pour faire passer un déploiement** : arrêter et analyser.
Historique : [`cca-eob-runtime-manifest-procedure.md`](cca-eob-runtime-manifest-procedure.md);
état courant : [`POLYPBASE_CURRENT_STATE.md`](../POLYPBASE_CURRENT_STATE.md).

Le script s’arrête au premier échec. Il ne restaure jamais la base et n’annule
jamais une migration automatiquement. Le journal distant, la sauvegarde et
l’ancien build frontend sont conservés sous `/srv/polypbase` pour permettre un
diagnostic ou un retour arrière décidé explicitement.

Pour exécuter uniquement les contrôles locaux sans contacter la VM :

```powershell
.\deploy\deploy_vm.ps1 -PreflightOnly
```

### Reprise rapide après CI GitHub vérifiée (`-UseVerifiedGitHubCi`)

Option explicite, jamais activée par défaut. Elle évite de relancer les tests
Django locaux et le build frontend local lorsque la CI GitHub officielle a déjà
validé le commit exact. Elle exige `gh` installé et connecté (`gh auth login`) ;
aucun jeton n'est stocké ou accepté par le script. Si la vérification est
indisponible, ambiguë, incomplète ou en échec, le script s'arrête avant tout
accès à la VM : il n'y a aucun repli.

Conditions : exactement un run `push` sur `main` du workflow
`.github/workflows/ci.yml` pour le SHA exact, terminé en `success`, issu du dépôt
`origin`, avec exactement un job `Frontend checks` et un job `Backend checks`
terminés en `success`. Restent exécutés : branche, état Git, alignement avec
`origin/main`, `git diff --check`, `manage.py check`, dérive de migrations, ainsi que
toutes les étapes côté VM (dépendances, build frontend, contrôles Django, sauvegarde,
garde read-only du plan de migration, publication, redémarrage, santé).

```powershell
.\deploy\deploy_vm.ps1 -UseVerifiedGitHubCi
```

`-UseVerifiedGitHubCi -PreflightOnly` vérifie la CI sans modifier la VM.

Le chemin de clé SSH doit être fourni avec `-SshKeyPath` ou, si ce paramètre
est absent ou vide, avec `POLYPBASE_SSH_KEY`. Sans l'un de ces deux réglages,
`deploy/deploy_vm.ps1` s'arrête avec une erreur : aucun chemin personnel de secours
n'est utilisé. La clé et son contenu ne sont jamais copiés dans le dépôt.

### Approbation explicite d'un plan de migration revu

Le déploiement normal est désormais **strictement read-only pour les migrations** :
`migrate --check` refuse toute migration en attente, quelle que soit son contenu.
Il n'applique jamais de migration et le script de release n'accepte plus aucun
paramètre d'approbation de migration.

Pour toute exécution dédiée, 0018 comme toute migration future, suivre la procédure
[de contrôle authentifié et de reprise](dedicated-migration-recovery.md). Aucune
opération, même personnalisée ou apparemment additive, ne reçoit d'approbation
implicite. Le plan d'approbation dédié est un fichier JSON déterministe ; le
`migrate-plan.txt` textuel du déploiement n'est pas interchangeable avec lui.
Un déploiement sans migration en attente continue normalement. Une migration en
attente bloque avant publication, redémarrage ou contrôles de santé, puis doit
passer par l'exécuteur dédié avec une nouvelle revue humaine.

L'exécuteur conserve `migrate-plan.txt` dans le dossier de release pour diagnostic.
Son contenu peut décrire n'importe quelle opération Django : aucun mot-clé ou
résumé textuel ne modifie la décision de blocage. Le déploiement normal ne
produit jamais `MIGRATION_REVIEW_APPROVED` et n'exécute jamais `migrate`.

En état intermédiaire code/schéma, ne pas prendre un échec d'un diagnostic utilisant
un champ encore non migré pour une preuve d'anomalie des données. Pour la release
ci-dessus, le diagnostic Phase 3B utilisant `Organization.portable_id` n'est pas
utilisable avant `organizations.0002`; la migration taxonomy conserve son propre
précontrôle de doublons sur les modèles historiques. La procédure de migration dédiée ne court-circuite pas ce précontrôle.

### Installation PostgreSQL Box 1902 et reprise d'une release préparée

L'installation de `measurements.0018_box_1902_preservation` est une opération
distincte du déploiement applicatif. Le compte `polypbase` ne doit jamais
exécuter une migration et ne reçoit aucun privilège de propriétaire protégé.
Le SQL de `deploy/scripts/box_1902_privileges.py provision` est seulement
émis pour revue : son exécution exige une autorisation séparée du
cluster-admin et une nouvelle sauvegarde vérifiée. Il refuse toute collision,
réutilisation ou reprovisionnement partiel. Après exécution, vérifier en
lecture seule les rôles, la base, les propriétaires, les ACL et l'absence de
CREATE/TEMP pour `polypbase`; ne pas poursuivre si une vérification échoue.

La connexion de migration doit ensuite être authentifiée indépendamment de
`polypbase` (mot de passe ou certificat administré hors dépôt, jamais un secret
dans la commande ou Git). Le rôle PostgreSQL doit être réellement
`box1902_migrator`, avec son unique appartenance SET-only à
`box1902_owner`. `PGOPTIONS` demande explicitement `SET ROLE
box1902_owner`; l'exécuteur vérifie avant et après migration
`session_user=box1902_migrator`, `current_user=box1902_owner`,
`session_replication_role=origin`, ainsi que l'enregistrement de 0018.
L'authentification locale peer actuelle de `polypbase` ne satisfait pas ce
contrat et ne doit pas être contournée par superutilisateur, `SET SESSION
AUTHORIZATION` ou une modification permissive.

**Ne pas lancer de runner depuis le checkout `8504f52`**, où il n'existe pas,
ni transférer un fichier non suivi. Exporter les contrôles depuis un commit Git
complet déjà intégré et indépendamment revu, puis vérifier leurs empreintes selon
[la procédure exécutable](dedicated-migration-recovery.md). Elle maintient le
checkout applicatif intact et distingue le commit de contrôle du commit à migrer.
Les identités OS de migration/sauvegarde et leurs secrets sont distincts du service
web ; l'ancien exemple `sudo -u polypbase` ne fournit pas cette isolation et ne doit
pas être utilisé.

Le résultat requis est `MIGRATION_COMPLETE`; toute autre sortie, toute
discordance d'identité, tout plan différent ou toute erreur arrête la
procédure. Ce script n'effectue ni provisioning de rôles ni restauration et
ne modifie pas Git. Une erreur laisse la base et la release intermédiaires
pour diagnostic read-only; aucune migration n'est annulée automatiquement.

Après vérification indépendante de `django_migrations`, reprendre le
déploiement pour le même commit. Le déploiement contrôle désormais le plan avec
`migrate --check` sans exécuter de DDL sous
`polypbase`, puis publie le frontend, redémarre le service et réalise les
contrôles de santé. Si le contrôle trouve encore une migration en attente,
arrêter et repasser par l'exécuteur dédié; ne jamais ajouter un privilège au
runtime. Les futures migrations suivent la même séparation et exigent une nouvelle revue
humaine de leur commit exact et de leur plan JSON exact, même pour un simple ajout. Une release
partiellement préparée peut être reprise seulement si le checkout est propre,
le commit cible exact, la sauvegarde et le journal précédents sont conservés,
et les contrôles de plan/identité passent.

Tests locaux isolés, sans VM ni base :

```powershell
python -m unittest discover -s deploy/tests -v
```

Ils exercent les fonctions et validations Bash réelles sans lancer l'exécuteur,
et les blocs PowerShell avec un transport factice. Bash est nécessaire ; sous
Windows, PowerShell 5.1 et PowerShell 7 sont vérifiés s'ils sont disponibles.
Les interpréteurs absents sont signalés comme tests ignorés.

## Arrêt et reprise après échec

La procédure officielle reste `deploy/deploy_vm.ps1` et son exécuteur versionné
`deploy/scripts/deploy_release.sh`, décrits ci-dessus. Ne pas reconstruire une
séquence manuelle de pull, build, migration et redémarrage : elle contournerait
les gardes de commit, de sauvegarde, de plan de migration et de santé.

Au premier échec, arrêter et conserver le journal, le commit atteint, le plan de
migration et les références de sauvegarde sans exposer de secrets. Diagnostiquer
l'étape et l'état intermédiaire en lecture seule avant toute proposition de
reprise. Une reprise par la procédure gardée exige une nouvelle autorisation
explicite ; ne pas relâcher une protection pour la faire passer. Ce document ne
fournit aucune recette d'urgence alternative. Une restauration, un rollback ou
une intervention hors déploiement exige sa propre procédure revue et son accord
explicite. Ne jamais éditer le code directement dans la copie de production.

Après l'activation de Certbot, ne pas écraser directement la configuration
Nginx active avec le gabarit HTTP du dépôt : reporter les nouvelles directives
dans le bloc HTTPS généré, puis valider avec `nginx -t` avant le rechargement.

## État DNS

État vérifié le 24 août 2026 :

- `polypbase.org` possède une entrée A vers `217.71.122.88` ;
- `www.polypbase.org` est utilisable en HTTPS et rejoint la même application.

La configuration attendue reste :

| Nom | Type | Valeur | TTL conseillé |
|---|---|---|---|
| `@` | `A` | `217.71.122.88` | 300 |
| `www` | `CNAME` | `polypbase.org.` | 300 |

Contrôler ensuite la propagation :

```bash
getent ahostsv4 polypbase.org
getent ahostsv4 www.polypbase.org
```

Les deux noms doivent continuer à rejoindre l’adresse IPv4 de la VM.

## HTTPS

Le certificat est actif et HTTP redirige vers HTTPS. Les contrôles d’exploitation
restants sont :

```bash
systemctl status certbot.timer --no-pager
certbot certificates
certbot renew --dry-run
```

La connexion, les API, les QR codes et le scan doivent être revérifiés après une
modification importante de Nginx ou des paramètres HTTPS.

## Points encore ouverts

1. configurer un serveur SMTP pour les invitations et mots de passe oubliés ;
2. copier régulièrement les sauvegardes vers un stockage hors de la VM ;
3. intégrer plus tard les corrections métier des fichiers d’anomalies lorsqu’Anaïs et Étienne auront répondu.

Les fichiers d’anomalies non corrigés ne bloquent pas la mise en ligne : la base
de production conserve exactement l’import historique déjà effectué. Leurs
futures corrections devront faire l’objet d’une opération métier distincte,
documentée et sauvegardée.

## Contrôles de fin

Après activation du DNS et de HTTPS :

- `https://polypbase.org` ouvre le frontend sans avertissement
- HTTP redirige vers HTTPS
- une route interne du frontend se recharge sans erreur 404
- la connexion et la déconnexion fonctionnent
- l’API et l’administration passent bien par Nginx
- les fichiers statiques de l’administration sont présents
- un QR code ouvre la bonne fiche sur téléphone
- le scanner obtient l’autorisation de la caméra
- un relevé existant de l'organisation autorisée est consulté en lecture seule,
  avec contrôle des valeurs (dont zéro et absence), de la date et de l'auteur,
  sans créer, modifier ou supprimer de donnée scientifique
- une sauvegarde automatique est créée et une restauration de test est validée
  uniquement dans un environnement distinct, isolé et explicitement autorisé

Le smoke test de production utilise uniquement des données existantes dont la
consultation est autorisée. Une éventuelle écriture scientifique exceptionnelle
n'appartient ni à ce smoke test ni à l'accord de déploiement : elle exige une
décision distincte, une autorisation explicite immédiatement avant l'opération
et une procédure revue couvrant périmètre, audit, vérification et devenir des
données. Aucune écriture suivie d'une suppression de test n'est autorisée ici.
