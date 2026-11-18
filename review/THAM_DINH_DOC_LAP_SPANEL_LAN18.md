# Thẩm định SPanel — Lần 18: tag `v1.0.10` và `main` (PR #29, #30, #31)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Phạm vi:**
  - **`v1.0.10`** (tag ở `690eb8a`): `b147eb5..v1.0.10`. Gồm 8 commit sửa P1-1..P1-8 của lần 17 (PR #29) và commit phát hành (PR #30). 26 file, +1.231 / −319.
  - **`main`** (`d7d1491`): `v1.0.10..main`. Gồm 5 commit sửa P2-1..P2-5 và `105d3e6` bật Node.js Manager mặc định (PR #31). 13 file, +1.592 / −64.
- **Quy trình:** lần này mọi thay đổi đều đi qua PR. CI xanh trên cả `v1.0.10` (run `44223149090`) và `main` (run `44236870210`). Không có force-push. Nhánh `fix/round17-p1` (14/11) đã được viết lại thành `fix/round17`, merge đủ 8 commit, không mất gì.
- **Kiểm chứng:**
  - Đọc toàn bộ diff backend, helper, `update.sh`, migration và test.
  - Đọc phần frontend liên quan: `NodejsManager.jsx`, `Websites.jsx`.
  - `bash -n` cho helper và `update.sh`. Kiểm alembic: chỉ có một head.
  - Chưa chạy pytest tại máy (thiếu Python). Chưa thử trên VPS. N1 là suy từ hành vi MariaDB, cần thử thực tế.
- **Ngày lập:** 18/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**`v1.0.10` đóng được phần lớn P1 của lần 17.** P1-1, P1-2, P1-4, P1-6, P1-7 và P1-8 đạt. P1-3 và P1-5 đạt một phần. Các bản sửa làm đúng hướng: gọi đúng hàm, dùng một hàm chung lặp qua 3 host, đưa npm vào jail, `ProtectHome=tmpfs`, migration chạy một lần. Có test hành vi, và CI helper-integration có kiểm cross-tenant.

**`main` đóng P2-1 và P2-4, còn P2-2, P2-3, P2-5 mới đạt một phần.** Sau đó `105d3e6` bật Node.js Manager **mặc định trên mọi máy đang chạy khi cập nhật**. Theo tôi việc này còn sớm vì hai lý do:

- Còn lỗi race ở phát hiện **M1**: gỡ hoặc dừng ứng dụng trong lúc việc nền đang chạy sẽ để lại unit mồ côi và site thành proxy.
- README vẫn ghi cờ "mặc định tắt".

**Cần sửa trước khi gắn tag `v1.0.11`:**

1. **V1 (P2):** tạo host loopback cho DB đang bị khoá ghi làm script SQL dừng giữa chừng. Host `127.0.0.1` khi đó có mật khẩu nhưng **chưa bị `ACCOUNT LOCK`** và không có giới hạn kết nối.
2. **M1 (P2):** các thao tác ghi khác của Node.js Manager không lấy khoá của việc nền.
3. **M2 (P2):** reseller tạo khách vẫn không bị kiểm trần tài nguyên (P2-5 chưa kín).
4. **M8:** cân nhắc lại việc bật cờ mặc định, hoặc ít nhất ghi rõ trong CHANGELOG và README.

---

## 1. Đối chiếu P1 lần 17 (tag `v1.0.10`)

| Mục | Kết luận | Ghi chú |
|---|---|---|
| P1-1 CI đỏ | **Đạt** | Test round 7 có stub hàm xoá unit, kiểm thêm thứ tự xoá và trường hợp từ chối. Test php_fpm tìm đúng chuỗi mới |
| P1-2 Đổi tên làm mất worker | **Đạt** | `_resync_site_workers` (`websites.py:1017`) gọi `_sync_nodejs_worker_unit` và `_sync_laravel_worker_unit`, chữ ký khớp. Được gọi khi đổi tên, rollback, đổi chủ và transfer. Lỗi trả về qua `warnings`/`X-SPanel-Warning`. Site con chỉ đổi theo khi `rename_children=true` (mặc định false). Xem V4 |
| P1-3 MariaDB trên 3 host | **Đạt một phần** | Lock, hourly limit, khoá ghi, đổi mật khẩu và SSO đều lặp qua 3 host. `update.sh` đã bỏ `GRANT ALL`. **Còn thiếu:** `MAX_USER_CONNECTIONS`/`MAX_*_PER_HOUR` vẫn tính riêng cho từng `user@host`, nên trần thực tế gấp 3. Có lỗi mới V1. Restore backup vẫn `GRANT ALL` (V5) |
| P1-4 `.env` đặt lại mật khẩu | **Đạt** | `.env` chỉ dùng để chọn `DatabaseAccount` cùng chủ. Luôn dùng mật khẩu panel đã lưu. Nhánh bypass cho admin đã bỏ |
| P1-5 npm ngoài jail | **Đạt phần chính** | Helper không còn chạy npm. npm chạy qua `terminal-exec`: có `unshare`, tmpfs `/home`, `timeout --kill-after`. Nhưng lúc spawn tiến trình vẫn nằm trong cgroup `spanel-api.service`, chưa vào `user-<uid>.slice`, nên CHANGELOG nói quá. Còn `site-app-install-deps` (V2) |
| P1-6 Unit đọc `/home` khách khác | **Đạt** | `ProtectHome=tmpfs` + `BindReadOnlyPaths=/home/<user>` ở cả 4 loại unit. CI kiểm thật với unit Node; Laravel/app chỉ có test chuỗi |
| P1-7 Migration unit không chạy | **Đạt** | Lọc theo cột thật, có test chạy heredoc trên SQLite. Chạy lại không hỏng. Xem V3 |
| P1-8 `tasks_max` bị ghi đè | **Đạt** | `database.py` bỏ 2 câu UPDATE. Migration `tasks_max_2048_once` chỉ đụng `IN (80, 512)`, nối đúng sau `website_permissions_policy`, một head |
| P3-5 VERSION/CHANGELOG | **Đạt** | |

## 2. Đối chiếu P2 lần 17 (`main`)

| Mục | Kết luận | Ghi chú |
|---|---|---|
| P2-1 Endpoint khi chưa có instance | **Đạt** | `_require_active_nodejs_instance` ở mọi endpoint instance. Có test qua TestClient |
| P2-2 Chạy nền + 202 + hỏi trạng thái | **Đạt một phần** | Việc nền chạy qua `BackgroundTasks` với `SessionLocal()` riêng. Trạng thái lưu ra file, nên mọi worker đều đọc được. Khoá được trả trong `finally`. Endpoint trạng thái có kiểm quyền. **Thiếu:** các thao tác khác không lấy khoá (M1) |
| P2-3 Đổi tên: SSL, WAF | **Đạt một phần** | Rollback khôi phục WAF đúng thứ tự, có test. Nhưng cert Let's Encrypt (mode mặc định) không được giữ (M4) |
| P2-4 Khoá deploy theo PID | **Đạt** | Khoá ghi `{pid, started_at}`, TTL 2.400 s. Trần pipeline tính được khoảng 1.300 s. Rủi ro nhỏ ở M6 |
| P2-5 Quota reseller | **Đạt một phần** | `/limits` và gói đã kiểm pool và trần. `create_user` chưa kiểm (M2) |

---

## 3. Phát hiện mới trong `v1.0.10`

### V1 — P2. Tạo host loopback cho DB đang khoá ghi làm SQL dừng giữa chừng: host mới không bị LOCK, không có giới hạn

**Vị trí:** `mariadb.py:97-109` (`_grant_statements`) và vòng lặp trong `create_database_credentials` (~dòng 275-283).

**Nguyên nhân:**
- Với `write_locked=True`, câu đầu tiên là `REVOKE ALL PRIVILEGES ON db.* FROM u@host`.
- Nếu host đó vừa được `CREATE USER IF NOT EXISTS` tạo ra, user chưa có grant nào trên `db.*`, nên MariaDB trả **ERROR 1141** ("There is no such grant defined").
- `_run_sql` gọi `mysql` không có `--force` và `check=True`, nên script dừng tại đó.
- `ALTER USER … ACCOUNT LOCK` đứng **sau** câu REVOKE, nên không bao giờ chạy.

**Kịch bản:** máy cập nhật từ 1.0.9. Một khách chỉ có `u@localhost`, đang bị khoá ghi vì vượt quota và đang bị tạm ngưng. Lần đầu `sync_local_loopback_accounts` chạy:
1. Host `localhost` xử lý xong.
2. Tạo `u@'127.0.0.1'` có mật khẩu và **không bị khoá**, rồi REVOKE lỗi, script dừng.
3. `::1` không được tạo. `enforce_db_user_resource_limits` không chạy.

**Hệ quả:**
- Khách đang bị tạm ngưng vẫn đăng nhập MariaDB được qua TCP và mở kết nối không giới hạn. Chỉ có quyền USAGE, nhưng đó vẫn là đúng loại lỗ mà P1-3 định đóng.
- Khách không bị tạm ngưng: app Node.js kết nối qua `127.0.0.1` mất luôn quyền SELECT. Site chết hẳn thay vì chỉ ở chế độ đọc.
- `change_database_password` đi qua cùng đường nên cũng dính.

**Sửa:**
- Đưa `ACCOUNT LOCK` lên ngay sau `CREATE USER`.
- Với host mới: `GRANT <quyền khoá ghi>` trực tiếp, không REVOKE trước. Hoặc dùng `REVOKE ALL PRIVILEGES, GRANT OPTION FROM u@host`, dạng toàn cục không lỗi khi user chưa có grant.
- Thêm một test chạy SQL thật trên MariaDB trong CI. Mọi test round 17 hiện đều mock `_run_sql`, nên không bắt được lỗi này.

### V2 — P2 (có từ trước, cùng loại P1-5). `site-app-install-deps` / `site-app-python-deps` vẫn chạy mã của khách ngoài jail

Helper (~dòng 13778) chạy `timeout 900 runuser … npm install`, và tương tự với `pip install`. Lifecycle script hoặc `setup.py` của khách vì vậy chạy:
- không có `unshare`/tmpfs `/home`, nên đọc được file 0644 của khách khác;
- không trong slice của khách;
- `timeout` không có `--kill-after`.

**Sửa:** dùng cùng đường `terminal-exec` như Node.js Manager, hoặc `systemd-run --wait --uid=… --slice=user-<uid>.slice -p TemporaryFileSystem=/home -p BindPaths=/home/<user> -p RuntimeMaxSec=…`.

### V3 — P3. Mỗi lần `update.sh` đều restart mọi app Node.js / Laravel worker và chạy lại limits cho từng user

- `migrate_repair_nodejs_worker_units` gọi `nodejs-worker-sync`, và helper luôn `systemctl restart` dù unit không đổi. Phần Laravel cũng vậy.
- `migrate_sync_user_slice_limits_from_db` chạy `daemon-reload`, reload pool PHP và quét mọi tiến trình cho **từng** user.

**Hệ quả:** mỗi lần cập nhật đều có downtime ngắn cho toàn bộ ứng dụng, và thời gian cập nhật tăng theo số user.

**Sửa:** chỉ restart khi nội dung unit thay đổi (`cmp` trước `mv`), hoặc đặt marker để mỗi migration chỉ chạy một lần.

### V4 — P3. Các nhánh lỗi của đổi tên / đổi chủ

- **Rollback không báo kết quả resync:** `websites.py:1215` (và 2351) bỏ qua kết quả `_resync_site_workers`. Nếu bước dời thư mục ngược bị lỗi, resync vẫn chạy trên đường dẫn không tồn tại.
- **Đổi chủ không rollback:** khi đổi chủ trong `update_website` (1405-1430), nếu `move_site_runtime` đã chạy mà WAF hoặc vhost lỗi sau đó, nhánh `except` chỉ trả 400. Không dời thư mục ngược, không resync, nên Node/Laravel dừng hẳn.

**Sửa:** thêm rollback + resync như ở `_rename_single_website`, và đưa cảnh báo vào `detail` của lỗi.

### V5 — P2/P3. Restore backup vẫn `GRANT ALL`, bỏ qua khoá ghi và tạm ngưng

**Vị trí:** `backup.py:1599`, `1671` gọi `create_database_credentials(..., allow_existing_user=True)` với `write_locked` và `account_locked` mặc định False.

**Hệ quả:** DB đang khoá ghi mất khoá trong MariaDB. `resource_monitor` chỉ khoá khi trạng thái chuyển, nên không bao giờ khoá lại, trong khi giao diện vẫn báo "đang khoá".

**Sửa:** dùng `ensure_local_database_user_hosts` (tự đọc trạng thái panel), hoặc truyền `account_state(item)`.

### V6 — P3. `prepare_nodejs_app_as_user`: stub `.bin` hỏng bị xoá mà không cài lại

Phía Python, `_ensure_nodejs_app_ready` thấy `.bin/next` tồn tại (là file thường do giải nén zip) nên bỏ qua bước cài. Helper sau đó `rm` stub này.

**Hệ quả:** app giải nén từ zip thiếu gói sẽ lỗi `next: not found`.

**Sửa:** phía Python kiểm stub (dòng đầu bắt đầu bằng `../`) trước khi quyết định có cần `npm install` hay không.

---

## 4. Phát hiện mới trong `main` (PR #31)

### M1 — P2. Thao tác khác chạy song song với việc nền làm hỏng trạng thái

**Vị trí:**
- `nodejs_manager.py:3268`: chỉ nhánh start/restart lấy khoá.
- `remove_nodejs_instance` (4308), `run_npm_action` (3411), stop, env, detach đều không kiểm khoá.
- `_update_instance_state` (383-410) **tự tạo lại** dòng `NodejsInstance` nếu dòng đó đã bị xoá.

**Kịch bản:**
1. Khách bấm Start. Việc nền đang `npm install`.
2. Khách bấm "Gỡ cài đặt". Nút này không bị vô hiệu, vì frontend chỉ khoá nút start/restart.
3. Remove xoá unit, khôi phục vhost PHP, xoá state.
4. Việc nền chạy tiếp: tạo lại unit, đặt `app_type="application"`, ghi vhost proxy.
5. Kết quả: unit mồ côi chạy mãi và site thành proxy, trong khi panel báo đã gỡ.

**Biến thể:**
- Stop trong lúc Restart: DB lưu `process_enabled=False`, nhưng tiến trình vẫn chạy lại.
- `npm install` chạy song song với git-deploy trong cùng thư mục.

**Sửa:**
- Mọi endpoint ghi (stop/configure, npm, env PUT, security, permissions-policy, webhook, detach, remove) lấy cùng khoá theo site, hoặc trả 409 khi `_get_site_operation_status(...).active`.
- Frontend vô hiệu mọi nút khi `active_operation.active`.

### M2 — P2. Reseller tạo khách vẫn không bị kiểm trần tài nguyên

`create_user` (`users.py:328`) chỉ gọi `_enforce_reseller_quotas`, không gọi `_enforce_reseller_resource_ceilings`. Hàm này hiện chỉ được gọi ở `update_user` (745). Đồng thời `_package_for_payload` cho reseller gán gói chung (`owner_id=None`).

**Kịch bản:** admin có gói chung với `memory_max_mb=0` (không giới hạn), `cpu_quota_percent=400`. Reseller tạo khách với gói này, và khách nhận nguyên các giá trị đó.

**Sửa:** gọi `_enforce_reseller_resource_ceilings` trong `create_user` sau `_apply_package_limits`, và thêm test.

### M3 — P3. `update_user` và `/limits` kiểm trần trên mọi trường, kể cả trường không đổi

**Kịch bản:** khách cũ có `memory_max_mb=4096` trong khi trần của reseller là 2048. Reseller chỉ đổi email hoặc bật terminal cũng nhận 403.

**Sửa:** chỉ kiểm các trường có trong payload, hoặc chỉ khi giá trị mới lớn hơn giá trị cũ.

### M4 — P3. Giữ SSL khi đổi tên không áp dụng cho Let's Encrypt; nhánh "cert LE cho tên mới" là code chết

- `websites.py:1141` loại trừ mode `letsencrypt`, là mode mặc định và phổ biến nhất.
- `websites.py:1163-1168` kiểm `/etc/letsencrypt/live/<new>` trực tiếp. Panel chạy bằng user `spanel` nên không đọc được thư mục root-only này; lỗi bị nuốt, nên `has_new_le` luôn False.

**Hệ quả:** đổi tên site LE vẫn tắt SSL của site và mọi alias.

**Sửa:** kiểm qua helper bằng `ssl.cert_info(...)`, giống các mode khác.

### M5 — P3. Ghi `.env` trước khi lấy khoá

`nodejs_manager.py:3261-3264` ghi `PORT`/`NODE_ENV` vào `.env` trước `_acquire_deploy_lock` (3271).

**Kịch bản:**
1. Đang deploy. Khách đổi cổng rồi bấm Restart, nhận 409, nhưng `.env` đã mang cổng mới.
2. Unit có `EnvironmentFile=.env`, mà EnvironmentFile ghi đè `Environment=PORT`. Lần sync kế, app nghe cổng mới trong khi Nginx vẫn proxy cổng cũ, nên site trả 502.

**Sửa:** lấy khoá trước mọi thao tác ghi.

### M6 — P3. Rủi ro nhỏ của khoá

- **Lấy lại khoá stale không nguyên tử** (2119-2123: đọc → unlink → tạo lại). Hai request cùng thấy PID chết thì cả hai đều có thể giữ khoá.
- **Khoá có thể kẹt.** Nếu `_inspect_nodejs_instance` ném lỗi sau khi đã lấy khoá, khoá giữ PID của worker vẫn sống tới hết TTL 40 phút, và UI báo "Đang chạy nền". Nên bọc từ sau khi lấy khoá tới `return` trong try/except để trả khoá.
- **Mỗi job dài giữ một luồng anyio** (mặc định 40 luồng). Nhiều khách deploy cùng lúc có thể làm panel chậm.

### M7 — P3. Frontend ngừng hỏi trạng thái sau một lỗi mạng

`NodejsManager.jsx:447`: `catch` xoá timer, và `useEffect` không khởi động lại việc hỏi. Banner "Đang chạy nền" kẹt tới khi tải lại trang. Nên retry có backoff thay vì dừng hẳn.

### M8 — Bật Node.js Manager mặc định (`105d3e6`)

- `config.py:56` đổi mặc định thành `True`. Không installer hay updater nào ghi `NODEJS_MANAGER_ENABLED` vào `.env`. Vì vậy **mọi máy đang chạy sẽ tự bật Node.js Manager ngay khi cập nhật**, nếu đã có addon `applications`.
- `README.md:133` vẫn ghi "mặc định ở trạng thái tắt".
- **Đề xuất:** giữ mặc định tắt cho tới khi xong M1 và V1. Nếu vẫn muốn bật, thì:
  - ghi rõ trong CHANGELOG bản kế;
  - sửa README;
  - cho `update.sh` giữ trạng thái cũ, ví dụ ghi `NODEJS_MANAGER_ENABLED=false` nếu máy chưa từng bật.

---

## 5. Đề xuất

1. **Một PR "round 18"**, mỗi mục một commit có test:
   - **V1:** sửa thứ tự `CREATE` → `LOCK` → `GRANT`; thêm job CI chạy SQL thật trên MariaDB.
   - **M1:** dùng một khoá theo site cho mọi thao tác ghi; frontend khoá mọi nút.
   - **M2:** kiểm trần trong `create_user`.
   - **V5:** restore backup giữ trạng thái khoá.
   - **M8:** quyết định lại cờ mặc định và sửa README.
2. **Sau đó:** V2 (đưa cài phụ thuộc site-app vào jail), V3 (không restart khi update), V4, M3..M7.
3. **Trần kết nối MariaDB gấp 3** (còn sót từ P1-3): cân nhắc giảm `MAX_USER_CONNECTIONS` theo từng host xuống 1/3, hoặc ghi rõ trong tài liệu.

**Tiêu chí đóng lần 18:**
- Tài khoản bị tạm ngưng và khoá ghi vẫn bị khoá ở cả 3 host sau khi cập nhật từ 1.0.9 (có test SQL thật).
- Gỡ hoặc dừng ứng dụng trong lúc việc nền đang chạy bị từ chối hoặc chờ, không để lại unit mồ côi.
- Reseller không tạo được khách vượt trần.
- Restore backup giữ nguyên trạng thái khoá.
- Trạng thái mặc định của Node.js Manager khớp giữa code, README và CHANGELOG.
