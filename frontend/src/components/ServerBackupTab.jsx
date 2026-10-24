import React, { useEffect, useState } from 'react';
import { Copy, KeyRound, Play, RefreshCw, Save, ShieldCheck } from 'lucide-react';
import { t } from '../i18n.js';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function fmtSize(bytes) {
  const n = Number(bytes || 0);
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/* The panel's own disaster-recovery backup: passphrase escrow, schedule,
 * destinations, runs. */
function PanelDrCard({ request, setNotice, loading, targets }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [shownPassphrase, setShownPassphrase] = useState('');
  const [typed, setTyped] = useState('');

  async function load() {
    const res = await request('/maintenance/panel-dr', { silent: true }, null);
    if (res) {
      setData(res);
      setForm({
        enabled: res.enabled,
        window: res.window,
        weekdays: res.weekdays,
        target_ids: res.target_ids,
        keep_remote: res.keep_remote,
      });
    }
  }

  useEffect(() => {
    load();
  }, []);

  if (!data || !form) return null;
  const key = data.key || {};
  const serverTargets = (targets || []).filter(tg => tg.owner_id == null);

  async function createPassphrase(rotate) {
    if (
      rotate &&
      !window.confirm(
        t('Rotate the passphrase? Archives made before now still need the OLD passphrase; keep both copies until those archives have expired.')
      )
    ) {
      return;
    }
    const res = await request(
      '/maintenance/panel-dr/passphrase',
      { method: 'POST', body: JSON.stringify({ rotate: !!rotate }) },
      t('Creating passphrase...')
    );
    if (res?.passphrase) {
      setShownPassphrase(res.passphrase);
      setTyped('');
      await load();
    }
  }

  async function confirmPassphrase(e) {
    e.preventDefault();
    const res = await request(
      '/maintenance/panel-dr/passphrase/confirm',
      { method: 'POST', body: JSON.stringify({ passphrase: typed }) },
      t('Checking...')
    );
    if (res) {
      setShownPassphrase('');
      setTyped('');
      setNotice(t('Passphrase confirmed. The panel self-backup can run now.'));
      await load();
    }
  }

  async function save(e) {
    e.preventDefault();
    const res = await request('/maintenance/panel-dr', { method: 'PUT', body: JSON.stringify(form) }, t('Saving...'));
    if (res) {
      setData(res);
      setNotice(t('Panel self-backup settings saved.'));
    }
  }

  async function runNow() {
    const res = await request('/maintenance/panel-dr/run', { method: 'POST' }, t('Starting panel self-backup...'));
    if (res) setNotice(res.detail || t('Started.'));
  }

  const toggle = (field, value) =>
    setForm(prev => ({
      ...prev,
      [field]: prev[field].includes(value) ? prev[field].filter(v => v !== value) : [...prev[field], value],
    }));
  const last = data.last_run || {};

  return (
    <div className="card" style={{ padding: '18px', marginBottom: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
        <ShieldCheck size={17} />
        <h3 style={{ margin: 0 }}>{t('Panel self-backup (disaster recovery)')}</h3>
        <span className={`badge ${key.confirmed ? 'ok' : 'warn'}`}>
          {key.confirmed ? t('Passphrase confirmed') : key.initialised ? t('Passphrase not confirmed') : t('No passphrase yet')}
        </span>
      </div>
      <p className="hint">
        {t('An encrypted archive of the panel itself: its database, .env (SECRET_KEY), /etc/spanel, certificates, DNS zones, DKIM keys and the nginx/PHP/mail configuration it wrote. With it and the account backups, a lost server is rebuilt with: spanel dr-restore <archive>, then Backups > Restore for every account.')}
      </p>

      {shownPassphrase && (
        <div style={{ border: '2px solid var(--warning, #f59e0b)', borderRadius: '10px', padding: '12px', marginBottom: '12px' }}>
          <strong>{t('Store this passphrase outside this server now. It is not shown again.')}</strong>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', margin: '8px 0' }}>
            <code style={{ fontSize: '1rem', userSelect: 'all' }}>{shownPassphrase}</code>
            <button type="button" className="secondary-light" onClick={() => navigator.clipboard?.writeText(shownPassphrase)}>
              <Copy size={13} /> {t('Copy')}
            </button>
          </div>
          <span className="hint">{t('Put it in your password manager or print it. Then type it below to prove the copy works.')}</span>
        </div>
      )}

      {!key.initialised && !shownPassphrase && (
        <button type="button" onClick={() => createPassphrase(false)} disabled={!!loading}>
          <KeyRound size={14} /> {t('Create passphrase')}
        </button>
      )}

      {key.initialised && !key.confirmed && (
        <form onSubmit={confirmPassphrase} style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
          <input
            type="password"
            autoComplete="off"
            value={typed}
            onChange={e => setTyped(e.target.value)}
            placeholder={t('Type the passphrase from your stored copy')}
            style={{ minWidth: '320px' }}
            required
          />
          <button type="submit" disabled={!!loading}>{t('Confirm passphrase')}</button>
        </form>
      )}

      {key.initialised && (
        <div className="hint" style={{ marginBottom: '12px' }}>
          {t('Fingerprint')}: <code>{key.fingerprint}</code>{' '}
          <button type="button" className="secondary-light" style={{ fontSize: '0.75rem' }} onClick={() => createPassphrase(true)}>
            <RefreshCw size={12} /> {t('Rotate passphrase')}
          </button>
        </div>
      )}

      <form onSubmit={save} style={{ display: 'grid', gap: '10px' }}>
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input type="checkbox" checked={form.enabled} onChange={e => setForm(p => ({ ...p, enabled: e.target.checked }))} />
          {t('Run daily in the maintenance window at')}
          <input type="time" value={form.window} onChange={e => setForm(p => ({ ...p, window: e.target.value }))} />
        </label>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
          <span>{t('Days (none = every day)')}:</span>
          {WEEKDAYS.map((day, idx) => (
            <label key={day} style={{ display: 'flex', gap: '4px' }}>
              <input type="checkbox" checked={form.weekdays.includes(idx)} onChange={() => toggle('weekdays', idx)} />
              {t(day)}
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          <span>{t('Send to')}:</span>
          {serverTargets.length === 0 && <span className="hint">{t('Add a backup destination first (Restore & Destinations).')}</span>}
          {serverTargets.map(tg => (
            <label key={tg.id} style={{ display: 'flex', gap: '4px' }}>
              <input type="checkbox" checked={form.target_ids.includes(tg.id)} onChange={() => toggle('target_ids', tg.id)} />
              [{String(tg.kind || 'sftp').toUpperCase()}] {tg.name}
            </label>
          ))}
        </div>
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {t('Archives to keep on each destination')}
          <input type="number" min={1} max={365} style={{ width: '80px' }} value={form.keep_remote}
            onChange={e => setForm(p => ({ ...p, keep_remote: Number(e.target.value) }))} />
        </label>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button type="submit" disabled={!!loading}><Save size={14} /> {t('Save')}</button>
          <button type="button" className="secondary-light" disabled={!!loading || !key.confirmed} onClick={runNow}>
            <Play size={14} /> {t('Run now')}
          </button>
        </div>
      </form>

      {last.started_at && (
        <div className="hint" style={{ marginTop: '12px' }}>
          {t('Last run')}: {last.started_at} — <strong>{t(last.status || '')}</strong> {last.archive}
          {(last.errors || []).length > 0 && <div style={{ color: 'var(--danger, #dc2626)' }}>{last.errors.join('; ')}</div>}
        </div>
      )}
      {(data.local || []).length > 0 && (
        <div style={{ marginTop: '10px', fontSize: '0.8rem' }}>
          <strong>{t('Copies on this server')}</strong>
          {data.local.map(row => (
            <div key={row.name}>
              <code>{row.name}</code> · {fmtSize(row.size)} · {row.created_at} · sha256 {String(row.sha256).slice(0, 12)}…
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function ServerBackupTab({ request, setNotice, loading, targets }) {
  return (
    <div>
      <PanelDrCard request={request} setNotice={setNotice} loading={loading} targets={targets} />
    </div>
  );
}
