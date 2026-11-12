# Partages scientifiques — dossier de revue frontend

Date : 2026-11-12. Travail local non committé, sans push ni déploiement.

## 1. Baseline et périmètre

- Worktree : `exchange-box-sharing-ui/POLYPBASE`.
- Branche vérifiée : `feat/exchange-box-sharing-ui`.
- HEAD initial et final : `380235543d7be7d715b5de2b81fade1f38e90936`.
- État initial propre. État final : modifications de ce dossier de revue,
  du contexte frontend, de l'interface, des tests et du suivi i18n uniquement.
- Aucun fichier sous `backend/` modifié. Aucune migration, nouvelle dépendance
  produit, bibliothèque UI, bibliothèque de routage ou gestion d'état ajoutée.
- Aucune utilisation de Neon, d'une base partagée, de comptes réels ou de SMTP.

## 2. Administration

L'entrée existante **Administration > Envois et réceptions** conserve
`/administration/transfers`, sa position et son état actif dans `AdminFlowNav`.
L'ancien composant de transfert biologique reste non monté.

Fonctions : recherche serveur de boîte dans la structure active (20 résultats
par page), sélection explicite des informations du catalogue serveur,
validation des dépendances, choix LIVE/FROZEN sans présélection, dates UTC
explicites, aperçu scientifique serveur, confirmation et création réelle.

La liste des envois est paginée côté serveur à 25 lignes, filtrable par boîte.
Elle expose destinataire, boîte, mode, état, échéances, divulgation, acceptation,
création/auteur, révocation, livraison, révision et métadonnées de capture.
La modification transmet `expected_policy_revision`. Une extension de
sélection et une recapture FROZEN doivent être confirmées. Un conflit de
révision bloque l'enregistrement jusqu'à la relecture explicite du brouillon.
La régénération exige une date d'invitation et ne prolonge jamais l'accès.
La révocation exige une confirmation et décrit sa limite sur les données déjà vues.

Le lien **Boîtes partagées reçues** ouvre l'espace personnel, également disponible
sur le profil. Il ne crée aucune appartenance ni autre navigation institutionnelle.

## 3. Destinataire et lecteur

- `/shared-boxes` : liste autoritative des grants acceptés du compte.
- `/shared-boxes/accept#token=...` : connexion existante si nécessaire,
  inspection POST non consommatrice, puis acceptation POST sur action explicite.
- `/shared-boxes/<uuid>` : composition scientifique séparée de `BoxPage`.

Le fragment reste dans l'URL originale pendant la connexion, sans stockage
persistant ni passage dans `next`. Le token est envoyé uniquement aux deux
POST d'invitation. Une acceptation confirmée le retire de l'URL et de l'état.
Après une déconnexion volontaire, le changement de compte demande de rouvrir
le lien original. Un lien consommé ne peut pas être distingué d'un lien invalide,
remplacé ou révoqué par le contrat backend : le message reste volontairement commun.

Le lecteur ne comporte aucune mutation de boîte, relation vers d'autres boîtes,
export, graphique connecté à l'inventaire, ou remplissage depuis un cache interne.
Il relit le grant explicitement, au retour sur la page, périodiquement et à
l'échéance connue. Un refus supprime le snapshot; il n'existe aucun fallback.

Le bandeau bleu clair « Lecture seule », avec le nom exact de la source,
est non dismissible et sticky dans le défilement document. Le header personnel
n'est pas fixé : aucun décalage arbitraire de header institutionnel n'est requis.

## 4. Frontières de sécurité à vérifier

| Garantie frontend | Source principale |
|---|---|
| Un seul contexte monté, pas de bootstrap institutionnel sur les routes personnelles | `frontend/src/AppEntry.tsx:12–29` |
| Toutes les URL Administration sont neutralisées hors desktop avant montage, même inconnues | `frontend/src/AppEntry.tsx:15–26` |
| Requêtes personnelles sans en-tête d'organisation, sans cache ni déduplication | `frontend/src/api/boxSharing.ts:40–58` |
| Même client de session/CSRF; option sans contexte limitée au lecteur | `frontend/src/components/LoginPage.tsx:10–74` |
| Vérification du compte et de son adresse avant/après les opérations | `frontend/src/api/recipientSession.ts:15–48` |
| Admin strict de la structure explicite, sans raccourci superuser | `frontend/src/api/sourceSharingSession.ts:5–12` |
| Retrait du workspace source après invalidation, blur/pagehide et réautorisation au retour | `frontend/src/components/sharing/BoxSharingAdminSection.tsx:33–58` |
| Retrait du contexte lecteur au changement de session/visibilité | `frontend/src/components/sharing/SharedAccessApp.tsx:14–91` |
| Annulation, génération et garde jusque dans l'updater React; clé masquée pendant render | `frontend/src/hooks/useSharingResource.ts:11–33` |
| Seul endpoint de contenu destinataire : le GET de grant | `frontend/src/components/sharing/SharedAccessApp.tsx:157–184` |
| Confirmation de divulgation et paramètres de révision/recapture | `frontend/src/components/sharing/ShareEditor.tsx:60–80` |

Ces gardes ne prouvent pas l'autorisation inter-institutionnelle. Django reste
l'autorité pour l'acteur, la structure, la boîte, l'acceptation, les transitions,
les expirations et chaque lecture de grant. Il n'existe aucune révocation push :
les changements distants sont constatés à la prochaine revalidation, pas instantanément.

## 5. Sémantique scientifique

- Le catalogue et les dépendances viennent de `options/`. La table locale
  associe seulement les noms du contrat à des traductions.
- Aucun champ initialement choisi, y compris le champ obligatoire :
  la sélection de la structure propriétaire doit être intentionnelle.
- L'aperçu doit porter exactement la sélection courante avant de permettre
  la création ou une modification de divulgation.
- L'historique disponible entier des champs choisis est partagé : aucune
  période restrictive n'est simulée.
- `0`, `null`, `blank`, `absent` et non divulgué ont des rendus distincts.
- Un historique autorisé vide n'est pas un historique non divulgué.
- Les semaines ISO ne sont pas converties en lundis d'observation.
  Une date historique non vérifiée n'est pas annoncée comme un jour exact attesté.
- Une version ou un champ scientifique inconnu bloque son affichage.
- Historique rendu par pages locales de 50 lignes, sans points ou totaux inventés.
- LIVE est décrit comme une projection non atomique; FROZEN comme une capture
  enregistrée dont une nouvelle révision conserve les anciennes captures côté serveur.

## 6. UX, composants et accessibilité

Réutilisations : Administration, `SearchField`, `BoxTrackingPreview` dans le seul
contexte source Admin, boutons partagés, `PageLoader`, `LoginPage`,
`ConfirmActionModal` et `ModalPortal`. Les confirmations rendent le fond applicatif
`inert` pendant le dialogue, en conservant le focus trap/retour existant.

Styles locaux dans `frontend/src/styles/pages/box-sharing.css`, agrégés dans
la couche pages. Tokens, typographie, séparateurs et densité existants réutilisés.
Seules trois valeurs bleu contextuel sont locales au bandeau produit demandé.
L'application actuelle est en thème clair; aucun thème sombre n'a été créé.

Contraste calculé à partir des couleurs effectivement rendues du bandeau :
**9,65:1**, identique pour son texte secondaire. Formulaires et checkboxes natifs,
labels visibles, focus visible, régions d'erreur et défilement horizontal annoncé.
Le lien d'évitement fonctionne au clavier sans remplacer le fragment d'invitation.
Aucun audit NVDA/VoiceOver complet n'est allégué.

## 7. Validation effectivement exécutée

Depuis `frontend/` :

- `npm ci --no-audit --no-fund` : versions du lockfile installées, lockfile inchangé.
- `node --test scripts/test-box-sharing.mjs` : **20 réussis**.
- `npm run typecheck` : réussi.
- `npm run check:css` : réussi, 33 fichiers CSS.
- `npm run build` : réussi, CSS + TypeScript + Vite.
- Suite complète : **2 642 réussis**, répartis sur les mêmes points d'entrée
  découverts par le runner existant : **806 / 1 014 / 822**, aucun échec final.

Commande de répartition utilisée (une valeur de `shard` par exécution séquentielle) :

```sh
node --input-type=module -e "import {spawnSync} from 'node:child_process'; import {discoverTestEntries} from './scripts/run-tests.mjs'; const {entries}=discoverTestEntries('./scripts'); const r=spawnSync(process.execPath,['--test','--test-concurrency=1','--test-reporter=tap','--test-shard=1/3',...entries.map(file=>'scripts/'+file)],{stdio:'inherit'}); process.exit(r.status ?? 1);"
```

Même commande avec `--test-shard=2/3`, puis `--test-shard=3/3`.
La tentative monolithique `npm run test:all` a dépassé sa limite de 600 s;
une tentative de lots parallèles a épuisé la mémoire Windows/Node. Elles ne
sont pas comptées comme réussies. Les trois lots ont ensuite tous terminé
avec succès en séquentiel. La découverte et les groupes d'import existants
sont préservés : pas d'omission ni d'exécution double des tests importés.

QA réelle Chromium, frontend buildé avec API entièrement synthétique en mémoire :

```sh
python scripts/qa-box-sharing.py --screenshots
```

Réussi : Administration, formulaire, aperçu, création, conflit, recapture FROZEN,
pagination/filtre, régénération, révocation/annulation, politique non configurée,
connexion/fragment/inspection/acceptation sans appartenance, liste reçue, UUID
inaccessible, invitation expirée/invalide/mauvais compte, révocation/expiration,
changement de compte en lecture, déconnexion, zéro/NULL/semaine et 50 lignes.
Supports : **1440×1000, 960×600, 390×844, 320×800**. Bandeau vérifié après défilement,
absence de débordement global, redirection tablette y compris route Administration
inconnue. Aucun appel d'inventaire ou en-tête institutionnel dans le parcours
lecteur. Zéro erreur JavaScript et zéro requête API inattendue. Captures inspectées
puis supprimées; serveur éphémère arrêté par le script.

Depuis `backend/`, dans un processus isolé SQLite explicite :

```sh
POLYPBASE_TEST_POSTGRES=0 DJANGO_DEBUG=1 uv run --frozen python manage.py test apps.sharing --settings=config.test_settings --noinput
```

**121 tests, OK, dont 17 ignorés** car ils exigent PostgreSQL. Cela ne revalide
pas la concurrence ou les captures transactionnelles PostgreSQL.

Depuis la racine : `git diff --check`, réussi.

## 8. i18n et contrôle préexistant non vert

150 nouvelles clés FR/EN; libellé anglais existant `adminTabTransfers` adapté à
« Sending and receiving ». Journal append-only : B29, T1101–T1401.
Japonais validé inchangé; nouveaux textes en repli anglais et manifeste à jour.

`python docs/translation-review/application/check_register.py` conserve exactement
deux erreurs préexistantes : **T1099/T1100, lot B28**, modification du placeholder
`{minutes}` dans `probeLatestInterval`. Elles ont été reproduites en validant
les 1 100 événements du HEAD baseline obtenu par `git show`. Aucun événement
B29 n'ajoute d'erreur. Aucun historique n'a été corrigé pour masquer cet échec.
Les tests i18n frontend, les fingerprints historiques et la parité TypeScript passent.

## 9. Limites et prérequis

- Activation : les deux limites backend de durée et l'envoi d'e-mails doivent
  être configurés. Sans cela, aucune création fonctionnelle n'est proposée.
- FROZEN dépend de `frozen_capture_available`; une capture non supportée n'est
  jamais simulée par le navigateur.
- Le résumé des grants reçus n'a pas de code de boîte : aucune requête source
  complémentaire n'est faite pour l'ajouter. Plusieurs grants d'une même source
  peuvent donc avoir une présentation semblable avant ouverture.
- Les listes reçues et les observations ne sont pas paginées au niveau API.
  Le DOM est borné localement, mais le payload initial reste celui du backend.
- Pas de création de compte, d'appartenance, de boîte destination, d'Excel,
  de transfert biologique ou d'export.
- Pas de QA avec SMTP réel ni backend HTTP vivant. Les tests Django couvrent
  le serveur séparément; la QA visuelle utilise exclusivement des réponses simulées.
- Pas de garantie d'effacement des informations déjà vues ni de révocation
  distante instantanée. La réautorisation et le serveur déterminent le prochain accès.

## 10. Fichiers du chantier

Créés :

- `frontend/src/AppEntry.tsx`
- `frontend/src/api/boxSharing.ts`
- `frontend/src/api/recipientSession.ts`
- `frontend/src/api/sourceSharingSession.ts`
- `frontend/src/hooks/useSharingResource.ts`
- `frontend/src/hooks/useSharingConfirmation.ts`
- `frontend/src/utils/boxSharing.ts`
- `frontend/src/components/sharing/BoxSharingAdminSection.tsx`
- `frontend/src/components/sharing/ShareEditor.tsx`
- `frontend/src/components/sharing/SharingControls.tsx`
- `frontend/src/components/sharing/SourceBoxLookup.tsx`
- `frontend/src/components/sharing/ScientificSnapshotView.tsx`
- `frontend/src/components/sharing/SharedAccessApp.tsx`
- `frontend/src/styles/pages/box-sharing.css`
- `frontend/scripts/sharing-test-harness.mjs`
- `frontend/scripts/test-box-sharing.mjs`
- `frontend/scripts/qa-box-sharing.py`
- `docs/BOX_SHARING_UI_REVIEW.md`

Modifiés :

- `frontend/src/main.tsx`, `frontend/src/App.tsx`
- `frontend/src/api/client.ts`
- `frontend/src/components/AdminView.tsx`, `LoginPage.tsx`, `ProfileView.tsx`
- `frontend/src/utils/boxCollection.ts`, `frontend/src/styles/index.css`
- `frontend/src/i18n/fr.ts`, `frontend/src/i18n/en.ts`
- `frontend/scripts/app-operation-test-harness.mjs`
- `frontend/scripts/test-admin-transfer-qr.mjs`
- `frontend/scripts/test-box-collection-bootstrap.mjs`
- `frontend/scripts/test-i18n-japanese.mjs`
- `frontend/scripts/test-lab-configuration.mjs`
- `frontend/scripts/test-null-strobila.mjs`
- `docs/context/frontend-ux.md`
- `docs/translation-review/application/register.csv`
- `docs/translation-review/japanese/ja-manifest.csv`
- `docs/translation-review/japanese/build_ja_catalogs.py`

## 11. Revue indépendante et verdict

Une revue statique en lecture seule a identifié puis revérifié les frontières
source et destinataire. Ses deux constats de session ont été corrigés; aucune
écriture n'a été déléguée. Ce contrôle ne remplace pas la revue indépendante
complète demandée par Anthony.

Priorités de la prochaine revue : montage exclusif des contextes, mutations du
client commun, perte de session pendant une réponse ou confirmation, token consommé
avec réponse interrompue, révision/extension/recapture, absence de données non
divulguées, vrais réglages SMTP/FROZEN, contrat d'autorisation serveur et garde
des URL Administration sur pointeur tactile.

**READY FOR INDEPENDENT REVIEW**, avec le défaut B28 préexistant explicitement
signalé. Ce verdict n'est ni une autorisation de production, ni un déploiement,
ni une attestation de tests PostgreSQL ou d'e-mails réels.
