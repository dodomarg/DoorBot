/* DoorBot add-on UI. Relative URLs keep it working behind Home Assistant ingress. */
'use strict';

const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const state = { status: null, codes: [], keypad: null, pin: '', days: 127, credDays: 127, poll: null };

/* ------------------------------------------------------------------ http */
/* Ingress serves the app from a random prefix, so resolve API calls against the
   directory this page was loaded from rather than the server root. */
const BASE = new URL('.', window.location.href).href;

async function api(path, options = {}) {
  const res = await fetch(new URL(`api/${path}`, BASE), {
    headers: { 'Content-Type': 'application/json' },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  let data = {};
  try { data = await res.json(); } catch (_) { /* empty body */ }
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

let toastTimer;
function toast(message, bad = false) {
  const el = $('#toast');
  el.textContent = message;
  el.classList.toggle('bad', bad);
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.hidden = true; }, 3200);
}

async function run(fn, okMessage) {
  try {
    const result = await fn();
    if (okMessage) toast(okMessage);
    return result;
  } catch (err) {
    toast(err.message, true);
    return null;
  }
}

const MOVE_LABELS = {
  idle: 'Idle', moving: 'Moving…', arrived: 'Arrived',
  jammed: 'Jammed', timeout: 'Timed out', stalled: 'Found the end stop',
  aborted: 'Stopped', offline: 'No reply',
};

// In multi-turn mode a goal is no longer confined to one 0..4095 revolution,
// so the position inputs have to open up to match the firmware.
function applyTravelRange(multiTurn) {
  const lo = multiTurn ? -30719 : 0;
  const hi = multiTurn ? 30719 : 4095;
  const label = `${lo.toLocaleString()}&ndash;${hi.toLocaleString()}`;
  ['locked_position', 'unlocked_position', 'hold_position'].forEach((name) => {
    const field = document.getElementsByName(name)[0];
    if (!field) return;
    field.min = lo;
    field.max = hi;
  });
  const lockedRange = $('#lockedRange'); if (lockedRange) lockedRange.innerHTML = label;
  const unlockedRange = $('#unlockedRange'); if (unlockedRange) unlockedRange.innerHTML = label;
}

/* --------------------------------------------------------------- rendering */
function renderStatus(status) {
  if (!status) return;
  state.status = status;
  const servo = status.servo || {};
  const cal = status.calibration || {};

  const chip = $('#statechip');
  chip.textContent = status.state;
  chip.className = `statechip ${status.state}`;

  $('#posValue').textContent = servo.position ?? '—';
  $('#degValue').textContent = servo.degrees != null ? `${servo.degrees}°` : '';
  $('#mLoad').textContent = servo.load ?? '—';
  $('#mVolt').textContent = servo.voltage != null ? `${servo.voltage} V` : '—';
  $('#mTemp').textContent = servo.temperature != null ? `${servo.temperature} °C` : '—';
  $('#mTorque').textContent = servo.torque ? 'On' : 'Released';
  // "Torque on" only means the command was sent; "holding" is the servo
  // actually confirming it is powered and sitting on target.
  $('#mHolding').textContent = servo.torque ? (servo.holding ? 'Yes' : 'No') : '—';
  $('#mMove').textContent = MOVE_LABELS[servo.move_result] || '—';
  $('#turnsMetric').hidden = !cal.multi_turn;
  $('#mTurns').textContent = servo.turns != null ? servo.turns.toFixed(2) : '—';

  applyTravelRange(!!cal.multi_turn);

  // Dial: map the position between the two calibrated end points.
  const lo = Math.min(cal.locked_position, cal.unlocked_position);
  const hi = Math.max(cal.locked_position, cal.unlocked_position);
  const span = Math.max(1, hi - lo);
  const pct = Math.max(0, Math.min(1, ((servo.position ?? lo) - lo) / span));
  const circumference = 327;
  $('#dialProgress').style.strokeDashoffset = String(circumference * (1 - pct));
  $('#dialProgress').style.stroke =
    status.state === 'locked' ? 'var(--ok)'
      : status.state === 'jammed' ? 'var(--bad)'
        : status.state === 'unlocked' ? 'var(--warn)' : 'var(--brand)';
  $('#dialNeedle').style.transform = `rotate(${(servo.degrees ?? 0)}deg)`;

  $('#subtitle').textContent =
    `${status.backend === 'mock' ? 'Simulator' : 'ESPHome'} · ` +
    (servo.online === false ? 'servo offline · ' : '') +
    (status.calibrated ? 'calibrated' : 'not calibrated yet');

  // The two conditions that make every other reading on this page meaningless.
  const offline = servo.online === false;
  $('#offlineBanner').hidden = !offline;
  $('#mockBanner').hidden = status.backend !== 'mock';
  document.querySelectorAll('[data-needs-servo]').forEach((el) => {
    el.disabled = offline;
    el.title = offline ? 'The servo is not responding.' : '';
  });

  $('#pillLocked').textContent = `${cal.locked_position} (${(cal.locked_position * 360 / 4096).toFixed(0)}°)`;
  $('#pillLocked').classList.toggle('set', !!status.calibrated);
  $('#pillUnlocked').textContent = `${cal.unlocked_position} (${(cal.unlocked_position * 360 / 4096).toFixed(0)}°)`;
  $('#pillUnlocked').classList.toggle('set', !!status.calibrated);

  if (!$('#calForm').dataset.dirty) fillForm($('#calForm'), cal);
  if (!$('#holdForm').dataset.dirty) fillForm($('#holdForm'), cal);
  renderDirection(status);
  $('#devCard').hidden = status.backend !== 'mock';

  if (status.keypad) renderKeypad(status.keypad);
}

function renderDirection(status) {
  const dir = status.direction || {};
  const cal = status.calibration || {};
  const badge = $('#dirBadge');
  // Arrows are described by what the turn looks like at the thumbturn, which is
  // the only frame of reference the person calibrating actually has.
  const cw = '\u21bb clockwise';
  const ccw = '\u21ba counter-clockwise';
  const sign = dir.sign ?? 1;
  const invert = !!cal.invert;
  // A positive jog is clockwise at the servo; a mirrored mount reverses it.
  const positiveIsCw = !invert;
  $('#jogLegendLeft').textContent = `\u00ab ${positiveIsCw ? ccw : cw}`;
  $('#jogLegendRight').textContent = `${positiveIsCw ? cw : ccw} \u00bb`;

  if (!status.calibrated) {
    badge.textContent = 'Not calibrated yet';
    badge.classList.remove('bad');
    $('#dirDetail').textContent =
      'Capture the locked and unlocked positions and the direction appears here.';
  } else {
    badge.textContent = `Locking turns ${dir.locking === 'clockwise' ? cw : ccw}`;
    badge.classList.toggle('bad', dir.hold_valid === false);
    $('#dirDetail').textContent =
      `Locked ${cal.locked_position} \u2192 unlocked ${cal.unlocked_position}` +
      ` (${sign > 0 ? 'locking increases' : 'locking decreases'} the step count).` +
      (dir.hold_valid === false
        ? ' The hold-open position is on the locked side and would drive the bolt out.'
        : '');
  }
  $('#invertToggle').checked = invert;
}

function renderKeypad(kp) {
  state.keypad = kp;
  $('#kName').textContent = kp.keypad_name || (kp.paired ? 'Paired' : 'Not paired yet');
  $('#kBatt').textContent = kp.battery != null ? `${kp.battery}%` : '—';
  $('#kWho').textContent = kp.last_method
    ? describeCredential(kp.last_method, kp.last_slot, kp.credentials)
    : '—';
  $('#kResult').textContent = kp.last_result || '—';
  $('#kSeen').textContent = kp.last_seen ? new Date(kp.last_seen * 1000).toLocaleTimeString() : '—';
  if (!$('#keypadForm').dataset.dirty) fillForm($('#keypadForm'), kp.settings || {});
  renderCredentials(kp.credentials || []);
}

function describeCredential(method, slot, credentials) {
  const known = (credentials || []).find((c) => c.method === method && c.slot === slot);
  const label = known ? known.label : method;
  return known ? `${known.name} · ${label}` : `${label} slot ${slot}`;
}

function renderCredentials(creds) {
  const list = $('#credList');
  list.innerHTML = '';
  if (!creds.length) {
    list.innerHTML = '<p class="muted">No credentials named yet. Add one to see who unlocked the door.</p>';
    return;
  }
  creds.forEach((cred) => {
    const row = document.createElement('div');
    row.className = `codeitem ${cred.enabled ? '' : 'off'}`;
    const days = cred.days_mask === 127
      ? 'every day'
      : DAYS.filter((_, i) => (cred.days_mask >> i) & 1).join(' ') || 'never';
    const window = (cred.start_minute === 0 && cred.end_minute >= 1439)
      ? '' : ` · ${minutesToTime(cred.start_minute)}–${minutesToTime(cred.end_minute)}`;
    const tags = [];
    if (cred.duress) tags.push('<span class="tag duress">duress</span>');
    if (cred.notify) tags.push('<span class="tag recurring">notify</span>');
    row.innerHTML = `
      <div class="grow">
        <h4>${cred.icon} ${escapeHtml(cred.name)} ${tags.join(' ')}</h4>
        <div class="meta">${cred.label} · slot ${cred.slot} · ${days}${window}${cred.note ? ` · ${escapeHtml(cred.note)}` : ''}</div>
      </div>
      <button class="btn small" data-edit="${cred.key}">Edit</button>
      <button class="btn small danger ghost" data-del="${cred.key}">Delete</button>`;
    row.querySelector('[data-edit]').onclick = () => openCredModal(cred);
    row.querySelector('[data-del]').onclick = () => run(async () => {
      await api(`keypad/credentials/${encodeURIComponent(cred.key)}`, { method: 'DELETE' });
      await refreshStatus();
    }, 'Credential removed');
    list.appendChild(row);
  });
}

function openCredModal(cred) {
  const form = $('#credForm');
  form.reset();
  $('#credTitle').textContent = cred ? 'Edit credential' : 'Add credential';
  state.credDays = cred ? cred.days_mask : 127;
  if (cred) {
    fillForm(form, cred);
    form.elements.start_minute.value = minutesToTime(cred.start_minute);
    form.elements.end_minute.value = minutesToTime(cred.end_minute);
    // Method+slot are the identity, so editing one in place would orphan the old key.
    form.elements.method.disabled = true;
    form.elements.slot.readOnly = true;
  } else {
    form.elements.enabled.checked = true;
    form.elements.method.disabled = false;
    form.elements.slot.readOnly = false;
  }
  renderDayPicker('#credDays', 'credDays');
  $('#credModal').hidden = false;
}

function describeCode(code) {
  const bits = [];
  if (code.kind === 'one_time') bits.push('single use');
  if (code.kind === 'recurring') {
    bits.push(code.days.length === 7 ? 'every day' : code.days.join(' '));
    bits.push(`${minutesToTime(code.start_minute)}–${minutesToTime(code.end_minute)}`);
  }
  if (code.valid_to) bits.push(`until ${new Date(code.valid_to * 1000).toLocaleDateString()}`);
  if (code.max_uses) bits.push(`${code.use_count}/${code.max_uses} uses`);
  else if (code.use_count) bits.push(`used ${code.use_count}×`);
  return bits.join(' · ') || 'always valid';
}

function minutesToTime(mins) {
  const m = Math.min(1439, mins || 0);
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
}

function renderCodes(codes) {
  state.codes = codes;
  const list = $('#codeList');
  if (!codes.length) {
    list.innerHTML = '<p class="muted">No codes yet. Add one to get started.</p>';
    return;
  }
  list.innerHTML = '';
  codes.forEach((code) => {
    const el = document.createElement('div');
    el.className = `codeitem ${code.enabled ? '' : 'off'}`;
    el.innerHTML = `
      <div class="grow">
        <h4>${escapeHtml(code.name)} <span class="tag ${code.kind}">${code.kind.replace('_', ' ')}</span></h4>
        <div class="meta">${escapeHtml(code.code_hint)} · ${escapeHtml(describeCode(code))}</div>
      </div>
      <button class="btn small" data-edit="${code.id}">Edit</button>
      <button class="btn small" data-toggle="${code.id}">${code.enabled ? 'Disable' : 'Enable'}</button>
      <button class="btn small danger ghost" data-del="${code.id}">Delete</button>`;
    list.appendChild(el);
  });
}

function renderLog(events) {
  const list = $('#logList');
  if (!events.length) { list.innerHTML = '<p class="muted">Nothing logged yet.</p>'; return; }
  list.innerHTML = '';
  events.forEach((ev) => {
    const el = document.createElement('div');
    el.className = `logitem ${ev.kind}`;
    el.innerHTML = `<span class="dot"></span>
      <time>${new Date(ev.ts * 1000).toLocaleString()}</time>
      <span>${escapeHtml(ev.message)}${ev.actor ? ` <span class="meta">(${escapeHtml(ev.actor)})</span>` : ''}</span>`;
    list.appendChild(el);
  });
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/* --------------------------------------------------------------- forms */
function fillForm(form, values) {
  Object.entries(values).forEach(([key, value]) => {
    const field = form.elements[key];
    if (!field) return;
    if (field.type === 'checkbox') field.checked = !!value;
    else field.value = value ?? '';
  });
}

function readForm(form) {
  const out = {};
  Array.from(form.elements).forEach((field) => {
    if (!field.name) return;
    if (field.type === 'checkbox') out[field.name] = field.checked;
    else if (field.type === 'number') out[field.name] = field.value === '' ? null : Number(field.value);
    else out[field.name] = field.value;
  });
  return out;
}

/* ---------------------------------------------------------------- codes */
function openCodeModal(code) {
  const form = $('#codeForm');
  form.reset();
  $('#codeModalTitle').textContent = code ? 'Edit code' : 'Add code';
  state.days = code ? code.days_mask : 127;

  if (code) {
    fillForm(form, code);
    form.elements.code.value = '';
    form.elements.code.placeholder = 'Leave blank to keep the current PIN';
    form.elements.start_time.value = minutesToTime(code.start_minute);
    form.elements.end_time.value = minutesToTime(code.end_minute);
    ['valid_from', 'valid_to'].forEach((key) => {
      form.elements[key].value = code[key]
        ? new Date(code[key] * 1000).toISOString().slice(0, 16) : '';
    });
  } else {
    form.elements.id.value = '';
    form.elements.enabled.checked = true;
    form.elements.code.placeholder = '4–12 digits';
  }

  renderDays();
  syncKindVisibility();
  $('#codeModal').hidden = false;
}

function renderDays() {
  renderDayPicker('#daysPicker', 'days');
}

function renderDayPicker(selector, key) {
  const picker = $(selector);
  picker.innerHTML = '';
  DAYS.forEach((day, index) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `day ${(state[key] >> index) & 1 ? 'on' : ''}`;
    btn.textContent = day;
    btn.onclick = () => { state[key] ^= (1 << index); renderDayPicker(selector, key); };
    picker.appendChild(btn);
  });
}

function syncKindVisibility() {
  const kind = $('#codeForm').elements.kind.value;
  $$('#codeForm fieldset[data-when]').forEach((fs) => {
    fs.hidden = !fs.dataset.when.split(' ').includes(kind);
  });
  $('#codeForm').elements.max_uses.closest('label').hidden = kind === 'one_time';
}

function timeToMinutes(value) {
  const [h, m] = String(value || '00:00').split(':').map(Number);
  return (h || 0) * 60 + (m || 0);
}

/* ---------------------------------------------------------------- boot */
async function refreshStatus() {
  try { renderStatus(await api('status')); } catch (_) { /* keep last known */ }
}

async function refreshCodes() { renderCodes((await api('codes')).codes); }
async function refreshLog() { renderLog((await api('events?limit=80')).events); }

function bindEvents() {
  $$('.tab').forEach((tab) => {
    tab.onclick = () => {
      $$('.tab').forEach((t) => t.classList.remove('active'));
      $$('.panel').forEach((p) => p.classList.remove('active'));
      tab.classList.add('active');
      $(`#tab-${tab.dataset.tab}`).classList.add('active');
      if (tab.dataset.tab === 'log') refreshLog();
      if (tab.dataset.tab === 'codes') refreshCodes();
    };
  });

  $('#btnLock').onclick = () => run(async () => renderStatus(await api('lock', { method: 'POST' })), 'Locked');
  $('#btnUnlock').onclick = () => run(async () => renderStatus(await api('unlock', { method: 'POST' })), 'Unlocked');
  $('#btnStop').onclick = () => run(async () => renderStatus(await api('stop', { method: 'POST' })));
  $('#btnTestLock').onclick = $('#btnLock').onclick;
  $('#btnTestUnlock').onclick = $('#btnUnlock').onclick;

  $('#btnTorqueOff').onclick = () => run(async () =>
    renderStatus(await api('calibration/torque', { method: 'POST', body: { enabled: false } })), 'Servo released');
  $('#btnTorqueOn').onclick = () => run(async () =>
    renderStatus(await api('calibration/torque', { method: 'POST', body: { enabled: true } })), 'Holding position');

  $$('[data-jog]').forEach((btn) => {
    btn.onclick = () => run(async () =>
      renderStatus(await api('calibration/jog', { method: 'POST', body: { delta: Number(btn.dataset.jog) } })));
  });

  $$('[data-capture]').forEach((btn) => {
    btn.onclick = () => run(async () =>
      renderStatus(await api('calibration/capture', { method: 'POST', body: { which: btn.dataset.capture } })),
    `Captured the ${btn.dataset.capture} position`);
  });

  $('#btnResetCal').onclick = () => {
    if (!confirm('Reset calibration back to defaults?')) return;
    run(async () => renderStatus(await api('calibration/reset', { method: 'POST' })), 'Calibration reset');
  };

  const calForm = $('#calForm');
  calForm.oninput = () => { calForm.dataset.dirty = '1'; };
  calForm.onsubmit = (ev) => {
    ev.preventDefault();
    run(async () => {
      const status = await api('calibration', { method: 'POST', body: readForm(calForm) });
      delete calForm.dataset.dirty;
      renderStatus(status);
    }, 'Settings saved');
  };

  const holdForm = $('#holdForm');
  holdForm.oninput = () => { holdForm.dataset.dirty = '1'; };
  holdForm.onsubmit = (ev) => {
    ev.preventDefault();
    run(async () => {
      const status = await api('calibration', { method: 'POST', body: readForm(holdForm) });
      delete holdForm.dataset.dirty;
      renderStatus(status);
    }, 'Hold settings saved');
  };
  $('#btnTryHold').onclick = () =>
    run(async () => renderStatus(await api('open', { method: 'POST' })),
      'Unlocked and held the latch');

  $('#btnSwapDir').onclick = () =>
    run(async () => renderStatus(await api('calibration/swap', { method: 'POST' })),
      'Swapped the direction of rotation');

  $('#invertToggle').onchange = (ev) =>
    run(async () => {
      renderStatus(await api('calibration', {
        method: 'POST', body: { invert: ev.target.checked },
      }));
    }, 'Saved');

  $('#jamToggle').onchange = (ev) =>
    run(() => api('dev/jam', { method: 'POST', body: { enabled: ev.target.checked } }));

  $('#offlineToggle').onchange = (ev) =>
    run(async () => {
      await api('dev/offline', { method: 'POST', body: { enabled: ev.target.checked } });
      renderStatus(await api('status'));
    });

  // Virtual keypad
  $$('#virtualKeypad .key').forEach((key) => {
    key.onclick = () => {
      const k = key.dataset.k;
      if (k === 'clear') state.pin = state.pin.slice(0, -1);
      else if (k === 'enter') return submitPin();
      else if (state.pin.length < 12) state.pin += k;
      $('#pinDisplay').value = '•'.repeat(state.pin.length);
      return undefined;
    };
  });

  // Codes
  $('#btnNewCode').onclick = () => openCodeModal(null);
  $('#btnCancelCode').onclick = () => { $('#codeModal').hidden = true; };
  $('#codeForm').elements.kind.onchange = syncKindVisibility;
  $('#btnSuggest').onclick = () => run(async () => {
    $('#codeForm').elements.code.value = (await api('codes/suggest?length=6')).code;
  });

  $('#codeList').onclick = (ev) => {
    const btn = ev.target.closest('button');
    if (!btn) return;
    const { edit, del, toggle } = btn.dataset;
    if (edit) openCodeModal(state.codes.find((c) => String(c.id) === edit));
    if (del) {
      const code = state.codes.find((c) => String(c.id) === del);
      if (confirm(`Delete the code "${code.name}"?`)) {
        run(async () => { await api(`codes/${del}`, { method: 'DELETE' }); await refreshCodes(); }, 'Code deleted');
      }
    }
    if (toggle) {
      const code = state.codes.find((c) => String(c.id) === toggle);
      run(async () => {
        await api(`codes/${toggle}`, { method: 'PUT', body: { enabled: !code.enabled } });
        await refreshCodes();
      });
    }
  };

  $('#codeForm').onsubmit = (ev) => {
    ev.preventDefault();
    const form = ev.target;
    const body = readForm(form);
    body.days_mask = state.days;
    body.start_minute = timeToMinutes(form.elements.start_time.value);
    body.end_minute = timeToMinutes(form.elements.end_time.value);
    delete body.start_time; delete body.end_time;
    if (!body.code) delete body.code;
    const id = body.id;
    delete body.id;

    run(async () => {
      if (id) await api(`codes/${id}`, { method: 'PUT', body });
      else await api('codes', { method: 'POST', body });
      $('#codeModal').hidden = true;
      await refreshCodes();
    }, id ? 'Code updated' : 'Code added');
  };

  // Keypad
  const kpForm = $('#keypadForm');
  kpForm.oninput = () => { kpForm.dataset.dirty = '1'; };
  kpForm.onsubmit = (ev) => {
    ev.preventDefault();
    run(async () => {
      await api('keypad/settings', { method: 'POST', body: readForm(kpForm) });
      delete kpForm.dataset.dirty;
      await refreshStatus();
    }, 'Keypad settings saved');
  };

  const simulate = () => run(async () => {
    const result = await api('keypad/event', {
      method: 'POST',
      body: {
        method: $('#simMethod').value,
        slot: Number($('#simSlot').value || 0),
        keypad: state.keypad?.keypad_name || 'Simulated keypad',
        battery: state.keypad?.battery ?? 87,
      },
    });
    await refreshStatus();
    const who = result.name ? `${result.name} (${result.method_label})` : `${result.method_label} slot ${result.slot}`;
    if (result.result === 'accepted') {
      toast(`${who} accepted${result.acted ? ' → lock actuated' : ''}`);
    } else {
      toast(`${who}: ${result.reason || result.result}`, true);
    }
  });
  $('#btnSimUnlock').onclick = simulate;

  // Keypad credentials
  $('#btnAddCred').onclick = () => openCredModal(null);
  $('#btnCredCancel').onclick = () => { $('#credModal').hidden = true; };
  $('#credForm').onsubmit = (ev) => {
    ev.preventDefault();
    const form = $('#credForm');
    const body = readForm(form);
    // Method is disabled while editing (it is half the identity), so read it
    // explicitly rather than relying on how disabled fields are serialised.
    body.method = form.elements.method.value;
    body.days_mask = state.credDays;
    run(async () => {
      await api('keypad/credentials', { method: 'POST', body });
      $('#credModal').hidden = true;
      await refreshStatus();
    }, 'Credential saved');
  };

  $('#btnRefreshLog').onclick = () => run(refreshLog);

  document.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    if ($('#codeModal').hidden === false) $('#codeModal').hidden = true;
    if ($('#credModal').hidden === false) $('#credModal').hidden = true;
  });
}

async function submitPin() {
  if (state.pin.length < 4) { toast('PINs are at least 4 digits.', true); return; }
  const box = $('#pinResult');
  try {
    const result = await api('verify', { method: 'POST', body: { code: state.pin, source: 'web-ui' } });
    box.hidden = false;
    if (result.allowed) {
      box.className = 'result ok';
      box.textContent = `Accepted — welcome, ${result.name}.${result.duress ? ' (duress code!)' : ''}`;
      renderStatus(result.status);
    } else {
      box.className = 'result bad';
      box.textContent = `Rejected (${result.reason.replace(/_/g, ' ')}).`;
    }
  } catch (err) {
    box.hidden = false;
    box.className = 'result bad';
    box.textContent = err.message;
  }
  state.pin = '';
  $('#pinDisplay').value = '';
  refreshCodes().catch(() => {});
}

async function init() {
  bindEvents();
  await refreshStatus();
  await run(refreshCodes);
  await run(refreshLog);
  state.poll = setInterval(refreshStatus, 2000);
}

init();
