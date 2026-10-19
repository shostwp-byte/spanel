import React, { useState, useEffect, useRef } from 'react';
import {
  Wrench, RefreshCw, RotateCcw, CheckCircle, AlertCircle,
  Download, Trash2, Code2, Boxes, TerminalIcon, Layers, Sparkles, Check, Plus,
  Server, Database, Shield, Cpu, Zap, HardDrive
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const LOG_STORAGE_KEY = 'spanel_custombuild_logs';

function readStoredLogs() {
  try {
    const raw = sessionStorage.getItem(LOG_STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveStoredLogs(logs) {
  try {
    sessionStorage.setItem(LOG_STORAGE_KEY, JSON.stringify(logs.slice(0, 30)));
  } catch {
    // ignore storage errors
  }
}

function getCategoryVisual(category) {
  switch (category) {
    case 'Web Server':
      return { Icon: Server, tone: 'blue' };
    case 'PHP Runtime':
      return { Icon: Code2, tone: 'indigo' };
    case 'Database':
      return { Icon: Database, tone: 'emerald' };
    case 'Cache & Queue':
      return { Icon: Zap, tone: 'amber' };
    case 'Security':
      return { Icon: Shield, tone: 'rose' };
    default:
      return { Icon: Cpu, tone: 'slate' };
  }
}

export function CustomBuildPage({ request, isAdmin, loadPhpVersions, loadServiceNames, loadAddons }) {
  const {
    loading, setLoading, setNotice,
    phpVersions: ctxPhpVersions, setPhpVersions, sortPhpVersions, websites = [], addons: ctxAddons, appVersion
  } = useAppContext();

  const [activeTab, setActiveTab] = useState('overview');
  const [categoryFilter, setCategoryFilter] = useState('all');
  const [extFilter, setExtFilter] = useState('');

  const [serviceStates, setServiceStates] = useState({});
  const [phpVersionsData, setPhpVersionsData] = useState({
    installed: ctxPhpVersions?.installed || ['8.3', '8.4'],
    supported: ctxPhpVersions?.supported || ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5'],
  });
  const [phpExtensions, setPhpExtensions] = useState({ versions: [], extensions: [] });
  const [runtimes, setRuntimes] = useState({
    node: { installed: [], supported: [18, 20, 22] },
    docker: { installed: false, version: '' },
  });
  const [addonItems, setAddonItems] = useState(ctxAddons?.items || []);
  const [buildLogs, setBuildLogs] = useState(readStoredLogs);

  const busyRef = useRef(null);
  const [busyKey, setBusyKey] = useState('');
  const isBusy = Boolean(busyKey || loading);

  async function runLocked(key, fn) {
    if (busyRef.current) return;
    busyRef.current = key;
    setBusyKey(key);
    try {
      await fn();
    } finally {
      busyRef.current = null;
      setBusyKey('');
    }
  }

  const appendLog = (title, output, status = 'ok') => {
    const entry = {
      id: `cb-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`,
      timestamp: new Date().toISOString(),
      title,
      status,
      output: String(output || '').trim() || '(completed successfully)',
    };
    setBuildLogs(prev => {
      const next = [entry, ...prev].slice(0, 30);
      saveStoredLogs(next);
      return next;
    });
    return entry;
  };

  useEffect(() => {
    if (isAdmin) {
      loadStatus();
    }
  }, [isAdmin]);

  async function checkServiceUnit(unit) {
    const res = await request('/services/action', {
      method: 'POST',
      body: JSON.stringify({ name: unit, action: 'status' }),
      silent: true,
    });
    const text = `${res?.stdout || ''} ${res?.stderr || ''}`.toLowerCase();
    const running = text.includes('active (running)');
    setServiceStates(prev => ({ ...prev, [unit]: running }));
    return running;
  }

  async function loadStatus(silent = false) {
    if (!silent && !busyRef.current) {
      setLoading(t('Loading CustomBuild software stack...'));
    }
    try {
      const [phpVerRes, phpExtRes, addonsRes, svcListRes] = await Promise.all([
        request('/maintenance/php-versions', { silent: true }),
        request('/maintenance/php-extensions', { silent: true }),
        request('/addons', { silent: true }),
        request('/services/list', { silent: true }),
      ]);
      const appAddonInstalled = Boolean(
        (addonsRes?.items || []).some(a => a.slug === 'application' && a.installed)
      );
      const runtimeRes = appAddonInstalled
        ? await request('/site-runtimes/status', { silent: true })
        : null;

      const installedPhp = phpVerRes?.installed?.length ? phpVerRes.installed : (ctxPhpVersions?.installed || ['8.4']);
      const supportedPhp = phpVerRes?.supported?.length ? phpVerRes.supported : (ctxPhpVersions?.supported || ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5']);
      setPhpVersionsData({ installed: installedPhp, supported: supportedPhp });
      if (setPhpVersions) {
        const sorter = sortPhpVersions || (arr => [...arr]);
        setPhpVersions({ installed: sorter(installedPhp), supported: sorter(supportedPhp) });
      }

      if (phpExtRes) {
        setPhpExtensions({
          versions: phpExtRes.versions || installedPhp,
          extensions: phpExtRes.extensions || [],
        });
      }
      if (runtimeRes) {
        setRuntimes({
          ...runtimeRes,
          node: runtimeRes.node || {
            installed: runtimeRes.node_majors || [],
            supported: [18, 20, 22],
          },
        });
      }
      if (addonsRes?.items) {
        setAddonItems(addonsRes.items);
      }

      const allowedServices = Array.isArray(svcListRes?.services) && svcListRes.services.length
        ? svcListRes.services.filter(s => s !== 'spanel-api')
        : ['nginx', 'mariadb', 'redis-server'];
      await Promise.all(allowedServices.map(u => checkServiceUnit(u)));
    } finally {
      if (!silent && !busyRef.current) {
        setLoading('');
      }
    }
  }

  async function handleRestartService(unit, label) {
    if (busyRef.current) return;
    await runLocked(`restart-${unit}`, async () => {
      const res = await request(
        '/services/action',
        { method: 'POST', body: JSON.stringify({ name: unit, action: 'restart' }) }
      );
      const out = `${res?.stdout || ''}\n${res?.stderr || ''}`.trim() || `Service ${unit} restarted.`;
      const ok = res && (res.returncode === 0 || res.returncode === undefined);
      appendLog(`Restart ${label || unit}`, out, ok ? 'ok' : 'error');
      if (ok) {
        setNotice(t('Service {name} restarted.', { name: label || unit }));
      }
      await checkServiceUnit(unit);
    });
  }

  async function handleInstallPhp(version) {
    if (busyRef.current) return;
    if (!confirm(t('Install PHP {v} via CustomBuild (apt)?', { v: version }))) return;
    await runLocked(`install-php-${version}`, async () => {
      const res = await request(
        `/maintenance/php-versions/${version}/install`,
        { method: 'POST' }
      );
      if (res) {
        appendLog(`Install PHP ${version}-FPM`, res.output || `PHP ${version} installed successfully.`, 'ok');
        setNotice(t('PHP {v} installed successfully.', { v: version }));
        if (loadPhpVersions) await loadPhpVersions({ silent: true });
        if (loadServiceNames) await loadServiceNames();
        await loadStatus(true);
      }
    });
  }

  async function handleUninstallPhp(version, sitesCount = 0) {
    if (busyRef.current) return;
    if (version === '8.4') return;
    if (sitesCount > 0) {
      alert(t('Cannot uninstall PHP {v}: {n} website(s) are currently assigned to it. Please switch those websites to another PHP version first.', { v: version, n: sitesCount }));
      return;
    }
    if (!confirm(t('Uninstall PHP {v}? This will stop php{v}-fpm and remove all php{v}-* packages from the server.', { v: version }))) return;
    await runLocked(`uninstall-php-${version}`, async () => {
      const res = await request(
        `/maintenance/php-versions/${version}/uninstall`,
        { method: 'POST' }
      );
      if (res) {
        appendLog(`Uninstall PHP ${version}-FPM`, res.output || `PHP ${version} uninstalled successfully.`, 'ok');
        setNotice(t('PHP {v} uninstalled successfully.', { v: version }));
        if (loadPhpVersions) await loadPhpVersions({ silent: true });
        if (loadServiceNames) await loadServiceNames();
        await loadStatus(true);
      }
    });
  }

  async function handleInstallPhpExt(phpVer, extName) {
    if (busyRef.current) return;
    await runLocked(`ext-${phpVer}-${extName}`, async () => {
      const res = await request(
        '/maintenance/php-extensions',
        { method: 'POST', body: JSON.stringify({ php_version: phpVer, extension: extName }) }
      );
      if (res) {
        appendLog(`Install php${phpVer}-${extName}`, res.output || `Installed php${phpVer}-${extName}.`, 'ok');
        setPhpExtensions({
          versions: res.versions || phpExtensions.versions,
          extensions: res.extensions || phpExtensions.extensions,
        });
        setNotice(t('Installed php{v}-{e}.', { v: phpVer, e: extName }));
      }
    });
  }

  async function handleInstallPhpExtAll(extName, missingVers) {
    if (busyRef.current || !missingVers.length) return;
    await runLocked(`ext-all-${extName}`, async () => {
      for (const v of missingVers) {
        const res = await request(
          '/maintenance/php-extensions',
          { method: 'POST', body: JSON.stringify({ php_version: v, extension: extName }) }
        );
        if (!res) break;
        appendLog(`Install php${v}-${extName}`, res.output || `Installed php${v}-${extName}.`, 'ok');
        setPhpExtensions({
          versions: res.versions || phpExtensions.versions,
          extensions: res.extensions || phpExtensions.extensions,
        });
      }
      setNotice(t('Installed {e} across PHP versions.', { e: extName }));
    });
  }

  async function handleRetunePools() {
    if (busyRef.current) return;
    await runLocked('retune-pools', async () => {
      const res = await request(
        '/maintenance/php-tune/pools',
        { method: 'POST' }
      );
      if (res) {
        const msg = t('Rebuilt and tuned {n} PHP-FPM pool(s).', { n: res.pools_written ?? 0 });
        appendLog('Rebuild & Retune PHP-FPM Pools', JSON.stringify(res, null, 2), 'ok');
        setNotice(msg);
      }
    });
  }

  async function handleToggleAddon(addon, install) {
    if (busyRef.current) return;
    const label = addon.name || addon.slug;
    if (!install && !confirm(t('Remove {name}? Its data stays intact and can be re-enabled anytime.', { name: label }))) {
      return;
    }
    await runLocked(`addon-${addon.slug}`, async () => {
      const res = await request(
        `/addons/${addon.slug}/${install ? 'install' : 'uninstall'}`,
        { method: 'POST' }
      );
      if (res) {
        const out = res.output || res.next_step || res.kept || `${label} ${install ? 'installed' : 'removed'}.`;
        appendLog(`${install ? 'Install' : 'Remove'} Addon: ${label}`, out, 'ok');
        setNotice(install ? t('{name} installed.', { name: label }) : t('{name} removed.', { name: label }));
        if (loadAddons) await loadAddons();
        await loadStatus(true);
      }
    });
  }

  async function handleInstallDocker() {
    if (busyRef.current) return;
    if (!confirm(t('Install Docker Engine for containerized applications?'))) return;
    await runLocked('docker', async () => {
      if (!addonItems.some(a => a.slug === 'application' && a.installed)) {
        await request('/addons/application/install', { method: 'POST', silent: true });
      }
      const res = await request('/site-runtimes/docker-install', { method: 'POST' });
      if (res) {
        appendLog('Install Docker Engine', res.output || 'Docker Engine installed.', 'ok');
        setNotice(t('Docker Engine installed.'));
        await loadStatus(true);
      }
    });
  }

  async function handleInstallNode(major) {
    if (busyRef.current) return;
    if (!confirm(t('Install Node.js {v}.x LTS runtime?', { v: major }))) return;
    await runLocked(`node-${major}`, async () => {
      if (!addonItems.some(a => a.slug === 'application' && a.installed)) {
        await request('/addons/application/install', { method: 'POST', silent: true });
      }
      const res = await request(
        '/site-runtimes/node-install',
        { method: 'POST', body: JSON.stringify({ major: Number(major) }) }
      );
      if (res) {
        appendLog(`Install Node.js ${major}.x LTS`, res.output || `Node.js ${major} installed.`, 'ok');
        setNotice(t('Node.js {v} installed.', { v: major }));
        await loadStatus(true);
      }
    });
  }

  async function handleOsUpdate() {
    if (busyRef.current) return;
    if (!confirm(t('Run system package update (apt-get update && apt-get upgrade) now?'))) return;
    await runLocked('os-update', async () => {
      const res = await request('/updates/os/run', { method: 'POST' });
      if (res) {
        const out = `${res.stdout || ''}\n${res.stderr || ''}`.trim() || 'System packages updated.';
        appendLog('Update System Packages (apt)', out, res.returncode === 0 ? 'ok' : 'error');
        setNotice(t('System package update completed.'));
        await loadStatus(true);
      }
    });
  }

  if (!isAdmin) {
    return (
      <div className="section">
        <h2>{t('CustomBuild')}</h2>
        <p className="hint">{t('Only administrators can manage server software and build configurations.')}</p>
      </div>
    );
  }

  const phpUsage = {};
  (websites || []).forEach(site => {
    const v = String(site.php_version || '8.4').trim();
    phpUsage[v] = (phpUsage[v] || 0) + 1;
  });

  const phpMatrix = (phpVersionsData.supported || ['5.6', '7.4', '8.0', '8.1', '8.2', '8.3', '8.4', '8.5']).map(ver => {
    const isInstalled = (phpVersionsData.installed || []).includes(ver);
    const unit = `php${ver}-fpm`;
    return {
      version: ver,
      installed: isInstalled,
      running: isInstalled ? (serviceStates[unit] ?? true) : false,
      service_unit: unit,
      sites_count: phpUsage[ver] || 0,
      is_default: ver === '8.4',
    };
  });

  const malwareInstalled = addonItems.some(a => a.slug === 'malware' && a.installed);
  const fail2banAddon = addonItems.find(a => a.slug === 'fail2ban');
  const fail2banInstalled = fail2banAddon ? Boolean(fail2banAddon.installed) : true;

  const corePackages = [
    {
      id: 'nginx',
      name: 'Nginx Web Server',
      category: 'Web Server',
      version: '1.24+',
      installed: true,
      running: serviceStates['nginx'] ?? true,
      service_unit: 'nginx',
      description: t('High-performance HTTP/2 & HTTP/3 web server and reverse proxy with FastCGI cache.'),
      can_restart: true,
    },
    {
      id: 'mariadb',
      name: 'MariaDB Server',
      category: 'Database',
      version: '10.11 LTS',
      installed: true,
      running: serviceStates['mariadb'] ?? true,
      service_unit: 'mariadb',
      description: t('Relational database server powering customer MySQL databases.'),
      can_restart: true,
    },
    {
      id: 'redis',
      name: 'Redis Object Cache',
      category: 'Cache & Queue',
      version: '7.0+',
      installed: true,
      running: serviceStates['redis-server'] ?? true,
      service_unit: 'redis-server',
      description: t('In-memory key-value store for WordPress object caching and panel sessions.'),
      can_restart: true,
    },
    {
      id: 'fail2ban',
      name: 'Fail2Ban Intrusion Guard',
      category: 'Security',
      version: '1.0+',
      installed: fail2banInstalled,
      running: fail2banInstalled ? (serviceStates['fail2ban'] ?? true) : false,
      service_unit: 'fail2ban',
      description: t('Scans SSH, Nginx, Dovecot, and Postfix logs to ban brute-force attackers.'),
      can_restart: false,
      addon: fail2banAddon,
    },
    {
      id: 'ufw',
      name: 'UFW Packet Firewall',
      category: 'Security',
      version: 'Active',
      installed: true,
      running: true,
      service_unit: 'ufw',
      description: t('Host packet filtering firewall managed by SPanel Security & Firewall.'),
      can_restart: false,
    },
    {
      id: 'clamav',
      name: 'ClamAV Malware Engine',
      category: 'Security',
      version: '1.0+',
      installed: malwareInstalled,
      running: malwareInstalled,
      service_unit: 'clamav-daemon',
      description: t('Antivirus and malware signature scanner for website files and uploads.'),
      can_restart: false,
      addon: addonItems.find(a => a.slug === 'malware'),
    },
  ];

  const allOverviewItems = [
    ...corePackages,
    ...phpMatrix.map(p => ({
      id: `php-${p.version}`,
      name: `PHP ${p.version} (PHP-FPM)`,
      category: 'PHP Runtime',
      version: p.version,
      installed: p.installed,
      running: p.running,
      service_unit: p.service_unit,
      description: p.is_default
        ? t('Default PHP-FPM runtime ({n} website(s) assigned).', { n: p.sites_count })
        : t('Additional PHP-FPM branch ({n} website(s) assigned).', { n: p.sites_count }),
      can_restart: p.installed,
      can_install_php: !p.installed,
      can_uninstall_php: p.installed && !p.is_default,
      php_version: p.version,
      sites_count: p.sites_count,
      is_default: p.is_default,
    })),
  ];

  const categories = ['all', 'Web Server', 'PHP Runtime', 'Database', 'Cache & Queue', 'Security'];
  const filteredOverview = allOverviewItems.filter(
    item => categoryFilter === 'all' || item.category === categoryFilter
  );

  const extVersions = phpExtensions.versions || phpVersionsData.installed || [];
  const extRows = (phpExtensions.extensions || []).filter(row => {
    const extName = String(row.name || row.extension || '').toLowerCase();
    return !extFilter || extName.includes(extFilter.toLowerCase());
  });

  const installedPhpCount = phpMatrix.filter(p => p.installed).length;
  const activeServicesCount = allOverviewItems.filter(i => i.installed && i.running).length;
  const totalInstalledCount = allOverviewItems.filter(i => i.installed).length;

  const tabs = [
    ['overview', 'Software Stack', Layers],
    ['php', 'PHP Versions & Extensions', Code2],
    ['runtimes', 'Runtimes & Addons', Boxes],
    ['logs', `Build & Action Logs (${buildLogs.length})`, TerminalIcon],
  ];

  return (
    <div className="section">
      {/* Page Header */}
      <div className="section-title" style={{ marginBottom: '14px' }}>
        <div>
          <h2 style={{ display: 'flex', alignItems: 'center', gap: '10px', margin: 0 }}>
            <Wrench size={19} />
            <span>{t('CustomBuild 2.0 — Server Software Manager')}</span>
            {appVersion && <span className="badge ok">SPanel v{appVersion}</span>}
          </h2>
          <p className="hint" style={{ marginTop: '4px' }}>
            {t('Manage web server, PHP-FPM versions, PHP extensions, Node.js/Docker runtimes, and system packages.')}
          </p>
        </div>
        <div className="cb-toolbar-actions">
          <button type="button" className="secondary-light" disabled={isBusy} onClick={() => loadStatus(false)}>
            <RefreshCw size={14} className={isBusy && !busyKey ? 'spin' : ''} /> {t('Refresh')}
          </button>
          <button type="button" className="secondary" disabled={isBusy} onClick={handleRetunePools}>
            {busyKey === 'retune-pools' ? <RefreshCw size={14} className="spin" /> : <Sparkles size={14} />}
            {busyKey === 'retune-pools' ? t('Rebuilding...') : t('Rebuild PHP Pools')}
          </button>
          <button type="button" disabled={isBusy} onClick={handleOsUpdate}>
            {busyKey === 'os-update' ? <RefreshCw size={14} className="spin" /> : <Download size={14} />}
            {busyKey === 'os-update' ? t('Updating...') : t('Update System Packages')}
          </button>
        </div>
      </div>

      {/* Top KPI Summary Strip */}
      <div className="cb-stats-strip">
        <div className="cb-stat-box">
          <div className="cb-stat-icon blue"><Server size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Web & Proxy Engine')}</span>
            <strong>Nginx 1.24+ <small className="cb-stat-sub">HTTP/2 · FastCGI</small></strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon indigo"><Code2 size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('PHP-FPM Branches')}</span>
            <strong>
              {installedPhpCount} / {phpMatrix.length} {t('Installed')}
              <small className="cb-stat-sub">{(phpVersionsData.installed || []).join(', ')}</small>
            </strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon emerald"><Database size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Data & Object Cache')}</span>
            <strong>MariaDB + Redis <small className="cb-stat-sub">10.11 LTS · v7.0+</small></strong>
          </div>
        </div>
        <div className="cb-stat-box">
          <div className="cb-stat-icon rose"><Shield size={18} /></div>
          <div className="cb-stat-info">
            <span className="cb-stat-label">{t('Active Daemons')}</span>
            <strong>
              {activeServicesCount} / {totalInstalledCount} {t('Running')}
              <small className="cb-stat-sub">Systemd Managed</small>
            </strong>
          </div>
        </div>
      </div>

      {/* Native SPanel Underlined Tabs */}
      <div className="segmented-control backup-tabs" role="tablist" aria-label={t('CustomBuild sections')}>
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            className={activeTab === id ? 'active' : ''}
            onClick={() => setActiveTab(id)}
          >
            <Icon size={14} />
            {t(label)}
          </button>
        ))}
      </div>

      {/* TAB 1: SOFTWARE STACK */}
      {activeTab === 'overview' && (
        <div style={{ display: 'grid', gap: '16px', marginTop: '6px' }}>
          <div className="cb-filter-bar">
            <div className="cb-filter-pills">
              {categories.map(cat => (
                <button
                  key={cat}
                  type="button"
                  className={`cb-pill ${categoryFilter === cat ? 'active' : ''}`}
                  onClick={() => setCategoryFilter(cat)}
                >
                  {cat === 'all' ? t('All Components') : t(cat)}
                </button>
              ))}
            </div>
            <span className="hint">{t('Showing {n} component(s)', { n: filteredOverview.length })}</span>
          </div>

          <div className="cb-grid">
            {filteredOverview.map(pkg => {
              const { Icon: CatIcon, tone } = getCategoryVisual(pkg.category);
              const statusClass = pkg.installed ? (pkg.running ? 'running' : 'stopped') : 'uninstalled';
              const statusText = pkg.installed ? (pkg.running ? t('Running') : t('Stopped')) : t('Not Installed');
              const isInstallingThisPhp = pkg.php_version && busyKey === `install-php-${pkg.php_version}`;
              const isUninstallingThisPhp = pkg.php_version && busyKey === `uninstall-php-${pkg.php_version}`;
              const isTogglingThisAddon = pkg.addon && busyKey === `addon-${pkg.addon.slug}`;
              const isRestartingThis = pkg.service_unit && busyKey === `restart-${pkg.service_unit}`;

              return (
                <div className={`cb-card ${pkg.installed ? '' : 'is-uninstalled'}`} key={pkg.id}>
                  <div className="cb-card-top">
                    <div className="cb-card-header">
                      <div className={`cb-card-icon ${tone}`}>
                        <CatIcon size={18} />
                      </div>
                      <div className="cb-card-heading">
                        <div className="cb-card-title-row">
                          <h4 className="cb-card-title" title={pkg.name}>{pkg.name}</h4>
                        </div>
                        <div className="cb-card-meta">
                          <span>{t(pkg.category)}</span>
                          <span className="cb-dot">•</span>
                          <code className="cb-ver-tag">v{pkg.version}</code>
                          {pkg.is_default && (
                            <>
                              <span className="cb-dot">•</span>
                              <span className="cb-default-tag">{t('Default')}</span>
                            </>
                          )}
                        </div>
                      </div>
                      <span className={`cb-status-pill ${statusClass}`}>
                        <span className="cb-status-dot" />
                        {statusText}
                      </span>
                    </div>

                    <p className="cb-card-desc">{pkg.description}</p>
                  </div>

                  <div className="cb-card-footer">
                    <div className="cb-card-unit">
                      {pkg.service_unit ? (
                        <code title={t('Systemd service unit')}>{pkg.service_unit}</code>
                      ) : (
                        <span className="hint">{t('System managed')}</span>
                      )}
                    </div>

                    <div className="cb-card-actions">
                      {pkg.can_restart && pkg.service_unit && (
                        <button
                          type="button"
                          className="cb-btn secondary"
                          disabled={isBusy}
                          onClick={() => handleRestartService(pkg.service_unit, pkg.name)}
                        >
                          {isRestartingThis ? <RefreshCw size={13} className="spin" /> : <RotateCcw size={13} />}
                          {isRestartingThis ? t('Restarting...') : t('Restart')}
                        </button>
                      )}
                      {pkg.can_uninstall_php && (
                        <button
                          type="button"
                          className="cb-btn danger"
                          disabled={isBusy || pkg.sites_count > 0}
                          title={pkg.sites_count > 0 ? t('Cannot uninstall while {n} website(s) use PHP {v}', { n: pkg.sites_count, v: pkg.php_version }) : t('Uninstall PHP {v}', { v: pkg.php_version })}
                          onClick={() => handleUninstallPhp(pkg.php_version, pkg.sites_count)}
                        >
                          {isUninstallingThisPhp ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                          {isUninstallingThisPhp ? t('Uninstalling...') : t('Uninstall')}
                        </button>
                      )}
                      {pkg.can_install_php && (
                        <button
                          type="button"
                          className="cb-btn primary"
                          disabled={isBusy}
                          onClick={() => handleInstallPhp(pkg.php_version)}
                        >
                          {isInstallingThisPhp ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                          {isInstallingThisPhp
                            ? t('Installing PHP {v}...', { v: pkg.php_version })
                            : `${t('Install PHP')} ${pkg.php_version}`}
                        </button>
                      )}
                      {pkg.addon && (
                        pkg.installed ? (
                          <button
                            type="button"
                            className="cb-btn danger"
                            disabled={isBusy}
                            onClick={() => handleToggleAddon(pkg.addon, false)}
                          >
                            {isTogglingThisAddon ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                            {isTogglingThisAddon ? t('Removing...') : t('Remove')}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="cb-btn primary"
                            disabled={isBusy}
                            onClick={() => handleToggleAddon(pkg.addon, true)}
                          >
                            {isTogglingThisAddon ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                            {isTogglingThisAddon ? t('Installing...') : t('Install')}
                          </button>
                        )
                      )}
                      {!pkg.can_restart && !pkg.can_install_php && !pkg.addon && (
                        <span className="cb-kernel-tag">{t('Active in Kernel')}</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* TAB 2: PHP VERSIONS & EXTENSIONS */}
      {activeTab === 'php' && (
        <div style={{ display: 'grid', gap: '22px', marginTop: '6px' }}>
          <div>
            <div className="backup-panel-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', marginBottom: '14px' }}>
              <div>
                <h3 style={{ margin: 0 }}>{t('PHP-FPM Branches')}</h3>
                <p className="hint" style={{ margin: '2px 0 0' }}>{t('Install multiple PHP versions side-by-side and assign per website.')}</p>
              </div>
              <button type="button" className="cb-btn secondary" disabled={isBusy} onClick={handleRetunePools}>
                {busyKey === 'retune-pools' ? <RefreshCw size={14} className="spin" /> : <Sparkles size={14} />}
                {busyKey === 'retune-pools' ? t('Rebuilding...') : t('Rebuild & Tune FPM Pools')}
              </button>
            </div>

            <div className="cb-grid">
              {phpMatrix.map(p => {
                const statusClass = p.installed ? (p.running ? 'running' : 'stopped') : 'uninstalled';
                const statusText = p.installed ? (p.running ? t('Running') : t('Stopped')) : t('Not Installed');
                const isInstallingThis = busyKey === `install-php-${p.version}`;
                const isUninstallingThis = busyKey === `uninstall-php-${p.version}`;
                const isRestartingThis = busyKey === `restart-${p.service_unit}`;
                return (
                  <div className={`cb-card ${p.installed ? '' : 'is-uninstalled'}`} key={p.version}>
                    <div className="cb-card-top">
                      <div className="cb-card-header">
                        <div className="cb-card-icon indigo">
                          <Code2 size={18} />
                        </div>
                        <div className="cb-card-heading">
                          <div className="cb-card-title-row">
                            <h4 className="cb-card-title">PHP {p.version} (PHP-FPM)</h4>
                          </div>
                          <div className="cb-card-meta">
                            <code>{p.service_unit}</code>
                            {p.is_default && (
                              <>
                                <span className="cb-dot">•</span>
                                <span className="cb-default-tag">{t('Default')}</span>
                              </>
                            )}
                          </div>
                        </div>
                        <span className={`cb-status-pill ${statusClass}`}>
                          <span className="cb-status-dot" />
                          {statusText}
                        </span>
                      </div>
                      <p className="cb-card-desc">
                        {p.is_default
                          ? t('Primary PHP-FPM runtime branch ({n} website(s) assigned).', { n: p.sites_count })
                          : t(' isolated PHP-FPM worker pool branch ({n} website(s) assigned).', { n: p.sites_count })}
                      </p>
                    </div>

                    <div className="cb-card-footer">
                      <div className="cb-card-unit">
                        <span className="hint">{t('{n} website(s)', { n: p.sites_count })}</span>
                      </div>
                      <div className="cb-card-actions">
                        {p.installed ? (
                          <>
                            <button
                              type="button"
                              className="cb-btn secondary"
                              disabled={isBusy}
                              onClick={() => handleRestartService(p.service_unit, `PHP ${p.version}-FPM`)}
                            >
                              {isRestartingThis ? <RefreshCw size={13} className="spin" /> : <RotateCcw size={13} />}
                              {isRestartingThis ? t('Restarting...') : t('Restart FPM')}
                            </button>
                            {!p.is_default && (
                              <button
                                type="button"
                                className="cb-btn danger"
                                disabled={isBusy || p.sites_count > 0}
                                title={p.sites_count > 0 ? t('Cannot uninstall while {n} website(s) use PHP {v}', { n: p.sites_count, v: p.version }) : t('Uninstall PHP {v}', { v: p.version })}
                                onClick={() => handleUninstallPhp(p.version, p.sites_count)}
                              >
                                {isUninstallingThis ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                                {isUninstallingThis ? t('Uninstalling...') : t('Uninstall')}
                              </button>
                            )}
                          </>
                        ) : (
                          <button
                            type="button"
                            className="cb-btn primary"
                            disabled={isBusy}
                            onClick={() => handleInstallPhp(p.version)}
                          >
                            {isInstallingThis ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                            {isInstallingThis
                              ? t('Installing PHP {v}...', { v: p.version })
                              : `${t('Install PHP')} ${p.version}`}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div className="php-ext-card" style={{ marginTop: 0 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
              <div>
                <h3>{t('PHP Extensions Matrix')}</h3>
                <p className="hint">{t('Click + on any missing cell to install that extension for a specific PHP version.')}</p>
              </div>
              <input
                value={extFilter}
                onChange={e => setExtFilter(e.target.value)}
                placeholder={t('Filter extensions (e.g. redis, imagick, intl)...')}
                style={{ maxWidth: '280px' }}
              />
            </div>

            {extRows.length > 0 ? (
              <div className="php-ext-table" role="table" style={{ '--php-cols': Math.max(extVersions.length, 1) }}>
                <div className="php-ext-row php-ext-head" role="row">
                  <span role="columnheader">{t('Extension')}</span>
                  {extVersions.map(v => <span role="columnheader" key={v}>PHP {v}</span>)}
                  <span role="columnheader" className="php-ext-all">{t('Action')}</span>
                </div>
                {extRows.map(row => {
                  const extName = row.name || row.extension;
                  const verMap = row.versions || row.installed || {};
                  const missingVers = extVersions.filter(v => verMap[v] === 'available' || verMap[v] === false);
                  const isInstallingAll = busyKey === `ext-all-${extName}`;
                  return (
                    <div className="php-ext-row" role="row" key={extName}>
                      <code role="cell">{extName}</code>
                      {extVersions.map(v => {
                        const state = verMap[v];
                        const isInstalled = state === 'installed' || state === 'builtin' || state === true;
                        const isUnavailable = state === 'unavailable';
                        const isInstallingCell = busyKey === `ext-${v}-${extName}` || isInstallingAll;
                        return (
                          <span role="cell" key={v}>
                            {isInstalled && (
                              <span className="badge ok" title={state === 'builtin' ? t('Built into PHP') : `php${v}-${extName} installed`}>
                                <Check size={12} />
                              </span>
                            )}
                            {!isInstalled && !isUnavailable && (
                              <button
                                type="button"
                                className="mini secondary php-ext-install"
                                disabled={isBusy}
                                title={`${t('Install')} php${v}-${extName}`}
                                onClick={() => handleInstallPhpExt(v, extName)}
                              >
                                {isInstallingCell ? <RefreshCw size={13} className="spin" /> : <Plus size={14} />}
                              </button>
                            )}
                            {isUnavailable && (
                              <span className="php-ext-none" title={t('Not in the package repository for this version')}>—</span>
                            )}
                          </span>
                        );
                      })}
                      <span role="cell" className="php-ext-all">
                        {missingVers.length > 0 ? (
                          <button
                            type="button"
                            className="cb-btn secondary"
                            disabled={isBusy}
                            onClick={() => handleInstallPhpExtAll(extName, missingVers)}
                          >
                            {isInstallingAll && <RefreshCw size={12} className="spin" />}
                            {isInstallingAll ? t('Installing...') : t('All versions')}
                          </button>
                        ) : (
                          <span className="hint" style={{ fontSize: '12px' }}>{t('Installed')}</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="hint">{t('No matching PHP extensions found.')}</p>
            )}
          </div>
        </div>
      )}

      {/* TAB 3: APPLICATION RUNTIMES & ADDONS */}
      {activeTab === 'runtimes' && (
        <div style={{ display: 'grid', gap: '22px', marginTop: '6px' }}>
          <div>
            <h3 style={{ margin: '0 0 4px' }}>{t('Container & JavaScript Runtimes')}</h3>
            <p className="hint" style={{ margin: '0 0 14px' }}>{t('Install Docker Engine and Node.js LTS versions for modern web applications.')}</p>
            <div className="cb-grid">
              <div className={`cb-card ${runtimes?.docker?.installed ? '' : 'is-uninstalled'}`}>
                <div className="cb-card-top">
                  <div className="cb-card-header">
                    <div className="cb-card-icon blue">
                      <Boxes size={18} />
                    </div>
                    <div className="cb-card-heading">
                      <div className="cb-card-title-row">
                        <h4 className="cb-card-title">Docker Engine & Compose</h4>
                      </div>
                      <div className="cb-card-meta">
                        <span>Container Runtime</span>
                        <span className="cb-dot">•</span>
                        <code className="cb-ver-tag">{runtimes?.docker?.version || 'docker.io'}</code>
                      </div>
                    </div>
                    <span className={`cb-status-pill ${runtimes?.docker?.installed ? 'running' : 'uninstalled'}`}>
                      <span className="cb-status-dot" />
                      {runtimes?.docker?.installed ? t('Installed') : t('Not Installed')}
                    </span>
                  </div>
                  <p className="cb-card-desc">
                    {t('Container runtime for isolated Docker & Docker Compose web applications.')}
                  </p>
                </div>
                <div className="cb-card-footer">
                  <div className="cb-card-unit">
                    <code>docker.service</code>
                  </div>
                  <div className="cb-card-actions">
                    {!runtimes?.docker?.installed ? (
                      <button type="button" className="cb-btn primary" disabled={isBusy} onClick={handleInstallDocker}>
                        {busyKey === 'docker' ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                        {busyKey === 'docker' ? t('Installing Docker...') : t('Install Docker')}
                      </button>
                    ) : (
                      <span className="cb-kernel-tag">{t('Ready')}</span>
                    )}
                  </div>
                </div>
              </div>

              {(runtimes?.node?.supported || [18, 20, 22]).map(major => {
                const installedList = runtimes?.node?.installed || [];
                const found = installedList.find(n => Number(n.major || n) === Number(major));
                const isInst = !!found;
                const isInstallingNode = busyKey === `node-${major}`;
                return (
                  <div className={`cb-card ${isInst ? '' : 'is-uninstalled'}`} key={major}>
                    <div className="cb-card-top">
                      <div className="cb-card-header">
                        <div className="cb-card-icon emerald">
                          <Cpu size={18} />
                        </div>
                        <div className="cb-card-heading">
                          <div className="cb-card-title-row">
                            <h4 className="cb-card-title">Node.js {major}.x LTS</h4>
                          </div>
                          <div className="cb-card-meta">
                            <span>JS Runtime</span>
                            <span className="cb-dot">•</span>
                            <code className="cb-ver-tag">{found?.version || `v${major}.x`}</code>
                          </div>
                        </div>
                        <span className={`cb-status-pill ${isInst ? 'running' : 'uninstalled'}`}>
                          <span className="cb-status-dot" />
                          {isInst ? t('Installed') : t('Not Installed')}
                        </span>
                      </div>
                      <p className="cb-card-desc">
                        {t('JavaScript runtime for Next.js, Nuxt, Express, and Node apps.')}
                      </p>
                    </div>
                    <div className="cb-card-footer">
                      <div className="cb-card-unit">
                        <code>nodejs-{major}</code>
                      </div>
                      <div className="cb-card-actions">
                        <button
                          type="button"
                          className={`cb-btn ${isInst ? 'secondary' : 'primary'}`}
                          disabled={isBusy}
                          onClick={() => handleInstallNode(major)}
                        >
                          {isInstallingNode ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                          {isInstallingNode
                            ? t('Installing...')
                            : (isInst ? t('Reinstall / Update') : t('Install Node.js'))}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          <div>
            <h3 style={{ margin: '0 0 4px' }}>{t('SPanel Feature Addons')}</h3>
            <p className="hint" style={{ margin: '0 0 14px' }}>{t('Optional server stacks managed by SPanel.')}</p>
            <div className="cb-grid">
              {addonItems.map(addon => {
                const isTogglingAddon = busyKey === `addon-${addon.slug}`;
                return (
                  <div className={`cb-card ${addon.installed ? '' : 'is-uninstalled'}`} key={addon.slug}>
                    <div className="cb-card-top">
                      <div className="cb-card-header">
                        <div className="cb-card-icon amber">
                          <HardDrive size={18} />
                        </div>
                        <div className="cb-card-heading">
                          <div className="cb-card-title-row">
                            <h4 className="cb-card-title">{t(addon.name)}</h4>
                          </div>
                          <div className="cb-card-meta">
                            <span>SPanel Addon</span>
                            <span className="cb-dot">•</span>
                            <code className="cb-ver-tag">v{addon.installed ? (addon.installed_version || addon.version) : addon.version}</code>
                          </div>
                        </div>
                        <span className={`cb-status-pill ${addon.installed ? 'running' : 'uninstalled'}`}>
                          <span className="cb-status-dot" />
                          {addon.installed ? t('Installed') : t('Not Installed')}
                        </span>
                      </div>
                      <p className="cb-card-desc">{t(addon.summary)}</p>
                    </div>
                    <div className="cb-card-footer">
                      <div className="cb-card-unit">
                        <code>addon:{addon.slug}</code>
                      </div>
                      <div className="cb-card-actions">
                        {addon.installed ? (
                          <button
                            type="button"
                            className="cb-btn danger"
                            disabled={isBusy}
                            onClick={() => handleToggleAddon(addon, false)}
                          >
                            {isTogglingAddon ? <RefreshCw size={13} className="spin" /> : <Trash2 size={13} />}
                            {isTogglingAddon ? t('Removing...') : t('Remove')}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="cb-btn primary"
                            disabled={isBusy}
                            onClick={() => handleToggleAddon(addon, true)}
                          >
                            {isTogglingAddon ? <RefreshCw size={13} className="spin" /> : <Download size={13} />}
                            {isTogglingAddon ? t('Installing...') : t('Install')}
                          </button>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: BUILD & ACTION LOGS */}
      {activeTab === 'logs' && (
        <div style={{ display: 'grid', gap: '12px', marginTop: '6px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
            <p className="hint" style={{ margin: 0 }}>{t('Recent CustomBuild operations and command output in this session.')}</p>
            {buildLogs.length > 0 && (
              <button
                type="button"
                className="cb-btn secondary"
                onClick={() => {
                  setBuildLogs([]);
                  saveStoredLogs([]);
                }}
              >
                <Trash2 size={13} /> {t('Clear logs')}
              </button>
            )}
          </div>

          {buildLogs.length === 0 ? (
            <div style={{ padding: '32px', textAlign: 'center', border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', background: 'var(--surface-alt)' }}>
              <TerminalIcon size={28} style={{ opacity: 0.4, marginBottom: '8px' }} />
              <p className="hint" style={{ margin: 0 }}>{t('No CustomBuild operations executed in this session yet.')}</p>
            </div>
          ) : (
            buildLogs.map(log => (
              <div key={log.id} style={{ border: '1px solid var(--border)', borderRadius: 'var(--radius-md)', overflow: 'hidden', background: 'var(--surface)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '10px 14px', background: 'var(--surface-alt)', borderBottom: '1px solid var(--border)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {log.status === 'ok' ? <CheckCircle size={15} color="var(--success)" /> : <AlertCircle size={15} color="var(--danger)" />}
                    <strong>{log.title}</strong>
                  </div>
                  <span className="hint" style={{ fontSize: '12px' }}>{new Date(log.timestamp).toLocaleString()}</span>
                </div>
                <pre style={{ margin: 0, padding: '12px 14px', fontSize: '12px', maxHeight: '240px', overflow: 'auto', background: '#0f172a', color: '#e2e8f0', fontFamily: 'monospace' }}>
                  {log.output}
                </pre>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
