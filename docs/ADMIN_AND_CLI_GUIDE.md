# Cẩm nang Quản trị Hệ thống, Bảo mật & Lệnh CLI (`v1.0.6`)

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
| `sudo spanel dr-restore <file.spdr>` | Dựng lại trạng thái Panel trên máy chủ mới từ bản tự sao lưu của Panel (xem mục 6). Thêm `--inspect` để chỉ xem nội dung. |

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

### 4.1. PHP-FPM riêng cho từng tài khoản & cô lập tài nguyên

- Mỗi tài khoản hosting có **một master PHP-FPM riêng cho mỗi phiên bản PHP**: unit systemd `spanel-php<ver>-fpm@<user>.service` (template `/etc/systemd/system/spanel-php<ver>-fpm@.service`, drop-in `…@<user>.service.d/10-spanel-slice.conf` đặt `Slice=user-<uid>.slice`). Master và mọi worker sinh ra đều nằm ngay trong slice của tài khoản, nên giới hạn CPU/RAM/tasks/IO của gói áp dụng tức thì.
- Cấu hình: `/etc/spanel/php-fpm/<ver>/<user>/php-fpm.conf` và các pool trong `…/pool.d/`. Socket: `/run/spanel-php/<user>/<pool>.sock` (thư mục `root:www-data 0750`). `php.ini` và `conf.d` vẫn là của bản PHP hệ thống (`PHP_INI_SCAN_DIR`). Log: `/var/log/php/spanel-fpm-<user>-<ver>.log`, slowlog giữ tên cũ.
- `php<ver>-fpm` của hệ thống vẫn chạy cho phpMyAdmin/công cụ panel, và tiếp tục phục vụ pool nào chưa chuyển được (nếu master riêng không khởi động được, site được giữ trên master hệ thống và ghi log).
- Khi cập nhật, bước làm mới site chuyển từng site sang master riêng (không gián đoạn: master mới chạy song song, Nginx đổi socket, sau đó `spanel-helper php-fpm-legacy-prune` mới gỡ pool cũ).
- Kiểm tra: `systemctl status 'spanel-php*-fpm@*'`, `systemd-cgls /user.slice/user-<uid>.slice`.

**Quota đĩa của kernel (tùy chọn):** mặc định chỉ có kế toán dung lượng của panel (PHP/SFTP vẫn ghi vượt được). Bật quota kernel trong *Resource Monitor* hoặc khi cài/cập nhật với `SPANEL_DISK_QUOTA=kernel`: SPanel cài gói `quota` và thêm `usrjquota` (ext4) / `usrquota` (xfs) vào `/etc/fstab` (có bản sao lưu), **không bao giờ tự mount lại hay khởi động lại**. Sau khi bạn mount lại/khởi động lại, giới hạn dung lượng và số inode của từng gói được áp bằng `setquota`. Xem trạng thái: `spanel-helper quota-status`.

**Giới hạn MySQL theo giờ:** gói có thêm `MAX_QUERIES_PER_HOUR`, `MAX_UPDATES_PER_HOUR`, `MAX_CONNECTIONS_PER_HOUR` (0 = không giới hạn), áp cho mọi user MariaDB của tài khoản.

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

---

## 6. Khôi phục sau thảm hoạ (Disaster Recovery)

Bản sao lưu tài khoản khôi phục website, cơ sở dữ liệu và hộp thư. Bản **tự sao lưu của Panel** (Backups > Panel self-backup & verification) giữ phần còn lại: database của Panel (tài khoản, gói, đích sao lưu, lịch), `.env` (`SECRET_KEY` để giải mã mọi mật khẩu đã lưu), `/etc/spanel` (khoá audit, chứng chỉ Panel), `/etc/letsencrypt`, khoá DKIM, vùng DNS PowerDNS (theo backend SQLite/MySQL và dạng zone file), cấu hình nginx/PHP/mail mà Panel đã ghi.

**Passphrase:** do helper tạo (chỉ root đọc được, `/etc/spanel/dr-passphrase`) và chỉ hiển thị **một lần**. Hãy lưu nó ngoài máy chủ (trình quản lý mật khẩu, bản in). Lịch tự sao lưu chỉ chạy sau khi bạn gõ lại passphrase để xác nhận. Khi xoay vòng (rotate) passphrase, các bản cũ vẫn cần passphrase cũ.

Bản lưu trữ (`panel-dr-<host>-<thời gian>.spdr`, mã hoá AES-256-CBC + HMAC-SHA256) được đẩy lên các đích đã chọn trong thư mục `<hostname>/_panel-dr/` và chỉ giữ N bản mới nhất.

### Dựng lại một máy chủ đã mất

1. Cài SPanel mới (cùng phiên bản hoặc mới hơn) bằng `install.sh`.
2. Tải bản `.spdr` mới nhất từ đích sao lưu về máy chủ mới (`scp`, `aws s3 cp`, `rclone`...).
3. Chạy với quyền root:

   ```bash
   sudo spanel dr-restore /root/panel-dr-srv1-20261024T013000Z.spdr --inspect   # xem trước
   sudo spanel dr-restore /root/panel-dr-srv1-20261024T013000Z.spdr             # gõ RESTORE để xác nhận
   ```

   Lệnh kiểm tra HMAC trước khi giải mã, dừng `spanel-api` và các timer, giữ cấu hình hiện tại ở `/root/spanel-pre-dr-*.tar.gz`, rồi khôi phục database Panel, `.env`, `/etc/spanel`, chứng chỉ, PowerDNS và cấu hình mail/nginx của Panel. Vhost từng website, pool PHP-FPM và crontab của khách **không** được ghi đè (các tài khoản Linux chưa tồn tại); thêm `--with-site-configs` nếu cần.
4. Đăng nhập Panel > Backups > Restore & Destinations: chọn đích sao lưu (thông tin đăng nhập giải mã lại được vì `.env` đã về), chọn tất cả tài khoản và Restore. Bước này tạo lại tài khoản Linux, website, vhost, pool PHP, database và hộp thư.
5. Nếu IP thay đổi: `sudo spanel change-ip <IP_CŨ> <IP_MỚI>`, rồi trỏ DNS về máy chủ mới.

### Kiểm tra định kỳ

Backups > Panel self-backup & verification có lịch **kiểm tra backup**: kiểm tra toàn vẹn bản mới nhất của mỗi tài khoản trên máy chủ, tải mẫu từ đích xa (SHA-256, giải mã, liệt kê tar, có manifest) và tuỳ chọn khôi phục thử dump SQL vào một database tạm rồi xoá. Lỗi được gửi qua email/Telegram và webhook cảnh báo.
