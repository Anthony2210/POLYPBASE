export const movements = {
  confirmMoveAction: { fr: 'Déplacer', en: 'Move', ja: '移動' },
  confirmMoveMessage: {
    fr: 'Le nouvel emplacement sera ajouté à l’historique de la boîte.',
    en: 'The new location will be added to the box history.',
    ja: '新しい設置場所は容器の履歴に記録されます。',
  },
  confirmMoveTitle: {
    fr: 'Déplacer cette boîte',
    en: 'Move this box',
    ja: 'この容器を移動',
  },
  latestMovement: {
    fr: 'Dernier déplacement',
    en: 'Last movement',
    ja: '最新の移動',
  },
  locationPeriodChange: {
    fr: 'Changement de période',
    en: 'Period change',
    ja: '期間の切り替え',
  },
  locationPeriodChangeNote: {
    fr: 'Limite de période d’emplacement déduite de l’historique. Aucun déplacement enregistré à cet instant.',
    en: 'Location period boundary derived from history. No move recorded at this instant.',
    ja: '履歴から推定された設置期間の切り替わりです。この時点の移動記録はありません。',
  },
  moveAction: { fr: 'Déplacer', en: 'Move', ja: '移動' },
  moveDialogCurrent: { fr: 'actuel', en: 'current', ja: '現在' },
  moveDialogCurrentZone: {
    fr: 'Emplacement actuel',
    en: 'Current location',
    ja: '現在の設置場所',
  },
  moveDialogNewZone: {
    fr: 'Nouvel emplacement',
    en: 'New location',
    ja: '移動先の設置場所',
  },
  moveDialogNoHistory: {
    fr: 'Aucun historique d’emplacement pour cette boîte.',
    en: 'No location history for this box.',
    ja: 'この容器の設置場所の履歴はありません。',
  },
  moveDialogNote: { fr: 'Note', en: 'Note', ja: 'メモ' },
  moveDialogNotePlaceholder: {
    fr: 'Ex. changement de température, rangement, manipulation',
    en: 'For example, temperature change, storage, handling',
    ja: '例：水温の変更、配置換え、作業上の都合',
  },
  moveDialogTitle: {
    fr: 'Déplacer la boîte',
    en: 'Move box',
    ja: '容器を移動',
  },
  moveDialogUnknownEnd: {
    fr: 'date de fin inconnue',
    en: 'end date unknown',
    ja: '終了日不明',
  },
  moveForbidden: {
    fr: 'Ce compte ne peut pas déplacer de boîte. Merci de contacter un administrateur.',
    en: 'This account cannot move boxes. Please contact your administrator.',
    ja: 'このアカウントでは容器を移動できません。管理者に連絡してください。',
  },
  moveHistoryArrival: { fr: 'Arrivée', en: 'Arrival', ja: '到着' },
  moveHistoryDeparture: { fr: 'Départ', en: 'Departure', ja: '出発' },
  moveLocationChanged: {
    fr: 'L’emplacement de cette boîte a changé. Les informations ont été actualisées : vérifiez-les avant de relancer le déplacement.',
    en: 'This box location has changed. Its information has been refreshed: review it before moving the box again.',
    ja: 'この容器の設置場所が変更されました。情報が更新されたので、再度移動する前に内容を確認してください。',
  },
  movedTo: { fr: 'Déplacée vers', en: 'Moved to', ja: '移動先' },
  movementAtPeriodBoundaryNote: {
    fr: 'Coïncide avec une limite de période d’emplacement.',
    en: 'Coincides with a location period boundary.',
    ja: '設置期間の切り替わりと一致しています。',
  },
  movementCorrect: {
    fr: 'Corriger ce mouvement',
    en: 'Correct this movement',
    ja: 'この移動記録を修正',
  },
  movementDate: {
    fr: 'Date et heure (Paris)',
    en: 'Date and time (Paris)',
    ja: '日時（パリ時間）',
  },
  movementDestination: { fr: 'Destination', en: 'Destination', ja: '移動先' },
  movementErrorChanged: {
    fr: 'Ce mouvement a été modifié. La fiche va être actualisée : vérifiez le résumé puis rouvrez la correction.',
    en: 'This movement has changed. The box will be refreshed: review the summary, then reopen the correction.',
    ja: 'この移動記録は変更されました。容器情報を更新するので、概要を確認してから修正画面を開き直してください。',
  },
  movementErrorChronology: {
    fr: 'Cette date n’est pas compatible avec la chronologie des mouvements et des emplacements.',
    en: 'This date is incompatible with the chronology of movements and locations.',
    ja: 'この日付は、移動や設置場所の履歴と前後関係が合いません。',
  },
  movementErrorConflict: {
    fr: 'La correction est incompatible avec l’état actuel de la boîte. Actualisez la fiche.',
    en: 'The correction conflicts with the current box state. Refresh the box details.',
    ja: '修正内容が容器の現在の状態と矛盾しています。容器情報を更新してください。',
  },
  movementErrorDateReadOnly: {
    fr: 'La date de ce mouvement est liée au cycle de vie et ne peut pas être corrigée.',
    en: 'The date of this movement is tied to its lifecycle and cannot be corrected.',
    ja: 'この移動日は容器の履歴と連動しているため、変更できません。',
  },
  movementErrorForbidden: {
    fr: 'Ce compte n’est pas autorisé à corriger ce mouvement.',
    en: 'This account is not allowed to correct this movement.',
  },
  movementErrorFuture: {
    fr: 'Un mouvement enregistré ne peut pas être daté dans le futur.',
    en: 'A recorded movement cannot be dated in the future.',
    ja: '移動記録に未来の日付は指定できません。',
  },
  movementErrorMissingLink: {
    fr: 'Une période liée au mouvement manque. La date et la destination ne peuvent pas être corrigées.',
    en: 'A period linked to this movement is missing. Its date and destination cannot be corrected.',
    ja: 'この移動に関連する期間データが欠けているため、日付と移動先を修正できません。',
  },
  movementErrorNoChange: {
    fr: 'La correction ne change aucune information.',
    en: 'The correction does not change any information.',
    ja: '修正内容に変更がありません。',
  },
  movementErrorNotFound: {
    fr: 'Ce mouvement ou cette boîte n’est plus accessible.',
    en: 'This movement or box is no longer accessible.',
    ja: 'この移動記録または容器にはアクセスできなくなっています。',
  },
  movementErrorNotTerminal: {
    fr: 'La destination ne peut plus être corrigée : la boîte doit être active et ce mouvement doit correspondre à son emplacement actuel.',
    en: 'The destination can no longer be corrected: the box must be active and this movement must match its current location.',
    ja: '移動先は変更できません。変更するには、容器が有効で、この移動先が現在の設置場所と一致している必要があります。',
  },
  movementErrorOutsideWeek: {
    fr: 'La date doit rester dans la semaine du mouvement enregistré (du lundi au dimanche, Paris).',
    en: 'The date must stay within the week of the recorded movement (Monday to Sunday, Paris).',
    ja: '日付は記録された移動と同じ週（月曜～日曜、パリ時間）に収めてください。',
  },
  movementErrorOverlap: {
    fr: 'Une autre période d’emplacement chevauche les périodes concernées.',
    en: 'Another location period overlaps the affected periods.',
    ja: '対象期間が、別の設置期間と重なっています。',
  },
  movementErrorPeriodMismatch: {
    fr: 'Les périodes liées ne correspondent plus au mouvement. Vérifiez l’historique.',
    en: 'The linked periods no longer match this movement. Review the history.',
    ja: '移動記録と、紐づく設置期間が一致していません。履歴を確認してください。',
  },
  movementErrorProvenance: {
    fr: 'La provenance de ce mouvement n’est pas établie. Seul son commentaire peut être corrigé.',
    en: 'The provenance of this movement is not established. Only its comment can be corrected.',
  },
  movementErrorRefresh: {
    fr: 'Impossible d’actualiser la fiche. Réessayez avant toute nouvelle correction.',
    en: 'Unable to refresh the box details. Try again before making another correction.',
    ja: '容器情報を更新できませんでした。もう一度試してから修正してください。',
  },
  movementErrorSameZone: {
    fr: 'La destination doit être différente de la zone d’origine.',
    en: 'The destination must differ from the origin zone.',
    ja: '移動先は移動元と異なる設置場所を指定してください。',
  },
  movementErrorSave: {
    fr: 'Impossible d’enregistrer la correction. Vérifiez votre connexion et réessayez.',
    en: 'Unable to save the correction. Check your connection and try again.',
    ja: '修正を保存できません。接続を確認して、もう一度お試しください。',
  },
  movementErrorValidation: {
    fr: 'La correction est invalide. Vérifiez la date, la destination et le commentaire.',
    en: 'The correction is invalid. Check the date, destination and comment.',
    ja: '修正内容が無効です。日付、移動先、コメントを確認してください。',
  },
  movementErrorWeekConflict: {
    fr: 'Un autre déplacement ordinaire est déjà enregistré dans la semaine choisie (Europe/Paris).',
    en: 'Another ordinary movement is already recorded in the selected week (Europe/Paris).',
    ja: '選択した週（パリ時間）には、すでに別の通常移動が記録されています。',
  },
  movementEvent: { fr: 'Déplacement', en: 'Move', ja: '移動' },
  movementHistoryTitle: {
    fr: 'Historique des emplacements',
    en: 'Location history',
    ja: '設置場所の履歴',
  },
  movementKindInitialLocation: {
    fr: 'Premier emplacement (hors déplacement ordinaire)',
    en: 'Initial location (not an ordinary movement)',
    ja: '最初の設置場所（通常の移動ではありません）',
  },
  movementKindOrdinary: { fr: 'Déplacement', en: 'Movement', ja: '通常の移動' },
  movementKindQualification: {
    fr: 'Qualification (hors déplacement ordinaire)',
    en: 'Qualification (not an ordinary movement)',
    ja: '状態設定（通常の移動ではありません）',
  },
  movementKindReactivation: {
    fr: 'Réactivation (hors déplacement ordinaire)',
    en: 'Reactivation (not an ordinary movement)',
    ja: '再有効化（通常の移動ではありません）',
  },
  movementKindUnclassified: {
    fr: 'Provenance non établie',
    en: 'Provenance not established',
  },
  movementNoNotes: {
    fr: 'Aucun commentaire.',
    en: 'No comment.',
    ja: 'コメントはありません。',
  },
  movementNoOrigin: { fr: 'Non renseignée', en: 'Not recorded', ja: '未記録' },
  movementNotes: { fr: 'Commentaire', en: 'Comment', ja: 'コメント' },
  movementOrigin: { fr: 'Origine', en: 'Origin', ja: '移動元' },
  movementRefresh: {
    fr: 'Actualiser le résumé',
    en: 'Refresh summary',
    ja: '概要を更新',
  },
  movementReview: {
    fr: 'Revenir au résumé pour vérifier',
    en: 'Return to the summary to review',
    ja: '概要に戻って確認',
  },
  movementReviewReopen: {
    fr: 'Les informations ont changé. Fermez cet éditeur, vérifiez le résumé actualisé puis rouvrez la correction.',
    en: 'The information has changed. Close this editor, review the updated summary, then reopen the correction.',
    ja: '情報が変更されました。この画面を閉じて更新後の概要を確認し、修正画面を開き直してください。',
  },
  movementSave: {
    fr: 'Enregistrer la correction',
    en: 'Save correction',
    ja: '修正内容を保存',
  },
  movementSaved: {
    fr: 'Correction enregistrée.',
    en: 'Correction saved.',
    ja: '修正内容を保存しました',
  },
  movementTimeAmbiguous: {
    fr: 'Cette heure correspond à deux instants à Paris lors du passage à l’heure d’hiver. Choisissez une heure non ambiguë.',
    en: 'This time corresponds to two instants in Paris when daylight saving time ends. Choose an unambiguous time.',
    ja: '冬時間への切り替えにより、パリではこの時刻が2回あります。別の時刻を選んでください。',
  },
  movementTimeGap: {
    fr: 'Cette heure n’existe pas à Paris lors du passage à l’heure d’été. Choisissez une autre heure.',
    en: 'This time does not exist in Paris when daylight saving time starts. Choose another time.',
    ja: '夏時間への切り替え時、この時刻はパリでは存在しません。別の時刻を選択してください。',
  },
  movementTimeInvalid: {
    fr: 'Saisissez une date et une heure valides.',
    en: 'Enter a valid date and time.',
    ja: '有効な日付と時刻を入力してください。',
  },
  movementTimeOutsideWeek: {
    fr: 'Choisissez une heure dans la même semaine (du lundi au dimanche, Paris) que le mouvement enregistré.',
    en: 'Choose a time within the same week (Monday to Sunday, Paris) as the recorded movement.',
    ja: '記録された移動と同じ週（月曜～日曜、パリ時間）内の日時を選択してください。',
  },
  noMovementHistory: {
    fr: 'Aucun déplacement enregistré pour cette boîte.',
    en: 'No movement recorded for this box.',
    ja: 'この容器の移動記録はありません。',
  },
};
