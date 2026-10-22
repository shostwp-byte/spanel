import React, { useState, useEffect } from 'react';
import {
  Check, CheckCircle, Code2, Copy, Cpu, ExternalLink, Globe, Heart,
  KeyRound, RefreshCw, Server, ShieldCheck, Sparkles, Terminal as TerminalIcon, Users
} from 'lucide-react';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

function formatUptime(seconds) {
  const sec = Number(seconds || 0);
  if (!sec || sec <= 0) return 'Đang hoạt động';
  const days = Math.floor(sec / 86400);
  const hours = Math.floor((sec % 86400) / 3600);
  const mins = Math.floor((sec % 3600) / 60);
  if (days > 0) return `${days} ngày ${hours} giờ`;
  if (hours > 0) return `${hours} giờ ${mins} phút`;
  return `${mins} phút`;
}

export function LicensingPage({ request, isAdmin, appVersion }) {
  const { loading, setNotice, setError } = useAppContext();
  const [licenseData, setLicenseData] = useState(null);

  useEffect(() => {
    if (isAdmin) {
      loadLicensingStatus();
    }
  }, [isAdmin]);

  async function loadLicensingStatus() {
    const data = await request('/licensing/status', {}, 'Đang tải thông tin Phiên bản & Định danh Máy chủ...');
    if (data) setLicenseData(data);
  }

  async function copyTextValue(val, label = 'Đã sao chép vào bộ nhớ tạm.') {
    if (!val) return;
    try {
      await navigator.clipboard.writeText(val);
      setNotice(label);
    } catch {
      setError('Không thể tự động sao chép, vui lòng bôi đen và nhấn Ctrl+C.');
    }
  }

  if (!isAdmin) {
    return (
      <section className="section">
        <h2>{t('Licensing')}</h2>
        <p className="hint">{t('Administrators only')}</p>
      </section>
    );
  }

  const node = licenseData?.node_identity || {};
  const usage = licenseData?.usage || {};
  const capabilities = licenseData?.unlocked_capabilities || [];
  const githubRepo = licenseData?.github_repo || 'https://github.com/shostwp-byte/spanel';
  const githubReleases = licenseData?.github_releases || 'https://github.com/shostwp-byte/spanel/releases';
  const installCmd =
    licenseData?.install_command ||
    'curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/1.0.7/install.sh | sudo bash';

  return (
    <div className="section licensing-page" style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
      {/* HERO CERTIFICATE BANNER */}
      <div
        className="pkg-card"
        style={{
          padding: '24px',
          background: 'linear-gradient(135deg, rgba(16, 185, 129, 0.10) 0%, rgba(59, 130, 246, 0.06) 100%)',
          border: '1px solid rgba(16, 185, 129, 0.32)',
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: '16px' }}>
          <div style={{ maxWidth: '760px' }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: '8px', padding: '4px 12px', borderRadius: '999px', background: 'rgba(16, 185, 129, 0.16)', color: '#10b981', fontSize: '0.76rem', fontWeight: 800, marginBottom: '10px' }}>
              <ShieldCheck size={15} />
              OPEN-SOURCE ENTERPRISE EDITION • 100% FREE FOREVER ON GITHUB (0đ)
            </div>
            <h2 style={{ margin: 0, fontSize: '1.35rem', fontWeight: 800, color: 'var(--text-main)' }}>
              {licenseData?.edition || 'SPanel Open-Source Enterprise Edition'}
            </h2>
            <p style={{ margin: '8px 0 0', fontSize: '0.88rem', color: 'var(--text-muted)', lineHeight: 1.55 }}>
              SPanel được cung cấp <strong>hoàn toàn miễn phí 100% trên GitHub</strong> cho toàn thể cộng đồng Quản trị viên, Nhà cung cấp Hosting và Lập trình viên. Không thu bất kỳ chi phí bản quyền nào, không giới hạn số lượng tài khoản hay tên miền, mở khóa 100% các tính năng vận hành chuẩn Enterprise.
            </p>
          </div>

          <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
            <a
              href={githubRepo}
              target="_blank"
              rel="noopener noreferrer"
              className="button"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                padding: '9px 15px',
                borderRadius: '8px',
                background: 'var(--accent-primary)',
                color: '#ffffff',
                fontWeight: 700,
                fontSize: '0.83rem',
                textDecoration: 'none',
              }}
            >
              <ExternalLink size={14} /> GitHub Repository
            </a>
            <a
              href={githubReleases}
              target="_blank"
              rel="noopener noreferrer"
              className="secondary-light"
              style={{
                display: 'inline-flex',
                alignItems: 'center',
                gap: '7px',
                padding: '9px 14px',
                borderRadius: '8px',
                fontWeight: 600,
                fontSize: '0.83rem',
                textDecoration: 'none',
              }}
            >
              <Sparkles size={14} /> Bản phát hành (Releases)
            </a>
            <button
              type="button"
              className="secondary-light"
              disabled={!!loading}
              onClick={loadLicensingStatus}
            >
              <RefreshCw size={14} /> Làm mới
            </button>
          </div>
        </div>

        {/* LICENSE SUMMARY STRIP */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))', gap: '12px', marginTop: '20px' }}>
          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Mô hình Bản quyền
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: '#10b981', marginTop: '4px' }}>
              Miễn phí 100% (0đ / Trọn đời)
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', marginTop: '3px' }}>
              Open-Source Community & Enterprise
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Mã Định Danh Máy Chủ (Node ID)
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '4px' }}>
              <code style={{ fontSize: '0.95rem', fontWeight: 800, color: 'var(--text-main)' }}>
                {node.node_id || 'SPN-OPEN-SOURCE'}
              </code>
              {node.node_id && (
                <button
                  type="button"
                  className="secondary-light"
                  style={{ padding: '2px 7px', height: '24px', fontSize: '0.72rem' }}
                  onClick={() => copyTextValue(node.node_id, 'Đã sao chép Mã định danh Node ID.')}
                >
                  <Copy size={11} />
                </button>
              )}
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', marginTop: '3px' }}>
              SPanel v{node.panel_version || appVersion || '1.0.7'} • {node.architecture || 'x86_64'}
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Giới hạn Tài khoản & Đại lý
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: 'var(--text-main)', marginTop: '4px' }}>
              {usage.users_count ?? 0} Users / Unlimited
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', marginTop: '3px' }}>
              {usage.resellers_count ?? 0} Resellers đang hoạt động (Không giới hạn)
            </div>
          </div>

          <div style={{ padding: '14px 16px', borderRadius: '10px', background: 'var(--surface-1)', border: '1px solid var(--border-subtle)' }}>
            <div style={{ fontSize: '0.75rem', fontWeight: 700, color: 'var(--text-muted)', textTransform: 'uppercase' }}>
              Giới hạn Website & Database
            </div>
            <div style={{ fontSize: '1.05rem', fontWeight: 800, color: 'var(--text-main)', marginTop: '4px' }}>
              {usage.websites_count ?? 0} Domains / Unlimited
            </div>
            <div style={{ fontSize: '0.76rem', color: 'var(--text-muted)', marginTop: '3px' }}>
              {usage.databases_count ?? 0} MySQL Databases (Không giới hạn)
            </div>
          </div>
        </div>
      </div>

      {/* SERVER NODE IDENTITY & QUICK INSTALL SHARE */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(340px, 1fr))', gap: '20px' }}>
        <div className="pkg-card" style={{ padding: '20px 22px' }}>
          <h3 style={{ margin: '0 0 14px', fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Server size={18} style={{ color: 'var(--accent-primary)' }} />
            Thông tin Định danh Máy chủ (Server Node Identity)
          </h3>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', fontSize: '0.84rem' }}>
            {[
              ['Hostname Quản trị', node.hostname || window.location.hostname],
              ['Hệ điều hành (OS)', node.os_name || 'Linux Server'],
              ['Nhân hệ thống (Kernel)', `${node.kernel || 'Linux'} (${node.architecture || 'x86_64'})`],
              ['Địa chỉ IPv4 được cấp phép', (node.ipv4_addresses || []).join(', ') || window.location.hostname],
              ['Thời gian hoạt động (Uptime)', formatUptime(node.uptime_seconds)],
              ['Trạng thái Bản quyền', 'Open-Source Enterprise (Kích hoạt vĩnh viễn)'],
            ].map(([label, val]) => (
              <div
                key={label}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  gap: '12px',
                  paddingBottom: '8px',
                  borderBottom: '1px dashed var(--border-subtle)',
                }}
              >
                <span style={{ color: 'var(--text-muted)', fontWeight: 500 }}>{label}</span>
                <strong style={{ color: 'var(--text-main)', textAlign: 'right', wordBreak: 'break-all' }}>{val}</strong>
              </div>
            ))}
          </div>
        </div>

        <div className="pkg-card" style={{ padding: '20px 22px', display: 'flex', flexDirection: 'column', justifyContent: 'space-between', gap: '14px' }}>
          <div>
            <h3 style={{ margin: '0 0 8px', fontSize: '1.02rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
              <TerminalIcon size={18} style={{ color: 'var(--accent-primary)' }} />
              Triển khai Nhanh Máy chủ Mới & Chia sẻ Cộng đồng
            </h3>
            <p style={{ margin: '0 0 14px', fontSize: '0.82rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Bạn có thể cài đặt SPanel miễn phí lên bất kỳ máy chủ Ubuntu / Debian mới nào để mở rộng cụm <strong>Multi-Server Cluster</strong> hoặc chia sẻ lệnh cài đặt 1 dòng bên dưới cho cộng đồng:
            </p>
            <div style={{
              padding: '12px 14px',
              borderRadius: '10px',
              background: 'var(--surface-2)',
              border: '1px solid var(--border-subtle)',
              fontFamily: 'monospace',
              fontSize: '0.8rem',
              color: 'var(--accent-primary)',
              wordBreak: 'break-all',
              lineHeight: 1.5,
            }}>
              {installCmd}
            </div>
          </div>

          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
            <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)', display: 'inline-flex', alignItems: 'center', gap: '5px' }}>
              <Heart size={13} fill="#e11d48" color="#e11d48" /> Hãy tặng 1 Star trên GitHub để lan tỏa SPanel!
            </span>
            <button
              type="button"
              onClick={() => copyTextValue(installCmd, 'Đã sao chép lệnh cài đặt SPanel.')}
            >
              <Copy size={14} /> Sao chép lệnh cài đặt
            </button>
          </div>
        </div>
      </div>

      {/* UNLOCKED ENTERPRISE CAPABILITIES MATRIX */}
      <div className="pkg-card" style={{ padding: '20px 22px' }}>
        <div style={{ marginBottom: '16px' }}>
          <h3 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, color: 'var(--text-main)', display: 'flex', alignItems: 'center', gap: '8px' }}>
            <Sparkles size={18} style={{ color: 'var(--accent-primary)' }} />
            Danh mục Tính năng Enterprise Đã Mở Khóa (100% Free Forever)
          </h3>
          <p style={{ margin: '4px 0 0', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
            Toàn bộ các phân hệ cao cấp dưới đây đều được tích hợp sẵn và mở khóa không giới hạn trên mọi máy chủ SPanel.
          </p>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '14px' }}>
          {capabilities.map((cap) => (
            <div
              key={cap.key}
              style={{
                padding: '14px 16px',
                borderRadius: '10px',
                background: 'var(--surface-2)',
                border: '1px solid var(--border-subtle)',
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'space-between',
                gap: '10px',
              }}
            >
              <div>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', marginBottom: '6px' }}>
                  <strong style={{ fontSize: '0.88rem', color: 'var(--text-main)' }}>{cap.title}</strong>
                  <span style={{
                    padding: '2px 8px',
                    borderRadius: '999px',
                    fontSize: '0.7rem',
                    fontWeight: 700,
                    whiteSpace: 'nowrap',
                    background: 'rgba(16, 185, 129, 0.15)',
                    color: '#10b981',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: '4px',
                  }}>
                    <Check size={11} /> {cap.status}
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-muted)', lineHeight: 1.45 }}>
                  {cap.desc}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
