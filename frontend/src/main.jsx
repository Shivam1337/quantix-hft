import { render } from 'preact';
import { App } from './components/app.jsx';

const root = document.getElementById('app');
if (root) render(<App />, root);
