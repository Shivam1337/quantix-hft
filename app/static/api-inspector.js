import { copyText, setText } from './ui-utils.js';

export async function queryApi(endpoint, title) {
  const container = document.getElementById('json-preview-container');
  const titleElement = document.getElementById('json-preview-title');
  const content = document.getElementById('json-preview-content');
  const status = document.getElementById('api-status-badge');
  const latency = document.getElementById('api-latency-badge');
  setText(titleElement, `ENDPOINT: ${title || endpoint} (${endpoint})`);
  setText(content, 'Fetching live data...');
  if (status) { status.textContent = 'FETCHING...'; status.className = 'badge badge-info mono'; }
  if (container) container.style.display = 'block';
  const started = performance.now();
  try {
    const response = await fetch(endpoint);
    const json = await response.json();
    setText(content, JSON.stringify(json, null, 2));
    if (status) { status.textContent = `${response.status} ${response.statusText || 'OK'}`; status.className = response.ok ? 'badge badge-success mono' : 'badge badge-danger mono'; }
  } catch (error) {
    setText(content, `Error querying ${endpoint}: ${error}`);
    if (status) { status.textContent = 'ERROR'; status.className = 'badge badge-danger mono'; }
  } finally {
    setText(latency, `${Math.round(performance.now() - started)} ms`);
  }
}

export function selectAndQueryApi(endpoint, title) {
  const input = document.getElementById('api-custom-endpoint');
  if (input) input.value = endpoint;
  queryApi(endpoint, title);
}

export function sendCustomApiQuery() {
  const input = document.getElementById('api-custom-endpoint');
  queryApi(input?.value.trim() || '/api/market/prices', 'Custom Request');
}

export function clearApiResponse() {
  setText('json-preview-content', 'Response cleared. Select an endpoint or click "Send Request".');
  setText('json-preview-title', 'QUERY RESULT');
  const status = document.getElementById('api-status-badge');
  if (status) { status.textContent = 'IDLE'; status.className = 'badge badge-info mono'; }
  setText('api-latency-badge', '-- ms');
}

export function copyApiResponse(button) {
  copyText(document.getElementById('json-preview-content')?.innerText, button);
}

export function closePreview() {
  const container = document.getElementById('json-preview-container');
  if (container) container.style.display = 'none';
}

export function initialiseApiInspector() {
  const content = document.getElementById('json-preview-content');
  if (content && (content.innerText.startsWith('Click any endpoint') || !content.innerText.trim())) {
    selectAndQueryApi('/api/market/prices', 'Rapid 6-Way Prices');
  }
}

export function exposeInspectorActions() {
  Object.assign(window, {
    clearApiResponse,
    closePreview,
    copyApiResponse,
    selectAndQueryApi,
    sendCustomApiQuery,
  });
}
