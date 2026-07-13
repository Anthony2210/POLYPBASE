import { Component, type ErrorInfo, type ReactNode } from 'react';

/**
 * Catches render errors so a single broken component never leaves the user in
 * front of a blank page (which is exactly what happened when a referenced
 * variable was deleted: every box page crashed silently).
 *
 * It sits above <App/>, so it cannot read the user's language preference from
 * the profile: we fall back to the browser language.
 */

type Props = {
  children: ReactNode;
};

type State = {
  error: Error | null;
};

const copy = {
  fr: {
    title: 'Une erreur est survenue',
    text: "L'application a rencontré un problème inattendu. Vos données enregistrées ne sont pas perdues.",
    reload: 'Recharger la page',
    details: 'Détails techniques',
  },
  en: {
    title: 'Something went wrong',
    text: 'The application hit an unexpected problem. Your saved data is safe.',
    reload: 'Reload the page',
    details: 'Technical details',
  },
};

function getLanguage(): keyof typeof copy {
  return navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'fr';
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // Keep the stack in the console so the crash is diagnosable in the field.
    console.error('Unhandled UI error:', error, info.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    const labels = copy[getLanguage()];

    return (
      <main className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <h1>{labels.title}</h1>
          <p>{labels.text}</p>
          <button type="button" onClick={() => window.location.reload()}>
            {labels.reload}
          </button>
          <details>
            <summary>{labels.details}</summary>
            <pre>{error.message}</pre>
          </details>
        </div>
      </main>
    );
  }
}
