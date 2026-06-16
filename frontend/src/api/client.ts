type RequestOptions = RequestInit & {
  body?: BodyInit | null;
};

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function apiGet<T>(path: string): Promise<T> {
  return apiRequest<T>(path);
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

export async function apiDownload(path: string): Promise<string> {
  const response = await fetch(path, {
    credentials: 'include',
  });

  if (!response.ok) {
    const contentType = response.headers.get('content-type') ?? '';
    const data = contentType.includes('application/json')
      ? await response.json()
      : await response.text();
    const detail = getErrorDetail(data) ?? 'Le téléchargement a échoué.';
    throw new ApiError(response.status, detail);
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
  });

  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json') ? await response.json() : null;

  if (!response.ok) {
    const detail = getErrorDetail(data) ?? 'La requête a échoué.';
    throw new ApiError(response.status, detail);
  }

  return data as T;
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
