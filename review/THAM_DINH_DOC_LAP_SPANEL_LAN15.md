# Thẩm định SPanel — Lần 15: nhánh `1.0.9` (Node.js Manager, quota tự bật, cache WordPress)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** nhánh `1.0.9` @ `011819c` (09/11/2026, 15:09) = tag `v1.0.9`.
- **So với `main`:** `origin/main` @ `3bf2f6b` (02/11). `1.0.9` = `main` + 7 commit, không đi sau `main`.
- **Phạm vi** (`3bf2f6b..011819c`, 27 file, +7.600 / −111):

  | Commit | Nội dung |
  |---|---|
  | `8fb6b75` | Quota: tự bật kernel quota sau reboot, tự cài `quota_v2` (`disk_quota.py`, helper `quota_enable`) |
  | `496bcfd` | WordPress Manager: cache toolkit, bỏ quét wp-cli thừa |
  | `00244db` | **Node.js Manager** cho End User (`nodejs_manager.py` 2.857 dòng, `NodejsManager.jsx` 3.147 dòng, 4 verb helper mới), nâng VERSION lên 1.0.9 |
  | `4e57eb9`, `ae0787d`, `dc22c24`, `011819c` | Sửa test menu, chmod `.env` 0640, giao diện modal Node.js |

- **Kiểm chứng:** đọc toàn bộ diff helper (`spanel-helper.sh`, +362), đọc `nodejs_manager.py` theo các đường gọi helper / MariaDB / file của khách, đọc `test_nodejs_manager.py`. Đối chiếu tham số giữa Python và helper. Chưa đọc kỹ `NodejsManager.jsx` và `wordpress_manager.py` (chỉ lướt). Không chạy trên VPS.
- **CI:** chạy theo push trên nhánh `1.0.9`. `00244db` và `4e57eb9` **đỏ**; ba commit sau xanh. Không có PR vào `main`.
- **Phát hành:** đã có tag `v1.0.9` trỏ `011819c`, nhưng chưa có GitHub Release, và `main` ≠ tag. `spanel-update` kênh stable chỉ dùng tag khi `main` trùng tag, nên máy khách **hiện vẫn nhận `main`**, chưa nhận 1.0.9 — còn kịp sửa.
- **Ngày lập:** 11/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Không phát hành `1.0.9` như hiện tại. Node.js Manager có 3 lỗi P0: khách đọc được bí mật của panel / root MariaDB (hiện bị lỗi P1-1 che, sẽ mở ra ngay khi P1-1 được sửa), khách chiếm được database của khách khác, và khách xoá được database của khách khác. Ngoài ra tiến trình Node.js không chạy được trên máy thật, vì Python và helper truyền tham số lệch thứ tự.**

- Đây là lặp lại đúng kịch bản Laravel Manager ở lần 13–14: tính năng lớn (≈ 6.000 dòng) vào thẳng nhánh phát hành và được gắn tag, không PR, không thẩm định, test chỉ chạy ở chế độ dry-run nên không bắt được lỗi giao tiếp với helper.
- Phần quota (`8fb6b75`) đúng hướng, có `flock`, có test; chỉ còn ghi chú nhỏ (mục 4).
- Phần cache WordPress (`496bcfd`) không thấy lỗi qua lần đọc lướt; chưa thẩm định sâu.

**Điểm:** nếu phát hành nguyên `011819c` thì **giảm** so với lần 14: có 2 lỗi P0 cho phép khách chiếm quyền vượt ranh giới tài khoản (P0-1, P0-2). Nếu gỡ Node.js Manager ra khỏi bản phát hành (giữ quota + WordPress) thì điểm không đổi.

---

## 0. Quy tắc (lần 2 mục 0 + lần 14 mục 0) — đối chiếu

| Quy tắc | Trạng thái |
|---|---|
| Nhánh phát hành chỉ nhận mã đã thẩm định; tính năng chưa xong để ở `feat/…`, nếu hiện menu thì sau cờ, mặc định tắt | **Vi phạm.** Node.js Manager vào thẳng `1.0.9` và được bật cho mọi End User |
| Mỗi mục một commit | Đạt một phần: `00244db` gộp tính năng mới + nâng VERSION + sửa README/CHANGELOG |
| Test gọi mã thật, không grep chuỗi | **Vi phạm.** `test_nodejs_manager.py:83` (`test_menu_order_nodejs_before_laravel`) và `:97` (`test_helper_verbs_present_in_spanel_helper`) đọc file nguồn rồi `in`. Test vòng đời (`:110`) chạy dry-run: mọi lời gọi helper trả giả, nên lỗi P1-1 lọt qua |
| PR, CI xanh trước merge; tag mới sau merge | **Vi phạm.** Tag `v1.0.9` có trước khi có PR; 2 commit đầu CI đỏ |
| Không ghi điểm / "Enterprise" vào commit | Đạt |

---

## 1. P0 — Phải sửa trước khi phát hành

### P0-1. Unit systemd nạp `.env` trong thư mục khách bằng quyền root ⇒ khách đọc được file bí mật bất kỳ

- **Vị trí:** `spanel-helper.sh:8175` trong `sync_nodejs_worker_unit`: `EnvironmentFile=-${app_target}/.env`, với `app_target` nằm trong `/home/<user>/<domain>/public_html[/<sub>]`.
- **Cơ chế:** systemd (PID 1, root) đọc `EnvironmentFile` **trước** khi hạ quyền xuống `User=`, và đi theo symlink. Khách sở hữu thư mục ứng dụng nên thay `.env` bằng symlink tuỳ ý:
  - `.env -> /opt/spanel/backend/.env` ⇒ `SECRET_KEY` của panel vào `process.env` của ứng dụng khách ⇒ ký JWT admin.
  - `.env -> /root/.my.cnf` ⇒ dòng `password=…` thành biến `password` ⇒ mật khẩu root MariaDB.
  - Khách chỉ cần bấm "Khởi động lại" trong Node.js Manager (hoặc sửa `.env` qua panel) để unit được restart.
- **Hiện trạng:** trên `011819c`, unit chưa bao giờ được ghi ra vì P1-1 (tham số lệch) làm `nodejs-worker-sync` luôn bị từ chối. Tức là P0-1 **đang bị P1-1 che**. Sửa P1-1 mà chưa sửa P0-1 là mở lỗi này ra ngay ⇒ phải sửa hai mục cùng một PR, P0-1 trước.
- **Ghi chú:** chính helper đã biết điều này: `app_env_file()` (≈ 7708) cố ý đặt file env của Site Apps ở `$SPANEL_DATA_DIR/apps/`, ngoài home khách, với lý do "systemd loads EnvironmentFile before dropping privileges". Node.js Manager đi ngược lại quyết định đó.
- **Sửa (chọn một):**
  - (a) Bỏ `EnvironmentFile=` khỏi unit. Ứng dụng tự đọc `.env` (dotenv) dưới quyền user khách. Panel chỉ đặt `PORT`, `HOST`, `NODE_ENV` bằng `Environment=`.
  - (b) Helper sao `.env` sang `$SPANEL_DATA_DIR/nodejs/<unit>.env` (root 0600) bằng cách đọc **dưới quyền user khách** (`setpriv`/`runuser` + `cat`), không đọc bằng root theo đường dẫn khách.
- **Tiêu chí:** `git grep -n 'EnvironmentFile=.*app_target\|EnvironmentFile=.*/home' -- installer` rỗng. Test helper: tạo `.env` là symlink tới file root 0600, chạy `nodejs-worker-sync`, kiểm tra biến đó không có trong `systemctl show -p Environment` / môi trường tiến trình.

### P0-2. Cài Node.js với `db_name` của người khác ⇒ được `GRANT ALL` lên database đó

- **Vị trí:** `nodejs_manager.py:1970–1985` (`install`): `db_name = payload.db_name or …`, rồi `mariadb.create_database_credentials(db_name, db_user, db_password)`.
- **Cơ chế:** `create_database_credentials` (`mariadb.py:184`) chạy `CREATE DATABASE IF NOT EXISTS` (database đã có thì bỏ qua) rồi `GRANT ALL PRIVILEGES ON <db_name>.* TO <db_user>`. Hàm chỉ từ chối **user** đã tồn tại, không từ chối **database** đã tồn tại. API Databases chặn bằng `DatabaseAccount.db_name == db_name` (`databases.py:190`); Node.js Manager không chặn.
- **Hậu quả:** End User gửi `create_database=true, db_name=<db WordPress của khách khác>, db_user=<tên mới>, db_password=<tự chọn>` ⇒ có toàn quyền đọc/ghi database đó (bảng `wp_users`, …). Tên database thường đoán được từ tên miền.
- **Thêm:** không gọi `enforce_user_database_limit` ⇒ vượt giới hạn database của gói.
- **Sửa:** dùng chung đường tạo database của `app/api/databases.py` (kiểm trùng `DatabaseAccount`, kiểm database đã có trên MariaDB, tiền tố theo user, giới hạn gói). Ở tầng `mariadb.py`, cho `create_database_credentials` từ chối database đã tồn tại trừ khi gọi từ đường khôi phục.
- **Tiêu chí:** test qua `TestClient`: user B cài Node.js với `db_name` của user A ⇒ 409, không có lệnh `GRANT` nào được chạy (mock `_run_sql`).

### P0-3. Gỡ Node.js với `drop_database=true` xoá được database của người khác

- **Vị trí:** `nodejs_manager.py:2820–2837` (`remove`) và `:2398–2405` (`PUT /env`).
- **Cơ chế:** `PUT /instances/{id}/env` ở chế độ `raw` nhận `.env` do khách viết, rồi ghi `DB_DATABASE` / `DB_USERNAME` vào state (`inst["db_name"]`, `inst["db_user"]`). Khi gỡ: nếu không có `DatabaseAccount` của chủ site trùng tên thì nhánh `elif db_user:` vẫn gọi `mariadb.drop_database(db_name, db_user)` bằng tài khoản quản trị MariaDB.
- **Hậu quả:** End User đặt `DB_DATABASE=<db của khách khác>`, `DB_USERNAME=<user của khách đó>`, rồi gỡ với "xoá database" ⇒ database và user MariaDB của khách khác bị `DROP`. Cùng họ lỗi với Laravel Manager ở lần 13 ("gỡ cài đặt có thể xoá DB WordPress").
- **Sửa:** chỉ xoá qua `teardown.drop_database_record` cho đúng `DatabaseAccount` có `owner_id == site.owner_id` **và** `website_id == site.id` (do Node.js Manager tạo). Bỏ hẳn nhánh `elif`. Không lấy `db_name` từ `.env` để làm việc gì có quyền quản trị.
- **Tiêu chí:** `git grep -n "mariadb.drop_database" -- backend/app/api/nodejs_manager.py` rỗng; test: `.env` trỏ DB của user khác ⇒ gỡ không xoá gì của user đó.

---

## 2. P1

### P1-1. Python và helper truyền tham số `nodejs-worker-sync` lệch thứ tự ⇒ ứng dụng không bao giờ chạy trên máy thật

- **Python** (`nodejs_manager.py:836–851`): `user, site_root, app_dir, major, start_mode, start_arg, port, memory_mb, node_env, enabled`.
- **Helper** (`spanel-helper.sh:8108`, usage ở `:13583`): `user, site_root, app_dir, node_major, port, memory_mb, start_mode, start_arg, node_env, enabled`.
- **Hậu quả:** `$5` là `npm`/`node` ⇒ `require_app_port` từ chối ⇒ cài đặt (`strict=True`) trả 500 "Không thể cấu hình dịch vụ systemd Node.js"; Start/Restart/Git deploy/lưu `.env` (`strict=False`) im lặng thất bại. Test không bắt được vì chạy dry-run.
- **Sửa:** thống nhất một thứ tự; thêm test gọi helper thật (hoặc ít nhất mock `shell.privileged` và so `helper_args` với usage của helper, đọc bằng cách chạy `spanel-helper nodejs-worker-sync` thiếu tham số và parse dòng usage — không grep file).

### P1-2. `nodejs-worker-status` trả khoá khác với khoá Python đọc

- Helper (`:8222`) in `unit_present, enabled, state, pid, memory_bytes, unit`. Python (`:920–928`) đọc `main_pid, memory_current_bytes, restarts, port, node_major, start_mode, start_arg, memory_mb`.
- **Hậu quả:** giao diện luôn hiện PID 0, RAM 0, số lần restart 0; cổng/phiên bản lấy từ state chứ không từ unit thật.
- **Sửa:** đổi tên khoá ở một phía; in thêm `NRestarts` từ `systemctl show`.

### P1-3. Webhook deploy công khai, bật mặc định, chạy đồng bộ

- **Vị trí:** `nodejs_manager.py:2685` `POST /api/nodejs-manager/webhook/{website_id}/{token}` — không cần đăng nhập. `webhook_enabled` mặc định `True` (`:1710`, `:2696`).
- **Vấn đề:**
  1. Bật sẵn cho mọi ứng dụng dù khách chưa bao giờ cấu hình.
  2. Mỗi lần gọi chạy `git pull` + `npm install` + build, tới ~15 phút, **trong worker HTTP**, không khoá theo site: gọi song song hai lần là hai `npm install` cùng ghi một thư mục; vài chục request là chiếm hết worker của API (từ chối dịch vụ panel cho mọi người).
  3. Token mặc định là HMAC cố định theo `SECRET_KEY` + id + domain: không xoay được trừ khi khách bấm "rotate".
- **Sửa:** mặc định tắt; chỉ sinh token ngẫu nhiên khi khách bật; chạy deploy nền với khoá theo site (trả 202, request trùng thì 409); giới hạn tần suất theo IP/site.

---

## 3. P2

1. **Rơi về phiên bản Node khác mà không báo** — `resolve_or_install_node_bin_dir` (`:8046`): cài bản yêu cầu thất bại thì lấy bản bất kỳ trong `/opt/spanel/node` hoặc `/usr/bin/node`. Ứng dụng chạy Node 18 trong khi panel ghi "Node 22". Nên `deny` thay vì rơi về.
2. **`terminal-exec --node-version` có thể cài Node bằng root** trong một request của End User (cùng hàm trên). Nên tách cài đặt ra verb riêng chỉ admin gọi.
3. **State Node.js là một khối JSON duy nhất**, trong bảng tạo bằng `CREATE TABLE IF NOT EXISTS` lúc chạy (`:284`), ngoài Alembic. Khoá chỉ là `threading.RLock` trong một tiến trình: nếu API chạy nhiều worker thì cấp cổng (`_allocate_nodejs_port`, `:458`) và ghi state có thể đè nhau / cấp trùng cổng. Nên dùng bảng có cột (`website_id`, `port` UNIQUE) qua migration.
4. **Cổng loopback không gắn với user**: ứng dụng của khách A có thể nghe trên cổng đã cấp cho khách B khi app B đang dừng/restart (`Restart=always`, `RestartSec=5`) ⇒ Nginx của B chuyển khách truy cập sang app của A. Lỗi này có sẵn ở Site Apps; Node.js Manager nhân rộng nó cho mọi End User. Hướng sửa lâu dài: unix socket trong thư mục của user, hoặc socket activation.
5. **`GIT_BRANCH_RE` cho phép tên nhánh bắt đầu bằng `-`** (`:68`), truyền thẳng vào `git pull origin <branch>`. Chưa thấy cách khai thác (không có `=`), nhưng nên chặn `-*` như `start_arg`.
6. **Test**: hai test grep chuỗi (mục 0) cần thay bằng test hành vi; thêm test cho P0-1..3 và P1-1..2.

---

## 4. Quota (`8fb6b75`) — ghi chú nhỏ

- `quota_enable` có `flock` (`QUOTA_LOCK_FILE`), chạy lệnh thiết bị qua `systemd-run -p PrivateDevices=no` như lần 13 yêu cầu. Đúng hướng.
- Mỗi lần `quotaon` thất bại, helper chạy `apt-get update` + cài `linux-modules-extra-$(uname -r)` (có thể vài trăm MB) — kể cả khi gọi tự động lúc khởi động (`start_boot_reconciler`). Nên chỉ làm khi admin bấm, hoặc giới hạn một lần / kernel.
- `quotacheck -cfum` (ép quét trên mount đang ghi) có thể cho số liệu lệch nếu đang có ghi nhiều; chấp nhận được nếu chạy lúc khởi động, nên ghi chú cho admin.
- Các biến `QUOTA_MODULES_CONF`, `QUOTA_LOCK_FILE`, `QUOTA_ROOT_PREFIX` đọc từ môi trường: an toàn vì helper chạy qua `sudo -n` (env_reset), nhưng đừng thêm chúng vào `env_keep` trong sudoers.

---

## 5. Lộ trình đề xuất

1. **Phát hành 1.0.9 không có Node.js Manager:** nhánh từ `3bf2f6b`, cherry-pick `8fb6b75` và `496bcfd` ⇒ PR vào `main` ⇒ CI xanh ⇒ tag (không dời `v1.0.9` đang có nếu đã có máy dùng `--tag v1.0.9`; nếu chưa ai dùng thì xoá tag đó và tạo lại sau merge, ghi rõ trong CHANGELOG).
2. Node.js Manager chuyển sang `feat/nodejs-manager`. Sửa P0-1..3, P1-1..3, thêm test hành vi (ít nhất một test chạy helper thật trên CI hoặc VM). Thẩm định lại trước khi merge; khi merge thì để sau cờ, mặc định tắt.
3. Áp dụng quy tắc tự động đã đề xuất ở lần 14 (mục 6): CI chặn `EnvironmentFile=` trỏ vào `/home`, chặn `mariadb.drop_database` / `create_database_credentials` ngoài `databases.py` + `teardown.py`. Họ lỗi "tính năng mới bỏ qua lớp kiểm tra có sẵn" đã lặp ở Laravel và nay ở Node.js.

**Tiêu chí đóng lần 15:** bản phát hành tiếp theo trên `main` không chứa `nodejs_manager` **hoặc** mọi mục P0/P1 ở trên đã đóng, có test hành vi, CI xanh trên PR.
