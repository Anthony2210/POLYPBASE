# Socle de démonstration isolée

**L'option A est retenue** : héberger une instance de démonstration indépendante à partir d'un instantané des données de l'Aquarium de Paris, dont la réutilisation devra être expressément autorisée et dont le contenu devra être assaini selon un contrat approuvé.

Ce document décrit un socle logiciel local, pas une instance déployée ou opérationnelle. Le socle ne constitue ni une autorisation d'utiliser des données de production ni une autorisation d'accéder à la production, à Neon ou à une base partagée. Aucun processus d'extraction, de copie, d'assainissement, de restauration ou d'admission d'un instantané de production n'a été implémenté.

## Contrat applicatif

Renseigner ces variables dans l'environnement **du processus Django dédié**. Ne pas copier le `.env` ordinaire. Fixer `DJANGO_SETTINGS_MODULE=config.demo_settings` et `POLYPBASE_DEMO=1` dans le lanceur démo. Ce module refuse de démarrer si le marqueur manque, même si aucune autre variable démo n'est présente. Le module ordinaire conserve son comportement en l'absence de variables démo ; une variable démo sans le mode explicite y fait aussi échouer le démarrage.

| Variable | Valeur attendue |
| --- | --- |
| `POLYPBASE_DEMO` | `1` (seule valeur qui active le mode) |
| `DEMO_PUBLIC_ORIGIN` | Origine DNS HTTPS dédiée, de forme `https://demo.example.test`, sans chemin, port, compte ni joker. L'exemple n'est pas un nom à publier. |
| `DEMO_SECRET_KEY` | Secret aléatoire propre à Django démo, de 50 à 128 caractères URL-safe. |
| `DEMO_DB_NAME`, `DEMO_DB_USER` | Nom et rôle propres à la base démo, préfixés `demo_`, en minuscules ASCII, chiffres et `_`. |
| `DEMO_DB_PASSWORD` | Secret aléatoire distinct, de 32 à 128 caractères URL-safe. |

Django ne charge pas `backend/.env` en démo. Il refuse les variables libpq `PG*` et utilise uniquement le socket Unix PostgreSQL `/run/polypbase-demo/postgresql/.s.PGSQL.5432`, sans repli vers TCP ou vers la configuration `POSTGRES_*`. Le socket absent ou le serveur inaccessible font échouer la connexion, pas basculer vers une autre base. Un nom de base et un préfixe ne prouvent pas l'identité du serveur ni son isolation. Le frontend standard utilise les API de la même origine ; le bandeau FR/EN reflète le marqueur explicite de `/api/auth/session/`, sans deuxième configuration Vite. Ne pas utiliser le proxy Vite de développement comme lanceur de démo : il cible le port de développement ordinaire. Servir le build et Django sur la même origine. Les nouveaux QR utilisent cette origine Django ; aucun QR déjà imprimé ou URL stockée n'est modifié par ce socle.

En démo, Django coupe la livraison de courriels (backend factice, sans réglages SMTP hérités), la collecte et la condensation iMinilide via la commande planifiée, et les appels WoRMS. Les permissions et l'isolation institutionnelles sont inchangées. Le bandeau ne donne ni provenance ni date des données.

## Comptes et partages : ce que le socle ne neutralise pas

Les empreintes de mots de passe restaurées peuvent encore permettre à des comptes actifs de se connecter. Les droits de partage déjà acceptés peuvent rester effectifs pour leurs bénéficiaires si les conditions d'accès sont toujours remplies. Des jetons indépendants de la clé Django, notamment les secrets d'invitation au partage vérifiés contre une empreinte stockée, peuvent aussi rester valides. L'absence de durées configurées empêche de créer, de prolonger ou de réémettre des partages externes ; elle ne révoque ni les droits ni les jetons préexistants.

Une `SECRET_KEY` et un nom de cookie de session propres à la démo séparent la configuration des deux instances, mais ne neutralisent ni les mots de passe restaurés, ni les droits de partage, ni les jetons indépendants. Ils ne dispensent pas de vérifier les sessions héritées. Le socle ne contrôle pas l'admissibilité du contenu de la base et ne doit pas être utilisé avec un instantané réel tant que les conditions ci-dessous ne sont pas remplies.

## À autoriser et à vérifier séparément

- Isoler le logement du processus et du cluster PostgreSQL : compte OS et stockage propres, socket monté depuis ce seul cluster, répertoire et permissions contrôlés, rôle sans accès à aucune autre base et sans privilège d'administration. Aucun socket, partage, montage, secret, service libpq ou volume issu d'un autre environnement. Ne pas réutiliser des secrets, comptes ou sessions réels.
- Bloquer l'egress (y compris TCP PostgreSQL, SMTP et HTTPS vers les services externes, production et Neon). Vérifier la politique depuis le périmètre d'exécution du serveur et de la base. Le mode Django n'est ni un pare-feu ni une règle de montage. Un lien externe ouvert par le navigateur de l'utilisateur ne relève pas de l'egress du serveur.
- Ne reprendre aucun timer, cron, service de collecte, tâche de sauvegarde ou maintenance, ni script de déploiement de production. La sauvegarde système actuelle est indépendante des settings Django. Limiter séparément les commandes et accès opérateur.
- Utiliser une origine et un proxy TLS dédiés, idéalement sur un autre site que la production ; faire respecter le nom d'hôte au proxy, écraser les en-têtes de schéma transmis par le client, ne router vers aucun service ordinaire, et ne pas mettre en cache les réponses de session. Vérifier que les routes `/api`, `/bac` et les ressources QR pointent vers la démo et que les ressources statiques n'introduisent pas de dépendance externe.

## Option A : conditions bloquantes pour la phase suivante

Avant tout traitement des données source, obtenir une autorisation explicite de réutilisation et faire approuver un inventaire des données, une politique de confidentialité et un contrat d'assainissement. L'inventaire doit couvrir les données scientifiques et personnelles, les preuves et leurs auteurs, les accès, les données partagées et les liens ou références externes.

Avant d'admettre un instantané dans la démo puis d'ouvrir l'accès, vérifier indépendamment que :

- Les comptes, appartenances, privilèges et autres chemins d'accès hérités ont été inspectés et neutralisés selon le contrat approuvé. Aucun mot de passe restauré, aucune session héritée, aucun secret d'authentification réutilisable, ni aucun lien d'invitation ou de réinitialisation hérité ne permet d'accéder à la démo.
- Aucun droit de partage externe préexistant n'est effectif, et aucun jeton de partage hérité ne peut accorder un accès. Les identifiants, secrets et accès autorisés pour la démo sont provisionnés séparément.
- L'isolation a été vérifiée, sans communication sortante ni intégration de production. Ces vérifications ne se limitent pas aux garde-fous Django.
- Les preuves scientifiques et l'attribution historique sont préservées sans fabriquer de provenance. Le contrat distingue l'attribution des droits d'accès et établit quelles informations sur les auteurs peuvent être exposées sans compromettre la confidentialité.

**L'instance de démonstration doit rester inaccessible aux utilisateurs** tant que l'autorisation, l'assainissement et l'isolation n'ont pas été vérifiés indépendamment et que les revues de sécurité et scientifique n'ont pas donné leur accord à l'ouverture. Ne jamais lancer `seed_demo_data` sur une base restaurée.

## Essai technique du socle sans instantané

La procédure ne lance pas `seed_demo_data` automatiquement. Pour un essai avec des données fictives uniquement, sur une base démo vide, migrée et vérifiée comme jetable, l’option explicite `--allow-non-debug` autorise cette commande avec `--settings=config.demo_settings` sans activer `DEBUG`. Ne jamais l’utiliser sur un instantané, une base partagée ou une base de production.

Après revue et autorisation de l'environnement d'essai, vérifier l'identité du socket d'un cluster PostgreSQL vide dans un périmètre jetable sans accès réseau sortant. Avec une base vide migrée et, si nécessaire, un compte jetable, vérifier la connexion, le bandeau FR/EN, le refus des effets sortants et la redirection tablette `/administration…` vers `/`. Cet essai ne valide ni l'admissibilité d'un instantané ni l'ouverture de la démo.
