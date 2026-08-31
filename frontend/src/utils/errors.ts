import { ApiError } from '../api/client';

export function getErrorMessage(
  error: unknown,
  fallback = 'Impossible de joindre l API Django.',
) {
  if (error instanceof ApiError) {
    return error.message;
  }

  return fallback;
}
