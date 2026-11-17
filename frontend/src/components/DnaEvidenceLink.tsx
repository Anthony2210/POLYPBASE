import { ExternalLink } from 'lucide-react';

export function isDnaEvidenceUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 2000) return false;
  try {
    const url = new URL(trimmed);
    return (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.hostname);
  } catch {
    return false;
  }
}

export function DnaEvidenceLink({ url }: { url: string }) {
  if (!isDnaEvidenceUrl(url)) return <span>{url}</span>;
  return (
    <a href={url} title={url} target="_blank" rel="noopener noreferrer">
      <span>{new URL(url).hostname}</span>
      <ExternalLink aria-hidden="true" size={14} />
    </a>
  );
}
