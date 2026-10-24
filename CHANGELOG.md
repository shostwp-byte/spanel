# Changelog — SPanel

Mọi thay đổi, bản vá bảo mật, tối ưu hiệu năng và tính năng mới của **SPanel** ([https://github.com/shostwp-byte/spanel](https://github.com/shostwp-byte/spanel)) được ghi nhận tại tài liệu này.

---

## [Chưa phát hành] — sau v1.0.7

Các tính năng dưới đây đã có test tự động nhưng **chưa cài thử trên máy chủ thật**. Trước khi gắn tag, chạy hết [docs/STAGING_CHECKLIST.md](docs/STAGING_CHECKLIST.md).

### ⚠️ Lưu ý khi nâng cấp
- **PHP-FPM:** mỗi tài khoản có master PHP-FPM riêng (`spanel-php<ver>-fpm@<user>.service`) chạy trong `user-<uid>.slice`. `update.sh` tự chuyển từng site và chỉ gỡ pool cũ khi nginx đã trỏ sang socket mới.
- **Giới hạn tài nguyên:** trước đây giới hạn đặt trong gói (CPU, RAM, tasks, IO, inode, MySQL) bị mất sau mỗi request và mọi tài khoản thực tế chạy theo mặc định. Từ bản này, giới hạn trong gói **được áp dụng thật**. Kiểm tra lại các gói trước khi cập nhật.
- **Cụm đa máy chủ:** request giữa các node phải có chữ ký, nên **mọi node phải cập nhật**. Tạo token node mới (scope `cluster:*`) để chuyển tài khoản giữa các node.
- **Billing API:** token cũ `provisioning:read/write` vẫn dùng được. Token `sso:admin` bắt buộc phải có IP Allowlist.

### Cách ly tài nguyên
- PHP-FPM theo từng tài khoản, nằm trong slice ngay từ khi worker sinh ra; giới hạn IO cho scope.
- Quota đĩa của kernel, có giới hạn inode (tuỳ chọn, `SPANEL_DISK_QUOTA=kernel`; không bao giờ tự remount).
- Giới hạn MySQL theo giờ: số query, update và kết nối.

### Cụm đa máy chủ và Remote MySQL
- Remote MySQL đủ vòng đời: tạo, xoá, đổi mật khẩu, backup và khôi phục đúng máy chủ, tạm khoá và mở khoá, phpMyAdmin, TLS có kiểm tra CA. Tài khoản DB chỉ mở cho IP của web node, không dùng `%`.
- Ký request giữa các node (HMAC, timestamp, nonce); token node được mã hoá khi lưu; mặc định kiểm tra TLS, hỗ trợ pin fingerprint.
- Đồng bộ DNS có outbox bền vững, tự gửi lại và đối chiếu định kỳ. Mỗi zone có node gốc, serial luôn tăng, và zone của node nhận không bao giờ bị ghi đè.
- Chuyển tài khoản giữa các node SPanel (backup mã hoá một lần → upload có chữ ký → khôi phục → tuỳ chọn chuyển DNS và tạm khoá ở nguồn).
- Kiểm tra sức khoẻ node định kỳ, gợi ý node đặt tài khoản; kiểm tra trùng có chế độ fail-closed.

### WHMCS, API và webhook
- Sửa UsageUpdate (route bị che), xoá lịch sử webhook, và sự kiện provisioning không phát webhook.
- API: scope chi tiết, CIDR allowlist, hạn dùng và xoay vòng token, giới hạn tốc độ, `Idempotency-Key`, dọn sạch khi tạo tài khoản thất bại, danh sách tài khoản, băng thông thật.
- Webhook có outbox với retry, chữ ký kèm timestamp và danh mục sự kiện.
- Module WHMCS: SSO cho khách và admin, ListAccounts, tab admin, các nút tuỳ chỉnh, configurable options, backup trước khi terminate; CI lint PHP 7.4 và 8.3.
- Tài liệu API tiếng Việt và tiếng Anh, kèm `docs/openapi.json`.

### Mail và DNS
- Autoresponder được áp dụng thật (Sieve vacation).
- Xoay DKIM bằng 2 selector; chỉnh chính sách DMARC; MTA-STS, TLS-RPT, autoconfig/autodiscover.
- Chứng chỉ riêng cho `mail.<domain>` (chọn theo SNI ở Exim và Dovecot).
- Quản lý hàng đợi mail; auto-hold hoạt động thật; Rspamd học spam khi kéo thư vào hoặc ra khỏi Junk; cảnh báo quota.
- DNS: thêm các bản ghi TLSA, HTTPS, SVCB, PTR, DS, SSHFP, NAPTR; import và export file BIND; secondary qua AXFR/NOTIFY; template zone theo gói.

### Vá lỗi, chịu lỗi, sao lưu
- Bộ lập lịch theo khung giờ bảo trì cho tự cập nhật panel và vá core stack có lịch (có `nginx -t` trước và sau); unattended-upgrades tránh các gói do panel quản lý.
- Rollback cập nhật khôi phục cả `spanel.db`, venv và file hệ thống.
- Cảnh báo watchdog qua webhook (Slack, Discord, JSON); khôi phục `spanel.db` từ hot backup.
- Panel tự backup ra ngoài máy (mã hoá) và có lệnh `spanel dr-restore` để dựng lại máy chủ.
- Retention trên S3/SFTP/FTP, có thêm đích FTP/FTPS; kiểm tra backup định kỳ và khôi phục thử SQL.

### Bảo mật
- Helper chạy từ `/` và đặt `PYTHONSAFEPATH`, để thư mục do user `spanel` sở hữu không bao giờ lọt vào Python chạy bằng root.
- Nhận tài khoản chuyển từ node khác: archive không thể ghi đè một tài khoản đang có trên máy.

---

## [v1.0.7] — 2026-10-24

### ⚠️ Lưu ý khi nâng cấp từ v1.0.6
- **Webhook Git Auto-Deploy phải cấu hình lại.** URL webhook đã đổi công thức và mọi lần push phải có chữ ký (`X-Hub-Signature-256` của GitHub hoặc `X-Gitlab-Token` của GitLab) bằng **Webhook Secret** riêng. Webhook cài trước v1.0.7 bị từ chối với thông báo "Legacy pre-v1.0.7 webhook URL". Vào **Applications**, sao chép lại **Webhook URL** và **Webhook Secret** rồi dán vào GitHub/GitLab.
- **Đổi khoá webhook:** đặt `WEBHOOK_SECRET_KEY` trong `.env` để đổi toàn bộ URL và secret webhook mà không đụng tới `SECRET_KEY`.
- **Mã hoá backup theo lịch:** đặt `SPANEL_BACKUP_PASSPHRASE` (tối thiểu 12 ký tự) trong `.env` để mọi bản backup tự động được mã hoá AES-256. Không có giao diện nhập passphrase cho từng lịch.
- **Cron của khách hàng** được chuyển sang `/etc/cron.d/spanel-user-<user>` và chạy trong `spanel-cron-jail`. Bản sao crontab cũ được lưu ở `/var/backups/spanel/cron-spool-backup/<thời điểm>/`; dòng nào không chuyển được sẽ có cảnh báo trong log cập nhật.
- **Process Monitor** chỉ cho kill tiến trình của tài khoản hosting (uid 1000–64999). Dịch vụ hệ thống khởi động lại tại **System Services**.

### 🔒 Bảo mật
- Cron khách hàng: toàn bộ lệnh và chuyển hướng nằm trong một chuỗi `/bin/sh -c '...'` chạy sau `runuser`. Backend và `cron-write` dùng chung một ngữ pháp kiểm tra; dấu `%` chưa escape bị từ chối.
- Bước chuyển đổi cron trong `update.sh` không còn chạy mã của panel bằng root.
- `spanel-api` giữ `ProtectProc=invisible`; Process Monitor đọc tiến trình qua `spanel-helper`.
- Khoá audit riêng `/etc/spanel/audit-hmac.key`, anchor chuỗi audit do root ghi.
- Tắt cấp phát cơ sở dữ liệu trên MySQL từ xa cho tới khi hoàn thiện vòng đời.

### 🛠️ Sửa lỗi
- Backup theo lịch chạy lại được (lỗi `TypeError` ở mọi lần chạy trong bản thử nghiệm).
- Cron giữ đúng thư mục làm việc khi ghi vào `/etc/cron.d`.
- Watchdog timer chạy được; bỏ `apt-mark hold` tự động và gỡ hold cũ.
- `Alt-Svc` chỉ gửi một lần và chỉ khi nginx có HTTP/3; `reuseport` của QUIC được chuyển cho vhost khác (kể cả vhost dùng IP riêng) khi xoá vhost đang giữ nó.
- Giải nén archive: quét đồng bộ có giới hạn, phần vượt giới hạn được đưa vào hàng đợi quét nền.

### 🧪 Kiểm thử
- Khôi phục thư mục `tests/` và thêm CI GitHub Actions (backend, migration, cú pháp shell, build frontend, chặn PR xoá code hàng loạt).

---

## [v1.0.6] — 2026-10-20

### 🛠️ Khắc phục Lỗi Triển khai VPS & Cơ sở Dữ liệu (Deployment & Migration Hotfixes)
- **Khắc phục triệt để lỗi `npm error code ERESOLVE` khi build Frontend trên VPS mới (`frontend/package.json`, `installer/install.sh`, `installer/update.sh`):**
  - Đồng bộ bộ phụ thuộc `devDependencies` trong `frontend/package.json` (`@eslint/js@^9.22.0`, `eslint@^9.22.0`, `eslint-plugin-react@^7.37.5`, `globals@^16.0.0`) để loại bỏ hoàn toàn xung đột peer dependency giữa `@eslint/js@10.x` và `eslint-plugin-react@7.37.5`.
  - Bổ sung cờ `--no-audit --no-fund --legacy-peer-deps` (kèm cơ chế fallback `--force`) vào bước `npm install` trong `installer/install.sh` và `installer/update.sh` để đảm bảo quá trình build giao diện trên VPS mới không bao giờ bị gián đoạn bởi xung đột gói phụ thuộc thượng nguồn.
- **Khắc phục lỗi khởi tạo Database Alembic trên VPS mới (`backend/alembic/versions/f7a7bbef3278_add_missing_foreign_keys_and_cascades.py`, `backend/app/core/database.py`):**
  - Kiểm tra sự tồn tại của bảng tạm `_alembic_tmp_api_tokens` trước khi xoá trong migration `f7a7bbef3278`, ngăn chặn lỗi `sqlite3.OperationalError: no such table: _alembic_tmp_api_tokens` khi chạy `python -m app.seed` trên VPS mới hoàn toàn.
  - Chuẩn hoá tên các khoá ngoại (Foreign Key Constraints) và kiểm tra tồn tại cột/chỉ mục an toàn trong `f7a7bbef3278`, đồng thời bổ sung `Base.metadata.create_all(bind=engine)` trong `run_migrations()` để đảm bảo 100% bảng dữ liệu luôn đồng bộ.
- **Hỗ trợ linh hoạt Tag và Nhánh (`main` / `1.0.6`) trong bộ cài đặt & cập nhật (`install.sh`, `installer/update.sh`, `installer/files/spanel-update`):**
  - Cho phép tải gói cài đặt từ Release Tag (`v1.0.6`) hoặc tự động chuyển sang nhánh `main` / `1.0.6` / `HEAD` trên kho GitHub `https://github.com/shostwp-byte/spanel`.

### ✨ Tính năng Mới & Cải tiến Giao diện (Features & UX/UI Enhancements)
- **Quản lý Tên miền & Subdomain chuẩn DirectAdmin (`frontend/src/pages/Websites.jsx`):**
  - **Domain Setup (`/website`):** Tích hợp thanh chọn tên miền nhanh (`Top Domain Selector Bar`), nút `+ ADD NEW`, `✎ RENAME DOMAIN`, thanh trạng thái hạn mức Quota, thao tác hàng loạt (bật/tắt Force SSL, đổi phiên bản PHP, bật/tắt cô lập `Open_basedir`, xoá tên miền) và tuỳ chỉnh ẩn/hiện cột (`Columns ⌄`).
  - **Subdomain Management (`/subdomains`):** Tách biệt trang quản lý Subdomain độc lập có định tuyến `/subdomains`, hỗ trợ tạo Subdomain tức thì với bộ lọc tiền tố tự động, tuỳ chỉnh đường dẫn `Document Root`, theo dõi băng thông và trạng thái SSL/PHP.
- **Tối ưu hoá Giao diện Đa thiết bị & Header Di động (`frontend/src/App.jsx`, `frontend/src/ui.css`, `frontend/src/pages/Websites.jsx`):**
  - Thiết kế lại thanh Header (`.topbar`) trên thiết bị di động theo bố cục 2 hàng cân đối: Hàng 1 hiển thị cụm `[☰ Menu + Tiêu đề trang]` bên trái và cụm tiện ích `[❤️ Donate + 🔔 Thông báo + Ngôn ngữ + Giao diện + Tài khoản]` bên phải; Hàng 2 hiển thị thanh chuyển đổi vai trò `[ Admin | Reseller | User ]` dàn đều 3 cột toàn chiều rộng, khắc phục triệt để lỗi bị cắt chữ `Reseller` trên màn hình nhỏ.
  - Tự động tinh gọn các cột phụ trên màn hình điện thoại (`<= 640px`) trong bảng Domains và Subdomains.
  - Bổ sung hệ thống Media Queries cho Tablet (`<= 950px`), Mobile (`<= 768px`) và màn hình nhỏ (`<= 480px`), cố định khung chuông thông báo trong khung nhìn di động và hỗ trợ cuộn mượt cho bảng/hộp thoại Modal.
- **Tích hợp Nút Donate & Hộp thoại Mã QR VietQR (`frontend/src/App.jsx`):**
  - Bố trí nút `❤️ Donate` nhỏ gọn, nổi bật ngay trên thanh Header và chân trang Sidebar, tích hợp sẵn mã QR chuẩn VietQR Napas247 (Ngân hàng **MB Bank**, STK **978579798888**, Chủ tài khoản **Tran Quang Quoc Dung**, Nội dung **Donate Spanel**) kèm nút sao chép nhanh từng trường thông tin.
- **Tính năng Import Gói Dịch vụ (User & Reseller Packages) từ DirectAdmin, cPanel/WHM và Plesk (`frontend/src/pages/Packages.jsx`, `backend/app/api/packages.py`):**
  - Hỗ trợ nhập gói dịch vụ cho cả **Manage User Packages** và **Manage Reseller Packages** từ 3 nền tảng Hosting Panel phổ biến nhất: **DirectAdmin** (`.pkg`, API cổng `2222`), **cPanel / WHM** (`/var/cpanel/packages/*`, JSON `listpkgs`, WHM API cổng `2087`) và **Plesk Obsidian** (XML `<service-plan>` / `<reseller-plan>`, REST JSON `/api/v2/serviceplans`, API cổng `8443`).
  - Hỗ trợ 2 phương thức nhập linh hoạt: **Tải lên nhiều tệp / Dán cấu hình trực tiếp (kèm mẫu thử có sẵn)** hoặc **Kết nối trực tiếp API máy chủ Panel từ xa**.
  - **Tự động nhận diện quyền tài khoản DirectAdmin API (`Admin` & `Reseller`) và hỗ trợ cả Basic Auth lẫn Session Login:** Hỗ trợ chuẩn hoá URL tự động, gửi `Referer`/`Origin` chuẩn cho DirectAdmin (`check_referer=1`), ưu tiên gọi `CMD_API_PACKAGES_USER` trước khi tài khoản đăng nhập không phải `admin` (tránh bị chặn ở `CMD_API_PACKAGES_RESELLER` trả về HTTP 401/403), và tự động fallback sang cơ chế đăng nhập phiên (`POST /api/login` & `POST /CMD_LOGIN` kèm Cookie + `CMD_PACKAGES_USER?json=yes`) khi máy chủ DirectAdmin giới hạn HTTP Basic Auth đối với mật khẩu thường.
  - **Cố định khung hiển thị Modal Import Packages qua React Portal (`createPortal(..., document.body)`) & Bố cục Form 2 cột (`frontend/src/pages/Packages.jsx`, `frontend/src/ui.css`):** Đưa hộp thoại Import Packages ra ngoài `.section` (vốn có `backdrop-filter`/`transform` tạo CSS containing block làm cắt cụt khung modal), thiết kế lại biểu mẫu **Connect Remote Panel API** dạng lưới 2 cột rộng rãi (không bị cắt URL dài, có nút Ẩn/Hiện mật khẩu và tuỳ chọn xác thực SSL), đồng thời tự động `systemctl restart spanel-api` khi chạy lại bộ cài đặt hoặc cập nhật.
  - **Đồng bộ chuẩn hoá trường `personal_dns_mode` (`none` / `2_ips` / `3_ips`) (`frontend/src/pages/Packages.jsx`, `backend/app/schemas/schemas.py`, `backend/app/api/packages.py`):** Khắc phục triệt để lỗi xác thực Pydantic (`Input should be 'none', '2_ips' or '3_ips'`) khi tạo hoặc nhập gói dịch vụ Reseller / User.
  - Cung cấp bảng **Preview & Chuyển đổi thông số tự động** (Disk, Bandwidth, Websites, Subdomains, Domain Aliases, MySQL Databases, Email Accounts, Forwarders, FTP/SFTP, Max Users, Dedicated IPs, Overselling, SSH/SSL/DNS/Cron/Git) với tuỳ chọn xử lý trùng tên (`Ghi đè`, `Bỏ qua`, `Tự động đổi tên`) hoặc nạp từng gói vào biểu mẫu chỉnh sửa chi tiết trước khi lưu.
- **Phân loại Thông báo theo Vai trò (`Admin` / `Reseller` / `End User`):**
  - Phân luồng thông báo độc lập cho từng cấp độ người dùng kèm thanh lọc danh mục (`Tất cả`, `Chưa đọc`, `Hệ thống`, `Bảo mật`, `Sao lưu`, `Tài nguyên`).
- **Tự động Đồng bộ Menu End User theo Trạng thái Add-ons:**
  - Tự động ẩn các mục menu liên quan ở giao diện End User khi Admin chưa cài đặt hoặc tắt tiện ích mở rộng tương ứng (Application/Docker, Email Server, PowerDNS, Malware Scanner, MCP AI).
- **Biên soạn lại Toàn bộ Hệ thống Tài liệu (`docs/` & `README.md`):**
  - Làm sạch toàn bộ hình ảnh/thông tin cũ và xuất bản bộ tài liệu kỹ thuật mới của SPanel `v1.0.6` (`docs/README.md`, `docs/FEATURES.md`, `docs/INSTALLATION_AND_UPDATE.md`, `docs/ADMIN_AND_CLI_GUIDE.md`, `docs/API_AND_WHMCS.md`, `docs/index.html`).
