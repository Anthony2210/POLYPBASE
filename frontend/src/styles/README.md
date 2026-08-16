# Architecture CSS

`index.css` est l'unique feuille importee par React. Les couches CSS fixent la
cascade avant toute notion de specificite :

1. `tokens` definit les couleurs, dimensions et rythmes communs.
2. `base` normalise le document, la typographie et l'accessibilite.
3. `layout` organise la navigation et l'espace de travail.
4. `components` contient les primitives reutilisables.
5. `pages` ne contient que les compositions propres a un ecran.
6. `responsive` reorganise ces compositions selon le support.
7. `print` isole le rendu des etiquettes.

## Heritages

Les pages heritent des controles, panneaux, modales et etats partages. Une
page ne doit pas recreer un bouton, un champ, un panneau ou une fenetre deja
present dans `components/`. Les variantes utilisent les classes d'etat
`is-active`, `is-selected`, `is-danger`, `is-primary` et `is-wide`.

Les selecteurs groupes avec `:where()` gardent une specificite faible. Les
couleurs et dimensions reutilisees viennent exclusivement de `tokens.css`.
Une valeur locale reste acceptable lorsqu'elle decrit une contrainte propre a
un composant, comme la taille d'une planche d'etiquettes.

## Responsivite

Le DOM reste commun aux trois supports. `tablet.css` et `phone.css` portent les
changements globaux de navigation, de colonnes et de dimensions tactiles. Une
feuille de page peut conserver une adaptation locale lorsque son composant
change reellement de structure, sans recreer les styles du composant.

- Bureau : a partir de `1024px`, navigation laterale et outils de gestion.
- Tablette : principalement de `760px` a `1023px`, navigation haute et saisie
  tactile en paysage.
- Telephone : sous `760px` ou en portrait etroit, une colonne et navigation
  basse.

Les nouveaux seuils doivent reprendre ces valeurs communes. Un seuil local
supplementaire doit correspondre a une contrainte mesurable du composant.

## Regles de maintenance

- Rechercher une primitive existante avant d'ajouter une classe.
- Garder les selecteurs courts et sans identifiant.
- Faire heriter les composants avant d'ajouter une declaration locale.
- Eviter `!important`, sauf pour neutraliser une contrainte externe documentee.
- Utiliser les couleurs semantiques : primaire, succes, avertissement, danger.
- Conserver les ombres pour les surfaces flottantes, pas pour les sections.
- Tester le build et les trois formats apres une modification visuelle.

Les anciennes feuilles monolithiques ne sont plus chargees. Aucun style ne
doit etre ajoute en dehors de cette architecture.

`npm run check:css` controle ce contrat. La commande est aussi executee avant
chaque build de production.
