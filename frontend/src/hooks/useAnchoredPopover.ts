import { useId, useLayoutEffect, useRef, useState, type CSSProperties } from 'react';

const OPEN_EVENT = 'polypbase:popover-open';

export function useAnchoredPopover<T extends HTMLElement>(
  isOpen: boolean,
  onClose: (restoreFocus?: boolean) => void,
  align: 'start' | 'end' = 'start',
) {
  const id = useId();
  const anchorRef = useRef<T>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<CSSProperties>({ visibility: 'hidden' });

  useLayoutEffect(() => {
    if (!isOpen) return;
    const anchor = anchorRef.current;
    const panel = panelRef.current;
    if (!anchor || !panel) return;

    function placePanel() {
      if (!anchor || !panel) return;
      const rect = anchor.getBoundingClientRect();
      if (rect.bottom < 0 || rect.top > window.innerHeight) {
        onClose();
        return;
      }
      const margin = 12;
      const gap = 6;
      const { width, height } = panel.getBoundingClientRect();
      const left = align === 'end' ? rect.right - width : rect.left;
      const below = rect.bottom + gap;
      const top = below + height <= window.innerHeight - margin
        ? below
        : rect.top - height - gap;
      setPosition({
        left: Math.max(margin, Math.min(left, window.innerWidth - width - margin)),
        top: Math.max(margin, Math.min(top, window.innerHeight - height - margin)),
        visibility: 'visible',
      });
    }

    function handleOutside(event: PointerEvent) {
      if (event.target instanceof Node && !anchor?.contains(event.target) && !panel?.contains(event.target)) onClose();
    }
    function handleEscape(event: KeyboardEvent) {
      if (event.key !== 'Escape') return;
      event.preventDefault();
      event.stopPropagation();
      onClose(true);
    }
    function handleOtherPopover(event: Event) {
      if ((event as CustomEvent<string>).detail !== id) onClose();
    }

    // Menus and previews share dismissal, but never lock the page like a modal.
    document.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: id }));
    document.addEventListener(OPEN_EVENT, handleOtherPopover);
    document.addEventListener('pointerdown', handleOutside);
    document.addEventListener('keydown', handleEscape, true);
    window.addEventListener('resize', placePanel);
    window.addEventListener('scroll', placePanel, true);
    const observer = new ResizeObserver(placePanel);
    observer.observe(panel);
    placePanel();
    return () => {
      observer.disconnect();
      document.removeEventListener(OPEN_EVENT, handleOtherPopover);
      document.removeEventListener('pointerdown', handleOutside);
      document.removeEventListener('keydown', handleEscape, true);
      window.removeEventListener('resize', placePanel);
      window.removeEventListener('scroll', placePanel, true);
    };
  }, [align, id, isOpen, onClose]);

  return { anchorRef, panelRef, position, id };
}
