import { ApiError } from '../api/client';

export type SpeciesCodeAssignment = { id: number; species: number; species_scientific_name: string; code: string };

export function hasSpeciesCode(assignments: SpeciesCodeAssignment[] | null, speciesId: number): boolean | null {
  return assignments === null ? null : assignments.some((assignment) => assignment.species === speciesId);
}

export function isMissingStrainSpeciesCode(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 400 || !error.data || typeof error.data !== 'object') return false;
  const species = (error.data as Record<string, unknown>).species;
  const messages = Array.isArray(species) ? species : [species];
  return messages.some((message) => typeof message === 'string' && message.includes('Assign an AAA code'));
}
