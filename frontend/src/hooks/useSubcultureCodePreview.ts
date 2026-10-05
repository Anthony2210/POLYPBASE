import { useEffect, useState } from 'react';

import { apiGet, getOrganizationResourceSignal } from '../api/client';

type Preview = {
  reserved: false;
  children: { position: number; global_code: string; box_number: string }[];
};

// Advisory identities only. Never include preview codes in the mutation payload.
export default function useSubcultureCodePreview(boxId: number, organizationId: number, count: number) {
  const [preview, setPreview] = useState<{ requestKey: string; codes: string[] } | null>(null);
  const requestKey = `${organizationId}:${boxId}:${count}`;

  useEffect(() => {
    const controller = new AbortController();
    const contextSignal = getOrganizationResourceSignal();
    const abort = () => controller.abort();
    contextSignal.addEventListener('abort', abort, { once: true });
    if (contextSignal.aborted) abort();
    apiGet<Preview>(`/api/boxes/${boxId}/subcultures/code-preview/?count=${count}`, {
      signal: controller.signal,
      cache: 'no-store',
    }).then((result) => {
      if (!controller.signal.aborted && result.reserved === false && result.children.length === count) {
        setPreview({ requestKey, codes: result.children.map((child) => child.global_code) });
      }
    }).catch(() => {
      // A failed advisory read must not block the operation or invent an identity.
      if (!controller.signal.aborted) setPreview(null);
    });
    return () => {
      controller.abort();
      contextSignal.removeEventListener('abort', abort);
    };
  }, [boxId, organizationId, count, requestKey]);

  return preview?.requestKey === requestKey ? preview.codes : [];
}
