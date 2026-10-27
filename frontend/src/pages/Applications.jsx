import React, { useState, useEffect } from 'react';
import { Archive, ArchiveRestore, ArrowLeft, Ban, Bot, Boxes, Check, ChevronDown, Clock, Code2, Copy, Cpu, Database, Dices, ExternalLink, Eye, FileText, FolderOpen, Globe, HardDrive, Home, Image, KeyRound, Lock, LogIn, LogOut, MemoryStick, Menu, Moon, MoveRight, Network, Pencil, Save, Search, Server, Settings as SettingsIcon, Shield, Sun, Trash2, TerminalIcon, Users, X, RefreshCw, Plus, Download, Upload, Play, Square, RotateCcw, AlertCircle, Activity, BrickWall, Bug, LockKeyhole, PackageOpen, ScrollText, ShieldAlert, CheckCircle, Zap, Bell, Mail, Send, Cloud, Inbox, Forward, ShieldCheck } from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export function ApplicationsPage({ request, API, setPage, isReseller, fileAppId, setFileAppId, setFileListPath, EMPTY_SITE_APP_DRAFT, loadSiteApps }) {
  const {
    loading, setLoading, setError, setNotice, siteApps,
    isAdmin, SITE_APP_KINDS, SITE_APP_KIND_LABELS,
    siteRuntimes, loadSiteRuntimes, composePlan, setComposePlan, composeWebPorts,
    validateCompose, installDockerEngine, pruneDocker, installNodeMajor,
    navigateToPage, setSiteType, setCreateFormOpen,
    createSiteAppId, setCreateSiteAppId,
    composeValidating, composeValidation, setComposeValidation,
    dockerPruning, dockerPruneConfirm, setDockerPruneConfirm, dockerPruneIncludeVolumes, setDockerPruneIncludeVolumes,
    nodeMajorPicker, setNodeMajorPicker,
  } = useAppContext();
  
  const [siteAppDraft, setSiteAppDraft] = useState(EMPTY_SITE_APP_DRAFT);
  const [siteAppLog, setSiteAppLog] = useState(null);
  const [siteAppEdit, setSiteAppEdit] = useState(null);
  const [siteAppEditPlan, setSiteAppEditPlan] = useState(null);

  useEffect(() => { loadSiteApps(); loadSiteRuntimes(); }, []);
  useEffect(() => {
    if (siteAppEdit && !siteAppEditPlan) {
      checkSiteAppEdit();
    }
  }, [siteAppEdit, siteAppEditPlan]);

  async function checkComposeFile() {
    const data = await validateCompose(siteAppDraft.compose_source, siteAppDraft.web_service, siteAppDraft.env, siteAppDraft.container_port);
    if (data) {
      setComposePlan(data);
      if (data.web_service && !siteAppDraft.web_service) {
        setSiteAppDraft(prev => ({ ...prev, web_service: data.web_service }));
      }
    }
  }



  function openSiteAppEdit(app) {
    if (siteAppEdit?.id === app.id) { setSiteAppEdit(null); setSiteAppEditPlan(null); return; }
    setSiteAppEditPlan(null);
    setSiteAppEdit({
      id: app.id,
      kind: app.kind,
      compose_source: app.compose_source || '',
      web_service: app.web_service || '',
      container_port: app.container_port || '',
      env: app.env || '',
    });
  }

  async function checkSiteAppEdit() {
    const data = await validateCompose(siteAppEdit.compose_source, siteAppEdit.web_service, siteAppEdit.env, siteAppEdit.container_port);
    if (data) {
      setSiteAppEditPlan(data);
      if (data.web_service && !siteAppEdit.web_service) {
        setSiteAppEdit(prev => ({ ...prev, web_service: data.web_service }));
      }
    }
  }

  async function saveSiteAppEdit(app) {
    const patch = app.kind === 'compose'
      ? {
          compose_source: siteAppEdit.compose_source,
          web_service: siteAppEdit.web_service || null,
          env: siteAppEdit.env,
          container_port: Number(siteAppEdit.container_port) || null,
        }
      : { env: siteAppEdit.env };
    const data = await request(`/site-apps/${app.id}`, { method: 'PUT', body: JSON.stringify(patch) }, t('Saving configuration...'));
    if (data) {
      setSiteAppEdit(null);
      setSiteAppEditPlan(null);
      setNotice(`Saved ${data.name}.`);
      await loadSiteApps();
    }
  }

  async function deploySiteApp(app) {
    const data = await request(`/site-apps/${app.id}/deploy`, { method: 'POST' }, `Deploying ${app.name}...`);
    if (data) {
      setNotice(data.running ? `${app.name} is running on port ${app.port}.` : `${app.name} was deployed but is not running — check the log.`);
      // What it downloaded and installed, which is otherwise invisible.
      if (data.output) setSiteAppLog({ name: `${app.name} deploy`, log: data.output });
      await loadSiteApps();
    }
  }

  async function gitDeploySiteApp(app) {
    const data = await request(`/site-apps/${app.id}/git-deploy`, { method: 'POST' }, `Git Pull & Deploying ${app.name}...`);
    if (data) {
      setNotice(data.running ? `${app.name} pulled from Git and is running on port ${app.port}.` : `${app.name} Git deploy finished — check the log.`);
      const combinedLog = [data.git_output, data.output].filter(Boolean).join('\n\n--- Runtime Deploy Output ---\n');
      if (combinedLog) setSiteAppLog({ name: `${app.name} git-deploy`, log: combinedLog });
      await loadSiteApps();
    }
  }

  function copyWebhookUrl(app) {
    const path = app.webhook_path || `/api/site-apps/webhook/${app.id}/${app.webhook_token || ''}`;
    const fullUrl = `${window.location.origin}${path}`;
    navigator.clipboard?.writeText(fullUrl);
    setNotice(t('Copied Git Webhook Auto-Deploy URL to clipboard.'));
  }

  function copyWebhookSecret(app) {
    if (!app.webhook_secret) return;
    navigator.clipboard?.writeText(app.webhook_secret);
    setNotice(t('Copied Git Webhook HMAC Secret to clipboard.'));
  }

  async function controlSiteApp(app, action) {
    const data = await request(`/site-apps/${app.id}/control`, { method: 'POST', body: JSON.stringify({ action }) }, `${action} ${app.name}...`);
    if (data) {
      setNotice(`${app.name} is ${data.running ? 'running' : 'stopped'}.`);
      await loadSiteApps();
    }
  }

  async function openSiteAppLog(app) {
    const data = await request(`/site-apps/${app.id}/logs?lines=300`, {}, `Loading ${app.name} log...`);
    if (data) setSiteAppLog({ name: app.name, log: data.log || 'No output yet.' });
  }

  async function createSiteApp() {
    const body = {
      name: siteAppDraft.name,
      kind: siteAppDraft.kind,
    };
    if (String(siteAppDraft.port).trim()) body.port = Number(siteAppDraft.port);
    if (String(siteAppDraft.memory_limit_mb).trim()) body.memory_limit_mb = Number(siteAppDraft.memory_limit_mb);
    if (siteAppDraft.env.trim()) body.env = siteAppDraft.env;
    if (siteAppDraft.kind === 'node') {
      body.start_kind = siteAppDraft.start_kind;
      body.start_arg = siteAppDraft.start_arg;
      body.node_major = siteAppDraft.node_major;
    }
    if (siteAppDraft.kind === 'python') {
      body.start_kind = ['uvicorn', 'gunicorn', 'python'].includes(siteAppDraft.start_kind) ? siteAppDraft.start_kind : 'uvicorn';
      body.start_arg = siteAppDraft.start_arg || 'main:app';
    }
    if (siteAppDraft.kind === 'docker') {
      body.image = siteAppDraft.image.trim();
      body.container_port = Number(siteAppDraft.container_port) || 3000;
      body.cpu_limit = siteAppDraft.cpu_limit;
    }
    if (siteAppDraft.kind === 'compose') {
      body.compose_source = siteAppDraft.compose_source;
      body.cpu_limit = siteAppDraft.cpu_limit;
      if (siteAppDraft.web_service) body.web_service = siteAppDraft.web_service;
      if (siteAppDraft.container_port) body.container_port = Number(siteAppDraft.container_port);
    }
    const data = await request('/site-apps', { method: 'POST', body: JSON.stringify(body) }, t('Creating application...'));
    if (data) {
      setNotice(`Application ${data.name} created. Upload your files to ${data.directory} and press Deploy.`);
      setSiteAppDraft(EMPTY_SITE_APP_DRAFT);
      setComposePlan(null);
      await loadSiteApps();
    }
  }

  async function updateSiteApp(app, patch, label = 'Updating application...') {
    const data = await request(`/site-apps/${app.id}`, { method: 'PUT', body: JSON.stringify(patch) }, label);
    if (data) {
      setNotice(`Updated ${data.name}.`);
      await loadSiteApps();
    }
  }

  async function deleteSiteApp(app) {
    if (!confirm(`Delete application ${app.name}? Its files stay on disk; only the runtime is removed.`)) return;
    const data = await request(`/site-apps/${app.id}`, { method: 'DELETE' }, t('Deleting application...'));
    if (data) {
      setNotice(`Deleted ${app.name}.`);
      await loadSiteApps();
    }
  }

  async function suggestSiteAppPort() {
    const data = await request('/site-apps/suggest-port', { silent: true });
    if (data?.port) setSiteAppDraft(prev => ({ ...prev, port: String(data.port) }));
  }

  function openAppFileManager(app) {
    if (isAdmin || isReseller) return;
    setFileAppId(String(app.id));
    setFileListPath('');
    navigateToPage('files');
  }

  function renderApplications() {
    const [portFrom, portTo] = siteApps.port_range || [21000, 21999];
    const atLimit = !isAdmin && siteApps.limit > 0 && siteApps.used >= siteApps.limit;
    const dockerReady = !!siteRuntimes.docker?.installed;
    const kindHint = (SITE_APP_KINDS.find(([value]) => value === siteAppDraft.kind) || [])[2];
    return <>
      <div className="section">
        <div className="section-title">
          <div>
            <h2>{t('Applications')}</h2>
            <p className="hint">
              {t('Each application runs on its own port under its own systemd unit. Point a website at one by setting its mode to')} <strong>{t('Application')}</strong>.
              {siteApps.limit > 0 && <> {t('Using {used} of {limit} allowed.', { used: siteApps.used, limit: siteApps.limit })}</>}
            </p>
          </div>
          <button className="secondary-light" disabled={!!loading} onClick={() => { loadSiteApps(); loadSiteRuntimes(); }}><RefreshCw size={14}/>{t('Refresh')}</button>
        </div>
        <div className="site-runtime-strip">
          <span>{t('Docker:')}{' '}<strong>{dockerReady ? (siteRuntimes.docker.version || 'installed') : 'not installed'}</strong></span>
          <span>{t('Node:')}{' '}<strong>{siteRuntimes.node_majors?.length ? siteRuntimes.node_majors.map(major => `v${major}`).join(', ') : 'system version only'}</strong></span>
          {isAdmin && !dockerReady && <button className="mini secondary-light" disabled={!!loading} onClick={installDockerEngine}>{t('Install Docker')}</button>}
          {isAdmin && <button className="mini secondary-light" disabled={!!loading} onClick={() => { const major = prompt('Install which Node major version?', '22'); if (major) installNodeMajor(major.trim()); }}>{t('Add Node version')}</button>}
        </div>
        {isAdmin && dockerReady && siteRuntimes.docker?.disk?.length > 0 && <div className="site-runtime-strip">
          <span>{t('Docker disk (whole server, not counted against customer quotas):')}</span>
          {siteRuntimes.docker.disk.map(row => <span key={row.type}>
            {row.type}: <strong>{row.size}</strong>{row.reclaimable && !row.reclaimable.startsWith('0B') ? <> · {row.reclaimable} reclaimable</> : null}
          </span>)}
          <button className="mini secondary-light" disabled={!!loading} onClick={pruneDocker}>{t('Prune unused layers')}</button>
        </div>}
        {!atLimit && <div className="site-app-form">
          <label><span>{t('Name')}</span>
            <input value={siteAppDraft.name} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, name: e.target.value }))} />
          </label>
          <label><span>{t('Runtime')}</span>
            <select
              value={siteAppDraft.kind}
              disabled={!!loading}
              onChange={e => {
                const nextKind = e.target.value;
                setSiteAppDraft(prev => ({
                  ...prev,
                  kind: nextKind,
                  start_kind: nextKind === 'python' ? 'uvicorn' : (nextKind === 'node' ? 'npm' : prev.start_kind),
                  start_arg: nextKind === 'python' ? 'main:app' : (nextKind === 'node' ? 'start' : prev.start_arg),
                }));
              }}
            >
              {SITE_APP_KINDS.map(([value, label]) => <option key={value} value={value} disabled={value === 'docker' && !dockerReady}>{t(label)}</option>)}
            </select>
          </label>
          <label><span>{t('Port')}</span>
            <input
              type="number"
              value={siteAppDraft.port}
              min={portFrom}
              max={portTo}
              disabled={!!loading}
              placeholder={`auto (${portFrom}-${portTo})`}
              onChange={e => setSiteAppDraft(prev => ({ ...prev, port: e.target.value }))}
            />
          </label>
          <label><span>{t('Memory (MB)')}</span>
            <input
              type="number"
              value={siteAppDraft.memory_limit_mb}
              min={64}
              max={siteApps.memory_ceiling_mb || 512}
              disabled={!!loading}
              placeholder={String(siteApps.memory_ceiling_mb || 512)}
              onChange={e => setSiteAppDraft(prev => ({ ...prev, memory_limit_mb: e.target.value }))}
            />
          </label>
          {siteAppDraft.kind === 'node' && <>
            <label><span>{t('Start with')}</span>
              <select value={siteAppDraft.start_kind} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, start_kind: e.target.value }))}>
                <option value="npm">npm run</option>
                <option value="npx">npx</option>
                <option value="yarn">yarn</option>
                <option value="node">node</option>
              </select>
            </label>
            <label><span>{siteAppDraft.start_kind === 'node' ? 'Entry file' : 'Script or package'}</span>
              <input value={siteAppDraft.start_arg} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, start_arg: e.target.value }))} placeholder={siteAppDraft.start_kind === 'node' ? 'server.js' : 'start'} />
            </label>
            <label><span>{t('Node version')}</span>
              <select value={siteAppDraft.node_major} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, node_major: e.target.value }))}>
                {(siteRuntimes.node_majors?.length ? siteRuntimes.node_majors : ['22']).map(major => <option key={major} value={major}>Node {major}</option>)}
              </select>
            </label>
          </>}
          {siteAppDraft.kind === 'python' && <>
            <label><span>{t('Python Runner')}</span>
              <select value={['uvicorn', 'gunicorn', 'python'].includes(siteAppDraft.start_kind) ? siteAppDraft.start_kind : 'uvicorn'} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, start_kind: e.target.value }))}>
                <option value="uvicorn">uvicorn (FastAPI / ASGI)</option>
                <option value="gunicorn">gunicorn (Flask / Django WSGI)</option>
                <option value="python">python (Custom Script)</option>
              </select>
            </label>
            <label><span>{siteAppDraft.start_kind === 'python' ? t('Entry script') : t('ASGI / WSGI module:app')}</span>
              <input value={siteAppDraft.start_arg} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, start_arg: e.target.value }))} placeholder={siteAppDraft.start_kind === 'python' ? 'app.py' : 'main:app'} />
            </label>
          </>}
          {siteAppDraft.kind === 'compose' && <>
            <label className="site-app-env"><span>docker-compose.yml</span>
              <textarea
                className="code-editor"
                rows={12}
                value={siteAppDraft.compose_source}
                disabled={!!loading}
                onChange={e => { setSiteAppDraft(prev => ({ ...prev, compose_source: e.target.value })); setComposePlan(null); }}
                placeholder={'services:\n  app:\n    image: myorg/app:1.0\n    ports: ["3000:3000"]\n  db:\n    image: postgres:16\n    volumes: ["pgdata:/var/lib/postgresql/data"]\nvolumes:\n  pgdata:'}
              />
            </label>
            {composePlan?.services?.length > 0 && <label><span>{t('Service behind the domain')}</span>
              <select value={siteAppDraft.web_service} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, web_service: e.target.value }))}>
                <option value="">{t('Choose for me')}</option>
                {composePlan.services.map(service => <option key={service.name} value={service.name}>{service.name}{service.container_port ? ` · :${service.container_port}` : ''}</option>)}
              </select>
            </label>}
            {composeWebPorts(composePlan, siteAppDraft.web_service).length > 1 && <label><span>{t('Port behind the domain')}</span>
              <select value={siteAppDraft.container_port} disabled={!!loading} onChange={e => { setSiteAppDraft(prev => ({ ...prev, container_port: e.target.value })); setComposePlan(null); }}>
                {composeWebPorts(composePlan, siteAppDraft.web_service).map(port => <option key={port} value={port}>{port}</option>)}
              </select>
            </label>}
            <label><span>{t('CPU per service')}</span>
              <input value={siteAppDraft.cpu_limit} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, cpu_limit: e.target.value }))} placeholder="1" />
            </label>
            <p className="compose-hint">{t('Where the file refers to')}{' '}<code>{'${VAR}'}</code>, set the value in the <strong>.env</strong> box below,
              exactly as an <code>.env</code> file beside <code>docker-compose.yml</code> would. For a public address
              (an OAuth callback, a webhook) use <code>{'${spanel_URL}'}</code> / <code>{'${spanel_DOMAIN}'}</code>:
              the app only ever sees its internal port, and the panel fills in the domain of the website pointing at it.</p>
          </>}
          {siteAppDraft.kind === 'docker' && <>
            <label><span>{t('Image')}</span>
              <input value={siteAppDraft.image} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, image: e.target.value }))} placeholder="n8nio/n8n:latest" />
            </label>
            <label><span>{t('Port in container')}</span>
              <input type="number" value={siteAppDraft.container_port} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, container_port: e.target.value }))} placeholder="3000" />
            </label>
            <label><span>CPU</span>
              <input value={siteAppDraft.cpu_limit} disabled={!!loading} onChange={e => setSiteAppDraft(prev => ({ ...prev, cpu_limit: e.target.value }))} placeholder="1" />
            </label>
          </>}
          <label className="site-app-env"><span>{siteAppDraft.kind === 'compose' ? '.env (KEY=value, one per line)' : 'Environment (KEY=value, one per line)'}</span>
            <textarea
              className="code-editor"
              rows={4}
              value={siteAppDraft.env}
              disabled={!!loading}
              onChange={e => setSiteAppDraft(prev => ({ ...prev, env: e.target.value }))}
              placeholder={'N8N_ENCRYPTION_KEY=...\nGENERIC_TIMEZONE=Asia/Ho_Chi_Minh'}
            />
          </label>
          <div className="site-app-form-actions">
            {siteAppDraft.kind === 'compose' && <button className="secondary-light" disabled={!!loading || !siteAppDraft.compose_source.trim()} onClick={checkComposeFile}>{t('Check file')}</button>}
            <button className="secondary-light" disabled={!!loading} onClick={suggestSiteAppPort}>{t('Pick free port')}</button>
            <button disabled={!!loading || !siteAppDraft.name.trim()} onClick={createSiteApp}><Plus size={14}/>{t('Install application')}</button>
          </div>
          {composePlan && <div className={`compose-report ${composePlan.ok ? 'ok' : 'bad'}`}>
            {composePlan.ok
              ? <p><Check size={14}/> {t('{n} service(s) will run.', { n: composePlan.services.length })} <strong>{composePlan.web_service}</strong> {t('serves the domain.')}</p>
              : <p><AlertCircle size={14}/> {t('{n} thing(s) to fix before importing:', { n: composePlan.issues.length })}</p>}
            {composePlan.issues.length > 0 && <ul>
              {composePlan.issues.map((issue, index) => <li key={index}>
                {issue.service && <code>{issue.service}</code>} {issue.message}
              </li>)}
            </ul>}
            {composePlan.notes?.length > 0 && <ul className="compose-notes">
              {composePlan.notes.map((note, index) => <li key={index}>{note}</li>)}
            </ul>}
            {composePlan.ok && <ul className="compose-services">
              {composePlan.services.map(service => <li key={service.name}>
                <code>{service.name}</code> {service.image}
                {service.web ? ' · serves the domain' : ' · internal only'}
                {service.container_port ? ` · port ${service.container_port}` : ''}
              </li>)}
            </ul>}
          </div>}
        </div>}
        {atLimit && <p className="hint">{t('This package allows {n} application(s). Delete one to install another.', { n: siteApps.limit })}</p>}
        {kindHint && <p className="hint site-apps-note">{kindHint} {t('Containers publish on 127.0.0.1 only, run as your own user with no capabilities, and are capped at the memory shown.')} {t('Images come from {list}.', { list: (siteRuntimes.allowed_registries || []).join(', ') || t('the allowed registries') })}</p>}
      </div>

      <div className="section">
        <div className="section-title">
          <div><h2>{t('Installed')}</h2><p className="hint">{t('{n} application(s)', { n: siteApps.items.length })}</p></div>
        </div>
        {siteApps.items.length === 0 && <EmptyState icon={Server} message={t('No applications yet. Install one above.')} />}
        {siteApps.items.some(app => app.webhook_path) && <p className="hint site-apps-note"><AlertCircle size={13}/> {t('Since v1.0.7 the Git webhook URL has changed and every push must be signed. A webhook set up before then is rejected: paste the new URL and the Webhook Secret into GitHub/GitLab again.')}</p>}
        <div className="site-app-list">
          {siteApps.items.map(app => <div className="site-app-item" key={app.id}>
            <div className="site-app-head">
              <strong>{app.name}</strong>
              <span className="badge">{SITE_APP_KIND_LABELS[app.kind] || app.kind}</span>
              <code>127.0.0.1:{app.port}</code>
              <span className={`badge ${app.status === 'running' ? 'ok' : app.status === 'error' ? 'bad' : ''}`}>
                {app.status === 'running' ? 'Running' : app.status === 'error' ? 'Failed' : 'Stopped'}
              </span>
              {app.websites?.length > 0 && <span className="site-app-domains">{app.websites.join(', ')}</span>}
            </div>
            {app.last_error && <p className="site-app-error">{app.last_error}</p>}
            <dl className="site-app-meta">
              <div><dt>{t('Upload code to')}</dt><dd><code>{app.directory}</code></dd></div>
              {app.kind === 'node' && <div><dt>{t('Start')}</dt><dd><code>{app.start_kind} {app.start_arg}</code></dd></div>}
              {app.kind === 'node' && <div><dt>Node</dt><dd>v{app.node_major || '22'}</dd></div>}
              {app.kind === 'python' && <div><dt>{t('Python Runner')}</dt><dd><code>{app.start_kind} {app.start_arg}</code> (.venv)</dd></div>}
              {app.kind === 'compose' && <div><dt>{t('Serves domain')}</dt><dd><code>{app.web_service}</code></dd></div>}
              {app.kind === 'docker' && <div><dt>{t('Image')}</dt><dd><code>{app.image}</code></dd></div>}
              {app.kind === 'docker' && <div><dt>{t('In container')}</dt><dd>port {app.container_port} · {app.cpu_limit} CPU</dd></div>}
              <div><dt>{t('Unit')}</dt><dd><code>{app.unit}</code></dd></div>
              {app.webhook_path && <div><dt>{t('Git Webhook')}</dt><dd><code>{app.webhook_path}</code></dd></div>}
              {app.webhook_secret && <div><dt>{t('Webhook Secret')}</dt><dd><code onClick={() => copyWebhookSecret(app)} style={{ cursor: 'pointer' }} title={t('Click to copy HMAC Secret')}>{app.webhook_secret.slice(0, 16)}…</code></dd></div>}
            </dl>
            <div className="site-app-actions">
              <div className="site-app-fields">
                <label className="site-app-port">
                  <span>{t('Port')}</span>
                  <input
                    type="number"
                    defaultValue={app.port}
                    min={portFrom}
                    max={portTo}
                    disabled={!!loading}
                    onBlur={e => {
                      const next = Number(e.target.value);
                      if (next && next !== app.port) updateSiteApp(app, { port: next }, t('Moving application port...'));
                    }}
                  />
                </label>
                <label className="site-app-port">
                  <span>{t('Memory (MB)')}</span>
                  <input
                    type="number"
                    defaultValue={app.memory_limit_mb}
                    min={64}
                    max={isAdmin ? 16384 : (siteApps.memory_ceiling_mb || 512)}
                    disabled={!!loading}
                    onBlur={e => {
                      const next = Number(e.target.value);
                      if (next && next !== app.memory_limit_mb) updateSiteApp(app, { memory_limit_mb: next }, t('Applying the new memory limit...'));
                    }}
                  />
                </label>
                {app.kind === 'docker' && <label className="site-app-port">
                  <span>CPU</span>
                  <input
                    defaultValue={app.cpu_limit}
                    disabled={!!loading}
                    onBlur={e => {
                      const next = e.target.value.trim();
                      if (next && next !== app.cpu_limit) updateSiteApp(app, { cpu_limit: next }, t('Applying the new CPU limit...'));
                    }}
                  />
                </label>}
              </div>
              <div className="site-app-buttons">
                <button className="mini secondary-light" disabled={!!loading} onClick={() => openSiteAppEdit(app)}><Pencil size={13}/> {app.kind === 'compose' ? 'Compose' : 'Environment'}</button>
                {!isAdmin && !isReseller && <button className="mini secondary-light" disabled={!!loading} onClick={() => openAppFileManager(app)}><FolderOpen size={13}/>{t('Files')}</button>}
                <button className="mini secondary-light" disabled={!!loading} onClick={() => copyWebhookUrl(app)} title={t('Copy GitHub/GitLab Push Webhook URL')}><Copy size={13}/>{t('Webhook')}</button>
                <button className="mini secondary-light" disabled={!!loading} onClick={() => gitDeploySiteApp(app)} title={t('Pull latest Git commit & redeploy')}><Download size={13}/>{t('Git Deploy')}</button>
                <button className="mini" disabled={!!loading} onClick={() => deploySiteApp(app)}><Play size={13}/>{t('Deploy')}</button>
                <button className="mini secondary-light" disabled={!!loading} onClick={() => controlSiteApp(app, 'restart')}><RotateCcw size={13}/>{t('Restart')}</button>
                <button className="mini secondary-light" disabled={!!loading} onClick={() => controlSiteApp(app, 'stop')}><Square size={13}/>{t('Stop')}</button>
                <button className="mini secondary-light" disabled={!!loading} onClick={() => openSiteAppLog(app)}><FileText size={13}/>{t('Log')}</button>
                <button className="mini danger" disabled={!!loading} onClick={() => deleteSiteApp(app)}><Trash2 size={13}/>{t('Delete')}</button>
              </div>
            </div>
            {siteAppEdit?.id === app.id && <div className="site-app-editor">
              {app.kind === 'compose' ? <>
                <label className="site-app-env"><span>docker-compose.yml</span>
                  <textarea
                    className="code-editor"
                    rows={14}
                    value={siteAppEdit.compose_source}
                    disabled={!!loading}
                    onChange={e => { setSiteAppEdit(prev => ({ ...prev, compose_source: e.target.value })); setSiteAppEditPlan(null); }}
                  />
                </label>
                <p className="compose-hint">{t('The panel reads this file and generates the one it actually runs.')}{' '}<code>{'${VAR}'}</code> comes
                  from the .env box; for a public address use <code>{'${spanel_URL}'}</code> / <code>{'${spanel_DOMAIN}'}</code>
                  {app.websites?.length > 0 ? ` (currently ${app.websites[0]})` : ' (point a website at this app first)'}.</p>
                <label className="site-app-env"><span>.env (KEY=value, one per line)</span>
                  <textarea
                    className="code-editor"
                    rows={6}
                    value={siteAppEdit.env}
                    disabled={!!loading}
                    onChange={e => { setSiteAppEdit(prev => ({ ...prev, env: e.target.value })); setSiteAppEditPlan(null); }}
                  />
                </label>
                {siteAppEditPlan?.services?.length > 0 && <label><span>{t('Service behind the domain')}</span>
                  <select value={siteAppEdit.web_service} disabled={!!loading} onChange={e => setSiteAppEdit(prev => ({ ...prev, web_service: e.target.value }))}>
                    <option value="">{t('Choose for me')}</option>
                    {siteAppEditPlan.services.map(service => <option key={service.name} value={service.name}>{service.name}{service.container_port ? ` · :${service.container_port}` : ''}</option>)}
                  </select>
                </label>}
                {composeWebPorts(siteAppEditPlan, siteAppEdit.web_service).length > 1 && <label><span>{t('Port behind the domain')}</span>
                  <select value={siteAppEdit.container_port} disabled={!!loading} onChange={e => { setSiteAppEdit(prev => ({ ...prev, container_port: e.target.value })); setSiteAppEditPlan(null); }}>
                    {composeWebPorts(siteAppEditPlan, siteAppEdit.web_service).map(port => <option key={port} value={port}>{port}</option>)}
                  </select>
                </label>}
              </> : <label className="site-app-env"><span>{t('Environment (KEY=value, one per line)')}</span>
                <textarea
                  className="code-editor"
                  rows={8}
                  value={siteAppEdit.env}
                  disabled={!!loading}
                  onChange={e => setSiteAppEdit(prev => ({ ...prev, env: e.target.value }))}
                />
              </label>}
              <div className="site-app-form-actions">
                {app.kind === 'compose' && <button className="secondary-light" disabled={!!loading || !siteAppEdit.compose_source.trim()} onClick={checkSiteAppEdit}>{t('Check file')}</button>}
                <button disabled={!!loading} onClick={() => saveSiteAppEdit(app)}><Save size={14}/>{t('Save')}</button>
                <button className="secondary-light" disabled={!!loading} onClick={() => { setSiteAppEdit(null); setSiteAppEditPlan(null); }}><X size={14}/>{t('Cancel')}</button>
              </div>
              {siteAppEditPlan && <div className={`compose-report ${siteAppEditPlan.ok ? 'ok' : 'bad'}`}>
                {siteAppEditPlan.ok
                  ? <p><Check size={14}/> {t('{n} service(s) will run.', { n: siteAppEditPlan.services.length })} <strong>{siteAppEditPlan.web_service}</strong> {t('serves the domain.')}</p>
                  : <p><AlertCircle size={14}/> {t('{n} thing(s) to fix:', { n: siteAppEditPlan.issues.length })}</p>}
                {siteAppEditPlan.issues.length > 0 && <ul>
                  {siteAppEditPlan.issues.map((issue, index) => <li key={index}>
                    {issue.service && <code>{issue.service}</code>} {issue.message}
                  </li>)}
                </ul>}
                {siteAppEditPlan.notes?.length > 0 && <ul className="compose-notes">
                  {siteAppEditPlan.notes.map((note, index) => <li key={index}>{note}</li>)}
                </ul>}
              </div>}
            </div>}
          </div>)}
        </div>
        {siteAppLog && <div className="site-app-log">
          <div className="site-app-log-head">
            <h4>{siteAppLog.name} log</h4>
            <button className="mini secondary-light" onClick={() => setSiteAppLog(null)}><X size={13}/>{t('Close')}</button>
          </div>
          <pre>{siteAppLog.log}</pre>
        </div>}
      </div>
    </>;
  }
  return renderApplications();
}
