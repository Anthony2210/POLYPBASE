import React from 'react';
import { createRoot } from 'react-dom/client';

import App from './App';
import ErrorBoundary from './components/ErrorBoundary';
import './styles/index.css';

const root = document.getElementById('root');

if (root) {
  createRoot(root).render(
    <React.StrictMode>
      <ErrorBoundary>
        <App />
      </ErrorBoundary>
    </React.StrictMode>,
  );
}
