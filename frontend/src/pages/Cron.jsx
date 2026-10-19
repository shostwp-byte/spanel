import React, { useState, useEffect } from 'react';
import {
  Clock, Plus, Trash2, RefreshCw, Globe, TerminalIcon,
  Layers, CheckCircle, Search, Code2, User
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';
import { WebsiteSelect } from '../components/WebsiteSelect.jsx';
import { EmptyState } from '../components/EmptyState.jsx';

export function CronPage({ request, isAdmin, mode = 'site' }) {
  const {
    websites = [], users = [], loading, setLoading, setNotice, setError,
    selectedWebsiteId, setSelectedWebsiteId,
    cronRuntime: ctxCronRuntime, setCronRuntime: ctxSetCronRuntime,
    cronPreset: ctxCronPreset, setCronPreset: ctxSetCronPreset,
    cronParts: ctxCronParts, setCronParts: ctxSetCronParts,
    cronCommand: ctxCronCommand, setCronCommand: ctxSetCronCommand,
    cronJobs: ctxCronJobs, setCronJobs: ctxSetCronJobs,
  } = useAppContext();

  const [localCronRuntime, setLocalCronRuntime] = useState(null);
  const [localCronPreset, setLocalCronPreset] = useState('every_5_minutes');
  const [localCronParts, setLocalCronParts] = useState({
    minute: '*/5',
    hour: '*',
    day: '*',
    month: '*',
    weekday: '*',
  });
  const [localCronCommand, setLocalCronCommand] = useState('');
  const [localCronJobs, setLocalCronJobs] = useState([]);

  const cronRuntime = ctxCronRuntime !== undefined ? ctxCronRuntime : localCronRuntime;
  const setCronRuntime = ctxSetCronRuntime || setLocalCronRuntime;
  const cronPreset = ctxCronPreset || localCronPreset;
  const setCronPreset = ctxSetCronPreset || setLocalCronPreset;
  const cronParts = ctxCronParts || localCronParts;
  const setCronParts = ctxSetCronParts || setLocalCronParts;
  const cronCommand = ctxCronCommand !== undefined ? ctxCronCommand : localCronCommand;
  const setCronCommand = ctxSetCronCommand || setLocalCronCommand;
  const cronJobs = Array.isArray(ctxCronJobs) ? ctxCronJobs : localCronJobs;
  const setCronJobs = ctxSetCronJobs || setLocalCronJobs;

  const [activeTab, setActiveTab] = useState(isAdmin && mode === 'all' ? 'all' : 'manage');
  const [allJobs, setAllJobs] = useState([]);
  const [siteRuntimesMap, setSiteRuntimesMap] = useState({});
  const [searchQuery, setSearchQuery] = useState('');
  const [websiteFilter, setWebsiteFilter] = useState('all');

  useEffect(() => {
    if (isAdmin && mode === 'all') {
      setActiveTab('all');
    } else if (!isAdmin) {
      setActiveTab('manage');
    }
  }, [isAdmin, mode]);

  const CRON_PRESETS = [
    { key: 'every_minute', label: t('Every minute'), schedule: '* * * * *', parts: { minute: '*', hour: '*', day: '*', month: '*', weekday: '*' } },
    { key: 'every_5_minutes', label: t('Every 5 minutes'), schedule: '*/5 * * * *', parts: { minute: '*/5', hour: '*', day: '*', month: '*', weekday: '*' } },
    { key: 'every_15_minutes', label: t('Every 15 minutes'), schedule: '*/15 * * * *', parts: { minute: '*/15', hour: '*', day: '*', month: '*', weekday: '*' } },
    { key: 'every_30_minutes', label: t('Every 30 minutes'), schedule: '*/30 * * * *', parts: { minute: '*/30', hour: '*', day: '*', month: '*', weekday: '*' } },
    { key: 'hourly', label: t('Hourly'), schedule: '0 * * * *', parts: { minute: '0', hour: '*', day: '*', month: '*', weekday: '*' } },
    { key: 'daily', label: t('Daily at 03:00'), schedule: '0 3 * * *', parts: { minute: '0', hour: '3', day: '*', month: '*', weekday: '*' } },
    { key: 'weekly', label: t('Weekly (Sun 03:00)'), schedule: '0 3 * * 0', parts: { minute: '0', hour: '3', day: '*', month: '*', weekday: '0' } },
    { key: 'monthly', label: t('Monthly (1st 03:00)'), schedule: '0 3 1 * *', parts: { minute: '0', hour: '3', day: '1', month: '*', weekday: '*' } },
    { key: 'custom', label: t('Custom'), schedule: '', parts: null },
  ];

  function currentCronSchedule() {
    const clean = value => String(value ?? '*').trim() || '*';
    return [clean(cronParts?.minute), clean(cronParts?.hour), clean(cronParts?.day), clean(cronParts?.month), clean(cronParts?.weekday)].join(' ');
  }

  function describeSchedule(schedule) {
    const found = CRON_PRESETS.find(p => p.schedule && p.schedule === (schedule || '').trim());
    return found ? found.label : t('Custom schedule');
  }

  function selectCronPreset(key) {
    setCronPreset(key);
    const preset = CRON_PRESETS.find(item => item.key === key);
    if (preset?.parts) setCronParts(preset.parts);
  }

  function updateCronPart(field, value) {
    setCronPreset('custom');
    setCronParts(prev => ({ ...(prev || { minute: '*/5', hour: '*', day: '*', month: '*', weekday: '*' }), [field]: value }));
  }

  function applyCronCommandTemplate(kind) {
    const php = cronRuntime?.php_binary || '/usr/bin/php';
    const root = cronRuntime?.document_root || '/var/www/site';
    if (kind === 'wp') {
      setCronCommand(`${php} -q ${root}/wp-cron.php >/dev/null 2>&1`);
      selectCronPreset('every_5_minutes');
    } else if (kind === 'laravel') {
      setCronCommand(`cd ${root} && ${php} artisan schedule:run >> /dev/null 2>&1`);
      selectCronPreset('every_minute');
    } else if (kind === 'php_script') {
      setCronCommand(`${php} -q ${root}/cron.php >/dev/null 2>&1`);
    } else if (kind === 'curl') {
      const site = websites.find(item => String(item.id) === String(selectedWebsiteId));
      const url = site ? `https://${site.domain}/cron` : 'https://example.com/cron';
      setCronCommand(`/usr/bin/curl -fsSL ${url} >/dev/null 2>&1`);
    }
  }

  async function loadCron(websiteId = selectedWebsiteId, options = {}) {
    if (!websiteId) return;
    const data = await request(`/maintenance/cron/${websiteId}`, options, t('Loading cron jobs...'));
    if (!data) return;
    setCronJobs(data.items || []);
    setCronRuntime({
      cron_user: data.cron_user || 'www-data',
      php_version: data.php_version || '',
      php_binary: data.php_binary || '/usr/bin/php',
      document_root: data.document_root || '',
    });
  }

  async function loadAllServerCrons(silent = false) {
    if (!websites.length) {
      setAllJobs([]);
      return;
    }
    if (!silent) setLoading(t('Scanning cron schedules across all websites...'));
    try {
      const results = await Promise.all(
        websites.map(async site => {
          const data = await request(`/maintenance/cron/${site.id}`, { silent: true });
          return { site, data };
        })
      );
      const aggregated = [];
      const runtimeMap = {};
      for (const { site, data } of results) {
        if (!data) continue;
        const owner = users.find(u => Number(u.id) === Number(site.owner_id));
        runtimeMap[site.id] = {
          cron_user: data.cron_user || site.linux_user || 'www-data',
          php_version: data.php_version || site.php_version || '8.4',
          php_binary: data.php_binary || '/usr/bin/php',
          document_root: data.document_root || site.root_path || '',
        };
        const items = data.items || [];
        items.forEach((job, idx) => {
          aggregated.push({
            key: `${site.id}-${job.index ?? idx}`,
            website_id: site.id,
            domain: site.domain,
            owner_username: owner?.username || (site.owner_id ? `user #${site.owner_id}` : 'admin'),
            cron_user: data.cron_user || site.linux_user || 'www-data',
            php_version: data.php_version || site.php_version || '8.4',
            index: job.index ?? idx,
            schedule: job.schedule,
            command: job.command,
          });
        });
      }
      setSiteRuntimesMap(runtimeMap);
      setAllJobs(aggregated);
    } finally {
      if (!silent) setLoading('');
    }
  }

  useEffect(() => {
    if (!selectedWebsiteId && websites.length > 0) {
      setSelectedWebsiteId(String(websites[0].id));
    }
  }, [websites, selectedWebsiteId]);

  useEffect(() => {
    if (selectedWebsiteId) {
      loadCron(selectedWebsiteId, { silent: true });
    }
  }, [selectedWebsiteId]);

  useEffect(() => {
    if (isAdmin && websites.length > 0) {
      loadAllServerCrons(true);
    }
  }, [isAdmin, websites.length]);

  async function addCron(e) {
    e.preventDefault();
    if (!selectedWebsiteId) {
      setError(t('Select a website before adding a cron job.'));
      return;
    }
    const schedule = currentCronSchedule();
    const data = await request(
      '/maintenance/cron',
      { method: 'POST', body: JSON.stringify({ website_id: Number(selectedWebsiteId), schedule, command: cronCommand }) },
      t('Saving cron job...')
    );
    if (!data) return;
    setCronCommand('');
    await loadCron(selectedWebsiteId, { silent: true });
    setNotice(t('Cron job added.'));
    if (isAdmin) loadAllServerCrons(true);
  }

  async function deleteCron(index, targetWebsiteId = selectedWebsiteId) {
    if (!targetWebsiteId) return;
    const data = await request(
      '/maintenance/cron',
      { method: 'DELETE', body: JSON.stringify({ website_id: Number(targetWebsiteId), index }) },
      t('Removing cron job...')
    );
    if (!data) return;
    if (String(targetWebsiteId) === String(selectedWebsiteId)) {
      await loadCron(selectedWebsiteId, { silent: true });
    }
    setNotice(t('Cron job removed.'));
    if (isAdmin) loadAllServerCrons(true);
  }

  const filteredAllJobs = allJobs.filter(job => {
    if (websiteFilter !== 'all' && String(job.website_id) !== String(websiteFilter)) return false;
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      (job.domain || '').toLowerCase().includes(q) ||
      (job.cron_user || '').toLowerCase().includes(q) ||
      (job.owner_username || '').toLowerCase().includes(q) ||
      (job.schedule || '').toLowerCase().includes(q) ||
      (job.command || '').toLowerCase().includes(q)
    );
  });

  const websitesWithCronsCount = new Set(allJobs.map(j => j.website_id)).size;
  const distinctLinuxUsers = new Set([
    ...Object.values(siteRuntimesMap).map(r => r.cron_user),
    ...allJobs.map(j => j.cron_user),
  ].filter(Boolean)).size;
  const distinctPhpVers = Array.from(new Set([
    ...websites.map(w => w.php_version || '8.4'),
    ...allJobs.map(j => j.php_version),
  ].filter(Boolean)));

  return (
    <section className="section">
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <Clock size={19} />
            <span>{isAdmin && activeTab === 'all' ? t('All User Cron Jobs') : t('Cron Jobs')}</span>
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {isAdmin
              ? t('Audit, filter, add, and remove scheduled crontab tasks across all hosted websites and isolated Linux accounts.')
              : t('Run scheduled tasks under the website Linux user and selected PHP CLI version.')}
          </p>
        </div>
        <div className="cb-toolbar-actions">
          {isAdmin && activeTab === 'all' ? (
            <>
              <button
                type="button"
                className="cb-btn secondary"
                disabled={!!loading}
                onClick={() => loadAllServerCrons(false)}
              >
                <RefreshCw size={14} /> {t('Refresh All')}
              </button>
              <button
                type="button"
                className="cb-btn primary"
                onClick={() => setActiveTab('manage')}
              >
                <Plus size={14} /> {t('Add Cron Job')}
              </button>
            </>
          ) : (
            <>
              <WebsiteSelect
                value={selectedWebsiteId}
                websites={websites}
                onChange={value => {
                  setSelectedWebsiteId(value);
                  loadCron(value);
                }}
              />
              <button
                type="button"
                className="cb-btn secondary"
                disabled={!selectedWebsiteId || !!loading}
                onClick={() => loadCron()}
              >
                <RefreshCw size={14} /> {t('Load cron')}
              </button>
            </>
          )}
        </div>
      </div>

      {/* KPI Strip for Admin */}
      {isAdmin && (
        <div className="cb-stats-strip">
          <div className="cb-stat-box">
            <div className="cb-stat-icon blue"><Clock size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Total Scheduled Jobs')}</span>
              <strong>
                {allJobs.length} {t('Jobs')}
                <small className="cb-stat-sub">{t('Server-wide crontab')}</small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon emerald"><Globe size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Websites with Crons')}</span>
              <strong>
                {websitesWithCronsCount} / {websites.length}
                <small className="cb-stat-sub">{t('Active domains')}</small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon indigo"><User size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('Isolated Linux Users')}</span>
              <strong>
                {distinctLinuxUsers || websites.length} {t('Users')}
                <small className="cb-stat-sub">/etc/cron.d · crontab</small>
              </strong>
            </div>
          </div>
          <div className="cb-stat-box">
            <div className="cb-stat-icon amber"><Code2 size={18} /></div>
            <div className="cb-stat-info">
              <span className="cb-stat-label">{t('PHP CLI Runtimes')}</span>
              <strong>
                {distinctPhpVers.length || 1} {t('Branches')}
                <small className="cb-stat-sub">{distinctPhpVers.join(', ') || '8.4'}</small>
              </strong>
            </div>
          </div>
        </div>
      )}

      {/* Admin Tabs */}
      {isAdmin && (
        <div className="segmented-control backup-tabs" role="tablist" style={{ marginBottom: '16px' }}>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'all'}
            className={activeTab === 'all' ? 'active' : ''}
            onClick={() => setActiveTab('all')}
          >
            <Layers size={14} /> {t('All Server Cron Jobs')} ({allJobs.length})
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={activeTab === 'manage'}
            className={activeTab === 'manage' ? 'active' : ''}
            onClick={() => setActiveTab('manage')}
          >
            <Plus size={14} /> {t('Add / Manage by Website')}
          </button>
        </div>
      )}

      {/* TAB 1: ALL SERVER CRON JOBS (Admin) */}
      {isAdmin && activeTab === 'all' && (
        <div style={{ display: 'grid', gap: '14px' }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'space-between', alignItems: 'center', gap: '12px' }}>
            <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: '10px', flex: '1 1 320px' }}>
              <div style={{ position: 'relative', minWidth: '260px', flex: '1 1 260px', maxWidth: '380px' }}>
                <input
                  type="text"
                  value={searchQuery}
                  onChange={e => setSearchQuery(e.target.value)}
                  placeholder={t('Filter by domain, user, schedule, or command...')}
                  style={{ width: '100%' }}
                />
              </div>
              <select
                value={websiteFilter}
                onChange={e => setWebsiteFilter(e.target.value)}
                style={{ width: 'auto', minWidth: '200px' }}
              >
                <option value="all">{t('All Websites')} ({websites.length})</option>
                {websites.map(w => (
                  <option key={w.id} value={String(w.id)}>{w.domain}</option>
                ))}
              </select>
            </div>
            <span className="hint">{t('Showing {n} scheduled job(s)', { n: filteredAllJobs.length })}</span>
          </div>

          <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
            {filteredAllJobs.length === 0 ? (
              <div style={{ padding: '42px 20px', textAlign: 'center' }}>
                <Clock size={36} style={{ opacity: 0.35, marginBottom: '10px' }} />
                <h3 style={{ margin: '0 0 6px', fontSize: '15px' }}>{t('No scheduled cron jobs found')}</h3>
                <p className="hint" style={{ margin: '0 0 14px' }}>
                  {websites.length === 0
                    ? t('Create a website first to schedule cron jobs.')
                    : t('No cron jobs match your current filter, or no websites have scheduled tasks yet.')}
                </p>
                {websites.length > 0 && (
                  <button type="button" className="cb-btn primary" onClick={() => setActiveTab('manage')}>
                    <Plus size={13} /> {t('Create First Cron Job')}
                  </button>
                )}
              </div>
            ) : (
              <div style={{ width: '100%', overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                  <thead>
                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '12px 16px' }}>{t('Website')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Owner / Linux User')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Schedule')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Command')}</th>
                      <th style={{ padding: '12px 16px', textAlign: 'right' }}>{t('Action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredAllJobs.map(job => (
                      <tr key={job.key} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 16px' }}>
                          <div style={{ fontWeight: 600 }}>{job.domain}</div>
                          <small className="hint">PHP {job.php_version}</small>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <div><strong>{job.owner_username}</strong></div>
                          <code style={{ fontSize: '11.5px' }}>{job.cron_user}</code>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <code>{job.schedule}</code>
                          <div className="hint" style={{ fontSize: '11.5px', marginTop: '2px' }}>
                            {describeSchedule(job.schedule)}
                          </div>
                        </td>
                        <td style={{ padding: '12px 16px', maxWidth: '440px' }}>
                          <code style={{ wordBreak: 'break-all', fontSize: '12px' }}>{job.command}</code>
                        </td>
                        <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '8px' }}>
                            <button
                              type="button"
                              className="cb-btn secondary"
                              onClick={() => {
                                setSelectedWebsiteId(String(job.website_id));
                                setActiveTab('manage');
                              }}
                            >
                              {t('Manage')}
                            </button>
                            <button
                              type="button"
                              className="cb-btn danger"
                              disabled={!!loading}
                              onClick={() => deleteCron(job.index, job.website_id)}
                            >
                              <Trash2 size={13} /> {t('Delete')}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {/* TAB 2: ADD / MANAGE BY WEBSITE */}
      {(!isAdmin || activeTab === 'manage') && (
        <div style={{ display: 'grid', gap: '16px' }}>
          {cronRuntime && (
            <div className="inline-meta cron-runtime-note">
              <span>{t('Linux user')}: <code>{cronRuntime.cron_user}</code></span>
              <span>{t('PHP CLI')}: <code>{cronRuntime.php_binary}</code>{cronRuntime.php_version ? ` (PHP ${cronRuntime.php_version})` : ''}</span>
              {cronRuntime.document_root && <span>{t('Root')}: <code>{cronRuntime.document_root}</code></span>}
            </div>
          )}

          <form className="cron-builder" onSubmit={addCron}>
            <div className="cron-section-block">
              <span className="cron-section-label">{t('Schedule Presets')}</span>
              <div className="cron-presets" role="group" aria-label={t('Schedule presets')}>
                {CRON_PRESETS.map(preset => (
                  <button
                    type="button"
                    key={preset.key}
                    className={`cb-pill ${cronPreset === preset.key ? 'active' : ''}`}
                    disabled={!selectedWebsiteId || !!loading}
                    onClick={() => selectCronPreset(preset.key)}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="cron-parts-grid">
              <label>
                <span>{t('Minute')}</span>
                <input value={cronParts?.minute ?? '*'} disabled={!selectedWebsiteId || !!loading} onChange={e => updateCronPart('minute', e.target.value)} placeholder="*" required />
              </label>
              <label>
                <span>{t('Hour')}</span>
                <input value={cronParts?.hour ?? '*'} disabled={!selectedWebsiteId || !!loading} onChange={e => updateCronPart('hour', e.target.value)} placeholder="*" required />
              </label>
              <label>
                <span>{t('Day')}</span>
                <input value={cronParts?.day ?? '*'} disabled={!selectedWebsiteId || !!loading} onChange={e => updateCronPart('day', e.target.value)} placeholder="*" required />
              </label>
              <label>
                <span>{t('Month')}</span>
                <input value={cronParts?.month ?? '*'} disabled={!selectedWebsiteId || !!loading} onChange={e => updateCronPart('month', e.target.value)} placeholder="*" required />
              </label>
              <label>
                <span>{t('Weekday')}</span>
                <input value={cronParts?.weekday ?? '*'} disabled={!selectedWebsiteId || !!loading} onChange={e => updateCronPart('weekday', e.target.value)} placeholder="*" required />
              </label>
            </div>

            <div className="cron-Templates">
              <span className="cron-section-label" style={{ marginBottom: 0, marginRight: '4px' }}>{t('Quick command')}:</span>
              <button type="button" className="cb-btn secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => applyCronCommandTemplate('wp')}>{t('WordPress cron')}</button>
              <button type="button" className="cb-btn secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => applyCronCommandTemplate('laravel')}>{t('Laravel scheduler')}</button>
              <button type="button" className="cb-btn secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => applyCronCommandTemplate('php_script')}>{t('Custom PHP script')}</button>
              <button type="button" className="cb-btn secondary" disabled={!selectedWebsiteId || !!loading} onClick={() => applyCronCommandTemplate('curl')}>{t('cURL URL')}</button>
            </div>

            <div className="cron-command-row">
              <label className="cron-schedule-preview">
                <span>{t('Schedule')}</span>
                <code>{currentCronSchedule()}</code>
              </label>
              <label className="cron-command-field">
                <span>{t('Command')}</span>
                <input value={cronCommand || ''} disabled={!selectedWebsiteId || !!loading} onChange={e => setCronCommand(e.target.value)} placeholder="/usr/bin/php8.4 -q /var/www/site/cron.php >/dev/null 2>&1" required />
              </label>
              <button type="submit" className="cb-btn primary cron-submit-btn" disabled={!selectedWebsiteId || !!loading}>
                <Plus size={15} /> {t('Add cron')}
              </button>
            </div>
          </form>

          {cronJobs?.length ? (
            <div style={{ border: '1px solid var(--border)', borderRadius: '12px', overflow: 'hidden', background: 'var(--surface)' }}>
              <div style={{ width: '100%', overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', textAlign: 'left', fontSize: '13.5px' }}>
                  <thead>
                    <tr style={{ background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                      <th style={{ padding: '12px 16px', width: '220px' }}>{t('Schedule')}</th>
                      <th style={{ padding: '12px 16px' }}>{t('Command')}</th>
                      <th style={{ padding: '12px 16px', textAlign: 'right', width: '120px' }}>{t('Action')}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {cronJobs.map(item => (
                      <tr key={item.index} style={{ borderBottom: '1px solid var(--border)' }}>
                        <td style={{ padding: '12px 16px' }}>
                          <code>{item.schedule}</code>
                          <div className="hint" style={{ fontSize: '11.5px', marginTop: '2px' }}>
                            {describeSchedule(item.schedule)}
                          </div>
                        </td>
                        <td style={{ padding: '12px 16px' }}>
                          <code style={{ wordBreak: 'break-all', fontSize: '12.5px' }}>{item.command}</code>
                        </td>
                        <td style={{ padding: '12px 16px', textAlign: 'right' }}>
                          <button type="button" className="cb-btn danger" disabled={!!loading} onClick={() => deleteCron(item.index)}>
                            <Trash2 size={13} /> {t('Delete')}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : (
            <EmptyState
              icon={Clock}
              message={selectedWebsiteId ? t('No cron jobs scheduled for this website yet.') : t('Select a website to view its cron jobs.')}
            />
          )}
        </div>
      )}
    </section>
  );
}
