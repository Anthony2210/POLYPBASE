# Taxonomie, espèces et souches

`Species` reste la référence partagée des souches et boîtes. `InstitutionSpecies` porte le nom scientifique et l'AphiaID propres à une institution, sans déplacer les relations historiques ni basculer tous les consommateurs vers ce nom. `OrganizationSpeciesCode` porte le code institutionnel AAA; ce n'est ni le code partagé ni une clé d'URL. Le code BBB et la provenance biologique ne se déduisent pas du fournisseur, d'un don ou d'une chaîne historique.

## Catalogue institutionnel

Administration > Espèces demande un Admin actif dans l'institution sélectionnée. Le catalogue ouvre le profil par identifiant, pas par AAA. Les fiches historiques sans profil restent en lecture seule. Le POST `…/institution-species/resolve/` vérifie l'autorisation puis répond `410 institution_species_resolution_retired`, sans profil, code ni audit. React ne l'appelle pas; ouvrir une fiche ou un dialogue n'attribue rien.

Création explicite via POST, édition d'un profil via PATCH avec `expected_revision`. L'édition du nom et de WoRMS ne dépend pas d'AAA; la création de souche exige un code utilisable. La recherche WoRMS propose des résultats de rang espèce; l'utilisateur sélectionne explicitement une référence ou saisit un nom sans référence. Une référence devenue incompatible doit être retirée explicitement ou remplacée. Pas de correction taxonomique automatique. Les effectifs et dates qui dépendent des boîtes n'utilisent que la collection complète; les bornes sont celles des relevés canoniques, pas une date technique de création.

## Souches

La création institutionnelle sélectionne un pays autorisé et réutilise son code BBB ou demande un code de trois lettres. Acquisition, coordonnées et nom descriptif sont enregistrés uniquement s'ils sont renseignés. Django alloue le numéro X et le code; une intention identique réutilise son `request_id`, une intention modifiée en utilise un autre. Les codes et libellés historiques ne sont pas normalisés à la lecture. Le détail présente Souches et boîtes avec date de premier et dernier relevé; sans relevé, ne pas substituer une date de création. Pas de correction d'espèce d'une boîte ni d'édition de souche dans ce parcours.

## Changement AAA

`GET …/code-change-preview/?proposed_code=…` est un aperçu consultatif : `200` n'implique pas `feasible`, ne réserve rien et ne modifie rien. Aucun écran React n'est monté pour ce changement. Le service interne `apply_species_code_change` n'est invoqué par aucune route : il exige le plan attendu, une intention idempotente et la revalidation sous verrous. Changement cohérent des identités concernées et audit dans une seule transaction; pas de renommage implicite, de manifeste autorisé par un aperçu ou de preuve de validation PostgreSQL par SQLite.

Voir l'[annexe institutionnelle](annexes/institution-species-foundation.md), le [contrat d'origine du service](../archive/sources-fusion/species-code-change-apply.md) et les [routes](api.md).
