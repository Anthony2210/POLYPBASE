# Collecte des sondes iMinilide

> Mise en place le 29 octobre 2026. Remplace les pistes de
> [`plan_integration_api.md`](plan_integration_api.md) : la VM étant hors du réseau
> de l'aquarium, Polypbase lit le cloud iminilide.com au lieu de la sonde locale.

## Fonctionnement

- Enregistreur « AQUA MEDUSES », numéro de série `1162040825`, 12 voies, une par
  étuve. Chaque voie devient une `Probe` Polypbase rattachée à sa zone thermique.
- Toutes les 10 minutes, `polypbase-iminilide.timer` lance
  `manage.py collect_iminilide` : connexion au compte iminilide.com, lecture de
  chaque voie depuis la dernière mesure de toute son histoire (anciennes sondes
  comprises, au plus 10 jours de rattrapage disponibles sur le cloud),
  enregistrement minute, recalcul des moyennes journalières touchées,
  puis condensation des mesures de plus de 14 jours en tranches de 15 minutes.
- Le cloud n'a pas d'API officielle : le client reproduit les appels de sa page
  web (`POST /en/` puis `GET /en/mesuresjson_<série>_<voie>_<AAAAMMJJhhmmss>`).
  Si Microlide modifie son site, la voie concernée échoue explicitement sans
  insérer de mesure pour sa réponse. Les autres voies peuvent être collectées.

## Voies de l'enregistreur

| Voie | Nom sur le cloud |
|---|---|
| 1 | ETUVE 10 G |
| 2 | ETUVE 10 D |
| 3 | ETUVE 5 D |
| 4 | ETUVE 5 G |
| 5 | ETUVE 15 D |
| 6 | ETUVE 15 G |
| 7 | ETUVE 20 G |
| 8 | ETUVE 20 D |
| 9 | ETUVE 30 G |
| 10 | ETUVE 30 D |
| 11 | ETUVE 25 D |
| 12 | ETUVE 25 G |

Chaque zone thermique reçoit ses deux voies G et D.

## Mise en service sur la VM

Procédure à exécuter uniquement après autorisation explicite de production.
Ce document ne vaut pas autorisation et ne rapporte aucune exécution en production.

À faire après le déploiement normal (`deploy/deploy_vm.ps1`), qui applique la
migration `measurements.0015_iminilide_probe_collection` (additive).

1. **Identifiants iMinilide, saisis directement sur la VM** (jamais dans le dépôt,
   le chat ou `backend/.env`). Le fichier n'est lu que par systemd, en root :

   ```bash
   sudo install -d -m 0755 /etc/polypbase
   sudo install -m 0600 -o root -g root /dev/null /etc/polypbase/iminilide.env
   sudo nano /etc/polypbase/iminilide.env
   ```

   Contenu :

   ```
   IMINILIDE_EMAIL=adresse-du-compte
   IMINILIDE_PASSWORD=mot-de-passe-du-compte
   ```

2. **Relier les voies aux zones.** Lister d'abord les noms exacts des zones :

   ```bash
   cd /srv/polypbase/app/backend
   sudo -u polypbase ../.venv/bin/python manage.py link_iminilide_probe \
       --organization "Aquarium de Paris" --list
   ```

   Puis une commande par voie, avec le nom exact de la zone :

   ```bash
   sudo -u polypbase ../.venv/bin/python manage.py link_iminilide_probe \
       --organization "Aquarium de Paris" --zone "<nom de la zone 25 °C>" \
       --serial 1162040825 --channel 12 --code "ETUVE 25 G"
   ```

   Relancer la commande peut modifier le code ou l'emplacement de la sonde
   active. Dès qu'une minute ou une tranche existe, changer sa zone est interdit,
   même si la sonde est inactive. Utiliser le remplacement décrit ci-dessous
   pour une nouvelle affectation; chaque liaison est auditée.

3. **Premier passage manuel**, qui rapatrie les 10 jours encore disponibles :

   ```bash
   sudo cp /srv/polypbase/app/deploy/systemd/polypbase-iminilide.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl start polypbase-iminilide.service
   journalctl -u polypbase-iminilide.service -n 40 --no-pager
   ```

   Chaque voie doit afficher `N new`. Un `FAILED` indique la voie et la cause.

4. **Activer le minuteur** :

   ```bash
   sudo systemctl enable --now polypbase-iminilide.timer
   systemctl list-timers polypbase-iminilide.timer --no-pager
   ```

## Réglages (variables d'environnement)

| Variable | Défaut | Rôle |
|---|---|---|
| `IMINILIDE_TIME_ZONE` | `Europe/Paris` | Fuseau des horodatages du cloud |
| `IMINILIDE_MAX_LOOKBACK_DAYS` | `10` | Rattrapage maximal |
| `PROBE_READING_RETENTION_DAYS` | `14` | Âge à partir duquel les mesures minute sont condensées |
| `PROBE_BUCKET_MINUTES` | `15` | Taille des tranches condensées |
| `PROBE_MANUAL_FALLBACK_HOURS` | `6` | Silence des sondes au-delà duquel la saisie manuelle revient |

## Règles retenues

- Toutes les mesures minute sont gardées 14 jours, puis condensées (min, moyenne,
  max, nombre et somme exacte par 15 min). Chaque tranche conserve aussi
  `last_measured_at`, l'instant exact de sa dernière mesure, y compris lors d'une
  fusion. Ce n'est ni le début ni la fin théorique de la tranche. La condensation
  est irréversible.
- La moyenne journalière d'une zone combine ses deux sondes. Un jour déjà saisi à
  la main n'est jamais réécrit par les sondes.
- Une valeur absente n'est jamais transformée en `0`.
- Pas d'alerte : l'interface affiche seulement l'heure de la dernière mesure et,
  après 6 h sans mesure, propose de nouveau la saisie manuelle.

## Couverture temporelle des graphiques

La fenêtre demandée est un intervalle demi-ouvert `[start, end)`. Une mesure
brute n'est sélectionnée que si son instant appartient à cet intervalle. Une
tranche condensée est sélectionnée si `[period_start, period_start +
period_minutes)` le chevauche : son début est strictement avant `end` et sa fin
strictement après `start`. Un simple contact à une borne ne suffit pas. La durée
utilisée est celle stockée dans chaque tranche, pas la configuration actuelle.

Une tranche partiellement visible conserve **toutes** ses statistiques stockées :
somme exacte, nombre, minimum, maximum et moyenne pondérée. Elles peuvent inclure
des observations avant ou après la sélection. Le graphique distingue ces tranches
par un trait discontinu et un avertissement de portée ; ses résumés signalent
également cette inclusion. Le chevauchement d'un intervalle stocké ne prouve pas
qu'une observation existe dans la portion visible. Aucune interpolation,
proratisation ou reconstruction de mesures individuelles n'est effectuée.

L'API conserve les quadruplets `points` et ajoute, pour chaque sonde, un tableau
parallèle `point_sources`. Chaque point conserve la liste de ses sources sous la
forme `[start_ms, end_ms_ou_null, somme_décimale_exacte, nombre, min, max,
dernier_instant_mesuré_ms]`. Une fin `null` désigne une observation brute à son
instant réel ; sinon les bornes désignent l'intervalle stocké complet. Le timestamp
d'un point regroupé reste un repère de regroupement, jamais un instant mesuré
inventé. La réponse conserve séparément les bornes demandées `start` et `end`.

Aux résolutions 15 et 60 minutes, les sommes et compteurs des sources sont
combinés pour calculer la moyenne pondérée, jamais une moyenne des moyennes
arrondies. La liste des sources conserve les intervalles séparés et les trous :
une plage englobante ne devient pas une preuve de couverture continue. Les
intervalles sont rendus séparément, avec leurs propres statistiques complètes ;
le détail du groupe permet d'inspecter ses sources et son compteur. Une moyenne
d'agrégat est nommée comme telle, pas comme dernière température instantanée.
Les observations brutes gardent leurs positions réelles et restent distinctes.

Le cache frontend reprojette les sources pour chaque fenêtre : il conserve les
tranches chevauchantes entières et ne garde que les observations brutes comprises
dans la sélection. Il recalcule les statistiques du groupe à partir des sommes
et compteurs, sans compter une tranche deux fois. Un aperçu plus grossier ne vaut
pas une réponse de résolution plus fine ; un aperçu ne couvrant qu'une partie de
la sélection est explicitement signalé pendant la récupération des données.
La condensation étant irréversible, les statistiques exactes d'une sous-partie
de tranche ne sont pas disponibles.

## Remplacer une affectation sans déplacer l'historique

Une sonde ayant au moins une mesure minute ou une tranche ne change jamais de
zone : le modèle (`clean()` et `save()`) et la commande refusent ce déplacement.
Le code, l'emplacement (`--location`) et l'état actif restent modifiables, sous
réserve des contraintes d'unicité. Sans historique, une correction de zone reste
possible en mode normal.

Pour affecter la même voie à une autre zone, utiliser `--replace` et **un nouveau
code**, unique dans l'organisation, y compris parmi les sondes inactives :

```bash
sudo -u polypbase ../.venv/bin/python manage.py link_iminilide_probe \
    --organization "Aquarium de Paris" --zone "<nouvelle zone>" \
    --serial 1162040825 --channel 12 --code "ETUVE 25 G V2" --replace
```

Cette commande désactive l'ancienne sonde active et crée une nouvelle `Probe`
pour la même voie dans la zone demandée. Elle ne renomme ni ne déplace l'ancienne.
Les minutes, tranches et agrégats historiques restent attribués à leur zone
d'origine et restent consultables avec la sonde inactive. Aucun historique n'est
copié dans la nouvelle zone.

La désactivation, la création et les deux audits sont atomiques : `UPDATE` sur
l'ancienne sonde avec `replacement_probe_id`, `CREATION` sur la nouvelle avec
`replaced_probe_id`. Une erreur d'audit ou une collision de code annule toute
l'opération. Une seule sonde peut être active pour une voie; plusieurs versions
inactives sont conservées. Réactiver une ancienne version alors qu'une autre
est active est refusé par la base.

Sans sonde active, le remplacement prend la dernière version inactive comme
prédécesseur. En mode normal, une ancienne sonde inactive ayant un historique
n'est pas réactivée pour la déplacer : une nouvelle sonde est créée avec un
nouveau code. Aucune liaison ne reprend une voie appartenant à une autre
organisation, même si ses sondes sont inactives; la zone demandée doit aussi
appartenir à l'organisation indiquée.

## Reprise stricte de la collecte

Le point de reprise utilise le dernier instant mesuré de **toutes les sondes de
la voie**, actives ou inactives : maximum des minutes et des `last_measured_at`
des tranches. Il respecte la durée disponible sur le cloud. Cette règle vaut
après plusieurs remplacements, sans mesure dans la version intermédiaire, et
après une désactivation suivie d'une création manuelle sur la même voie.

La fenêtre demandée au cloud peut revenir en arrière pour lever l'ambiguïté de
l'heure d'hiver. Le service d'enregistrement applique donc aussi une barrière
stricte : la nouvelle sonde n'accepte aucun instant **inférieur ou égal** au
maximum historique des autres sondes de la voie, même si le cloud renvoie un
ancien payload ou toute la journée du changement d'heure. Le premier instant
strictement postérieur peut être accepté, y compris dans la même tranche de
15 minutes que la dernière ancienne mesure. Une dernière valeur `0` reste une
mesure et établit la même barrière.

Les horodatages ambigus sans retour arrière observable sont écartés et comptés,
jamais devinés. Ils peuvent être récupérés lors d'une collecte dont la réponse
permet de distinguer les deux occurrences. La reprise ne réattribue pas les
mesures précédentes à la nouvelle zone et ne reconstruit pas de données absentes.
