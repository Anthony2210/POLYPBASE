import type { BoxDetail, BoxItem } from '../types';

export type QrLabelItem = {
  id: number;
  globalCode: string;
  speciesName: string;
  zoneName: string;
  qrImageUrl: string;
};

export type QrLabelOrientation = 'portrait' | 'landscape';
export type QrLabelFontFamily = 'arial' | 'system' | 'georgia' | 'courier';

export type QrLabelPrintSettings = {
  orientation: QrLabelOrientation;
  columns: number;
  labelWidthMm: number;
  labelHeightMm: number;
  gapMm: number;
  paddingMm: number;
  qrSizeMm: number;
  codeFontPt: number;
  speciesFontPt: number;
  fontFamily: QrLabelFontFamily;
  showSpecies: boolean;
};

export const DEFAULT_QR_LABEL_PRINT_SETTINGS: QrLabelPrintSettings = {
  orientation: 'portrait',
  columns: 3,
  labelWidthMm: 58,
  labelHeightMm: 58,
  gapMm: 5,
  paddingMm: 2.8,
  qrSizeMm: 40,
  codeFontPt: 11.4,
  speciesFontPt: 7.4,
  fontFamily: 'arial',
  showSpecies: true,
};

export function buildQrLabelItem(box: BoxItem | BoxDetail, qrImageUrl?: string): QrLabelItem {
  return {
    id: box.id,
    globalCode: box.global_code,
    speciesName: box.species.scientific_name,
    zoneName: box.thermal_zone?.name ?? '',
    qrImageUrl: getBoxQrImageUrl(box, qrImageUrl),
  };
}

export function getBoxQrImageUrl(box: BoxItem | BoxDetail, explicitUrl?: string) {
  const source = explicitUrl
    || ('qr_image_url' in box && box.qr_image_url)
    || `/boites/${box.id}/qr.svg`;

  try {
    const url = new URL(source, window.location.origin);
    if (!/^\/boites\/\d+\/qr\.svg$/.test(url.pathname)) return source;

    url.searchParams.set('public_base_url', window.location.origin);
    return `${url.pathname}?${url.searchParams.toString()}`;
  } catch {
    return source;
  }
}

export function getBoxScanUrl(box: BoxDetail) {
  return new URL(`/bac/${box.id}/`, window.location.origin).href;
}

export function printQrLabels(labels: QrLabelItem[], settings?: Partial<QrLabelPrintSettings>) {
  if (!labels.length) return;

  const printWindow = window.open('', '_blank', 'width=980,height=720');
  if (!printWindow) return;

  void prepareQrPrint(labels, printWindow, normalizeQrLabelPrintSettings(settings));
}

export async function downloadQrLabel(label: QrLabelItem) {
  const qrDataUrl = await getQrDataUrl(label.qrImageUrl);
  const svg = buildQrLabelSvg(label, qrDataUrl);
  downloadTextFile(svg, `${label.globalCode}_etiquette.svg`, 'image/svg+xml;charset=utf-8');
}

async function prepareQrPrint(
  labels: QrLabelItem[],
  printWindow: Window,
  settings: QrLabelPrintSettings,
) {
  const printableLabels = await Promise.all(
    labels.map(async (label) => ({
      ...label,
      // Embed each QR image so the print document does not depend on a session request.
      qrImageUrl: await getQrDataUrl(label.qrImageUrl),
    })),
  );

  if (printWindow.closed) return;

  printWindow.document.write(buildQrPrintDocument(printableLabels, settings));
  printWindow.document.close();

  await Promise.all(
    Array.from(printWindow.document.images).map((image) => {
      if (image.complete) return Promise.resolve();
      return new Promise<void>((resolve) => {
        image.addEventListener('load', () => resolve(), { once: true });
        image.addEventListener('error', () => resolve(), { once: true });
      });
    }),
  );

  if (!printWindow.closed) {
    printWindow.focus();
    printWindow.print();
  }
}

async function getQrDataUrl(qrImageUrl: string) {
  try {
    const response = await fetch(qrImageUrl, { credentials: 'include' });
    if (!response.ok) throw new Error('QR unavailable');
    const svgText = await response.text();
    return `data:image/svg+xml;base64,${window.btoa(unescape(encodeURIComponent(svgText)))}`;
  } catch {
    return new URL(qrImageUrl, window.location.origin).href;
  }
}

function buildQrPrintDocument(labels: QrLabelItem[], settings: QrLabelPrintSettings) {
  const labelMarkup = labels.map((label, index) => {
    const previousLabel = labels[index - 1];
    const startsZone = Boolean(label.zoneName) && (!previousLabel || previousLabel.zoneName !== label.zoneName);
    return renderPrintableQrLabel(label, settings, startsZone);
  }).join('');
  const fontFamily = getPrintFontFamily(settings.fontFamily);

  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Etiquettes Polypbase</title>
<style>
  @page { size: A4 ${settings.orientation}; margin: 10mm; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #000; font-family: ${fontFamily}; }
  .sheet { display: grid; grid-template-columns: repeat(${settings.columns}, ${settings.labelWidthMm}mm); gap: ${settings.gapMm}mm; width: max-content; max-width: 100%; margin: 0 auto; padding-left: 4mm; align-items: start; justify-content: center; }
  .label-slot { position: relative; width: ${settings.labelWidthMm}mm; min-height: ${settings.labelHeightMm}mm; break-inside: avoid; page-break-inside: avoid; }
  .label { display: grid; align-content: center; justify-items: center; gap: 1.2mm; width: 100%; min-height: ${settings.labelHeightMm}mm; padding: ${settings.paddingMm}mm; border: 0.35mm solid #000; border-radius: 2mm; text-align: center; }
  .label-main { display: grid; gap: 0.6mm; min-width: 0; width: 100%; }
  .label-code { display: block; width: 100%; font-size: ${settings.codeFontPt}pt; font-style: italic; font-weight: 900; line-height: 0.92; overflow-wrap: anywhere; }
  .label-species { display: ${settings.showSpecies ? 'block' : 'none'}; color: #333; font-size: ${settings.speciesFontPt}pt; line-height: 1.1; overflow-wrap: anywhere; }
  .label-qr { display: grid; justify-items: center; }
  .label-qr img { width: ${settings.qrSizeMm}mm; height: ${settings.qrSizeMm}mm; image-rendering: pixelated; }
  .zone-marker { position: absolute; top: 1mm; bottom: 1mm; left: -3.4mm; z-index: 2; display: grid; align-items: center; padding-left: 0.8mm; border-left: 0.25mm solid #000; color: #000; font-size: 4.2pt; font-weight: 800; line-height: 1; letter-spacing: 0.2px; writing-mode: vertical-rl; transform: rotate(180deg); }
</style>
</head>
<body>
  <main class="sheet">${labelMarkup}</main>
</body>
</html>`;
}

function renderPrintableQrLabel(label: QrLabelItem, settings: QrLabelPrintSettings, showZoneMarker: boolean) {
  return `<div class="label-slot">
  ${showZoneMarker ? `<span class="zone-marker">${escapeHtml(label.zoneName)}</span>` : ''}
  <section class="label">
  <div class="label-qr">
    <img src="${escapeAttribute(new URL(label.qrImageUrl, window.location.origin).href)}" alt="">
  </div>
  <div class="label-main">
    <strong class="label-code">${escapeHtml(label.globalCode)}</strong>
    ${settings.showSpecies ? `<span class="label-species">${escapeHtml(label.speciesName)}</span>` : ''}
  </div>
</section>
</div>`;
}

function normalizeQrLabelPrintSettings(settings?: Partial<QrLabelPrintSettings>): QrLabelPrintSettings {
  return {
    orientation: 'portrait',
    columns: clampInteger(settings?.columns ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.columns, 1, 4),
    labelWidthMm: clampNumber(settings?.labelWidthMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.labelWidthMm, 30, 80),
    labelHeightMm: clampNumber(settings?.labelHeightMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.labelHeightMm, 30, 80),
    gapMm: clampNumber(settings?.gapMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.gapMm, 2, 18),
    paddingMm: clampNumber(settings?.paddingMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.paddingMm, 2, 12),
    qrSizeMm: clampNumber(settings?.qrSizeMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.qrSizeMm, 14, 55),
    codeFontPt: clampNumber(settings?.codeFontPt ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.codeFontPt, 9, 42),
    speciesFontPt: clampNumber(settings?.speciesFontPt ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.speciesFontPt, 6, 18),
    fontFamily: isQrLabelFontFamily(settings?.fontFamily)
      ? settings.fontFamily
      : DEFAULT_QR_LABEL_PRINT_SETTINGS.fontFamily,
    showSpecies: settings?.showSpecies ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.showSpecies,
  };
}

function isQrLabelFontFamily(value: unknown): value is QrLabelFontFamily {
  return value === 'arial' || value === 'system' || value === 'georgia' || value === 'courier';
}

function getPrintFontFamily(fontFamily: QrLabelFontFamily) {
  switch (fontFamily) {
    case 'system':
      return 'system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif';
    case 'georgia':
      return 'Georgia, "Times New Roman", serif';
    case 'courier':
      return '"Courier New", monospace';
    case 'arial':
    default:
      return 'Arial, sans-serif';
  }
}

function clampInteger(value: number, min: number, max: number) {
  return Math.round(clampNumber(value, min, max));
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.min(Math.max(value, min), max);
}

function buildQrLabelSvg(label: QrLabelItem, qrImageUrl: string) {
  const width = 420;
  const height = 420;
  const code = escapeXml(label.globalCode);
  const species = escapeXml(label.speciesName);
  const image = escapeXml(qrImageUrl);

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <rect x="10" y="10" width="400" height="400" rx="18" fill="#fff" stroke="#000" stroke-width="3"/>
  <image href="${image}" x="92" y="34" width="236" height="236"/>
  <text x="210" y="326" text-anchor="middle" font-family="Arial, sans-serif" font-size="36" font-weight="900" font-style="italic" fill="#111827">${code}</text>
  <text x="210" y="362" text-anchor="middle" font-family="Arial, sans-serif" font-size="19" fill="#4b5563">${species}</text>
</svg>`;
}

function downloadTextFile(content: string, fileName: string, type: string) {
  const blob = new Blob([content], { type });
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
}

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function escapeXml(value: string) {
  return escapeHtml(value).replace(/"/g, '&quot;').replace(/'/g, '&apos;');
}

function escapeAttribute(value: string) {
  return escapeXml(value);
}
