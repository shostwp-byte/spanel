# Danh mục Tính năng Chi tiết của SPanel (`v1.0.6`)

Tài liệu này mô tả chi tiết các nhóm tính năng của **SPanel** được phân chia theo 3 vai trò người dùng (**Admin**, **Reseller**, **End User**) cùng cơ chế quản lý tiện ích mở rộng (**Add-ons**).

---

## 1. Hệ thống Phân quyền 3 Cấp độ (Role-Based Access Control)

| Tính năng / Khu vực | Admin (`admin`) | Reseller (`reseller`) | End User (`user`) |
|---|:---:|:---:|:---:|
| Quản lý Website, Tên miền & Subdomain | Toàn máy chủ | Các user thuộc đại lý | Các tên miền của mình |
| Quản lý File, Database, Cron, SSL, SFTP | Toàn máy chủ | Các user thuộc đại lý | Tài khoản của mình |
| Khởi tạo & Quản lý Người dùng (Users) | Tạo Admin / Reseller / User | Tạo & quản lý User thuộc đại lý | Không |
| Quản lý Gói dịch vụ (Packages) | Gói Reseller & Gói User | Gói User của đại lý | Xem hạn mức cá nhân |
| Tuỳ biến Thương hiệu (White-Label Branding) | Toàn hệ thống | Riêng cho khách của đại lý | Hiển thị theo đại lý/hệ thống |
| Chuyển đổi tư cách đăng nhập (Impersonation) | Đăng nhập vào mọi Reseller/User | Đăng nhập vào User của đại lý | Không |
| Quản trị Dịch vụ, PHP, Tường lửa, WAF toàn cục | Có | Không | Không |
| Quản lý IP, Name Servers, Custom HTTPD, Server TLS | Có | Không | Không |
| Cài đặt / Bật tắt Tiện ích Mở rộng (Add-ons) | Có | Phụ thuộc cấu hình Admin | Tự động ẩn/hiện theo Admin |
| Cập nhật Phiên bản Panel (Online Updates) | Có | Không | Không |

---

## 2. Trải nghiệm Quản trị Tên miền & Subdomain (Chuẩn DirectAdmin)

SPanel mang đến trải nghiệm quản lý tên miền quen thuộc, chuyên nghiệp và đồng nhất với **DirectAdmin**:

### 2.1. Quản lý Tên miền chính — Domain Setup (`/website`)
- **Thanh Chọn Tên miền Nhanh (`Top Domain Selector Bar`):** Cho phép chuyển đổi ngữ cảnh tên miền đang làm việc ở bất kỳ đâu, kèm huy hiệu đếm tổng số tên miền hiện có.
- **Thêm Tên miền Mới (`+ ADD NEW`):**
  - Khởi tạo nhanh Website PHP truyền thống, cài đặt **WordPress 1-Click** (tự động cấu hình WP-CLI, tạo tài khoản quản trị WordPress), hoặc khởi tạo ứng dụng Node.js / Docker.
  - Chọn phiên bản PHP riêng biệt ngay khi tạo.
- **Đổi Tên miền (`✎ RENAME DOMAIN`):**
  - Cho phép đổi tên miền hiện có sang tên miền mới thông qua hộp thoại trực quan, tự động cập nhật cấu hình Nginx vhost và thư mục liên quan.
- **Thanh Công cụ Quota & Hành động Hàng loạt:**
  - Hiển thị hạn mức số lượng Domain đang sử dụng so với giới hạn gói dịch vụ.
  - Chọn một hoặc nhiều tên miền để thực hiện nhanh: **Bật/Tắt Force SSL (HTTPS Redirect)**, **Đổi phiên bản PHP**, **Bật/Tắt cô lập `Open_basedir`**, hoặc **Xoá tên miền** (kèm xác nhận an toàn).
- **Bảng Dữ liệu Thông minh & Nút chọn cột (`Columns ⌄`):**
  - Hiển thị đầy đủ: `Domain`, `Bandwidth`, `Disk Usage`, `Subdomains` (bấm vào để chuyển thẳng sang danh sách Subdomain của tên miền đó), `Document Root` (bấm vào để mở ngay File Manager tại thư mục gốc của web), `PHP Version`, `SSL Status` và `Actions`.
  - Trên màn hình điện thoại (`<= 640px`), bảng tự động ẩn bớt các cột phụ (`Bandwidth`, `Disk Usage`, `Document Root`) để hiển thị gọn gàng mà vẫn cho phép bật lại qua nút `Columns ⌄`.

### 2.2. Quản lý Tên miền phụ — Subdomain Management (`/subdomains`)
- **Tạo Subdomain Tức thì (`+ ADD SUBDOMAIN`):**
  - Khung nhập liệu thông minh tách rõ phần tiền tố (`sub`) và phần hậu tố (`.domain.com`).
  - Ngay cả khi người dùng dán cả cụm `blog.domain.com` vào ô nhập, hệ thống sẽ tự động bóc tách chính xác phần tiền tố `blog`.
  - Cho phép sử dụng đường dẫn `Document Root` mặc định hoặc chỉ định thư mục tuỳ chỉnh bên trong `public_html`.
- **Quản lý Tập trung theo Tên miền:**
  - Lọc danh sách Subdomain theo tên miền đang chọn trên thanh `Top Domain Selector Bar`.
  - Theo dõi lưu lượng băng thông, đường dẫn thư mục gốc, trạng thái SSL, phiên bản PHP và xoá nhanh từng Subdomain hoặc xoá hàng loạt.

---

## 3. Tính năng Dành cho Quản trị viên (Admin Level)

1. **Giám sát Tài nguyên & Tiến trình (`Resource Monitor` & `Process Monitor`):**
   - Biểu đồ và chỉ số thời gian thực cho CPU, RAM, Disk I/O, Network Traffic.
   - Xem danh sách tiến trình đang tiêu thụ nhiều CPU/RAM nhất trên máy chủ và kết thúc (Kill) tiến trình treo.
2. **Quản lý Địa chỉ IP & Name Servers (`IP Manager` & `Name Servers`):**
   - Quản lý danh sách IPv4/IPv6 trên máy chủ, phân bổ IP dùng chung (Shared) hoặc IP riêng (Dedicated) cho từng Reseller/User.
   - Thiết lập cặp Name Server mặc định (`ns1`, `ns2`) cho toàn bộ DNS Zone mới khởi tạo.
3. **Tuỳ chỉnh Cấu hình Máy chủ Web (`Custom HTTPD`) & Chứng chỉ Hệ thống (`Server TLS`):**
   - Xem và chèn cấu hình Nginx tuỳ chỉnh cho từng tên miền kèm kiểm tra cú pháp `nginx -t` tự động trước khi áp dụng.
   - Quản lý chứng chỉ SSL/TLS cho chính cổng giao diện SPanel, Mail Server (Exim/Dovecot) và FTP/SFTP.
4. **Quản lý Đa phiên bản PHP & Dịch vụ Hệ thống:**
   - Cài đặt/gỡ cài đặt các phiên bản PHP (`5.6` đến `8.5`), bật/tắt PHP Extensions, chỉnh sửa `php.ini` và tự động tối ưu `pm.max_children` của PHP-FPM cũng như `innodb_buffer_pool_size` của MariaDB theo dung lượng RAM thực tế của VPS.
5. **Di chuyển Dữ liệu Toàn diện (`Panel Migration`):**
   - Hỗ trợ nhập gói sao lưu từ **DirectAdmin** (`.tar.zst`, `.tar.gz`) và **cPanel** (`cpmove-*.tar.gz`), tự động khôi phục mã nguồn website, cơ sở dữ liệu MariaDB, hộp thư Email và bản ghi DNS.
6. **Gửi Thông báo Toàn hệ thống (`Message All Users` & `Message Templates`):**
   - Soạn thảo và gửi thông báo bảo trì, cảnh báo tài nguyên đến toàn bộ người dùng hoặc nhóm Reseller dựa trên mẫu có sẵn.

---

## 4. Tính năng Dành cho Đại lý (Reseller Level)

1. **Quản lý Khách hàng & Gói Dịch vụ Đại lý:**
   - Tạo, tạm khoá (Suspend), mở khoá (Unsuspend) và xoá tài khoản End User thuộc quyền quản lý của đại lý.
   - Tạo các gói dịch vụ (`User Packages`) trong phạm vi hạn mức dung lượng, băng thông và số lượng tên miền được Admin cấp.
2. **Tuỳ biến Thương hiệu Riêng (`White-Label Branding`):**
   - Đại lý có thể tự tải lên **Logo**, **Favicon**, đặt **Tên Bảng điều khiển (Brand Name)** và **Văn bản chân trang (Footer Text)** riêng. Khi khách hàng thuộc đại lý đăng nhập, giao diện sẽ hiển thị hoàn toàn theo nhận diện thương hiệu của đại lý.
3. **Công cụ Quản trị Hàng loạt:**
   - Đổi mật khẩu nhanh cho nhiều tài khoản khách hàng (`Change Passwords`).
   - Sao lưu và khôi phục dữ liệu cho các tài khoản thuộc quyền quản lý của đại lý.

---

## 5. Hệ thống Thông báo Phân loại (Categorized Notifications)

Không còn sử dụng chung một luồng thông báo hỗn tạp, SPanel phân loại thông báo thông minh theo đúng **Vai trò (Role)** và **Danh mục (Category)**:

- **Phân tách theo Vai trò:**
  - **Admin:** Nhận cảnh báo dịch vụ hệ thống dừng hoạt động, tường lửa/Fail2ban, trạng thái ổ đĩa VPS, bản cập nhật SPanel mới và kết quả quét mã độc toàn máy chủ.
  - **Reseller:** Nhận thông báo về hạn mức gói đại lý, trạng thái khởi tạo/sao lưu của các tài khoản khách hàng trực thuộc và thông báo từ Admin.
  - **End User:** Chỉ nhận các thông báo gắn liền với tài khoản cá nhân (gia hạn SSL của tên miền, kết quả sao lưu/khôi phục website, cảnh báo dung lượng gói hosting, thông báo từ nhà cung cấp).
- **Bộ lọc Trực tiếp trên Giao diện:**
  - Hộp thoại chuông thông báo trên thanh Topbar tích hợp các tab lọc nhanh: `Tất cả`, `Chưa đọc`, `Hệ thống`, `Bảo mật`, `Sao lưu`, `Tài nguyên`.

---

## 6. Hệ thống Tiện ích Mở rộng (Add-ons) & Tự động Đồng bộ Menu

Các thành phần chuyên biệt được đóng gói dưới dạng **Add-on** tại trang **Cài đặt → Tiện ích (Add-ons)**:

| Tên Add-on | Chức năng chi tiết | Đồng bộ Menu End User |
|---|---|---|
| **Application (Node.js & Docker)** | Triển khai ứng dụng Node.js, Python, Docker Container và Docker Compose chạy sau Nginx Reverse Proxy. | Chỉ hiện menu **Applications** khi Admin bật Add-on này. |
| **Email Server Suite** | Cài đặt Exim4, Dovecot, Rspamd lọc thư rác và SPanel Webmail (cổng `2096`) hỗ trợ đăng nhập 1-click (SSO), Forwarders, Autoresponders, DKIM/DMARC. | Chỉ hiện cụm menu **Email Accounts / Forwarders / Autoresponders / Webmail** khi Admin bật Add-on Email. |
| **DNS Management (PowerDNS)** | Máy chủ DNS Authoritative trên cổng `53`, tự động khởi tạo DNS Zone và quản lý bản ghi `A`, `AAAA`, `CNAME`, `MX`, `TXT`, `SRV`, `CAA`, `NS`. | Chỉ hiện menu **DNS Management** khi Admin bật Add-on DNS. |
| **Malware Scanner (LMD + ClamAV)** | Quét mã độc theo yêu cầu, quét tự động theo lịch và giám sát tệp tin tải lên theo thời gian thực. | Chỉ hiện menu **Malware Scanner** khi Admin bật Add-on này. |
| **Fail2ban Brute-Force Protection** | Giám sát nhật ký SSH và dịch vụ để tự động khóa IP dò mật khẩu. | Hiển thị trong khu vực Bảo mật & Brute Force Monitor của Admin. |
| **AI Assistant & MCP Server** | Cung cấp giao thức Model Context Protocol (MCP) bằng token cá nhân để tích hợp Claude Code, Cursor, VS Code và trợ lý AI nội bộ. | Chỉ hiện menu **MCP / AI Assistant** khi được kích hoạt. |
| **Notifications (SMTP & Telegram)** | Gửi cảnh báo tức thì qua Email SMTP và Telegram Bot khi có sự cố dịch vụ, đầy ổ đĩa hoặc phát hiện mã độc. | Quản lý tại trang Thông báo của Admin. |
| **Demo Mode** | Bật chế độ trải nghiệm chỉ đọc (Read-only) an toàn cho mục đích giới thiệu sản phẩm. | Khoá mọi thao tác ghi/xoá trên toàn hệ thống. |
