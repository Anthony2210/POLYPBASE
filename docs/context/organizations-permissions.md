# Organisations, comptes et permissions

## Modèle actuel

`Organization` représente l'institution qui possède un espace de travail. Un utilisateur peut avoir une `OrganizationMembership` active dans plusieurs institutions, avec un rôle propre à chacune :

- `admin` : administration de l'institution active;
- `lab_technician` : écriture des données de laboratoire;
- `viewer` : consultation.

Responsable n'est pas un quatrième rôle. C'est un privilège institutionnel porté par un membership Admin : `role="admin"` et `is_responsable=True`. Une contrainte de base interdit de conserver ce privilège avec un autre rôle. Aucun Admin existant n'est désigné automatiquement.

Un Responsable actif conserve toutes les capacités Admin et peut, en plus, inviter, promouvoir, rétrograder, activer ou désactiver des Admins ordinaires dans son institution, sous réserve des protections du dernier Admin actif. Un Admin ordinaire continue de gérer les Techniciens et Lecteurs, mais ne peut pas nommer ou modifier un autre Admin. Aucun utilisateur produit ne peut attribuer le privilège Responsable ni modifier un membership qui le porte par l'endpoint générique.

Un Responsable actif peut renoncer uniquement à son propre privilège lorsqu'un autre membership actif `role="admin"`, `is_responsable=True` existe déjà dans la même institution. L'opération conserve son rôle Admin, verrouille l'institution et écrit l'audit dans la même transaction. La définition produit des Responsables inactifs reste volontairement non décidée; ils ne sont pas comptés pour cette opération active et les mutations génériques les concernant restent bloquées.

Le superutilisateur Django est un mécanisme technique, pas un rôle produit à reproduire dans l'interface. Il conserve un break-glass explicite sur les memberships ordinaires. L'attribution ou le retrait forcé du privilège Responsable passe par la commande contrôlée `set_institution_responsable`, jamais par l'interface produit ou le `ModelAdmin` en lecture seule. Les capacités de sponsor ou de création d'institution restent hors périmètre.

Les comptes utilisent les sessions Django. Les parcours de connexion, invitation et récupération de mot de passe vivent dans `backend/apps/accounts/`; la configuration du transport e-mail vient de l'environnement. Une invitation ou un lien de réinitialisation constitue un secret d'accès pendant sa validité et ne doit jamais apparaître dans les logs ou la documentation.

## Organisation active

Le frontend conserve l'identifiant de l'organisation choisie et l'envoie dans l'en-tête `X-Organization-Id`. Le backend résout ce contexte dans `backend/apps/accounts/permissions.py` et ne l'accepte que si l'utilisateur peut réellement accéder à l'institution demandée.

Ce mécanisme n'est que le point de départ. Chaque endpoint doit encore limiter au serveur :

- listes et détails;
- créations, mises à jour et suppressions;
- identifiants fournis par le client;
- relations entre objets;
- choix proposés par les serializers;
- agrégats, compteurs et suggestions;
- exports et actions groupées.

Masquer un bouton ou filtrer une liste dans React ne protège pas l'API. Django reste la source de vérité pour les permissions.

## Relations et référentiels

Lorsqu'une mutation relie deux objets, vérifier la portée des deux côtés. Une boîte ne doit par exemple pas recevoir une zone d'une autre institution, même si son identifiant a été transmis manuellement. La même vigilance s'applique aux transferts, membres, sondes, imports et objets sélectionnés en masse.

Les taxons, espèces, souches et autres référentiels taxonomiques suivent leur portée partagée actuelle. Ne pas les convertir implicitement en objets institutionnels, ni déduire de leur caractère partagé un accès aux mesures ou aux cultures.

`PartnerInstitution` et `SharingAgreement` existent dans le schéma. Leur présence n'accorde aucun accès transversal implicite et ne prouve pas qu'un parcours de partage inter-institution est disponible. Le multi-base, le partage automatique et la création sponsorisée d'institutions restent des idées tant qu'une décision produit et une implémentation ne les établissent pas.

## Permissions et audit

Une action sensible doit vérifier, dans cet ordre logique :

1. utilisateur authentifié;
2. appartenance active et rôle suffisant dans l'organisation active;
3. appartenance des objets ciblés à cette organisation;
4. validité métier de l'action;
5. audit dans la bonne institution lorsque la trace fait partie de l'opération.

Pour une écriture composée, déterminer si la donnée et son `AuditLog` doivent partager une transaction. Ne pas généraliser une convention depuis une autre vue sans inspecter le service concerné.

## Tests attendus

Toute évolution sensible à l'organisation doit inclure au moins deux institutions et vérifier :

- accès autorisé dans l'institution active;
- refus ou absence d'objet pour l'autre institution;
- rejet d'un identifiant étranger transmis directement;
- impossibilité de créer une relation croisée;
- cohérence des permissions entre interface et backend;
- attribution correcte de l'audit.

Les tests backend doivent utiliser une configuration isolée. Les commandes sont dans [`development-deployment.md`](development-deployment.md).

## Points d'entrée

- Modèles de comptes : `backend/apps/accounts/models.py`.
- Résolution de l'organisation : `backend/apps/accounts/permissions.py`.
- API des membres, invitations et renoncement Responsable : `backend/apps/accounts/api_views.py`.
- Attribution plateforme du privilège : `backend/apps/accounts/management/commands/set_institution_responsable.py`.
- Modèle d'institution : `backend/apps/organizations/`.
- Client API frontend : `frontend/src/api/client.ts`.
- Traçabilité du modèle : [`../tracabilite_mcd.md`](../tracabilite_mcd.md).
