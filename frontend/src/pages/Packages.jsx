import React, { useState, useEffect, useRef } from 'react';
import {
  ArrowLeft, Boxes, Check, CloudDownload, Cpu, FileSpreadsheet, FileText,
  FolderUp, Globe, Pencil, Plus, RefreshCw, Save, Server, ShieldCheck, Sparkles, Trash2, Upload, X
} from 'lucide-react';
import { EmptyState } from '../components/EmptyState.jsx';
import { useAppContext } from '../contexts/AppContext.jsx';
import { t } from '../i18n.js';

export const DEFAULT_PKG_STATE = {
  name: '',
  is_reseller_package: false,
  reseller_user_limit: 10,
  reseller_overselling_enabled: false,
  storage_limit_mb: 5120,
  bandwidth_limit_mb: 102400,
  website_limit: 5,
  subdomain_limit: 20,
  alias_limit: 10,
  domain_pointers_limit: 10,
  inode_limit: 250000,
  sftp_accounts_limit: 5,
  ftp_accounts_limit: 5,
  backup_retention_days: 14,
  ips_limit: 0,
  database_limit: 5,
  mysql_storage_limit_mb: 2048,
  mysql_max_db_size_mb: 1024,
  mail_accounts_limit: 10,
  email_forwarders_limit: 20,
  autoresponders_limit: 10,
  mailing_lists_limit: 0,
  node_apps_limit: 0,
  node_app_memory_mb: 512,
  cpu_quota_percent: 100,
  tasks_max: 80,
  memory_high_mb: 768,
  memory_max_mb: 1024,
  io_read_mbps: 100,
  io_write_mbps: 100,
  iops_read_max: 1000,
  iops_write_max: 1000,
  php_access: true,
  wordpress_enabled: true,
  waf_enabled: true,
  ssl_access: true,
  terminal_enabled: false,
  ssh_access: false,
  ssh_access_users: false,
  redis_access: true,
  cron_jobs: true,
  dns_control: true,
  git_access: true,
  spamassassin: true,
  catch_all_email: false,
  login_keys: true,
  system_info: false,
  cgi_access: false,
  anonymous_ftp: false,
  personal_dns_mode: 'off',
  share_server_ip: true,
};

function normalizePackageToForm(pkg) {
  const aliasVal = pkg.alias_limit ?? pkg.domain_pointers_limit ?? 0;
  const sftpVal = pkg.sftp_accounts_limit ?? pkg.ftp_accounts_limit ?? 0;
  const termVal = Boolean(pkg.terminal_enabled ?? pkg.ssh_access ?? false);
  return {
    ...DEFAULT_PKG_STATE,
    ...pkg,
    alias_limit: aliasVal,
    domain_pointers_limit: aliasVal,
    sftp_accounts_limit: sftpVal,
    ftp_accounts_limit: sftpVal,
    terminal_enabled: termVal,
    ssh_access: termVal,
    node_apps_limit: Number(pkg.node_apps_limit || 0),
    node_app_memory_mb: Math.max(64, Number(pkg.node_app_memory_mb || 512)),
    backup_retention_days: Math.max(1, Number(pkg.backup_retention_days || 14)),
  };
}

function formatMb(val) {
  const n = Number(val || 0);
  if (n <= 0) return t('Unlimited');
  if (n >= 1024 && n % 1024 === 0) return `${n / 1024} GB`;
  return `${n.toLocaleString()} MB`;
}

function formatCount(val) {
  const n = Number(val || 0);
  return n <= 0 ? '∞' : n.toLocaleString();
}

function parseQuotaInt(val, fallback = 0, bytesToMb = false) {
  if (val === undefined || val === null) return fallback;
  if (typeof val === 'number') {
    if (val < 0) return 0;
    if (bytesToMb && val >= 1048576) return Math.max(1, Math.round(val / 1048576));
    return Math.round(val);
  }
  const s = String(val).trim().toLowerCase();
  if (!s || ['unlimited', 'unlim', 'inf', 'infinite', '∞', '-1', 'none', 'null', 'all'].includes(s)) {
    return 0;
  }
  const m = s.match(/^([+-]?\d+(?:\.\d+)?)\s*([kmgt]b?)?$/i);
  if (m) {
    const num = parseFloat(m[1]);
    if (num < 0) return 0;
    const unit = (m[2] || '').toLowerCase().replace('b', '');
    if (unit === 'g') return Math.round(num * 1024);
    if (unit === 't') return Math.round(num * 1024 * 1024);
    if (unit === 'k') return num > 0 ? Math.max(1, Math.round(num / 1024)) : 0;
    const n = Math.round(num);
    if (bytesToMb && n >= 1048576) return Math.max(1, Math.round(n / 1048576));
    return n;
  }
  const numOnly = parseInt(s.replace(/[^0-9-]/g, ''), 10);
  if (isNaN(numOnly)) return fallback;
  if (numOnly < 0) return 0;
  if (bytesToMb && numOnly >= 1048576) return Math.max(1, Math.round(numOnly / 1048576));
  return numOnly;
}

function parseBoolFlag(val, fallback = true) {
  if (val === undefined || val === null) return fallback;
  if (typeof val === 'boolean') return val;
  const s = String(val).trim().toLowerCase();
  if (['on', 'yes', 'y', 'true', '1', 'enabled', 'allow', 'two', 'three'].includes(s)) return true;
  if (['off', 'no', 'n', 'false', '0', 'disabled', 'none', 'deny', '/bin/false', '/sbin/nologin'].includes(s)) return false;
  return fallback;
}

function mapDirectAdminRecord(name, raw, isReseller = false) {
  const kv = {};
  Object.entries(raw || {}).forEach(([k, v]) => {
    kv[String(k).trim().toLowerCase()] = v;
  });
  const pkg = {
    ...DEFAULT_PKG_STATE,
    name: (name || kv.name || kv.package || 'DA_Package').trim(),
    is_reseller_package: Boolean(isReseller),
    reseller_user_limit: isReseller ? 25 : 0,
    ips_limit: isReseller ? 1 : 0,
    source_panel: 'directadmin',
  };
  if ('quota' in kv) pkg.storage_limit_mb = parseQuotaInt(kv.quota, 5120);
  if ('bandwidth' in kv) pkg.bandwidth_limit_mb = parseQuotaInt(kv.bandwidth, 102400);
  if ('vdomains' in kv) pkg.website_limit = parseQuotaInt(kv.vdomains, 5);
  if ('nsubdomains' in kv) pkg.subdomain_limit = parseQuotaInt(kv.nsubdomains, 20);
  if ('domainptr' in kv) {
    const ptr = parseQuotaInt(kv.domainptr, 10);
    pkg.alias_limit = ptr;
    pkg.domain_pointers_limit = ptr;
  }
  if ('inode' in kv) pkg.inode_limit = parseQuotaInt(kv.inode, 250000);
  if ('mysql' in kv) pkg.database_limit = parseQuotaInt(kv.mysql, 5);
  if ('nemails' in kv) pkg.mail_accounts_limit = parseQuotaInt(kv.nemails, 10);
  if ('nemailf' in kv) pkg.email_forwarders_limit = parseQuotaInt(kv.nemailf, 20);
  if ('nemailr' in kv) pkg.autoresponders_limit = parseQuotaInt(kv.nemailr, 10);
  if ('nemailml' in kv) pkg.mailing_lists_limit = parseQuotaInt(kv.nemailml, 0);
  if ('ftp' in kv) {
    const ftpVal = parseQuotaInt(kv.ftp, 5);
    pkg.sftp_accounts_limit = ftpVal;
    pkg.ftp_accounts_limit = ftpVal;
  }
  if ('nusers' in kv) {
    pkg.reseller_user_limit = parseQuotaInt(kv.nusers, 25);
    pkg.is_reseller_package = true;
  }
  if ('ips' in kv) pkg.ips_limit = parseQuotaInt(kv.ips, 0);
  if ('oversell' in kv) pkg.reseller_overselling_enabled = parseBoolFlag(kv.oversell, false);
  if ('php' in kv) pkg.php_access = parseBoolFlag(kv.php, true);
  if ('ssl' in kv) pkg.ssl_access = parseBoolFlag(kv.ssl, true);
  if ('ssh' in kv) {
    const sshFlag = parseBoolFlag(kv.ssh, false);
    pkg.terminal_enabled = sshFlag;
    pkg.ssh_access = sshFlag;
  }
  if ('userssh' in kv) pkg.ssh_access_users = parseBoolFlag(kv.userssh, false);
  if ('cron' in kv) pkg.cron_jobs = parseBoolFlag(kv.cron, true);
  if ('dnscontrol' in kv) pkg.dns_control = parseBoolFlag(kv.dnscontrol, true);
  if ('git' in kv) pkg.git_access = parseBoolFlag(kv.git, true);
  if ('redis' in kv) pkg.redis_access = parseBoolFlag(kv.redis, true);
  if ('spam' in kv) pkg.spamassassin = parseBoolFlag(kv.spam, true);
  if ('catchall' in kv) pkg.catch_all_email = parseBoolFlag(kv.catchall, false);
  if ('login_keys' in kv) pkg.login_keys = parseBoolFlag(kv.login_keys, true);
  if ('sysinfo' in kv) pkg.system_info = parseBoolFlag(kv.sysinfo, false);
  if ('cgi' in kv) pkg.cgi_access = parseBoolFlag(kv.cgi, false);
  if ('aftp' in kv) pkg.anonymous_ftp = parseBoolFlag(kv.aftp, false);
  if ('serverip' in kv) pkg.share_server_ip = parseBoolFlag(kv.serverip, true);
  if ('dns' in kv) {
    const dMode = String(kv.dns || '').trim().toUpperCase();
    pkg.personal_dns_mode = ['ON', 'TWO', 'THREE', '1', 'TRUE'].includes(dMode) ? 'on' : 'off';
  }
  if ('cpu_quota' in kv || 'lve_cpu' in kv) {
    pkg.cpu_quota_percent = parseQuotaInt(kv.cpu_quota ?? kv.lve_cpu, 100);
  }
  if ('mem_max' in kv || 'lve_pmem' in kv) {
    const mem = parseQuotaInt(kv.mem_max ?? kv.lve_pmem, 1024);
    pkg.memory_max_mb = mem;
    pkg.memory_high_mb = mem > 0 ? Math.max(256, Math.round(mem * 0.75)) : 0;
  }
  if ('tasks_max' in kv || 'lve_nproc' in kv || 'lve_ep' in kv) {
    pkg.tasks_max = parseQuotaInt(kv.tasks_max ?? kv.lve_nproc ?? kv.lve_ep, 80);
  }
  return pkg;
}

function mapCpanelRecord(name, raw, isReseller = false) {
  const kv = {};
  Object.entries(raw || {}).forEach(([k, v]) => {
    kv[String(k).trim().toUpperCase()] = v;
  });
  const pkg = {
    ...DEFAULT_PKG_STATE,
    name: (name || kv.NAME || kv.PACKAGE || 'cPanel_Package').trim(),
    is_reseller_package: Boolean(isReseller),
    reseller_user_limit: isReseller ? 25 : 0,
    ips_limit: isReseller ? 1 : 0,
    source_panel: 'cpanel',
  };
  if ('QUOTA' in kv) pkg.storage_limit_mb = parseQuotaInt(kv.QUOTA, 5120);
  if ('BWLIMIT' in kv) pkg.bandwidth_limit_mb = parseQuotaInt(kv.BWLIMIT, 102400);
  if ('MAXADDON' in kv) {
    const addonRaw = String(kv.MAXADDON ?? '').trim().toLowerCase();
    if (['unlimited', 'unlim', '∞', '-1', 'all'].includes(addonRaw)) {
      pkg.website_limit = 0;
    } else {
      pkg.website_limit = 1 + parseQuotaInt(addonRaw, 0);
    }
  }
  if ('MAXSUB' in kv) pkg.subdomain_limit = parseQuotaInt(kv.MAXSUB, 20);
  if ('MAXPARK' in kv) {
    const park = parseQuotaInt(kv.MAXPARK, 10);
    pkg.alias_limit = park;
    pkg.domain_pointers_limit = park;
  }
  if ('MAXSQL' in kv) pkg.database_limit = parseQuotaInt(kv.MAXSQL, 5);
  if ('MAXPOP' in kv) pkg.mail_accounts_limit = parseQuotaInt(kv.MAXPOP, 10);
  if ('MAXFTP' in kv) {
    const ftpVal = parseQuotaInt(kv.MAXFTP, 5);
    pkg.sftp_accounts_limit = ftpVal;
    pkg.ftp_accounts_limit = ftpVal;
  }
  if ('MAXLST' in kv) pkg.mailing_lists_limit = parseQuotaInt(kv.MAXLST, 0);
  if ('HASSHELL' in kv) {
    const sh = parseBoolFlag(kv.HASSHELL, false);
    pkg.terminal_enabled = sh;
    pkg.ssh_access = sh;
  }
  if ('CGI' in kv) pkg.cgi_access = parseBoolFlag(kv.CGI, false);
  if ('IP' in kv) pkg.ips_limit = parseBoolFlag(kv.IP, false) ? 1 : 0;
  if ('MAXACCT' in kv || 'ACCT_LIMIT' in kv) {
    pkg.reseller_user_limit = parseQuotaInt(kv.MAXACCT ?? kv.ACCT_LIMIT, 25);
    pkg.is_reseller_package = true;
  }
  if ('OVERSELL' in kv) pkg.reseller_overselling_enabled = parseBoolFlag(kv.OVERSELL, false);
  return pkg;
}

function mapPleskRecord(name, raw, isReseller = false) {
  const kv = {};
  Object.entries(raw || {}).forEach(([k, v]) => {
    kv[String(k).trim().toLowerCase()] = v;
  });
  const pkg = {
    ...DEFAULT_PKG_STATE,
    name: (name || kv.name || kv.plan_name || 'Plesk_Plan').trim(),
    is_reseller_package: Boolean(isReseller),
    reseller_user_limit: isReseller ? 25 : 0,
    ips_limit: isReseller ? 1 : 0,
    source_panel: 'plesk',
  };
  if ('disk_space' in kv) pkg.storage_limit_mb = parseQuotaInt(kv.disk_space, 5120, true);
  if ('max_traffic' in kv || 'traffic' in kv) {
    pkg.bandwidth_limit_mb = parseQuotaInt(kv.max_traffic ?? kv.traffic, 102400, true);
  }
  if ('max_dom' in kv || 'max_site' in kv) {
    pkg.website_limit = parseQuotaInt(kv.max_dom ?? kv.max_site, 5);
  }
  if ('max_subdom' in kv) pkg.subdomain_limit = parseQuotaInt(kv.max_subdom, 20);
  if ('max_dom_aliases' in kv) {
    const aliasVal = parseQuotaInt(kv.max_dom_aliases, 10);
    pkg.alias_limit = aliasVal;
    pkg.domain_pointers_limit = aliasVal;
  }
  if ('max_db' in kv || 'max_mysql_db' in kv) {
    pkg.database_limit = parseQuotaInt(kv.max_db ?? kv.max_mysql_db, 5);
  }
  if ('max_box' in kv) pkg.mail_accounts_limit = parseQuotaInt(kv.max_box, 10);
  if ('max_subftp_users' in kv || 'max_wu' in kv) {
    const ftpVal = parseQuotaInt(kv.max_subftp_users ?? kv.max_wu, 5);
    pkg.sftp_accounts_limit = ftpVal;
    pkg.ftp_accounts_limit = ftpVal;
  }
  if ('max_maillists' in kv) pkg.mailing_lists_limit = parseQuotaInt(kv.max_maillists, 0);
  if ('max_cl' in kv) {
    pkg.reseller_user_limit = parseQuotaInt(kv.max_cl, 25);
    pkg.is_reseller_package = true;
  }
  if ('oversell' in kv) pkg.reseller_overselling_enabled = parseBoolFlag(kv.oversell, false);
  if ('allow_sh_access' in kv || 'shell' in kv) {
    const shVal = String(kv.allow_sh_access ?? kv.shell ?? '').trim().toLowerCase();
    const shFlag = !['false', 'off', 'no', '0', 'none', '/bin/false', '/sbin/nologin'].includes(shVal);
    pkg.terminal_enabled = shFlag;
    pkg.ssh_access = shFlag;
  }
  if ('php' in kv) pkg.php_access = parseBoolFlag(kv.php, true);
  if ('ssl' in kv) pkg.ssl_access = parseBoolFlag(kv.ssl, true);
  if ('manage_crontab' in kv) pkg.cron_jobs = parseBoolFlag(kv.manage_crontab, true);
  if ('manage_dns' in kv) pkg.dns_control = parseBoolFlag(kv.manage_dns, true);
  if ('manage_spamfilter' in kv) pkg.spamassassin = parseBoolFlag(kv.manage_spamfilter, true);
  if ('git' in kv) pkg.git_access = parseBoolFlag(kv.git, true);
  return pkg;
}

function parseRawPackageContent(text, defaultName, panelHint = 'directadmin', isReseller = false) {
  const trimmed = (text || '').trim();
  if (!trimmed) return [];

  // 1. Check if XML (Plesk <service-plan> / <reseller-plan> / <domain-template>)
  if (trimmed.startsWith('<') && (trimmed.includes('service-plan') || trimmed.includes('reseller-plan') || trimmed.includes('limits') || trimmed.includes('domain-template'))) {
    try {
      const parser = new DOMParser();
      const doc = parser.parseFromString(trimmed, 'text/xml');
      const planNodes = Array.from(
        doc.querySelectorAll('service-plan > get > result, reseller-plan > get > result, service-plan > add, reseller-plan > add, domain-template, plan')
      );
      const targets = planNodes.length > 0 ? planNodes : [doc.documentElement];
      const parsedPlans = [];
      targets.forEach((node, idx) => {
        const nameNode = node.querySelector(':scope > name') || node.querySelector('name');
        const pName = (nameNode ? nameNode.textContent : defaultName || `Plesk_Plan_${idx + 1}`).trim();
        const flat = {};
        node.querySelectorAll('limit').forEach(lim => {
          const ln = lim.querySelector('name')?.textContent?.trim();
          const lv = lim.querySelector('value')?.textContent?.trim();
          if (ln) flat[ln] = lv;
        });
        node.querySelectorAll('permission').forEach(perm => {
          const pn = perm.querySelector('name')?.textContent?.trim();
          const pv = perm.querySelector('value')?.textContent?.trim();
          if (pn) flat[pn] = pv;
        });
        if (Object.keys(flat).length > 0) {
          parsedPlans.push(mapPleskRecord(pName, flat, isReseller));
        }
      });
      if (parsedPlans.length > 0) return parsedPlans;
    } catch (_) {
      // Fall through to text parser
    }
  }

  // 2. Check if JSON (WHM listpkgs, Plesk REST API, DirectAdmin JSON, or array of packages)
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const obj = JSON.parse(trimmed);
      const items = Array.isArray(obj)
        ? obj
        : (obj?.data?.pkg || obj?.pkg || obj?.packages || obj?.plans || [obj]);
      const out = [];
      items.forEach((it, idx) => {
        if (!it || typeof it !== 'object') return;
        const pName = String(it.name || it.PACKAGE || it.pkg || defaultName || `Imported_${idx + 1}`).trim();
        if (Array.isArray(it.limits) || 'disk_space' in it || 'max_dom' in it || panelHint === 'plesk') {
          const flat = { ...it };
          if (Array.isArray(it.limits)) {
            it.limits.forEach(l => { if (l?.name) flat[l.name] = l.value; });
          }
          if (Array.isArray(it.permissions)) {
            it.permissions.forEach(p => { if (p?.name) flat[p.name] = p.value; });
          }
          out.push(mapPleskRecord(pName, flat, isReseller));
        } else if ('QUOTA' in it || 'BWLIMIT' in it || 'MAXADDON' in it || 'MAXPOP' in it || panelHint === 'cpanel') {
          out.push(mapCpanelRecord(pName, it, isReseller));
        } else {
          out.push(mapDirectAdminRecord(pName, it, isReseller));
        }
      });
      if (out.length > 0) return out;
    } catch (_) {
      // Fall through to line/section parser
    }
  }

  // 3. Support multi-package blocks separated by [package_name] or --- package_name ---
  const blocks = [];
  let currentHeader = defaultName || (panelHint === 'cpanel' ? 'cPanel_Package' : panelHint === 'plesk' ? 'Plesk_Plan' : 'DA_Package');
  let currentLines = [];
  for (const rawLine of trimmed.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#') || line.startsWith(';')) continue;
    const secMatch = line.match(/^\[([^\]]+)\]$/) || line.match(/^===+\s*([^=]+?)\s*===+$/);
    if (secMatch) {
      if (currentLines.length > 0) {
        blocks.push({ name: currentHeader, lines: currentLines });
        currentLines = [];
      }
      currentHeader = secMatch[1].trim();
      continue;
    }
    currentLines.push(line);
  }
  if (currentLines.length > 0) {
    blocks.push({ name: currentHeader, lines: currentLines });
  }

  return blocks.map(blk => {
    const kv = {};
    blk.lines.forEach(line => {
      // Also support URL query string on a single line: bandwidth=102400&quota=5120&vdomains=5
      if (line.includes('&') && line.includes('=') && !line.includes(' ')) {
        line.split('&').forEach(pair => {
          const eqIdx = pair.indexOf('=');
          if (eqIdx > 0) {
            const k = decodeURIComponent(pair.slice(0, eqIdx).trim());
            const v = decodeURIComponent(pair.slice(eqIdx + 1).trim());
            kv[k] = v;
          }
        });
        return;
      }
      const eqIdx = line.indexOf('=');
      const colonIdx = line.indexOf(':');
      if (eqIdx > 0) {
        const k = line.slice(0, eqIdx).trim();
        const v = line.slice(eqIdx + 1).trim();
        kv[k] = v;
      } else if (colonIdx > 0) {
        const k = line.slice(0, colonIdx).trim();
        const v = line.slice(colonIdx + 1).trim();
        kv[k] = v;
      }
    });

    const pkgName = String(kv.name || kv.NAME || kv.package || kv.PACKAGE || blk.name).trim();
    const keysUpper = Object.keys(kv).map(k => k.toUpperCase());
    const keysLower = Object.keys(kv).map(k => k.toLowerCase());

    if (keysUpper.some(k => ['BWLIMIT', 'MAXADDON', 'MAXPOP', 'MAXSQL', 'HASSHELL', 'FEATURELIST'].includes(k))) {
      return mapCpanelRecord(pkgName, kv, isReseller);
    }
    if (keysLower.some(k => ['disk_space', 'max_traffic', 'max_dom', 'max_subdom', 'max_box', 'max_wu'].includes(k))) {
      return mapPleskRecord(pkgName, kv, isReseller);
    }
    if (panelHint === 'cpanel') return mapCpanelRecord(pkgName, kv, isReseller);
    if (panelHint === 'plesk') return mapPleskRecord(pkgName, kv, isReseller);
    return mapDirectAdminRecord(pkgName, kv, isReseller);
  });
}

function getSampleConfigText(panel, isReseller) {
  if (panel === 'directadmin') {
    if (isReseller) {
      return `[DA_Reseller_Silver]
bandwidth=512000
quota=51200
inode=1000000
vdomains=unlimited
nsubdomains=unlimited
nemails=unlimited
nemailf=unlimited
nemailr=unlimited
mysql=unlimited
domainptr=unlimited
ftp=unlimited
nusers=30
ips=1
oversell=ON
php=ON
ssl=ON
ssh=OFF
userssh=ON
dnscontrol=ON
cron=ON
sysinfo=ON
login_keys=ON
dns=TWO
serverip=ON

[DA_Reseller_Gold]
bandwidth=unlimited
quota=153600
inode=3000000
vdomains=unlimited
nsubdomains=unlimited
nemails=unlimited
mysql=unlimited
ftp=unlimited
nusers=100
ips=2
oversell=ON
php=ON
ssl=ON
ssh=ON
userssh=ON
dnscontrol=ON
cron=ON
dns=TWO
serverip=ON`;
    }
    return `[DA_Starter_Basic]
bandwidth=51200
quota=5120
inode=150000
vdomains=2
nsubdomains=10
nemails=10
nemailf=20
nemailr=5
nemailml=0
mysql=2
domainptr=5
ftp=2
aftp=OFF
cgi=OFF
php=ON
spam=ON
catchall=OFF
ssl=ON
ssh=OFF
cron=ON
sysinfo=OFF
login_keys=ON
dnscontrol=ON
git=ON
redis=ON

[DA_Business_Pro]
bandwidth=unlimited
quota=25600
inode=500000
vdomains=15
nsubdomains=unlimited
nemails=unlimited
nemailf=unlimited
mysql=20
domainptr=20
ftp=10
php=ON
spam=ON
catchall=ON
ssl=ON
ssh=ON
cron=ON
login_keys=ON
dnscontrol=ON
git=ON
redis=ON`;
  }

  if (panel === 'cpanel') {
    if (isReseller) {
      return `[cPanel_Reseller_Starter]
QUOTA=51200
BWLIMIT=512000
MAXACCT=25
OVERSELL=y
MAXADDON=unlimited
MAXSUB=unlimited
MAXPARK=unlimited
MAXSQL=unlimited
MAXPOP=unlimited
MAXFTP=unlimited
HASSHELL=n
CGI=y
IP=y

[cPanel_Reseller_Enterprise]
QUOTA=204800
BWLIMIT=unlimited
MAXACCT=100
OVERSELL=y
MAXADDON=unlimited
MAXSUB=unlimited
MAXPARK=unlimited
MAXSQL=unlimited
MAXPOP=unlimited
MAXFTP=unlimited
HASSHELL=y
CGI=y
IP=y`;
    }
    return `[cPanel_WP_Standard]
QUOTA=10240
BWLIMIT=102400
MAXADDON=4
MAXSUB=25
MAXPARK=10
MAXSQL=10
MAXPOP=25
MAXFTP=5
MAXLST=0
HASSHELL=n
CGI=n
IP=n
FEATURELIST=default

[cPanel_Ecommerce_VIP]
QUOTA=51200
BWLIMIT=unlimited
MAXADDON=19
MAXSUB=unlimited
MAXPARK=unlimited
MAXSQL=50
MAXPOP=unlimited
MAXFTP=20
HASSHELL=y
CGI=y
IP=y
FEATURELIST=default`;
  }

  // Plesk XML / Key-Value format
  if (isReseller) {
    return `<?xml version="1.0" encoding="UTF-8"?>
<packet>
  <reseller-plan>
    <get>
      <result>
        <name>Plesk Reseller Partner</name>
        <limits>
          <limit><name>max_cl</name><value>40</value></limit>
          <limit><name>disk_space</name><value>107374182400</value></limit>
          <limit><name>max_traffic</name><value>-1</value></limit>
          <limit><name>max_dom</name><value>-1</value></limit>
          <limit><name>max_subdom</name><value>-1</value></limit>
          <limit><name>max_db</name><value>-1</value></limit>
          <limit><name>max_box</name><value>-1</value></limit>
          <limit><name>oversell</name><value>true</value></limit>
        </limits>
        <permissions>
          <permission><name>allow_sh_access</name><value>true</value></permission>
          <permission><name>manage_dns</name><value>true</value></permission>
          <permission><name>manage_crontab</name><value>true</value></permission>
        </permissions>
      </result>
    </get>
  </reseller-plan>
</packet>`;
  }
  return `<?xml version="1.0" encoding="UTF-8"?>
<packet>
  <service-plan>
    <get>
      <result>
        <name>Plesk Web Host Edition</name>
        <limits>
          <limit><name>disk_space</name><value>16106127360</value></limit>
          <limit><name>max_traffic</name><value>214748364800</value></limit>
          <limit><name>max_dom</name><value>10</value></limit>
          <limit><name>max_subdom</name><value>50</value></limit>
          <limit><name>max_dom_aliases</name><value>20</value></limit>
          <limit><name>max_db</name><value>15</value></limit>
          <limit><name>max_box</name><value>30</value></limit>
          <limit><name>max_subftp_users</name><value>10</value></limit>
        </limits>
        <permissions>
          <permission><name>allow_sh_access</name><value>false</value></permission>
          <permission><name>php</name><value>true</value></permission>
          <permission><name>ssl</name><value>true</value></permission>
          <permission><name>manage_dns</name><value>true</value></permission>
          <permission><name>manage_crontab</name><value>true</value></permission>
          <permission><name>git</name><value>true</value></permission>
        </permissions>
      </result>
    </get>
  </service-plan>
</packet>`;
}

export function PackagesPage({ mode = 'users' }) {
  const {
    packages, users, request, setNotice, setError, loading, loadPackages, loadUsers,
  } = useAppContext();

  const [packageTab, setPackageTab] = useState('list');
  const [pkgSearch, setPkgSearch] = useState('');
  const [newPackage, setNewPackage] = useState({ ...DEFAULT_PKG_STATE });
  const [editingPackageId, setEditingPackageId] = useState(null);
  const [editingPackageForm, setEditingPackageForm] = useState({ ...DEFAULT_PKG_STATE });

  // Import Modal State
  const [showImportModal, setShowImportModal] = useState(false);
  const [importPanel, setImportPanel] = useState('directadmin'); // directadmin | cpanel | plesk
  const [importMethod, setImportMethod] = useState('file'); // file | remote
  const [rawImportText, setRawImportText] = useState('');
  const [parsedPackages, setParsedPackages] = useState([]);
  const [conflictMode, setConflictMode] = useState('overwrite'); // overwrite | skip | rename
  const [remoteForm, setRemoteForm] = useState({
    host_url: '',
    username: 'admin',
    secret: '',
    auth_type: 'password',
    verify_ssl: false,
  });
  const fileInputRef = useRef(null);

  useEffect(() => {
    setPackageTab('list');
    setEditingPackageId(null);
    if (!packages || packages.length === 0) {
      if (typeof loadPackages === 'function') loadPackages();
    }
    if ((!users || users.length === 0) && typeof loadUsers === 'function') {
      loadUsers();
    }
  }, [mode]);

  function sanitizePayload(formObj) {
    const clean = { ...formObj };
    const intFields = [
      'storage_limit_mb', 'bandwidth_limit_mb', 'website_limit', 'subdomain_limit',
      'alias_limit', 'domain_pointers_limit', 'inode_limit', 'sftp_accounts_limit',
      'ftp_accounts_limit', 'backup_retention_days', 'ips_limit', 'reseller_user_limit',
      'database_limit', 'mysql_storage_limit_mb', 'mysql_max_db_size_mb',
      'mail_accounts_limit', 'email_forwarders_limit', 'autoresponders_limit', 'mailing_lists_limit',
      'node_apps_limit', 'node_app_memory_mb',
      'cpu_quota_percent', 'tasks_max', 'memory_high_mb', 'memory_max_mb',
      'io_read_mbps', 'io_write_mbps', 'iops_read_max', 'iops_write_max',
    ];
    for (const key of intFields) {
      clean[key] = Math.max(0, parseInt(clean[key], 10) || 0);
    }
    clean.domain_pointers_limit = clean.alias_limit;
    clean.ftp_accounts_limit = clean.sftp_accounts_limit;
    clean.ssh_access = Boolean(clean.terminal_enabled);
    clean.terminal_enabled = Boolean(clean.terminal_enabled);
    clean.node_app_memory_mb = Math.min(16384, Math.max(64, clean.node_app_memory_mb || 512));
    clean.backup_retention_days = Math.min(365, Math.max(1, clean.backup_retention_days || 14));
    clean.is_reseller_package = mode === 'resellers';
    delete clean._selected;
    delete clean._id;
    delete clean.source_panel;
    return clean;
  }

  function openImportModal(defaultPanel = 'directadmin') {
    setImportPanel(defaultPanel);
    setImportMethod('file');
    setShowImportModal(true);
  }

  async function handleFileSelect(e) {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;
    const isResellerMode = mode === 'resellers';
    const newlyParsed = [];
    for (const file of files) {
      try {
        const content = await file.text();
        const baseName = file.name.replace(/\.(pkg|conf|txt|json|xml|plan)$/i, '').trim() || file.name;
        const items = parseRawPackageContent(content, baseName, importPanel, isResellerMode);
        items.forEach(it => {
          newlyParsed.push({
            ...it,
            is_reseller_package: isResellerMode,
            _selected: true,
            _id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
          });
        });
      } catch (err) {
        setError(`${t('Failed to read file')}: ${file.name}`);
      }
    }
    if (newlyParsed.length > 0) {
      setParsedPackages(prev => [...prev, ...newlyParsed]);
      setNotice(t('Parsed {count} package(s) from uploaded file(s).', { count: newlyParsed.length }));
    } else {
      setError(t('Could not detect any valid package configuration in the selected file(s).'));
    }
    if (fileInputRef.current) fileInputRef.current.value = '';
  }

  function handleParseRawText(customText) {
    const textToParse = typeof customText === 'string' ? customText : rawImportText;
    if (!textToParse.trim()) {
      setError(t('Please paste package configuration content or load a sample template first.'));
      return;
    }
    const isResellerMode = mode === 'resellers';
    const items = parseRawPackageContent(textToParse, '', importPanel, isResellerMode);
    if (items.length === 0) {
      setError(t('Could not detect any valid package configuration.'));
      return;
    }
    const decorated = items.map(it => ({
      ...it,
      is_reseller_package: isResellerMode,
      _selected: true,
      _id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    }));
    setParsedPackages(decorated);
  }

  function handleLoadSample() {
    const sample = getSampleConfigText(importPanel, mode === 'resellers');
    setRawImportText(sample);
    handleParseRawText(sample);
  }

  async function handleFetchRemotePackages(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (!remoteForm.host_url.trim() || !remoteForm.secret.trim()) {
      setError(t('Please enter the remote Panel URL and Password / API Token.'));
      return;
    }
    const res = await request('/packages/fetch-remote', {
      method: 'POST',
      body: JSON.stringify({
        panel_type: importPanel,
        host_url: remoteForm.host_url.trim(),
        username: remoteForm.username.trim() || 'admin',
        secret: remoteForm.secret,
        auth_type: remoteForm.auth_type,
        is_reseller_package: mode === 'resellers',
        verify_ssl: Boolean(remoteForm.verify_ssl),
      }),
    }, t('Connecting to remote panel and fetching packages...'));
    if (res && Array.isArray(res.packages)) {
      if (res.packages.length === 0) {
        setError(t('Connected successfully, but no packages were found on the remote panel.'));
        return;
      }
      const isResellerMode = mode === 'resellers';
      const decorated = res.packages.map(it => ({
        ...DEFAULT_PKG_STATE,
        ...it,
        is_reseller_package: isResellerMode,
        source_panel: importPanel,
        _selected: true,
        _id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
      }));
      setParsedPackages(decorated);
      setNotice(t('Fetched {count} package(s) from remote panel.', { count: decorated.length }));
    }
  }

  function handleLoadSingleIntoEditor(pkg) {
    const normalized = normalizePackageToForm(pkg);
    setEditingPackageId(null);
    setNewPackage({
      ...normalized,
      is_reseller_package: mode === 'resellers',
    });
    setShowImportModal(false);
    setPackageTab('create');
    setNotice(t('Loaded package "{name}" into the editor. Review and click Create Package to save.', { name: normalized.name }));
  }

  async function handleBatchImportSubmit() {
    const selected = parsedPackages.filter(p => p._selected && p.name && p.name.trim());
    if (selected.length === 0) {
      setError(t('Please select at least one package with a valid name to import.'));
      return;
    }
    const sanitizedList = selected.map(p => sanitizePayload(p));
    const res = await request('/packages/import', {
      method: 'POST',
      body: JSON.stringify({
        packages: sanitizedList,
        conflict_mode: conflictMode,
        source_panel: importPanel,
      }),
    }, t('Importing packages...'));
    if (res && res.ok) {
      await loadPackages();
      setShowImportModal(false);
      setParsedPackages([]);
      setRawImportText('');
      setNotice(
        t('Import completed: {created} created, {updated} updated, {skipped} skipped.', {
          created: res.created ?? 0,
          updated: res.updated ?? 0,
          skipped: res.skipped ?? 0,
        })
      );
    }
  }

  async function createPackage(e) {
    if (e && e.preventDefault) e.preventDefault();
    if (!newPackage.name.trim()) {
      setError(t('Please enter a package name.'));
      return;
    }
    const payload = sanitizePayload(newPackage);
    const created = await request('/packages', {
      method: 'POST',
      body: JSON.stringify(payload),
    }, t('Creating package...'));
    if (created?.id) {
      await loadPackages();
      setNewPackage({ ...DEFAULT_PKG_STATE });
      setNotice(t('Package created successfully.'));
      setPackageTab('list');
    }
  }

  function startEditingPackage(pkg) {
    setEditingPackageId(pkg.id);
    setEditingPackageForm(normalizePackageToForm(pkg));
    setPackageTab('create');
  }

  function cancelEditingPackage() {
    setEditingPackageId(null);
    setEditingPackageForm({ ...DEFAULT_PKG_STATE });
    setPackageTab('list');
  }

  async function savePackageEdit(packageId) {
    if (!editingPackageForm.name.trim()) {
      setError(t('Please enter a package name.'));
      return;
    }
    const payload = sanitizePayload(editingPackageForm);
    const updated = await request(`/packages/${packageId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    }, t('Updating package...'));
    if (updated?.id) {
      await loadPackages();
      cancelEditingPackage();
      setNotice(t('Package updated successfully.'));
    }
  }

  async function deletePackage(pkg) {
    if (!confirm(t('Delete package "{name}"?', { name: pkg.name }))) return;
    const res = await request(`/packages/${pkg.id}`, { method: 'DELETE' }, t('Deleting package...'));
    if (res) {
      await loadPackages();
      if (editingPackageId === pkg.id) cancelEditingPackage();
      setNotice(t('Package deleted.'));
    }
  }

  const isEdit = Boolean(editingPackageId);
  const currentForm = isEdit ? editingPackageForm : newPackage;
  const setCurrentForm = isEdit ? setEditingPackageForm : setNewPackage;

  function renderQuotaRow({
    label,
    field,
    unit = '',
    defaultFallback = 10,
    min = 0,
    max = undefined,
    allowUnlimited = true,
    zeroLabel = t('Unlimited'),
  }) {
    const rawVal = Number(currentForm[field] ?? 0);
    const isZeroChecked = allowUnlimited && rawVal === 0;

    return (
      <div className="pkg-field-row" key={field}>
        <span className="pkg-field-label">{label}</span>
        <div className="pkg-input-unit-wrap">
          <input
            type="number"
            min={allowUnlimited ? (isZeroChecked ? 0 : 1) : min}
            max={max}
            disabled={isZeroChecked}
            value={isZeroChecked ? '' : rawVal}
            placeholder={isZeroChecked ? zeroLabel : String(defaultFallback)}
            onChange={e => {
              const v = e.target.value === '' ? (allowUnlimited ? 0 : min) : Math.max(min, parseInt(e.target.value, 10) || 0);
              setCurrentForm(prev => ({ ...prev, [field]: max ? Math.min(max, v) : v }));
            }}
          />
          {unit && <span className="pkg-unit-tag">{unit}</span>}
        </div>
        {allowUnlimited ? (
          <label className="pkg-unlim-check">
            <input
              type="checkbox"
              checked={isZeroChecked}
              onChange={e => {
                setCurrentForm(prev => ({
                  ...prev,
                  [field]: e.target.checked ? 0 : defaultFallback,
                }));
              }}
            />
            <span>{zeroLabel}</span>
          </label>
        ) : (
          <span style={{ fontSize: '12px', color: 'var(--text-muted)' }}>
            {min}–{max || 365} {unit}
          </span>
        )}
      </div>
    );
  }

  function renderFeatureCheck(field, label, onChangeCustom) {
    const checked = Boolean(currentForm[field]);
    return (
      <label className="pkg-feature-check" key={field}>
        <input
          type="checkbox"
          checked={checked}
          onChange={e => {
            if (onChangeCustom) {
              onChangeCustom(e.target.checked);
            } else {
              setCurrentForm(prev => ({ ...prev, [field]: e.target.checked }));
            }
          }}
        />
        <span>{label}</span>
      </label>
    );
  }

  const filteredPackages = (packages || []).filter(p => {
    const matchMode = mode === 'resellers' ? Boolean(p.is_reseller_package) : !p.is_reseller_package;
    const matchSearch = !pkgSearch || p.name.toLowerCase().includes(pkgSearch.toLowerCase());
    return matchMode && matchSearch;
  });

  const selectedImportCount = parsedPackages.filter(p => p._selected).length;

  return (
    <div className="section">
      <div className="section-title" style={{ marginBottom: '16px', alignItems: 'center', flexWrap: 'wrap' }}>
        <div>
          <h2 style={{ margin: 0 }}>
            {mode === 'resellers' ? t('Manage Reseller Packages') : t('Manage User Packages')}
          </h2>
        </div>

        {packageTab === 'list' ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <input
              type="text"
              placeholder={t('Search packages by name...')}
              value={pkgSearch}
              onChange={e => setPkgSearch(e.target.value)}
              style={{ width: '210px', height: '36px', margin: 0 }}
            />
            <button
              className="secondary-light"
              disabled={!!loading}
              onClick={() => loadPackages()}
            >
              <RefreshCw size={14} /> {t('Refresh')}
            </button>
            <button
              type="button"
              className="secondary-light"
              onClick={() => openImportModal('directadmin')}
              title={t('Import packages from DirectAdmin, cPanel/WHM, or Plesk')}
            >
              <Upload size={14} /> {t('Import Packages')}
            </button>
            <button
              onClick={() => {
                setEditingPackageId(null);
                setNewPackage({ ...DEFAULT_PKG_STATE });
                setPackageTab('create');
              }}
            >
              <Plus size={14} /> {t('Create Package')}
            </button>
          </div>
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
            <button
              type="button"
              className="secondary-light"
              onClick={() => openImportModal('directadmin')}
            >
              <Upload size={14} /> {t('Import from DA / cPanel / Plesk')}
            </button>
            <button className="secondary-light" onClick={cancelEditingPackage}>
              <ArrowLeft size={14} /> {t('Back to Package List')}
            </button>
          </div>
        )}
      </div>

      {packageTab === 'list' && (
        <div className="pkg-list-card">
          {filteredPackages.length === 0 ? (
            <div style={{ padding: '2.5rem' }}>
              <EmptyState icon={Boxes} message={t('No packages found.')} />
            </div>
          ) : (
            <div style={{ overflowX: 'auto', width: '100%' }}>
              <table className="pkg-clean-table">
                <thead>
                  <tr>
                    <th>{t('Package Name')}</th>
                    <th>{t('Disk')} / {t('Bandwidth')}</th>
                    <th>
                      {mode === 'resellers'
                        ? `${t('Max Sub-Accounts (Users)')} & IPs`
                        : `${t('Websites')} & ${t('Databases')}`}
                    </th>
                    <th>{t('Email')} & {t('App Runtimes')}</th>
                    <th>CPU / RAM</th>
                    <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredPackages.map(pkg => {
                    const assignedCount = (users || []).filter(
                      u => u.package_id === pkg.id || u.reseller_package_id === pkg.id
                    ).length;
                    const nodeApps = Number(pkg.node_apps_limit || 0);
                    const nodeMem = Number(pkg.node_app_memory_mb || 512);

                    return (
                      <tr key={pkg.id}>
                        <td>
                          <strong style={{ color: 'var(--primary)', fontSize: '14px' }}>{pkg.name}</strong>
                          <span className="muted" style={{ marginLeft: '8px', fontSize: '12px' }}>
                            ({assignedCount} {t('Users')})
                          </span>
                        </td>
                        <td>
                          {formatMb(pkg.storage_limit_mb)} <span className="muted">/</span> {formatMb(pkg.bandwidth_limit_mb)}
                        </td>
                        <td>
                          {mode === 'resellers' ? (
                            <>
                              {formatCount(pkg.reseller_user_limit)} {t('Users')} <span className="muted">·</span>{' '}
                              {formatCount(pkg.ips_limit)} IPs
                              {pkg.reseller_overselling_enabled && (
                                <span className="badge ok" style={{ marginLeft: '6px', fontSize: '11px' }}>
                                  Oversell
                                </span>
                              )}
                            </>
                          ) : (
                            <>
                              {formatCount(pkg.website_limit)} {t('Sites')} <span className="muted">·</span> {formatCount(pkg.database_limit)} DBs
                            </>
                          )}
                        </td>
                        <td>
                          {formatCount(pkg.mail_accounts_limit)} Mail <span className="muted">·</span>{' '}
                          {nodeApps > 0 ? (
                            <span className="badge ok" style={{ fontSize: '11.5px' }}>
                              {nodeApps} Apps ({nodeMem} MB)
                            </span>
                          ) : (
                            <span className="muted" style={{ fontSize: '12.5px' }}>{t('Apps: Off')}</span>
                          )}
                        </td>
                        <td>
                          {pkg.cpu_quota_percent > 0 ? `${pkg.cpu_quota_percent}%` : '∞'} CPU <span className="muted">·</span>{' '}
                          {formatMb(pkg.memory_max_mb)} RAM
                        </td>
                        <td style={{ textAlign: 'right' }}>
                          <div style={{ display: 'inline-flex', gap: '6px' }}>
                            <button
                              className="secondary-light mini"
                              onClick={() => startEditingPackage(pkg)}
                            >
                              <Pencil size={13} /> {t('Modify')}
                            </button>
                            <button
                              className="danger mini"
                              onClick={() => deletePackage(pkg)}
                            >
                              <Trash2 size={13} /> {t('Delete')}
                            </button>
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {packageTab === 'create' && (
        <div className="pkg-editor-shell">
          <div className="pkg-top-bar">
            <div className="pkg-name-inline">
              <label>{t('Package Name')}</label>
              <input
                type="text"
                placeholder={mode === 'resellers' ? t('e.g. Reseller Gold') : t('e.g. Business Hosting')}
                value={currentForm.name}
                onChange={e => setCurrentForm(prev => ({ ...prev, name: e.target.value }))}
              />
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <button type="button" className="secondary-light" onClick={cancelEditingPackage}>
                <X size={14} /> {t('Cancel')}
              </button>
              <button
                type="button"
                disabled={!!loading}
                onClick={isEdit ? () => savePackageEdit(editingPackageId) : createPackage}
              >
                <Save size={14} /> {isEdit ? t('Save Changes') : t('Create Package')}
              </button>
            </div>
          </div>

          <div className="pkg-cards-grid">
            {/* Left Column: Web, Database & Email Quotas */}
            <div className="pkg-card">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <Globe size={15} /> {t('Web, Domains & Storage Quotas')}
                </h4>
              </div>
              <div className="pkg-card-body">
                {mode === 'resellers' && (
                  <>
                    {renderQuotaRow({
                      label: t('Max Sub-Accounts (Users)'),
                      field: 'reseller_user_limit',
                      unit: t('Users'),
                      defaultFallback: 25,
                    })}
                    {renderQuotaRow({
                      label: t('Dedicated IPs Quota'),
                      field: 'ips_limit',
                      unit: 'IPs',
                      defaultFallback: 1,
                    })}
                  </>
                )}
                {renderQuotaRow({
                  label: t('Disk Space Quota'),
                  field: 'storage_limit_mb',
                  unit: 'MB',
                  defaultFallback: 5120,
                })}
                {renderQuotaRow({
                  label: t('Monthly Bandwidth'),
                  field: 'bandwidth_limit_mb',
                  unit: 'MB',
                  defaultFallback: 102400,
                })}
                {renderQuotaRow({
                  label: t('Hosted Websites (Domains)'),
                  field: 'website_limit',
                  unit: t('Sites'),
                  defaultFallback: 5,
                })}
                {renderQuotaRow({
                  label: t('Subdomains'),
                  field: 'subdomain_limit',
                  unit: 'Subs',
                  defaultFallback: 20,
                })}
                {renderQuotaRow({
                  label: t('Domain Aliases / Pointers'),
                  field: 'alias_limit',
                  unit: 'Aliases',
                  defaultFallback: 10,
                })}
                {renderQuotaRow({
                  label: t('MySQL Databases'),
                  field: 'database_limit',
                  unit: 'DBs',
                  defaultFallback: 5,
                })}
                {renderQuotaRow({
                  label: t('Total MySQL Storage Quota'),
                  field: 'mysql_storage_limit_mb',
                  unit: 'MB',
                  defaultFallback: 2048,
                })}
                {renderQuotaRow({
                  label: t('Email Accounts (Mailboxes)'),
                  field: 'mail_accounts_limit',
                  unit: 'Mail',
                  defaultFallback: 10,
                })}
                {renderQuotaRow({
                  label: t('Email Forwarders'),
                  field: 'email_forwarders_limit',
                  unit: 'Fwd',
                  defaultFallback: 20,
                })}
                {renderQuotaRow({
                  label: t('SFTP / FTP Sub-Accounts'),
                  field: 'sftp_accounts_limit',
                  unit: 'SFTP',
                  defaultFallback: 5,
                })}
              </div>
            </div>

            {/* Right Column: Application Runtimes & Cgroups v2 Hardware Limits */}
            <div className="pkg-card">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <Cpu size={15} /> {t('App Runtimes')} & {t('Hardware (Cgroups v2)')}
                </h4>
              </div>
              <div className="pkg-card-body">
                {renderQuotaRow({
                  label: t('Max Applications (node_apps_limit)'),
                  field: 'node_apps_limit',
                  unit: 'Apps',
                  defaultFallback: 3,
                  allowUnlimited: true,
                  zeroLabel: t('Disabled (0)'),
                })}
                {renderQuotaRow({
                  label: t('Memory Limit per App (node_app_memory_mb)'),
                  field: 'node_app_memory_mb',
                  unit: 'MB',
                  defaultFallback: 512,
                  min: 64,
                  max: 16384,
                  allowUnlimited: false,
                })}
                {renderQuotaRow({
                  label: t('CPU Quota'),
                  field: 'cpu_quota_percent',
                  unit: '%',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('RAM Hard Limit (memory.max)'),
                  field: 'memory_max_mb',
                  unit: 'MB',
                  defaultFallback: 1024,
                })}
                {renderQuotaRow({
                  label: t('RAM Soft Limit (memory.high)'),
                  field: 'memory_high_mb',
                  unit: 'MB',
                  defaultFallback: 768,
                })}
                {renderQuotaRow({
                  label: t('Max Processes / Tasks (tasks.max)'),
                  field: 'tasks_max',
                  unit: 'PIDs',
                  defaultFallback: 80,
                })}
                {renderQuotaRow({
                  label: t('Disk Read Bandwidth'),
                  field: 'io_read_mbps',
                  unit: 'MB/s',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('Disk Write Bandwidth'),
                  field: 'io_write_mbps',
                  unit: 'MB/s',
                  defaultFallback: 100,
                })}
                {renderQuotaRow({
                  label: t('Inodes Limit'),
                  field: 'inode_limit',
                  unit: 'Files',
                  defaultFallback: 250000,
                })}
                {renderQuotaRow({
                  label: t('Backup Retention Period'),
                  field: 'backup_retention_days',
                  unit: t('Days'),
                  defaultFallback: 14,
                  min: 1,
                  max: 365,
                  allowUnlimited: false,
                })}
              </div>
            </div>

            {/* Bottom Full-Width Card: Compact Single-Line Checkboxes */}
            <div className="pkg-card pkg-card-full">
              <div className="pkg-card-header">
                <h4 className="pkg-card-title">
                  <ShieldCheck size={15} /> {t('Package Features & Access Permissions')}
                </h4>
              </div>
              <div className="pkg-feature-grid">
                {renderFeatureCheck('php_access', t('PHP Runtime Access'))}
                {renderFeatureCheck('wordpress_enabled', t('WordPress Toolkit'))}
                {renderFeatureCheck('waf_enabled', t('Web Application Firewall (WAF)'))}
                {renderFeatureCheck('ssl_access', t('SSL Certificates'))}
                {renderFeatureCheck('terminal_enabled', t('SSH & Web Terminal Access'), checked =>
                  setCurrentForm(prev => ({
                    ...prev,
                    terminal_enabled: checked,
                    ssh_access: checked,
                  }))
                )}
                {renderFeatureCheck('redis_access', t('Redis Object Cache'))}
                {renderFeatureCheck('cron_jobs', t('Cron Jobs'))}
                {renderFeatureCheck('dns_control', t('DNS Zone Control'))}
                {renderFeatureCheck('git_access', t('Git Deployment'))}
                {renderFeatureCheck('spamassassin', t('Spam Filter Controls'))}
                {renderFeatureCheck('catch_all_email', t('Catch-All Email'))}
                {renderFeatureCheck('login_keys', t('API & Login Keys'))}

                {mode === 'resellers' && (
                  <>
                    {renderFeatureCheck('reseller_overselling_enabled', t('Allow Overselling'))}
                    {renderFeatureCheck('share_server_ip', t('Share Server Main IP'))}
                    {renderFeatureCheck('ssh_access_users', t('Grant SSH to Sub-Users'))}
                    <label className="pkg-feature-check">
                      <input
                        type="checkbox"
                        checked={currentForm.personal_dns_mode === 'on'}
                        onChange={e =>
                          setCurrentForm(prev => ({
                            ...prev,
                            personal_dns_mode: e.target.checked ? 'on' : 'off',
                          }))
                        }
                      />
                      <span>{t('Custom White-Label Nameservers')}</span>
                    </label>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Modal Import Packages from DirectAdmin, cPanel/WHM, Plesk */}
      {showImportModal && (
        <div
          className="modal-backdrop pkg-import-modal-backdrop"
          onClick={() => setShowImportModal(false)}
          style={{ zIndex: 1200 }}
        >
          <div
            className="modal-card pkg-import-modal-card"
            onClick={e => e.stopPropagation()}
          >
            <div className="pkg-import-header">
              <div>
                <h3 style={{ margin: 0, display: 'flex', alignItems: 'center', gap: '8px', fontSize: '16px' }}>
                  <Upload size={18} style={{ color: 'var(--primary)' }} />
                  {mode === 'resellers'
                    ? t('Import Reseller Packages (DirectAdmin / cPanel / Plesk)')
                    : t('Import User Packages (DirectAdmin / cPanel / Plesk)')}
                </h3>
                <p className="muted" style={{ margin: '4px 0 0', fontSize: '12.5px' }}>
                  {t('Automatically map quotas, domains, databases, email limits, and permissions into SPanel packages.')}
                </p>
              </div>
              <button
                type="button"
                className="secondary-light mini"
                onClick={() => setShowImportModal(false)}
              >
                <X size={15} />
              </button>
            </div>

            <div className="pkg-import-body">
              {/* Step 1: Select Source Panel */}
              <div>
                <label style={{ display: 'block', fontSize: '12.5px', fontWeight: 700, marginBottom: '8px', color: 'var(--text)' }}>
                  1. {t('Select Source Control Panel')}
                </label>
                <div className="pkg-panel-cards">
                  <div
                    className={`pkg-panel-card ${importPanel === 'directadmin' ? 'active' : ''}`}
                    onClick={() => setImportPanel('directadmin')}
                  >
                    <div className="pkg-panel-card-title">
                      <Server size={15} style={{ color: '#2563eb' }} /> DirectAdmin
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports .pkg files (/usr/local/directadmin/data/users/*/packages/*.pkg) & DirectAdmin API :2222')}
                    </div>
                  </div>

                  <div
                    className={`pkg-panel-card ${importPanel === 'cpanel' ? 'active' : ''}`}
                    onClick={() => setImportPanel('cpanel')}
                  >
                    <div className="pkg-panel-card-title">
                      <FileSpreadsheet size={15} style={{ color: '#ea580c' }} /> cPanel / WHM
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports /var/cpanel/packages/* files, WHM listpkgs JSON & WHM API :2087')}
                    </div>
                  </div>

                  <div
                    className={`pkg-panel-card ${importPanel === 'plesk' ? 'active' : ''}`}
                    onClick={() => setImportPanel('plesk')}
                  >
                    <div className="pkg-panel-card-title">
                      <Globe size={15} style={{ color: '#0891b2' }} /> Plesk Obsidian
                    </div>
                    <div className="pkg-panel-card-desc">
                      {t('Supports Plesk Service/Reseller Plan XML, REST JSON (/api/v2/serviceplans) & API :8443')}
                    </div>
                  </div>
                </div>
              </div>

              {/* Step 2: Select Import Method */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px' }}>
                <div className="pkg-import-method-tabs">
                  <button
                    type="button"
                    className={`pkg-import-method-tab ${importMethod === 'file' ? 'active' : ''}`}
                    onClick={() => setImportMethod('file')}
                  >
                    <FolderUp size={14} /> {t('Upload Files / Paste Config')}
                  </button>
                  <button
                    type="button"
                    className={`pkg-import-method-tab ${importMethod === 'remote' ? 'active' : ''}`}
                    onClick={() => setImportMethod('remote')}
                  >
                    <CloudDownload size={14} /> {t('Connect Remote Panel API')}
                  </button>
                </div>

                {importMethod === 'file' && (
                  <button
                    type="button"
                    className="secondary-light mini"
                    onClick={handleLoadSample}
                  >
                    <Sparkles size={13} /> {t('Load Sample Template')} ({importPanel === 'directadmin' ? 'DirectAdmin' : importPanel === 'cpanel' ? 'cPanel/WHM' : 'Plesk'})
                  </button>
                )}
              </div>

              {/* Method A: Upload Files or Paste Raw Configuration */}
              {importMethod === 'file' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <div className="pkg-upload-dropzone">
                    <div>
                      <strong style={{ fontSize: '13.5px', display: 'block', color: 'var(--text)' }}>
                        {t('Select one or multiple package files from your computer')}
                      </strong>
                      <span className="muted" style={{ fontSize: '12px' }}>
                        {importPanel === 'directadmin' && t('DirectAdmin path: /usr/local/directadmin/data/users/admin/packages/*.pkg')}
                        {importPanel === 'cpanel' && t('cPanel path: /var/cpanel/packages/<package_name> or WHM JSON')}
                        {importPanel === 'plesk' && t('Plesk format: XML <service-plan> / <reseller-plan> or REST JSON')}
                      </span>
                    </div>
                    <div>
                      <input
                        ref={fileInputRef}
                        type="file"
                        multiple
                        accept=".pkg,.conf,.txt,.json,.xml,.plan,*"
                        style={{ display: 'none' }}
                        onChange={handleFileSelect}
                      />
                      <button
                        type="button"
                        onClick={() => fileInputRef.current && fileInputRef.current.click()}
                      >
                        <FolderUp size={14} /> {t('Choose Package Files...')}
                      </button>
                    </div>
                  </div>

                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                      <label style={{ fontSize: '12.5px', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {t('Or paste package configuration content (.pkg / key=value / JSON / XML):')}
                      </label>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => handleParseRawText()}
                      >
                        <FileText size={13} /> {t('Parse Configuration')}
                      </button>
                    </div>
                    <textarea
                      rows={5}
                      value={rawImportText}
                      onChange={e => setRawImportText(e.target.value)}
                      placeholder={
                        importPanel === 'directadmin'
                          ? '[Business_Pkg]\nquota=10240\nbandwidth=102400\nvdomains=5\nnsubdomains=20\nmysql=10\nnemails=20\nphp=ON\nssl=ON\nssh=OFF'
                          : importPanel === 'cpanel'
                          ? '[cPanel_Gold]\nQUOTA=10240\nBWLIMIT=102400\nMAXADDON=4\nMAXSUB=20\nMAXSQL=10\nMAXPOP=20\nHASSHELL=n'
                          : '<packet><service-plan><get><result><name>Plesk_Pro</name><limits><limit><name>disk_space</name><value>10737418240</value></limit></limits></result></get></service-plan></packet>'
                      }
                      style={{ width: '100%', fontFamily: 'monospace', fontSize: '12.5px', margin: 0 }}
                    />
                  </div>
                </div>
              )}

              {/* Method B: Remote Panel API Connection */}
              {importMethod === 'remote' && (
                <form
                  onSubmit={handleFetchRemotePackages}
                  style={{
                    padding: '14px 16px',
                    borderRadius: '10px',
                    border: '1px solid var(--border)',
                    background: 'var(--surface-alt)',
                    display: 'grid',
                    gridTemplateColumns: 'repeat(auto-fit, minmax(210px, 1fr))',
                    gap: '12px',
                    alignItems: 'end',
                  }}
                >
                  <div>
                    <label style={{ fontSize: '12px', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
                      {t('Remote Panel URL')}
                    </label>
                    <input
                      type="text"
                      placeholder={
                        importPanel === 'directadmin'
                          ? 'https://da.example.com:2222'
                          : importPanel === 'cpanel'
                          ? 'https://whm.example.com:2087'
                          : 'https://plesk.example.com:8443'
                      }
                      value={remoteForm.host_url}
                      onChange={e => setRemoteForm(prev => ({ ...prev, host_url: e.target.value }))}
                      style={{ margin: 0, height: '36px' }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: '12px', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
                      {t('Admin / Reseller Username')}
                    </label>
                    <input
                      type="text"
                      placeholder={importPanel === 'cpanel' ? 'root' : 'admin'}
                      value={remoteForm.username}
                      onChange={e => setRemoteForm(prev => ({ ...prev, username: e.target.value }))}
                      style={{ margin: 0, height: '36px' }}
                    />
                  </div>

                  <div>
                    <label style={{ fontSize: '12px', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
                      {t('Authentication Method')}
                    </label>
                    <select
                      value={remoteForm.auth_type}
                      onChange={e => setRemoteForm(prev => ({ ...prev, auth_type: e.target.value }))}
                      style={{ margin: 0, height: '36px' }}
                    >
                      <option value="password">{t('Password / Login Key')}</option>
                      <option value="token">{t('API Token / API Key')}</option>
                    </select>
                  </div>

                  <div>
                    <label style={{ fontSize: '12px', fontWeight: 600, display: 'block', marginBottom: '4px' }}>
                      {remoteForm.auth_type === 'token' ? t('API Token / Key') : t('Password / Login Key')}
                    </label>
                    <input
                      type="password"
                      placeholder="••••••••••••••••"
                      value={remoteForm.secret}
                      onChange={e => setRemoteForm(prev => ({ ...prev, secret: e.target.value }))}
                      style={{ margin: 0, height: '36px' }}
                    />
                  </div>

                  <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                    <button type="submit" disabled={!!loading} style={{ height: '36px', width: '100%' }}>
                      <CloudDownload size={14} /> {t('Fetch Remote Packages')}
                    </button>
                  </div>
                </form>
              )}

              {/* Step 3: Interactive Preview & Mapping Table */}
              {parsedPackages.length > 0 && (
                <div className="pkg-preview-box">
                  <div className="pkg-preview-toolbar">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                      <label style={{ display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 700, cursor: 'pointer' }}>
                        <input
                          type="checkbox"
                          checked={selectedImportCount === parsedPackages.length && parsedPackages.length > 0}
                          onChange={e => {
                            const checked = e.target.checked;
                            setParsedPackages(prev => prev.map(p => ({ ...p, _selected: checked })));
                          }}
                          style={{ width: '15px', height: '15px', margin: 0 }}
                        />
                        {t('Preview Parsed Packages')} ({selectedImportCount}/{parsedPackages.length})
                      </label>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                      <span style={{ fontSize: '12px', color: 'var(--text-secondary)', fontWeight: 600 }}>
                        {t('If package name exists:')}
                      </span>
                      <select
                        value={conflictMode}
                        onChange={e => setConflictMode(e.target.value)}
                        style={{ width: 'auto', height: '30px', minHeight: '30px', padding: '2px 8px', fontSize: '12px', margin: 0 }}
                      >
                        <option value="overwrite">{t('Overwrite existing package')}</option>
                        <option value="skip">{t('Skip duplicate package')}</option>
                        <option value="rename">{t('Auto-rename (append _imported)')}</option>
                      </select>
                      <button
                        type="button"
                        className="secondary-light mini"
                        onClick={() => setParsedPackages([])}
                      >
                        <Trash2 size={12} /> {t('Clear List')}
                      </button>
                    </div>
                  </div>

                  <div style={{ overflowX: 'auto', maxHeight: '280px' }}>
                    <table className="pkg-clean-table">
                      <thead>
                        <tr>
                          <th style={{ width: '36px' }}></th>
                          <th>{t('Package Name')}</th>
                          <th>{t('Source')}</th>
                          <th>{t('Disk')} / {t('Bandwidth')}</th>
                          <th>
                            {mode === 'resellers'
                              ? `${t('Max Sub-Accounts (Users)')} / IPs`
                              : `${t('Websites')} / ${t('Subdomains')} / DBs`}
                          </th>
                          <th>{t('Email')} / FTP</th>
                          <th>{t('Features')}</th>
                          <th style={{ textAlign: 'right' }}>{t('Actions')}</th>
                        </tr>
                      </thead>
                      <tbody>
                        {parsedPackages.map((pkg, idx) => (
                          <tr key={pkg._id || idx}>
                            <td>
                              <input
                                type="checkbox"
                                checked={Boolean(pkg._selected)}
                                onChange={e => {
                                  const checked = e.target.checked;
                                  setParsedPackages(prev =>
                                    prev.map((item, i) => (i === idx ? { ...item, _selected: checked } : item))
                                  );
                                }}
                                style={{ width: '15px', height: '15px', margin: 0 }}
                              />
                            </td>
                            <td>
                              <input
                                type="text"
                                value={pkg.name}
                                onChange={e => {
                                  const val = e.target.value;
                                  setParsedPackages(prev =>
                                    prev.map((item, i) => (i === idx ? { ...item, name: val } : item))
                                  );
                                }}
                                style={{ height: '30px', minHeight: '30px', padding: '4px 8px', fontSize: '13px', width: '175px', margin: 0 }}
                              />
                            </td>
                            <td>
                              <span className="badge" style={{ fontSize: '11px', textTransform: 'uppercase' }}>
                                {pkg.source_panel || importPanel}
                              </span>
                            </td>
                            <td>
                              {formatMb(pkg.storage_limit_mb)} <span className="muted">/</span> {formatMb(pkg.bandwidth_limit_mb)}
                            </td>
                            <td>
                              {mode === 'resellers' ? (
                                <>
                                  {formatCount(pkg.reseller_user_limit)} {t('Users')} · {formatCount(pkg.ips_limit)} IPs
                                </>
                              ) : (
                                <>
                                  {formatCount(pkg.website_limit)} Web · {formatCount(pkg.subdomain_limit)} Sub · {formatCount(pkg.database_limit)} DB
                                </>
                              )}
                            </td>
                            <td>
                              {formatCount(pkg.mail_accounts_limit)} Mail · {formatCount(pkg.sftp_accounts_limit)} FTP
                            </td>
                            <td>
                              <div style={{ display: 'inline-flex', gap: '4px', flexWrap: 'wrap' }}>
                                {pkg.php_access && <span className="badge ok" style={{ fontSize: '10.5px' }}>PHP</span>}
                                {pkg.ssl_access && <span className="badge ok" style={{ fontSize: '10.5px' }}>SSL</span>}
                                {pkg.terminal_enabled && <span className="badge warn" style={{ fontSize: '10.5px' }}>SSH</span>}
                                {pkg.dns_control && <span className="badge" style={{ fontSize: '10.5px' }}>DNS</span>}
                                {mode === 'resellers' && pkg.reseller_overselling_enabled && (
                                  <span className="badge ok" style={{ fontSize: '10.5px' }}>Oversell</span>
                                )}
                              </div>
                            </td>
                            <td style={{ textAlign: 'right' }}>
                              <div style={{ display: 'inline-flex', gap: '6px' }}>
                                <button
                                  type="button"
                                  className="secondary-light mini"
                                  onClick={() => handleLoadSingleIntoEditor(pkg)}
                                  title={t('Load into Package Editor to customize all parameters before saving')}
                                >
                                  <Pencil size={12} /> {t('Load to Editor')}
                                </button>
                                <button
                                  type="button"
                                  className="danger mini"
                                  onClick={() => setParsedPackages(prev => prev.filter((_, i) => i !== idx))}
                                >
                                  <Trash2 size={12} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>

            <div className="pkg-import-footer">
              <div className="muted" style={{ fontSize: '12px' }}>
                {t('Tip: Click "Load to Editor" on any row to customize Cgroups v2 & App Runtime limits before saving.')}
              </div>
              <div style={{ display: 'flex', gap: '8px' }}>
                <button
                  type="button"
                  className="secondary-light"
                  onClick={() => setShowImportModal(false)}
                >
                  <X size={14} /> {t('Cancel')}
                </button>
                <button
                  type="button"
                  disabled={!!loading || selectedImportCount === 0}
                  onClick={handleBatchImportSubmit}
                >
                  <Check size={14} /> {t('Import Selected')} ({selectedImportCount})
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export const Packages = PackagesPage;
