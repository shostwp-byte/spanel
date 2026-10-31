# Changelog — SPanel

Mọi thay đổi, bản vá bảo mật, tối ưu hiệu năng và tính năng mới của **SPanel** ([https://github.com/shostwp-byte/spanel](https://github.com/shostwp-byte/spanel)) được ghi nhận tại tài liệu này.

---

## [v1.0.8] — 2026-10-31

Bản phát hành hợp nhất các nâng cấp kiến trúc sau `v1.0.7` cùng toàn bộ bản vá bảo mật, hạn mức gói dịch vụ, Webmail, Quota, Nginx và Application Manager qua các đợt thẩm định độc lập Lần 11 → Lần 14:

### ⚠️ Lưu ý khi nâng cấp từ v1.0.7
- **PHP-FPM theo từng tài khoản:** mỗi tài khoản có master PHP-FPM riêng (`spanel-php<ver>-fpm@<user>.service`) chạy trong `user-<uid>.slice`. `update.sh` tự chuyển từng site và chỉ gỡ pool cũ khi Nginx đã trỏ sang socket mới.
- **Giới hạn tài nguyên gói dịch vụ:** các giới hạn trong gói (`CPU`, `RAM`, `Tasks`, `IO`, `Inode`, `MySQL`, cũng như số lượng `Website`, `Subdomain`, `Database`, `SFTP`, `Node/Docker Apps` với quy ước duy nhất `-1 = Không giới hạn`, `0 = Tắt`, `> 0 = Tối đa`) được cưỡng chế thật và chuẩn hóa tự động qua migration `unify_package_quota_limits`.
- **Cụm đa máy chủ (`Multi-Server`):** request giữa các node bắt buộc có chữ ký HMAC + timestamp + nonce; tạo token node mới (scope `cluster:*`) để chuyển tài khoản giữa các node.
- **Billing API:** token cũ `provisioning:read/write` vẫn tương thích ngược; token `sso:admin` bắt buộc phải cấu hình IP Allowlist.

### 🔒 Bảo mật & Phân tách đặc quyền
- **Khôi phục Sandbox `spanel-api` (`P1-1`):** Giữ nguyên `PrivateDevices=true` và `ProtectKernelModules=true` cho tiến trình panel; các tác vụ đĩa/quota (`findmnt --verify`, `quotacheck`, `quotaon`, `modprobe`) được thực thi cô lập qua `systemd-run -p PrivateDevices=no`.
- **Chấm dứt thao tác xóa file trực tiếp từ tiến trình Panel (`P0-1`, `P1-B7`):** Bổ sung động từ helper `site-file-delete` mở đường dẫn bằng `O_NOFOLLOW` và kiểm chứng `st_uid` thuộc đúng Linux user của khách; bổ sung `.env.bak` vào `SITE_SECRET_FILES` để luôn áp quyền `0640`; giữ nguyên `O_NOFOLLOW` + `fstat` khi clone WordPress.
- **Cô lập kênh `x-originating-ip` giữa Webmail và Dovecot (`P2-4`):** Gỡ `127.0.0.1/32 ::1/128` khỏi `login_trusted_networks` chung và chuyển kết nối xác thực IMAP của Webmail sang UNIX socket chuyên biệt `/run/dovecot/imap-webmail` (`0660`, nhóm `spanel-webmail`) để tiến trình PHP/cron/terminal của khách không thể giả mạo IP gốc.
- **Gia cố Webmail & Helper (`P0-A`, `P1-1..P1-3`, `P2-2`, `P3-2`):** Gỡ bỏ `WEBMAIL_REPO` và nhánh fallback `${APP_DIR}` trong `spanel-helper.sh`; rút TTL SSO Master xuống 2 giờ kèm mã thế hệ hộp thư (`gen`); hỗ trợ đăng nhập cổng `:2096` và mật khẩu UTF-8 (`AUTHENTICATE PLAIN`); gộp rate-limit IPv6 theo tiền tố `/64` ở cả ứng dụng và Nginx `map`, kết hợp trì hoãn lũy tiến (`tarpit`) theo địa chỉ email chống botnet dò mật khẩu mà không khóa chủ hộp thư thật; xóa header giả mạo `X-Forwarded-Host` tại Nginx proxy.
- **MariaDB Import & Helper Python (`P1-2`, `P1-3`, `P2-2`):** Luôn nhập SQL dưới thông tin xác thực của tenant (`--defaults-file` tạm, `--binary-mode`), chỉ lọc `DEFINER=` trên các dòng DDL; helper chạy từ `/` kèm `PYTHONSAFEPATH=1`.

### 📦 Hạn mức Gói Dịch vụ (`quota.py`), Cách ly Tài nguyên & Quota Kernel
- **Một nguồn sự thật cho toàn bộ hạn mức đếm (`P2-5`):** Hợp nhất logic kiểm tra vào `backend/app/services/quota.py` (`effective_limit(user, field)` ưu tiên `package` trước `user`, `-1 = ∞`, `0 = Tắt`, `> 0 = Tối đa`) kèm migration Alembic `unify_package_quota_limits`, áp dụng đồng bộ trên tạo/gán/chuyển chủ Website, Subdomain, Database, SFTP (`GET /limits` & `POST`) và toàn bộ nhóm `Application`.
- **Quota đĩa tầng Kernel an toàn (`P1-2`, `P3-1`):** Đối chiếu `SOURCE` `[subpath]` kết hợp `MAJ:MIN` khi phân giải bind-mount `/home` về `/`; cập nhật `/etc/fstab` nguyên tử qua tệp tạm cùng phân vùng + `findmnt --verify`, hạ `mount -fav` xuống mức cảnh báo để không bị chặn bởi các dòng NFS/USB `nofail` ngoại lai.
- **Cách ly PHP-FPM & MySQL Governor:** Mỗi tài khoản chạy master PHP-FPM riêng trong `user-<uid>.slice` với giới hạn IO cgroups v2 và giới hạn truy vấn/kết nối MariaDB theo giờ.

### 🌐 Nginx, Chuyển chủ Website & Giao diện
- **Thứ tự `custom_include_path` & Cache ảnh Uploads thư mục con (`P1-5`, `P2-1`, `P3-5`):** Đặt `include {{ custom_include_path }};` về sau khối `location ~ \.php$` trong `php.conf.j2` và `wordpress.conf.j2` để ngăn chặn lộ mã nguồn `.php` khi khách viết regex tùy chỉnh; giữ luật `location ^~ /<sub>/wp-content/uploads/` kèm `expires 30d; access_log off;` và `location ~* \.php$ { deny all; }`.
- **Chuyển chủ sở hữu Website toàn diện (`P1-5`, `P1-6`, `P3-6`):** Chuyển nguyên tử cả thư mục hộp thư `/home/<cũ>/mail/<domain>` qua `mail-move-domain`, thu hồi phiên Webmail cũ, tự động rollback nếu lỗi và trả cảnh báo rõ ràng nếu đồng bộ Dovecot thất bại.
- **Tự phục hồi Frontend & Hiển thị Menu theo Addon (`P1-4`, `P3-3`, `P3-4`):** Phân biệt chính xác lỗi tải chunk và lỗi render trong `ChunkErrorBoundary`; ẩn `Docker` và `Node.js` khỏi menu End User khi chưa cài addon `Application`; gỡ bỏ đăng ký router `app_installer` dư thừa.

### 🚀 Application Manager (`Laravel Manager` & `WordPress Manager`)
- **Hoàn thiện sản xuất 100% `Laravel Manager` (`P0-1`, `P1-B1..B7`, `P2-B`):**
  - Thực thi thật `composer` (2.8+ PHAR), `git` và `php artisan` bên trong mount-namespace jail `terminal-exec` dưới quyền Linux user của khách, hỗ trợ đầy đủ PHP `8.1` → `8.5` và tự động ánh xạ danh sách phiên bản PHP-FPM thực tế trên máy chủ.
  - Chuẩn hóa `DB_HOST=localhost` trong `.env` tương thích với cấu hình `skip_name_resolve = 1` của MariaDB nội bộ, đồng thời tự động chữa các tệp `.env` cũ (`127.0.0.1` → `localhost`) và xóa cache cấu hình cũ khi thực thi `artisan migrate` / Queue Worker.
  - Quản lý Laravel Queue Worker thật qua unit systemd template `spanel-laravel-worker` chạy dưới `user-<uid>.slice`; bật/tắt chế độ bảo trì thật (`artisan down --secret` / `up` kèm cơ chế cứu hộ khi lỗi autoloader); kiểm tra và gia cố thật 7 tiêu chí bảo mật trên Nginx & filesystem; lưu state nguyên tử mode `0600`.
- **Nâng cấp toàn diện `WordPress Manager`:**
  - Phân tách minh bạch `Detach` (chỉ gỡ khỏi khung quản lý) và `Remove` (xóa sạch mã nguồn qua `rm-site` + xóa Database được liên kết + khôi phục Nginx vhost, chống tái xuất hiện khi `Scan`).
  - Trang **Maintenance Mode** tùy biến đầy đủ nội dung thông báo và tải ảnh nền trực tiếp (tự động miễn trừ cho quản trị viên đang đăng nhập `wp-admin` / `wp-login.php`).
  - Chuẩn hóa và đồng bộ bộ 3 nút biểu tượng chuyển chế độ hiển thị (`Card View`, `Grid View`, `List View`) giữa WordPress Manager và Laravel Manager.

### ✉️ Mail, DNS, Cụm Đa Máy chủ, WHMCS & Vận hành DR
- **Bảo toàn cấu hình Nginx Webmail cổng 2096 (`P2-3`):** Sao lưu `prev_conf` và tự động khôi phục nguyên trạng nếu `nginx -t` thất bại trong quá trình `mail_reconfigure`.
- **Mail & PowerDNS nâng cao:** Autoresponder Sieve thật, xoay khóa DKIM 2 selector, chỉnh DMARC, MTA-STS, TLS-RPT, autoconfig/autodiscover, chứng chỉ SNI riêng cho `mail.<domain>`, quản lý hàng đợi Exim, học spam Rspamd và hỗ trợ bản ghi `TLSA`, `HTTPS`, `SVCB`, `PTR`, `DS`, `SSHFP`, `NAPTR`, import/export BIND, AXFR/NOTIFY, DNSSEC.
- **Multi-Server & Remote MySQL:** Trọn vòng đời Remote MySQL qua TLS có kiểm tra CA & phpMyAdmin SSO; ký HMAC liên node, outbox đồng bộ DNS bền vững và chuyển tài khoản không gián đoạn giữa các node SPanel.
- **WHMCS, Provisioning API & Cập nhật Không treo:** Scope API chi tiết, CIDR allowlist, `Idempotency-Key`, webhook outbox có retry; khắc phục lỗi treo `spanel-update` trên SSH TTY bằng cờ không tương tác `COMPOSER_ALLOW_SUPERUSER=1 COMPOSER_NO_INTERACTION=1 --no-interaction </dev/null`; kích hoạt GitHub Actions CI tự động trên mọi nhánh push; hỗ trợ tự sao lưu DR mã hóa (`spanel dr-restore`) và auto-rollback cả mã nguồn lẫn `spanel.db`.

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
