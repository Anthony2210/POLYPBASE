import { ApiError } from '../api/client';

export function isBoxLocationChangedError(error: unknown) {
  return error instanceof ApiError
    && error.status === 409
    && typeof error.data === 'object'
    && error.data !== null
    && 'code' in error.data
    && error.data.code === 'box_location_changed';
}
