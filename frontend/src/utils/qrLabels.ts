import type { BoxDetail, BoxItem } from '../types';

export type QrLabelItem = {
  id: number;
  globalCode: string;
  speciesName: string;
  zoneName: string;
  qrImageUrl: string;
};


export type QrLabelFontFamily = 'arial' | 'system' | 'georgia' | 'courier';

export type QrLabelPrintSettings = {
  labelWidthMm: number;
  labelHeightMm: number;
  paddingMm: number;
  qrSizeMm: number;
  textFontPt: number;
  fontFamily: QrLabelFontFamily;
  showSpecies: boolean;
};


// Physical label requested by Aquarium de Paris: 40 mm wide x 30 mm high.
// The QR sits at the left extremity and the code/species block is rotated 90
// degrees in the right band. These values are shared by print, modal and SVG.
export const QR_LABEL_WIDTH_MM = 40;
export const QR_LABEL_HEIGHT_MM = 30;
export const QR_LABEL_QR_SIZE_MM = 25;
export const QR_LABEL_PADDING_MM = 0.8;

export const QR_LABEL_BORDER_MM = 0.35;
export const QR_LABEL_QR_TEXT_GAP_MM = 0.5;
export const QR_LABEL_TEXT_GAP_MM = 0.4;
// The rotated block is laid out at full inner-label length before rotation. Its
// height becomes the visible width of the narrow band beside the QR.
export const QR_LABEL_TEXT_ZONE_MM = Math.round((
  QR_LABEL_WIDTH_MM
  - 2 * QR_LABEL_BORDER_MM
  - 2 * QR_LABEL_PADDING_MM
  - QR_LABEL_QR_SIZE_MM
  - QR_LABEL_QR_TEXT_GAP_MM
) * 1000) / 1000;
export const QR_LABEL_TEXT_LINE_LENGTH_MM = Math.round((
  QR_LABEL_HEIGHT_MM
  - 2 * QR_LABEL_BORDER_MM
  - 2 * QR_LABEL_PADDING_MM
) * 1000) / 1000;
// At 7 pt, the longest representative code (PARTNER-AAU-1.001) and species
// (Cassiopea andromeda) fit the 27.7 mm line without compression. At 7.5 pt,
// the bold code no longer fits under the conservative Arial width estimate.
export const QR_LABEL_TEXT_FONT_PT = 7;
export const QR_LABEL_SPECIES_FONT_PT = 7.25;

export const DEFAULT_QR_LABEL_PRINT_SETTINGS: QrLabelPrintSettings = {
  labelWidthMm: QR_LABEL_WIDTH_MM,
  labelHeightMm: QR_LABEL_HEIGHT_MM,
  paddingMm: QR_LABEL_PADDING_MM,
  qrSizeMm: QR_LABEL_QR_SIZE_MM,
  textFontPt: QR_LABEL_TEXT_FONT_PT,
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

export function buildQrPrintDocument(labels: QrLabelItem[], settings: QrLabelPrintSettings) {
  const pageMarkup = labels.map((label) => renderPrintableQrLabel(label, settings)).join('');
  const fontFamily = getPrintFontFamily(settings.fontFamily);

  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Etiquettes Polypbase</title>
<style>
  @page { size: ${settings.labelWidthMm}mm ${settings.labelHeightMm}mm; margin: 0; }
  * { box-sizing: border-box; }
  html, body { width: ${settings.labelWidthMm}mm; margin: 0; color: #000; font-family: ${fontFamily}; }
  .label-slot { display: block; width: ${settings.labelWidthMm}mm; height: ${settings.labelHeightMm}mm; overflow: hidden; break-after: page; page-break-after: always; break-inside: avoid; page-break-inside: avoid; }
  .label-slot:last-child { break-after: auto; page-break-after: auto; }
  .label { display: flex; flex-direction: row; align-items: center; width: 100%; height: 100%; overflow: hidden; padding: ${settings.paddingMm}mm; border: ${QR_LABEL_BORDER_MM}mm solid #000; border-radius: 1.5mm; }
  .label-qr { flex: 0 0 auto; display: grid; justify-items: center; }
  .label-qr img { display: block; width: ${settings.qrSizeMm}mm; height: ${settings.qrSizeMm}mm; object-fit: contain; transform: rotate(-90deg); transform-origin: center; }
  .label-main { position: relative; flex: 1 1 auto; align-self: stretch; min-width: 0; margin-left: ${QR_LABEL_QR_TEXT_GAP_MM}mm; overflow: hidden; }
  .label-text { position: absolute; top: 50%; left: 50%; display: grid; align-content: center; width: ${QR_LABEL_TEXT_LINE_LENGTH_MM}mm; height: ${QR_LABEL_TEXT_ZONE_MM}mm; gap: ${QR_LABEL_TEXT_GAP_MM}mm; text-align: center; transform: translate(-50%, -50%) rotate(-90deg); transform-origin: center; }
  .label-code { display: block; width: 100%; font-size: ${settings.textFontPt}pt; font-style: italic; font-weight: 900; line-height: 1.05; overflow-wrap: break-word; }
  .label-species { display: ${settings.showSpecies ? '-webkit-box' : 'none'}; width: 100%; color: #333; font-size: ${settings.textFontPt * QR_LABEL_SPECIES_FONT_PT / QR_LABEL_TEXT_FONT_PT}pt; font-style: italic; line-height: 1.1; overflow-wrap: break-word; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden; }
</style>
</head>
<body>
  ${pageMarkup}
</body>
</html>`;
}



// Express a physical millimetre value as a percentage of the label width, for
// previews whose query container is the label itself.
export function getQrLabelWidthPercent(mm: number) {
  return roundPercent((mm / QR_LABEL_WIDTH_MM) * 100);
}

export function pointsToMillimetres(pt: number) {
  return (pt * 25.4) / 72;
}

// CSS custom properties for the single-label preview. They are read as `cqw`
// units, so the label must sit inside a `container-type: inline-size` frame
// whose width is the label width.
export function getQrLabelPreviewCssVariables(settings: QrLabelPrintSettings) {
  return {
    '--label-preview-ratio': `${settings.labelWidthMm} / ${settings.labelHeightMm}`,
    '--label-preview-qr-size': `${getQrLabelWidthPercent(settings.qrSizeMm)}cqw`,
    '--label-preview-padding': `${getQrLabelWidthPercent(settings.paddingMm)}cqw`,
    '--label-preview-gap': `${getQrLabelWidthPercent(QR_LABEL_QR_TEXT_GAP_MM)}cqw`,
    '--label-preview-text-gap': `${getQrLabelWidthPercent(QR_LABEL_TEXT_GAP_MM)}cqw`,
    '--label-preview-text-zone-width': `${getQrLabelWidthPercent(QR_LABEL_TEXT_ZONE_MM)}cqw`,
    '--label-preview-text-line-length': `${getQrLabelWidthPercent(QR_LABEL_TEXT_LINE_LENGTH_MM)}cqw`,
    '--label-preview-font-size': `${getQrLabelWidthPercent(pointsToMillimetres(settings.textFontPt))}cqw`,
    '--label-preview-species-font-size': `${getQrLabelWidthPercent(pointsToMillimetres(settings.textFontPt * QR_LABEL_SPECIES_FONT_PT / QR_LABEL_TEXT_FONT_PT))}cqw`,
  };
}


function roundPercent(value: number) {
  return Math.round(value * 10000) / 10000;
}

function renderPrintableQrLabel(label: QrLabelItem, settings: QrLabelPrintSettings) {
  return `<main class="label-slot">
  <section class="label">
  <div class="label-qr">
    <img src="${escapeAttribute(new URL(label.qrImageUrl, window.location.origin).href)}" alt="">
  </div>
  <div class="label-main">
    <div class="label-text">
      <strong class="label-code">${escapeHtml(label.globalCode)}</strong>
      ${settings.showSpecies ? `<span class="label-species">${escapeHtml(label.speciesName)}</span>` : ''}
    </div>
  </div>
</section>
</main>`;
}

function normalizeQrLabelPrintSettings(settings?: Partial<QrLabelPrintSettings>): QrLabelPrintSettings {
  return {
    labelWidthMm: QR_LABEL_WIDTH_MM,
    labelHeightMm: QR_LABEL_HEIGHT_MM,
    paddingMm: clampNumber(settings?.paddingMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.paddingMm, 0.5, 12),
    qrSizeMm: clampNumber(settings?.qrSizeMm ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.qrSizeMm, 14, 55),
    textFontPt: clampNumber(settings?.textFontPt ?? DEFAULT_QR_LABEL_PRINT_SETTINGS.textFontPt, 6, 18),
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


function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) return min;
  return Math.min(Math.max(value, min), max);
}

// Rough Arial advance width per character, used only to wrap the downloaded SVG
// text. The browser print document stays the authoritative artifact.
const QR_LABEL_SVG_CHAR_WIDTH_RATIO = 0.55;
const QR_LABEL_SVG_BOLD_CHAR_WIDTH_RATIO = 0.62;

// The downloaded SVG mirrors the printed label: 40 x 30 mm landscape, QR at the
// left extremity, with the text block rotated counter-clockwise in the right band.
export function buildQrLabelSvg(label: QrLabelItem, qrImageUrl: string) {
  const width = QR_LABEL_WIDTH_MM;
  const height = QR_LABEL_HEIGHT_MM;
  const inset = QR_LABEL_BORDER_MM + QR_LABEL_PADDING_MM;
  const fontSize = pointsToMillimetres(QR_LABEL_TEXT_FONT_PT);
  const codeLineHeight = fontSize * 1.05;
  const speciesFontSize = pointsToMillimetres(QR_LABEL_SPECIES_FONT_PT);
  const speciesLineHeight = speciesFontSize * 1.1;
  const qrX = inset;
  const qrY = (height - QR_LABEL_QR_SIZE_MM) / 2;
  const textZoneLeft = qrX + QR_LABEL_QR_SIZE_MM + QR_LABEL_QR_TEXT_GAP_MM;
  const textZoneRight = width - inset;
  const textCenterX = (textZoneLeft + textZoneRight) / 2;

  const codeLines = wrapSvgText(
    label.globalCode,
    QR_LABEL_TEXT_LINE_LENGTH_MM,
    fontSize,
    QR_LABEL_SVG_BOLD_CHAR_WIDTH_RATIO,
    2,
  );
  const speciesLines = wrapSvgText(
    label.speciesName,
    QR_LABEL_TEXT_LINE_LENGTH_MM,
    speciesFontSize,
    QR_LABEL_SVG_CHAR_WIDTH_RATIO,
    2,
  );

  const codeBlockHeight = codeLines.length * codeLineHeight;
  const speciesBlockHeight = speciesLines.length * speciesLineHeight;
  const textBlockHeight = codeBlockHeight + QR_LABEL_TEXT_GAP_MM + speciesBlockHeight;
  const textTop = -textBlockHeight / 2;

  const codeMarkup = codeLines.map((line, index) => (
    `<text x="0" y="${roundMm(textTop + codeLineHeight * (index + 0.8))}" text-anchor="middle" font-family="Arial, sans-serif" font-size="${roundMm(fontSize)}" font-weight="900" font-style="italic" fill="#111"${svgTextFitAttributes(line, fontSize, QR_LABEL_TEXT_LINE_LENGTH_MM, QR_LABEL_SVG_BOLD_CHAR_WIDTH_RATIO)}>${escapeXml(line)}</text>`
  )).join('\n    ');
  const speciesMarkup = speciesLines.map((line, index) => (
    `<text x="0" y="${roundMm(textTop + codeBlockHeight + QR_LABEL_TEXT_GAP_MM + speciesLineHeight * (index + 0.8))}" text-anchor="middle" font-family="Arial, sans-serif" font-size="${roundMm(speciesFontSize)}" font-style="italic" fill="#333"${svgTextFitAttributes(line, speciesFontSize, QR_LABEL_TEXT_LINE_LENGTH_MM, QR_LABEL_SVG_CHAR_WIDTH_RATIO)}>${escapeXml(line)}</text>`
  )).join('\n    ');

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}mm" height="${height}mm" viewBox="0 0 ${width} ${height}">
  <rect x="${roundMm(QR_LABEL_BORDER_MM / 2)}" y="${roundMm(QR_LABEL_BORDER_MM / 2)}" width="${roundMm(width - QR_LABEL_BORDER_MM)}" height="${roundMm(height - QR_LABEL_BORDER_MM)}" rx="1.5" fill="#fff" stroke="#000" stroke-width="${QR_LABEL_BORDER_MM}"/>
  <image href="${escapeXml(qrImageUrl)}" x="${roundMm(qrX)}" y="${roundMm(qrY)}" width="${QR_LABEL_QR_SIZE_MM}" height="${QR_LABEL_QR_SIZE_MM}" transform="rotate(-90 ${roundMm(qrX + QR_LABEL_QR_SIZE_MM / 2)} ${roundMm(qrY + QR_LABEL_QR_SIZE_MM / 2)})"/>
  <g class="label-text" transform="translate(${roundMm(textCenterX)} ${roundMm(height / 2)}) rotate(-90)">
    ${codeMarkup}
    ${speciesMarkup}
  </g>
</svg>`;
}

// Greedy wrap that honours the break opportunities a browser uses: after a
// hyphen and after a space. Remaining words stay on the last line instead of
// being dropped.
function wrapSvgText(
  value: string,
  maxWidthMm: number,
  fontSizeMm: number,
  charWidthRatio: number,
  maxLines: number,
) {
  const segments = value.match(/[^\s-]+[\s-]*/g) ?? [];
  if (!segments.length) return [''];

  const widthOf = (text: string) => text.length * fontSizeMm * charWidthRatio;
  const lines: string[] = [];

  for (const segment of segments) {
    const current = lines[lines.length - 1];
    if (current && widthOf(`${current}${segment}`) <= maxWidthMm) {
      lines[lines.length - 1] = `${current}${segment}`;
      continue;
    }
    if (lines.length < maxLines) {
      lines.push(segment);
      continue;
    }
    lines[lines.length - 1] = `${lines[lines.length - 1]}${segment}`;
  }

  return lines.map((line) => line.trim());
}

function svgTextFitAttributes(
  value: string,
  fontSizeMm: number,
  maxWidthMm: number,
  charWidthRatio: number,
) {
  const estimatedWidthMm = value.length * fontSizeMm * charWidthRatio;
  if (estimatedWidthMm <= maxWidthMm) return '';
  return ` textLength="${roundMm(maxWidthMm)}" lengthAdjust="spacingAndGlyphs"`;
}

function roundMm(value: number) {
  return Math.round(value * 1000) / 1000;
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
