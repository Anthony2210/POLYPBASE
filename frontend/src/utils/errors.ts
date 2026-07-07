import { ApiError } from '../api/client';

export function getErrorMessage(error: unknown) {
  if (error instanceof ApiError) {
    if (error.status === 401 || error.status === 403) {
      return 'Connecte-toi avec un compte demo pour voir les donnees.';
    }
    return error.message;
  }

  return 'Impossible de joindre l API Django.';
}
