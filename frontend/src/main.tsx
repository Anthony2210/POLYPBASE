import React from 'react';
import { createRoot } from 'react-dom/client';

function App() {
  return <main>Polypbase frontend will be built here.</main>;
}

const root = document.getElementById('root');

if (root) {
  createRoot(root).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}
