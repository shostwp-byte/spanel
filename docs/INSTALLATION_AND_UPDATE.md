# Hướng dẫn Cài đặt & Cập nhật Trực tuyến SPanel (`v1.0.60`)

Tài liệu này hướng dẫn chi tiết quy trình triển khai **SPanel** trên máy chủ VPS mới hoàn toàn cũng như quy trình cập nhật phiên bản mới trực tuyến.

---

## 1. Yêu cầu Hệ thống

- **Hệ điều hành:** **Ubuntu 24.04 LTS (Noble Numbat)** 64-bit. Khuyến nghị sử dụng máy chủ mới cài đặt (Fresh OS) để tránh xung đột cổng dịch vụ.
- **Quyền thực thi:** Tài khoản `root` hoặc người dùng có quyền `sudo`.
- **Cấu hình phần cứng:**
  - **Tối thiểu:** 1 vCPU, 1 GB RAM, 20 GB dung lượng đĩa trống (trình cài đặt sẽ tự động khởi tạo Swap nếu RAM thấp).
  - **Khuyến nghị:** 2+ vCPU, 2 GB – 4 GB RAM trở lên (đặc biệt nếu sử dụng trọn bộ Email Server Rspamd và trình quét mã độc ClamAV).
- **Cổng mạng (Network Ports):**
  - `2222/tcp`: Cổng giao diện quản trị SPanel mặc định (hỗ trợ HTTPS/SNI).
  - `80/tcp`, `443/tcp`: Máy chủ web Nginx & cấp phát SSL Let's Encrypt.
  - `22/tcp`: SSH & SFTP (chroot).
  - `53/tcp`, `53/udp`: PowerDNS (khi bật Add-on DNS Management).
  - `25`, `465`, `587`, `993`, `995`, `2096`: Dịch vụ Email & Webmail (khi bật Add-on Email).

---

## 2. Quy trình Cài đặt trên VPS Mới

### Cách 1: Cài đặt Tương tác (Interactive Mode — Khuyến nghị)
Đăng nhập SSH vào VPS với quyền `root` và chạy lệnh:

```bash
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh -o /tmp/spanel-install.sh && sudo bash /tmp/spanel-install.sh
```

Trong quá trình chạy, trình cài đặt sẽ cho phép bạn thiết lập:
1. **Tên miền của Panel (Hostname):** Nhập tên miền đã trỏ IP về VPS (hoặc nhấn `Enter` để truy cập qua địa chỉ IP của VPS).
2. **Cổng truy cập Panel:** Mặc định là `2222`.
3. **Cấp chứng chỉ SSL Let's Encrypt:** Tự động cấp SSL hợp lệ nếu bạn sử dụng tên miền.

### Cách 2: Cài đặt Tự động Không Tương tác (Unattended Mode)
Phù hợp khi tích hợp vào Cloud-Init, User-Data hoặc script triển khai hàng loạt:

```bash
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh | sudo bash -s -- \
  --admin-user admin \
  --admin-pass 'MatKhauManh@2026!' \
  --admin-email admin@yourdomain.com \
  --php-default 8.4 \
  --panel-hostname panel.yourdomain.com
```

Hoặc sử dụng biến môi trường:

```bash
export PANEL_URL="https://panel.yourdomain.com:2222"
export ENABLE_SSL="yes"
export SSL_EMAIL="admin@yourdomain.com"
export SPANEL_ADMIN_PASSWORD="MatKhauManh@2026!"
curl -fsSL https://raw.githubusercontent.com/shostwp-byte/spanel/refs/heads/main/install.sh | sudo bash
```

---

## 3. Các Thành phần Được Trình cài đặt Thiết lập Tự động

Khi chạy `install.sh`, hệ thống thực hiện tuần tự các bước sau:

1. **Tải mã nguồn:** Kiểm tra Release Tag mới nhất từ `https://github.com/shostwp-byte/spanel` (hoặc tự động fallback tải nhánh `main` nếu kho lưu trữ chưa gắn tag).
2. **Cài đặt các gói hệ thống:** Nginx, MariaDB, Redis, PHP-FPM (mặc định `8.4` từ kho `ondrej/php`), OpenSSH/SFTP, Certbot, phpMyAdmin, WP-CLI, `iptables`, `ipset`, Node.js và Python `venv`.
3. **Thiết lập không gian chạy cô lập:**
   - Đặt mã nguồn tại `/opt/spanel`.
   - Khởi tạo user hệ thống `spanel` (không có quyền root) và cài đặt `/usr/local/sbin/spanel-helper` kèm cấu hình `/etc/sudoers.d/spanel`.
   - Tạo dịch vụ systemd `spanel-api.service` và `spanel-memory-guard.service`.
4. **Tự động tối ưu hiệu năng (Auto-Tuning):**
   - Tính toán thông số `pm.max_children` của PHP-FPM và `innodb_buffer_pool_size` của MariaDB dựa trên dung lượng RAM và số nhân CPU của VPS.
5. **Xuất thông tin đăng nhập:**
   - Hiển thị URL truy cập, tài khoản `admin`, mật khẩu ngẫu nhiên an toàn trên màn hình và lưu bản sao tại `/root/login.txt` (quyền `0600`).

---

## 4. Cấu hình Biến Môi trường (`/opt/spanel/backend/.env`)

Tệp cấu hình chính của Backend được đặt tại `/opt/spanel/backend/.env`:

```ini
APP_ENV=production
SECRET_KEY=<khoá-bí-mật-ngẫu-nhiên-32-byte>
COMMAND_DRY_RUN=false
DATABASE_URL=sqlite:////opt/spanel/backend/spanel.db
REDIS_URL=redis://localhost:6379/0
RATE_LIMIT_BACKEND=redis
BACKUP_ROOT=/var/backups/spanel
SSL_EMAIL=admin@yourdomain.com
PANEL_URL=https://panel.yourdomain.com:2222
PANEL_PORT=2222
PANEL_SNI_DIR=/etc/spanel/sni
FRONTEND_DIST=/opt/spanel/frontend/dist
```

Sau khi chỉnh sửa `.env`, khởi động lại dịch vụ API bằng lệnh:

```bash
sudo systemctl restart spanel-api
```

---

## 5. Quy trình Cập nhật Phiên bản Mới (Online Update)

SPanel tích hợp sẵn cơ chế cập nhật trực tuyến an toàn từ kho lưu trữ `https://github.com/shostwp-byte/spanel`.

### Cách 1: Cập nhật ngay trên Giao diện Web (Web UI)
1. Đăng nhập với tài khoản **Admin**.
2. Truy cập menu **Cài đặt → Cập nhật (Panel Updates)**.
3. Nhấn **Kiểm tra cập nhật (Check for updates)** để so sánh phiên bản hiện tại với kho GitHub.
4. Nhấn **Cập nhật ngay (Update Now)**. Tiến trình cập nhật sẽ chạy ngầm qua `systemd`, hiển thị thanh tiến độ theo thời gian thực và tự động khởi động lại `spanel-api` khi hoàn tất.

### Cách 2: Cập nhật qua Dòng lệnh SSH (CLI)
Quản trị viên có thể cập nhật bất kỳ lúc nào qua SSH:

```bash
# Cập nhật lên bản phát hành mới nhất (hoặc nhánh main mới nhất)
sudo spanel-update

# Hoặc chỉ định rõ chế độ cập nhật qua update.sh:
sudo bash /opt/spanel/installer/update.sh --release
sudo bash /opt/spanel/installer/update.sh --tag v1.0.60
sudo bash /opt/spanel/installer/update.sh --branch main
```

### Cơ chế An toàn khi Cập nhật:
- **Bảo toàn dữ liệu 100%:** Giữ nguyên cơ sở dữ liệu `/opt/spanel/backend/spanel.db`, tệp cấu hình `.env`, toàn bộ dữ liệu người dùng trong `/home/*` và cấu hình Nginx vhost.
- **Tự động chạy Migration DB:** Tự động nâng cấp cấu trúc bảng SQLAlchemy / Alembic mà không làm mất dữ liệu cũ.
- **Tự động Rollback khi gặp sự cố:** Trước khi ghi đè mã nguồn, `spanel-update` tạo bản sao lưu tạm thời của `backend` và `frontend`; nếu quá trình cài đặt gói phụ thuộc hoặc build giao diện thất bại, hệ thống tự động khôi phục về phiên bản trước đó.
