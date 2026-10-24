# 📊 BẢNG THỐNG KÊ CHI TIẾT TIẾN ĐỘ CHỨC NĂNG SPANEL (`v1.0.7`)

Dựa trên cấu trúc Menu chuẩn DirectAdmin (Evolution Skin) và kết quả rà soát mã nguồn tại phiên bản `v1.0.7`, dưới đây là báo cáo phân tích rạch ròi các tính năng đã hoàn thiện, đang ở mức một phần, hoặc chưa triển khai.

---

## 1. 🛡️ QUYỀN ADMIN (QUẢN TRỊ VIÊN)

| Module / Danh mục | Tên chức năng (Menu) | Trạng thái thực tế trong SPanel (`v1.0.7`) |
| :--- | :--- | :--- |
| **Account Manager** | Show All Users | 🟢 **Hoàn thiện** (Quản lý User, tìm kiếm, lọc, khóa/mở khóa, đăng nhập một chạm) |
| | My Users | 🟢 **Hoàn thiện** |
| | Manage User Packages | 🟢 **Hoàn thiện** (Gói cước User kèm hạn mức CPU, RAM, I/O, Tasks, MySQL) |
| | Move Users Between Resellers | 🟢 **Hoàn thiện** |
| | Message templates | 🟢 **Hoàn thiện** (Mẫu email & tin nhắn thông báo) |
| | Change Passwords | 🟢 **Hoàn thiện** (Đổi mật khẩu Panel và SFTP) |
| | Resellers | 🟢 **Hoàn thiện** (Quản lý Reseller) |
| | Manage Reseller Packages | 🟢 **Hoàn thiện** (Gói cước Reseller & Overselling) |
| | Administrators | 🟢 **Hoàn thiện** (Quản lý Admin) |
| **Server Manager** | Administrator Settings | 🟢 **Hoàn thiện** |
| | Custom HTTPD Configurations | 🟢 **Hoàn thiện** (Tùy chỉnh Nginx/Webserver kèm kiểm tra `nginx -t`) |
| | DNS Administration | 🟢 **Hoàn thiện** (PowerDNS + 1-Click DNSSEC ECDSAP256SHA256) |
| | IP Management | 🟢 **Hoàn thiện** |
| | Name Servers | 🟢 **Hoàn thiện** |
| | Multi Server Setup | 🟡 **Một phần** (Đồng bộ DNS Slave hoạt động; Remote MySQL mới hỗ trợ lưu cấu hình & kiểm tra kết nối, chưa mở cấp phát DB từ xa) |
| | PHP Settings | 🟢 **Hoàn thiện** (Quản lý đa phiên bản PHP 5.6 – 8.5, cấu hình `php.ini` & FPM) |
| | Server TLS Certificate | 🟢 **Hoàn thiện** (Let's Encrypt, Cloudflare DNS-01 Wildcard, SNI per-domain) |
| **Admin Tools** | Backup and Restore | 🟢 **Hoàn thiện** (Full / Incremental, SFTP / S3, mã hóa AES-256-GCM + xác thực SHA-256) |
| | Brute Force Monitor | 🟢 **Hoàn thiện** (Tích hợp Fail2ban + iptables/ipset) |
| | Process Monitor | 🟢 **Hoàn thiện** (Giám sát tiến trình, MySQL Slow Query Sniper & PHP-FPM Slowlog) |
| | Mail Queue Administration | 🟢 **Hoàn thiện** (Exim4 + Dovecot + Rspamd + Roundcube) |
| | System Services | 🟢 **Hoàn thiện** (Giám sát & tự động phục hồi qua `spanel-watchdog.service`) |
| | cPanel / DirectAdmin Import | 🟢 **Hoàn thiện** (Khôi phục từ gói backup cPanel / DirectAdmin / SPanel) |
| | Message All Users | 🟢 **Hoàn thiện** (Gửi thông báo phân loại theo vai trò) |
| | Customize Skin | 🟢 **Hoàn thiện** (White-label Logo, Favicon, Màu chủ đạo, Tên Panel) |
| **System Info & Files** | All User Cron Jobs | 🟢 **Hoàn thiện** (Cô lập trong `spanel-cron-jail` `/etc/cron.d/spanel-user-*`, bảo vệ cả toán tử chuyển hướng output) |
| | System File Editor | 🟢 **Hoàn thiện** |
| | Log Viewer & Audit Chain | 🟢 **Hoàn thiện** (Nhật ký truy cập/lỗi website + Audit Log chuỗi băm HMAC-SHA256 với khóa riêng `/etc/spanel/audit-hmac.key`) |
| | Resource & Server Statistics | 🟢 **Hoàn thiện** (Giám sát tài nguyên CPU/RAM/IO/MySQL và phân bổ PHP-FPM vào `user-<uid>.slice`) |
| **Extra Features** | Addon Manager | 🟢 **Hoàn thiện** |
| | Malware Scanner | 🟢 **Hoàn thiện** (LMD + ClamAV + quét YARA/Heuristic khi tải lên và sau khi giải nén nén) |
| | Firewall (`iptables + ipset`) | 🟢 **Hoàn thiện** |
| **Support** | Create Ticket | 🟢 **Hoàn thiện** (Liên kết hệ thống hỗ trợ tùy chỉnh) |
| | Updates | 🟢 **Hoàn thiện** (Cập nhật trực tuyến kèm khóa phiên bản tùy chọn cho quản trị viên) |

---

## 2. 🤝 QUYỀN RESELLER (ĐẠI LÝ)

| Module / Danh mục | Tên chức năng (Menu) | Trạng thái thực tế trong SPanel (`v1.0.7`) |
| :--- | :--- | :--- |
| **Account Manager** | Show All Users | 🟢 **Hoàn thiện** (Quản lý User thuộc phạm vi Reseller) |
| | Add New User | 🟢 **Hoàn thiện** (Tạo tài khoản khách hàng trực tiếp từ danh sách User) |
| | Manage User Packages | 🟢 **Hoàn thiện** (Gói cước User thuộc Reseller) |
| | Message templates | 🟢 **Hoàn thiện** |
| | Change Passwords | 🟢 **Hoàn thiện** |
| **Reseller Manager** | Reseller Settings & Branding | 🟢 **Hoàn thiện** (White-Label Branding riêng cho từng đại lý) |
| | Manage User IPs | 🟢 **Hoàn thiện** (Phân bổ IP chia sẻ hoặc IP riêng cho khách hàng) |
| | Name Servers | 🟢 **Hoàn thiện** |
| | User Backups | 🟢 **Hoàn thiện** (Sao lưu các tài khoản thuộc quyền quản lý của đại lý) |
| **Support** | Create Ticket / Notifications | 🟢 **Hoàn thiện** |

---

## 3. 👤 QUYỀN END USER (NGƯỜI DÙNG CUỐI)

| Module / Danh mục | Tên chức năng (Menu) | Trạng thái thực tế trong SPanel (`v1.0.7`) |
| :--- | :--- | :--- |
| **Account Manager** | Domain Setup (`/website`) | 🟢 **Hoàn thiện** (Thêm/đổi tên miền, PHP version, Force SSL, Open_basedir) |
| | Subdomain Management | 🟢 **Hoàn thiện** |
| | DNS Management | 🟢 **Hoàn thiện** (Quản lý bản ghi DNS & 1-Click DNSSEC; hiển thị "Không xác định" khi PowerDNS không phản hồi) |
| | SSL Certificates | 🟢 **Hoàn thiện** (Let's Encrypt, Wildcard Cloudflare, Custom SSL, Shared SSL) |
| | SFTP Management | 🟢 **Hoàn thiện** (Tài khoản chính & tài khoản phụ chroot theo từng website) |
| | MySQL Management | 🟢 **Hoàn thiện** (Tạo DB/User cục bộ, đổi mật khẩu, khóa ghi khi vượt hạn mức, phpMyAdmin 1-Click SSO) |
| **Email Manager** | E-Mail Accounts | 🟢 **Hoàn thiện** |
| | Forwarders | 🟢 **Hoàn thiện** |
| | Autoresponders | 🟢 **Hoàn thiện** |
| | Webmail (Roundcube) | 🟢 **Hoàn thiện** |
| **Advanced Features** | Cronjobs | 🟢 **Hoàn thiện** (Chạy trong Mount Namespace Jail `spanel-cron-jail`) |
| | ModSecurity WAF & Bot Block | 🟢 **Hoàn thiện** (OWASP CRS Detect/Block + HTTP Flood + Chặn Bot theo site) |
| | Applications (Node/Python/Docker) | 🟢 **Hoàn thiện** (Kèm Git Webhook bắt buộc chữ ký HMAC với secret riêng biệt) |
| **System Info & Files** | File Manager | 🟢 **Hoàn thiện** (Quản lý tệp, chỉnh sửa code, nén/giải nén có quét mã độc YARA/Heuristic) |
| | Site Summary / Statistics / Logs | 🟢 **Hoàn thiện** |

---

## ⚠️ CÁC HẠNG MỤC ĐANG GIỚI HẠN HOẶC LỘ TRÌNH TIẾP THEO

1. **Remote MySQL Provisioning (`Multi-Server`):** Đã có giao diện lưu host từ xa và kiểm tra kết nối, nhưng tính năng tạo DB khách hàng trên máy chủ từ xa hiện được khóa chủ động (`HTTP 501`) để đảm bảo an toàn cho đến khi hoàn tất đồng bộ sao lưu, xóa và phpMyAdmin SSO từ xa.
2. **Kiến trúc cơ sở dữ liệu điều khiển:** Sử dụng SQLite (`WAL` mode) cho mô hình 1 máy chủ độc lập; chưa hỗ trợ gom cụm nhiều máy chủ dùng chung 1 cơ sở dữ liệu PostgreSQL/MySQL trung tâm.
