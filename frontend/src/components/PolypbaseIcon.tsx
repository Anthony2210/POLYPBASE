import type { ReactNode, SVGProps } from 'react';

const iconPaths = {
  'chevron-left': <path d="M15 18l-6-6 6-6" />,
  'chevron-right': <path d="M9 18l6-6-6-6" />,
  'chevron-up': <path d="M18 15l-6-6-6 6" />,
  'chevron-down': <path d="M6 9l6 6 6-6" />,
  'chevrons-left': <path d="M11 17l-5-5 5-5M18 17l-5-5 5-5" />,
  'chevrons-right': <path d="M13 17l5-5-5-5M6 17l5-5-5-5" />,
  close: <path d="M18 6L6 18M6 6l12 12" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </>
  ),
  'reset-filter': (
    <>
      <path d="M20 4H4l6 8v7l4 2v-9z" />
      <path d="M2 2l20 20" />
    </>
  ),
  check: <path d="M20 6L9 17l-5-5" />,
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 16v-5M12 7h.01" />
    </>
  ),
  download: (
    <>
      <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
      <path d="M7 10l5 5 5-5M12 15V3" />
    </>
  ),
  print: (
    <>
      <path d="M6 9V3h12v6" />
      <path d="M6 18H4a2 2 0 01-2-2v-5a2 2 0 012-2h16a2 2 0 012 2v5a2 2 0 01-2 2h-2" />
      <path d="M6 14h12v7H6z" />
    </>
  ),
  edit: (
    <>
      <path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7" />
      <path d="M18.5 2.5a2.12 2.12 0 013 3L12 15l-4 1 1-4 9.5-9.5z" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a2 2 0 002 2h10a2 2 0 002-2V8M10 12h4" />
    </>
  ),
  'inactive-alt': (
    <>
      <rect x="5" y="3" width="14" height="4" rx="1" />
      <path d="M6 7v13a1 1 0 001 1h4.5M18 7v4.5" />
      <circle cx="16" cy="16" r="4" />
      <path d="M14 16h4" />
    </>
  ),
  restore: (
    <>
      <path d="M3 12a9 9 0 11.7 4.3" />
      <path d="M3 12h5M3 7v5" />
    </>
  ),
  logout: <path d="M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9" />,
  probe: (
    <>
      <path d="M12 4v11M10 15h4v3a2 2 0 01-4 0v-3z" />
      <path d="M4 12c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2" />
    </>
  ),
} satisfies Record<string, ReactNode>;

export type PolypbaseIconName = keyof typeof iconPaths;

type PolypbaseIconProps = Omit<SVGProps<SVGSVGElement>, 'children'> & {
  name: PolypbaseIconName;
  size?: number;
  strokeWidth?: number;
};

export default function PolypbaseIcon({
  className,
  name,
  size = 18,
  strokeWidth = 2,
  ...props
}: PolypbaseIconProps) {
  const classes = ['polypbase-icon', className].filter(Boolean).join(' ');

  return (
    <svg
      {...props}
      aria-hidden={props['aria-label'] ? undefined : true}
      className={classes}
      fill="none"
      focusable="false"
      height={size}
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={strokeWidth}
      viewBox="0 0 24 24"
      width={size}
    >
      {iconPaths[name]}
    </svg>
  );
}
