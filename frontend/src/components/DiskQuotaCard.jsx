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
    <div className="card" style={{ marginBottom: '14px', padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
      <div style={{ minWidth: 0 }}>
        <strong style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', fontSize: '13.5px' }}>
          <HardDrive size={15} /> {t('Disk quota enforcement')}
          <span className={kernel ? 'badge ok' : 'badge warn'}>{kernel ? t('Kernel') : t('Panel only')}</span>
          <span className="badge">{t(STATE_LABELS[quota.state] || quota.state)}</span>
        </strong>
        <p className="hint" style={{ margin: '4px 0 0' }}>
          {quota.mount ? `${quota.mount} (${quota.fstype || '?'}) · ` : ''}{quota.hint}
        </p>
      </div>
      <div style={{ display: 'flex', gap: '8px' }}>
        <button type="button" className="mini secondary-light" disabled={working || busy} onClick={load}>
          <RefreshCw size={13} /> {t('Refresh')}
        </button>
        {quota.can_enable && (
          <button type="button" className="mini" disabled={working || busy} onClick={enable}>
            {t('Enable kernel quota')}
          </button>
        )}
      </div>
    </div>
  );
}
