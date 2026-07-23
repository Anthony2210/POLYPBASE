type RequestOptions = RequestInit & {
  body?: BodyInit | null;
  skipOrganizationContext?: boolean;
};

const ACTIVE_ORGANIZATION_STORAGE_KEY = 'polypbase.activeOrganizationId';

export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, message: string, data: unknown = null) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export function getStoredActiveOrganizationId(): number | null {
  const value = window.localStorage.getItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
  if (!value) return null;
  const organizationId = Number(value);
  return Number.isInteger(organizationId) && organizationId > 0 ? organizationId : null;
}

export function setActiveOrganizationContext(organizationId: number | null) {
  if (organizationId == null) {
    window.localStorage.removeItem(ACTIVE_ORGANIZATION_STORAGE_KEY);
    return;
  }

  window.localStorage.setItem(ACTIVE_ORGANIZATION_STORAGE_KEY, String(organizationId));
}

export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return apiRequest<T>(path, options);
}

export async function apiEnsureCsrfCookie() {
  return apiGet<{ detail: string }>('/api/auth/session/');
}

export async function apiPost<T>(path: string, payload: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: JSON.stringify(payload),
  });
}

export async function apiPatch<T>(path: string, payload: unknown): Promise<T> {
  return apiRequest<T>(path, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': getCookie('csrftoken'),
    },
    body: JSON.stringify(payload),
  });
}

export async function apiDelete<T>(path: string): Promise<T> {
  return apiRequest<T>(path, {
    method: 'DELETE',
    headers: {
      'X-CSRFToken': getCookie('csrftoken'),
    },
  });
}

export async function apiDownload(path: string): Promise<string> {
  const response = await fetch(path, {
    credentials: 'include',
    headers: buildRequestHeaders(),
  });

  if (!response.ok) {
    const contentType = response.headers.get('content-type') ?? '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : await response.text();
    const detail = getErrorDetail(data) ?? 'Le telechargement a echoue.';
    throw new ApiError(response.status, detail, data);
  }

  const blob = await response.blob();
  const fileName = getDownloadFileName(response.headers.get('content-disposition'));
  const objectUrl = URL.createObjectURL(blob);
  const link = document.createElement('a');

  link.href = objectUrl;
  link.download = fileName;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(objectUrl);
  return fileName;
}

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
    ...options,
    headers: buildRequestHeaders(options.headers, options.skipOrganizationContext),
  });

  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json') ? await response.json() : null;

  if (!response.ok) {
    const detail = getErrorDetail(data) ?? 'La requete a echoue.';
    throw new ApiError(response.status, detail, data);
  }

  return data as T;
}

function buildRequestHeaders(input?: HeadersInit, skipOrganizationContext = false) {
  const headers = new Headers(input);
  const organizationId = getStoredActiveOrganizationId();

  if (!skipOrganizationContext && organizationId != null) {
    headers.set('X-Organization-Id', String(organizationId));
  }

  return headers;
}

function getErrorDetail(value: unknown): string | null {
  if (typeof value === 'string') {
    return value;
  }

  if (Array.isArray(value)) {
    for (const item of value) {
      const detail = getErrorDetail(item);
      if (detail) return detail;
    }
    return null;
  }

  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;

    for (const key of ['detail', 'error']) {
      const detail = getErrorDetail(record[key]);
      if (detail) return detail;
    }

    for (const item of Object.values(record)) {
      const detail = getErrorDetail(item);
      if (detail) return detail;
    }
  }

  return null;
}

function getCookie(name: string) {
  const value = document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${name}=`));

  return value ? decodeURIComponent(value.split('=').slice(1).join('=')) : '';
}

function getDownloadFileName(contentDisposition: string | null) {
  const match = contentDisposition?.match(/filename="?([^"]+)"?/i);
  return match?.[1] ?? 'polypbase_export.csv';
}
