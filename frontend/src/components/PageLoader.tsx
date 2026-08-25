import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';

export type PageLoaderVariant =
  | 'pilotage'
  | 'overview'
  | 'box'
  | 'zones'
  | 'zone'
  | 'exports'
  | 'labels'
  | 'admin'
  | 'profile';

const LOADER_DELAY_MS = 140;

function Block({ className = '' }: { className?: string }) {
  return <span className={`loader-block ${className}`.trim()} />;
}

function Blocks({ count, className = '' }: { count: number; className?: string }) {
  return Array.from({ length: count }, (_, index) => (
    <span className={`loader-block ${className}`.trim()} key={index} />
  ));
}

function Panel({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`loader-panel ${className}`.trim()}>{children}</div>;
}

export default function PageLoader({
  label = 'Chargement',
  variant,
}: {
  label?: string;
  variant: PageLoaderVariant;
}) {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(() => setIsVisible(true), LOADER_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, []);

  return (
    <section
      className={`page-loader page-loader-${variant}${isVisible ? ' is-visible' : ''}`}
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">{label}</span>
      <div className="page-loader-content" aria-hidden="true">
        {renderLoaderContent(variant)}
      </div>
    </section>
  );
}

function renderLoaderContent(variant: PageLoaderVariant) {
  switch (variant) {
    case 'pilotage':
      return (
        <>
          <div className="loader-search">
            <Block className="is-label" />
            <Block className="is-input" />
          </div>
          <div className="loader-recent">
            <Block className="is-heading" />
            <div className="loader-strip"><Blocks count={5} /></div>
          </div>
          <Block className="loader-scanner" />
        </>
      );

    case 'overview':
      return (
        <>
          <div className="loader-summary"><Blocks count={3} /></div>
          <Panel className="loader-zone-filters">
            <Block className="is-heading" />
            <div className="loader-strip"><Blocks count={4} /></div>
          </Panel>
          <Panel className="loader-filter-bar"><Blocks count={4} /></Panel>
          <div className="loader-overview-grid">
            {Array.from({ length: 4 }, (_, index) => (
              <div className="loader-panel loader-overview-card" key={index}>
                <Block className="is-heading" />
                <Block className="is-metric" />
                <Block className="is-chart" />
              </div>
            ))}
          </div>
        </>
      );

    case 'box':
      return (
        <>
          <Panel className="loader-entity-header">
            <div className="loader-identity">
              <Block className="is-label" />
              <Block className="is-title" />
              <Block className="is-text" />
              <Block className="is-meta" />
            </div>
            <Block className="loader-qr" />
            <div className="loader-location">
              <Block className="is-label" />
              <Block className="is-heading" />
              <div className="loader-metrics"><Blocks count={3} /></div>
            </div>
            <div className="loader-actions"><Blocks count={3} /></div>
          </Panel>
          <div className="loader-box-body">
            <Panel className="loader-measurement-form">
              <Block className="is-heading" />
              <Block className="is-input-wide" />
              <div className="loader-form-grid"><Blocks count={3} /></div>
              <Block className="is-textarea" />
              <Block className="is-button" />
            </Panel>
            <Panel className="loader-last-measurement">
              <Block className="is-heading" />
              <Blocks count={3} className="is-metric" />
            </Panel>
          </div>
          <Panel className="loader-insights">
            <Block className="is-tabs" />
            <Block className="is-chart" />
          </Panel>
        </>
      );

    case 'zones':
      return (
        <div className="loader-zones-grid">
          {Array.from({ length: 6 }, (_, index) => (
            <div className="loader-panel loader-zone-card" key={index}>
              <Block className="is-heading" />
              <Block className="is-text" />
              <div className="loader-form-grid"><Blocks count={2} /></div>
              <Block className="is-progress" />
              <div className="loader-form-grid"><Blocks count={2} /></div>
            </div>
          ))}
        </div>
      );

    case 'zone':
      return (
        <>
          <Panel className="loader-zone-header">
            <div className="loader-identity">
              <Block className="is-label" />
              <Block className="is-title" />
              <Block className="is-text" />
            </div>
            <div className="loader-summary"><Blocks count={5} /></div>
          </Panel>
          <Panel className="loader-thermal-chart">
            <Block className="is-heading" />
            <Block className="is-chart" />
            <div className="loader-summary"><Blocks count={4} /></div>
          </Panel>
          <div className="loader-zone-body">
            <Panel className="loader-list"><Blocks count={7} /></Panel>
            <Panel className="loader-side-chart">
              <Block className="is-heading" />
              <Blocks count={5} className="is-bar" />
            </Panel>
          </div>
        </>
      );

    case 'exports':
      return (
        <>
          <Panel className="loader-period">
            <Block className="is-heading" />
            <div className="loader-form-grid"><Blocks count={2} /></div>
          </Panel>
          <Panel className="loader-filter-list"><Blocks count={5} /></Panel>
          <Panel className="loader-export-chart">
            <Block className="is-heading" />
            <Block className="is-chart" />
          </Panel>
          <Block className="loader-download" />
        </>
      );

    case 'labels':
      return (
        <>
          <div className="loader-label-tabs"><Blocks count={2} /></div>
          <div className="loader-label-workspace">
            <Panel className="loader-label-selection">
              <div className="loader-label-heading">
                <div>
                  <Block className="is-heading" />
                  <Block className="is-text" />
                </div>
                <div className="loader-label-actions"><Blocks count={2} /></div>
              </div>
              <div className="loader-label-controls"><Blocks count={2} className="is-input-wide" /></div>
              <Block className="loader-label-group-heading" />
              <div className="loader-label-grid"><Blocks count={8} /></div>
              <Block className="loader-label-group-heading" />
              <div className="loader-label-grid"><Blocks count={4} /></div>
            </Panel>
            <Panel className="loader-sheet-preview">
              <div className="loader-label-preview-heading">
                <Block className="is-heading" />
                <Block className="is-button" />
              </div>
              <div className="loader-label-preview-summary"><Blocks count={2} /></div>
              <div className="loader-sheet"><Blocks count={12} /></div>
              <Block className="is-button" />
            </Panel>
          </div>
        </>
      );

    case 'admin':
      return (
        <>
          <Block className="loader-admin-tabs" />
          <div className="loader-summary"><Blocks count={4} /></div>
          <Panel className="loader-admin-form">
            <Block className="is-heading" />
            <div className="loader-form-grid"><Blocks count={4} /></div>
            <Block className="is-button" />
          </Panel>
          <Panel className="loader-table"><Blocks count={5} /></Panel>
        </>
      );

    case 'profile':
      return (
        <>
          <Panel className="loader-profile-header">
            <div className="loader-identity">
              <Block className="is-label" />
              <Block className="is-title" />
              <Block className="is-text" />
            </div>
            <Block className="is-button" />
          </Panel>
          <Panel className="loader-organizations">
            <Block className="is-heading" />
            <div className="loader-strip"><Blocks count={3} /></div>
          </Panel>
          <Panel className="loader-preferences">
            <Block className="is-heading" />
            <Block className="is-input" />
          </Panel>
        </>
      );
  }
}
