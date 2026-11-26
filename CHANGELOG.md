# Changelog — SPanel

Mọi thay đổi, bản vá bảo mật, tối ưu hiệu năng và tính năng mới của **SPanel** ([https://github.com/shostwp-byte/spanel](https://github.com/shostwp-byte/spanel)) được ghi nhận tại tài liệu này.

---

## [v1.0.12] — 2026-11-26

Bản vá `v1.0.12` đóng toàn bộ các phát hiện P2/P3 qua hai vòng Thẩm định Độc lập Lần 20 (`PR #35`: `N1`–`N7`) và Lần 21 (`PR #36`: `R1`–`R4`), đồng thời chuẩn hóa tài liệu README (`PR #34`):

### 🔒 Bảo mật, Khóa Đồng bộ & Cách ly Tài nguyên (`N1`, `N2`, `N5`, `R2`, `R4`)
- **Khóa theo site xuyên suốt mọi thao tác phá hủy Website & Xóa Tài khoản (`N1`, `R2`):** `_get_site_operation_status` kiểm tra trực tiếp `lock_live` độc lập với file trạng thái hoàn tất trước đó; các thao tác `_rename_single_website`, `delete_website`, `transfer_website_owner`, đổi `owner_id` trong `update_website` và `delete_user` (qua `ExitStack` theo thứ tự `Website.id.asc()`) đều giữ trực tiếp `site_operation_lock` trong suốt quá trình thực thi để triệt tiêu TOCTOU và chặn xóa tài khoản khi bất kỳ site nào còn đang chạy `npm install` / `git-deploy`.
- **Bảo toàn mức hạn chế chống tấn công MariaDB & phân bổ kết nối ưu tiên `localhost` (`N2`, `R4`):** `change_database_password` và `create_database_credentials(allow_existing_user=True)` bảo toàn mức hạn chế chống tấn công từ cả `resource_monitor.cached_user_attack_state` lẫn giới hạn thực tế theo từng host trong `mysql.global_priv` (ngăn xoay mật khẩu DB để thoát chế độ chống tấn công); phân bổ ngân sách `MAX_USER_CONNECTIONS` theo trọng số phù hợp lưu lượng thực tế (`~65%` cho Unix socket `localhost` phục vụ PHP-FPM/WordPress, `~25%` cho `127.0.0.1`, `~10%` cho `::1` — mặc định `25` → `17 / 6 / 2` tổng `25`, chống tấn công `8` → `5 / 2 / 1` tổng `8`), kiểm chứng trực tiếp trên MariaDB 10.11 thật trong CI.
- **Tạo sẵn leaf cgroup scope cho Tenant Jail (`N5`):** Tách `ensure_tenant_cgroup_scope` tạo sẵn lá `user-<uid>.slice/spanel.scope` và áp hạn mức từ `/etc/spanel/limits/<user>.env` trước khi vào `unshare` ở cả `run_in_tenant_home_jail`, `terminal-exec` và `classify_all_tenant_workers`, bỏ nhánh ghi vào slice không phải lá (`EBUSY`) và ghi cảnh báo qua `logger` khi thất bại.

### 🛠️ Độ tin cậy Rollback, Cập nhật & Tài liệu (`N3`, `N4`, `N6`, `N7`, `R1`, `R3`)
- **Đồng bộ bền vững DB khi rollback đổi chủ/chuyển chủ thất bại (`N4`, `R1`):** Chỉ khôi phục `root_path`/`linux_user`, ghi lại vhost và resync worker sau khi di chuyển ngược runtime thành công; nếu di chuyển ngược thất bại (hoặc site cũ thiếu `old_linux_user`), lưu đồng bộ cả `website.owner_id`, `root_path`, `linux_user` và `NodejsInstance.owner_id` xuống DB (`db.commit()`) trước khi trả lỗi nhằm tránh lệch quyền giữa hai tenant và lệch giữa DB với đĩa.
- **Dấu `.applied` tin cậy cho `user-limits-apply --if-changed` & Restart Node.js khi sửa `.bin` (`N6`, `R3`):** Chỉ bỏ qua với `unchanged=1` khi tồn tại stamp `.applied` khớp digest hiện tại (xóa trước khi áp và chỉ ghi sau khi hoàn tất); `prepare_nodejs_app_as_user` báo hiệu khi có sửa/xóa stub `.bin` hỏng để `sync_nodejs_worker_unit` vẫn restart app trong chế độ `if-changed`.
- **Cảnh báo gia hạn Let's Encrypt khi đổi tên miền & Dọn mã mặc định (`N3`, `N7`):** Trả cảnh báo rõ trong `warnings` và `X-SPanel-Warning` khi đổi tên miền kế thừa chứng chỉ Let's Encrypt từ tên miền cũ; dẫn xuất `_DEFAULT_USER_RESOURCE_LIMITS` trực tiếp từ cột mặc định trên model `User`.

---

## [v1.0.11] — 2026-11-20

Bản phát hành `v1.0.11` chính thức kích hoạt **Node.js Manager** mặc định (`NODEJS_MANAGER_ENABLED=true`) sau khi hoàn tất toàn bộ hạng mục nhóm P2 của Thẩm định Lần 17 (`PR #31`) và khép kín 15/15 phát hiện bảo mật, độ tin cậy, cách ly đa người thuê, vận hành không gián đoạn và kiểm thử tích hợp MariaDB thật của Thẩm định Độc lập Lần 18 (`PR #32`):

### 🔒 Gia cố & Khép kín Toàn bộ Phát hiện Thẩm định Lần 18 (`V1..V6`, `M1..M8`)
- **MariaDB Loopback `ACCOUNT LOCK` nguyên tử, triệt tiêu `ERROR 1141` & CI MariaDB thật (`V1`, `P1-3`):** Đưa `ACCOUNT LOCK` trực tiếp vào `CREATE USER IF NOT EXISTS` và trước mọi `GRANT`/`REVOKE`; chèn `GRANT` trước `REVOKE ALL PRIVILEGES ON db.*` để các host loopback mới sinh (`127.0.0.1`, `::1`) trên máy nâng cấp từ `1.0.9` không bị lỗi `ERROR 1141`; chia trần `MAX_USER_CONNECTIONS` hợp lý trên 3 host loopback (`10`/host thay vì `25`/host); bổ sung job CI `MariaDB live integration` chạy SQL thật với `skip-name-resolve=1`.
- **Bảo toàn khóa ghi, tạm ngưng và giới hạn truy vấn khi Restore Backup (`V5`):** Luồng khôi phục cơ sở dữ liệu trong `backup.py` chỉ mở quyền tạm thời trong bước nạp dump SQL và luôn khôi phục lại `write_locked`, `ACCOUNT LOCK` và `hourly_limits` trong khối `finally` (kể cả khi import thất bại).
- **Khóa đồng bộ toàn diện Node.js Manager, chống hồi sinh dòng đã xóa & Polling Backoff (`M1`, `M5`, `M6`, `M7`):** Mọi endpoint ghi của Node.js Manager (`install`, `process`, `npm`, `env`, `security`, `permissions-policy`, `git-deploy`, `webhook`, `logs`, `detach`, `remove`) cùng các thao tác xóa/đổi tên/chuyển chủ website đều kiểm tra khóa theo site trước mọi thao tác ghi (kể cả trước ghi `.env`); `_update_instance_state` mặc định không tự tạo lại dòng đã bị gỡ; thu hồi khóa mồ côi nguyên tử qua `.reclaim` mutex; giải phóng khóa khi có ngoại lệ trước khi bàn giao `BackgroundTasks`; giao diện `NodejsManager.jsx` khóa toàn bộ nút ghi khi `active_operation.active` và tự động retry polling theo backoff luỹ tiến khi đứt mạng thoáng qua.
- **Kiểm trần tài nguyên Reseller khi tạo khách & thu hẹp phạm vi kiểm tra khi sửa lẻ (`M2`, `M3`):** Áp dụng `_enforce_reseller_resource_ceilings` (bao gồm cả 3 trường `mysql_max_*_per_hour`) trước bước tạo Linux user ở `users.py:create_user` và WHMCS `provisioning.py` (`create_account` / `change_package`), kể cả với gói chung của Admin; khi Reseller chỉ cập nhật thông tin lẻ trên khách cũ không đổi gói thì chỉ kiểm tra các trường trần có trong payload.
- **Đưa `site-app-install-deps` / `site-app-python-deps` / `site-app-git-pull` vào `/home` Jail + Cgroup Slice & tự sửa stub `.bin` hỏng (`V2`, `V6`):** Hợp nhất cơ chế `unshare` + `tmpfs /home` + `timeout --kill-after=10` + gán cgroup `user-<uid>.slice` (khi cgroup v2 sẵn sàng) vào hàm dùng chung `run_in_tenant_home_jail` trong `spanel-helper.sh`; phát hiện stub `node_modules/.bin/*` bị đứt đích từ tệp `.zip` thiếu gói để tự động chạy `npm install`.
- **Cập nhật Zero-Downtime trong `update.sh` (`V3`):** Bổ sung chế độ `if-changed` (`cmp -s`) cho `nodejs-worker-sync`, `laravel-worker-sync` và `user-limits-apply` để tránh restart hàng loạt ứng dụng và `daemon-reload` thừa khi cấu hình không đổi.
- **Bảo toàn SSL Let's Encrypt qua Helper khi đổi tên & hoàn thiện Rollback đổi chủ (`V4`, `M4`):** Kiểm tra SAN chứng chỉ hiện có cho cả mode `letsencrypt` và tra cứu cert tên miền mới qua `ssl.cert_info` thay vì đọc trực tiếp thư mục root-only `/etc/letsencrypt/live`; bổ sung rollback đầy đủ (dời ngược runtime, khôi phục vhost/WAF, resync worker khi dời ngược thành công) cho nhánh đổi chủ trong `update_website` và hiển thị cảnh báo rollback trong thông điệp lỗi.
- **Đồng bộ tài liệu cờ `NODEJS_MANAGER_ENABLED=true` mặc định (`M8`):** Cập nhật `README.md` và `CHANGELOG.md` khớp hoàn toàn với mặc định `nodejs_manager_enabled: bool = True` trên cả máy cài mới lẫn máy cập nhật (vẫn giữ 2 lớp cổng: addon `Application` và hạn mức gói `node_apps_limit = 0` mặc định, kèm hướng dẫn tắt khẩn cấp `NODEJS_MANAGER_ENABLED=false`).

### 🚀 Kích hoạt chính thức Node.js Manager & Hoàn thiện Nhóm P2 (`Round 17 P2-1..P2-5`)
- **Mặc định bật `nodejs_manager_enabled = True`:** Sau khi khép kín toàn bộ các hạng mục bảo mật và vận hành P1–P2 của đợt thẩm định Lần 17, tính năng **Node.js Manager** được bật mặc định trên `main` (vẫn giữ cơ chế công tắc ngắt khẩn cấp `NODEJS_MANAGER_ENABLED=false`).
- **Chặn thao tác trên Website chưa khởi tạo hoặc đã gỡ Node.js Manager (`P2-1`):** Toàn bộ nhóm endpoint `/api/nodejs-manager/instances/{website_id}/*` kiểm tra bắt buộc sự tồn tại của bản ghi `NodejsInstance` đang hoạt động (`detached == False`), trả `404` rõ ràng nếu chưa khởi tạo.
- **Chạy nền bất đồng bộ (`202 Accepted`) cho `process` & `git-deploy` kèm theo dõi trạng thái trực tiếp (`P2-2`):** Chuyển các tác vụ `npm install` / `npm run build` / `git-deploy` sang chạy nền với khóa theo site, bổ sung endpoint `GET /api/nodejs-manager/instances/{website_id}/operation-status` và banner tiến độ thời gian thực trên giao diện `NodejsManager.jsx` để tránh lỗi `504 Gateway Timeout`.
- **Khóa triển khai nhận diện PID sống/chết & chuẩn hóa TTL (`P2-4`):** Giảm timeout `nodejs-worker-sync` về `60s`, nâng `_DEPLOY_LOCK_TTL_SECONDS` lên `2400s` khớp trần thực thi mới, và tự động thu hồi khóa mồ côi ngay lập tức nếu tiến trình giữ khóa đã kết thúc (`_pid_is_alive`).
- **Giữ nguyên chứng chỉ SSL hợp lệ khi đổi tên miền & khôi phục WAF an toàn khi rollback (`P2-3`):** Bảo toàn trạng thái HTTPS của website và các alias giữ nguyên khi chứng chỉ Wildcard / Shared / Manual hoặc Let's Encrypt sẵn có bao phủ tên miền mới; tránh xóa nhầm chứng chỉ còn bao phủ alias của chính site trong `release_site_certificates`; đồng bộ lại file WAF của domain cũ trước khi ghi lại vhost cũ và dọn sạch vhost/WAF của domain mới khi rollback.
- **Khóa chặt hạn mức Pool & trần tài nguyên của Reseller trên mọi đường cập nhật (`P2-5`):** Áp dụng thống nhất kiểm tra quyền sở hữu gói (`_package_for_payload`), tổng dung lượng/website/database của Reseller (`_enforce_reseller_quotas`, chặn giá trị `<= 0` không giới hạn khi tắt overselling) và trần cgroup/MySQL (`_enforce_reseller_resource_ceilings`) trên `POST /api/resources/users/{id}/limits`, `PATCH /api/users/{id}` và `POST`/`PATCH /api/packages`.

---

## [v1.0.10] — 2026-11-18

Bản phát hành `v1.0.10` hợp nhất toàn bộ các bản vá bảo mật cách ly đa người thuê, quản lý quyền MariaDB loopback, khôi phục worker khi đổi tên/chuyển chủ website, chuẩn hóa header `Permissions-Policy` theo từng site và hoàn tất đầy đủ 8 hạng mục P1 qua các đợt thẩm định độc lập Lần 15 → Lần 17 (`PR #27`, `PR #28`, `PR #29`):

> **Lưu ý phát hành:** Mã nguồn **Node.js Manager** đã được hợp nhất vào `main` kèm đầy đủ cơ chế cô lập `systemd` và chốt kiểm thử tích hợp CI, nhưng cờ cấu hình `nodejs_manager_enabled` vẫn giữ mặc định **`False`** (tắt trên môi trường sản xuất) cho tới khi hoàn tất các hạng mục hoàn thiện trải nghiệm nhóm P2.

### 🔒 Bảo mật & Cách ly Đa Người thuê (`Round 16` & `Round 17 P1-3..P1-6`)
- **Ẩn hoàn toàn `/home` của khách khác trên mọi systemd unit ứng dụng (`P1-6`):** Thay `ProtectHome=read-only` bằng `ProtectHome=tmpfs` kết hợp `BindReadOnlyPaths=/home/<user>` trên cả 4 trình sinh unit (`sync_laravel_worker_unit`, `sync_nodejs_worker_unit`, `write_node_app_unit`, `write_python_app_unit`), đồng thời tự động nâng cấp các unit cũ khi chạy `update.sh` và kiểm chứng trực tiếp trên Ubuntu runner của CI (`own=true`, `own_write=true`, `other=false`).
- **Chấm dứt chạy `npm install` / `npm run build` ngoài jail trong `spanel-helper` (`P1-5`):** Thu gọn `prepare_nodejs_app_as_user` chỉ sửa stub symlink trong `node_modules/.bin` và bit thực thi dưới quyền user của site; toàn bộ lifecycle/build scripts của khách chỉ chạy qua `terminal-exec` (có mount-namespace jail `/home` và `timeout --kill-after`, bổ sung gán cgroup `user-<uid>.slice` ở vòng 18).
- **Đồng bộ khóa tạm ngưng (`ACCOUNT LOCK`), khóa ghi vượt quota (`write_locked`) và giới hạn truy vấn theo giờ trên cả 3 host MariaDB (`P1-3`):** Cấp phát tài khoản MariaDB cục bộ trên `localhost`, `127.0.0.1` và `::1` cho các kết nối TCP loopback (dưới `skip_name_resolve = 1`), đồng thời cưỡng chế đồng nhất `ACCOUNT LOCK`, bộ quyền chỉ-đọc/dọn-dẹp khi vượt quota và `MAX_*_PER_HOUR` trên cả 3 host kể cả sau khi khởi động lại panel, đổi mật khẩu DB hoặc đăng nhập phpMyAdmin SSO.
- **Chặn tuyệt đối việc lấy mật khẩu từ `.env` của khách để `ALTER USER` MariaDB (`P1-4`):** `_ensure_nodejs_local_db_grants` chỉ đồng bộ các bản ghi `DatabaseAccount` thuộc chính chủ sở hữu website bằng mật khẩu đã lưu trong panel, không bao giờ đụng tới tài khoản MariaDB nằm ngoài sự quản lý của panel.

### 🌐 Website, Đổi Tên miền, Quota & Header `Permissions-Policy` (`P1-2`, `P1-7`, `P1-8`)
- **Đổi tên miền dời thư mục runtime & khôi phục tự động Worker (`42f4559`, `P1-2`, `P1-7`):** Khi đổi tên miền hoặc chuyển chủ sở hữu website, hệ thống dời thư mục site an toàn, cập nhật `NodejsInstance.owner_id`, tự động dựng lại cả systemd unit Node.js và Laravel Queue Worker tại đường dẫn mới (hoặc đường dẫn cũ khi rollback), trả cảnh báo rõ ràng trong HTTP response (`warnings` / `X-SPanel-Warning`) nếu có sự cố, và chỉ đổi tên các subdomain con khi người dùng chủ động bật tùy chọn `rename_children`. Sửa lỗi migration `migrate_repair_nodejs_worker_units` trong `update.sh` để đồng bộ thực sự các unit đang chạy.
- **Quản lý `Permissions-Policy` theo từng Website & chống xung đột Header (`b147eb5`):** Bổ sung cấu hình `Permissions-Policy` bền vững theo từng website (UI + API + Custom Nginx). Với các ứng dụng reverse proxy (`proxy.conf.j2`), Nginx ẩn header gốc (`proxy_hide_header Permissions-Policy`) và tự động hợp nhất chính sách upstream (như `microphone=(self)` từ Next.js) vào một header duy nhất; với các vhost PHP/WordPress (`fastcgi_hide_header Permissions-Policy`), chính sách do SPanel quản lý tại tầng vhost/UI sẽ được ưu tiên áp dụng thay cho header phát sinh từ plugin PHP nhằm tránh trình duyệt nhận 2 header xung đột.
- **Chuẩn hóa hạn mức lưu trữ & nâng `TasksMax` đúng một lần (`42f4559`, `a263c4a`, `P1-8`):** Quy ước thống nhất `storage_limit_mb <= 0` là không giới hạn; chuyển việc nâng mặc định `tasks_max` cũ (`80`/`512` → `2048`) sang migration Alembic chạy một lần `tasks_max_2048_once` và gỡ bỏ mọi vòng lặp ghi đè định kỳ khi khởi động hoặc cập nhật để bảo toàn tuyệt đối cấu hình `tasks_max` do Admin chỉ định.

---

## [v1.0.9] — 2026-11-11

Bản phát hành `v1.0.9` tập trung vào độ ổn định của Kernel Disk Quota sau khi khởi động lại máy chủ, hiệu năng bộ nhớ đệm của WordPress Manager, đồng bộ chủ sở hữu website khi đối chiếu đĩa (`fix-round7-p1`), và quy tắc bảo vệ ranh giới cơ sở dữ liệu / systemd trên CI:

> **Lưu ý phát hành:** Tính năng **Node.js Manager** đang trong quá trình hoàn thiện và thẩm định bảo mật độc lập nên được tách riêng sang nhánh phát triển `feat/nodejs-manager` (mặc định tắt sau cờ cấu hình), không đưa vào bản phát hành `v1.0.9` trên `main`. Tag `v1.0.9` được tạo lại sau khi hợp nhất PR vào `main`.

### 🔒 Bảo mật & Kiểm soát Ranh giới Tự động
- **Chỉ khôi phục bản ghi bị mất trong `_reconcile_websites_from_disk` (`fix-round7-p1`):** Luồng đối chiếu đĩa chỉ thêm lại các website bị mất dòng trong SQLite, tuyệt đối không ghi đè `owner_id`, `linux_user`, `root_path` của các website đã có chủ sở hữu; đồng bộ chủ sở hữu chỉ thực hiện qua hành động quản trị tường minh (`move-owner`).
- **Chặn tạo trùng hoặc chiếm quyền cơ sở dữ liệu trong `mariadb.create_database_credentials`:** Mặc định từ chối cơ sở dữ liệu đã tồn tại trên MariaDB (`database_exists`) trừ luồng khôi phục sao lưu (`allow_existing_user=True` / `allow_existing_db=True`).
- **Quy tắc CI tự động (`Security boundary guard`):** Chặn mọi lệnh gọi trực tiếp `mariadb.drop_database` / `create_database_credentials` trong `backend/app/api/` ngoài `databases.py` và chặn `EnvironmentFile=` trong `installer/` trỏ vào thư mục khách (`/home`, `app_target`, `site_root`).

### 📦 Kernel Disk Quota & Hiệu năng WordPress Manager
- **Tự động kích hoạt lại Kernel Quota sau khi reboot (`8fb6b75` + Mục 4):** Tự động chạy `quotaon` / `quotacheck` qua `systemd-run -p PrivateDevices=no` khi khởi động nếu phân vùng đã gắn tùy chọn quota nhưng trạng thái kernel quota đang tắt; giới hạn việc tự động cài gói `linux-modules-extra-$(uname -r)` tối đa **1 lần trên mỗi phiên bản kernel** (`quota-kmod-attempted-<uname -r>`).
- **Bộ nhớ đệm WordPress Manager (`496bcfd`):** Lưu đệm danh sách WordPress instance theo tài khoản khi chuyển trang trong phiên làm việc và bỏ các lượt quét `wp-cli` lặp lại không cần thiết, giúp mở lại WordPress Manager tức thì trên tài khoản có nhiều website.

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
