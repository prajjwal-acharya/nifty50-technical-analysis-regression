const $ = (id) => document.getElementById(id);
const currency = (value) => value == null ? '—' : '₹' + Number(value).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
const shortDate = (value) => value ? new Date(`${value}T12:00:00+05:30`).toLocaleDateString('en-IN', {day: '2-digit', month: 'short', year: 'numeric'}).toUpperCase() : '—';
const issueTime = (value) => value ? new Date(value).toLocaleString('en-IN', {timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hour12: true}) : '—';
let snapshot = null;
let horizon = '1';
let selected = 'RELIANCE';

function notice(message) {
  $('notice').textContent = message;
  $('notice').hidden = !message;
}

function validate(payload) {
  if (!payload || payload.schema_version !== 1 || !Array.isArray(payload.stocks)) throw new Error('Unsupported snapshot format');
  if (payload.status === 'unavailable') return payload;
  if (!['current', 'resolved_or_stale'].includes(payload.status)) throw new Error('Unknown snapshot status');
  if (payload.stocks.length !== 50 || new Set(payload.stocks.map(s => s.symbol)).size !== 50) throw new Error('Snapshot must cover 50 unique stocks');
  for (const stock of payload.stocks) {
    if (!stock.horizons?.['1'] || !stock.horizons?.['5'] || !Number.isFinite(stock.origin_close)) throw new Error('Incomplete stock result');
    for (const key of ['1', '5']) {
      const item = stock.horizons[key];
      if (!item.target_session || !['issued', 'abstained_known_corporate_action'].includes(item.status)) throw new Error('Invalid horizon result');
      if (item.status === 'issued' && !Number.isFinite(item.forecast_close)) throw new Error('Missing forecast value');
    }
  }
  return payload;
}

function selectedHorizon(stock) { return stock.horizons[horizon]; }

function renderSelected() {
  if (!snapshot?.stocks.length) return;
  const stock = snapshot.stocks.find(s => s.symbol === selected) || snapshot.stocks[0];
  selected = stock.symbol;
  const item = selectedHorizon(stock);
  const issued = item.status === 'issued';
  $('detail-title').textContent = `${stock.symbol} ↗`;
  $('symbol').textContent = stock.symbol;
  $('detail-target').textContent = shortDate(item.target_session);
  $('origin-close').textContent = currency(stock.origin_close);
  $('forecast-close').textContent = issued ? currency(item.forecast_close) : 'ABSTAINED';
  $('forecast-caption').textContent = issued ? 'target session estimate' : 'known corporate action';
  $('policy').textContent = horizon === '1' ? 'FROZEN MODEL' : 'UNCHANGED CLOSE';
  let change = issued ? (item.forecast_close / stock.origin_close - 1) * 100 : null;
  $('change').textContent = change == null ? '—' : `${change > 0 ? '+' : ''}${change.toFixed(3)}%`;
  $('change').className = change == null || change === 0 ? 'neutral' : change > 0 ? 'positive' : 'negative';
  $('actual-close').textContent = currency(item.actual_close);
  $('absolute-error').textContent = currency(item.absolute_error);
  $('outcome-heading').textContent = item.actual_close == null ? 'Awaiting target close' : 'Target close verified';
  $('outcome-copy').textContent = item.actual_close == null
    ? 'The actual quoted close is unknown until this NSE session is verified.'
    : issued ? 'The forecast is frozen. The actual close and rupee error are shown separately.'
      : 'This horizon abstained because a known corporate action affected its price basis.';
  $('detail-note').textContent = horizon === '1'
    ? 'The frozen model was not refit for this session. Its estimate was issued before the next target opened.'
    : 'The adopted fifth-session policy is the unchanged origin close; no fitted fifth-day model is presented as adopted.';
  for (const row of document.querySelectorAll('#stock-rows tr')) {
    const active = row.dataset.symbol === selected;
    row.classList.toggle('selected', active);
    row.setAttribute('aria-selected', String(active));
  }
}

function renderTable() {
  if (!snapshot?.stocks.length) return;
  const query = $('search').value.trim().toUpperCase();
  const rows = snapshot.stocks.filter(s => s.symbol.includes(query));
  const tbody = $('stock-rows');
  tbody.replaceChildren();
  rows.forEach((stock, index) => {
    const item = selectedHorizon(stock);
    const row = document.createElement('tr');
    row.tabIndex = 0;
    row.dataset.symbol = stock.symbol;
    const status = item.status === 'issued' ? item.actual_close == null ? 'AWAITING CLOSE' : 'ACTUAL VERIFIED' : 'ABSTAINED';
    const cells = [String(index + 1).padStart(2, '0'), stock.symbol, currency(stock.origin_close),
      currency(item.forecast_close), currency(item.actual_close), status];
    cells.forEach((value, i) => {
      const cell = document.createElement('td');
      cell.textContent = value;
      if (i >= 2 && i <= 4) cell.classList.add('numeric');
      row.append(cell);
    });
    row.addEventListener('click', () => {selected = stock.symbol; renderSelected(); document.querySelector('.detail').scrollIntoView({behavior: 'smooth', block: 'start'});});
    row.addEventListener('keydown', event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault(); row.click();}});
    tbody.append(row);
  });
  $('empty').hidden = rows.length > 0;
  if (!rows.length) $('empty').textContent = 'No symbols match your search.';
  $('table-count').textContent = `${rows.length} OF ${snapshot.stocks.length} STOCKS SHOWN`;
  renderSelected();
}

function render() {
  const one = snapshot.stocks[0].horizons[horizon];
  $('origin-hero').textContent = shortDate(snapshot.origin_session);
  $('publication-status').textContent = snapshot.status === 'current' ? 'Prospective forecast on file' : 'Historical forecast · target may be resolved';
  $('coverage').textContent = '50';
  $('target-date').textContent = shortDate(one.target_session);
  $('target-caption').textContent = horizon === '1' ? 'next NSE session' : 'fifth NSE session';
  $('latest-date').textContent = shortDate(snapshot.latest_verified_session);
  $('issued-at').textContent = issueTime(snapshot.issued_at_utc);
  $('source-label').textContent = snapshot.source_label;
  $('basket-date').textContent = shortDate(snapshot.basket_as_of);
  $('export').disabled = false;
  $('horizon-1').setAttribute('aria-pressed', String(horizon === '1'));
  $('horizon-5').setAttribute('aria-pressed', String(horizon === '5'));
  notice(snapshot.status === 'current' ? '' : 'This is an earlier issued forecast. Check the target and latest verified dates before interpreting it as current.');
  renderTable();
}

async function load() {
  try {
    const configResponse = await fetch('./config.json', {cache: 'no-store'});
    if (!configResponse.ok) throw new Error('Dashboard configuration is unavailable');
    const config = await configResponse.json();
    if (typeof config.snapshot_url !== 'string' || !config.snapshot_url) throw new Error('Snapshot URL is missing');
    const response = await fetch(config.snapshot_url, {cache: 'no-store'});
    if (!response.ok) throw new Error(`Snapshot endpoint returned ${response.status}`);
    const payload = validate(await response.json());
    if (payload.status === 'unavailable') {
      $('publication-status').textContent = 'PUBLIC DATA PENDING';
      $('latest-date').textContent = shortDate(payload.latest_verified_session);
      $('empty').textContent = 'Forecast results are not publicly available yet.';
      notice(payload.reason || 'No public snapshot is available.');
      return;
    }
    snapshot = payload;
    render();
  } catch (error) {
    $('publication-status').textContent = 'SNAPSHOT UNAVAILABLE';
    notice(`Could not load a verified snapshot: ${error.message}`);
  }
}

function exportCsv() {
  if (!snapshot) return;
  const rows = [['origin_session', 'target_session', 'symbol', 'horizon', 'origin_close_inr',
    'forecast_close_inr', 'actual_close_inr', 'absolute_error_inr', 'status', 'policy']];
  for (const stock of snapshot.stocks) {
    const item = selectedHorizon(stock);
    rows.push([snapshot.origin_session, item.target_session, stock.symbol, horizon, stock.origin_close,
      item.forecast_close ?? '', item.actual_close ?? '', item.absolute_error ?? '', item.status, item.policy]);
  }
  const csv = rows.map(row => row.map(value => `"${String(value).replaceAll('"', '""')}"`).join(',')).join('\r\n');
  const url = URL.createObjectURL(new Blob([csv], {type: 'text/csv'}));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = `nifty50-${snapshot.origin_session}-h${horizon}.csv`;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('horizon-1').addEventListener('click', () => {horizon = '1'; if (snapshot) render();});
$('horizon-5').addEventListener('click', () => {horizon = '5'; if (snapshot) render();});
$('search').addEventListener('input', renderTable);
$('export').addEventListener('click', exportCsv);
load();
