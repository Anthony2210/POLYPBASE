# Installation et déploiement de Polypbase sur la VM

Ce document décrit l'installation réalisée sur la machine virtuelle destinée à
héberger Polypbase. Il permet de comprendre l'état actuel du serveur, de
reproduire les étapes déjà terminées et de reprendre le déploiement sans
confondre la base Neon actuelle avec la future base PostgreSQL locale.

État de la VM relevé le 12 août 2026. Préparation locale mise à jour le
17 août 2026.

## Règles de sécurité

Le dépôt GitHub étant public, les informations suivantes ne doivent jamais être
ajoutées dans Git :

- le mot de passe du compte Linux
- la phrase secrète de la clé SSH
- une clé SSH privée
- le contenu de `backend/.env`
- la clé secrète Django
- les identifiants PostgreSQL ou Neon
- les mots de passe des comptes Polypbase

Dans les commandes de ce document, les valeurs entourées de chevrons doivent
être remplacées localement. Par exemple, `<IP_VM>` représente l'adresse de la
VM. Les vraies valeurs restent dans le gestionnaire de mots de passe de
l'équipe ou dans les fichiers protégés du serveur.

## État actuel de la VM

| Élément | État vérifié |
|---|---|
| Système | Debian GNU/Linux 13, architecture x86_64 |
| Nom de la machine | `aquariumparis01` |
| Compte d'administration | connexion SSH avec une clé, puis passage en `root` avec `su -` |
| Compte de service | `polypbase` |
| Dépôt | `/srv/polypbase/app` |
| Branche Git | `main`, propre et synchronisée avec `origin/main` lors de la vérification |
| Python | 3.13.5 |
| uv | 0.12.3, installé dans `/usr/local/bin` |
| Node.js | 20.19.2 |
| npm | 9.2.0 |
| Nginx | installé, activé et démarré sur le port 80 |
| PostgreSQL | 17.10, installé et démarré |
| Adresse PostgreSQL | uniquement `127.0.0.1:5432` et `[::1]:5432` |
| Backend Django | dépendances installées et `manage.py check` validé |
| Base utilisée par Django | base PostgreSQL distante Neon |
| Frontend React | dépendances installées et build Vite réussi |
| Domaine | `polypbase.org` prévu, mais liaison DNS et HTTPS non finalisées |

Le 17 août 2026, `polypbase.org` pointait encore vers le service de redirection
de Gandi et non vers la VM. Le port SSH de la VM répondait, mais le port HTTP
public ne répondait pas encore. La règle réseau du fournisseur et le pare-feu
de la VM doivent donc être vérifiés avant la demande de certificat.

Le service `postgresql.service` apparaît comme `active (exited)`. Ce comportement
est normal sur Debian : ce service supervise les clusters PostgreSQL. Le
processus PostgreSQL réel écoute bien sur le port local 5432.

## Ce qui est déjà opérationnel

Les éléments suivants ont été installés ou vérifiés :

1. l'accès SSH à la VM par clé publique
2. Nginx et son démarrage automatique
3. le compte système `polypbase`
4. le clonage du dépôt dans `/srv/polypbase/app`
5. uv et l'environnement virtuel Python du projet
6. les dépendances backend verrouillées par `uv.lock`
7. les dépendances frontend verrouillées par `package-lock.json`
8. la validation de la configuration Django
9. la connexion temporaire de Django à Neon
10. la compilation du frontend React
11. l'installation de PostgreSQL 17 sur la VM
12. l'écoute de PostgreSQL limitée à la machine locale

La version locale contient désormais les éléments de production suivants, qui
ne seront présents sur la VM qu'après le prochain déploiement :

- Gunicorn dans `pyproject.toml` et `uv.lock`
- `STATIC_ROOT` pour les fichiers statiques Django
- le service `deploy/systemd/polypbase.service`
- la configuration `deploy/nginx/polypbase.conf`
- les variables SMTP documentées dans `backend/.env.example`

## Ce qui n'est pas encore terminé

La VM ne sert pas encore Polypbase en production. Les points suivants restent à
réaliser :

1. pousser puis récupérer sur la VM la version finale du code local
2. préparer une base et un utilisateur PostgreSQL locaux
3. sauvegarder puis transférer les données de Neon vers PostgreSQL local
4. comparer les données avant et après le transfert
5. remplacer la connexion Neon dans `backend/.env` par la connexion locale
6. récupérer Gunicorn sur la VM avec `uv sync --frozen`
7. installer et activer le service `systemd` versionné
8. lancer `collectstatic`
9. installer et activer la configuration Nginx versionnée
10. relier `polypbase.org` à la VM
11. installer et vérifier le certificat HTTPS
12. mettre en place les sauvegardes automatiques de PostgreSQL
13. documenter et tester une restauration complète

Le PostgreSQL local est actuellement installé, mais il n'est pas encore utilisé
par Polypbase. Il ne faut donc pas supprimer Neon ni modifier ses données pour
le moment.

## Accès à la VM

La connexion se fait avec une clé SSH. La clé privée reste uniquement sur le
poste autorisé.

Exemple avec PuTTY :

```text
Host Name: <IP_VM>
Port: 22
Connection type: SSH
Username: aquariumparis
Private key: fichier .ppk conservé localement
```

Après la connexion :

```bash
whoami
su -
whoami
```

Le dernier résultat attendu est `root` pour les opérations système. Les
commandes applicatives doivent néanmoins être exécutées avec le compte
`polypbase` dès que possible.

## Préparation initiale du serveur

Les outils système présents sur la VM ont été vérifiés avec :

```bash
node --version
npm --version
python3 --version
df -h /
```

Résultats observés :

```text
Node.js 20.19.2
npm 9.2.0
Python 3.13.5
environ 32 Gio disponibles sur le disque racine
```

Nginx a été vérifié avec :

```bash
systemctl status nginx --no-pager
ss -lntp
curl -I --max-time 5 http://127.0.0.1
```

À ce stade, Nginx répond avec sa page Debian par défaut. Cela vérifie le serveur
HTTP, mais cela ne signifie pas encore que Polypbase est déployé.

## Compte système et dépôt Git

Le compte dédié et le dépôt ont été préparés avec les commandes suivantes :

```bash
adduser --system --group --home /srv/polypbase polypbase
git clone https://github.com/Anthony2210/POLYPBASE.git /srv/polypbase/app
chown -R polypbase:polypbase /srv/polypbase
```

L'état du dépôt doit être consulté avec son propriétaire :

```bash
runuser -u polypbase -- git -C /srv/polypbase/app status -sb
```

Cette méthode évite l'erreur Git `detected dubious ownership`. Il n'est pas
nécessaire d'ajouter le dépôt aux répertoires sûrs globaux de `root` lorsque
les commandes Git sont correctement lancées avec `runuser`.

## Installation de uv et des dépendances Python

uv a été installé pour tout le système avec :

```bash
curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
hash -r
uv --version
```

Les dépendances verrouillées ont ensuite été installées sous le compte de
service :

```bash
cd /srv/polypbase/app
runuser -u polypbase -- uv sync --frozen
```

Cette commande a créé l'environnement virtuel suivant :

```text
/srv/polypbase/app/.venv
```

Il ne faut pas utiliser `pip install` directement dans le système. Toute
nouvelle dépendance Python doit d'abord être ajoutée au projet avec uv sur le
poste de développement, puis `pyproject.toml` et `uv.lock` doivent être
versionnés.

## Configuration Django sur la VM

Le fichier suivant a été créé directement sur le serveur :

```text
/srv/polypbase/app/backend/.env
```

Il est ignoré par Git. Ses droits ont été limités avec :

```bash
chown polypbase:polypbase /srv/polypbase/app/backend/.env
chmod 600 /srv/polypbase/app/backend/.env
```

Le fichier contient actuellement les catégories de variables suivantes, sans
que leurs vraies valeurs soient reproduites ici :

```dotenv
# Connexion temporaire à Neon
POSTGRES_DB=<nom_base_neon>
POSTGRES_USER=<utilisateur_neon>
POSTGRES_PASSWORD=<mot_de_passe_neon>
POSTGRES_HOST=<hote_neon>
POSTGRES_PORT=5432
POSTGRES_SSLMODE=require

# Django en production
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<cle_aleatoire_unique>
DJANGO_ALLOWED_HOSTS=polypbase.org,www.polypbase.org,<IP_VM>,127.0.0.1
DJANGO_CSRF_TRUSTED_ORIGINS=https://polypbase.org,https://www.polypbase.org
PUBLIC_BASE_URL=https://polypbase.org
DJANGO_SECURE_SSL_REDIRECT=1
DJANGO_SECURE_HSTS_SECONDS=0
```

La clé Django de production doit être différente de celle du développement et
ne doit jamais être envoyée par email ou ajoutée au dépôt.

La configuration a été validée avec :

```bash
cd /srv/polypbase/app
runuser -u polypbase -- uv run python backend/manage.py check
```

Résultat obtenu :

```text
System check identified no issues (0 silenced).
```

La connexion à la base configurée a été contrôlée sans afficher les
identifiants :

```bash
runuser -u polypbase -- uv run python backend/manage.py shell -c \
  "from django.db import connection; connection.ensure_connection(); print('Base connectee :', connection.vendor)"
```

Résultat obtenu :

```text
Base connectee : postgresql
```

Ce résultat confirme le moteur PostgreSQL. À la date de rédaction, la connexion
vient encore de Neon, pas du PostgreSQL installé sur la VM.

## Installation et vérification du frontend

Les dépendances et le build du frontend ont été réalisés avec :

```bash
cd /srv/polypbase/app
runuser -u polypbase -- npm --prefix frontend ci
runuser -u polypbase -- npm --prefix frontend run build
ls -la frontend/dist
```

Le build Vite a réussi et a produit notamment :

```text
frontend/dist/index.html
frontend/dist/assets/
frontend/dist/favicon.svg
frontend/dist/jellyfish.svg
```

Deux avertissements ont été observés :

- npm signale deux vulnérabilités de sévérité élevée
- Vite signale des fichiers JavaScript de plus de 500 kB après minification

Ces avertissements doivent être analysés, mais il ne faut pas exécuter
`npm audit fix` automatiquement sur le serveur. Une correction de dépendances
doit être testée localement, versionnée, validée par la CI, puis déployée.

Le build présent sur la VM correspond au dernier commit publié au moment de la
commande. Les modifications locales non poussées ne peuvent pas apparaître sur
le serveur.

## Installation de PostgreSQL local

PostgreSQL a été installé sur la VM avec les paquets Debian :

```bash
apt update
apt install -y postgresql postgresql-contrib
systemctl enable --now postgresql
```

La vérification a été réalisée avec :

```bash
psql --version
systemctl status postgresql --no-pager
ss -lntp | grep 5432
```

Résultats obtenus :

```text
PostgreSQL 17.10
service postgresql activé
écoute sur 127.0.0.1:5432 et [::1]:5432 uniquement
```

Cette écoute locale est adaptée : PostgreSQL n'a pas besoin d'être exposé à
Internet puisque Django sera exécuté sur la même VM.

Aucune base Polypbase locale, aucun rôle applicatif et aucun transfert des
données Neon n'ont encore été réalisés.

## Vérifications rapides de l'état actuel

Les commandes suivantes donnent un diagnostic sans modifier le serveur :

```bash
echo "=== SYSTEME ==="
hostname
python3 --version
node --version
npm --version
uv --version

echo "=== SERVICES ==="
systemctl is-active nginx
systemctl is-active postgresql

echo "=== PORTS ==="
ss -lntp

echo "=== DEPOT ==="
runuser -u polypbase -- git -C /srv/polypbase/app status -sb

echo "=== CONFIGURATION DJANGO ==="
cd /srv/polypbase/app
runuser -u polypbase -- uv run python backend/manage.py check

echo "=== FICHIERS SENSIBLES ==="
stat -c '%U %G %a %n' /srv/polypbase/app/backend/.env

echo "=== BUILD FRONTEND ==="
test -f /srv/polypbase/app/frontend/dist/index.html \
  && echo "build frontend present" \
  || echo "build frontend absent"
```

Les droits attendus pour `backend/.env` sont :

```text
propriétaire polypbase
groupe polypbase
mode 600
```

## Mise à jour du code après un push validé

Cette partie pourra être utilisée lorsque la version locale aura été testée,
poussée sur GitHub et validée par la CI.

Avant toute mise à jour, vérifier que le dépôt de la VM est propre :

```bash
runuser -u polypbase -- git -C /srv/polypbase/app status -sb
```

Puis mettre à jour les sources et reconstruire les dépendances :

```bash
runuser -u polypbase -- git -C /srv/polypbase/app pull --ff-only

cd /srv/polypbase/app
runuser -u polypbase -- uv sync --frozen
runuser -u polypbase -- npm --prefix frontend ci
runuser -u polypbase -- npm --prefix frontend run build
runuser -u polypbase -- uv run python backend/manage.py check
```

`git pull --ff-only` empêche la création involontaire d'un commit de fusion sur
le serveur. Aucune modification manuelle du code ne doit être effectuée dans la
copie de production.

## Installation du backend et de Nginx

Après le premier `git pull` contenant le dossier `deploy/`, installer les
fichiers de service en tant que `root` :

```bash
cd /srv/polypbase/app
runuser -u polypbase -- uv sync --frozen
runuser -u polypbase -- npm --prefix frontend ci
runuser -u polypbase -- npm --prefix frontend run build
runuser -u polypbase -- uv run python backend/manage.py collectstatic --noinput

install -m 0644 deploy/systemd/polypbase.service /etc/systemd/system/polypbase.service
install -m 0644 deploy/nginx/polypbase.conf /etc/nginx/sites-available/polypbase
ln -sfn /etc/nginx/sites-available/polypbase /etc/nginx/sites-enabled/polypbase
test ! -L /etc/nginx/sites-enabled/default || unlink /etc/nginx/sites-enabled/default

systemctl daemon-reload
systemctl enable --now polypbase
nginx -t
systemctl reload nginx
```

Contrôler ensuite les services et les journaux sans afficher le contenu du
fichier `.env` :

```bash
systemctl status polypbase nginx --no-pager
journalctl -u polypbase -n 100 --no-pager
curl -I -H 'Host: polypbase.org' http://127.0.0.1/
curl -I -H 'Host: polypbase.org' -H 'X-Forwarded-Proto: https' \
  http://127.0.0.1:8000/api/auth/session/
```

Le port `8000` reste lié à `127.0.0.1` et ne doit pas être ouvert sur Internet.
Seuls les ports `22`, `80` et `443` sont nécessaires publiquement.

## DNS de polypbase.org

Le gestionnaire DNS est Gandi. Demander au webmestre de remplacer la
redirection actuelle par les entrées suivantes :

| Nom | Type | Valeur |
|---|---|---|
| `@` | `A` | `<IP_VM>` |
| `www` | `CNAME` | `polypbase.org.` |

Une durée de cache de 300 secondes est pratique pendant la bascule. Avant de
lancer Certbot, vérifier que les deux noms renvoient l'adresse de la VM :

```bash
getent ahostsv4 polypbase.org
getent ahostsv4 www.polypbase.org
```

Il faut aussi confirmer que les ports 80 et 443 sont autorisés dans le
pare-feu du fournisseur et celui de Debian. Certbot ne pourra pas valider le
domaine si le port 80 reste inaccessible.

## Activation HTTPS

Une fois le DNS propagé et le port 80 accessible :

```bash
apt update
apt install -y certbot python3-certbot-nginx
certbot --nginx -d polypbase.org -d www.polypbase.org --redirect
systemctl status certbot.timer --no-pager
certbot renew --dry-run
```

Dans `backend/.env`, conserver `DJANGO_SECURE_HSTS_SECONDS=0` pour les premiers
tests. Après validation de HTTPS, de la connexion et des QR codes, la valeur
peut être portée progressivement à `86400`, puis davantage.

La production doit aussi utiliser le backend SMTP Django et des identifiants
fournis par l'équipe informatique. Sans SMTP, la fonction de réinitialisation
du mot de passe écrit le lien dans les journaux au lieu d'envoyer un email.

## Précautions concernant la base de données

Tant que `backend/.env` pointe vers Neon, toutes les commandes Django utilisant
la base agissent sur la base partagée.

Avant la migration vers PostgreSQL local :

1. figer une courte fenêtre d'intervention
2. empêcher les nouvelles saisies pendant le transfert
3. créer une sauvegarde complète de Neon
4. vérifier que la sauvegarde est lisible
5. créer la base et le rôle locaux avec un mot de passe unique
6. restaurer la sauvegarde dans la base locale
7. comparer les nombres d'organisations, comptes, boîtes, relevés et événements
8. tester l'application avec la base locale
9. modifier `backend/.env` seulement après validation
10. conserver temporairement la sauvegarde et la possibilité de revenir à Neon

Ne pas lancer sur la base partagée sans préparation :

```text
manage.py migrate
manage.py loaddata
manage.py seed_demo_data
DROP DATABASE
TRUNCATE
```

Les migrations seront nécessaires au déploiement final, mais uniquement après
une sauvegarde, avec la version exacte du code à déployer et sur la base
explicitement choisie.

## Ordre recommandé pour terminer le déploiement

La prochaine session doit reprendre dans cet ordre :

1. terminer et tester les changements locaux
2. vérifier le frontend avec `npm run build`
3. vérifier Django, les migrations et les tests
4. pousser le code et attendre la réussite de la CI
5. mettre à jour la VM avec `git pull --ff-only`
6. reconstruire les dépendances et le frontend
7. préparer et tester PostgreSQL local
8. migrer les données de Neon avec une sauvegarde vérifiée
9. installer Gunicorn et le service `systemd` déjà versionnés
10. installer la configuration Nginx déjà versionnée
11. faire pointer le domaine vers la VM
12. activer HTTPS avec un certificat valide
13. vérifier la connexion, les API, l'administration, les QR codes et le scan
14. activer progressivement HSTS après validation de HTTPS
15. mettre en place les sauvegardes et tester une restauration

## Critères de fin du déploiement

Le déploiement pourra être considéré comme terminé lorsque :

- `https://polypbase.org` ouvre l'application sans avertissement
- HTTP redirige correctement vers HTTPS
- le frontend React se recharge sur une URL interne sans erreur 404
- la connexion et la déconnexion fonctionnent
- les appels `/api/` passent par Nginx vers Django
- l'administration Django charge ses fichiers statiques
- un QR code ouvre la bonne fiche sur téléphone
- le scanner obtient l'autorisation de la caméra sur iOS et Android
- la création d'un relevé est persistée dans PostgreSQL local
- les permissions sont respectées entre institutions
- aucun secret n'est présent dans Git ou dans le frontend compilé
- les services redémarrent automatiquement après un redémarrage de la VM
- une sauvegarde automatique est créée hors de la base active
- une restauration de test a été effectuée avec succès

## Résumé de reprise

Au 17 août 2026, la machine possède tous les composants de base : Nginx,
Python, uv, Node.js, le dépôt, les dépendances, un build React et PostgreSQL 17.
Django fonctionne encore avec Neon. Les fichiers Gunicorn, `systemd` et Nginx
sont prêts localement, mais ils ne sont pas encore publiés ni installés sur la
VM. Aucun certificat HTTPS et aucun transfert vers PostgreSQL local ne sont
encore finalisés.

Il faut donc conserver Neon comme source active jusqu'à ce que la nouvelle
version du code soit poussée, que la base locale soit sauvegardée et vérifiée,
et que le chemin complet Nginx, Django et HTTPS soit testé.
