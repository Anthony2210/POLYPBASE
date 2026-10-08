# Développement, validation et déploiement

## Inspection minimale

Avant une modification :

1. lire `AGENTS.md`, le routeur et seulement le contexte de domaine utile;
2. exécuter `git status --short --branch` et examiner le diff existant;
3. lire modèles, permissions, services, serializers, vues, types, composants et tests réellement concernés;
4. rechercher tous les consommateurs avant de modifier un contrat;
5. identifier les invariants applicables : zéro, organisation, permission, historique, audit et concurrence;
6. implémenter dans le périmètre, puis relire le diff complet.

Préserver tout changement existant. Ne pas lancer automatiquement une migration, un import ou des données de démonstration : identifier d'abord la base ciblée.

## Backend isolé

Exécuter depuis `backend/`. `config.test_settings` force une base SQLite en mémoire et évite la base configurée dans l'environnement local.

```powershell
# Remplacer le module par le test du domaine modifié
uv run python manage.py test apps.cultures.test_box_inventory_api --settings=config.test_settings

# Suite backend isolée
uv run python manage.py test --settings=config.test_settings

# Configuration et dérive de migrations
uv run python manage.py check --settings=config.test_settings
uv run python manage.py makemigrations --check --dry-run --settings=config.test_settings
```

Utiliser les tests ciblés en premier. Élargir à la suite complète lorsqu'une règle partagée, une permission, une transaction ou un contrat API transversal change. La liste détaillée des tests existants est dans [`../tests_backend.md`](../tests_backend.md).

Les conclusions dépendant des verrous, contraintes ou transactions PostgreSQL doivent être reproduites sur un PostgreSQL QA isolé. SQLite ne suffit pas. Cet environnement doit être temporaire, distinct de Neon et de la production, puis entièrement nettoyé.

## QA locale avec copie PostgreSQL de la VM

**QA locale contre une copie locale uniquement : jamais contre la VM, la production ou Neon.** Cette procédure suppose une copie déjà restaurée et autorisée pour la QA dans le conteneur local existant `polypbase-postgres-local`; elle ne réalise ni accès VM, ni extraction, ni restauration. Vérifier que Docker utilise le moteur local, pas un contexte distant. Ne pas remplacer ce conteneur par un `docker run`, ni recréer son volume.

**Session jetable obligatoire.** Ne pas exécuter cette procédure dans une session de travail existante. Depuis la racine du checkout souhaité, ouvrir un processus PowerShell enfant dédié :

```powershell
powershell -NoProfile
```

Exécuter tous les blocs backend ci-dessous uniquement dans cette session enfant. Elle hérite de l’environnement de départ, mais les modifications DB, Django, hôtes/CSRF et `PUBLIC_BASE_URL` restent dans ce processus et ses enfants ; elles ne modifient pas la session d’origine. Ne pas utiliser `setx`, écrire un profil ou persister ces valeurs. Après toute erreur, arrêter la procédure et fermer cette session : ne pas poursuivre avec un environnement partiellement configuré.

Depuis la racine du checkout souhaité, **dans cette session enfant dédiée** :

```powershell
Set-Location .\backend

# Discover only the initialized database and role names, never the password.
$containerEnv = docker inspect --format '{{range .Config.Env}}{{if or (eq (index (split . `=`) 0) `POSTGRES_DB`) (eq (index (split . `=`) 0) `POSTGRES_USER`)}}{{println .}}{{end}}{{end}}' polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local container inspection failed' }
foreach ($name in @('POSTGRES_DB', 'POSTGRES_USER')) {
    $entry = $containerEnv | Where-Object { $_.StartsWith("${name}=") } | Select-Object -First 1
    if (-not $entry) { throw "Missing container variable: $name" }
    [Environment]::SetEnvironmentVariable($name, $entry.Substring($name.Length + 1), 'Process')
}
Remove-Variable containerEnv, entry

# Check configured bindings even when the existing container is stopped.
$configuredBindingsJson = docker inspect --format '{{json .HostConfig.PortBindings}}' polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL binding inspection failed' }
$configuredBindings = $configuredBindingsJson | ConvertFrom-Json -ErrorAction Stop
$postgresBindings = @($configuredBindings.PSObject.Properties | Where-Object { $_.Name -match '^5432/' })
if (-not $configuredBindings -or -not $configuredBindings.'5432/tcp') {
    throw 'No configured PostgreSQL TCP publication; stop QA'
}
foreach ($port in $postgresBindings) {
    if (-not $port.Value) { throw 'Missing explicit PostgreSQL host bindings; stop QA' }
    foreach ($hostBinding in $port.Value) {
        if ($hostBinding.HostIp -notin @('127.0.0.1', '::1')) {
            throw 'Configured PostgreSQL bindings must all be loopback; correct them separately before starting'
        }
    }
}
Remove-Variable configuredBindingsJson, configuredBindings, postgresBindings, port, hostBinding

docker start polypbase-postgres-local
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL container unavailable' }

# After startup, validate actual bindings and discover the host port.
$portBindings = docker port polypbase-postgres-local 5432
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL port unavailable' }
$portBindings
if (-not $portBindings -or @($portBindings | Where-Object { $_ -notmatch '^(127\.0\.0\.1|\[::1\]):[0-9]+$' }).Count -gt 0) {
    throw 'PostgreSQL must be published only on loopback; stop QA and correct the local binding separately'
}
$binding = $portBindings | Where-Object { $_ -match '^127\.0\.0\.1:[0-9]+$' } | Select-Object -First 1
if (-not $binding) { throw 'No IPv4 loopback PostgreSQL binding' }
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = ($binding -split ':')[-1]
$env:POSTGRES_SSLMODE = 'disable'
$env:DJANGO_DEBUG = 'True'
$env:DJANGO_SECRET_KEY = 'django-insecure-local-qa-only-never-production'
$env:DJANGO_SECURE_SSL_REDIRECT = '0'
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:5173'

docker exec polypbase-postgres-local pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB
if ($LASTEXITCODE -ne 0) { throw 'Local PostgreSQL not ready; wait and repeat the readiness check before supplying credentials' }

# Supply the verified CURRENT password of the LOCAL role without echo/history.
$rolePassword = $null
$passwordBstr = [IntPtr]::Zero
try {
    $rolePassword = Read-Host 'Current verified LOCAL PostgreSQL role password' -AsSecureString
    if ($rolePassword.Length -eq 0) { throw 'Local role password required' }
    $passwordBstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($rolePassword)
    $env:POSTGRES_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordBstr)
}
finally {
    if ($passwordBstr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordBstr)
    }
    if ($null -ne $rolePassword) { $rolePassword.Dispose() }
    Remove-Variable rolePassword, passwordBstr -ErrorAction SilentlyContinue
}

uv run python manage.py check
if ($LASTEXITCODE -ne 0) { throw 'Django check failed' }
uv run python manage.py migrate --plan
if ($LASTEXITCODE -ne 0) { throw 'Migration plan unavailable' }
```

Le contrôle JSON de `HostConfig.PortBindings` ne lit aucun identifiant et précède le démarrage : `docker port` peut être vide tant que le conteneur existant est arrêté. Après `docker start`, le contrôle des publications effectives fournit `POSTGRES_PORT`; la saisie du mot de passe attend ensuite la disponibilité PostgreSQL. Si PostgreSQL initialise encore son démarrage, attendre puis reprendre au contrôle `pg_isready`, sans saisir le secret avant son succès. Le filtre DB/USER utilise la fonction `split`, prise en charge par les [templates Docker officiels](https://docs.docker.com/engine/cli/formatting/#split); les chaînes Go entre accents graves dans l'argument PowerShell entre apostrophes évitent les problèmes de guillemets natifs Windows. Seules les deux entrées sélectionnées sont retournées, jamais `POSTGRES_PASSWORD`.

`Config.Env.POSTGRES_PASSWORD` décrit l'initialisation du conteneur, pas nécessairement le mot de passe actuel du rôle dans PostgreSQL après un changement ou une restauration du volume. Ne jamais l'utiliser comme autorité. Les noms `POSTGRES_DB` et `POSTGRES_USER` découverts doivent eux aussi correspondre à la copie locale attendue. Le mot de passe actuel doit être fourni par l'opérateur depuis une source locale vérifiée; s'il est inconnu ou si l'authentification échoue, arrêter et faire confirmer les accès. Aucun `ALTER ROLE`, reset de mot de passe ou changement de rôle ne fait partie de cette procédure. `pg_isready` vérifie la disponibilité, pas la validité du mot de passe; le plan de migrations nécessite ensuite une vraie connexion Django.

`DJANGO_DEBUG=True` configure `DEBUG=True` dans les réglages actuels du dépôt; la variable d'environnement `DEBUG` seule n'est pas utilisée. La saisie masquée ne place pas le secret dans une commande, son historique ou un fichier. Le BSTR est effacé/libéré et le `SecureString` détruit dans `finally`; la conversion en variable d'environnement crée néanmoins une chaîne en mémoire non effaçable de façon garantie et transmise aux processus enfants Django/uv. Ne pas afficher l'environnement, activer une transcription, enregistrer la session ou publier mots de passe, `.env`, dumps, données ou sorties sensibles. La clé Django ci-dessus est volontairement locale et non utilisable en production. Les variables du processus priment sur `backend/.env`; ne pas lire ou recopier ce fichier. Toutes les publications PostgreSQL doivent rester sur loopback, jamais `0.0.0.0`, `[::]` ou une adresse LAN; sinon arrêter avant de démarrer la QA et faire corriger séparément la publication sans recréer le conteneur/volume dans cette procédure.

**Aucune migration automatique.** Si le schéma est en retard, arrêter avant le lancement :

1. sauvegarder la copie locale hors dépôt dans un emplacement protégé et vérifier qu'elle est restaurable;
2. examiner les migrations en attente (`uv run python manage.py showmigrations --plan`), leurs opérations et dépendances dans le checkout;
3. diagnostiquer les préconditions en lecture seule sur cette copie uniquement, sans corriger les mesures pour satisfaire une contrainte;
4. appliquer seulement les migrations locales explicitement validées, puis refaire `check` et `migrate --plan`.

Un diagnostic utilisant les modèles actuels peut lui-même échouer faute de colonne ou de table : des migrations additives de dépendance peuvent être nécessaires avant ce diagnostic. Inspecter leur sûreté et le graphe courant, puis les appliquer uniquement à la copie sauvegardée et validée. Ne pas transformer une séquence historique de migrations en recette permanente, utiliser `--fake` pour masquer un écart ou modifier des migrations existantes.

Après validation du schéma, lancer le backend **dans la même session**, toujours depuis `backend/` :

```powershell
uv run python manage.py runserver 127.0.0.1:8000
```

Dans une seconde session PowerShell enfant dédiée (`powershell -NoProfile`), depuis la racine du **même checkout** :

```powershell
Set-Location .\frontend
npm run dev -- --host 127.0.0.1
```

Ouvrir `http://127.0.0.1:5173/`. Le script `dev` lance Vite; l'option limite ici l'écoute à la machine locale (la configuration Vite écoute sinon sur `0.0.0.0`). Son proxy pointe vers Django sur `127.0.0.1:8000`. Ne pas exposer cette copie par tunnel public ni lancer `seed_demo_data` dessus. Garder les tests automatisés sur les bases isolées décrites plus haut.

Pour un autre worktree, seuls les chemins du checkout changent : le même conteneur est réutilisé, mais préparer de nouvelles sessions enfants dédiées. Arrêter les serveurs précédents avant de changer de checkout; les mutations QA et migrations affectent la copie partagée entre worktrees.

**Nettoyage en fin de QA ou après une erreur :** arrêter les serveurs avec `Ctrl+C`, puis quitter les deux sessions enfants dédiées avec `exit`. Ne pas supprimer ou réécrire les variables de la session d’origine. La fermeture abandonne toutes les valeurs QA du processus — `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_SSLMODE`, `DJANGO_DEBUG`, `DJANGO_SECRET_KEY`, `DJANGO_SECURE_SSL_REDIRECT`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS` et `PUBLIC_BASE_URL` — sans altérer les valeurs préexistantes de la session d’origine. Les objets temporaires de saisie du mot de passe sont déjà nettoyés par `finally`. Protéger puis nettoyer les artefacts locaux selon le périmètre autorisé, sans supprimer le conteneur ou son volume.

### QA physique téléphone/tablette sur un LAN de confiance

Cette variante utilise uniquement la copie locale autorisée ci-dessus. **Aucun tunnel public, accès VM, production ou Neon. PostgreSQL et Django restent sur localhost; seul Vite est accessible au LAN.** Le PC et l'appareil doivent partager un réseau privé de confiance, sans isolation Wi-Fi entre clients. Le HTTP transporte sessions et données sans chiffrement : ne pas utiliser un Wi-Fi public/invité ou exposer une copie dont le périmètre d'autorisation exclut ce réseau. Limiter toute autorisation du pare-feu Windows au port TCP `5173`, au profil privé et aux appareils/sous-réseau de QA; ne pas ouvrir `8000` ou le port PostgreSQL ni désactiver le pare-feu. Les serveurs de développement ne constituent pas un déploiement sécurisé.

**Configuration actuelle à compléter pour le LAN :** `npm run dev:lan` lance `vite --host 0.0.0.0`, sur le port fixe `5173` (`strictPort: true`). Même `npm run dev` écoute par défaut sur toutes les interfaces dans `vite.config.ts`; ce n'est pas un retour à localhost. Les proxies `/api`, `/accounts`, `/boites` et `/bac` ciblent `http://127.0.0.1:8000` et ne configurent pas `changeOrigin` : l'hôte LAN du navigateur arrive donc à Django. Les valeurs par défaut de `DJANGO_ALLOWED_HOSTS` ne l'autorisent pas. Vite accepte les adresses IP; son entrée `allowedHosts: ['.trycloudflare.com']` n'est pas une restriction aux clients de confiance et n'autorise pas tous les noms DNS LAN. Utiliser l'IPv4 du PC, sans joker d'hôte ni changement de configuration versionnée.

Arrêter les deux serveurs (`Ctrl+C`). Dans la **session enfant backend jetable déjà configurée**, depuis `backend/`, découvrir les adresses de la machine et sélectionner celle de l'interface Wi-Fi/Ethernet du LAN de confiance (pas Docker, VPN ou loopback) :

```powershell
Get-NetIPConfiguration
$lanAddress = Read-Host 'PC IPv4 address on the trusted QA LAN'
$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($lanAddress, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
    [System.Net.IPAddress]::IsLoopback($parsedAddress) -or
    $lanAddress -eq '0.0.0.0') {
    throw 'Select the actual PC IPv4 address shown for the trusted LAN interface'
}
$lanOrigin = "http://${lanAddress}:5173"
$env:DJANGO_ALLOWED_HOSTS = "localhost,127.0.0.1,$lanAddress"
$env:DJANGO_CSRF_TRUSTED_ORIGINS = "http://127.0.0.1:5173,http://localhost:5173,$lanOrigin"
$env:PUBLIC_BASE_URL = $lanOrigin
$lanOrigin
uv run python manage.py runserver 127.0.0.1:8000
```

La validation de format ne prouve ni l'appartenance au PC ni la confiance du réseau : vérifier la sélection dans la sortie de `Get-NetIPConfiguration`. Garder `DJANGO_DEBUG=True` et les autres variables locales préparées ci-dessus. L'origine CSRF contient le schéma et le port; les hôtes autorisés n'en contiennent pas. Les remplacer explicitement évite d'hériter de valeurs de production ou de tunnel. Ne pas désactiver le middleware CSRF. Le frontend envoie les cookies de session et `X-CSRFToken` via des chemins de même origine au proxy Vite; aucun accès direct du téléphone à Django ni configuration CORS supplémentaire n'est nécessaire. En DEBUG, les cookies ne sont pas forcés en HTTPS. Se connecter sur l'origine LAN : une session ouverte sur `127.0.0.1` sur le PC n'est pas celle de l'adresse LAN. `PUBLIC_BASE_URL` rend les nouveaux liens QR accessibles au téléphone; les étiquettes déjà générées ne changent pas.

Dans la seconde session, depuis `frontend/` du même checkout :

```powershell
npm run dev:lan
```

Ouvrir sur l'appareil l'origine affichée par `$lanOrigin` et vérifier qu'elle correspond à l'adresse réseau annoncée par Vite. `localhost`/`127.0.0.1` sur le téléphone désigne **le téléphone**, pas le PC. Vérifier connexion, organisation active, navigation, recherche manuelle, états vide/zéro/erreur et interactions tactiles autorisées sur la copie; sur tablette, Administration doit rester absente et ses URL revenir à `/`. Un `DisallowedHost`/400 demande de vérifier l'IPv4 autorisée; un 403 CSRF demande de vérifier l'origine exacte et la session, pas de supprimer les protections. Si Vite est inaccessible, vérifier adresse, réseau, isolation et règle privée du pare-feu.

**Limite caméra :** `http://` sur une adresse LAN n'est normalement pas un contexte sécurisé, sur Safari iPhone/iPad comme sur les autres navigateurs mobiles. `TabletQrScanner` refuse explicitement un contexte non sécurisé ou l'absence de `getUserMedia`. Cette QA HTTP couvre l'interface et la recherche manuelle, **pas la caméra QR**. `camera=(self)` côté Django ne remplace pas HTTPS. Une validation caméra nécessiterait un dispositif HTTPS local approuvé avec certificat reconnu par l'appareil, absent de cette configuration; ne pas inventer ici une recette TLS, contourner les protections du navigateur ou reprendre les suggestions de tunnel/production du README pour cette copie.

**Retour localhost pendant la QA :** arrêter les deux serveurs, puis dans la session enfant backend dédiée rétablir les valeurs QA localhost ci-dessous. Ce n’est pas le nettoyage final ni une restauration de l’environnement de départ : celui-ci reste intact dans la session d’origine. Ne pas retirer le mot de passe avant de relancer Django.

```powershell
$env:DJANGO_ALLOWED_HOSTS = 'localhost,127.0.0.1'
$env:DJANGO_CSRF_TRUSTED_ORIGINS = 'http://127.0.0.1:5173,http://localhost:5173'
$env:PUBLIC_BASE_URL = 'http://127.0.0.1:5173'
Remove-Variable lanAddress, parsedAddress, lanOrigin -ErrorAction SilentlyContinue
uv run python manage.py runserver 127.0.0.1:8000
```

Dans la session frontend, relancer `npm run dev -- --host 127.0.0.1` et ouvrir `http://127.0.0.1:5173/` sur le PC. Retirer toute autorisation temporaire du pare-feu créée pour la QA LAN. En fin de travail ou après une erreur, appliquer le nettoyage ci-dessus : arrêter les serveurs et quitter les deux sessions enfants jetables, sans toucher aux variables de la session d’origine.

## Frontend

Exécuter depuis `frontend/`.

Pour la validation complète :

```powershell
npm run test:all
npm run typecheck
npm run build
```

Pour une validation ciblée, utiliser la commande du domaine concerné plutôt que de réexécuter ces tests après `test:all` :

```powershell
npm run test:api
npm run test:charts
npm run test:inventory
npm run check:css
```

`npm run test:all` découvre les fichiers `frontend/scripts/test-*.mjs` et exécute la suite avec le runner Node, également en CI. Les imports statiques entre modules de tests définissent les groupes : chaque module est exécuté une seule fois, en conservant l'isolation entre points d'entrée. La commande refuse une suite vide, un cycle sans point d'entrée ou un module partagé entre plusieurs groupes plutôt que d'omettre ou de dupliquer des tests. Les nouveaux tests doivent suivre cette convention et utiliser des imports statiques directs entre fichiers frères; les helpers et le contrôle CSS ne font pas partie de la découverte. La concurrence est limitée à deux processus pour borner la consommation de ressources des harness TypeScript/VM. Les commandes ciblées existantes restent disponibles.

`npm run build` réexécute le contrôle CSS et TypeScript avant le build Vite. Les scripts ciblés couvrent respectivement la gestion des erreurs API, les fenêtres de graphiques et la logique d'Inventaire; ils ne remplacent pas une QA navigateur pour une interaction visuelle.

Pour une modification substantielle, vérifier dans un vrai navigateur les supports concernés et les états loading, empty, error, zéro, clavier et tactile. Ne pas laisser de capture, serveur, base ou réglage QA temporaire.

## Git et revue

Depuis la racine :

```powershell
git status --short --branch
git diff --stat
git diff
git diff --check
```

Une revue multi-agent reste en lecture seule par défaut. Le reviewer lit les invariants concernés, inspecte le diff et les tests, puis présente les problèmes par gravité. Un correctif nécessite une demande distincte. Éviter deux agents écrivains sur les mêmes fichiers.

Avant un commit demandé, vérifier que tous les fichiers appartiennent au chantier et qu'aucun artefact local n'est inclus. Ne pas commit, push ou déployer sans demande explicite.

## Migrations et données existantes

Avant d'ajouter une contrainte sur des données existantes, diagnostiquer en lecture seule si elles respectent l'invariant. Ne jamais corriger automatiquement des données scientifiques pour faire passer une migration.

## Déploiement

La procédure de référence est :

```powershell
.\deploy\deploy_vm.ps1
```

Le mode `-PreflightOnly` exécute les contrôles locaux sans modifier la VM. Le script doit être inspecté avant usage, car il constitue la procédure gardée actuelle. Il vérifie notamment branche, état Git, alignement avec `origin/main`, espaces, Django, dérive de migrations, tests backend isolés et build frontend.

Sans `-PreflightOnly`, la procédure :

1. refuse les états locaux ou distants incohérents;
2. crée et vérifie une sauvegarde PostgreSQL;
3. avance le dépôt de production en fast-forward vers le commit attendu;
4. synchronise les dépendances;
5. construit le frontend dans une zone intermédiaire;
6. exécute les checks Django, affiche le plan et applique les migrations;
7. collecte les statiques et publie le build;
8. redémarre Polypbase, valide puis recharge Nginx;
9. contrôle HTTPS, page React, commit et services.

Ne pas reproduire manuellement ces étapes lorsqu'elles sont déjà orchestrées. Toute mutation de production demande une autorisation explicite immédiatement avant exécution. En cas d'échec d'une protection, arrêter au lieu de la contourner.

Les détails d'exploitation, de sauvegarde et de secours sont dans [`../deploiement_vm.md`](../deploiement_vm.md).

## Sécurité durable

- Secrets hors dépôt et chargés par l'environnement; ne jamais afficher un `.env`.
- Aucune donnée réelle dans tests, captures, logs partagés ou réponses.
- Aucun test, import, migration d'essai ou démonstration sur Neon ou la production.
- Aucune opération destructive sans sauvegarde, diagnostic et accord explicite.
- Après déploiement, vérifier commit, migrations, services, endpoint health et parcours ciblé sans créer de donnée scientifique uniquement pour le smoke test.
