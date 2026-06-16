import { getBoxStatusPresentation } from '../boxStatus';
import type { BoxLineage, LineageGraph } from '../types';
import InteractiveLineageGraph from './InteractiveLineageGraph';

type Language = 'fr' | 'en';

type Props = {
  lineage: BoxLineage;
  graph: LineageGraph | null;
  isGraphLoading: boolean;
  graphError: string | null;
  language: Language;
  onClose: () => void;
  onSelectBox: (boxId: number, globalCode: string) => void;
};

const labels = {
  fr: {
    title: 'Parenté de la boîte',
    close: 'Fermer',
    parents: 'Boîtes parentes',
    children: 'Boîtes enfants',
    noParents: 'Aucune boîte parente enregistrée.',
    noChildren: 'Aucune boîte enfant enregistrée.',
    historicalLink: 'Lien historique',
    by: 'par',
    noReason: 'Aucun motif renseigné',
    openBox: 'Ouvrir la boîte',
    loadingGraph: 'Chargement de la famille...',
  },
  en: {
    title: 'Box lineage',
    close: 'Close',
    parents: 'Parent boxes',
    children: 'Child boxes',
    noParents: 'No parent box recorded.',
    noChildren: 'No child box recorded.',
    historicalLink: 'Historical link',
    by: 'by',
    noReason: 'No reason provided',
    openBox: 'Open box',
    loadingGraph: 'Loading family...',
  },
};

export default function LineageModal({
  lineage,
  graph,
  isGraphLoading,
  graphError,
  language,
  onClose,
  onSelectBox,
}: Props) {
  const text = labels[language];

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <section
        className="lineage-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="lineage-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-heading">
          <div>
            <h2 id="lineage-title">{text.title}</h2>
            <span>{lineage.parents.length + lineage.children.length}</span>
          </div>
          <button type="button" onClick={onClose}>{text.close}</button>
        </div>

        <div className="lineage-modal-body">
          {isGraphLoading ? <p className="lineage-graph-status">{text.loadingGraph}</p> : null}
          {graphError ? <p className="inline-error lineage-graph-status">{graphError}</p> : null}
          {graph ? (
            <InteractiveLineageGraph
              graph={graph}
              language={language}
              onSelectBox={onSelectBox}
            />
          ) : null}

          <div className="lineage-columns">
            <LineageGroup
              title={text.parents}
              emptyMessage={text.noParents}
              relations={lineage.parents}
              language={language}
              labels={text}
              onSelectBox={onSelectBox}
            />
            <LineageGroup
              title={text.children}
              emptyMessage={text.noChildren}
              relations={lineage.children}
              language={language}
              labels={text}
              onSelectBox={onSelectBox}
            />
          </div>
        </div>
      </section>
    </div>
  );
}

function LineageGroup({
  title,
  emptyMessage,
  relations,
  language,
  labels: text,
  onSelectBox,
}: {
  title: string;
  emptyMessage: string;
  relations: BoxLineage['parents'];
  language: Language;
  labels: typeof labels.fr;
  onSelectBox: (boxId: number, globalCode: string) => void;
}) {
  return (
    <section className="lineage-group">
      <header>
        <h3>{title}</h3>
        <span>{relations.length}</span>
      </header>

      {!relations.length ? <p className="lineage-empty">{emptyMessage}</p> : null}

      {relations.map((relation) => {
        const status = getBoxStatusPresentation(relation.box.status, language);

        return (
          <article className="lineage-relation" key={relation.id}>
            <button
              className="lineage-box-link"
              type="button"
              onClick={() => onSelectBox(relation.box.id, relation.box.global_code)}
            >
              <span>
                <span className="lineage-box-title">
                  <strong>{relation.box.global_code}</strong>
                  <span className={`box-life-status is-${status.tone}`}>
                    {status.label}
                  </span>
                </span>
                <small>{relation.box.species_name}</small>
              </span>
              <span aria-hidden="true">→</span>
            </button>

            <div className="lineage-event">
              <strong>
                {relation.event
                  ? formatEventDate(relation.event.event_date, language)
                  : text.historicalLink}
              </strong>
              {relation.event?.user ? <small>{text.by} {relation.event.user}</small> : null}
              <p>{relation.event?.reason || text.noReason}</p>
              {relation.event?.notes ? <p className="lineage-note">{relation.event.notes}</p> : null}
            </div>

            <span className="sr-only">{text.openBox}</span>
          </article>
        );
      })}
    </section>
  );
}

function formatEventDate(value: string, language: Language) {
  return new Intl.DateTimeFormat(language === 'fr' ? 'fr-FR' : 'en-GB', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(new Date(`${value}T00:00:00Z`));
}
