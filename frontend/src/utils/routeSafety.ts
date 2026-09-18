export function isRouteRequestCurrent(
  requestGeneration: number,
  currentRequestGeneration: number,
  navigationGeneration: number,
  currentNavigationGeneration: number,
): boolean {
  return requestGeneration === currentRequestGeneration
    && navigationGeneration === currentNavigationGeneration;
}
