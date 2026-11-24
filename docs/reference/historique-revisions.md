# Historique et révisions des boîtes

## Lire l'historique

L'API de fiche et la route `GET /api/boxes/<box_id>/history/` ne publient pas les révisions stockées. Les mutations ordinaires de mouvement gardent leur [contrat propre](boites-cycle-de-vie.md#correction-dun-mouvement). Une période d'emplacement est distincte d'un mouvement enregistré.

La reconstruction historique distingue observation, précision temporelle, preuve source et décision de placement. Une semaine ISO ne fournit pas un instant. Une source incohérente reste visible comme conflit plutôt que d'être déplacée silencieusement. Voir l'[annexe mouvements historiques](annexes/historical-movements.md) pour les preuves et règles d'attribution du chantier Aquarium de Paris.

## Simuler une correction

`GET /api/boxes/<box_id>/history/preview/?start=…&end=…` renvoie l'état initial pour deux bornes ISO8601 avec fuseau. `POST` sur la même route simule un brouillon complet dans un corps JSON, sans paramètres de requête. Session, CSRF et Admin actif de l'institution explicitement sélectionnée sont requis. Les boîtes absentes, fusionnées ou étrangères sont indiscernables (`404`). Réponse privée, `no-store`. Ni superutilisateur seul ni identité soumise par le client ne donne d'autorité.

La simulation compare avant/après, identités et événements affectés, périodes, attribution thermique, lacunes et conflits. Ses valeurs décimales sont des chaînes; zéro, négatif et null restent distincts. Les références de preuve sont bornées, sans audit brut ni cellules confidentielles. `not_reported_after` signifie que le moteur ne relève plus le conflit, pas qu'une résolution est certifiée. La fenêtre est limitée à 366 jours écoulés; 100 opérations et 100 justifications au plus, plus des budgets de comparaison et de lecture. Un lot rejeté ne produit pas de simulation partielle. L'API n'approuve, ne sauvegarde, ne publie et n'applique rien. [Exemples du contrat d'origine](../archive/sources-fusion/history-preview-api.md).

## Stockage des révisions

- `BoxHistoryStream` réserve l'identité institution + boîte d'origine. Le pointeur vers la boîte peut devenir null sans perdre la trace. Une tête désigne un préfixe explicite; un successeur inséré ne devient pas automatiquement publié. La tête ne saute pas de révision et ne recule pas.
- `BoxHistoryRevision` conserve opérations typées ordonnées, preuves minimales, motif, auteur, empreintes et clé d'idempotence. La séquence et la clé sont uniques par flux. La suppression d'un compte peut détacher sa référence sans réécrire l'auteur capturé ni le contenu.
- `BoxHistoryEventIdentity` réserve les identités de preuves persistées ou d'événements insérés dans un flux. Les révisions composent un historique effectif sans réécrire la preuve originale, la boîte, les mesures ni l'état scientifique courant.

Ce socle n'est pas un service public de publication. L'intégrité du stockage ne remplace pas l'autorisation d'une requête. L'activation d'un futur publieur exige une autorité vérifiée, la comparaison de tête et l'ajout/avancement atomiques. Les [preuves PostgreSQL datées](../archive/rapports/history-revision-postgres-qa.md) ne valent pas validation de production.
