type PageLoaderVariant = 'pilotage' | 'box' | 'zones' | 'zone' | 'exports' | 'admin' | 'profile';

export default function PageLoader({
  label = 'Chargement',
  variant,
}: {
  label?: string;
  variant: PageLoaderVariant;
}) {
  return (
    <section
      className={`page-loader page-loader-${variant}`}
      aria-busy="true"
      aria-live="polite"
    >
      <span className="sr-only">{label}</span>
      {renderLoaderContent(variant)}
    </section>
  );
}

function renderLoaderContent(variant: PageLoaderVariant) {
  switch (variant) {
    case 'pilotage':
      return (
        <>
          <div className="page-loader-search">
            <span />
            <strong />
          </div>
          <div className="page-loader-strip">
            {Array.from({ length: 5 }, (_, index) => <span key={index} />)}
          </div>
          <div className="page-loader-scan" aria-hidden="true">
            <span />
          </div>
        </>
      );
    case 'box':
      return (
        <>
          <div className="page-loader-hero">
            <div>
              <strong />
              <span />
              <span />
            </div>
            <div className="page-loader-qr" />
          </div>
          <div className="page-loader-body">
            <div className="page-loader-form">
              <span />
              <strong />
              <strong />
              <button aria-hidden="true" type="button" />
            </div>
            <div className="page-loader-side">
              <span />
              <strong />
              <strong />
            </div>
          </div>
        </>
      );
    case 'zones':
      return (
        <>
          <div className="page-loader-summary" />
          <div className="page-loader-grid">
            {Array.from({ length: 5 }, (_, index) => <span key={index} />)}
          </div>
        </>
      );
    case 'zone':
      return (
        <>
          <div className="page-loader-hero is-zone">
            <div>
              <strong />
              <span />
            </div>
            <div className="page-loader-metrics">
              <span />
              <span />
              <span />
            </div>
          </div>
          <div className="page-loader-thermal">
            <span />
            <strong />
          </div>
          <div className="page-loader-grid is-wide">
            {Array.from({ length: 4 }, (_, index) => <span key={index} />)}
          </div>
        </>
      );
    case 'exports':
      return (
        <>
          <div className="page-loader-export-head">
            <span />
            <span />
          </div>
          <div className="page-loader-chart">
            <span />
            <span />
            <span />
          </div>
        </>
      );
    case 'admin':
      return (
        <>
          <div className="page-loader-admin-table">
            {Array.from({ length: 4 }, (_, index) => <span key={index} />)}
          </div>
          <div className="page-loader-grid">
            {Array.from({ length: 2 }, (_, index) => <span key={index} />)}
          </div>
        </>
      );
    case 'profile':
      return (
        <>
          <div className="page-loader-profile">
            <span />
            <div>
              <strong />
              <small />
            </div>
          </div>
          <div className="page-loader-grid">
            {Array.from({ length: 2 }, (_, index) => <span key={index} />)}
          </div>
        </>
      );
  }
}
