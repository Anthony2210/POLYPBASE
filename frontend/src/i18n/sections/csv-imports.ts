export const csvImports = {
  csvAbsent: { fr: 'Non fourni', en: 'Not supplied' },
  csvAccepted: {
    fr: 'Import accepté. La source est conservée de façon privée et permanente, y compris si aucun relevé n’a été créé.',
    en: 'Import accepted. The source is retained privately and permanently, including when no measurements were created.',
  },
  csvAcceptedAt: { fr: 'Accepté le', en: 'Accepted at' },
  csvApply: {
    fr: 'Importer et conserver la source',
    en: 'Import and retain source',
  },
  csvApplyUnavailable: {
    fr: 'Import indisponible pour cet aperçu. Corrigez les blocages et fournissez une date exacte pour chaque relevé à créer, puis recalculez l’aperçu.',
    en: 'Import is unavailable for this preview. Resolve blockers and supply an exact date for every measurement to create, then recalculate the preview.',
  },
  csvApplying: {
    fr: 'Import en cours… Quitter ou changer la source n’annule pas une acceptation déjà effectuée par le serveur.',
    en: 'Importing… Leaving or changing the source does not cancel acceptance already completed by the server.',
  },
  csvBlank: { fr: 'Vide', en: 'Blank' },
  csvClose: { fr: 'Fermer l’import CSV', en: 'Close CSV import' },
  csvColumn: { fr: 'Colonne', en: 'Column' },
  csvComma: { fr: 'Virgule (,)', en: 'Comma (,)' },
  csvConfirmImport: {
    fr: 'J’ai vérifié l’aperçu. Je confirme l’import dans l’institution active et la conservation permanente de la source.',
    en: 'I have reviewed the preview. I confirm the import into the active institution and permanent retention of the source.',
  },
  csvConfirmMapping: {
    fr: 'Je confirme ces correspondances pour calculer l’aperçu. Ceci n’autorise aucun import.',
    en: 'I confirm these mappings to calculate the preview. This does not authorize any import.',
  },
  csvCreated: { fr: 'Relevés créés', en: 'Measurements created' },
  csvDelimiter: { fr: 'Séparateur', en: 'Delimiter' },
  csvDiagnosticBlocked: {
    fr: 'Le diagnostic signale des blocages. Corrigez la source ou les correspondances, puis recalculez l’aperçu.',
    en: 'Diagnostics found blockers. Correct the source or mappings, then recalculate the preview.',
  },
  csvDiagnosticReady: {
    fr: 'Aucun blocage détecté. Aucun relevé n’est importé à ce stade : une confirmation séparée est requise.',
    en: 'No blockers detected. No measurements imported at this stage: separate confirmation is required.',
  },
  csvDownloadReceipt: { fr: 'Télécharger le reçu', en: 'Download receipt' },
  csvDownloadSource: {
    fr: 'Télécharger la source originale',
    en: 'Download original source',
  },
  csvDownloading: { fr: 'Téléchargement en cours…', en: 'Downloading…' },
  csvErrorApply: {
    fr: 'L’acceptation n’a pas pu être confirmée. Vérifiez la connexion puis confirmez à nouveau avec le même fichier et les mêmes correspondances pour récupérer le reçu si l’import a déjà été accepté.',
    en: 'Acceptance could not be confirmed. Check your connection, then confirm again with the same file and mappings to recover the receipt if the import was already accepted.',
  },
  csvErrorConfiguration: {
    fr: 'Requête non valide. Sélectionnez à nouveau le fichier et son séparateur, puis inspectez les colonnes.',
    en: 'Invalid request. Select the file and its delimiter again, then inspect the columns.',
  },
  csvErrorDownload: {
    fr: 'Téléchargement indisponible. Vérifiez la connexion et votre institution active, puis réessayez.',
    en: 'Download unavailable. Check your connection and active institution, then retry.',
  },
  csvErrorMapping: {
    fr: 'Correspondances non valides ou non confirmées. Inspectez à nouveau les colonnes, choisissez chaque correspondance et confirmez-les.',
    en: 'Invalid or unconfirmed mappings. Inspect the columns again, choose every mapping and confirm them.',
  },
  csvErrorPermission: {
    fr: 'Accès refusé. Vérifiez la session et sélectionnez une institution où vous êtes administrateur ou technicien de laboratoire actif.',
    en: 'Access denied. Check your session and select an institution where you are an active administrator or laboratory technician.',
  },
  csvErrorRequest: {
    fr: 'Aperçu indisponible. Vérifiez la connexion puis réessayez. Si le problème persiste, contactez un administrateur.',
    en: 'Preview unavailable. Check your connection and retry. If the problem persists, contact an administrator.',
  },
  csvErrorSize: {
    fr: 'Fichier ou requête trop volumineux. Choisissez un CSV de 5 Mio maximum avec des en-têtes plus courts si nécessaire.',
    en: 'File or request too large. Choose a CSV no larger than 5 MiB, with shorter headers if necessary.',
  },
  csvErrorStale: {
    fr: 'L’aperçu ne correspond plus à la source ou aux données actuelles. Recalculez l’aperçu et vérifiez-le avant de confirmer à nouveau.',
    en: 'The preview no longer matches the source or current data. Recalculate and review the preview before confirming again.',
  },
  csvExactDate: { fr: 'Date exacte', en: 'Exact date' },
  csvFalse: { fr: 'Faux', en: 'False' },
  csvFieldAttention: { fr: 'Attention requise', en: 'Needs attention' },
  csvFieldBox: { fr: 'Référence de boîte', en: 'Box reference' },
  csvFieldDate: { fr: 'Date du relevé', en: 'Measurement date' },
  csvFieldEphyrae: { fr: 'Éphyrules', en: 'Ephyrae' },
  csvFieldNotes: { fr: 'Notes', en: 'Notes' },
  csvFieldObserver: { fr: 'Référence observateur', en: 'Observer reference' },
  csvFieldPolyps: { fr: 'Polypes', en: 'Polyps' },
  csvFieldSalinity: { fr: 'Salinité (PSU)', en: 'Salinity (PSU)' },
  csvFieldStatus: { fr: 'État de culture', en: 'Culture status' },
  csvFieldStrobila: { fr: 'Strobiles', en: 'Strobilae' },
  csvFieldWeek: { fr: 'Semaine ISO', en: 'ISO week' },
  csvFieldYear: { fr: 'Année ISO', en: 'ISO year' },
  csvFile: { fr: 'Fichier CSV', en: 'CSV file' },
  csvIgnore: { fr: 'Ignorer volontairement', en: 'Deliberately ignore' },
  csvImportConfirmation: { fr: 'Confirmer l’import', en: 'Confirm import' },
  csvInspect: { fr: 'Inspecter les colonnes', en: 'Inspect columns' },
  csvInspecting: { fr: 'Inspection des colonnes…', en: 'Inspecting columns…' },
  csvIssueBoolean: {
    fr: 'Valeur attendue : true ou false, en minuscules.',
    en: 'Expected value: true or false, in lowercase.',
  },
  csvIssueChoice: {
    fr: 'État attendu : good, medium, bad, dead ou not_specified.',
    en: 'Expected status: good, medium, bad, dead or not_specified.',
  },
  csvIssueChronology: {
    fr: 'Un repiquage dans cette semaine empêche de certifier la chronologie. Fournissez la vraie date du relevé ou abandonnez la ligne.',
    en: 'A subculture in this week prevents chronology certification. Supply the actual measurement date or discard the row.',
  },
  csvIssueColumns: {
    fr: 'Trop de colonnes. Réduisez le nombre de colonnes source.',
    en: 'Too many columns. Reduce the number of source columns.',
  },
  csvIssueContradictory: {
    fr: 'Des lignes source se contredisent pour la même boîte et semaine. Résolvez-les dans la source.',
    en: 'Source rows contradict one another for the same box and week. Resolve them in the source.',
  },
  csvIssueCountRange: {
    fr: 'Compte hors limites. Vérifiez la valeur source.',
    en: 'Count out of range. Check the source value.',
  },
  csvIssueDate: {
    fr: 'Date exacte attendue au format AAAA-MM-JJ.',
    en: 'An exact date in YYYY-MM-DD format is required.',
  },
  csvIssueDateConflict: {
    fr: 'La date diffère du relevé existant. Vérifiez la source et l’historique.',
    en: 'The date differs from the existing measurement. Check the source and history.',
  },
  csvIssueDateWeek: {
    fr: 'La date et la semaine ISO ne correspondent pas. Corrigez la source.',
    en: 'The date and ISO week do not match. Correct the source.',
  },
  csvIssueDecimal: {
    fr: 'Utilisez un nombre décimal avec un point et au plus deux décimales.',
    en: 'Use a decimal number with a dot and at most two decimal places.',
  },
  csvIssueDecimalRange: {
    fr: 'Valeur décimale hors limites (−999.99 à 999.99).',
    en: 'Decimal value out of range (−999.99 to 999.99).',
  },
  csvIssueDuplicate: {
    fr: 'Plusieurs lignes visent la même boîte et semaine. Résolvez les doublons source.',
    en: 'Multiple rows target the same box and week. Resolve source duplicates.',
  },
  csvIssueFieldLimit: {
    fr: 'Champ trop long. Réduisez sa longueur dans la source.',
    en: 'Field too long. Shorten it in the source.',
  },
  csvIssueFormula: {
    fr: 'Formule numérique interdite. Fournissez une valeur littérale.',
    en: 'Numeric formula forbidden. Supply a literal value.',
  },
  csvIssueHeaderMissing: {
    fr: 'En-tête absent. Ajoutez une ligne de noms de colonnes.',
    en: 'Missing header. Add a row of column names.',
  },
  csvIssueHeaders: {
    fr: 'En-têtes vides, doublonnés ou ambigus. Donnez un nom distinct à chaque colonne.',
    en: 'Empty, duplicate or ambiguous headers. Give each column a distinct name.',
  },
  csvIssueInactive: {
    fr: 'Boîte inactive. Vérifiez son état avant toute future saisie.',
    en: 'Inactive box. Check its status before any future entry.',
  },
  csvIssueIncomplete: {
    fr: 'Préparation incomplète. Corrigez les problèmes du fichier puis recommencez.',
    en: 'Incomplete preparation. Correct the file issues and try again.',
  },
  csvIssueInteger: {
    fr: 'Un entier positif ou nul est requis, sans signe ni formule.',
    en: 'A non-negative integer is required, without a sign or formula.',
  },
  csvIssueMalformed: {
    fr: 'CSV mal formé. Vérifiez les guillemets et le séparateur.',
    en: 'Malformed CSV. Check quoting and the delimiter.',
  },
  csvIssueMappingDuplicate: {
    fr: 'Plusieurs colonnes ciblent le même champ. Choisissez une seule colonne par champ.',
    en: 'Multiple columns target the same field. Choose only one column per field.',
  },
  csvIssueMappingIncomplete: {
    fr: 'Chaque en-tête doit être associé ou volontairement ignoré.',
    en: 'Every header must be mapped or deliberately ignored.',
  },
  csvIssueMappingRequired: {
    fr: 'Associez les références de boîte et les comptes de polypes et d’éphyrules.',
    en: 'Map box references and polyp and ephyrae counts.',
  },
  csvIssueMappingTime: {
    fr: 'Associez la date exacte ou les deux champs année et semaine ISO.',
    en: 'Map an exact date or both ISO year and week fields.',
  },
  csvIssueNul: {
    fr: 'Le fichier contient un caractère nul interdit. Réexportez-le en CSV texte.',
    en: 'The file contains a forbidden null character. Export it again as text CSV.',
  },
  csvIssueObserver: {
    fr: 'La résolution de la référence observateur n’est pas prise en charge.',
    en: 'Observer reference resolution is not supported.',
  },
  csvIssueRequired: {
    fr: 'Valeur requise absente. Renseignez ce champ dans la source.',
    en: 'Required value missing. Fill this field in the source.',
  },
  csvIssueRows: {
    fr: 'Trop de lignes. Réduisez le nombre de lignes source.',
    en: 'Too many rows. Reduce the number of source rows.',
  },
  csvIssueSourceString: {
    fr: 'Valeur source non textuelle. Réexportez le fichier en CSV.',
    en: 'Non-text source value. Export the file again as CSV.',
  },
  csvIssueTime: {
    fr: 'Fournissez une date exacte ou une année et une semaine ISO valides.',
    en: 'Supply an exact date or a valid ISO year and week.',
  },
  csvIssueUnknown: {
    fr: 'Problème non reconnu. Vérifiez la source ou transmettez le code technique à un administrateur.',
    en: 'Unrecognized issue. Check the source or send the technical code to an administrator.',
  },
  csvIssueUnknownBox: {
    fr: 'Référence de boîte introuvable dans l’institution active. Vérifiez le code exact.',
    en: 'Box reference not found in the active institution. Check the exact code.',
  },
  csvIssueUnknownField: {
    fr: 'Champ canonique inconnu. Inspectez à nouveau les colonnes.',
    en: 'Unknown canonical field. Inspect the columns again.',
  },
  csvIssueUtf8: {
    fr: 'Enregistrez le fichier en UTF-8 puis recommencez.',
    en: 'Save the file as UTF-8 and try again.',
  },
  csvIssueValueConflict: {
    fr: 'Une valeur fournie diffère du relevé existant. Aucune réécriture ne sera effectuée.',
    en: 'A supplied value differs from the existing measurement. No rewrite will occur.',
  },
  csvIssueVersion: {
    fr: 'Version de préparation non prise en charge. Contactez un administrateur.',
    en: 'Unsupported preparation version. Contact an administrator.',
  },
  csvIssueWeek: {
    fr: 'Année ou semaine ISO non valide. Vérifiez les deux valeurs.',
    en: 'Invalid ISO year or week. Check both values.',
  },
  csvIssueWeekPersistence: {
    fr: 'La persistance d’un relevé à la semaine seule n’est pas disponible.',
    en: 'Persisting week-only measurements is not available.',
  },
  csvIssueWidth: {
    fr: 'Nombre de cellules différent de l’en-tête. Vérifiez cette ligne et le séparateur.',
    en: 'Cell count differs from the header. Check this row and the delimiter.',
  },
  csvLines: { fr: 'Lignes source', en: 'Source lines' },
  csvMapping: { fr: 'Correspondance des colonnes', en: 'Column mapping' },
  csvMappingHelp: {
    fr: 'Choisissez un champ ou ignorez volontairement chaque colonne. Aucune correspondance n’est devinée. Champs requis : boîte, polypes, éphyrules et date exacte ou année + semaine ISO. Les notes et la référence observateur ne sont pas affichées dans l’aperçu.',
    en: 'Choose a field or deliberately ignore every column. No mapping is guessed. Required fields: box, polyps, ephyrae, and an exact date or ISO year + week. Notes and observer references are not displayed in the preview.',
  },
  csvNext: { fr: 'Suivant', en: 'Next' },
  csvNoRows: {
    fr: 'Aucune ligne diagnostiquée. Consultez les problèmes du fichier ou des correspondances ci-dessus.',
    en: 'No diagnosed rows. Review the file or mapping issues above.',
  },
  csvNull: { fr: 'NULL (sans valeur)', en: 'NULL (no value)' },
  csvOpen: { fr: 'Importer un CSV de relevés', en: 'Import a measurement CSV' },
  csvOutcomeBlocked: { fr: 'Bloquée', en: 'Blocked' },
  csvOutcomeConflict: { fr: 'Conflit', en: 'Conflict' },
  csvOutcomeCreate: { fr: 'Candidate à la création', en: 'Creation candidate' },
  csvOutcomeIgnored: {
    fr: 'Ignorée (sans valeurs scientifiques)',
    en: 'Ignored (no scientific values)',
  },
  csvOutcomeInvalid: { fr: 'Ligne invalide', en: 'Invalid row' },
  csvOutcomeMatches: {
    fr: 'Déjà identique, aucune réécriture',
    en: 'Already matching, no rewrite',
  },
  csvOutcomeUnknown: { fr: 'Boîte inconnue', en: 'Unknown box' },
  csvOutcomeUnknownCode: {
    fr: 'Résultat non reconnu',
    en: 'Unrecognized outcome',
  },
  csvPage: { fr: 'Page', en: 'Page' },
  csvPagination: {
    fr: 'Pagination des lignes diagnostiquées',
    en: 'Diagnosed row pagination',
  },
  csvPrecision: { fr: 'Précision', en: 'Precision' },
  csvPreview: { fr: 'Calculer l’aperçu', en: 'Calculate preview' },
  csvPreviewHelp: {
    fr: 'Vérifiez les correspondances et l’aperçu avant de confirmer séparément l’import. L’aperçu ne crée aucun relevé.',
    en: 'Review the mappings and preview before separately confirming the import. The preview creates no measurements.',
  },
  csvPreviewTitle: { fr: 'Aperçu avant import', en: 'Preview before import' },
  csvPreviewing: {
    fr: 'Calcul du diagnostic…',
    en: 'Calculating diagnostics…',
  },
  csvPrevious: { fr: 'Précédent', en: 'Previous' },
  csvReceiptHelp: {
    fr: 'Téléchargez le reçu pour retrouver les identifiants des relevés et les références de la source. Les téléchargements restent soumis aux autorisations de l’institution.',
    en: 'Download the receipt for measurement IDs and source references. Downloads remain subject to institution permissions.',
  },
  csvReceiptId: { fr: 'Identifiant du reçu', en: 'Receipt ID' },
  csvReceiptTitle: { fr: 'Reçu d’import', en: 'Import receipt' },
  csvRecord: { fr: 'Enregistrement', en: 'Record' },
  csvReplayed: {
    fr: 'Cet import a déjà été accepté. Le reçu initial est retourné, sans nouvelle création ni réécriture.',
    en: 'This import was already accepted. The original receipt is returned without creating or rewriting measurements.',
  },
  csvRetentionHelp: {
    fr: 'À l’acceptation, le fichier source original est conservé de façon privée et permanente dans l’institution active, même si aucun relevé n’est créé. Les relevés déjà identiques ne sont pas réécrits.',
    en: 'On acceptance, the original source file is retained privately and permanently in the active institution, even if no measurements are created. Already matching measurements are not rewritten.',
  },
  csvSemicolon: { fr: 'Point-virgule (;)', en: 'Semicolon (;)' },
  csvSourceHash: { fr: 'SHA-256 de la source', en: 'Source SHA-256' },
  csvSourceHelp: {
    fr: 'Fichier UTF-8, 5 Mio maximum. L’inspection et l’aperçu ne conservent pas la source. Seuls les relevés avec une date exacte peuvent être créés.',
    en: 'UTF-8 file, maximum 5 MiB. Inspection and preview do not retain the source. Only measurements with an exact date can be created.',
  },
  csvSourceIncomplete: {
    fr: 'Source incomplètement analysée. Ces résultats sont partiels : corrigez les problèmes signalés puis recommencez.',
    en: 'Source analysis is incomplete. These results are partial: correct the reported issues and try again.',
  },
  csvSummaryBlocked: { fr: 'Lignes avec blocage', en: 'Rows with blockers' },
  csvSummaryConflicts: { fr: 'Conflits', en: 'Conflicts' },
  csvSummaryCreate: {
    fr: 'Candidats à la création',
    en: 'Creation candidates',
  },
  csvSummaryIgnored: { fr: 'Lignes ignorées', en: 'Ignored rows' },
  csvSummaryInvalid: { fr: 'Invalides ou bloquées', en: 'Invalid or blocked' },
  csvSummaryMatches: { fr: 'Déjà identiques', en: 'Already matching' },
  csvSummaryTotal: { fr: 'Lignes source', en: 'Source rows' },
  csvSummaryUnknown: { fr: 'Boîtes inconnues', en: 'Unknown boxes' },
  csvTab: { fr: 'Tabulation', en: 'Tab' },
  csvTitle: { fr: 'Import CSV de relevés', en: 'Measurement CSV import' },
  csvTrue: { fr: 'Vrai', en: 'True' },
  csvUnselected: { fr: 'Choisir une correspondance…', en: 'Choose a mapping…' },
  csvValuesHelp: {
    fr: 'Valeurs canoniques : 0 est une mesure réelle. Non fourni, vide et NULL restent distincts. Une semaine seule n’est pas une date d’observation exacte.',
    en: 'Canonical values: 0 is a real measurement. Not supplied, blank and NULL remain distinct. A week alone is not an exact observation date.',
  },
  csvWeekOnly: { fr: 'Semaine ISO uniquement', en: 'ISO week only' },
};
