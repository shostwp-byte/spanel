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

function formatQuotaHint(quota) {
  if (!quota) return '';
  const mount = quota.mount || '/home';
  if (quota.reconciling) {
    return t('Initializing quota files and switching on kernel quota on {mount}...', { mount });
  }
  switch (quota.state) {
    case 'active':
      return t("Kernel quota enforces each account's disk and inode limits on {mount}.", { mount });
    case 'needs-quotaon':
      return t('Quota options are mounted on {mount}; enable kernel quota to build the quota files and switch it on.', { mount });
    case 'remount-required':
      return t('Quota options are in /etc/fstab for {mount}. Reboot the server (or run mount -o remount {mount}); SPanel will automatically finish switching kernel quota on.', { mount });
    case 'kernel-cmdline-required':
      return t('/ is XFS: add rootflags=uquota to GRUB_CMDLINE_LINUX in /etc/default/grub, run update-grub and reboot.');
    case 'not-configured':
      return t("Only the panel's own accounting applies; PHP and SFTP can write past the limit.");
    case 'unsupported':
      return t('Kernel quota needs ext4 or xfs; {mount} is on {fstype}.', { mount, fstype: quota.fstype || 'unknown' });
    default:
      return quota.hint || '';
  }
}

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

  useEffect(() => {
    if (!quota?.reconciling) return undefined;
    const timer = setTimeout(() => {
      load();
    }, 3000);
    return () => clearTimeout(timer);
  }, [quota?.reconciling, quota?.state]);

  async function enable() {
    const mount = quota?.mount || '/home';
    const promptText = quota?.state === 'needs-quotaon'
      ? t('Build the quota files and switch on kernel disk quota enforcement for {mount} now?', { mount })
      : t('Install the quota tools and add user quota to /etc/fstab for {mount}? Nothing is remounted or rebooted; if a reboot is needed, SPanel will finish switching kernel quota on automatically after reboot.', { mount });
    if (!window.confirm(promptText)) return;
    setWorking(true);
    try {
      const res = await request('/resources/disk-quota/enable', { method: 'POST' }, t('Enabling kernel disk quota...'));
      if (res) {
        setQuota(res);
        setNotice(res.mode === 'kernel' ? t('Kernel disk quota is active.') : formatQuotaHint(res) || t('Disk quota settings saved.'));
      }
    } finally {
      setWorking(false);
    }
  }

  if (!quota) return null;
  const kernel = quota.mode === 'kernel';
  const reconciling = Boolean(quota.reconciling);
  const hintText = formatQuotaHint(quota);
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
              {reconciling ? t('Switching on...') : t(STATE_LABELS[quota.state] || quota.state)}
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
            {hintText}
          </p>
          {!kernel && !reconciling && quota.last_error && (
            <p style={{ margin: '2px 0 0', fontSize: '12px', color: 'var(--danger, #dc2626)', lineHeight: 1.35 }}>
              {quota.last_error}
            </p>
          )}
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0, marginLeft: 'auto' }}>
        <button
          type="button"
          className="mini secondary-light"
          style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
          disabled={working || busy || reconciling}
          onClick={load}
        >
          <RefreshCw size={13} /> {t('Refresh')}
        </button>
        {quota.can_enable && (
          <button
            type="button"
            className="mini primary"
            data-sp-loading={reconciling ? 'true' : undefined}
            aria-busy={reconciling ? 'true' : undefined}
            style={{ display: 'inline-flex', alignItems: 'center', gap: '6px' }}
            disabled={working || busy || reconciling}
            onClick={enable}
          >
            {reconciling ? t('Switching on...') : t('Enable kernel quota')}
          </button>
        )}
      </div>
    </div>
  );
}
