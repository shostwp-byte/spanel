# SPanel — Nền tảng Quản trị Web Hosting Đa tầng Thế hệ mới

**SPanel (`v1.0.6`)** là bảng điều khiển quản trị máy chủ và web hosting hiện đại, gọn nhẹ, bảo mật cao dành cho **Ubuntu 24.04 LTS**. Hệ thống hỗ trợ kiến trúc phân quyền **3 cấp độ (Admin / Reseller / End User)**, giao diện quản lý tên miền & subdomain trực quan theo phong cách DirectAdmin, tối ưu hiển thị trên cả máy tính lẫn thiết bị di động (Mobile Responsive), hỗ trợ song ngữ **Tiếng Việt / Tiếng Anh** cùng chế độ **Sáng / Tối (Light / Dark Mode)**.

- **Kho mã nguồn chính thức:** [https://github.com/shostwp-byte/spanel](https://github.com/shostwp-byte/spanel)
- **Phiên bản hiện tại:** `v1.0.6`
- **Giấy phép:** GNU AGPLv3 ([LICENSE](LICENSE))

---

## Tài liệu Chi tiết (`docs/`)

Toàn bộ tài liệu kỹ thuật, hướng dẫn cài đặt, vận hành và tích hợp API của SPanel được biên soạn chi tiết trong thư mục [`docs/`](docs/README.md):

1. [**Tổng quan & Kiến trúc Hệ thống (`docs/README.md`)**](docs/README.md) — Kiến trúc tổng thể, ngăn xếp công nghệ (Tech Stack) và luồng vận hành.
2. [**Danh mục Tính năng Chi tiết (`docs/FEATURES.md`)**](docs/FEATURES.md) — Chi tiết toàn bộ tính năng theo 3 vai trò **Admin**, **Reseller**, **End User** và hệ thống **Add-ons**.
3. [**Hướng dẫn Cài đặt & Cập nhật Trực tuyến (`docs/INSTALLATION_AND_UPDATE.md`)**](docs/INSTALLATION_AND_UPDATE.md) — Quy trình cài đặt trên VPS mới, tham số tự động hóa và cơ chế Online Update.
4. [**Cẩm nang Quản trị CLI, Bảo mật & Di chuyển Dữ liệu (`docs/ADMIN_AND_CLI_GUIDE.md`)**](docs/ADMIN_AND_CLI_GUIDE.md) — Mô hình bảo mật `spanel-helper`, lệnh cứu hộ `spanel`, tường lửa, WAF và công cụ Migration từ DirectAdmin / cPanel.
5. [**Tài liệu Provisioning API, WHMCS & MCP AI (`docs/API_AND_WHMCS.md`)**](docs/API_AND_WHMCS.md) — Tích hợp WHMCS tự động cấp phát hosting và kết nối trợ lý AI qua giao thức MCP.
6. [**Trang Cổng thông tin Tài liệu (`docs/index.html`)**](docs/index.html) — Giao diện tài liệu trực quan hỗ trợ GitHub Pages.

---

## Điểm nổi bật của SPanel

### 1. Kiến trúc Đa tầng 3 Cấp độ (Admin / Reseller / User)
- **Admin (Quản trị viên Hệ thống):** Toàn quyền kiểm soát máy chủ, quản lý Reseller & User, định nghĩa gói dịch vụ (Packages), quản lý IP (`IP Manager`), Name Servers, tuỳ chỉnh cấu hình Nginx/HTTPD (`Custom HTTPD`), quản lý chứng chỉ TLS cho dịch vụ hệ thống (`Server TLS`), giám sát tiến trình (`Process Monitor`) và tài nguyên thời gian thực (`Resource Monitor`).
- **Reseller (Đại lý Hosting):** Khởi tạo và quản lý tài khoản khách hàng thuộc phạm vi đại lý, thiết lập gói hosting riêng, chuyển giao hoặc đổi mật khẩu người dùng, gửi thông báo riêng cho khách hàng và tuỳ biến thương hiệu (**White-Label Branding**: Logo, Favicon, Tên Panel, Chân trang).
- **End User (Người dùng cuối):** Không gian quản trị hosting trực quan, tối ưu theo bố cục DirectAdmin hiện đại, tự động ẩn các menu tiện ích nếu Admin chưa kích hoạt Add-on tương ứng trên máy chủ.

### 2. Quản lý Tên miền & Subdomain chuẩn DirectAdmin
- **Domain Setup (`/website`):** Quản lý tập trung toàn bộ tên miền chính với thanh trạng thái Quota, thêm tên miền mới (`+ ADD NEW`), đổi tên miền (`✎ RENAME DOMAIN`), bật/tắt bắt buộc HTTPS (`Force SSL`), chọn phiên bản PHP riêng cho từng tên miền, bật/tắt cô lập `Open_basedir`, theo dõi băng thông, dung lượng ổ đĩa, số lượng Subdomain, đường dẫn `Document Root` và tuỳ chỉnh ẩn/hiện cột (`Columns ⌄`).
- **Subdomain Management (`/subdomains`):** Thanh chọn tên miền nhanh ở đầu trang (`Top Domain Selector Bar`), tạo Subdomain tức thì với bộ nhập tiền tố tự động chuẩn hoá, hỗ trợ thư mục `Document Root` mặc định hoặc tuỳ chỉnh, lọc/tìm kiếm nhanh và xoá hàng loạt.

### 3. Ngăn xếp Web, Database & Ứng dụng Hiệu năng cao
- **Multi-PHP chạy song song:** Hỗ trợ PHP `5.6`, `7.4`, `8.0`, `8.1`, `8.2`, `8.3`, `8.4` (mặc định) và `8.5`. Mỗi website chạy trên một pool PHP-FPM độc lập dưới quyền Linux user riêng.
- **WordPress 1-Click & Ứng dụng hiện đại:** Cài đặt WordPress tự động qua WP-CLI; triển khai ứng dụng **Node.js**, **Python**, **Docker Container** và **Docker Compose** qua Reverse Proxy Nginx (Add-on Application).
- **MariaDB & phpMyAdmin SSO:** Quản lý cơ sở dữ liệu, người dùng DB và đăng nhập tự động vào phpMyAdmin chỉ với 1 lần bấm (token một lần 60 giây).
- **File Manager & Web Terminal:** Trình quản lý tệp tin hỗ trợ chỉnh sửa code, tải lên, nén/giải nén, phân quyền và giữ nguyên quyền sở hữu Linux user; kèm Web Terminal chạy trong không gian cô lập `unshare`.

### 4. Bảo mật Đa lớp & Tường lửa Chủ động
- **Cô lập Đặc quyền (`spanel-helper`):** Tiến trình web `spanel-api` chạy dưới user `spanel` không có quyền root. Mọi tác vụ hệ thống đều đi qua `/usr/local/sbin/spanel-helper` với cơ chế kiểm duyệt tham số nghiêm ngặt.
- **Tường lửa `iptables + ipset` & Brute Force Monitor:** Bảo vệ các cổng trọng yếu, chặn IP tức thì, nạp danh sách đen (Blocklist URL) hàng triệu IP với độ trễ bằng 0 và tích hợp Fail2ban chống dò mật khẩu.
- **WAF ModSecurity + OWASP CRS:** Bật/tắt luật bảo vệ ứng dụng web theo từng website, chống tấn công SQLi, XSS, LFI và chống HTTP Flood.
- **Quét Mã độc (LMD + ClamAV):** Quét theo yêu cầu, lập lịch quét định kỳ và giám sát tệp tải lên theo thời gian thực.
- **Xác thực 2 lớp (2FA TOTP & WebAuthn Passkey):** Bảo vệ tài khoản với Google Authenticator và khóa bảo mật sinh trắc học Passkey.

### 5. Hệ thống Thông báo Phân loại & Tối ưu Di động
- **Phân loại Thông báo theo Vai trò:** Tự động phân luồng thông báo riêng biệt cho **Admin**, **Reseller** và **End User** theo từng nhóm danh mục (`Hệ thống`, `Bảo mật`, `Sao lưu`, `Tài nguyên`, `Tài khoản`).
- **Tối ưu Hiển thị Di động (Mobile-First Responsive):** Tự động thu gọn cột bảng trên màn hình điện thoại (`<= 640px`), điều chỉnh lưới thẻ chức năng, thanh công cụ và hộp thoại Modal tương thích hoàn hảo trên cả iOS và Android.

---

## Yêu cầu Hệ thống

- **Hệ điều hành:** Ubuntu 24.04 LTS (64-bit, khuyến nghị cài mới).
- **Quyền truy cập:** `root` (hoặc `sudo`).
- **Phần cứng tối thiểu:** 1 vCPU / 1 GB RAM / 20 GB SSD.
- **Phần cứng khuyến nghị:** Từ 2 vCPU / 2 GB RAM trở lên (đặc biệt khi bật Email Rspamd hoặc Quét mã độc ClamAV).

---

## Cài đặt Nhanh trên VPS

Đăng nhập SSH vào máy chủ Ubuntu 24.04 LTS với quyền `root` và chạy lệnh:

```bash
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh -o /tmp/spanel-install.sh && sudo bash /tmp/spanel-install.sh
```

Hoặc cài đặt tự động không cần tương tác (Unattended):

```bash
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh | sudo bash -s -- \
  --admin-user admin \
  --admin-pass 'MatKhauManh@2026!' \
  --php-default 8.4
```

Sau khi hoàn tất, thông tin đăng nhập sẽ hiển thị trên màn hình và được lưu an toàn tại `/root/login.txt`.

---

## Cập nhật & Quản trị qua CLI

- **Cập nhật phiên bản mới trực tuyến:** Thực hiện trực tiếp trên giao diện web tại **Cài đặt → Cập nhật (Panel Updates)** hoặc chạy lệnh:
  ```bash
  sudo spanel-update
  ```
- **Menu cứu hộ & quản trị nhanh qua SSH:**
  ```bash
  sudo spanel
  ```

Xem hướng dẫn đầy đủ các lệnh CLI tại [`docs/ADMIN_AND_CLI_GUIDE.md`](docs/ADMIN_AND_CLI_GUIDE.md).

---

## Cấu trúc Thư mục Dự án

```text
spanel/
├── backend/          # FastAPI, SQLAlchemy, Alembic, Pydantic v2 & hệ thống dịch vụ hệ thống
├── frontend/         # Giao diện React 19 + Vite (Admin / Reseller / User & Mobile Responsive)
├── installer/        # Bộ cài đặt install.sh, update.sh, spanel-helper.sh, spanelctl, spanel-update
├── modules/          # Module tích hợp WHMCS (modules/servers/spanel/)
├── tools/i18n/       # Công cụ quản lý từ điển đa ngôn ngữ (Tiếng Việt / Tiếng Anh)
├── docs/             # Bộ tài liệu kỹ thuật & cổng thông tin GitHub Pages của SPanel
├── install.sh        # Script bootstrap cài đặt nhanh từ GitHub
└── VERSION           # Phiên bản phát hành chuẩn SemVer (1.0.6)
```

---

## Hỗ trợ & Đóng góp

- Báo lỗi hoặc đề xuất tính năng tại: [GitHub Issues](https://github.com/shostwp-byte/spanel/issues)
- Mã nguồn phát hành theo giấy phép **GNU Affero General Public License v3.0 (AGPL-3.0)**. Xem chi tiết tại [LICENSE](LICENSE).
