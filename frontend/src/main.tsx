import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { LiquiThemeProvider } from '@liqui-design/glass';
import App from './App';
import { i18n } from './i18n';
import { duetLiquiTheme } from './lib/liquiPresets';
import './Duet.css';
import './liquidGlass.css';

document.documentElement.lang = i18n.locale;

const root = document.getElementById('root');
if (!root) throw new Error('Missing root element');

createRoot(root).render(
  <StrictMode>
    <LiquiThemeProvider theme={duetLiquiTheme}>
      <App />
    </LiquiThemeProvider>
  </StrictMode>,
);
