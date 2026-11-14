# Thẩm định SPanel — Lần 17: `main` sau PR #27/#28 (Node.js Manager, quota, đổi tên miền, quyền MariaDB, Permissions-Policy)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Phạm vi:** `main` từ `bf51341` (đầu PR #27, đã thẩm định ở lần 16) tới `b147eb5`. 9 commit không phải merge, 41 file, +3.419 / −289:
  - `468f86e` P1-A/P1-B lần 16 (vào qua PR #27) · `2beb3c9` P2/P3 lần 16 (PR #28)
  - `fda3dfe` README · `f4f0af5` Next.js `next not found` · `2da57e7` log Nginx dạng dict
  - `42f4559` quota `<= 0` = không giới hạn + đổi tên miền dời thư mục · `a263c4a` TasksMax 2048
  - `20b077d` tài khoản MariaDB trên `localhost` / `127.0.0.1` / `::1` · `b147eb5` Permissions-Policy theo site
  - 7 commit cuối (`fda3dfe` → `b147eb5`) push thẳng lên `main`, không qua PR.
- **Force-push:** không. `4fcd35b` (đầu `main` lần trước) vẫn là tổ tiên của `b147eb5`.
- **Kiểm chứng:** đọc toàn bộ diff backend, helper, `install.sh`, `update.sh`, template Nginx, CI. Đọc log CI các run trên `main`. Thử cú pháp `git remote set-url origin -- <url>` trên máy (chạy đúng). Chưa chạy trên VPS. Chưa đọc kỹ frontend (`NodejsManager.jsx`, `Websites.jsx`, `permissionsPolicy.js`).
- **Ngày lập:** 14/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Các mục của lần 16 đều đã đóng** (P1-A, P1-B, P2-1..3, P3-1, P3-3..5), làm đúng hướng, có test.

**Nhưng 7 commit push thẳng sau đó đưa vào lỗi mới nặng hơn, và CI backend đỏ trên `main` từ `42f4559` (4 run liên tiếp) mà không ai sửa.** Phần lớn lỗi mới **không nằm sau cờ `nodejs_manager_enabled`**: đổi tên miền, quyền MariaDB, TasksMax chạy trên mọi máy ngay khi cập nhật.

**Cần sửa trước khi phát hành bản kế (8 mục P1):**

1. **P1-1 — CI đỏ trên `main`:** 2 test hỏng, một trong số đó là triệu chứng của P1-2.
2. **P1-2 — Đổi tên miền làm chết tiến trình Node.js và Laravel queue worker** và không tạo lại (hàm được gọi không tồn tại, lỗi bị nuốt).
3. **P1-3 — Nhân bản tài khoản MariaDB sang `127.0.0.1`/`::1` làm hỏng khoá tạm ngưng, khoá ghi khi vượt quota và giới hạn truy vấn theo giờ:** các khoá đó chỉ áp lên `@'localhost'` hoặc bị `GRANT ALL` ghi đè.
4. **P1-4 — `.env` của khách có thể đặt lại mật khẩu tài khoản MariaDB mà panel không quản lý:** mở lại đúng lỗ đã vá ở `create_database_credentials`.
5. **P1-5 — Helper chạy mã của khách (`npm install`, `npm run build`) ngoài mọi giới hạn:** không jail `/home`, không timeout, không nằm trong slice của khách, không sandbox systemd.
6. **P1-6 (phát hiện mới, có từ PR #27):** unit Node.js chỉ có `ProtectHome=read-only`, nên ứng dụng Node.js của khách đọc được file 0644 của khách khác. Đây đúng là lỗ mà jail của `terminal-exec` được viết ra để chặn.
7. **P1-7 — Migration sửa unit Node.js trong `update.sh` không bao giờ chạy** (`row.data` không tồn tại).
8. **P1-8 — Mỗi lần backend khởi động, `tasks_max` = 80 hoặc 512 của mọi gói và mọi user bị ghi đè thành 2048,** kể cả giá trị admin cố ý đặt.

Đề xuất: dừng push thẳng lên `main`, gom P1-1..P1-8 vào một PR, CI xanh rồi mới gắn tag bản kế.

---

## 1. Đối chiếu lần 16

| Mục lần 16 | Commit | Trạng thái | Ghi chú |
|---|---|---|---|
| P1-A DB dùng lại bị chuyển `website_id` | `468f86e` | **Đạt** | Không đổi `website_id`; chỉ `DROP` khi `db_created_by_manager`. Cả Node.js và Laravel. Còn P3-1 (Laravel cũ) |
| P1-B CI đọc `/proc/<pid>/environ` | `468f86e` | **Đạt** | `sudo cat`, có đối chứng dương `APP_MARKER=ok` và `PORT=21055`. Log run `43384961754` không còn `Permission denied` |
| P2-1 Xoá website không xoá file ⇒ unit còn chạy | `2beb3c9` | **Đạt** | Luôn gọi `nodejs-worker-delete` khi có `NodejsInstance` |
| P2-2 Rollback không xoá unit | `2beb3c9` | **Đạt** | |
| P2-3 Hết 8 lần thử im lặng | `2beb3c9` | **Đạt** | `for … else: 409`; trùng `website_id` trả 409 ngay |
| P3-1 `.env` đè `PORT`/`HOST`… | `2beb3c9` | **Đạt** | `copy_site_env_as_user` bỏ 6 khoá |
| P3-3 Webhook khi gói tắt ứng dụng | `2beb3c9` | **Đạt** | Kiểm addon và `app_limit_for` |
| P3-4 Khoá deploy 900 s | `2beb3c9` | **Đạt một phần** | Lên 1.800 s, nhưng `f4f0af5` làm pipeline dài thêm (xem P2-4) |
| P3-5 Đổi repo Git | `2beb3c9` | **Đạt** | `git remote set-url origin -- <repo>`, đã thử cú pháp |
| P3-6 VERSION / CHANGELOG | — | **Chưa** | `VERSION` vẫn `1.0.9`. CHANGELOG vẫn ghi Node.js Manager "không đưa vào `main`" (xem P3-5) |

---

## 2. P1 — sửa trước khi phát hành

### P1-1. CI backend đỏ trên `main` từ `42f4559`

| Run | Commit | Test hỏng |
|---|---|---|
| `43114683896` | `42f4559` | `test_release_round7_regressions.py::test_site_runtime_move_refuses_an_existing_target` |
| `43149168900` | `a263c4a` | như trên |
| `43201923452` | `20b077d` | như trên |
| `43384961754` | `b147eb5` | như trên + `test_isolation_php_fpm.py::test_update_retires_moved_pools_only_after_the_site_refresh` |

- **Test 1:** `bash exited 127 … delete_laravel_worker_units: command not found`. Test cắt riêng khối `site-runtime-move` của helper để chạy; `42f4559` thêm lời gọi `delete_laravel_worker_units` / `delete_nodejs_worker_units` vào khối đó mà không cập nhật phần stub của test. Hàm có tồn tại trong helper (dòng 7915, 8235), nên đây là lỗi test. Nhưng chính lời gọi này gây ra P1-2.
- **Test 2:** `ValueError: substring not found`. Test tìm chuỗi `nginx.rewrite_vhost(` trong `update.sh` để kiểm thứ tự; `b147eb5` đổi khối làm mới site sang `_rewrite_website_vhost(website)`. Thứ tự vẫn đúng, chỉ cần sửa chuỗi tìm trong test.
- **Vấn đề chính là quy trình:** 4 commit push thẳng, CI đỏ, vẫn push tiếp. Mọi lỗi khác trong báo cáo này đều lọt qua theo cùng đường.

### P1-2. Đổi tên miền làm dừng hẳn ứng dụng Node.js và Laravel queue worker

- **Vị trí:** helper `site-runtime-move` (dòng ~13057) gọi `delete_laravel_worker_units` + `delete_nodejs_worker_units` trên đường dẫn cũ trước khi `mv`. Sau đó `websites.py:1109` gọi:
  ```python
  from app.api.nodejs_manager import _sync_systemd_unit
  _sync_systemd_unit(website, node_inst)
  ```
  **`_sync_systemd_unit` không tồn tại** trong `nodejs_manager.py` (hàm thật là `_sync_nodejs_worker_unit` với chữ ký khác). `ImportError` bị `except Exception: pass` nuốt.
- **Hệ quả:** đổi tên `a.com` → `b.com` là unit Node.js bị xoá và không tạo lại ⇒ Nginx proxy tới cổng không ai nghe ⇒ 502 mãi. Laravel queue worker cũng bị xoá và không có dòng nào tạo lại. Panel vẫn báo "đang chạy" vì state không đổi. Khi rollback (đổi tên lỗi giữa chừng), thư mục được dời lại nhưng unit cũng không được tạo lại.
- **Kèm theo (P2):** đổi tên còn **tự đổi tên luôn mọi site con cùng chủ** có đuôi `.a.com` (`shop.a.com` → `shop.b.com`, `websites.py:1169`), không hỏi, lỗi bị nuốt. Khách đổi tên site chính sẽ thấy site con đổi theo mà không biết trước. SSL của alias cũng bị tắt dù tên alias không đổi.
- **Sửa:**
  1. Thay bằng lời gọi thật: lấy `inst = _row_to_instance_dict(node_inst)`, `_resolve_nodejs_paths(...)`, rồi `_sync_nodejs_worker_unit(..., strict=False)` nếu `process_enabled`. Làm tương tự cho Laravel worker.
  2. Bỏ `except: pass`: ghi log cảnh báo và trả trong response để giao diện báo cho khách.
  3. Site con: chỉ đổi khi người dùng tick "đổi luôn các site con", mặc định không.
- **Test:** đổi tên site có `NodejsInstance` ⇒ helper `nodejs-worker-sync` được gọi với đường dẫn mới; site có Laravel worker ⇒ worker được tạo lại; site con không đổi khi không tick.

### P1-3. Tài khoản MariaDB trên `127.0.0.1`/`::1` vượt qua khoá tạm ngưng, khoá ghi và giới hạn theo giờ

`20b077d` tạo mỗi tài khoản khách ở 3 host (`localhost`, `127.0.0.1`, `::1`) vì Node.js kết nối TCP. Nhưng các cơ chế chặn không theo kịp:

| Cơ chế | Vị trí | Vấn đề |
|---|---|---|
| Tạm ngưng tài khoản (`ACCOUNT LOCK`) | `mariadb.set_account_locked` → `account_lock_sql(client_host="localhost")` | Chỉ khoá `@'localhost'`. Khách bị tạm ngưng vẫn đăng nhập được qua `127.0.0.1` (mọi ứng dụng PHP/Node đặt `DB_HOST=127.0.0.1`) |
| Giới hạn truy vấn theo giờ | `hourly_limits_sql` | Chỉ `ALTER USER …@'localhost'`. Hai host kia không giới hạn |
| Khoá ghi khi vượt quota MySQL (`set_database_write_lock`) | `sync_local_loopback_accounts()` chạy ở `ensure_mysql_governor_settings` mỗi lần backend khởi động | `GRANT ALL` cho mọi tài khoản ở cả 3 host ⇒ **mỗi lần restart panel, mọi database đang bị khoá ghi được mở lại** trong MariaDB, trong khi giao diện vẫn báo "đang khoá" |
| Như trên | `databases.py:346` `change_database_password` → `ensure_local_database_user_hosts` → `GRANT ALL` | **Khách tự mở khoá ghi bằng cách đổi mật khẩu database** |
| Như trên | `update.sh` `refresh_spanel_mariadb_grants` | `GRANT ALL` ở `127.0.0.1`/`::1` cho mọi user có dòng `mysql.db`, kể cả đang bị khoá |
| `MAX_USER_CONNECTIONS` | `enforce_db_user_resource_limits` | Tính theo từng `user@host` ⇒ trần kết nối thực tế gấp 3 |

- **Sửa:**
  1. Một hàm duy nhất trả danh sách host (`LOCAL_CLIENT_HOSTS`) và **mọi** câu `ALTER USER` / `GRANT` / `REVOKE` / `ACCOUNT LOCK` / giới hạn theo giờ đều lặp qua nó.
  2. `ensure_local_database_user_hosts` và `sync_local_loopback_accounts` phải tôn trọng `DatabaseAccount.write_locked`: nếu đang khoá thì cấp đúng bộ quyền khoá (`SELECT, DELETE, DROP, …`) thay vì `ALL`. Bản sao trong `update.sh` cũng vậy, hoặc bỏ hẳn phần SQL trong `update.sh` và gọi hàm Python.
  3. Sao chép cả trạng thái `ACCOUNT LOCK` khi tạo host mới cho user đang bị tạm ngưng.
  4. Cách khác, đơn giản hơn: chỉ tạo `@'127.0.0.1'` (bỏ `::1`), và trong `.env` mặc định của Node.js dùng `127.0.0.1`. Vẫn phải làm (1)–(3) cho host đó.
- **Test:** tài khoản bị `set_account_locked(True)` ⇒ cả 3 host có `ACCOUNT LOCK`; DB `write_locked=True` ⇒ sau `sync_local_loopback_accounts()` và sau `change_database_password`, `SHOW GRANTS` ở cả 3 host không có `INSERT`/`UPDATE`.

### P1-4. `.env` của khách đặt lại mật khẩu tài khoản MariaDB mà panel không quản lý

- **Vị trí:** `nodejs_manager.py:1471` `_ensure_nodejs_local_db_grants`, bước 2. Chạy khi cài, bật tiến trình, lưu `.env`, deploy Git.
- **Luồng:** đọc `DB_DATABASE`/`DB_USER`/`DB_PASSWORD` (hoặc `MYSQL_*`, hoặc `DATABASE_URL`) từ `.env` / `.env.local` do khách viết. Nếu `DB_DATABASE` là DB của chính khách và **không có dòng `DatabaseAccount` nào của chủ khác** mang `DB_USER` đó, gọi `ensure_local_database_user_hosts(db_name, db_user, db_pass)` ⇒ `create_database_credentials(..., allow_existing_user=True)` ⇒ `ALTER USER '<db_user>'@'localhost' IDENTIFIED BY '<mật khẩu khách chọn>'` + `GRANT ALL`.
- **Lỗi:** chỉ chặn 5 tên trong `RESERVED_DB_USERS` và tên có trong bảng `DatabaseAccount`. Mọi tài khoản MariaDB mà panel **không** theo dõi (do admin tạo tay, phần mềm khác cài, PowerDNS `gmysql` trên máy đã đổi backend, tài khoản còn sót khi dòng `DatabaseAccount` đã xoá, …) đều bị khách đặt lại mật khẩu và chiếm. Docstring của `user_exists` nói rõ kiểm tra đó tồn tại để "nhận ra tài khoản spanel không biết". `allow_existing_user=True` ở đây bỏ qua đúng kiểm tra đó. Đây là lỗ mà CHANGELOG 1.0.9 ghi là đã đóng.
- **Sửa:** bỏ bước 2 (đọc `.env`). Chỉ đồng bộ host cho tài khoản có dòng `DatabaseAccount` **cùng chủ** (bước 1), và khi đồng bộ thì dùng hash đã lưu, **không bao giờ `ALTER USER` bằng mật khẩu lấy từ file của khách**. Khách muốn dùng DB khác thì chọn "dùng database có sẵn" trong giao diện.
- **Test:** tạo user MariaDB `ext_app` không có `DatabaseAccount`; `.env` của khách ghi `DB_DATABASE=<db của khách>`, `DB_USER=ext_app`, `DB_PASSWORD=x` ⇒ sau `update_nodejs_env`, mật khẩu của `ext_app` không đổi và không có `GRANT` mới.

### P1-5. `prepare_nodejs_app_as_user` chạy mã của khách ngoài mọi giới hạn

- **Vị trí:** helper `prepare_nodejs_app_as_user` (dòng 8122), gọi trong mọi `nodejs-worker-sync`. Chạy `npm install` (lifecycle scripts `preinstall`/`postinstall` của khách và của mọi gói phụ thuộc) và `npm run build` (script do khách viết) bằng `runuser -u <user>` trực tiếp.
- **So với `terminal-exec`:** đường đó có `unshare --mount` + tmpfs thay `/home` (chỉ còn home của khách), `timeout --kill-after`, và chú thích nói rõ lý do ("the tenant already runs their own code … through npm lifecycle scripts"). Đường mới không có cái nào:
  1. **Không jail `/home`:** script build thấy được `/home/<khách khác>/…` (0751 traversable, file 0644 theo thiết kế).
  2. **Không timeout:** helper gọi với `timeout=600`, nhưng khi Python giết `sudo`, các tiến trình `runuser`/`npm` con vẫn sống. `postinstall: "sleep infinity"` treo mãi.
  3. **Không nằm trong `user-<uid>.slice`:** chạy trong cgroup của `spanel-api.service` (`TasksMax=infinity`), không bị `MemoryMax`/`CPUQuota` của gói giới hạn. Một `npm run build` ăn RAM tính vào panel.
  4. **Không có sandbox systemd** (`ProtectSystem`, `PrivateTmp`, `NoNewPrivileges`) như unit.
- **Lách được dù Python đã build trước trong jail:** Python `_ensure_nodejs_app_ready` chạy qua `terminal-exec` (có jail) trước, rồi helper chỉ build lại khi thiếu `.next/BUILD_ID`. Script build chỉ cần tự phát hiện jail (`[ -d /home/<ai đó> ] || exit 1`): trong jail thì thất bại, ngoài jail (helper) thì chạy.
- **Sửa:** bỏ `npm install` và `npm run build` khỏi helper. Python đã làm việc đó qua `terminal-exec`. Helper chỉ giữ bước 1 (sửa stub symlink trong `.bin`) và bước 3 (`chmod`), là thao tác file dưới quyền user. Nếu vẫn muốn helper tự build: chạy qua `systemd-run --wait --pipe --uid=<user> --slice=user-<uid>.slice -p MemoryMax=… -p RuntimeMaxSec=… -p TemporaryFileSystem=/home -p BindPaths=/home/<user> …`.
- **Test (CI helper-integration):** `package.json` có `"postinstall": "touch /home/runner-other/pwned || true; sleep 999"` ⇒ `nodejs-worker-sync` trả về trong thời gian giới hạn và file không được tạo.

### P1-6. (Phát hiện mới, có từ PR #27) Unit Node.js đọc được file của khách khác

- **Vị trí:** unit do `sync_nodejs_worker_unit` sinh ra: `ProtectHome=read-only`, `ProtectSystem=strict`, `ReadWritePaths=${app_target} …`. Không có `TemporaryFileSystem=/home` hay `InaccessiblePaths`.
- **Lỗi:** `ProtectHome=read-only` chỉ làm `/home` thành chỉ đọc, **không ẩn** home của người khác. Đầu helper ghi rõ: site tree 0644, home 0751, ranh giới giữa khách là open_basedir của PHP-FPM, chroot SFTP và jail terminal, "not by these bits". Ứng dụng Node.js không có lớp nào trong ba lớp đó ⇒ `fs.readFileSync('/home/<khách khác>/<domain>/public_html/…')` đọc được mã nguồn, upload, file cấu hình 0644 của khách khác. Tên user và domain dễ đoán (`/etc/passwd` đọc được).
- **Phạm vi:** các unit khác cũng `ProtectHome=read-only` (dòng 8012 Laravel queue worker, 8865 / 8937 ứng dụng chung và Python). Laravel worker chạy PHP CLI, cần kiểm có `open_basedir` hay không. Unit Node.js và Python chắc chắn không có.
- **Sửa:** giống jail của terminal:
  ```ini
  TemporaryFileSystem=/home:ro
  BindPaths=/home/${user}
  ```
  (hoặc `BindReadOnlyPaths=` cho phần ngoài `ReadWritePaths`). Áp cho cả 4 loại unit.
- **Test (CI helper-integration):** tạo user thứ hai có file 0644 trong home; ứng dụng Node.js thử `readFileSync` file đó ⇒ phải lỗi `ENOENT`.

### P1-7. Migration `migrate_repair_nodejs_worker_units` trong `update.sh` không bao giờ chạy

- **Vị trí:** `update.sh:1028`: `data = row.data if isinstance(row.data, dict) else {}`.
- **Lỗi:** `NodejsInstance` (từ `8cec410`) là bảng có cột riêng, không có thuộc tính `data`. `AttributeError` nằm ngoài `try` bên trong vòng lặp ⇒ rơi vào `except` ngoài cùng ⇒ in một dòng `WARNING … skipped` rồi bỏ qua **mọi** instance. Các key `installed`, `process_enabled` cũng là key của dict cũ.
- **Hệ quả:** máy đang chạy Node.js sau khi cập nhật không được ghi lại unit (không có `PATH` có `node_modules/.bin`, không có `StartLimit*`, vẫn `TasksMax=256`). Lỗi "next not found" mà `f4f0af5` định sửa vẫn còn trên máy cũ cho tới khi khách tự bật/tắt tiến trình.
- **Sửa:** `inst = nm._row_to_instance_dict(row)`, lọc `row.detached` / `row.process_enabled`, rồi gọi như cũ. Thêm test chạy đoạn Python này trên DB SQLite có một dòng `NodejsInstance`.

### P1-8. Mỗi lần khởi động, `tasks_max` 80/512 bị ghi đè thành 2048

- **Vị trí:** `core/database.py:168–170`, trong `_ensure_resource_limit_columns()` (chạy lúc khởi động):
  ```sql
  UPDATE user_packages SET tasks_max = 2048 WHERE tasks_max IN (80, 512);
  UPDATE users         SET tasks_max = 2048 WHERE tasks_max IN (80, 512);
  ```
- **Lỗi:** không phải migration một lần mà chạy mỗi lần restart. Admin cố ý đặt 512 cho một gói (giá trị mặc định cũ, rất phổ biến) thì sau restart thành 2048, và không bao giờ đặt lại được 80 hay 512. Đây cũng là trần chống fork bomb của cả slice khách, tăng gấp 4–25 lần cho mọi khách, không chỉ khách dùng Node.js.
- **Sửa:** chuyển thành migration Alembic chạy một lần (hoặc cờ "đã chạy" trong `settings`). Chỉ nâng `TasksMax` trong **unit** Node.js (nơi gây EAGAIN), không đụng giới hạn slice của gói trừ khi admin đổi.

---

## 3. P2 — sửa trước khi bật cờ Node.js

### P2-1. `POST /api/nodejs/instances/{id}/permissions-policy` biến site không có Node.js thành reverse proxy

`nodejs_manager.py:3297` không kiểm có instance. Với site WordPress không cài Node.js: `saved` rỗng ⇒ cấp cổng mới ⇒ `_sync_nodejs_nginx_vhost(..., app_type="application")` ⇒ vhost proxy tới cổng trống ⇒ site trả 502. `site.app_type` trong DB không đổi, nên lần ghi vhost sau mới tự hồi phục. Chỉ ảnh hưởng site của chính khách, nhưng lỗi khó hiểu. Sửa: `if not saved or saved.get("detached"): 404`. Với site thường đã có `PATCH /api/websites/{id}/permissions-policy`.

### P2-2. Bật tiến trình chạy `npm install` + `npm run build` đồng bộ trong request

`manage_nodejs_process` gọi `_ensure_nodejs_app_ready` (tối đa 300 + 300 s cho install có fallback + 300 s build) rồi `nodejs-worker-sync` (timeout 600 s). Một lần bấm "Bật" có thể giữ worker API hơn 20 phút, vượt timeout của proxy trước panel. Giao diện báo lỗi trong khi việc vẫn chạy. Nên chạy nền như webhook (202 + khoá theo site + trạng thái để giao diện hỏi lại).

### P2-3. Đổi tên miền: các vấn đề kèm theo P1-2

- Tự đổi tên site con (đã nêu ở P1-2).
- SSL alias bị tắt dù tên alias không đổi.
- Rollback không khôi phục WAF của tên cũ (`waf.remove_site_rules(old_domain)` đã chạy).
- Không có test cho `_rename_single_website` ngoài nhánh helper.

### P2-4. Khoá deploy 1.800 s vẫn có thể ngắn hơn pipeline

Pipeline mới: `set-url` 30 + `fetch` 180 + `npm install` 300 **×2** (fallback `--legacy-peer-deps`) + build 300 + `nodejs-worker-sync` 600, **cộng** `npm install` / build không giới hạn trong helper (P1-5). Tổng có thể vượt 1.800 s. Sau khi sửa P1-5, tính lại tổng trần và đặt TTL lớn hơn, hoặc ghi PID vào file khoá.

### P2-5. (Có từ trước) Reseller đặt giới hạn tài nguyên không qua kiểm "pool"

`POST /api/resources/users/{id}/limits` cho reseller đặt `storage_limit_mb`, `memory_max_mb`, … cho khách của mình mà không gọi `_enforce_reseller_quotas`. Trước đây đã đặt được tới 1 TB; nay thêm `-1` / `0` = không giới hạn. Nên gọi cùng hàm kiểm như `create_user` / `update_user`.

---

## 4. P3 — ghi chú

1. **Laravel cài trước `468f86e` mất khả năng xoá DB khi gỡ:** state cũ không có `db_created_by_manager` ⇒ tick "xoá database" không làm gì (an toàn, nhưng DB mồ côi). Nên migrate: đặt cờ `true` khi `DatabaseAccount.website_id == site.id` và tên DB khớp mẫu do Laravel Manager sinh.
2. **Migration `nodejs_instances_table` bị sửa tại chỗ** (thêm cột `db_created_by_manager`). Không ảnh hưởng máy khách vì migration chưa từng phát hành, nhưng VPS thử nào đã chạy nhánh `feat/nodejs-manager` sẽ thiếu cột. Lần sau nên thêm migration mới.
3. **`spanel@'127.0.0.1'` và `spanel@'::1'` có `ALL … WITH GRANT OPTION`:** cần cho panel nếu panel kết nối TCP, nhưng mở thêm đường đăng nhập superuser qua TCP cho mọi tiến trình cục bộ biết mật khẩu. Nếu panel chỉ dùng socket thì bỏ.
4. **`prepare_nodejs_app_as_user` đọc `package.json` và liệt kê `node_modules/.bin` bằng quyền root** (`grep`, `ls -A`, glob). Khách đặt symlink sẽ biết được một bit (file có chứa chuỗi hay không, thư mục rỗng hay không). Nhỏ, nhưng tự hết khi sửa P1-5.
5. **CHANGELOG / VERSION:** `VERSION` vẫn `1.0.9`, tag `v1.0.9` ở `4fcd35b`, CHANGELOG vẫn ghi Node.js Manager "không đưa vào `main`". Nay `main` đã có Node.js Manager (sau cờ) và nhiều thay đổi không nằm sau cờ. Cần mục `[Unreleased]` / `1.0.10`.
6. **`fastcgi_hide_header` / `proxy_hide_header Permissions-Policy`:** plugin WordPress hay ứng dụng tự đặt Permissions-Policy giờ bị Nginx bỏ đi, với PHP thì không có cơ chế gộp như proxy. Nên ghi trong tài liệu, vì khách sẽ thắc mắc tại sao header của plugin không còn.
7. **`extract_zip` ghi symlink trong zip thành file thường 0644** (chú thích trong `prepare_nodejs_app_as_user`). Bước 1 của helper là sửa triệu chứng. Gốc là hàm giải nén, nên sửa ở đó (hoặc từ chối symlink khi giải nén, tuỳ chính sách).

---

## 5. Đề xuất

1. **Ngưng push thẳng lên `main`.** Mọi thay đổi qua PR, CI xanh mới merge. (Không đề nghị bật branch protection, chỉ là thoả thuận làm việc.)
2. **Một PR "round 17"** sửa P1-1 → P1-8, mỗi mục một commit có test:
   - P1-1: sửa 2 test.
   - P1-2: gọi đúng hàm, tạo lại cả Laravel worker, bỏ `except: pass`.
   - P1-3 + P1-4: gom mọi thao tác quyền MariaDB qua một hàm lặp host, tôn trọng `write_locked` / `ACCOUNT LOCK`, bỏ đọc `.env`.
   - P1-5 + P1-6: bỏ `npm` khỏi helper; thêm `TemporaryFileSystem=/home` + `BindPaths` cho 4 loại unit; test CI cross-tenant.
   - P1-7, P1-8.
3. **Sau đó mới gắn tag bản kế** và cập nhật CHANGELOG. Cờ `nodejs_manager_enabled` giữ tắt tới khi xong P2.

**Tiêu chí đóng lần 17:** CI `main` xanh; đổi tên site có Node.js/Laravel worker xong vẫn chạy; khoá tạm ngưng và khoá ghi có hiệu lực ở cả 3 host và giữ nguyên sau restart / đổi mật khẩu; `.env` không làm đổi mật khẩu tài khoản ngoài panel; helper không còn chạy `npm` ngoài jail; ứng dụng Node.js không đọc được `/home` của khách khác (có test CI); migration sửa unit chạy thật; `tasks_max` của admin không bị ghi đè.
