import React, { useState, useEffect } from 'react';
import {
  Activity, AlertCircle, Check, CheckCircle, Clock, Code2, Copy, Edit2, Globe, KeyRound, Lock, Plus,
  RefreshCw, RotateCcw, Send, Server, Settings as SettingsIcon, ShieldCheck,
  Trash2, Zap, ExternalLink
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

const INTEGRATION_PLATFORMS = [
  {
    id: 'whmcs',
    label: 'WHMCS',
    defaultName: 'WHMCS Billing',
    badge: 'Server Module',
    guide: 'Trong WHMCS → System Settings → Servers → Add New Server: Chọn module "spanel", điền Hostname/IP, Port 2222 và dán mã Token bp_... vào ô Access Hash.',
  },
  {
    id: 'hostbill',
    label: 'HostBill / Blesta',
    defaultName: 'HostBill Server',
    badge: 'Provisioning Module',
    guide: 'Trong HostBill hoặc Blesta → Settings → Apps / Server Modules: Cấu hình Hostname, Port 2222 (HTTPS) và sử dụng Token bp_... làm API Token / Access Hash.',
  },
  {
    id: 'perfex',
    label: 'Perfex CRM',
    defaultName: 'Perfex CRM',
    badge: 'REST API + Webhook',
    guide: 'Perfex CRM gọi chiều vào qua Header "Authorization: Bearer bp_..." tới Base Endpoint bên dưới khi Hóa đơn/Hợp đồng hoàn tất, kết hợp cấu hình Outbound Webhook ở khung dưới để nhận sự kiện ngược lại từ SPanel.',
  },
  {
    id: 'custom',
    label: 'Custom API / n8n',
    defaultName: 'Custom Integration',
    badge: 'REST JSON',
    guide: 'Sử dụng chuẩn RESTful JSON với Bearer Token bp_... để tự động hóa tạo tài khoản, khóa/mở khóa, cấp link đăng nhập SSO 1-click và lấy thống kê dung lượng.',
  },
];

const WEBHOOK_EVENT_PRESETS = [
  { id: 'all', label: 'Tất cả sự kiện (*)', value: '*' },
  {
    id: 'provisioning',
    label: 'Chỉ sự kiện Provisioning / Billing',
    value: 'provisioning_create,provisioning_suspend,provisioning_unsuspend,provisioning_terminate,provisioning_change_package,provisioning_change_password',
  },
  {
    id: 'websites',
    label: 'Sự kiện Website & Database',
    value: 'create_website,delete_website,create_database,delete_database,issue_ssl',
  },
];

const PROVISIONING_ENDPOINTS = [
  { method: 'GET', path: '/plans', desc: 'Lấy danh sách gói Hosting (UserPackage) để đồng bộ lên hệ thống bán hàng' },
  { method: 'POST', path: '/accounts', desc: 'Tạo tài khoản Hosting mới (+ Website, WordPress, DNS, SSL tự động)' },
  { method: 'GET', path: '/accounts/{external_id}', desc: 'Truy vấn trạng thái & thông tin tài khoản theo ID đơn hàng/dịch vụ' },
  { method: 'POST', path: '/accounts/{external_id}/suspend', desc: 'Tạm khóa tài khoản Hosting & toàn bộ Website trực thuộc' },
  { method: 'POST', path: '/accounts/{external_id}/unsuspend', desc: 'Mở khóa lại tài khoản Hosting' },
  { method: 'DELETE', path: '/accounts/{external_id}?backup=true', desc: 'Hủy dịch vụ & xóa tài khoản (tùy chọn tự sao lưu trước khi xóa)' },
  { method: 'PATCH', path: '/accounts/{external_id}/package', desc: 'Nâng cấp / Hạ cấp gói tài nguyên (Upgrade / Downgrade Package)' },
  { method: 'PATCH', path: '/accounts/{external_id}/password', desc: 'Đặt lại mật khẩu đăng nhập Panel & xoay vòng mật khẩu SFTP' },
  { method: 'POST', path: '/accounts/{external_id}/login', desc: 'Tạo đường dẫn đăng nhập 1-click SSO (hiệu lực 300 giây)' },
  { method: 'GET', path: '/accounts/usage', desc: 'Đồng bộ dung lượng ổ cứng, số lượng Website & Database của toàn bộ tài khoản' },
];

export function SettingsPage({ request, isAdmin, loadPanelSettings, currentPanelHost }) {
  const {
    loading, setLoading, setError, setNotice, currentUser, loadCurrentUser, panelSettings, setPanelSettings,
    clearSession, panelSettingsTab, setPanelSettingsTab,
  } = useAppContext();

  const [panelSettingsForm, setPanelSettingsForm] = useState({
    app_name: 'spanel',
    panel_hostname: '',
    panel_port: 2222,
    ssl_enabled: false,
    primary_color: '#16a34a',
    ticket_url: '',
  });

  // API Tokens state
  const [apiTokens, setApiTokens] = useState([]);
  const [selectedPlatform, setSelectedPlatform] = useState('whmcs');
  const [newApiToken, setNewApiToken] = useState({
    name: 'WHMCS Billing',
    allowed_ips: '',
    scopes: 'provisioning:read,provisioning:write',
    expires_in_days: '',
    rate_limit_per_minute: '',
  });
  const [createdApiToken, setCreatedApiToken] = useState('');
  const [showEndpointsRef, setShowEndpointsRef] = useState(false);

  // Outbound Webhooks state
  const [webhooks, setWebhooks] = useState([]);
  const [webhookDeliveries, setWebhookDeliveries] = useState([]);
  const [whmcsDiagnostic, setWhmcsDiagnostic] = useState(null);
  const [showWebhookForm, setShowWebhookForm] = useState(false);
  const [webhookForm, setWebhookForm] = useState({
    id: null,
    url: '',
    secret: '',
    events: '*',
    is_active: true,
  });

  // Admin Account state
  const [adminAccountForm, setAdminAccountForm] = useState({
    email: '',
    current_password: '',
    password: '',
    confirm_password: '',
    code: '',
  });

  // Security Audit Trail & Cron Confinement state
  const [auditLogs, setAuditLogs] = useState([]);
  const [auditChain, setAuditChain] = useState(null);
  const [auditSearch, setAuditSearch] = useState('');
  const [cronSecurity, setCronSecurity] = useState(null);
  const [busyAudit, setBusyAudit] = useState(false);

  useEffect(() => {
    if (currentUser?.email) {
      setAdminAccountForm((prev) => ({ ...prev, email: prev.email || currentUser.email }));
    }
  }, [currentUser?.email]);

  useEffect(() => {
    if (isAdmin) {
      loadApiTokens();
      loadWebhooks();
      loadWebhookDeliveries();
      loadWhmcsDiagnostic();
      loadAuditAndCronSecurity();
      request('/panel-settings').then((data) => {
        if (data) setPanelSettings(data);
      });
    }
  }, [isAdmin]);

  async function loadAuditAndCronSecurity(searchQuery = auditSearch) {
    try {
      const qParam = searchQuery ? `?q=${encodeURIComponent(searchQuery)}&limit=100` : '?limit=100';
      const [auditRes, cronRes] = await Promise.all([
        request(`/panel-settings/audit-logs${qParam}`),
        request('/panel-settings/cron-security'),
      ]);
      if (auditRes) {
        setAuditLogs(auditRes.items || []);
        setAuditChain(auditRes.chain || null);
      }
      if (cronRes) {
        setCronSecurity(cronRes);
      }
    } catch {}
  }

  async function verifyAuditChainNow() {
    setBusyAudit(true);
    try {
      const res = await request('/panel-settings/audit-logs/verify', { method: 'POST' }, 'Đang xác thực chuỗi băm SHA-256...');
      if (res) {
        setAuditChain(res);
        if (res.valid) {
          setNotice(`Xác thực chuỗi SHA-256 thành công: Toàn vẹn 100% trên ${res.total_entries} bản ghi (Head: ${(res.head_hash || '').slice(0, 16)}...).`);
        } else {
          setError(`Cảnh báo: Phát hiện ${res.tampered_entries?.length || 1} bản ghi có dấu hiệu sai lệch mã băm SHA-256!`);
        }
        await loadAuditAndCronSecurity();
      }
    } finally {
      setBusyAudit(false);
    }
  }

  async function enforceCronConfinementNow() {
    setBusyAudit(true);
    try {
      const res = await request('/panel-settings/cron-security/enforce', { method: 'POST' }, 'Đang chuẩn hóa & bọc cô lập Cron...');
      if (res) {
        setCronSecurity(res);
        setNotice(`Đã chuẩn hóa bảo mật Cron thành công (${res.confined_jobs}/${res.total_jobs} tác vụ được bọc open_basedir + disable_functions).`);
        await loadAuditAndCronSecurity();
      }
    } finally {
      setBusyAudit(false);
    }
  }

  useEffect(() => {
    setPanelSettingsForm(formFromPanelSettings(panelSettings));
  }, [panelSettings]);

  function formFromPanelSettings(data = {}) {
    let hostname = data.panel_hostname || currentPanelHost();
    let port = Number(data.panel_port || 2222);
    if ((!hostname || !port) && data.panel_url) {
      try {
        const parsed = new URL(data.panel_url);
        hostname = hostname || parsed.hostname;
        port = port || Number(parsed.port || 2222);
      } catch {}
    }
    return {
      app_name: data.app_name || 'spanel',
      panel_hostname: hostname,
      panel_port: port,
      ssl_enabled: !!data.ssl_enabled,
      primary_color: data.primary_color || '#16a34a',
      ticket_url: data.ticket_url || '',
    };
  }

  async function savePanelSettings() {
    const wantsSsl = !!panelSettingsForm.ssl_enabled;
    const hasSsl = !!panelSettings.ssl_enabled;
    const hostname = String(panelSettingsForm.panel_hostname || '').trim();
    const port = Number(panelSettingsForm.panel_port || 2222);
    const currentHostname = panelSettings.panel_hostname || currentPanelHost();
    const hostnameChanged = hostname && hostname !== currentHostname;

    if (wantsSsl && (!hasSsl || hostnameChanged)) {
      const nameData = await request(
        '/panel-settings',
        {
          method: 'PATCH',
          body: JSON.stringify({
            app_name: panelSettingsForm.app_name,
            panel_port: port,
          }),
        },
        t('Saving panel settings...')
      );
      if (!nameData) return;
      const sslData = await request(
        '/panel-settings/ssl',
        {
          method: 'POST',
          body: JSON.stringify({ panel_hostname: hostname, panel_port: port }),
        },
        t('Installing panel SSL...')
      );
      if (sslData) {
        setPanelSettings(sslData);
        setPanelSettingsForm(formFromPanelSettings(sslData));
        setNotice(sslData.message || 'Panel SSL installed. The panel may restart in a moment.');
        if (loadPanelSettings) await loadPanelSettings();
      }
      return;
    }

    const payload =
      hasSsl && !wantsSsl
        ? {
            app_name: panelSettingsForm.app_name,
            panel_url: `http://${hostname}:${port}`,
          }
        : {
            app_name: panelSettingsForm.app_name,
            panel_hostname: hostname,
          };
    const data = await request(
      '/panel-settings',
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      t('Saving panel settings...')
    );
    if (data) {
      setPanelSettings(data);
      setPanelSettingsForm(formFromPanelSettings(data));
      setNotice(
        hasSsl && !wantsSsl
          ? 'Panel SSL disabled. The panel remains reachable by IP and port over HTTP.'
          : 'Panel settings updated.'
      );
      if (loadPanelSettings) await loadPanelSettings();
    }
  }

  async function toggleIpv6(enable) {
    const ipv6 = panelSettings.ipv6 || {};
    if (enable && !ipv6.available) {
      setError(ipv6.detail || 'This server has no IPv6 address, so this feature cannot be used.');
      return;
    }
    if (
      !confirm(
        t(
          enable
            ? "Enable IPv6 for every website and the panel?\n\nspanel adds listen [::] to every website's nginx config, checks it with nginx -t, and rolls back on any error. The panel restarts."
            : 'Disable IPv6?\n\nWebsites and the panel will accept IPv4 only. If a domain still has an AAAA record, visitors arriving over IPv6 will not get through.'
        )
      )
    )
      return;
    const data = await request(
      '/panel-settings/ipv6',
      {
        method: 'POST',
        body: JSON.stringify({ enabled: enable }),
      },
      enable ? 'Enabling IPv6...' : 'Disabling IPv6...'
    );
    if (data) {
      setPanelSettings(data);
      setNotice(data.message || (enable ? 'IPv6 enabled.' : 'IPv6 disabled.'));
    }
  }

  async function loadApiTokens() {
    const data = await request('/provisioning/v1/tokens');
    if (data) setApiTokens(data);
  }

  async function loadWebhooks() {
    try {
      const data = await request('/resellers/webhooks');
      if (data) setWebhooks(data);
    } catch {
      setWebhooks([]);
    }
  }

  async function loadWebhookDeliveries() {
    try {
      const data = await request('/resellers/webhooks/deliveries', { silent: true });
      if (data?.items) setWebhookDeliveries(data.items);
    } catch {
      setWebhookDeliveries([]);
    }
  }

  async function loadWhmcsDiagnostic(announce = false) {
    try {
      const data = await request(
        '/resellers/whmcs-diagnostic',
        announce ? {} : { silent: true },
        announce ? 'Đang kiểm tra kết nối WHMCS & Billing...' : ''
      );
      if (data) {
        setWhmcsDiagnostic(data);
        if (announce) {
          setNotice(
            data.ready
              ? `Hệ thống đạt chuẩn sẵn sàng kết nối WHMCS / Billing (${data.score}).`
              : `Hoàn tất kiểm tra WHMCS (${data.score}). Hãy xem các mục khuyến nghị bên dưới.`
          );
        }
      }
    } catch {}
  }

  async function replayWebhookDelivery(delivery) {
    const res = await request(
      `/resellers/webhooks/deliveries/${delivery.id}/replay`,
      { method: 'POST' },
      'Đang gửi lại sự kiện Webhook...'
    );
    if (res) {
      if (res.status === 'success') {
        setNotice(`Đã gửi lại sự kiện "${res.event}" thành công (HTTP ${res.status_code}, ${res.latency_ms}ms).`);
      } else {
        setError(`Gửi lại thất bại (${res.status_code || 'Timeout'}): ${res.response_preview}`);
      }
      await loadWebhookDeliveries();
      await loadWhmcsDiagnostic();
    }
  }

  async function clearWebhookDeliveriesLog() {
    if (!confirm('Xóa toàn bộ lịch sử gửi Webhook?')) return;
    const res = await request('/resellers/webhooks/deliveries', { method: 'DELETE' }, 'Đang xóa lịch sử Webhook...');
    if (res) {
      setNotice(res.message || 'Đã xóa lịch sử Webhook.');
      await loadWebhookDeliveries();
      await loadWhmcsDiagnostic();
    }
  }

  function handleSelectPlatform(platformId) {
    setSelectedPlatform(platformId);
    const found = INTEGRATION_PLATFORMS.find((p) => p.id === platformId);
    if (found) {
      setNewApiToken((prev) => ({ ...prev, name: found.defaultName }));
    }
  }

  async function createApiToken() {
    if (!newApiToken.name.trim()) {
      setError(t('Token name is required.'));
      return;
    }
    const data = await request(
      '/provisioning/v1/tokens',
      {
        method: 'POST',
        body: JSON.stringify({
          name: newApiToken.name.trim(),
          scopes: newApiToken.scopes || 'provisioning:read,provisioning:write',
          allowed_ips: newApiToken.allowed_ips.trim(),
          expires_in_days: Number(newApiToken.expires_in_days) > 0 ? Number(newApiToken.expires_in_days) : null,
          rate_limit_per_minute: Number(newApiToken.rate_limit_per_minute) > 0 ? Number(newApiToken.rate_limit_per_minute) : 0,
        }),
      },
      t('Creating API token...')
    );
    if (data) {
      setCreatedApiToken(data.token || '');
      setNotice(
        t('API token created. Copy it now; it will not be shown again.')
      );
      await loadApiTokens();
    }
  }

  async function copyToClipboard(value, noticeMsg) {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setNotice(noticeMsg || t('Copied to clipboard.'));
    } catch {
      setError(t('Copy failed. Please select and press Ctrl+C.'));
    }
  }

  async function revokeApiToken(token) {
    if (!confirm(`Thu hồi API token "${token.name}"? Các hệ thống Billing/CRM đang dùng token này sẽ ngừng kết nối.`)) return;
    const data = await request(
      `/provisioning/v1/tokens/${token.id}`,
      { method: 'DELETE' },
      `Revoking ${token.name}...`
    );
    if (data) {
      setNotice(`Đã thu hồi API token ${token.name}.`);
      await loadApiTokens();
    }
  }

  async function rotateApiToken(token) {
    const graceInput = prompt(
      `Xoay vòng (rotate) API token "${token.name}". Token cũ còn dùng được thêm bao nhiêu phút để kịp cập nhật WHMCS? (0 = ngừng ngay)`,
      '60'
    );
    if (graceInput === null) return;
    const grace = Math.max(0, Math.min(10080, parseInt(graceInput, 10) || 0));
    const data = await request(
      `/provisioning/v1/tokens/${token.id}/rotate`,
      { method: 'POST', body: JSON.stringify({ grace_minutes: grace }) },
      `Rotating ${token.name}...`
    );
    if (data) {
      setCreatedApiToken(data.token || '');
      setNotice(`Đã tạo token thay thế cho ${token.name}. Sao chép ngay, token chỉ hiển thị 1 lần.`);
      await loadApiTokens();
    }
  }

  async function saveWebhook(e) {
    e.preventDefault();
    setLoading(t('Saving...'));
    try {
      let res;
      if (webhookForm.id) {
        res = await request(`/resellers/webhooks/${webhookForm.id}`, {
          method: 'PUT',
          body: JSON.stringify(webhookForm),
        });
        if (res) setNotice(t('Webhook updated.'));
      } else {
        res = await request('/resellers/webhooks', {
          method: 'POST',
          body: JSON.stringify(webhookForm),
        });
        if (res) setNotice(t('Webhook created.'));
      }
      if (res) {
        setShowWebhookForm(false);
        await loadWebhooks();
      }
    } catch (err) {
      setError(err.message);
    }
    setLoading('');
  }

  async function testWebhookEndpoint(wh) {
    setLoading(t('Testing webhook...'));
    try {
      const res = await request(`/resellers/webhooks/${wh.id}/test`, { method: 'POST' });
      if (res) {
        setNotice(`Đã gửi sự kiện thử nghiệm thành công tới ${wh.url} (HTTP ${res.status_code || 200}, ${res.latency_ms || 0}ms).`);
      }
      await loadWebhookDeliveries();
      await loadWhmcsDiagnostic();
    } catch (err) {
      setError(err.message);
      await loadWebhookDeliveries();
    }
    setLoading('');
  }

  async function deleteWebhook(wh) {
    if (!confirm(`Xóa kết nối Webhook tới "${wh.url}"?`)) return;
    setLoading(t('Deleting...'));
    try {
      const res = await request(`/resellers/webhooks/${wh.id}`, { method: 'DELETE' });
      if (res) {
        setNotice(t('Webhook deleted.'));
        await loadWebhooks();
      }
    } catch (err) {
      setError(err.message);
    }
    setLoading('');
  }

  async function saveAdminAccount() {
    const email = String(adminAccountForm.email || '').trim();
    const password = String(adminAccountForm.password || '');
    const confirmPassword = String(adminAccountForm.confirm_password || '');
    const currentPassword = String(adminAccountForm.current_password || '');
    const code = String(adminAccountForm.code || '').trim();

    if (!email) {
      setError(t('Email is required.'));
      return;
    }
    if (password && password.length < 12) {
      setError(t('Password must be at least 12 characters.'));
      return;
    }
    if (password && password !== confirmPassword) {
      setError(t('Passwords do not match.'));
      return;
    }

    const payload = { email };
    if (password) {
      if (!currentPassword) {
        setError(t('Current password is required to change password.'));
        return;
      }
      payload.password = password;
      payload.current_password = currentPassword;
      if (currentUser?.totp_enabled) {
        if (!code) {
          setError(t('Authentication code is required.'));
          return;
        }
        payload.code = code;
      }
    }

    const data = await request(
      '/panel-settings/admin-account',
      {
        method: 'PATCH',
        body: JSON.stringify(payload),
      },
      t('Saving admin account...')
    );
    if (!data) return;
    if (data.password_changed) {
      clearSession('Password changed. Please log in again.');
      return;
    }
    setAdminAccountForm((prev) => ({
      ...prev,
      current_password: '',
      password: '',
      confirm_password: '',
      code: '',
    }));
    await loadCurrentUser({ clearOnUnauthorized: false });
    setNotice(data.message || 'Admin account updated.');
  }

  if (!isAdmin) {
    return (
      <div className="section">
        <h2>{t('Settings')}</h2>
        <p className="hint">{t('No permission.')}</p>
      </div>
    );
  }

  const tabs = [
    ['general', 'General', SettingsIcon],
    ['account', 'Admin account', Lock],
    ['api', 'API & Billing Integration', KeyRound],
    ['audit', 'Security Audit & Cron Jail', ShieldCheck],
  ];
  const activeTab = tabs.some(([id]) => id === panelSettingsTab) ? panelSettingsTab : 'general';
  const taspanel = (id, content) =>
    activeTab === id && (
      <div
        className="backup-tab-panel"
        id={`panel-settings-tab-${id}`}
        role="tabpanel"
        aria-labelledby={`panel-settings-tab-button-${id}`}
      >
        {content}
      </div>
    );

  const panelOrigin =
    panelSettingsForm.panel_hostname
      ? `${panelSettingsForm.ssl_enabled ? 'https' : 'http'}://${panelSettingsForm.panel_hostname}:${panelSettingsForm.panel_port || 2222}`
      : window.location.origin;
  const baseProvisioningUrl = `${panelOrigin}/api/provisioning/v1`;
  const activePlatformInfo =
    INTEGRATION_PLATFORMS.find((p) => p.id === selectedPlatform) || INTEGRATION_PLATFORMS[0];

  return (
    <div className="section panel-settings-page">
      <div className="segmented-control backup-tabs" role="tablist" aria-label={t('Panel settings sections')}>
        {tabs.map(([id, label, Icon]) => (
          <button
            key={id}
            type="button"
            role="tab"
            id={`panel-settings-tab-button-${id}`}
            aria-controls={`panel-settings-tab-${id}`}
            aria-selected={activeTab === id}
            className={activeTab === id ? 'active' : ''}
            onClick={() => setPanelSettingsTab(id)}
          >
            <Icon size={14} />
            {t(label)}
          </button>
        ))}
      </div>

      {/* TAB 1: GENERAL */}
      {taspanel(
        'general',
        <>
          <div className="backup-panel-title">
            <div>
              <h3>{t('General')}</h3>
              <p className="hint">{t("Panel port, hostname and the server's addresses.")}</p>
            </div>
            <button className="secondary-light" disabled={!!loading} onClick={loadPanelSettings}>
              <RefreshCw size={14} />
              {t('Refresh')}
            </button>
          </div>
          <div className="panel-settings-grid panel-settings-compact">
            <label>
              <span>{t('Panel hostname')}</span>
              <input
                value={panelSettingsForm.panel_hostname}
                onChange={(e) => setPanelSettingsForm((prev) => ({ ...prev, panel_hostname: e.target.value }))}
                placeholder="panel.domain.com"
              />
            </label>
            <label>
              <span>{t('Panel port')}</span>
              <input
                type="number"
                min="1024"
                max="65535"
                value={panelSettingsForm.panel_port}
                onChange={(e) => setPanelSettingsForm((prev) => ({ ...prev, panel_port: e.target.value }))}
                placeholder="2222"
              />
            </label>
            <label className="check-line panel-ssl-status">
              <input
                type="checkbox"
                checked={!!panelSettingsForm.ssl_enabled}
                onChange={(e) => setPanelSettingsForm((prev) => ({ ...prev, ssl_enabled: e.target.checked }))}
              />
              {t('Panel SSL')}
            </label>
            <button
              disabled={!!loading || !panelSettingsForm.panel_port || !panelSettingsForm.panel_hostname}
              onClick={savePanelSettings}
            >
              <SettingsIcon size={14} />
              {t('Save settings')}
            </button>
          </div>
          <div className="panel-net-strip">
            <div className="panel-net-row">
              <span className="panel-net-label">IPv4</span>
              <div className="panel-net-value">
                {panelSettings.server_ipv4?.length > 0 ? (
                  panelSettings.server_ipv4.map((address) => (
                    <span key={address} className="badge">
                      {address}
                    </span>
                  ))
                ) : (
                  <span className="hint">{t("Could not read the server's IPv4 address.")}</span>
                )}
              </div>
            </div>
            <div className="panel-net-row">
              <span className="panel-net-label">IPv6</span>
              <div className="panel-net-value">
                {panelSettings.ipv6?.addresses?.length > 0 ? (
                  panelSettings.ipv6.addresses.map((address) => (
                    <span key={address} className="badge">
                      {address}
                    </span>
                  ))
                ) : (
                  <span className="badge">{t('None')}</span>
                )}
                <span className={`badge ${panelSettings.ipv6?.enabled ? 'ok' : ''}`}>
                  {panelSettings.ipv6?.enabled ? 'On' : 'Off'}
                </span>
              </div>
              {panelSettings.ipv6?.enabled ? (
                <button className="secondary-light" disabled={!!loading} onClick={() => toggleIpv6(false)}>
                  {t('Disable IPv6')}
                </button>
              ) : (
                <button
                  className="secondary-light"
                  disabled={!!loading || !panelSettings.ipv6?.available}
                  onClick={() => toggleIpv6(true)}
                >
                  {t('Enable IPv6')}
                </button>
              )}
            </div>
            <span className="hint">{panelSettings.ipv6?.detail}</span>
          </div>
        </>
      )}

      {/* TAB 2: ADMIN ACCOUNT */}
      {taspanel(
        'account',
        <>
          <div className="backup-panel-title">
            <div>
              <h3>{t('Admin account')}</h3>
              <p className="hint">
                {t('A new password needs the current one, and the authenticator code when two-factor sign-in is on.')}
              </p>
            </div>
          </div>
          <div className="panel-settings-grid admin-account-grid">
            <label>
              <span>{t('Email')}</span>
              <input
                type="email"
                value={adminAccountForm.email}
                onChange={(e) => setAdminAccountForm((prev) => ({ ...prev, email: e.target.value }))}
                placeholder="admin@domain.com"
              />
            </label>
            <label>
              <span>{t('Current password')}</span>
              <input
                type="password"
                value={adminAccountForm.current_password}
                onChange={(e) => setAdminAccountForm((prev) => ({ ...prev, current_password: e.target.value }))}
                placeholder={t('Current password')}
                autoComplete="current-password"
              />
            </label>
            <label>
              <span>{t('New password')}</span>
              <input
                type="password"
                value={adminAccountForm.password}
                onChange={(e) => setAdminAccountForm((prev) => ({ ...prev, password: e.target.value }))}
                placeholder={t('New password')}
                autoComplete="new-password"
              />
            </label>
            <label>
              <span>{t('Confirm password')}</span>
              <input
                type="password"
                value={adminAccountForm.confirm_password}
                onChange={(e) => setAdminAccountForm((prev) => ({ ...prev, confirm_password: e.target.value }))}
                placeholder={t('Repeat new password')}
                autoComplete="new-password"
              />
            </label>
            <label>
              <span>{t('Authenticator code')}</span>
              <input
                value={adminAccountForm.code}
                onChange={(e) => setAdminAccountForm((prev) => ({ ...prev, code: e.target.value }))}
                placeholder="123456"
                inputMode="numeric"
                autoComplete="one-time-code"
              />
            </label>
            <button
              disabled={
                !!loading ||
                !adminAccountForm.email.trim() ||
                (!!adminAccountForm.password && adminAccountForm.password !== adminAccountForm.confirm_password)
              }
              onClick={saveAdminAccount}
            >
              <Lock size={14} />
              {t('Save account')}
            </button>
          </div>
        </>
      )}

      {/* TAB 3: API & BILLING INTEGRATION (UNIFIED INBOUND TOKENS + OUTBOUND WEBHOOKS) */}
      {taspanel(
        'api',
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* CARD 1: INBOUND PROVISIONING API TOKENS */}
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '14px', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <KeyRound size={18} style={{ color: 'var(--accent-primary)' }} />
                  1. Provisioning API Tokens (WHMCS, HostBill, Blesta, Perfex CRM)
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                  Cấp quyền cho hệ thống Billing / CRM bên ngoài gọi vào SPanel để tự động khởi tạo Hosting, khóa/mở khóa, nâng cấp gói và đăng nhập 1-click (SSO).
                </p>
              </div>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
                <button
                  type="button"
                  className="secondary-light"
                  disabled={!!loading}
                  onClick={() => loadWhmcsDiagnostic(true)}
                >
                  <ShieldCheck size={14} />
                  Kiểm tra chuẩn WHMCS
                </button>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setShowEndpointsRef((v) => !v)}
                >
                  <Code2 size={14} />
                  {showEndpointsRef ? 'Ẩn tài liệu API' : 'Xem tài liệu REST API'}
                </button>
                <button
                  type="button"
                  className="secondary-light"
                  disabled={!!loading}
                  onClick={() => {
                    loadApiTokens();
                    loadWebhooks();
                    loadWebhookDeliveries();
                    loadWhmcsDiagnostic();
                  }}
                >
                  <RefreshCw size={14} /> {t('Refresh')}
                </button>
              </div>
            </div>

            {/* WHMCS / Billing Readiness Diagnostic Strip */}
            {whmcsDiagnostic && (
              <div style={{
                padding: '12px 15px',
                borderRadius: '10px',
                background: whmcsDiagnostic.ready ? 'rgba(16, 185, 129, 0.08)' : 'rgba(245, 158, 11, 0.08)',
                border: whmcsDiagnostic.ready ? '1px solid rgba(16, 185, 129, 0.28)' : '1px solid rgba(245, 158, 11, 0.28)',
                marginBottom: '16px',
                display: 'flex',
                flexDirection: 'column',
                gap: '10px',
              }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '8px' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    {whmcsDiagnostic.ready ? (
                      <CheckCircle size={16} style={{ color: '#10b981' }} />
                    ) : (
                      <AlertCircle size={16} style={{ color: '#f59e0b' }} />
                    )}
                    <strong style={{ fontSize: '0.86rem', color: 'var(--text-main)' }}>
                      Chẩn đoán Độ sẵn sàng Tích hợp WHMCS / HostBill / Perfex CRM ({whmcsDiagnostic.score} hạng mục đạt)
                    </strong>
                  </div>
                  <span style={{
                    padding: '2px 10px',
                    borderRadius: '999px',
                    fontSize: '0.74rem',
                    fontWeight: 700,
                    background: whmcsDiagnostic.ready ? 'rgba(16, 185, 129, 0.16)' : 'rgba(245, 158, 11, 0.16)',
                    color: whmcsDiagnostic.ready ? '#10b981' : '#f59e0b',
                  }}>
                    {whmcsDiagnostic.ready ? 'SẴN SÀNG KẾT NỐI 100%' : 'CẦN BỔ SUNG CẤU HÌNH'}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: '8px' }}>
                  {(whmcsDiagnostic.checks || []).map((chk) => (
                    <div
                      key={chk.key}
                      style={{
                        padding: '8px 10px',
                        borderRadius: '8px',
                        background: 'var(--surface-1)',
                        border: '1px solid var(--border-subtle)',
                        fontSize: '0.78rem',
                      }}
                    >
                      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontWeight: 700, color: chk.ok ? '#10b981' : '#f59e0b', marginBottom: '3px' }}>
                        {chk.ok ? <Check size={13} /> : <AlertCircle size={13} />}
                        <span>{chk.title}</span>
                      </div>
                      <div style={{ color: 'var(--text-muted)', lineHeight: 1.4 }}>{chk.detail}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Platform Selector Pills + Quick Connection Info */}
            <div style={{
              padding: '14px 16px', borderRadius: '10px',
              background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
              marginBottom: '16px', display: 'flex', flexDirection: 'column', gap: '12px',
            }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.79rem', fontWeight: 700, color: 'var(--text-muted)' }}>
                    Hệ thống tích hợp:
                  </span>
                  {INTEGRATION_PLATFORMS.map((plat) => {
                    const active = selectedPlatform === plat.id;
                    return (
                      <button
                        key={plat.id}
                        type="button"
                        onClick={() => handleSelectPlatform(plat.id)}
                        style={{
                          padding: '5px 12px',
                          borderRadius: '999px',
                          fontSize: '0.8rem',
                          fontWeight: 600,
                          border: active ? '1px solid var(--accent-primary)' : '1px solid var(--border-subtle)',
                          background: active ? 'rgba(13, 148, 136, 0.14)' : 'var(--surface-1)',
                          color: active ? 'var(--accent-primary)' : 'var(--text-main)',
                          cursor: 'pointer',
                        }}
                      >
                        {plat.label}
                      </button>
                    );
                  })}
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                  <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>Base API URL:</span>
                  <code style={{
                    padding: '4px 8px', borderRadius: '6px',
                    background: 'var(--surface-1)', border: '1px solid var(--border-subtle)',
                    fontSize: '0.78rem', color: 'var(--accent-primary)', fontWeight: 600,
                  }}>
                    {baseProvisioningUrl}
                  </code>
                  <button
                    type="button"
                    className="secondary-light"
                    style={{ padding: '4px 8px', height: '28px', fontSize: '0.76rem' }}
                    onClick={() => copyToClipboard(baseProvisioningUrl, 'Đã sao chép Base API URL.')}
                  >
                    <Copy size={12} /> Copy URL
                  </button>
                </div>
              </div>

              <div style={{ fontSize: '0.81rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
                <strong style={{ color: 'var(--text-main)' }}>Hướng dẫn ({activePlatformInfo.label}):</strong>{' '}
                {activePlatformInfo.guide}
              </div>
            </div>

            {/* Collapsible REST API Reference */}
            {showEndpointsRef && (
              <div style={{
                padding: '14px 16px', borderRadius: '10px',
                background: 'var(--surface-2)', border: '1px solid var(--border-subtle)',
                marginBottom: '16px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '10px', flexWrap: 'wrap', gap: '8px' }}>
                  <strong style={{ fontSize: '0.88rem', color: 'var(--text-main)' }}>
                    Danh sách Endpoint REST API ({baseProvisioningUrl})
                  </strong>
                  <code style={{ fontSize: '0.77rem', color: 'var(--text-muted)' }}>
                    Header: Authorization: Bearer bp_xxxxxxxx...
                  </code>
                </div>
                <div className="pkg-table-wrap">
                  <table className="pkg-clean-table">
                    <thead>
                      <tr>
                        <th style={{ width: '90px' }}>Method</th>
                        <th>Endpoint Path</th>
                        <th>Chức năng nghiệp vụ</th>
                      </tr>
                    </thead>
                    <tbody>
                      {PROVISIONING_ENDPOINTS.map((ep) => (
                        <tr key={`${ep.method}-${ep.path}`}>
                          <td>
                            <span style={{
                              padding: '2px 7px', borderRadius: '6px', fontSize: '0.72rem', fontWeight: 700,
                              background:
                                ep.method === 'GET'
                                  ? 'rgba(59, 130, 246, 0.14)'
                                  : ep.method === 'POST'
                                  ? 'rgba(16, 185, 129, 0.14)'
                                  : ep.method === 'DELETE'
                                  ? 'rgba(239, 68, 68, 0.14)'
                                  : 'rgba(245, 158, 11, 0.14)',
                              color:
                                ep.method === 'GET'
                                  ? '#3b82f6'
                                  : ep.method === 'POST'
                                  ? '#10b981'
                                  : ep.method === 'DELETE'
                                  ? '#ef4444'
                                  : '#f59e0b',
                            }}>
                              {ep.method}
                            </span>
                          </td>
                          <td><code>/api/provisioning/v1{ep.path}</code></td>
                          <td style={{ fontSize: '0.82rem', color: 'var(--text-muted)' }}>{ep.desc}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {/* Newly Created Token Banner */}
            {createdApiToken && (
              <div style={{
                padding: '14px 16px', borderRadius: '10px', marginBottom: '16px',
                background: 'rgba(16, 185, 129, 0.1)', border: '1px solid rgba(16, 185, 129, 0.35)',
                display: 'flex', flexDirection: 'column', gap: '10px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
                  <strong style={{ fontSize: '0.88rem', color: '#10b981', display: 'flex', alignItems: 'center', gap: '6px' }}>
                    <Check size={16} /> Khóa API Token mới đã được khởi tạo (Chỉ hiển thị 1 lần duy nhất):
                  </strong>
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(createdApiToken, 'Đã sao chép API Token vào bộ nhớ tạm.')}
                    >
                      <Copy size={14} /> Sao chép Token
                    </button>
                    <button
                      type="button"
                      className="secondary-light"
                      onClick={() => setCreatedApiToken('')}
                    >
                      Đóng
                    </button>
                  </div>
                </div>
                <input
                  id="created-api-token"
                  readOnly
                  value={createdApiToken}
                  onFocus={(e) => e.target.select()}
                  style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: '0.86rem' }}
                />
              </div>
            )}

            {/* Create Token Form */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))',
              gap: '12px',
              alignItems: 'end',
              padding: '14px 16px',
              borderRadius: '10px',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-subtle)',
              marginBottom: '16px',
            }}>
              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Tên gợi nhớ (Hệ thống kết nối)
                </span>
                <input
                  value={newApiToken.name}
                  onChange={(e) => setNewApiToken((prev) => ({ ...prev, name: e.target.value }))}
                  placeholder="VD: WHMCS, HostBill, Perfex CRM"
                />
              </label>

              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Giới hạn IP máy chủ gọi API (Để trống = Cho phép mọi IP)
                </span>
                <input
                  value={newApiToken.allowed_ips}
                  onChange={(e) => setNewApiToken((prev) => ({ ...prev, allowed_ips: e.target.value }))}
                  placeholder="Tùy chọn: 103.1.2.3, 103.1.2.0/24"
                />
              </label>

              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Quyền hạn (Scopes)
                </span>
                <select
                  value={newApiToken.scopes}
                  onChange={(e) => setNewApiToken((prev) => ({ ...prev, scopes: e.target.value }))}
                >
                  <option value="provisioning:read,provisioning:write">Toàn quyền (Đọc & Ghi Provisioning)</option>
                  <option value="provisioning:read">Chỉ đọc (Đồng bộ Gói & Dung lượng)</option>
                  <option value="plans:read,accounts:read,accounts:write,usage:read,sso">WHMCS chuẩn (phân quyền chi tiết)</option>
                  <option value="plans:read,accounts:read,accounts:write,usage:read,sso,sso:admin">WHMCS + Đăng nhập Admin (Admin SSO)</option>
                  <option value="accounts:read,usage:read">Chỉ đồng bộ tài khoản & dung lượng</option>
                </select>
              </label>

              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Hết hạn sau (ngày, để trống = không hết hạn)
                </span>
                <input
                  type="number"
                  min="1"
                  max="3650"
                  value={newApiToken.expires_in_days}
                  onChange={(e) => setNewApiToken((prev) => ({ ...prev, expires_in_days: e.target.value }))}
                  placeholder="VD: 365"
                />
              </label>

              <label style={{ margin: 0 }}>
                <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                  Giới hạn request/phút (để trống = 120)
                </span>
                <input
                  type="number"
                  min="1"
                  max="6000"
                  value={newApiToken.rate_limit_per_minute}
                  onChange={(e) => setNewApiToken((prev) => ({ ...prev, rate_limit_per_minute: e.target.value }))}
                  placeholder="120"
                />
              </label>

              <button
                type="button"
                disabled={!!loading || !newApiToken.name.trim()}
                onClick={createApiToken}
                style={{ height: '40px', padding: '0 18px', fontWeight: 600 }}
              >
                <Plus size={15} /> Tạo API Token
              </button>
            </div>

            {/* Tokens Table */}
            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Tên Token / Hệ thống</th>
                    <th>Quyền hạn (Scopes)</th>
                    <th>IP được phép (Whitelist)</th>
                    <th>Trạng thái</th>
                    <th>Lần gọi gần nhất</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {apiTokens.map((token) => (
                    <tr key={token.id}>
                      <td>
                        <strong style={{ color: 'var(--text-main)' }}>{token.name}</strong>
                        {token.created_at && (
                          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>
                            Tạo lúc: {new Date(token.created_at).toLocaleString()}
                          </div>
                        )}
                      </td>
                      <td>
                        <code style={{ fontSize: '0.76rem' }}>{token.scopes}</code>
                      </td>
                      <td>
                        {token.allowed_ips ? (
                          <code style={{ fontSize: '0.78rem', color: 'var(--accent-primary)' }}>{token.allowed_ips}</code>
                        ) : (
                          <span style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>Tất cả IP (All IPs)</span>
                        )}
                      </td>
                      <td>
                        <span style={{
                          padding: '3px 9px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 700,
                          background: token.is_active ? 'rgba(16, 185, 129, 0.14)' : 'rgba(239, 68, 68, 0.14)',
                          color: token.is_active ? '#10b981' : '#ef4444',
                        }}>
                          {token.is_active ? 'Đang hoạt động' : 'Đã thu hồi'}
                        </span>
                        {token.is_active && token.expires_at && (
                          <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)', marginTop: '4px' }}>
                            Hết hạn: {new Date(`${token.expires_at}${String(token.expires_at).endsWith('Z') ? '' : 'Z'}`).toLocaleString()}
                          </div>
                        )}
                      </td>
                      <td style={{ fontSize: '0.81rem', color: 'var(--text-muted)' }}>
                        {token.last_used_at ? new Date(token.last_used_at).toLocaleString() : 'Chưa từng gọi'}
                      </td>
                      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                        <button
                          type="button"
                          className="secondary-light"
                          style={{ padding: '6px 12px', fontSize: '0.78rem', marginRight: '6px' }}
                          disabled={!!loading || !token.is_active}
                          onClick={() => rotateApiToken(token)}
                        >
                          <RotateCcw size={14} /> Xoay vòng
                        </button>
                        <button
                          type="button"
                          className="danger"
                          style={{ padding: '6px 12px', fontSize: '0.78rem' }}
                          disabled={!!loading || !token.is_active}
                          onClick={() => revokeApiToken(token)}
                        >
                          <Trash2 size={14} /> Thu hồi
                        </button>
                      </td>
                    </tr>
                  ))}
                  {apiTokens.length === 0 && (
                    <tr>
                      <td colSpan="6">
                        <EmptyState
                          icon={KeyRound}
                          title="Chưa có API Token nào"
                          subtitle="Tạo một API Token ở khung phía trên để kết nối WHMCS, HostBill, Blesta hoặc Perfex CRM."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* CARD 2: OUTBOUND EVENT WEBHOOKS */}
          <div className="pkg-card" style={{ padding: '20px 22px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '14px', marginBottom: '16px' }}>
              <div>
                <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Zap size={18} style={{ color: 'var(--accent-primary)' }} />
                  2. Outbound Event Webhooks (Perfex CRM, ERP, n8n, Automation)
                </h3>
                <p style={{ margin: '4px 0 0', fontSize: '0.83rem', color: 'var(--text-muted)' }}>
                  Tự động đẩy thông báo JSON (kèm chữ ký bảo mật <code>X-SPanel-Signature</code> HMAC-SHA256) sang hệ thống CRM / Billing bên ngoài khi phát sinh sự kiện trên SPanel.
                </p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setWebhookForm({ id: null, url: '', secret: '', events: '*', is_active: true });
                  setShowWebhookForm(true);
                }}
              >
                <Plus size={15} /> Thêm Webhook Endpoint
              </button>
            </div>

            {showWebhookForm && (
              <form
                onSubmit={saveWebhook}
                style={{
                  padding: '16px',
                  borderRadius: '10px',
                  background: 'var(--surface-2)',
                  border: '1px solid var(--border-subtle)',
                  marginBottom: '16px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '14px',
                }}
              >
                <div style={{ fontWeight: 700, fontSize: '0.9rem', color: 'var(--text-main)' }}>
                  {webhookForm.id ? 'Chỉnh sửa Webhook Endpoint' : 'Thêm mới Webhook Endpoint'}
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: '12px' }}>
                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Endpoint URL nhận sự kiện (HTTPS/HTTP)
                    </span>
                    <input
                      type="url"
                      value={webhookForm.url}
                      onChange={(e) => setWebhookForm({ ...webhookForm, url: e.target.value })}
                      placeholder="https://crm.yourdomain.com/api/webhooks/spanel"
                      required
                    />
                  </label>

                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      HMAC Secret (Tùy chọn — ký xác thực X-SPanel-Signature)
                    </span>
                    <input
                      type="text"
                      value={webhookForm.secret || ''}
                      onChange={(e) => setWebhookForm({ ...webhookForm, secret: e.target.value })}
                      placeholder={webhookForm.id ? 'Để trống nếu giữ nguyên Secret cũ' : 'Chuỗi bí mật dùng để kiểm tra chữ ký SHA-256'}
                    />
                  </label>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(220px, 1fr) minmax(260px, 1.5fr) auto', gap: '12px', alignItems: 'end' }}>
                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Mẫu bộ lọc sự kiện nhanh
                    </span>
                    <select
                      value={
                        WEBHOOK_EVENT_PRESETS.find((p) => p.value === webhookForm.events)?.value || 'custom'
                      }
                      onChange={(e) => {
                        if (e.target.value !== 'custom') {
                          setWebhookForm({ ...webhookForm, events: e.target.value });
                        }
                      }}
                    >
                      {WEBHOOK_EVENT_PRESETS.map((p) => (
                        <option key={p.id} value={p.value}>{p.label}</option>
                      ))}
                      <option value="custom">Tùy chỉnh danh sách sự kiện...</option>
                    </select>
                  </label>

                  <label style={{ margin: 0 }}>
                    <span style={{ display: 'block', fontSize: '0.78rem', fontWeight: 600, color: 'var(--text-muted)', marginBottom: '5px' }}>
                      Danh sách sự kiện đăng ký (Events)
                    </span>
                    <input
                      type="text"
                      value={webhookForm.events}
                      onChange={(e) => setWebhookForm({ ...webhookForm, events: e.target.value })}
                      placeholder="*, provisioning_create, provisioning_suspend..."
                      required
                    />
                  </label>

                  <label className="check-line" style={{ margin: 0, height: '40px', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <input
                      type="checkbox"
                      checked={!!webhookForm.is_active}
                      onChange={(e) => setWebhookForm({ ...webhookForm, is_active: e.target.checked })}
                    />
                    <span>Kích hoạt</span>
                  </label>
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    onClick={() => setShowWebhookForm(false)}
                  >
                    Hủy
                  </button>
                  <button type="submit" disabled={!!loading}>
                    <Check size={15} /> Lưu cấu hình Webhook
                  </button>
                </div>
              </form>
            )}

            <div className="pkg-table-wrap">
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>Endpoint URL</th>
                    <th>Sự kiện đăng ký (Events)</th>
                    <th>Bảo mật HMAC</th>
                    <th>Trạng thái</th>
                    <th style={{ textAlign: 'right' }}>Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  {webhooks.map((wh) => (
                    <tr key={wh.id}>
                      <td>
                        <code style={{ wordBreak: 'break-all', fontSize: '0.82rem', color: 'var(--text-main)' }}>
                          {wh.url}
                        </code>
                      </td>
                      <td>
                        <code style={{ fontSize: '0.76rem', color: 'var(--accent-primary)' }}>
                          {wh.events === '*' ? '* (Tất cả sự kiện)' : wh.events}
                        </code>
                      </td>
                      <td>
                        <span style={{
                          padding: '2px 8px', borderRadius: '999px', fontSize: '0.73rem', fontWeight: 600,
                          background: wh.has_secret ? 'rgba(13, 148, 136, 0.14)' : 'var(--surface-3)',
                          color: wh.has_secret ? 'var(--accent-primary)' : 'var(--text-muted)',
                        }}>
                          {wh.has_secret ? 'HMAC-SHA256' : 'Không dùng Secret'}
                        </span>
                      </td>
                      <td>
                        <span style={{
                          padding: '3px 9px', borderRadius: '999px', fontSize: '0.74rem', fontWeight: 700,
                          background: wh.is_active ? 'rgba(16, 185, 129, 0.14)' : 'var(--surface-3)',
                          color: wh.is_active ? '#10b981' : 'var(--text-muted)',
                        }}>
                          {wh.is_active ? 'Đang bật (Active)' : 'Tạm tắt (Inactive)'}
                        </span>
                      </td>
                      <td style={{ textAlign: 'right' }}>
                        <div style={{ display: 'inline-flex', gap: '6px', justifyContent: 'flex-end' }}>
                          <button
                            type="button"
                            className="secondary-light"
                            style={{ padding: '6px 10px', fontSize: '0.78rem' }}
                            disabled={!!loading}
                            onClick={() => testWebhookEndpoint(wh)}
                            title="Gửi gói tin JSON thử nghiệm tới URL này"
                          >
                            <Send size={13} /> Gửi thử
                          </button>
                          <button
                            type="button"
                            className="secondary-light"
                            style={{ padding: '6px 10px' }}
                            disabled={!!loading}
                            onClick={() => {
                              setWebhookForm({
                                id: wh.id,
                                url: wh.url,
                                secret: '',
                                events: wh.events || '*',
                                is_active: wh.is_active,
                              });
                              setShowWebhookForm(true);
                            }}
                            title="Chỉnh sửa"
                          >
                            <Edit2 size={14} />
                          </button>
                          <button
                            type="button"
                            className="danger"
                            style={{ padding: '6px 10px' }}
                            disabled={!!loading}
                            onClick={() => deleteWebhook(wh)}
                            title="Xóa Webhook"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {webhooks.length === 0 && (
                    <tr>
                      <td colSpan="5">
                        <EmptyState
                          icon={Zap}
                          title="Chưa cấu hình Outbound Webhook nào"
                          subtitle="Chỉ cần thêm Webhook nếu bạn muốn SPanel bắn sự kiện thời gian thực sang Perfex CRM, n8n hoặc hệ thống tự động hóa bên ngoài."
                        />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* WEBHOOK DELIVERY LOG & 1-CLICK REPLAY */}
            <div style={{ marginTop: '20px', paddingTop: '16px', borderTop: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px', marginBottom: '12px' }}>
                <div>
                  <h4 style={{ margin: 0, fontSize: '0.95rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <Activity size={16} style={{ color: 'var(--accent-primary)' }} />
                    Nhật ký Gửi Sự kiện Webhook & Công cụ Gửi lại (Webhook Delivery Log & Replay)
                  </h4>
                  <p style={{ margin: '3px 0 0', fontSize: '0.79rem', color: 'var(--text-muted)' }}>
                    Theo dõi mã phản hồi HTTP, độ trễ mạng và gửi lại (Replay) tức thì các gói tin Webhook bị lỡ khi máy chủ CRM/Billing bảo trì.
                  </p>
                </div>
                <div style={{ display: 'flex', gap: '8px' }}>
                  <button
                    type="button"
                    className="secondary-light"
                    disabled={!!loading}
                    onClick={loadWebhookDeliveries}
                    style={{ padding: '5px 10px', fontSize: '0.78rem' }}
                  >
                    <RefreshCw size={13} /> Làm mới nhật ký
                  </button>
                  {webhookDeliveries.length > 0 && (
                    <button
                      type="button"
                      className="secondary-light"
                      disabled={!!loading}
                      onClick={clearWebhookDeliveriesLog}
                      style={{ padding: '5px 10px', fontSize: '0.78rem' }}
                    >
                      <Trash2 size={13} /> Xóa nhật ký
                    </button>
                  )}
                </div>
              </div>

              <div className="pkg-table-wrap">
                <table className="pkg-clean-table">
                  <thead>
                    <tr>
                      <th>Thời gian</th>
                      <th>Sự kiện (Event)</th>
                      <th>Đích nhận (URL)</th>
                      <th>Phản hồi HTTP</th>
                      <th>Độ trễ</th>
                      <th>Chi tiết phản hồi</th>
                      <th style={{ textAlign: 'right' }}>Thao tác</th>
                    </tr>
                  </thead>
                  <tbody>
                    {webhookDeliveries.map((deliv) => {
                      const isOk = deliv.status === 'success';
                      return (
                        <tr key={deliv.id}>
                          <td style={{ fontSize: '0.78rem', color: 'var(--text-muted)', whiteSpace: 'nowrap' }}>
                            {deliv.created_at ? new Date(deliv.created_at).toLocaleString() : '—'}
                          </td>
                          <td>
                            <code style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--accent-primary)' }}>
                              {deliv.event}
                            </code>
                            {deliv.replayed_from && (
                              <span style={{ marginLeft: '6px', fontSize: '0.68rem', padding: '1px 5px', borderRadius: '4px', background: 'var(--surface-3)', color: 'var(--text-muted)' }}>
                                Replayed
                              </span>
                            )}
                          </td>
                          <td>
                            <code style={{ fontSize: '0.76rem', wordBreak: 'break-all' }}>{deliv.url}</code>
                          </td>
                          <td>
                            <span style={{
                              padding: '2px 8px',
                              borderRadius: '999px',
                              fontSize: '0.73rem',
                              fontWeight: 700,
                              background: isOk ? 'rgba(16, 185, 129, 0.14)' : 'rgba(239, 68, 68, 0.14)',
                              color: isOk ? '#10b981' : '#ef4444',
                            }}>
                              {deliv.status_code ? `HTTP ${deliv.status_code}` : (isOk ? '200 OK' : 'Timeout / Error')}
                            </span>
                          </td>
                          <td style={{ fontSize: '0.78rem', fontFamily: 'monospace', color: 'var(--text-muted)' }}>
                            {deliv.latency_ms ? `${deliv.latency_ms} ms` : '—'}
                          </td>
                          <td style={{ fontSize: '0.77rem', color: 'var(--text-muted)', maxWidth: '240px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={deliv.response_preview || ''}>
                            {deliv.response_preview || '—'}
                          </td>
                          <td style={{ textAlign: 'right' }}>
                            <button
                              type="button"
                              className="secondary-light"
                              style={{ padding: '5px 10px', fontSize: '0.76rem' }}
                              disabled={!!loading}
                              onClick={() => replayWebhookDelivery(deliv)}
                              title="Gửi lại gói tin sự kiện này tới URL đích"
                            >
                              <RotateCcw size={13} /> Gửi lại (Replay)
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                    {webhookDeliveries.length === 0 && (
                      <tr>
                        <td colSpan="7" style={{ textAlign: 'center', padding: '18px', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                          Chưa có bản ghi lịch sử gửi Webhook nào. Nhấn nút <strong>"Gửi thử"</strong> trên một Webhook Endpoint bất kỳ để kiểm tra kết nối.
                        </td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* TAB 4: SECURITY AUDIT TRAIL (SHA-256 HASH-CHAINED) & CRON JAIL */}
      {taspanel(
        'audit',
        <div className="panel-settings-layout" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          {/* Section 1: Cron Namespace & PHP CLI Confinement */}
          <div style={{ padding: '20px 22px', borderRadius: '14px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '14px', flexWrap: 'wrap', marginBottom: '14px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <ShieldCheck size={18} style={{ color: '#10b981' }} />
                  <h3 style={{ margin: 0, fontSize: '1rem' }}>Cron & Terminal Namespace Confinement (Zero-Escape Jail)</h3>
                  <span className="badge ok">ACTIVE ENFORCEMENT</span>
                </div>
                <p className="hint" style={{ margin: '4px 0 0 0', fontSize: '0.82rem' }}>
                  Mọi tác vụ Cron PHP và Terminal đều bị khóa cứng trong <code>open_basedir</code> riêng của từng Linux User, vô hiệu hóa hàm thực thi shell (<code>disable_functions</code>) và cô lập Mount Namespace (<code>unshare --mount --propagation private</code>).
                </p>
              </div>
              <button
                type="button"
                className="primary"
                style={{ padding: '8px 14px', fontSize: '0.82rem' }}
                disabled={busyAudit}
                onClick={enforceCronConfinementNow}
              >
                <Zap size={14} /> Chuẩn hóa & Bọc cô lập toàn bộ Cron
              </button>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '12px' }}>
              <div style={{ padding: '12px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>Tổng số tác vụ Cron</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700, marginTop: '2px' }}>
                  {cronSecurity?.total_jobs ?? 0} tác vụ
                </div>
              </div>
              <div style={{ padding: '12px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>Đã bọc cô lập hoàn toàn</div>
                <div style={{ fontSize: '1.15rem', fontWeight: 700, color: '#10b981', marginTop: '2px' }}>
                  {cronSecurity?.confined_jobs ?? 0} / {cronSecurity?.total_jobs ?? 0}
                </div>
              </div>
              <div style={{ padding: '12px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>Mount Namespace Isolation</div>
                <div style={{ fontSize: '0.9rem', fontWeight: 700, color: '#10b981', marginTop: '4px' }}>
                  Private /home tmpfs + Bind-Mount
                </div>
              </div>
              <div style={{ padding: '12px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.74rem', color: 'var(--text-muted)' }}>Chặn hàm nguy hiểm PHP CLI</div>
                <div style={{ fontSize: '0.75rem', fontFamily: 'monospace', marginTop: '4px', wordBreak: 'break-all' }}>
                  {cronSecurity?.disabled_functions || 'exec,passthru,shell_exec,system,proc_open,popen,pcntl_exec...'}
                </div>
              </div>
            </div>
          </div>

          {/* Section 2: Tamper-Evident SHA-256 Hash-Chained Security Audit Trail */}
          <div style={{ padding: '20px 22px', borderRadius: '14px', background: 'var(--surface-2)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '14px', flexWrap: 'wrap', marginBottom: '16px' }}>
              <div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <Lock size={18} style={{ color: 'var(--accent-primary)' }} />
                  <h3 style={{ margin: 0, fontSize: '1rem' }}>Tamper-Evident Security Audit Trail (SHA-256 Hash Chain)</h3>
                  <span className={auditChain?.valid !== false ? 'badge ok' : 'badge bad'}>
                    {auditChain?.valid !== false ? 'CHUỖI BĂM SHA-256 TOÀN VẸN 100%' : 'PHÁT HIỆN SAI LỆCH CHUỖI BĂM'}
                  </span>
                </div>
                <p className="hint" style={{ margin: '4px 0 0 0', fontSize: '0.82rem' }}>
                  Mỗi hành động quản trị đều được ký băm mật mã học <code>SHA-256(prev_hash | user | action | target | detail | timestamp)</code> nối tiếp nhau như chuỗi khối (Blockchain), giúp phát hiện ngay lập tức mọi hành vi xóa hoặc sửa trộm nhật ký trong DB.
                </p>
              </div>
              <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
                <button
                  type="button"
                  className="secondary"
                  style={{ padding: '8px 12px', fontSize: '0.8rem' }}
                  disabled={busyAudit}
                  onClick={() => loadAuditAndCronSecurity(auditSearch)}
                >
                  <RefreshCw size={14} /> Làm mới
                </button>
                <button
                  type="button"
                  className="primary"
                  style={{ padding: '8px 14px', fontSize: '0.8rem' }}
                  disabled={busyAudit}
                  onClick={verifyAuditChainNow}
                >
                  <ShieldCheck size={14} /> Xác thực toàn vẹn chuỗi SHA-256
                </button>
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '10px', marginBottom: '16px' }}>
              <div style={{ padding: '10px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Tổng số bản ghi đã ký</div>
                <div style={{ fontSize: '1.1rem', fontWeight: 700, marginTop: '2px' }}>
                  {auditChain?.verified_entries ?? 0} / {auditChain?.total_entries ?? 0} bản ghi
                </div>
              </div>
              <div style={{ padding: '10px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Trạng thái kiểm toán</div>
                <div style={{ fontSize: '0.95rem', fontWeight: 700, color: auditChain?.valid !== false ? '#10b981' : '#ef4444', marginTop: '3px' }}>
                  {auditChain?.valid !== false ? 'Verified • Không có chỉnh sửa trái phép' : `${auditChain?.tampered_entries?.length || 1} bản ghi bị can thiệp`}
                </div>
              </div>
              <div style={{ padding: '10px 14px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
                <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)' }}>Mã băm đỉnh chuỗi (Head SHA-256)</div>
                <div style={{ fontSize: '0.78rem', fontFamily: 'monospace', fontWeight: 600, marginTop: '4px', wordBreak: 'break-all' }}>
                  {auditChain?.head_hash ? `${auditChain.head_hash.slice(0, 28)}...` : '—'}
                </div>
              </div>
            </div>

            <div style={{ display: 'flex', gap: '10px', marginBottom: '12px' }}>
              <input
                type="text"
                placeholder="Tìm theo hành động (login, create_website...), đối tượng, địa chỉ IP hoặc mã băm SHA-256..."
                value={auditSearch}
                onChange={(e) => setAuditSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') loadAuditAndCronSecurity(auditSearch);
                }}
                style={{ flex: 1 }}
              />
              <button
                type="button"
                className="secondary"
                onClick={() => loadAuditAndCronSecurity(auditSearch)}
              >
                Tìm kiếm
              </button>
            </div>

            <div className="pkg-table-scroll" style={{ maxHeight: '460px', overflowY: 'auto' }}>
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>#ID</th>
                    <th>Thời gian (UTC)</th>
                    <th>Tài khoản</th>
                    <th>Hành động</th>
                    <th>Đối tượng</th>
                    <th>Chi tiết & Địa chỉ IP</th>
                    <th>Chuỗi băm SHA-256 (Prev → Entry)</th>
                  </tr>
                </thead>
                <tbody>
                  {auditLogs.map((row) => (
                    <tr key={row.id}>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.78rem', color: 'var(--text-muted)' }}>#{row.id}</td>
                      <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                        {row.created_at ? new Date(row.created_at).toLocaleString() : '—'}
                      </td>
                      <td>
                        <strong style={{ fontSize: '0.8rem' }}>{row.username}</strong>
                      </td>
                      <td>
                        <code style={{ fontSize: '0.76rem', fontWeight: 700, color: 'var(--accent-primary)' }}>{row.action}</code>
                      </td>
                      <td style={{ fontSize: '0.8rem', fontWeight: 600 }}>{row.target}</td>
                      <td style={{ fontSize: '0.76rem', color: 'var(--text-muted)', maxWidth: '280px', wordBreak: 'break-word' }}>
                        {row.detail || '—'}
                      </td>
                      <td style={{ fontFamily: 'monospace', fontSize: '0.73rem', whiteSpace: 'nowrap' }} title={`Prev: ${row.prev_hash}\nEntry: ${row.entry_hash}`}>
                        <span style={{ color: 'var(--text-muted)' }}>{(row.prev_hash || '').slice(0, 8)}</span>
                        {' → '}
                        <strong style={{ color: '#10b981' }}>{(row.entry_hash || '').slice(0, 12)}</strong>
                      </td>
                    </tr>
                  ))}
                  {auditLogs.length === 0 && (
                    <tr>
                      <td colSpan="7" style={{ textAlign: 'center', padding: '18px', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
                        Không tìm thấy bản ghi nhật ký bảo mật nào khớp với bộ lọc.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
