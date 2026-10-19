import React, { useState, useEffect, useRef } from 'react';
import { Ban, Check, Cpu, Play, RotateCcw, Trash2, AlertCircle, Plus, RefreshCw } from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function PhpConfigPage({ request, isAdmin, loadServiceNames, loadPhpVersions, refreshAll, sortPhpVersions }) {
  const { loading, setNotice, phpVersions } = useAppContext();

  const [phpConfig, setPhpConfig] = useState({
    php_version: '8.4',
    display_errors: 'Off',
    max_execution_time: 300,
    max_input_time: 600,
    max_input_vars: 10000,
    memory_limit: '1024M',
    post_max_size: '1024M',
    upload_max_filesize: '1024M',
  });
  const [phpExtensions, setPhpExtensions] = useState({ versions: [], extensions: [] });
  const [phpTune, setPhpTune] = useState(null);
  const [phpTuneApplied, setPhpTuneApplied] = useState(false);

  const busyRef = useRef(null);
  const [busyAction, setBusyAction] = useState('');
  const isBusy = Boolean(busyAction || loading);

  async function runLocked(key, fn) {
    if (busyRef.current) return;
    busyRef.current = key;
    setBusyAction(key);
    try {
      await fn();
    } finally {
      busyRef.current = null;
      setBusyAction('');
    }
  }

  useEffect(() => {
    if (isAdmin) {
      if (loadPhpVersions) loadPhpVersions({ silent: true });
      loadPhpConfig();
      loadPhpExtensions();
    }
  }, [isAdmin]);

  useEffect(() => {
    if (phpConfig.php_version) loadPhpTune(phpConfig.php_version);
  }, [phpConfig.php_version]);

  async function loadPhpTune(version) {
    const data = await request(`/maintenance/php-tune?php_version=${encodeURIComponent(version)}`, { silent: true });
    setPhpTune(data || null);
    setPhpTuneApplied(false);
  }

  async function toggleOpcache() {
    if (busyRef.current) return;
    const version = phpTune?.php_version || phpConfig.php_version;
    const next = !phpTune?.opcache_enabled;
    await runLocked('opcache', async () => {
      const data = await request(
        '/maintenance/php-opcache',
        {
          method: 'POST',
          body: JSON.stringify({ php_version: version, enabled: next }),
        },
        next ? `Enabling OPcache for PHP ${version}...` : `Disabling OPcache for PHP ${version}...`
      );
      if (data) {
        setNotice(data.message || 'OPcache setting changed.');
        await loadPhpTune(version);
      }
    });
  }

  async function applyPhpTune() {
    if (busyRef.current) return;
    const version = phpTune?.php_version || phpConfig.php_version;
    await runLocked('tune', async () => {
      const data = await request(
        '/maintenance/php-tune',
        {
          method: 'POST',
          body: JSON.stringify({ php_version: version }),
        },
        t('Tuning PHP for this machine...')
      );
      if (data) {
        if (data.plan) setPhpTune(data.plan);
        setPhpTuneApplied(true);
        await loadPhpConfig(version);
      }
    });
  }

  async function loadPhpConfig(version = phpConfig.php_version) {
    const data = await request(`/maintenance/php-config?php_version=${encodeURIComponent(version)}`, {}, t('Loading PHP config...'));
    if (data) setPhpConfig(prev => ({ ...prev, ...data, php_version: version }));
  }

  async function updatePhpConfig() {
    if (busyRef.current) return;
    await runLocked('save-config', async () => {
      const data = await request(
        '/maintenance/php-config',
        {
          method: 'POST',
          body: JSON.stringify({
            ...phpConfig,
            max_execution_time: Number(phpConfig.max_execution_time),
            max_input_time: Number(phpConfig.max_input_time),
            max_input_vars: Number(phpConfig.max_input_vars),
          }),
        },
        t('Updating PHP config...')
      );
      if (data?.target) {
        setNotice(`Updated PHP config: ${data.target}`);
        await loadPhpConfig(phpConfig.php_version);
      }
    });
  }

  async function restorePhpDefaults() {
    if (busyRef.current) return;
    if (!confirm(`Restore default PHP ${phpConfig.php_version} values?`)) return;
    await runLocked('restore-defaults', async () => {
      const data = await request(
        '/maintenance/php-config/defaults',
        {
          method: 'POST',
          body: JSON.stringify({ php_version: phpConfig.php_version }),
        },
        t('Restoring PHP defaults...')
      );
      if (data?.values) {
        setPhpConfig(prev => ({ ...prev, ...data.values }));
        setNotice(`Restored PHP ${phpConfig.php_version} defaults.`);
      }
    });
  }

  async function loadPhpExtensions() {
    const data = await request('/maintenance/php-extensions', { silent: true });
    if (data) setPhpExtensions({ versions: data.versions || [], extensions: data.extensions || [] });
  }

  async function installPhpExtension(extension, versions) {
    if (busyRef.current || !versions?.length) return;
    const key = versions.length > 1 ? `ext-all-${extension}` : `ext-${versions[0]}-${extension}`;
    await runLocked(key, async () => {
      for (const version of versions) {
        const data = await request(
          '/maintenance/php-extensions',
          {
            method: 'POST',
            body: JSON.stringify({ php_version: version, extension }),
          }
        );
        if (!data) return;
        setPhpExtensions({ versions: data.versions || [], extensions: data.extensions || [] });
      }
      setNotice(t('{extension} installed for PHP {versions}.', { extension, versions: versions.join(', ') }));
    });
  }

  async function installPhpVersion(version) {
    if (busyRef.current) return;
    if (!confirm(t('Install PHP {version}? This will install php{version}-fpm via apt.', { version }))) return;
    await runLocked(`install-php-${version}`, async () => {
      const data = await request(
        `/maintenance/php-versions/${version}/install`,
        { method: 'POST' }
      );
      if (data) {
        setNotice(t('PHP {version} installed successfully.', { version }));
        if (loadPhpVersions) await loadPhpVersions({ silent: true });
        await loadPhpExtensions();
        if (loadServiceNames) await loadServiceNames();
      }
    });
  }

  async function uninstallPhpVersion(version) {
    if (busyRef.current) return;
    if (version === '8.4') return;
    if (!confirm(t('Uninstall PHP {version}? This will stop php{version}-fpm and remove all php{version}-* packages from the server.', { version }))) return;
    await runLocked(`uninstall-php-${version}`, async () => {
      const data = await request(
        `/maintenance/php-versions/${version}/uninstall`,
        { method: 'POST' }
      );
      if (data) {
        setNotice(t('PHP {version} uninstalled successfully.', { version }));
        if (loadPhpVersions) await loadPhpVersions({ silent: true });
        await loadPhpExtensions();
        if (loadServiceNames) await loadServiceNames();
        if (phpConfig.php_version === version) {
          setPhpConfig(prev => ({ ...prev, php_version: '8.4' }));
          await loadPhpConfig('8.4');
        }
      }
    });
  }

  function renderPhpConfig() {
    if (!isAdmin) return <div className="section"><h2>{t('PHP config')}</h2><p className="hint">{t('You do not have permission to edit PHP config.')}</p></div>;
    const notInstalled = sortPhpVersions(phpVersions.supported.filter(v => !phpVersions.installed.includes(v)));
    const tuneChanges = (phpTune?.settings || []).filter(row => row.changes && !row.overridden_value);
    const poolKey = p => `${p.max_children}|${p.idle_timeout}|${p.max_requests}|${p.request_terminate_timeout}`;
    const poolGroups = {};
    (phpTune?.pools || []).forEach(p => { (poolGroups[poolKey(p)] ||= []).push(p); });
    const [commonPools, ...restPoolGroups] = Object.values(poolGroups).sort((a, b) => b.length - a.length);
    const poolOutliers = restPoolGroups.flat();
    return (
      <div className="section">
        <div className="section-title">
          <div><h2>{t('PHP Configuration')}</h2></div>
        </div>
        <div className="user-create-card">
          <label><span>{t('PHP version')}</span><select disabled={isBusy} value={phpConfig.php_version} onChange={e => { const v = e.target.value; setPhpConfig(prev => ({ ...prev, php_version: v })); loadPhpConfig(v); loadPhpTune(v); }}>
            {phpVersions.installed.map(v => <option key={v} value={v}>PHP {v}</option>)}
          </select></label>
          <label><span>display_errors</span><select disabled={isBusy} value={phpConfig.display_errors} onChange={e => setPhpConfig(prev => ({ ...prev, display_errors: e.target.value }))}>
            <option value="Off">{t('Off (production)')}</option><option value="On">{t('On (debug)')}</option>
          </select></label>
          <label><span>max_execution_time</span><input disabled={isBusy} type="number" value={phpConfig.max_execution_time} onChange={e => setPhpConfig(prev => ({ ...prev, max_execution_time: e.target.value }))} /></label>
          <label><span>max_input_time</span><input disabled={isBusy} type="number" value={phpConfig.max_input_time} onChange={e => setPhpConfig(prev => ({ ...prev, max_input_time: e.target.value }))} /></label>
          <label><span>max_input_vars</span><input disabled={isBusy} type="number" value={phpConfig.max_input_vars} onChange={e => setPhpConfig(prev => ({ ...prev, max_input_vars: e.target.value }))} /></label>
          <label><span>memory_limit</span><input disabled={isBusy} value={phpConfig.memory_limit} onChange={e => setPhpConfig(prev => ({ ...prev, memory_limit: e.target.value }))} placeholder="1024M" /></label>
          <label><span>post_max_size</span><input disabled={isBusy} value={phpConfig.post_max_size} onChange={e => setPhpConfig(prev => ({ ...prev, post_max_size: e.target.value }))} placeholder="1024M" /></label>
          <label><span>upload_max_filesize</span><input disabled={isBusy} value={phpConfig.upload_max_filesize} onChange={e => setPhpConfig(prev => ({ ...prev, upload_max_filesize: e.target.value }))} placeholder="1024M" /></label>
          <button className="secondary-light" disabled={isBusy} onClick={restorePhpDefaults}><RotateCcw size={14}/>{t('Restore defaults')}</button>
          <button disabled={isBusy} onClick={updatePhpConfig}>{t('Save')}</button>
          {phpTune && tuneChanges.length > 0 && <div className="php-tune-diff">
            <strong><AlertCircle size={14}/> {t('Auto tune will change {n} setting(s) for PHP {version}', { n: tuneChanges.length, version: phpTune.php_version })}</strong>
            <span>{tuneChanges.map(row => `${row.key} ${row.current || 'unset'} → ${row.value}`).join(', ')}.</span>
            <button className="mini" disabled={isBusy} onClick={applyPhpTune}>{t('Auto tune PHP')}</button>
          </div>}
          {phpTune && tuneChanges.length === 0 && <div className="notice php-tune-diff">
            <Check size={14}/> PHP {phpTune.php_version} already matches what auto tune recommends for this machine ({phpTune.facts.cpu_count} CPU, {phpTune.facts.total_memory_mb} MB RAM).
          </div>}
        </div>
        {phpTune && <div className="php-tune" style={{ marginTop: 16 }}>
          <div className="php-tune-actions">
            <button disabled={isBusy} onClick={applyPhpTune}><Cpu size={14}/>{t('Auto tune PHP')}</button>
            <button className="secondary-light" disabled={isBusy} onClick={toggleOpcache}>
              {phpTune.opcache_enabled
                ? <><Ban size={14}/> {t('Disable OPcache (PHP {version})', { version: phpTune.php_version })}</>
                : <><Play size={14}/> {t('Enable OPcache (PHP {version})', { version: phpTune.php_version })}</>}
            </button>
          </div>
          {phpTuneApplied && <div className="notice php-tune-result">
            <strong><Check size={14}/> PHP {phpTune.php_version} tuned.</strong>
          </div>}
          {commonPools && <p className="hint">{t('PHP-FPM pools:')} {commonPools.length}/{phpTune.pools.length} running pm.max_children={commonPools[0].max_children || '—'},
            idle {commonPools[0].idle_timeout || '—'}, up to {commonPools[0].max_requests || '—'} requests per process.
            {poolOutliers.length > 0 && ` ${poolOutliers.length} other pool(s) run different settings:`}
          </p>}
          {poolOutliers.length > 0 && <ul className="php-tune-pool-outliers">
            {poolOutliers.map(p => <li key={p.pool}>
              <code>{p.pool}</code>
              <span>pm.max_children={p.max_children || '—'}, idle {p.idle_timeout || '—'}, up to {p.max_requests || '—'} requests</span>
            </li>)}
          </ul>}
        </div>}
        {phpExtensions.versions.length > 0 && <div className="php-ext-card">
          <h3>{t('PHP extensions')}</h3>
          <p className="hint">{t('Installed from the system packages; PHP-FPM reloads so websites can use it straight away. Removing is not offered here, since a website may depend on it.')}</p>
          <div className="php-ext-table" role="table" style={{ '--php-cols': phpExtensions.versions.length }}>
            <div className="php-ext-row php-ext-head" role="row">
              <span role="columnheader">{t('Name')}</span>
              {phpExtensions.versions.map(v => <span role="columnheader" key={v}>PHP {v}</span>)}
              <span role="columnheader" className="php-ext-all" aria-hidden="true"></span>
            </div>
            {phpExtensions.extensions.map(ext => {
              const missing = phpExtensions.versions.filter(v => ext.versions[v] === 'available');
              const isInstallingAll = busyAction === `ext-all-${ext.name}`;
              return <div className="php-ext-row" role="row" key={ext.name}>
                <code role="cell">{ext.name}</code>
                {phpExtensions.versions.map(v => {
                  const state = ext.versions[v];
                  const isInstallingCell = busyAction === `ext-${v}-${ext.name}` || isInstallingAll;
                  return <span role="cell" key={v}>
                    {(state === 'installed' || state === 'builtin') && <span className="badge ok" title={state === 'builtin' ? t('Built into PHP') : t('Installed')}><Check size={12}/></span>}
                    {state === 'available' && (
                      <button
                        className="mini secondary php-ext-install"
                        disabled={isBusy}
                        aria-label={`${t('Install')} php${v}-${ext.name}`}
                        title={`${t('Install')} php${v}-${ext.name}`}
                        onClick={() => installPhpExtension(ext.name, [v])}
                      >
                        {isInstallingCell ? <RefreshCw size={13} className="spin" /> : <Plus size={14}/>}
                      </button>
                    )}
                    {state === 'unavailable' && <span className="php-ext-none" title={t('Not in the package repository for this version')}>—</span>}
                  </span>;
                })}
                <span role="cell" className="php-ext-all">
                  {missing.length > 1 && (
                    <button className="mini secondary" disabled={isBusy} onClick={() => installPhpExtension(ext.name, missing)}>
                      {isInstallingAll ? <><RefreshCw size={12} className="spin"/> {t('Installing...')}</> : t('All versions')}
                    </button>
                  )}
                </span>
              </div>;
            })}
          </div>
        </div>}
        <div className="user-create-card" style={{ marginTop: 16 }}>
          <h3>{t('Manage PHP Versions')}</h3>
          <p className="hint" style={{ marginBottom: 12 }}>{t('Install additional PHP-FPM branches or uninstall unused versions (Default PHP 8.4 is protected).')}</p>
          {phpVersions.installed.length > 0 && (
            <div style={{ marginBottom: notInstalled.length > 0 ? 16 : 0 }}>
              <strong style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>{t('Installed PHP Versions')}</strong>
              <div className="php-install-grid" style={{ display: 'flex', flexWrap: 'wrap', gap: 10 }}>
                {sortPhpVersions(phpVersions.installed).map(v => {
                  const isUninstallingThis = busyAction === `uninstall-php-${v}`;
                  return (
                    <div key={v} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 8, border: '1px solid var(--border)', background: 'var(--surface-alt)' }}>
                      <span style={{ fontWeight: 600, fontSize: 13, color: 'var(--text)' }}>PHP {v}</span>
                      {v === '8.4' ? (
                        <span className="badge ok" style={{ fontSize: 11 }}>{t('Default')}</span>
                      ) : (
                        <button
                          type="button"
                          className="mini danger"
                          disabled={isBusy}
                          onClick={() => uninstallPhpVersion(v)}
                          title={t('Uninstall PHP {version}', { version: v })}
                          style={{ padding: '4px 8px', fontSize: 12, display: 'inline-flex', alignItems: 'center', gap: 4 }}
                        >
                          {isUninstallingThis ? <RefreshCw size={12} className="spin" /> : <Trash2 size={12} />}
                          {isUninstallingThis ? t('Uninstalling...') : t('Uninstall')}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
          {notInstalled.length > 0 && (
            <div>
              <strong style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>{t('Available to Install')}</strong>
              <div className="php-install-grid">
                {notInstalled.map(v => {
                  const isInstallingThis = busyAction === `install-php-${v}`;
                  return (
                    <button key={v} type="button" disabled={isBusy} onClick={() => installPhpVersion(v)}>
                      {isInstallingThis ? (
                        <><RefreshCw size={13} className="spin" /> {t('Installing PHP {version}...', { version: v })}</>
                      ) : (
                        `+ PHP ${v}`
                      )}
                    </button>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  }
  return renderPhpConfig();
}
