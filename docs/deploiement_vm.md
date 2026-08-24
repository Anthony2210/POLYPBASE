# Déploiement de Polypbase sur la VM

Ce document décrit l’état vérifié du serveur Polypbase au 17 août 2026 et les
opérations restantes. Les mots de passe, clés SSH, clés Django et identifiants
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
| Domaine | DNS encore dirigé vers la redirection Gandi |
| HTTPS | En attente du changement DNS |
| HSTS | Désactivé avec une durée de 0 jusqu’à validation de HTTPS |

Les services suivants sont installés, activés au démarrage et opérationnels :

```bash
systemctl is-active polypbase nginx postgresql
systemctl is-enabled polypbase nginx postgresql
```

Le frontend répond en HTTP sur l’adresse publique de la VM. Gunicorn et les
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

Le minuteur `polypbase-backup.timer` exécute une sauvegarde vérifiée chaque nuit
vers 2 h 30 et conserve 14 jours de sauvegardes portant le préfixe `polypbase`.

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
DEFAULT_FROM_EMAIL=Polypbase <no-reply@polypbase.org>
EMAIL_HOST=<serveur_smtp>
EMAIL_PORT=587
EMAIL_HOST_USER=<utilisateur_smtp>
EMAIL_HOST_PASSWORD=<mot_de_passe_smtp>
EMAIL_USE_TLS=1
EMAIL_USE_SSL=0
```

## Mise à jour de l’application

Après un push validé sur `main` :

```bash
sudo -u polypbase git -C /srv/polypbase/app status -sb
sudo -u polypbase git -C /srv/polypbase/app pull --ff-only

cd /srv/polypbase/app
sudo -u polypbase uv sync --frozen
sudo -u polypbase npm --prefix frontend ci
sudo -u polypbase npm --prefix frontend run build
sudo -u polypbase .venv/bin/python backend/manage.py migrate --plan
sudo -u polypbase .venv/bin/python backend/manage.py migrate
sudo -u polypbase .venv/bin/python backend/manage.py collectstatic --noinput
sudo -u polypbase .venv/bin/python backend/manage.py check --deploy

systemctl restart polypbase
nginx -t
systemctl reload nginx
```

Toujours créer une sauvegarde avant une migration Django qui modifie le schéma.
Ne jamais éditer le code directement dans la copie de production.

Après l'activation de Certbot, ne pas écraser directement la configuration
Nginx active avec le gabarit HTTP du dépôt : reporter les nouvelles directives
dans le bloc HTTPS généré, puis valider avec `nginx -t` avant le rechargement.

## DNS à demander au webmestre

Gandi renvoie encore actuellement :

- `polypbase.org` vers l’adresse de redirection Gandi
- `www.polypbase.org` vers `webredir.vip.gandi.net`

Le webmestre doit remplacer cette redirection par :

| Nom | Type | Valeur | TTL conseillé |
|---|---|---|---|
| `@` | `A` | `<IP_VM>` | 300 |
| `www` | `CNAME` | `polypbase.org.` | 300 |

Contrôler ensuite la propagation :

```bash
getent ahostsv4 polypbase.org
getent ahostsv4 www.polypbase.org
```

Les deux noms doivent finalement renvoyer l’adresse IPv4 de la VM.

## Activation de HTTPS

Certbot ne doit être lancé qu’après propagation des deux entrées DNS :

```bash
apt update
apt install -y certbot python3-certbot-nginx
certbot --nginx -d polypbase.org -d www.polypbase.org --redirect
systemctl status certbot.timer --no-pager
certbot renew --dry-run
```

Après validation complète de la connexion, des API, des QR codes et du scan sur
téléphone, `DJANGO_SECURE_HSTS_SECONDS` pourra passer progressivement à `86400`.

## Points encore ouverts

1. faire modifier les deux entrées DNS par Améni
2. installer le certificat HTTPS et vérifier son renouvellement
3. configurer un serveur SMTP pour les invitations et mots de passe oubliés
4. copier régulièrement les sauvegardes vers un stockage hors de la VM
5. intégrer plus tard les corrections métier des fichiers d’anomalies lorsqu’Anaïs et Étienne auront répondu

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
- un relevé de contrôle est écrit dans PostgreSQL local puis supprimé proprement
- une sauvegarde automatique est créée et une restauration de test est validée
