import type { ReactNode, SVGProps } from 'react';

const iconPaths = {
  location: (
    <>
      <path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 0 1 14 0Z" />
      <circle cx="12" cy="10" r="2.5" />
    </>
  ),
  'box-alt': (
    <>
      <ellipse cx="12" cy="5" rx="7" ry="1.5" />
      <path d="M5 5v3c0 .8 3.1 1.5 7 1.5s7-.7 7-1.5V5" />
      <path d="M6 8v10c0 1.1 2.7 2 6 2s6-.9 6-2V8" />
    </>
  ),
  'box-location-alt': (
    <>
      <path d="M3 10v9a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-9" />
      <path d="M3 14c2.5 0 2.5-2 5-2s2.5 2 5 2 2.5-2 5-2 2.5 2 3 2" />
      <rect x="9" y="9" width="6" height="2" rx="0.5" />
      <path d="M9.5 11v5h5v-5" />
    </>
  ),
  'qr-scan': (
    <>
      <path d="M8 4H5a1 1 0 0 0-1 1v3M16 4h3a1 1 0 0 1 1 1v3M20 16v3a1 1 0 0 1-1 1h-3M8 20H5a1 1 0 0 1-1-1v-3" />
      <rect x="8" y="8" width="3" height="3" rx="0.5" />
      <rect x="13" y="8" width="3" height="3" rx="0.5" />
      <rect x="8" y="13" width="3" height="3" rx="0.5" />
    </>
  ),
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
  'culture-box': (
    <>
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <circle cx="7" cy="9" r="1.5" />
      <circle cx="12" cy="9" r="1.5" />
      <circle cx="17" cy="9" r="1.5" />
      <circle cx="7" cy="15" r="1.5" />
      <circle cx="12" cy="15" r="1.5" />
      <circle cx="17" cy="15" r="1.5" />
    </>
  ),
  overview: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="18" height="7" rx="1" />
    </>
  ),
  'thermal-zone': (
    <>
      <rect x="3" y="17" width="18" height="4" rx="1" />
      <path d="M7 13c1-1 1-2 0-3s-1-2 0-3" />
      <path d="M12 13c1-1 1-2 0-3s-1-2 0-3" />
      <path d="M17 13c1-1 1-2 0-3s-1-2 0-3" />
    </>
  ),
  'export-data': (
    <>
      <path d="M14 2H6a2 2 0 00-2 2v16c0 1.1.9 2 2 2h12a2 2 0 002-2V8l-6-6z" />
      <path d="M14 2v6h6" />
      <path d="M12 12v6M9 15l3 3 3-3" />
    </>
  ),
  'label-qr': (
    <>
      <path d="M3 7a2 2 0 012-2h14a2 2 0 012 2v10a2 2 0 01-2 2H5a2 2 0 01-2-2z" />
      <rect x="7" y="9" width="3" height="3" rx="0.5" />
      <rect x="14" y="9" width="3" height="3" rx="0.5" />
      <rect x="7" y="14" width="3" height="3" rx="0.5" />
      <path d="M14 15h3v2h-3z" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" />
    </>
  ),
  user: (
    <>
      <path d="M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2" />
      <circle cx="12" cy="7" r="4" />
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
