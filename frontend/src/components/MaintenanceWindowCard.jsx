import React, { useEffect, useState } from 'react';
import { Clock, Save } from 'lucide-react';
import { t } from '../i18n.js';

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

function statusBadge(status) {
  if (!status) return <span className="badge">{t('Never run')}</span>;
  const cls = status === 'ok' ? 'ok' : status === 'skipped' ? 'ok' : status === 'running' ? 'warn' : 'warn';
  return <span className={`badge ${cls}`}>{t(status)}</span>;
}

/* The maintenance window: when panel auto-update, PHP auto-patch, core stack
 * patching, the panel self-backup and backup verification run. The schedule
 * runs from spanel-maintenance.timer on the server, not from this page. */
export function MaintenanceWindowCard({ request, setNotice, loading }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);

  async function load() {
    const res = await request('/updates/maintenance-window', { silent: true }, null);
    if (res) {
      setData(res);
      setForm({ window_minutes: res.config.window_minutes, core_stack: { ...res.config.core_stack } });
    }
  }

  useEffect(() => {
    load();
  }, []);

  if (!data || !form) return null;

  const core = form.core_stack;
  const setCore = patch => setForm(prev => ({ ...prev, core_stack: { ...prev.core_stack, ...patch } }));
  const togglePkg = pkg =>
    setCore({ packages: core.packages.includes(pkg) ? core.packages.filter(p => p !== pkg) : [...core.packages, pkg] });
  const toggleDay = day =>
    setCore({ weekdays: core.weekdays.includes(day) ? core.weekdays.filter(d => d !== day) : [...core.weekdays, day] });

  async function save(e) {
    e.preventDefault();
    setSaving(true);
    const res = await request(
      '/updates/maintenance-window',
      { method: 'PUT', body: JSON.stringify(form) },
      t('Saving maintenance window...')
    );
    setSaving(false);
    if (res) {
      setData(res);
      setForm({ window_minutes: res.config.window_minutes, core_stack: { ...res.config.core_stack } });
      setNotice(
        res.blacklist_applied === false
          ? t('Saved, but the unattended-upgrades blacklist could not be written. Check the helper log.')
          : t('Maintenance window saved.')
      );
    }
  }

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: '14px', background: 'var(--surface)', marginBottom: '16px', overflow: 'hidden' }}>
      <div style={{ background: 'var(--surface-alt)', padding: '15px 20px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
          <Clock size={17} style={{ color: 'var(--primary)' }} />
          <strong style={{ fontSize: '15px' }}>{t('Maintenance window')}</strong>
          <span className="badge">{t('Server time')}: {data.server_time.replace('T', ' ')}</span>
        </div>
        <span style={{ display: 'block', marginTop: '4px', fontSize: '12.5px', color: 'var(--text-muted)' }}>
          {t('Each job runs at most once per window, from a systemd timer that checks every 5 minutes. A window the server was off for is skipped, not run late.')}
        </span>
      </div>

      <div style={{ width: '100%', overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '13px' }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
              <th style={{ padding: '8px 20px' }}>{t('Job')}</th>
              <th style={{ padding: '8px 12px' }}>{t('Window')}</th>
              <th style={{ padding: '8px 12px' }}>{t('Next run')}</th>
              <th style={{ padding: '8px 12px' }}>{t('Last run')}</th>
              <th style={{ padding: '8px 20px' }}>{t('Result')}</th>
            </tr>
          </thead>
          <tbody>
            {data.jobs.map(job => (
              <tr key={job.name} style={{ borderBottom: '1px solid var(--border)' }}>
                <td style={{ padding: '8px 20px', fontWeight: 600 }}>{t(job.label)}</td>
                <td style={{ padding: '8px 12px' }}>{job.enabled ? job.window : <span className="badge">{t('Off')}</span>}</td>
                <td style={{ padding: '8px 12px' }}>{job.next_run_at ? job.next_run_at.replace('T', ' ') : '-'}</td>
                <td style={{ padding: '8px 12px' }}>{job.last_started_at || '-'}</td>
                <td style={{ padding: '8px 20px' }}>
                  {statusBadge(job.last_status)}{' '}
                  <span style={{ color: 'var(--text-muted)' }}>{job.last_message}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <form onSubmit={save} style={{ padding: '14px 20px', display: 'grid', gap: '12px' }}>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Window length (minutes)')}
          <input
            type="number"
            min={15}
            max={360}
            value={form.window_minutes}
            onChange={e => setForm(prev => ({ ...prev, window_minutes: Number(e.target.value) }))}
            style={{ width: '90px' }}
          />
        </label>
        <strong>{t('Scheduled core stack security patching')}</strong>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <input type="checkbox" checked={!!core.enabled} onChange={e => setCore({ enabled: e.target.checked })} />
          {t('Patch the selected packages in the window (nginx -t before and after; unattended-upgrades is told to leave them alone)')}
        </label>
        <label style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {t('Window start')}
          <input type="time" value={core.window} onChange={e => setCore({ window: e.target.value })} />
        </label>
        <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap', alignItems: 'center' }}>
          <span>{t('Days (none = every day)')}:</span>
          {WEEKDAYS.map((day, idx) => (
            <label key={day} style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="checkbox" checked={core.weekdays.includes(idx)} onChange={() => toggleDay(idx)} />
              {t(day)}
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {data.core_stack_packages.map(pkg => (
            <label key={pkg} style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="checkbox" checked={core.packages.includes(pkg)} onChange={() => togglePkg(pkg)} />
              <code>{pkg}</code>
            </label>
          ))}
        </div>
        <div>
          <button type="submit" className="cb-btn primary" disabled={!!loading || saving}>
            <Save size={14} /> {t('Save maintenance window')}
          </button>
        </div>
      </form>
    </div>
  );
}
