import type { BoxDetail, BoxItem } from '../types';

export type QrLabelItem = {
  id: number;
  globalCode: string;
  speciesName: string;
  qrImageUrl: string;
};

export function buildQrLabelItem(box: BoxItem | BoxDetail, qrImageUrl?: string): QrLabelItem {
  return {
    id: box.id,
    globalCode: box.global_code,
    speciesName: box.species.scientific_name,
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

export function printQrLabels(labels: QrLabelItem[]) {
  if (!labels.length) return;

  const printWindow = window.open('', '_blank', 'width=980,height=720');
  if (!printWindow) return;

  void prepareQrPrint(labels, printWindow);
}

export async function downloadQrLabel(label: QrLabelItem) {
  const qrDataUrl = await getQrDataUrl(label.qrImageUrl);
  const svg = buildQrLabelSvg(label, qrDataUrl);
  downloadTextFile(svg, `${label.globalCode}_etiquette.svg`, 'image/svg+xml;charset=utf-8');
}

async function prepareQrPrint(labels: QrLabelItem[], printWindow: Window) {
  const printableLabels = await Promise.all(
    labels.map(async (label) => ({
      ...label,
      // Embed each QR image so the print document does not depend on a session request.
      qrImageUrl: await getQrDataUrl(label.qrImageUrl),
    })),
  );

  if (printWindow.closed) return;

  printWindow.document.write(buildQrPrintDocument(printableLabels));
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

function buildQrPrintDocument(labels: QrLabelItem[]) {
  const labelMarkup = labels.map(renderPrintableQrLabel).join('');

  return `<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Etiquettes Polypbase</title>
<style>
  @page { size: A4; margin: 10mm; }
  * { box-sizing: border-box; }
  body { margin: 0; color: #000; font-family: Arial, sans-serif; }
  .sheet { display: grid; grid-template-columns: repeat(2, 92mm); gap: 7mm; align-items: start; }
  .label { display: grid; grid-template-columns: 1fr 30mm; gap: 5mm; min-height: 38mm; padding: 4.5mm; border: 0.35mm solid #000; border-radius: 2mm; break-inside: avoid; page-break-inside: avoid; }
  .label-main { display: grid; align-content: center; gap: 1.2mm; min-width: 0; }
  .label-code { display: block; width: 100%; font-size: 19pt; font-style: italic; font-weight: 900; line-height: 0.95; overflow-wrap: anywhere; }
  .label-species { font-size: 8.5pt; }
  .label-qr { display: grid; justify-items: center; gap: 1mm; font-size: 7pt; font-weight: 800; text-align: center; }
  .label-qr img { width: 27mm; height: 27mm; image-rendering: pixelated; }
</style>
</head>
<body>
  <main class="sheet">${labelMarkup}</main>
</body>
</html>`;
}

function renderPrintableQrLabel(label: QrLabelItem) {
  return `<section class="label">
  <div class="label-main">
    <strong class="label-code">${escapeHtml(label.globalCode)}</strong>
    <span class="label-species">${escapeHtml(label.speciesName)}</span>
  </div>
  <div class="label-qr">
    <img src="${escapeAttribute(new URL(label.qrImageUrl, window.location.origin).href)}" alt="">
    <span>QR code</span>
  </div>
</section>`;
}

function buildQrLabelSvg(label: QrLabelItem, qrImageUrl: string) {
  const width = 720;
  const height = 300;
  const code = escapeXml(label.globalCode);
  const species = escapeXml(label.speciesName);
  const image = escapeXml(qrImageUrl);

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
  <rect x="8" y="8" width="704" height="284" rx="18" fill="#fff" stroke="#000" stroke-width="3"/>
  <text x="34" y="100" font-family="Arial, sans-serif" font-size="64" font-weight="900" font-style="italic" fill="#111827">${code}</text>
  <text x="34" y="142" font-family="Arial, sans-serif" font-size="19" fill="#667085">${species}</text>
  <rect x="514" y="34" width="166" height="210" rx="16" fill="#fff" stroke="#d9e2ec"/>
  <image href="${image}" x="548" y="56" width="98" height="98"/>
  <text x="597" y="206" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="800" fill="#0b7890">QR code</text>
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
