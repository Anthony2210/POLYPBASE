// Explicit retention decisions for the 6455727 integration, not source-use evidence.
// No wildcard exemptions: the usage checker still rejects stale or unknown keys.
export const retainedSpeciesCopy = Object.freeze({
  ...Object.fromEntries([
    'quickStrainBackToStrain', 'quickStrainCreateAndUse', 'quickStrainNewSpecies', 'quickStrainTitle',
  ].map(key => [key, 'Species integration (2026-11-17): the legacy QuickStrainCreator was retired in favor of the institutional Species workflow. Preserve existing catalog and Japanese provenance pending an explicit removal batch.'])),
  ...Object.fromEntries([
    'speciesCodeChangeBoxPreviewHint', 'speciesCodeChangeBreakdown', 'speciesCodeChangeCodeLabel',
    'speciesCodeChangeConsequence', 'speciesCodeChangeDependencyActions', 'speciesCodeChangeDependencyLabels',
    'speciesCodeChangeDependencyLineage', 'speciesCodeChangeDependencyMergedRecords',
    'speciesCodeChangeDependencyPreviousIds', 'speciesCodeChangeDependencyQr', 'speciesCodeChangeDependencyTransfers',
    'speciesCodeChangeDependencyTransplants', 'speciesCodeChangeFeasible', 'speciesCodeChangeHint',
    'speciesCodeChangeHistoryNote', 'speciesCodeChangeHistoryTitle', 'speciesCodeChangeIdentifiersToChange',
    'speciesCodeChangeImpossibleNote', 'speciesCodeChangeInvariant', 'speciesCodeChangeLabelsNote',
    'speciesCodeChangeLabelsToReview', 'speciesCodeChangeMissingGlobalId', 'speciesCodeChangeNoImpact',
    'speciesCodeChangeNothingApplied', 'speciesCodeChangeOpen', 'speciesCodeChangeReady', 'speciesCodeChangeSimulation',
  ].map(key => [key, 'Species integration (2026-11-17): retain the original code-change preview copy. The Species sheet hid the preview entry before PR2 removed the standalone preview. Preserve existing catalog and Japanese provenance pending an explicit removal batch.'])),
  ...Object.fromEntries([
    'speciesCatalogHistorical', 'speciesDetailActionsUnavailable', 'speciesDetailCode', 'speciesDetailHistoricalLabel',
    'speciesDetailHistoricalNote', 'speciesDetailNoCode', 'speciesDetailNoWorms', 'speciesDetailUpdated',
  ].map(key => [key, 'Species integration (2026-11-17): retain the original institutional detail and historical-reference copy. The final single-Species presentation omits storage categories, redundant headings and success notices; historical routing and server prerequisite handling remain. No implicit copy-deletion decision.'])),
  ...Object.fromEntries([
    'speciesStrainAddBox', 'speciesStrainBoxColumn', 'speciesStrainCodePrefix', 'speciesStrainCreated',
    'speciesStrainLocationChoose', 'speciesStrainLocationCode', 'speciesStrainLocationCodeInvalid',
    'speciesStrainLocationCodeTaken', 'speciesStrainLocationExisting', 'speciesStrainLocationHint',
    'speciesStrainLocationName', 'speciesStrainLocationNameRequired', 'speciesStrainLocationNameTaken',
    'speciesStrainLocationNameTooLong', 'speciesStrainLocationNew', 'speciesStrainNoLocations',
    'speciesStrainNoMeasurement', 'speciesStrainNumberAutomatic', 'speciesStrainOriginDonation',
    'speciesStrainOriginFieldCollection', 'speciesStrainOriginType', 'speciesStrainUseLocation',
  ].map(key => [key, 'Species integration (2026-11-17): preserve source strain/location labels omitted by the final country-based dialog and compact strain/box presentation. Country linking, recorded acquisition metadata and server allocation remain implemented; retained wording is not evidence of an available control.'])),
  ...Object.fromEntries([
    'speciesCreateAboveSpeciesRank', 'speciesSearchOtherRanks',
  ].map(key => [key, 'Species integration (2026-11-17): retain the original WoRMS rank explanations while the final search filters unusable ranks instead of displaying them. Do not drop the source copy or weaken species-level reference validation.'])),
  createBoxInitialPolypsHint: 'Species integration (2026-11-17): retain the source initial-record explanation, omitted from the compact form. Initial counts remain required, include real zero and preserve explicit server provenance; no new visible hint is introduced.',
});
