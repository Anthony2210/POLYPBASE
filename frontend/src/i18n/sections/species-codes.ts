export const speciesCodes = {
  speciesCodeChangeActionBlocked: {
    fr: 'Identifiant bloqué',
    en: 'Identifier blocked',
  },
  speciesCodeChangeActionRename: {
    fr: 'Identifiant à modifier',
    en: 'Identifier to change',
  },
  speciesCodeChangeActionUnchanged: {
    fr: 'Identifiant inchangé',
    en: 'Identifier unchanged',
  },
  speciesCodeChangeAfter: { fr: 'Après', en: 'After' },
  speciesCodeChangeBefore: { fr: 'Avant', en: 'Before' },
  speciesCodeChangeBlockers: {
    fr: 'Situations bloquantes',
    en: 'Blocking conditions',
  },
  speciesCodeChangeBoxPreviewHint: {
    fr: 'Survolez ou sélectionnez au clavier un identifiant pour afficher le suivi de la boîte.',
    en: 'Hover or keyboard-focus an identifier to show the box tracking.',
  },
  speciesCodeChangeBoxes: { fr: 'Boîtes', en: 'Boxes' },
  speciesCodeChangeBoxesConcerned: {
    fr: 'Boîtes concernées',
    en: 'Boxes affected',
  },
  speciesCodeChangeBreakdown: {
    fr: 'identifiants à modifier : {rename}, bloqués : {blocked}, inchangés : {unchanged}',
    en: 'identifiers to change: {rename}, blocked: {blocked}, unchanged: {unchanged}',
  },
  speciesCodeChangeCodeInvalid: {
    fr: 'Le code espèce doit contenir exactement trois lettres, de A à Z.',
    en: 'The species code must be exactly three letters, A to Z.',
  },
  speciesCodeChangeCodeLabel: { fr: 'Code espèce', en: 'Species code' },
  speciesCodeChangeCodeRequired: {
    fr: 'Saisissez le code espèce proposé.',
    en: 'Enter the proposed species code.',
  },
  speciesCodeChangeConsequence: { fr: 'Conséquence', en: 'Consequence' },
  speciesCodeChangeCurrent: { fr: 'Actuel', en: 'Current' },
  speciesCodeChangeDependencyActions: {
    fr: 'Actions de l’historique mentionnant l’identifiant actuel',
    en: 'History actions mentioning the current identifier',
  },
  speciesCodeChangeDependencyLabels: {
    fr: 'Boîtes dont l’étiquette physique serait à vérifier',
    en: 'Boxes whose physical label would need review',
  },
  speciesCodeChangeDependencyLineage: {
    fr: 'Liens de lignée, non concernés',
    en: 'Lineage links, unaffected',
  },
  speciesCodeChangeDependencyMergedRecords: {
    fr: 'Anciennes fiches fusionnées, conservées telles quelles',
    en: 'Former merged records, kept as they are',
  },
  speciesCodeChangeDependencyPreviousIds: {
    fr: 'Boîtes ayant déjà un ancien identifiant local',
    en: 'Boxes that already have a former Local ID',
  },
  speciesCodeChangeDependencyQr: {
    fr: 'QR codes, non concernés',
    en: 'QR codes, unaffected',
  },
  speciesCodeChangeDependencyTransfers: {
    fr: 'Éléments de transfert conservant l’identifiant de leur époque',
    en: 'Transfer items keeping the identifier of their time',
  },
  speciesCodeChangeDependencyTransplants: {
    fr: 'Répartitions de repiquage conservant l’identifiant de leur époque',
    en: 'Transplant allocations keeping the identifier of their time',
  },
  speciesCodeChangeEmpty: {
    fr: 'Cette espèce n’a aucune souche ni boîte dans cette structure.',
    en: 'This species has no strain or box in this institution.',
  },
  speciesCodeChangeFeasible: {
    fr: 'Aucune situation bloquante détectée',
    en: 'No blocking condition detected',
  },
  speciesCodeChangeFilterAll: { fr: 'Toutes', en: 'All' },
  speciesCodeChangeFilterLabel: {
    fr: 'Filtrer les boîtes',
    en: 'Filter boxes',
  },
  speciesCodeChangeForbidden: {
    fr: 'Ce compte ne peut pas prévisualiser un changement de code espèce. Merci de contacter un administrateur.',
    en: 'This account cannot preview a species code change. Please contact your administrator.',
  },
  speciesCodeChangeHint: {
    fr: 'Trois lettres, de A à Z.',
    en: 'Three letters, A to Z.',
  },
  speciesCodeChangeHistoryNote: {
    fr: 'Les anciens identifiants locaux continueraient de désigner les mêmes boîtes. Les enregistrements historiques conservent l’identifiant de leur époque.',
    en: 'Former Local IDs would keep resolving to the same boxes. Historical records keep the identifier of their time.',
  },
  speciesCodeChangeHistoryTitle: {
    fr: 'Historique et étiquettes',
    en: 'History and labels',
  },
  speciesCodeChangeIdentifiersToChange: {
    fr: 'Identifiants locaux à modifier',
    en: 'Local IDs to change',
  },
  speciesCodeChangeImpossible: {
    fr: 'Modification impossible',
    en: 'Change not possible',
  },
  speciesCodeChangeImpossibleNote: {
    fr: 'Tant qu’une situation bloquante subsiste, aucun identifiant ne peut être modifié. Rien n’a été appliqué.',
    en: 'While a blocking condition remains, no identifier can be changed. Nothing has been applied.',
  },
  speciesCodeChangeInvariant: {
    fr: 'Seul le code espèce change. Le lieu de collecte, le numéro de souche, le numéro de boîte et l’identifiant global restent identiques.',
    en: 'Only the species code changes. The collection location, strain number, box number and Global ID stay the same.',
  },
  speciesCodeChangeKept: { fr: 'Conservé', en: 'Kept' },
  speciesCodeChangeLabelsNote: {
    fr: 'Aucune étiquette n’est imprimée ni sélectionnée par cette prévisualisation.',
    en: 'This preview neither prints nor selects any label.',
  },
  speciesCodeChangeLabelsToReview: {
    fr: 'Étiquettes physiques à vérifier',
    en: 'Physical labels to review',
  },
  speciesCodeChangeLoadError: {
    fr: 'La prévisualisation n’a pas pu être calculée. Réessayez.',
    en: 'The preview could not be computed. Try again.',
  },
  speciesCodeChangeLoading: {
    fr: 'Calcul des conséquences…',
    en: 'Computing the consequences…',
  },
  speciesCodeChangeMissingGlobalId: {
    fr: 'Boîtes sans identifiant global',
    en: 'Boxes without a Global ID',
  },
  speciesCodeChangeMoreRecords: {
    fr: 'et {count} autres',
    en: 'and {count} more',
  },
  speciesCodeChangeNoImpact: {
    fr: 'Aucune souche ni boîte concernée',
    en: 'No strain or box affected',
  },
  speciesCodeChangeNothingApplied: {
    fr: 'Ceci est une prévisualisation. Rien n’a été appliqué.',
    en: 'This is a preview. Nothing has been applied.',
  },
  speciesCodeChangeOpen: {
    fr: 'Prévisualiser un changement de code',
    en: 'Preview a code change',
  },
  speciesCodeChangeOutsideInstitution: {
    fr: 'Le conflit concerne un enregistrement extérieur à cette structure, sans autre détail.',
    en: 'The conflict involves a record outside this institution; no further detail is available.',
  },
  speciesCodeChangeProposed: { fr: 'Proposé', en: 'Proposed' },
  speciesCodeChangeReady: {
    fr: 'Prévisualisation calculée. Rien n’a été modifié.',
    en: 'Preview computed. Nothing has been changed.',
  },
  speciesCodeChangeReasonAssigned: {
    fr: 'Ce code est déjà attribué à une autre espèce de cette structure.',
    en: 'This code is already assigned to another species of this institution.',
  },
  speciesCodeChangeReasonBoxBatchCollision: {
    fr: 'Plusieurs boîtes recevraient le même identifiant local.',
    en: 'Several boxes would receive the same Local ID.',
  },
  speciesCodeChangeReasonBoxCollision: {
    fr: 'L’identifiant local proposé est déjà attribué à une autre boîte.',
    en: 'The proposed Local ID is already assigned to another box.',
  },
  speciesCodeChangeReasonBoxHistoricalCollision: {
    fr: 'L’identifiant local proposé a déjà désigné une autre boîte par le passé.',
    en: 'The proposed Local ID has designated another box in the past.',
  },
  speciesCodeChangeReasonBoxStrainMismatch: {
    fr: 'L’identifiant local de la boîte ne correspond pas à l’identifiant de sa souche.',
    en: 'The box Local ID does not match the identifier of its strain.',
  },
  speciesCodeChangeReasonBoxWithoutCode: {
    fr: 'L’identifiant local ne porte pas le code actuel de l’espèce : il n’est pas modifié.',
    en: 'The Local ID does not carry the current species code: it is not changed.',
  },
  speciesCodeChangeReasonCodeMissing: {
    fr: 'Cette espèce n’a pas de code dans cette structure.',
    en: 'This species has no code in this institution.',
  },
  speciesCodeChangeReasonCurrentOnOtherSpecies: {
    fr: 'Des boîtes d’une autre espèce de cette structure portent le code actuel : elles ne sont pas modifiées.',
    en: 'Boxes of another species of this institution carry the current code: they are not changed.',
  },
  speciesCodeChangeReasonCurrentUnusable: {
    fr: 'Le code actuel de cette espèce n’a pas le format à trois lettres requis.',
    en: 'The current code of this species does not have the required three-letter format.',
  },
  speciesCodeChangeReasonHistoryUnresolved: {
    fr: 'L’historique des identifiants locaux n’est pas établi sans ambiguïté.',
    en: 'The Local ID history is not established without ambiguity.',
  },
  speciesCodeChangeReasonIdentityMismatch: {
    fr: 'L’identité enregistrée de la souche ne correspond pas au code actuel de l’espèce.',
    en: 'The recorded identity of the strain does not match the current species code.',
  },
  speciesCodeChangeReasonMergedRecord: {
    fr: 'Ancienne fiche fusionnée : son identifiant est conservé comme identifiant historique.',
    en: 'Former merged record: its identifier is kept as a historical identifier.',
  },
  speciesCodeChangeReasonMissingGlobalId: {
    fr: 'Boîtes sans identifiant global permanent.',
    en: 'Boxes without a permanent Global ID.',
  },
  speciesCodeChangeReasonNumberingInUse: {
    fr: 'Des boîtes sont déjà numérotées sous l’identifiant de souche proposé.',
    en: 'Boxes are already numbered under the proposed strain identifier.',
  },
  speciesCodeChangeReasonNumberingReserved: {
    fr: 'La numérotation de boîtes de l’identifiant de souche proposé est déjà réservée.',
    en: 'The box numbering of the proposed strain identifier is already reserved.',
  },
  speciesCodeChangeReasonOther: {
    fr: 'Autre situation signalée lors du calcul.',
    en: 'Another condition reported by the calculation.',
  },
  speciesCodeChangeReasonOtherStrains: {
    fr: 'Le code actuel est aussi rattaché à des souches absentes de cette prévisualisation.',
    en: 'The current code is also attached to strains absent from this preview.',
  },
  speciesCodeChangeReasonReviewedIdentity: {
    fr: 'Identifiant historique vérifié et protégé : il ne peut pas être modifié.',
    en: 'Reviewed and protected historical identifier: it cannot be changed.',
  },
  speciesCodeChangeReasonStrainBatchCollision: {
    fr: 'Plusieurs souches recevraient le même identifiant.',
    en: 'Several strains would receive the same identifier.',
  },
  speciesCodeChangeReasonStrainBlocked: {
    fr: 'La souche de cette boîte est bloquée.',
    en: 'The strain of this box is blocked.',
  },
  speciesCodeChangeReasonStrainCollision: {
    fr: 'L’identifiant proposé est déjà celui d’une autre souche.',
    en: 'The proposed identifier already belongs to another strain.',
  },
  speciesCodeChangeReasonStrainForeign: {
    fr: 'Souche appartenant à une autre structure.',
    en: 'Strain owned by another institution.',
  },
  speciesCodeChangeReasonStrainFormat: {
    fr: 'Identifiant de souche hors du format habituel : il n’est pas modifié.',
    en: 'Strain identifier outside the usual format: it is not changed.',
  },
  speciesCodeChangeReasonStrainPrefix: {
    fr: 'L’identifiant de la souche ne commence pas par le code actuel de l’espèce.',
    en: 'The strain identifier does not start with the current species code.',
  },
  speciesCodeChangeReasonStrainShared: {
    fr: 'Souche également utilisée par des boîtes d’une autre structure.',
    en: 'Strain also used by boxes of another institution.',
  },
  speciesCodeChangeReasonStrainUnowned: {
    fr: 'Souche sans structure propriétaire enregistrée.',
    en: 'Strain without a recorded owning institution.',
  },
  speciesCodeChangeReasonUnchanged: {
    fr: 'Le code proposé est identique au code actuel.',
    en: 'The proposed code is the same as the current code.',
  },
  speciesCodeChangeReasonUsedByOtherSpecies: {
    fr: 'Des souches ou des boîtes d’une autre espèce de cette structure portent déjà ce code.',
    en: 'Strains or boxes of another species of this institution already carry this code.',
  },
  speciesCodeChangeReasonUsedOutside: {
    fr: 'Ce code est déjà utilisé pour une autre espèce en dehors de cette structure.',
    en: 'This code is already used for another species outside this institution.',
  },
  speciesCodeChangeRecord: {
    fr: '{count} enregistrement concerné',
    en: '{count} record concerned',
  },
  speciesCodeChangeRecords: {
    fr: '{count} enregistrements concernés',
    en: '{count} records concerned',
  },
  speciesCodeChangeShowAllBoxes: {
    fr: 'Afficher les {count} boîtes',
    en: 'Show all {count} boxes',
  },
  speciesCodeChangeSimulation: {
    fr: 'Simulation uniquement. Aucune modification n’est appliquée aux souches, aux boîtes ni aux étiquettes.',
    en: 'Simulation only. No change is applied to strains, boxes or labels.',
  },
  speciesCodeChangeStrainsConcerned: {
    fr: 'Souches concernées',
    en: 'Strains affected',
  },
  speciesCodeChangeSubmit: {
    fr: 'Prévisualiser les conséquences',
    en: 'Preview the consequences',
  },
  speciesCodeChangeTitle: { fr: 'Prévisualisation', en: 'Preview' },
  speciesCodeChangeWarnings: { fr: 'Avertissements', en: 'Warnings' },
  speciesCodeDataIssue: {
    fr: 'Anomalie des données du code espèce. Une vérification est nécessaire.',
    en: 'Species code data inconsistency. A review is required.',
  },
  speciesCodeExhausted: {
    fr: 'Tous les codes possibles pour ce nom sont déjà utilisés.',
    en: 'Every possible code for this name is already in use.',
  },
  speciesCodeHistoricalConflict: {
    fr: 'Les souches existantes de cette espèce portent des codes incompatibles. Une vérification est nécessaire.',
    en: 'Existing strains of this species carry conflicting codes. A review is needed.',
  },
  speciesCodeNameNotDerivable: {
    fr: 'Aucun code ne peut être déduit de ce nom scientifique.',
    en: 'No code can be derived from this scientific name.',
  },
};
