# SPanel

Bảng điều khiển hosting gọn nhẹ cho **Ubuntu 24.04**. Quản lý website WordPress
và PHP, database, SSL, backup, tường lửa, WAF và người dùng trong một giao diện
web, với tiếng Việt và tiếng Anh, chế độ sáng và tối.

![Tổng quan](docs/screenshots/02-dashboard.png)

## Mục lục

- [Ảnh chụp màn hình](#ảnh-chụp-màn-hình)
- [Tính năng](#tính-năng)
- [Tiện ích (addon)](#tiện-ích-addon)
- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Cập nhật](#cập-nhật)
- [Menu cứu hộ qua SSH](#menu-cứu-hộ-qua-ssh)
- [Tường lửa](#tường-lửa)
- [Người dùng, quyền và hạn mức](#người-dùng-quyền-và-hạn-mức)
- [API cấp phát và module WHMCS](#api-cấp-phát-và-module-whmcs)
- [Cấu hình](#cấu-hình)
- [Lệnh thường dùng](#lệnh-thường-dùng)
- [Mô hình bảo mật](#mô-hình-bảo-mật)
- [Cấu trúc mã nguồn](#cấu-trúc-mã-nguồn)
- [Hỗ trợ](#hỗ-trợ)
- [Giấy phép](#giấy-phép)

## Ảnh chụp màn hình

Chụp từ bản demo (giao diện tiếng Anh). Bấm vào ảnh để xem kích thước đầy đủ.

**Đăng nhập và tổng quan**

| | |
|---|---|
| <a href="docs/screenshots/01-login.png"><img src="docs/screenshots/01-login.png" alt="Đăng nhập"></a><br>Đăng nhập | <a href="docs/screenshots/02-dashboard.png"><img src="docs/screenshots/02-dashboard.png" alt="Tổng quan"></a><br>Tổng quan |
| <a href="docs/screenshots/29-dark-mode.png"><img src="docs/screenshots/29-dark-mode.png" alt="Giao diện tối"></a><br>Giao diện tối | <a href="docs/screenshots/30-mobile.png"><img src="docs/screenshots/30-mobile.png" alt="Trên điện thoại"></a><br>Trên điện thoại |

**Website**

| | |
|---|---|
| <a href="docs/screenshots/03-websites.png"><img src="docs/screenshots/03-websites.png" alt="Danh sách website"></a><br>Danh sách website | <a href="docs/screenshots/04-website-settings.png"><img src="docs/screenshots/04-website-settings.png" alt="Cấu hình website"></a><br>Cấu hình website |
| <a href="docs/screenshots/04b-website-terminal.png"><img src="docs/screenshots/04b-website-terminal.png" alt="Terminal của website"></a><br>Terminal của website | <a href="docs/screenshots/09-file-manager.png"><img src="docs/screenshots/09-file-manager.png" alt="Quản lý file"></a><br>Quản lý file |
| <a href="docs/screenshots/05-applications.png"><img src="docs/screenshots/05-applications.png" alt="Ứng dụng Node.js / Docker (addon)"></a><br>Ứng dụng Node.js / Docker (addon) | <a href="docs/screenshots/06-ssl.png"><img src="docs/screenshots/06-ssl.png" alt="SSL"></a><br>SSL |
| <a href="docs/screenshots/07-databases.png"><img src="docs/screenshots/07-databases.png" alt="Database"></a><br>Database | <a href="docs/screenshots/08-cron.png"><img src="docs/screenshots/08-cron.png" alt="Cron"></a><br>Cron |
| <a href="docs/screenshots/10-sftp.png"><img src="docs/screenshots/10-sftp.png" alt="Tài khoản SFTP"></a><br>Tài khoản SFTP |  |

**Backup và khôi phục**

| | |
|---|---|
| <a href="docs/screenshots/11-backups.png"><img src="docs/screenshots/11-backups.png" alt="Backup website"></a><br>Backup website | <a href="docs/screenshots/12-backup-schedule.png"><img src="docs/screenshots/12-backup-schedule.png" alt="Lịch backup tự động"></a><br>Lịch backup tự động |
| <a href="docs/screenshots/13-restore.png"><img src="docs/screenshots/13-restore.png" alt="Khôi phục"></a><br>Khôi phục | <a href="docs/screenshots/14-backup-destination.png"><img src="docs/screenshots/14-backup-destination.png" alt="Đích lưu backup (S3 / SFTP)"></a><br>Đích lưu backup (S3 / SFTP) |

**Người dùng**

| | |
|---|---|
| <a href="docs/screenshots/15-panel-users.png"><img src="docs/screenshots/15-panel-users.png" alt="Người dùng panel"></a><br>Người dùng panel | <a href="docs/screenshots/16-packages.png"><img src="docs/screenshots/16-packages.png" alt="Gói dịch vụ"></a><br>Gói dịch vụ |

**Bảo mật**

| | |
|---|---|
| <a href="docs/screenshots/19-firewall.png"><img src="docs/screenshots/19-firewall.png" alt="Tường lửa và Fail2ban"></a><br>Tường lửa và Fail2ban | <a href="docs/screenshots/20-waf.png"><img src="docs/screenshots/20-waf.png" alt="WAF và OWASP CRS"></a><br>WAF và OWASP CRS |
| <a href="docs/screenshots/21-waf-site.png"><img src="docs/screenshots/21-waf-site.png" alt="WAF của một website"></a><br>WAF của một website | <a href="docs/screenshots/22-access-logs.png"><img src="docs/screenshots/22-access-logs.png" alt="Nhật ký truy cập"></a><br>Nhật ký truy cập |
| <a href="docs/screenshots/23-security.png"><img src="docs/screenshots/23-security.png" alt="Passkey và đăng nhập hai lớp"></a><br>Passkey và đăng nhập hai lớp |  |

**Hệ thống**

| | |
|---|---|
| <a href="docs/screenshots/18-settings.png"><img src="docs/screenshots/18-settings.png" alt="Cài đặt"></a><br>Cài đặt | <a href="docs/screenshots/26-panel-settings.png"><img src="docs/screenshots/26-panel-settings.png" alt="Cài đặt panel"></a><br>Cài đặt panel |
| <a href="docs/screenshots/24-services.png"><img src="docs/screenshots/24-services.png" alt="Dịch vụ"></a><br>Dịch vụ | <a href="docs/screenshots/25-php.png"><img src="docs/screenshots/25-php.png" alt="Cấu hình PHP và extension"></a><br>Cấu hình PHP và extension |
| <a href="docs/screenshots/27-updates.png"><img src="docs/screenshots/27-updates.png" alt="Cập nhật"></a><br>Cập nhật | <a href="docs/screenshots/28-addons.png"><img src="docs/screenshots/28-addons.png" alt="Tiện ích"></a><br>Tiện ích |
| <a href="docs/screenshots/17-notifications.png"><img src="docs/screenshots/17-notifications.png" alt="Thông báo (addon)"></a><br>Thông báo (addon) |  |

## Tính năng

**Website và ứng dụng**

- Cài WordPress một lần bấm (WP-CLI), website PHP thường, hoặc website chạy
  ứng dụng Node.js / Docker (addon Application).
- Nhiều phiên bản PHP chạy song song: 5.6, 7.4, 8.0 đến 8.5. Mỗi website một
  pool PHP-FPM riêng, chạy bằng user Linux của chủ website.
- Sửa được toàn bộ vhost Nginx, có sẵn các mẫu rewrite, tên miền phụ (alias).
- MariaDB, phpMyAdmin đăng nhập một lần (token 60 giây).
- SSL Let's Encrypt cho website và cho chính panel.
- Quản lý file: tải lên, sửa, nén, giải nén, đổi quyền; quyền bạn đặt được
  giữ nguyên, panel không tự đặt lại.
- Cron, tài khoản SFTP giới hạn trong một thư mục, terminal cho từng website.

**Backup và khôi phục**

- Backup website (file + SQL), backup toàn bộ tài khoản, lịch backup tự động.
- Đích lưu S3 (AWS, Wasabi, Backblaze B2, Cloudflare R2, MinIO...) hoặc SFTP.
  Backup đã gửi lên đích lưu không giữ thêm bản trên server.
- Khôi phục theo từng bước như DirectAdmin: chọn nguồn (server này, đích lưu,
  hoặc máy chủ khác qua SFTP/FTP/FTPS), chọn tài khoản, khôi phục. Chạy nền và
  báo kết quả từng tài khoản.
- Nhập backup từ DirectAdmin.

**Bảo mật**

- Tường lửa iptables + ipset: luật cho phép / chặn theo IP, blocklist theo URL.
- WAF ModSecurity cho từng website, bộ luật OWASP CRS bật tắt theo website,
  chống HTTP flood, rule tuỳ chỉnh toàn cục, nhật ký truy cập và các lần chặn.
- Đăng nhập hai lớp (TOTP, tương thích Google Authenticator) và passkey.
- Quét mã độc bằng Linux Malware Detect + ClamAV (addon).
- Chặn dò mật khẩu SSH bằng Fail2ban (addon).

**Hệ thống**

- Tổng quan: CPU, RAM, ổ đĩa, mạng và các việc cần xử lý.
- Quản lý dịch vụ, cấu hình PHP và cài PHP extension, tự điều chỉnh PHP-FPM và
  MariaDB theo RAM/CPU của VPS.
- Cập nhật panel ngay trong giao diện.
- Người dùng panel, gói dịch vụ, hạn mức website và dung lượng.
- API cấp phát và module WHMCS dùng chung cho SPanel và OPanel.

## Tiện ích (addon)

Những phần không cần cho mọi server được tách thành tiện ích. Tất cả đều
**tắt mặc định**, bật và tắt trong **Cài đặt → Tiện ích**. Gỡ tiện ích chỉ tắt
tính năng, không xoá dữ liệu nó đã tạo.

| Tiện ích | Chức năng |
|---|---|
| **Application** | Chạy ứng dụng Node.js, container và Docker Compose sau Nginx. |
| **Fail2ban** | Chặn địa chỉ dò mật khẩu SSH. |
| **Quét mã độc** | LMD + ClamAV: quét theo yêu cầu, theo lịch, giám sát thời gian thực (Lớp 2), quét file tải lên. |
| **Trợ lý AI (MCP)** | Cho Claude Code, Cursor, VS Code đọc và thao tác panel bằng token cá nhân. |
| **Quản lý DNS** | Chạy DNS cho tên miền ngay trên server bằng PowerDNS (mở cổng 53). Quản trị viên sửa mọi zone, khách hàng sửa zone của website mình; bản ghi A, AAAA, CNAME, MX, TXT, NS, SRV, CAA. Website mới tự có zone theo mẫu bản ghi tuỳ chỉnh; SPF tự theo smarthost. |
| **Email** | Mail server trên VPS: Exim gửi/nhận, Dovecot cho IMAP/POP3, và SPanel Webmail trên cổng 2096. Khách hàng tạo hộp thư trên tên miền của mình trong số lượng gói cho phép, mở webmail từ panel không cần mật khẩu (SSO). Thư lưu trong home của khách, tính vào dung lượng và có trong backup. Ký DKIM; có Quản lý DNS thì bản ghi DKIM, DMARC, webmail tự được thêm. Rspamd lọc thư rác (từ chối hoặc chuyển vào Junk), có nhật ký lọc thư kèm lý do và danh sách cho phép; gửi thư qua smarthost khi nhà cung cấp chặn cổng 25. |
| **Chế độ demo** | Tài khoản demo công khai cho quản trị và khách hàng, có nút đăng nhập nhanh trên trang đăng nhập. Xem mọi trang, mọi thay đổi bị từ chối; không mở được nội dung file, tải xuống, phpMyAdmin hay terminal. |
| **Thông báo** | Gửi cảnh báo cho quản trị viên qua email (SMTP) và Telegram: dịch vụ dừng, ổ đĩa đầy, backup lỗi, mã độc, chứng chỉ sắp hết hạn, có bản cập nhật... |

Tiện ích có trang riêng sẽ hiện trên menu bên trái khi được bật.

## Yêu cầu hệ thống

- Ubuntu 24.04 LTS, nên là máy mới cài.
- Quyền root.
- Tối thiểu 1 vCPU / 1 GB RAM; khuyến nghị 2 vCPU / 2 GB RAM. Quét mã độc cần
  khoảng 1,3 GB RAM trong lúc quét.
- Tuỳ chọn: một tên miền trỏ về IP của server để panel có SSL.

## Cài đặt

Chạy bằng root trên server Ubuntu 24.04 mới:

```bash
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh | bash
```

Script tải bản phát hành mới nhất trên GitHub rồi cài. Trình cài sẽ hỏi:

- Tên miền của panel (để trống thì dùng IP của server).
- Cổng của panel (mặc định `2222`; tường lửa chỉ mở cổng này).
- Có cấp SSL Let's Encrypt cho tên miền panel không, và email đăng ký SSL.

Cài không cần hỏi (ví dụ trong script tự động):

```bash
export PANEL_URL=https://panel.example.com:2222
export ENABLE_SSL=yes
export SSL_EMAIL=admin@example.com
export SPANEL_ADMIN_PASSWORD='mật-khẩu-mạnh'   # bỏ trống thì trình cài tự tạo
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh | bash
```

Trình cài sẽ cài Nginx, MariaDB, Redis, PHP-FPM (mặc định PHP 8.4), OpenSSH/SFTP,
certbot, phpMyAdmin, WP-CLI, iptables, ipset và Node.js; dựng panel ở
`/opt/spanel`, tạo dịch vụ `spanel-api`, tạo tài khoản `admin`, tự điều chỉnh
PHP-FPM và MariaDB theo cấu hình VPS. Cuối cùng nó in ra địa chỉ panel, tên đăng
nhập và mật khẩu, đồng thời lưu vào `/root/login.txt`. Hãy cất mật khẩu vào
trình quản lý mật khẩu.

Panel không bị gắn với một tên miền: nó giữ bản sao của mọi chứng chỉ trên
server và chọn theo SNI, nên `https://<tên miền bất kỳ trên server>:<cổng panel>`
đều mở được panel với chứng chỉ hợp lệ.

## Cập nhật

```bash
spanel-update --release          # lên bản phát hành mới nhất
spanel-update --tag v1.0.167     # lên một bản cụ thể
```

Hoặc bấm cập nhật trong trang **Cài đặt → Cập nhật**. Nếu trình duyệt vẫn hiện
giao diện cũ sau khi cập nhật, nhấn Ctrl + Shift + R.

## Menu cứu hộ qua SSH

Khi không vào được panel, chạy bằng root:

```bash
spanel
```

Menu cho xem thông tin đăng nhập, trạng thái, log, khởi động lại dịch vụ, mở lại
các cổng cần thiết, đặt lại địa chỉ và cổng panel, sửa SSL của panel, sửa quyền
file, đổi mật khẩu `admin` và cập nhật panel. Một số lệnh dùng trực tiếp:

```bash
spanel change-ip IP_CŨ IP_MỚI         # đổi IP server không cần hỏi
spanel change-admin-password          # đổi mật khẩu admin của panel
spanel sync-admin-root-password       # dùng mật khẩu root hiện tại cho admin
spanel-rescue-firewall                # bị tường lửa khoá ngoài: sao lưu rồi dựng lại luật tối thiểu
```

## Tường lửa

Lọc IP chạy trên **iptables + ipset**. File `/var/lib/spanel/firewall/rules.tsv`
là nguồn gốc duy nhất; mỗi thay đổi dựng lại chuỗi `SPANEL-INPUT` và nạp lại
các ipset từ file, và `spanel-firewall.service` làm lại đúng việc đó khi khởi động.

- **Cổng được bảo vệ** (SSH, cổng panel, 80/443/465/587) luôn mở và không xoá
  được từ panel.
- Luật **cho phép** được xét trước luật **chặn**.
- **Blocklist theo URL** được tải hằng ngày vào `spanel-block4` / `spanel-block6`:
  một triệu địa chỉ vẫn chỉ tốn một lần tra bảng băm cho mỗi gói tin.
- Tắt tường lửa chỉ gỡ lệnh nhảy từ `INPUT`, không đổi chính sách của `INPUT`.

## Người dùng, quyền và hạn mức

| Vai trò | Quyền |
|---|---|
| `admin` | Toàn quyền: website, người dùng, dịch vụ, tường lửa, cấu hình PHP, backup, bảo mật, tiện ích. |
| `end_user` | Chỉ quản lý website của mình: file, database, SSL, WordPress, cron, backup của mình. |

- Mỗi người dùng panel có một user Linux cùng tên; website nằm ở
  `/home/<người dùng>/<tên miền>/public_html`.
- Mật khẩu panel đồng bộ với mật khẩu SFTP; SFTP bị giới hạn (chroot) trong thư
  mục của người dùng, không có shell.
- Quản trị viên có thể đăng nhập thay một người dùng để tạo website cho họ, và
  chuyển website sang người dùng khác.
- Người dùng thường có giới hạn số website và dung lượng (MB), gom thành các gói
  dùng lại được. Dung lượng được kiểm tra ở tầng ứng dụng khi tạo website, tải
  lên, sửa, nén, giải nén.
- Xoá người dùng sẽ xoá hẳn mọi website, file, database, cron và dữ liệu Linux
  của người đó.

## API cấp phát và module WHMCS

`modules/servers/spanel/` là một module WHMCS dùng chung cho **SPanel và OPanel**.
Xác thực bằng Bearer token (tạo ở **Cài đặt panel → API Token**, dán vào Access
Hash của server trong WHMCS).

| Hook WHMCS | Endpoint |
|---|---|
| `TestConnection`, `PackageLoader` | `GET /plans` |
| `CreateAccount` | `POST /accounts` |
| `SuspendAccount` | `POST /accounts/{external_id}/suspend` |
| `UnsuspendAccount` | `POST /accounts/{external_id}/unsuspend` |
| `TerminateAccount` | `DELETE /accounts/{external_id}` |
| `ChangePassword` | `PATCH /accounts/{external_id}/password` |
| `ChangePackage` | `PATCH /accounts/{external_id}/package` |
| `UsageUpdate` | `GET /accounts/{external_id}/usage` |
| `LoginLink`, `ClientArea` | `POST /accounts/{external_id}/login` |

`external_id` có dạng `whmcs:<serviceid>`. Đăng nhập một lần (SSO) dùng token chỉ
dùng được một lần, hết hạn sau 5 phút. Tạm khoá sẽ tắt đăng nhập, huỷ mọi phiên
đang mở và chuyển các website sang trang "tạm ngưng"; mở khoá khôi phục lại
nguyên trạng.

## Cấu hình

Trình cài tạo `/opt/spanel/backend/.env`:

```ini
APP_ENV=production
SECRET_KEY=<ngẫu nhiên 32 byte>
COMMAND_DRY_RUN=false
DATABASE_URL=sqlite:////opt/spanel/backend/spanel.db
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_BACKEND=redis
BACKUP_ROOT=/var/backups/spanel
SSL_EMAIL=admin@example.com
PANEL_URL=https://panel.example.com:2222
PANEL_PORT=2222
PANEL_SNI_DIR=/etc/spanel/sni
FRONTEND_DIST=/opt/spanel/frontend/dist
```

PHP-FPM và MariaDB được tự điều chỉnh theo RAM và CPU. Có thể ghi đè trong `.env`
(ví dụ `SPANEL_PHP_FPM_MAX_CHILDREN`, `SPANEL_MARIADB_BUFFER_POOL_SIZE`) rồi chạy lại:

```bash
sudo -u spanel env HOME=/opt/spanel sudo -n /usr/local/sbin/spanel-helper php-fpm-retune
sudo -u spanel env HOME=/opt/spanel sudo -n /usr/local/sbin/spanel-helper mariadb-retune
```

## Lệnh thường dùng

```bash
journalctl -u spanel-api -f                    # log của API
systemctl restart spanel-api                   # khởi động lại API
nginx -t && systemctl reload nginx             # nạp lại Nginx sau khi sửa vhost
systemctl status spanel-api nginx mariadb redis-server php8.4-fpm
```

## Mô hình bảo mật

Tiến trình panel **không chạy bằng root**. Trình cài tạo user hệ thống `spanel`
và một script trợ giúp duy nhất thuộc root làm mọi việc cần quyền cao:

```
spanel-api  (uvicorn, user=spanel, unit systemd đã siết chặt)
   |
   |  sudo -n /usr/local/sbin/spanel-helper <lệnh con> ...
   v
spanel-helper  (root, chỉ chạy các thao tác nằm trong danh sách cho phép)
```

- Helper kiểm tra tên miền, cổng, IP và đường dẫn trước khi gọi chương trình
  thật; mọi lệnh ngoài danh sách đều bị từ chối.
- Website được tách nhau bằng user Linux riêng, pool PHP-FPM riêng với
  `open_basedir`, SFTP chroot và shell `nologin`.
- Terminal của website chạy bằng user của website, chỉ cho các lệnh trong danh
  sách (PHP, Composer, WP-CLI, Node, npm, git và các công cụ file thường dùng),
  kiểm tra đường dẫn và giới hạn thời gian.
- Đăng nhập bị giới hạn tần suất qua Redis; mật khẩu database mã hoá khi lưu;
  cookie HttpOnly kèm token CSRF; Content-Security-Policy chặt; phiên bị huỷ khi
  đổi mật khẩu, đổi quyền, khoá tài khoản hoặc đổi đăng nhập hai lớp.
- Nếu API bị chiếm, kẻ tấn công chỉ ghi được vào cấu hình Nginx, thư mục website
  và thư mục backup, và chỉ chạy được các lệnh con của helper; không có đường
  lên root qua tiến trình API.

## Cấu trúc mã nguồn

```
spanel/
|-- backend/          FastAPI, SQLAlchemy, Pydantic v2 (API, dịch vụ, test)
|-- frontend/         React 19 + Vite (giao diện)
|-- installer/        install.sh, update.sh, spanel-helper.sh, cứu hộ tường lửa
|-- modules/          module WHMCS
|-- tools/i18n/       bản dịch tiếng Việt
`-- docs/screenshots/ ảnh chụp màn hình
```

## Hỗ trợ

- SPanel được phát hành miễn phí theo giấy phép AGPL-3.0. Bạn có thể
  báo lỗi hoặc đề xuất tính năng qua
  [GitHub Issues](https://github.com/shostwp-byte/spanel/issues).

## Giấy phép

SPanel phát hành theo giấy phép **GNU Affero General Public License, phiên bản 3
(AGPL-3.0)**. Xem [LICENSE](LICENSE).

Tóm tắt (không thay thế giấy phép):

- Bạn được dùng, sao chép, sửa đổi và phân phối SPanel, kể cả cho mục đích
  thương mại.
- Khi phân phối SPanel hay một bản đã sửa đổi, bạn phải kèm mã nguồn, giữ cùng
  giấy phép AGPL-3.0 và giữ nguyên các thông báo bản quyền.
- Nếu bạn sửa đổi SPanel rồi cho người khác dùng bản đó qua mạng (ví dụ cấp panel
  cho khách hosting), bạn phải cho những người dùng đó tải được mã nguồn của bản
  đã sửa.
- Phần mềm được cung cấp "nguyên trạng", không kèm bất kỳ bảo đảm nào.

Bản tiếng Anh trong file [LICENSE](LICENSE) là bản có hiệu lực.

```
SPanel - Copyright (C) 2026 SPanel (https://github.com/shostwp-byte/spanel)

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU Affero General Public License, version 3, as published by
the Free Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.
```
