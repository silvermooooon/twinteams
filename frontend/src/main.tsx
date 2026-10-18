import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { i18n } from './i18n';

document.documentElement.lang = i18n.locale;

const root = document.getElementById('root');
if (!root) throw new Error('Missing root element');

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
