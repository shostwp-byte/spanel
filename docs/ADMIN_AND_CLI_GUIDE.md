# Cẩm nang Quản trị Hệ thống, Bảo mật & Lệnh CLI (`v1.0.60`)

Tài liệu này cung cấp kiến thức chuyên sâu về mô hình bảo mật của **SPanel**, bộ công cụ dòng lệnh cứu hộ qua SSH, cơ chế tường lửa và quy trình di chuyển dữ liệu (Migration) từ các bảng điều khiển khác.

---

## 1. Mô hình Bảo mật Cô lập Đặc quyền (Privilege Separation)

Khác với các bảng điều khiển truyền thống chạy toàn bộ web server dưới quyền `root`, SPanel tách biệt hoàn toàn tầng giao diện/API và tầng thực thi hệ thống:

```text
[Trình duyệt] ---> [Nginx (:2222)] ---> [spanel-api (User: spanel, No Root)]
                                                |
                                                | sudo -n /usr/local/sbin/spanel-helper <subcommand>
                                                v
                                   [spanel-helper (Root Gateway)]
```

1. **Tiến trình `spanel-api` không chạy quyền `root`:**
   - Chạy dưới user hệ thống `spanel` với systemd unit được siết chặt (`NoNewPrivileges`, giới hạn ghi hệ thống tệp).
2. **Cổng kiểm duyệt `spanel-helper`:**
   - Mọi thao tác cần quyền `root` (tạo Linux user, cấu hình Nginx, cấp SSL, перезапуск dịch vụ, chỉnh tường lửa) đều phải gọi qua `/usr/local/sbin/spanel-helper`.
   - `spanel-helper` kiểm tra định dạng từng tham số (tên miền, đường dẫn, tên user, địa chỉ IP, số cổng) bằng biểu thức chính quy nghiêm ngặt trước khi thực thi. Mọi lệnh ngoài danh sách cho phép (Allowlist) đều bị từ chối.
3. **Cô lập Từng Tài khoản Hosting (Tenant Isolation):**
   - Mỗi người dùng sở hữu một tài khoản Linux riêng (`/home/<username>`), chạy pool PHP-FPM riêng dưới đúng UID/GID của người dùng đó kèm giới hạn `open_basedir`.
   - Truy cập SFTP được giam (chroot) trong thư mục cá nhân với shell `nologin`.
   - Công cụ **Web Terminal** của website chạy trong không gian cô lập `unshare --mount` kèm bộ lọc lệnh và giới hạn thời gian thực thi (`timeout`).

---

## 2. Công cụ Quản trị & Cứu hộ qua SSH (`spanel` / `spanelctl`)

Khi cần kiểm tra nhanh trạng thái máy chủ hoặc xử lý sự cố qua SSH, chạy lệnh:

```bash
sudo spanel
```

Menu tương tác sẽ hiển thị các chức năng cứu hộ toàn diện. Ngoài ra, bạn có thể gọi trực tiếp từng lệnh con:

| Lệnh CLI | Chức năng |
|---|---|
| `sudo spanel` | Mở menu cứu hộ tương tác qua SSH. |
| `sudo spanel status` | Kiểm tra trạng thái toàn bộ dịch vụ cốt lõi (Nginx, MariaDB, Redis, PHP-FPM, API). |
| `sudo spanel login-info` | Xem lại địa chỉ truy cập Panel và thông tin đăng nhập quản trị viên. |
| `sudo spanel change-admin-password` | Đặt lại mật khẩu cho tài khoản `admin` trên giao diện web. |
| `sudo spanel sync-admin-root-password` | Đồng bộ mật khẩu `admin` của Panel theo mật khẩu `root` hiện tại của Linux. |
| `sudo spanel change-ip <IP_CŨ> <IP_MỚI>` | Đổi địa chỉ IP máy chủ hàng loạt trong cấu hình Nginx vhost và DNS Zone. |
| `sudo spanel fix-permissions` | Khôi phục chuẩn phân quyền thư mục `/opt/spanel`, `/home/*` và cấu hình SFTP Chroot. |
| `sudo spanel ssl` | Cấp mới hoặc gia hạn chứng chỉ SSL Let's Encrypt cho tên miền quản trị Panel. |
| `sudo spanel-update` | Kiểm tra và cập nhật SPanel lên phiên bản mới nhất từ GitHub. |
| `sudo spanel-rescue-firewall` | Cứu hộ khẩn cấp khi cấu hình nhầm tường lửa: sao lưu luật cũ và mở lại các cổng thiết yếu. |

---

## 3. Tường lửa `iptables + ipset` & Bảo vệ Web (WAF)

### 3.1. Cơ chế Tường lửa (`/var/lib/spanel/firewall/rules.tsv`)
- Toàn bộ luật tường lửa được lưu trữ tập trung tại `/var/lib/spanel/firewall/rules.tsv` và được dịch vụ `spanel-firewall.service` nạp tự động khi khởi động máy chủ.
- **Cổng bảo vệ mặc định:** Cổng SSH hiện hành, cổng quản trị SPanel (`2222`), cùng các cổng `80`, `443`, `465`, `587` luôn được bảo vệ để tránh quản trị viên tự khoá mình bên ngoài máy chủ.
- **Hiệu năng cao với `ipset`:** Danh sách đen theo URL (Blocklist) chứa hàng trăm nghìn đến hàng triệu IP được nạp vào bảng băm `spanel-block4` / `spanel-block6` của nhân Linux, giúp kiểm tra gói tin với độ phức tạp $O(1)$.

### 3.2. WAF ModSecurity & OWASP CRS
- Tích hợp sẵn **ModSecurity v3** cùng bộ luật **OWASP Core Rule Set (CRS)**.
- Cho phép bật/tắt WAF riêng cho từng website, cấu hình giới hạn tốc độ truy cập (**HTTP Flood / Rate Limit**), chặn Bot độc hại và thêm luật tuỳ chỉnh toàn cục ngay trên giao diện web.

---

## 4. Tự động Tinh chỉnh Hiệu năng (Auto-Tuning PHP-FPM & MariaDB)

SPanel tự động tính toán cấu hình tối ưu cho PHP-FPM và MariaDB dựa trên dung lượng RAM và số vCPU của máy chủ. Nếu bạn vừa nâng cấp RAM/CPU cho VPS, hãy chạy hai lệnh sau để SPanel tính toán lại thông số mới:

```bash
sudo -u spanel env HOME=/opt/spanel sudo -n /usr/local/sbin/spanel-helper php-fpm-retune
sudo -u spanel env HOME=/opt/spanel sudo -n /usr/local/sbin/spanel-helper mariadb-retune
```

Bạn cũng có thể ghi đè thông số cố định trong `/opt/spanel/backend/.env` thông qua các biến `SPANEL_PHP_FPM_MAX_CHILDREN` và `SPANEL_MARIADB_BUFFER_POOL_SIZE`.

---

## 5. Di chuyển Dữ liệu từ DirectAdmin & cPanel sang SPanel

SPanel hỗ trợ công cụ **Migration & Restore** mạnh mẽ giúp chuyển khách hàng từ máy chủ cũ sang SPanel nhanh chóng:

1. **Từ DirectAdmin:**
   - Hỗ trợ các tệp sao lưu chuẩn của DirectAdmin (`user.admin.<username>.tar.zst`, `user.admin.<username>.tar.gz` hoặc `backup-*.tar.gz`).
   - Tự động khôi phục: Tên miền chính, Subdomains, mã nguồn trong `public_html`, cơ sở dữ liệu MySQL/MariaDB, tài khoản Email và bản ghi DNS.
2. **Từ cPanel:**
   - Hỗ trợ tệp sao lưu đầy đủ của cPanel (`cpmove-<username>.tar.gz` hoặc `backup-*_<username>.tar.gz`).
   - Quá trình giải nén được bảo vệ chống tấn công Path Traversal / Symlink, tự động chuyển đổi cấu trúc thư mục và cơ sở dữ liệu sang chuẩn của SPanel.
3. **Khôi phục từ Kho lưu trữ Từ xa (S3 / SFTP / FTP):**
   - Hỗ trợ kết nối trực tiếp tới AWS S3, Cloudflare R2, Wasabi, Backblaze B2, MinIO hoặc máy chủ SFTP/FTP từ xa để chọn danh sách tài khoản và khôi phục hàng loạt trong nền.
