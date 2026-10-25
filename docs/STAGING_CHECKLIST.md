# Checklist cài thử trên VPS staging (bản sau v1.0.7)

Những mục dưới đây đã có test tự động cho phần logic, nhưng **chỉ kiểm chứng được trên máy chủ thật** (systemd, Exim, Dovecot, PowerDNS, MariaDB, nginx, quota của kernel). Chạy trên **Ubuntu 24.04** sạch, rồi lặp lại trên một máy đang chạy 1.0.6 có dữ liệu thật (cập nhật tại chỗ).

Mỗi mục: chạy lệnh hoặc thao tác, ghi **Đạt / Không đạt** kèm log. Mục nào không đạt thì không gắn tag phát hành.

## 0. Chuẩn bị

- [ ] 2 VPS Ubuntu 24.04 (node A, node B) và 1 VPS MariaDB riêng (cho Remote MySQL), cùng đồng hồ NTP.
- [ ] Một tên miền thử có quyền sửa NS và DS ở nhà đăng ký.
- [ ] Một máy chủ WHMCS thử (bản 8.x).

## 1. Cài đặt và cập nhật

- [ ] `install.sh` trên máy sạch chạy hết, không có cảnh báo đỏ.
- [ ] Cập nhật 1.0.6 → bản mới trên máy có dữ liệu: website vẫn chạy trong lúc cập nhật, cron cũ được chuyển sang `/etc/cron.d/spanel-user-*`, bản sao nằm ở `/var/backups/spanel/cron-spool-backup/`.
- [ ] `systemctl list-timers` có `spanel-watchdog.timer`, `spanel-maintenance.timer`, và chạy không lỗi qua 2 chu kỳ.
- [ ] Cố ý làm hỏng một bước (ví dụ để `nginx -t` lỗi): `spanel-update` tự rollback, khôi phục cả `spanel.db` (đúng revision Alembic) và các file systemd/nginx.

## 2. PHP-FPM theo từng tài khoản (rủi ro cao nhất)

- [ ] Mỗi tài khoản có `spanel-php<ver>-fpm@<user>.service` đang chạy; `systemd-cgls` cho thấy master và mọi worker nằm trong `user-<uid>.slice`.
- [ ] **Đăng nhập rồi thoát SSH/SFTP bằng chính user đó, vài lần:** PHP của site **không bị dừng** (systemd-logind không được dừng `user-<uid>.slice`). Nếu bị dừng thì phải đổi sang slice riêng trước khi phát hành.
- [ ] Tạo tải bằng `ab`/`wrk` lên một site: CPU và RAM bị giới hạn đúng theo gói ngay từ worker đầu tiên.
- [ ] nginx đọc được socket trong `/run/spanel-php/<user>/`; user khác không đọc được.
- [ ] Đổi phiên bản PHP của site, xoá site, xoá user, gỡ một phiên bản PHP: không còn pool hay unit mồ côi.
- [ ] Sau khi cập nhật từ 1.0.6: mọi site chuyển sang master riêng, pool cũ trong `/etc/php/*/fpm/pool.d/` bị dọn, không có downtime.

## 3. Quota đĩa và giới hạn MySQL

- [ ] `SPANEL_DISK_QUOTA=kernel` (hoặc nút trong Resource Monitor) thêm `usrjquota` vào fstab; sau khi remount, `quotaon -p /` báo bật, `repquota -a` thấy giới hạn block và inode của từng user.
- [ ] Ghi vượt quota bằng PHP, SFTP và cron đều bị chặn.
- [ ] `SHOW GRANTS` có `MAX_QUERIES_PER_HOUR` / `MAX_UPDATES_PER_HOUR` / `MAX_CONNECTIONS_PER_HOUR` theo gói, và MariaDB chặn thật khi vượt.

## 4. Cron, backup, DR

- [ ] Cron của khách: tạo, sửa, xoá, đổi PHP; lệnh có dấu cách, `%` và chuyển hướng chạy đúng trong jail, đúng thư mục.
- [ ] Backup theo lịch (local, S3, SFTP, FTP/FTPS) chạy; khi đặt `SPANEL_BACKUP_PASSPHRASE` thì file được mã hoá, và khôi phục được.
- [ ] Retention xoá bản cũ trên S3/SFTP mà không xoá bản full của chuỗi incremental đang giữ.
- [ ] Kiểm tra backup định kỳ báo lỗi khi cố ý làm hỏng một file.
- [ ] Panel self-backup lên đích ngoài máy; trên một VPS **mới**, `spanel dr-restore <file>` dựng lại panel, sau đó khôi phục toàn bộ tài khoản từ backup user.
- [ ] Khôi phục `spanel.db` từ hot backup trong trang Services.

## 5. Mail và DNS

- [ ] `exim4 -bV` và `doveconf -n` không lỗi sau `mail-reconfigure`.
- [ ] Autoresponder trả lời đúng khoảng ngày, không trả lời thư spam.
- [ ] Xoay DKIM: selector mới được công bố, ký bằng khoá mới sau khi DNS thấy, khoá cũ bị gỡ sau N ngày; thư gửi đi qua kiểm tra DKIM của Gmail suốt quá trình.
- [ ] `mail.<domain>` có chứng chỉ Let's Encrypt riêng; `openssl s_client -starttls smtp -servername mail.<domain>` thấy đúng chứng chỉ; IMAPS cũng vậy.
- [ ] MTA-STS (`https://mta-sts.<domain>/.well-known/mta-sts.txt`), TLS-RPT, autoconfig (Thunderbird) và autodiscover (Outlook) hoạt động với client thật.
- [ ] Hàng đợi mail: freeze, thaw, retry, delete; vượt giới hạn gửi thì thư bị freeze.
- [ ] Kéo thư vào hoặc ra khỏi Junk thì Rspamd học (xem `rspamc stat`); cảnh báo quota ở 80% và 95%.
- [ ] PowerDNS 4.8 chấp nhận các bản ghi TLSA, HTTPS, SVCB, DS, SSHFP, NAPTR, PTR; import và export file BIND; secondary nhận AXFR/NOTIFY.
- [ ] DNSSEC: bật, nhập DS ở nhà đăng ký, `dig +dnssec` và DNSViz báo xanh.

## 6. Cụm đa máy chủ và Remote MySQL

- [ ] Ghép node A và node B bằng token node mới; đặt pin fingerprint hoặc CA; request không chữ ký hoặc phát lại (replay) bị từ chối.
- [ ] Sửa DNS trên A → B nhận được; tắt B 10 phút rồi bật lại → outbox tự gửi bù; B **không** ghi đè zone của chính nó.
- [ ] Chuyển một tài khoản có website, DB và mail từ A sang B (có chuyển DNS và tạm khoá ở A); website chạy trên B; mật khẩu panel giữ nguyên.
- [ ] Node B tắt → cảnh báo "node down" một lần, có cảnh báo khi phục hồi.
- [ ] Remote MySQL qua TLS có CA: tạo DB, đổi mật khẩu, backup và khôi phục, tạm khoá và mở khoá, xoá; phpMyAdmin mở đúng server từ xa.

## 7. WHMCS và API

- [ ] Module trong WHMCS thật: Create, Suspend, Unsuspend, Terminate (có backup), ChangePassword, ChangePackage, UsageUpdate (có băng thông), SSO cho khách và admin, ListAccounts (import), các nút tuỳ chỉnh.
- [ ] Retry với cùng Idempotency-Key không tạo tài khoản thứ hai; tạo thất bại không để lại user Linux mồ côi.
- [ ] Webhook nhận đủ sự kiện `account.*`, chữ ký `X-SPanel-Signature-256` kiểm tra đúng; endpoint tắt 1 giờ rồi bật lại thì nhận bù.

## 8. Đo tải (benchmark)

- [ ] Dùng `k6` hoặc `wrk` trên cấu hình công bố sẵn (CPU, RAM, số site), ghi lại RPS, p95 và lỗi cho: WordPress có và không có FastCGI cache, PHP thuần, API panel. Lưu script và kết quả vào `docs/benchmarks/`.
