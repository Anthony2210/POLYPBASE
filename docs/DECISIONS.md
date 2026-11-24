# Décisions produit et techniques

Une entrée consigne un arbitrage et sa portée. Elle ne prouve ni une mise en production ni une autorisation de mutation. Les dates sont celles des décisions documentées ou des jalons qui les attestent.

| Date ou jalon | Décision | Portée / référence |
|---|---|---|
| Invariant projet | Zéro est une observation, jamais une absence. Django décide des droits et transitions dans l'institution active; préserver auteurs, lignée et audit. | [AGENTS.md](../AGENTS.md) |
| État du 14/10/2026 | Un relevé canonique par boîte et semaine ISO. Un POST en conflit ne corrige pas l'existant; correction explicite et auditée. | [Mesures](reference/mesures-temperatures-sondes.md) |
| État du 14/10/2026 | Cible semaine seule : lundi comme clé ISO, jour absent; pas de jour inventé. Si un repiquage pertinent rend la chronologie ambiguë, demander le vrai jour ou annuler; pas de forçage. | Cible distincte du stockage actuel : [imports](reference/imports-exports-transferts.md) |
| État du 14/10/2026 | Un déplacement ordinaire par semaine ISO Europe/Paris; corriger l'événement existant dans le bandeau Origine → Destination. Une frontière de période n'est pas une preuve de mouvement. | [Boîtes](reference/boites-cycle-de-vie.md) |
| État du 14/10/2026 | Administration réservée à l'ordinateur; abandon des alertes et du grand catalogue partagé. Préserver les traces d'anciennes alertes. | [Frontend](reference/frontend.md) · [Architecture](reference/architecture.md) |
| 29/10/2026 | Condenser par défaut après 14 jours, en tranches de 15 minutes; suppression irréversible des points condensés. Conserver le dernier instant exact, sans reconstruire une valeur inconnue. | [Sondes](reference/mesures-temperatures-sondes.md#sondes-iminilide-collectées-décision-du-29-octobre-2026) |
| 04 et 17/11/2026 | Contrôle thermique factuel sur données mesurées; fenêtre brute de quatre heures, sans interpréter silence ou écart comme alerte. | [Contrôle thermique](reference/mesures-temperatures-sondes.md) |
| 12/11/2026 | Séparer les autorités de déploiement, de sauvegarde et de migration. Un schéma en attente bloque la release. | [Exploitation](operations/deploiement-exploitation.md) |
| PR #19, 17/11/2026 | Preuve ADN déclarée et révocable pour une boîte, sans héritage ni identification automatique. | [Boîtes](reference/boites-cycle-de-vie.md) |
| PR #24, 19/11/2026 | Pas de résolution implicite d'une espèce historique. Gérer explicitement le profil institutionnel sans réécriture des données historiques. | [Taxonomie](reference/taxonomie-especes.md) |
| Jalon proposition de don | Proposer n'est pas transférer : ni accès à l'historique source ni propriété accordée au destinataire. | [Dons](reference/imports-exports-transferts.md) |
| Jalon démo isolée | Instance indépendante envisagée; pas d'instantané réel ni d'ouverture avant autorisation et contrôles. | [Démo isolée](operations/demo-isolee.md) |
| 24/11/2026 | Retirer les sections d’administration Organisations, Références et Taxonomie. | Conserver le parcours Espèces et les modèles ; retirer uniquement les API propres aux sections supprimées. |
| 24/11/2026 | Anthony confirme l’application en production des opérations ponctuelles : CCA-EOB V3 et réaffectation des cinq mesures, Box 1902, PCA, CLA-SME, espèces urgentes et variantes, nettoyage des données d’essai, désactivation HS, lots de températures historiques, import 2026 et réconciliation des mouvements. | Confirmation opérateur ; archiver l’outillage ponctuel, pas les protections des données ni les dépendances vivantes. |
| 24/11/2026 | Retirer `'unsafe-inline'` de `script-src` uniquement si l’absence de scripts inline est vérifiée. | Vérifier le build frontend et les gabarits HTML servis ; ne pas changer `style-src`. |

Voir [l'état du projet](ETAT.md) pour les questions ouvertes et les [archives](archive/README.md) pour les preuves datées.
