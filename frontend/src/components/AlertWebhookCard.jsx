import React, { useEffect, useState } from 'react';
import { Bell, Save, Send } from 'lucide-react';
import { t } from '../i18n.js';

/* Watchdog and maintenance alerts over webhooks. Works without the
 * Notifications addon. The URL is write-only: the page only ever sees its host. */
export function AlertWebhookCard({ request, setNotice, loading }) {
  const [cfg, setCfg] = useState(null);
  const [form, setForm] = useState({ url: '', format: 'slack', enabled: false, endpoints_enabled: true, events: [], cooldown_minutes: 30 });

  async function load() {
    const res = await request('/services/alert-webhook', { silent: true }, null);
    if (res) {
      setCfg(res);
      setForm({
        url: '',
        format: res.format,
        enabled: res.enabled,
        endpoints_enabled: res.endpoints_enabled,
        events: res.events,
        cooldown_minutes: res.cooldown_minutes,
      });
    }
  }

  useEffect(() => {
    load();
  }, []);

  if (!cfg) return null;

  async function save(e) {
    e.preventDefault();
    const payload = { ...form };
    if (!payload.url) delete payload.url;
    const res = await request('/services/alert-webhook', { method: 'PUT', body: JSON.stringify(payload) }, t('Saving alert webhook...'));
    if (res) {
      setCfg(res);
      setForm(prev => ({ ...prev, url: '' }));
      setNotice(t('Alert webhook saved.'));
    }
  }

  async function clearUrl() {
    const res = await request('/services/alert-webhook', { method: 'PUT', body: JSON.stringify({ clear_url: true }) });
    if (res) {
      setCfg(res);
      setNotice(t('Webhook URL removed.'));
    }
  }

  async function sendTest() {
    const res = await request('/services/alert-webhook/test', { method: 'POST' }, t('Sending test alert...'));
    if (res) setNotice(t('Test alert delivered.'));
  }

  const toggleEvent = ev =>
    setForm(prev => ({ ...prev, events: prev.events.includes(ev) ? prev.events.filter(x => x !== ev) : [...prev.events, ev] }));

  return (
    <div className="card" style={{ padding: '16px', marginTop: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
        <Bell size={16} />
        <h3 style={{ margin: 0, fontSize: '1rem' }}>{t('Alert webhooks')}</h3>
        {cfg.url_set ? <span className="badge ok">{cfg.url_host}</span> : <span className="badge">{t('No URL set')}</span>}
      </div>
      <p className="hint" style={{ marginTop: 0 }}>
        {t('Watchdog, maintenance window, backup verification and panel self-backup alerts go to this chat webhook (Slack, Mattermost, Discord or generic JSON) and to the outbound webhooks of administrator accounts. No Notifications addon needed.')}
      </p>
      <form onSubmit={save} style={{ display: 'grid', gap: '10px' }}>
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input type="checkbox" checked={form.enabled} onChange={e => setForm(prev => ({ ...prev, enabled: e.target.checked }))} />
          {t('Send alerts to the chat webhook')}
        </label>
        <input
          type="password"
          autoComplete="off"
          placeholder={cfg.url_set ? t('https://... (leave empty to keep the current URL)') : 'https://hooks.slack.com/services/...'}
          value={form.url}
          onChange={e => setForm(prev => ({ ...prev, url: e.target.value }))}
        />
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {t('Format')}
          <select value={form.format} onChange={e => setForm(prev => ({ ...prev, format: e.target.value }))}>
            <option value="slack">Slack / Mattermost</option>
            <option value="discord">Discord</option>
            <option value="generic">{t('Generic JSON')}</option>
          </select>
        </label>
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input
            type="checkbox"
            checked={form.endpoints_enabled}
            onChange={e => setForm(prev => ({ ...prev, endpoints_enabled: e.target.checked }))}
          />
          {t("Also send to administrators' outbound webhooks (event ops.<name>, signed)")}
        </label>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
          {cfg.all_events.map(ev => (
            <label key={ev} style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
              <input type="checkbox" checked={form.events.includes(ev)} onChange={() => toggleEvent(ev)} />
              <code>{ev}</code>
            </label>
          ))}
        </div>
        <label style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          {t('Repeat the same alert at most every (minutes)')}
          <input
            type="number"
            min={0}
            max={1440}
            style={{ width: '90px' }}
            value={form.cooldown_minutes}
            onChange={e => setForm(prev => ({ ...prev, cooldown_minutes: Number(e.target.value) }))}
          />
        </label>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <button type="submit" className="cb-btn primary" disabled={!!loading}>
            <Save size={14} /> {t('Save')}
          </button>
          <button type="button" className="cb-btn secondary" disabled={!!loading || !cfg.url_set} onClick={sendTest}>
            <Send size={14} /> {t('Send test')}
          </button>
          {cfg.url_set && (
            <button type="button" className="cb-btn secondary" disabled={!!loading} onClick={clearUrl}>
              {t('Remove URL')}
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
