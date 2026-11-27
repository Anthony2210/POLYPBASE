export const probes = {
  probeAggregateAverage: {
    fr: 'Moyenne de l’agrégat',
    en: 'Aggregate average',
    ja: '集計対象の測定値の平均',
  },
  probeAggregateCoverage: {
    fr: 'Intervalles stockés et instants mesurés',
    en: 'Stored intervals and measured instants',
    ja: '保存された時間区間と測定時点',
  },
  probeAverage: { fr: 'Moyenne des sondes', en: 'Probe average' },
  probeAverageContributors: {
    fr: '{count} sondes contributrices',
    en: '{count} contributing probes',
  },
  probeChartKeyboardDescription: {
    fr: 'Flèches gauche et droite : parcourir les mesures. Plus et moins : zoomer. Échap : fermer. Glisser : zoomer sur la sélection. Double-clic ou double-tap : rétablir la période.',
    en: 'Left and right arrows: browse readings. Plus and minus: zoom. Escape: close. Drag: zoom to the selection. Double-click or double-tap: reset the period.',
    ja: '左右の矢印キー：測定値を順に確認。プラス・マイナスキー：拡大・縮小。Escキー：閉じる。ドラッグ：選択範囲を拡大。ダブルクリックまたはダブルタップ：表示期間を元に戻す。',
  },
  probeChartSummary: {
    fr: 'Températures entre {min} et {max} sur la période.',
    en: 'Temperatures between {min} and {max} over the period.',
    ja: 'この期間の水温は{min}から{max}の範囲です。',
  },
  probeCombinedChartKeyboard: {
    fr: 'Flèches gauche et droite : parcourir les mesures. Plus et moins : zoomer. Glisser : sélectionner une période. Double-clic : revenir aux dernières 24 h.',
    en: 'Left and right arrows: browse measurements. Plus and minus: zoom. Drag: select a period. Double-click: return to the last 24 h.',
  },
  probeCondensedNotice: {
    fr: 'Moyennes par tranche de {minutes} min, avec minimum et maximum.',
    en: 'Means per {minutes}-minute interval, with minimum and maximum.',
  },
  probeCoveragePreview: {
    fr: 'Aperçu partiel : les statistiques ne couvrent pas toute la sélection.',
    en: 'Partial preview: statistics do not cover the entire selection.',
    ja: '部分的なプレビューです。統計は選択範囲全体を網羅していません。',
  },
  probeElapsedDays: { fr: '{count} j', en: '{count} d', ja: '{count}日' },
  probeElapsedHours: { fr: '{count} h', en: '{count} h', ja: '{count}時間' },
  probeElapsedMinutes: {
    fr: '{count} min',
    en: '{count} min',
    ja: '{count}分',
  },
  probeInactive: { fr: 'inactive', en: 'inactive', ja: '無効' },
  probeIntervalMean: { fr: 'Moy.', en: 'Mean' },
  probeLastReading: {
    fr: 'Dernière mesure reçue',
    en: 'Last reading received',
    ja: '最後に受信した測定値',
  },
  probeLatestInWindow: { fr: 'Dernière', en: 'Latest', ja: '最新' },
  probeLatestInterval: {
    fr: 'Dernière moyenne sur {minutes} min',
    en: 'Latest {minutes}-min mean',
  },
  probeLegendProbe: { fr: 'Sonde', en: 'Probe' },
  probeManualFallbackAction: {
    fr: 'Saisir une température à la main',
    en: 'Enter a temperature manually',
    ja: '水温を手入力',
  },
  probeMeasurementCount: {
    fr: 'Nombre de mesures',
    en: 'Measurement count',
    ja: '測定数',
  },
  probeMonitoringError: {
    fr: 'Impossible de mettre à jour le statut des sondes. Le dernier statut connu est conservé.',
    en: 'The probe status could not be refreshed. The last known status is retained.',
    ja: 'センサーの状態を更新できませんでした。最後に確認できた状態を保持しています。',
  },
  probeNoCollectedProbe: {
    fr: 'Aucune sonde n’est reliée à cet emplacement.',
    en: 'No probe is linked to this location.',
  },
  probeNoReadingYet: {
    fr: 'Aucune mesure reçue pour l’instant',
    en: 'No reading received yet',
    ja: 'まだ測定値を受信していません',
  },
  probeNoReadings: {
    fr: 'Aucune mesure sur cette période',
    en: 'No readings in this period',
    ja: 'この期間の水温データはありません。',
  },
  probePartialCoverage: {
    fr: 'Tranches partiellement affichées : les statistiques complètes peuvent inclure des mesures hors de la sélection.',
    en: 'Partially displayed buckets: complete statistics may include observations outside the selection.',
    ja: '集計区間の一部のみを表示しています。区間全体の統計には、選択範囲外の測定値が含まれる場合があります。',
  },
  probePeriod: {
    fr: 'Période affichée',
    en: 'Displayed period',
    ja: '表示期間',
  },
  probePeriodCustom: { fr: 'Dates précises', en: 'Exact dates' },
  probePeriodLast30Days: { fr: '30 derniers jours', en: 'Last 30 days' },
  probePeriodLast7Days: { fr: '7 derniers jours', en: 'Last 7 days' },
  probePeriod_24h: { fr: '24 h', en: '24 h', ja: '24時間' },
  probePeriod_30d: { fr: '30 jours', en: '30 days', ja: '30日間' },
  probePeriod_7d: { fr: '7 jours', en: '7 days', ja: '7日間' },
  probePeriod_90d: { fr: '90 jours', en: '90 days', ja: '90日間' },
  probeRangeApply: { fr: 'Afficher', en: 'Show' },
  probeRangeEdit: {
    fr: 'Modifier la période : {range}',
    en: 'Change the period: {range}',
  },
  probeRangeEnd: { fr: 'Au', en: 'To' },
  probeRangeInvalid: {
    fr: 'Choisissez une période de 30 minutes à 366 jours, sans date future.',
    en: 'Choose a period from 30 minutes to 366 days, with no future date.',
  },
  probeRangeReset: { fr: 'Dernières 24 h', en: 'Last 24 h' },
  probeRangeStart: { fr: 'Du', en: 'From' },
  probeRawReading: {
    fr: 'Mesure instantanée',
    en: 'Instantaneous measurement',
    ja: '瞬時測定値',
  },
  probeReadingAgo: {
    fr: 'il y a {duration}',
    en: '{duration} ago',
    ja: '{duration}前',
  },
  probeReadingsAction: {
    fr: 'Voir les mesures des sondes',
    en: 'View probe readings',
    ja: 'センサーの測定値を見る',
  },
  probeReadingsTitle: {
    fr: 'Mesures des sondes',
    en: 'Probe readings',
    ja: 'センサーの測定値',
  },
  probeResetZoom: {
    fr: 'Annuler le zoom',
    en: 'Reset zoom',
    ja: 'ズームをリセット',
  },
  probeRetry: { fr: 'Réessayer', en: 'Retry', ja: '再試行' },
  probeSeriesToggle: {
    fr: 'Afficher ou masquer {probe}',
    en: 'Show or hide {probe}',
  },
  probeSilentBadge: {
    fr: 'Sondes silencieuses',
    en: 'Silent probes',
    ja: '測定値未受信',
  },
  probeSilentDescription: {
    fr: 'Aucune mesure reçue depuis {duration}. Vous pouvez saisir la température à la main en attendant.',
    en: 'No reading received for {duration}. You can enter the temperature manually in the meantime.',
  },
  probeSilentNoReadingDescription: {
    fr: 'Aucune mesure reçue pour l’instant. Vous pouvez saisir la température à la main en attendant.',
    en: 'No reading received yet. You can enter the temperature manually in the meantime.',
  },
  probeTimeZone: {
    fr: 'Heures affichées dans le fuseau {zone}',
    en: 'Times shown in the {zone} time zone',
  },
  probeWindowNext: {
    fr: 'Période suivante',
    en: 'Next period',
    ja: '次の表示期間',
  },
  probeWindowPrevious: {
    fr: 'Période précédente',
    en: 'Previous period',
    ja: '前の表示期間',
  },
};
