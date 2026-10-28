import React, { useEffect, useState } from 'react';
import { HardDrive, RefreshCw } from 'lucide-react';
import { t } from '../i18n.js';

const STATE_LABELS = {
  active: 'Kernel quota active',
  'needs-quotaon': 'Mounted, not switched on yet',
  'remount-required': 'Remount or reboot required',
  'kernel-cmdline-required': 'Kernel command line change required',
  'not-configured': 'Not configured',
  unsupported: 'Unsupported filesystem',
  unknown: 'Unknown',
};

// Which disk quota actually holds: the kernel's (disk space + inodes per
// account, nothing can write past it) or only the panel's own accounting.
export function DiskQuotaCard({ request, setNotice, busy }) {
  const [quota, setQuota] = useState(null);
  const [working, setWorking] = useState(false);

  async function load() {
    const res = await request('/resources/disk-quota', { silent: true });
    if (res) setQuota(res);
  }

  useEffect(() => {
    load();
  }, []);

  async function enable() {
    if (!window.confirm(t('Install the quota tools and add user quota to /etc/fstab for {mount}? Nothing is remounted or rebooted; if a remount is needed the panel says so.', { mount: quota?.mount || '/home' }))) return;
    setWorking(true);
    try {
      const res = await request('/resources/disk-quota/enable', { method: 'POST' }, t('Enabling kernel disk quota...'));
      if (res) {
        setQuota(res);
        setNotice(res.mode === 'kernel' ? t('Kernel disk quota is active.') : res.hint || t('Disk quota settings saved.'));
      }
    } finally {
      setWorking(false);
    }
  }

  if (!quota) return null;
  const kernel = quota.mode === 'kernel';
  return (
    <div
      className="disk-quota-banner"
      style={{
        marginBottom: '16px',
        padding: '12px 16px',
        minHeight: 0,
        display: 'flex',
        flexDirection: 'row',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '14px',
        flexWrap: 'wrap',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: '12px',
        boxShadow: 'var(--shadow-sm)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flex: '1 1 420px', minWidth: 0 }}>
        <div className={`cb-stat-icon ${kernel ? 'emerald' : 'amber'}`} style={{ flexShrink: 0 }}>
          <HardDrive size={18} />
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <strong style={{ fontSize: '13.5px', fontWeight: 700, color: 'var(--text)' }}>
              {t('Disk quota enforcement')}
            </strong>
            <span className={kernel ? 'badge ok' : 'badge warn'}>
              {kernel ? t('Kernel') : t('Panel only')}
            </span>
            <span className="badge">
              {t(STATE_LABELS[quota.state] || quota.state)}
            </span>
            {quota.mount && (
              <code
                style={{
                  fontSize: '11.5px',
                  padding: '1px 7px',
                  borderRadius: '6px',
                  background: 'var(--surface-alt)',
                  border: '1px solid var(--border)',
                  color: 'var(--text-secondary)',
                }}
              >
                {quota.mount}{quota.fstype ? ` (${quota.fstype})` : ''}{quota.device ? ` · ${quota.device}` : ''}
              </code>
            )}
          </div>
          <p className="hint" style={{ margin: 0, fontSize: '12.5px', maxWidth: 'none', lineHeight: 1.4 }}>
            {quota.hint}
          </p>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0, marginLeft: 'auto' }}>
        <button
          type="button"
          className="mini secondary-light"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
          disabled={working || busy}
          onClick={load}
        >
          <RefreshCw size={13} /> {t('Refresh')}
        </button>
        {quota.can_enable && (
          <button
            type="button"
            className="mini primary"
            style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
            disabled={working || busy}
            onClick={enable}
          >
            {t('Enable kernel quota')}
          </button>
        )}
      </div>
    </div>
  );
}
