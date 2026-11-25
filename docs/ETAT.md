# État de Polypbase — 24 novembre 2026

## Dépôt et production

- Référence `main` observée dans ce dépôt : `9e00e2c` (intégration des lots d’audit via la PR #37). L’intégration Git ne prouve pas un déploiement ; aucun contrôle de production n’a été effectué ici.
- **Release actuellement déployée : non vérifiée.** La dernière release explicitement rapportée dans le journal historique conservé est `cb07a2c` (3 novembre 2026, rapport opérateur); ce n'est pas une attestation de l'état actuel. L'exécution de corrections de données reste distincte de la livraison du code.
- [Historique intégral de l'ancien état](archive/etat-historique-2026-10-14_2026-11-16.md) et [règles de déploiement](operations/deploiement-exploitation.md).

## Périmètre du code relu pour PR4

| Domaine | Disponible et limites |
|---|---|
| Administration | Équipe, Inventaire, Espèces, Emplacements, Envois et réceptions et Historique sur ordinateur. Les anciennes sections Référentiels partagés et Institutions ne font plus partie du parcours. |
| Boîtes | Validation ADN par lien déclaré, révocable et sans héritage; registre des anciens Local ID et révisions historiques distincts. |
| Échanges | Proposer et consulter un don dans React, sans acceptation ni transfert exécuté. Partage externe en lecture seule par API, sans écran React; CSV v1 retiré. |
| Espèces | Catalogue et fiches institutionnelles montés. Gestion explicite des espèces déjà utilisées, même sans profil ; résolution implicite retirée (`410` après autorisation). Aperçu AAA par API, application interne seulement. |
| Mesures | Import exact-date avec applicateur API restreint; pas de parcours React monté. Semaine seule sans jour exact non persistable. |
| Sondes | Collecte iMinilide et contrôle quatre heures dans le code; connectivité physique et état du timer de production non vérifiés. |
| Démo | Socle isolé, pas d'autorisation d'utiliser un instantané réel ni d'ouvrir l'accès. |

## Décisions ou vérifications restantes

- Avant une release, établir SHA, date et schéma réellement en service. Vérifier séparément les anciens constats HSTS, les sondes et les sauvegardes; ne pas les supposer résolus ou encore d'actualité.
- Avant d'activer la précision semaine seule : politique de chronologie en présence d'un repiquage, gestion des états scientifiques non résolus et attribution des emplacements/températures sans inférence.
- Avant d'ouvrir l'acceptation des dons : politique de stock/statut, matérialisation destination et droits sur l'historique.
- Les [dossiers des campagnes ponctuelles](archive/README.md) sont des traces historiques, pas des procédures à rejouer. Le retrait des outils dédiés ne vérifie pas l’état des données en production. Toute nouvelle reprise exige ses propres preuves et son autorisation.

Voir les [décisions durables](DECISIONS.md) et les [références par domaine](INDEX.md).
