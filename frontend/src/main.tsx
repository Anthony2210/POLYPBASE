import React from 'react';
import { createRoot } from 'react-dom/client';
import '@xyflow/react/dist/style.css';

import App from './App';
import './styles/app.css';

const root = document.getElementById('root');

if (root) {
  createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
