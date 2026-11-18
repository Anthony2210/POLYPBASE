type AnchorRect = { top: number; bottom: number; left: number; right: number };

export function getAnchoredPopoverPlacement(
  anchor: AnchorRect,
  panel: { width: number; height: number },
  viewport: { width: number; height: number },
  align: 'start' | 'end' = 'start',
) {
  const margin = 12;
  const gap = 6;
  const below = Math.max(0, viewport.height - margin - anchor.bottom - gap);
  const above = Math.max(0, anchor.top - gap - margin);
  const useBelow = panel.height <= below || (panel.height > above && below >= above);
  const maxHeight = useBelow ? below : above;
  const height = Math.min(panel.height, maxHeight);
  const left = align === 'end' ? anchor.right - panel.width : anchor.left;

  return {
    left: Math.max(margin, Math.min(left, viewport.width - panel.width - margin)),
    top: useBelow ? anchor.bottom + gap : anchor.top - gap - height,
    maxHeight,
  };
}
