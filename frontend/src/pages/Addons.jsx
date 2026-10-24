import React, { useEffect, useState, useRef } from 'react';
import {
  Boxes, RefreshCw, Trash2, Download, AlertCircle, ExternalLink
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const ADDON_OPEN_TARGETS = {
  application: 'applications',
  notifications: 'notifications',
  dns: 'dns',
  mail: 'mail',
  malware: 'malware',
  mcp: 'mcp',
  fail2ban: 'brute_force',
};

export function AddonsPage({ request, isAdmin, addons, setAddons, loadAddons: loadAddonsProp }) {
  const {
    loading, setNotice, loadSiteApps, setSiteApps, loadDemoSettings,
    page, navigateToPage, renderDemoAccounts
  } = useAppContext();

  const busyRef = useRef(null);
  const [busySlug, setBusySlug] = useState('');
  const isBusy = Boolean(busySlug || loading);

  useEffect(() => {
    loadAddons();
  }, []);

  async function loadAddons() {
    if (loadAddonsProp) {
      await loadAddonsProp();
      if (isAdmin && loadDemoSettings) {
        await loadDemoSettings();
      }
      return;
    }
    const data = await request('/addons', { silent: true });
    const items = data?.items || [];
    setAddons({ items, can_manage: !!data?.can_manage, loaded: true });
    if (isAdmin && loadDemoSettings && items.some(item => item.slug === 'demo' && item.installed)) {
      await loadDemoSettings();
    }
  }

  async function setAddonInstalled(slug, install) {
    if (busyRef.current) return;
    const addon = addons.items.find(item => item.slug === slug);
    const label = addon?.name || slug;
    if (
      !install &&
      !confirm(
        t(
          'Remove the {name} addon? Anything it runs is stopped; its data stays where it is, and installing it again picks up from there.',
          { name: t(label) }
        )
      )
    ) {
      return;
    }
    busyRef.current = slug;
    setBusySlug(slug);
    try {
      const data = await request(
        `/addons/${slug}/${install ? 'install' : 'uninstall'}`,
        { method: 'POST' }
      );
      if (data) {
        await loadAddons();
        if (slug === 'demo' && install && isAdmin && loadDemoSettings) {
          await loadDemoSettings();
        }
        if (slug === 'application') {
          if (install) {
            await loadSiteApps({ silent: true });
          } else {
            if (setSiteApps) {
              setSiteApps({ items: [], limit: 0, used: 0, memory_ceiling_mb: 512, port_range: [21000, 21999] });
            }
            if (page === 'applications') navigateToPage('dashboard');
          }
        } else if (!install && ADDON_OPEN_TARGETS[slug] && page === ADDON_OPEN_TARGETS[slug]) {
          navigateToPage('dashboard');
        }
        setNotice(
          install
            ? `${t('{name} installed.', { name: t(label) })} ${data.next_step ? t(data.next_step) : ''}`.trim()
            : `${t('{name} removed.', { name: t(label) })} ${data.kept ? t(data.kept) : ''}`.trim()
        );
      }
    } finally {
      busyRef.current = null;
      setBusySlug('');
    }
  }

  return (
    <div className="section">
      <div className="section-title">
        <div>
          <h2>{t('Addons')}</h2>
          <p className="hint">
            {t(
              'The parts that are not in a default install. Add what you need, remove what you do not — removing turns the feature off and deletes nothing it created.'
            )}
          </p>
        </div>
        <button className="secondary-light" disabled={isBusy} onClick={loadAddons}>
          <RefreshCw size={14} className={isBusy && !busySlug ? 'spin' : ''} />
          {t('Refresh')}
        </button>
      </div>
      <div className="addon-list">
        {addons.items.map(addon => {
          const openTarget = ADDON_OPEN_TARGETS[addon.slug];
          const isThisAddonBusy = busySlug === addon.slug;
          return (
            <div className={`addon-card ${addon.installed ? 'installed' : ''}`} key={addon.slug}>
              <div className="addon-head">
                <strong>{t(addon.name)}</strong>
                <code>v{String(addon.installed ? addon.installed_version || addon.version : addon.version).replace(/^v/i, '')}</code>
                {addon.stack && (
                  <span className="hint" style={{ fontSize: '12px' }}>{addon.stack}</span>
                )}
                <span className={`badge ${addon.installed ? 'ok' : ''}`}>
                  {addon.installed ? t('Installed') : t('Not installed')}
                </span>
                {addon.installed && addon.installed_version && addon.installed_version !== addon.version && (
                  <span className="badge">v{String(addon.version).replace(/^v/i, '')} available</span>
                )}
              </div>
              <p className="addon-summary">{t(addon.summary)}</p>
              {(addon.details?.length > 0 || addon.notes?.length > 0) && (
                <details className="addon-more">
                  <summary>{t('Details')}</summary>
                  {addon.details?.length > 0 && (
                    <ul className="addon-details">
                      {addon.details.map((line, index) => (
                        <li key={index}>{t(line)}</li>
                      ))}
                    </ul>
                  )}
                  {addon.notes?.length > 0 && (
                    <div className="addon-notes">
                      <strong>
                        <AlertCircle size={13} />
                        {t('Worth knowing first')}
                      </strong>
                      <ul>
                        {addon.notes.map((line, index) => (
                          <li key={index}>{t(line)}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </details>
              )}
              {addons.can_manage && (
                <div className="addon-actions">
                  {addon.installed ? (
                    <>
                      {openTarget && (
                        <button
                          type="button"
                          className="secondary-light"
                          disabled={isBusy}
                          onClick={() => navigateToPage(openTarget)}
                        >
                          <ExternalLink size={14} />
                          {t('Open')} {t(addon.name)}
                        </button>
                      )}
                      <button
                        type="button"
                        className="danger"
                        disabled={isBusy}
                        onClick={() => setAddonInstalled(addon.slug, false)}
                      >
                        {isThisAddonBusy ? <RefreshCw size={14} className="spin" /> : <Trash2 size={14} />}
                        {isThisAddonBusy ? t('Removing...') : t('Remove')}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      disabled={isBusy}
                      onClick={() => setAddonInstalled(addon.slug, true)}
                    >
                      {isThisAddonBusy ? <RefreshCw size={14} className="spin" /> : <Download size={14} />}
                      {isThisAddonBusy ? t('Installing...') : t('Install')}
                    </button>
                  )}
                </div>
              )}
              {addon.slug === 'demo' && addon.installed && addons.can_manage && renderDemoAccounts && renderDemoAccounts()}
            </div>
          );
        })}
        {addons.loaded && addons.items.length === 0 && <EmptyState icon={Boxes} message={t('No addons yet.')} />}
      </div>
    </div>
  );
}
