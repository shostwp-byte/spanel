# Kế hoạch sửa Node.js Manager — đi kèm Thẩm định lần 15

- **Repo:** `github.com/shostwp-byte/spanel`
- **Gốc:** nhánh `1.0.9` @ `011819c` (tag `v1.0.9`). Mọi số dòng dưới đây tính trên commit này.
- **Đi kèm:** `THAM_DINH_DOC_LAP_SPANEL_LAN15.md` (mục 1–3). File này chỉ nói **làm thế nào** để đóng các mục Node.js ở lần 15; lý do và cơ chế lỗi xem ở bản thẩm định.
- **Ngày lập:** 11/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Tóm tắt

| Giai đoạn | Nhánh / PR | Nội dung | Điều kiện xong |
|---|---|---|---|
| 0 | `release/1.0.9` → `main` | Phát hành 1.0.9 **không có** Node.js Manager | CI xanh, `main` = tag |
| 1 | `feat/nodejs-manager` · PR A | 3 P0 + P1-1, P1-2 + 2 lỗ hổng mới (mục 1.6, 1.7) | Test hành vi cho từng mục, CI xanh |
| 2 | PR B | Webhook (P1-3) | Như trên |
| 3 | PR C | State sang bảng có migration, cổng, các P2 | Migration chạy được trên bản cài cũ |
| 4 | PR D | Test helper chạy thật trên CI, cờ bật/tắt, thẩm định lần 16 | Job CI mới xanh |

Thứ tự trong PR A là bắt buộc: **P0-1 phải vào trước P1-1**. P1-1 (tham số lệch) hiện đang làm unit systemd không bao giờ được ghi ra, nên che luôn P0-1. Sửa P1-1 trước là mở P0-1 ra trên máy khách.

---

## Giai đoạn 0 — Tách Node.js Manager khỏi bản phát hành

1. `git switch -c release/1.0.9 3bf2f6b`, cherry-pick `8fb6b75` (quota) và `496bcfd` (cache WordPress).
2. Sửa `VERSION`, `backend/app/core/version.py`, `CHANGELOG.md` về nội dung 1.0.9 không có Node.js.
3. PR vào `main`, chờ CI xanh, merge, rồi mới tạo tag.
4. Tag `v1.0.9` đang trỏ `011819c`: nếu chưa máy nào cài bằng `--tag v1.0.9` thì xoá và tạo lại sau merge. Nếu đã có máy dùng thì giữ nguyên và phát hành bản này là `v1.0.10`. Ghi rõ lựa chọn trong CHANGELOG.
5. `git switch -c feat/nodejs-manager 011819c`: Node.js Manager được sửa trên nhánh này.

---

## Giai đoạn 1 — PR A: bảo mật và chạy được (một PR, mỗi mục một commit)

### 1.1 (P0-1) Không cho systemd đọc `.env` trong thư mục của khách bằng quyền root

**Chọn cách (b) của lần 15:** helper sao `.env` ra ngoài home khách, đọc **dưới quyền user khách**. Ứng dụng vẫn nhận biến môi trường như hiện tại, nên khách không phải thêm `dotenv`.

`installer/files/spanel-helper.sh`, hàm `sync_nodejs_worker_unit` (8106):

- Thêm hàm `nodejs_env_file <base>` → `${SPANEL_DATA_DIR}/nodejs/<base>.env`, giống `app_env_file` (7709) của Site Apps.
- Thêm hàm `copy_site_env_as_user <user> <app_target> <dest>`:
  - Đọc bằng `runuser -u "$user" -- head -c 65536 -- "${app_target}/.env"`. Khách chỉ đọc được những gì user đó đọc được, nên symlink tới `/opt/spanel/backend/.env` hay `/root/.my.cnf` sẽ không đọc được.
  - Lọc từng dòng: bỏ dòng trống và dòng `#`, bỏ tiền tố `export `, chỉ giữ dòng khớp `^[A-Za-z_][A-Za-z0-9_]*=`. Không dùng `write_app_env_file` (8613) vì hàm đó từ chối dòng chú thích, mà `.env` thật thường có.
  - Ghi vào file tạm root 0600 trong `${SPANEL_DATA_DIR}/nodejs/` (thư mục root 0700), rồi `mv`.
  - Không có `.env` hoặc đọc lỗi thì ghi file rỗng, không `deny`.
- Trong unit (8175): `EnvironmentFile=-${app_target}/.env` → `EnvironmentFile=-$(nodejs_env_file "$base")`.
- Các dòng `Environment=PORT/HOST/NODE_ENV` giữ **sau** `EnvironmentFile=` để panel luôn thắng nếu `.env` cũng đặt `PORT`.
- `delete_nodejs_worker_units` (8078) và `delete_nodejs_worker_units_for_user` (8089): xoá cả file env tương ứng.

Python không đổi gì: các đường đã gọi `nodejs-worker-sync` sau khi lưu `.env` (2421), deploy (2619), restart (2219) sẽ tự sao lại file.

**Test:**
- `git grep -nE 'EnvironmentFile=.*(app_target|/home)' -- installer` rỗng (đưa thành bước CI, xem 4.2).
- Test helper (giai đoạn 4): `.env` là symlink tới một file root 0600 chứa `LEAK=1` ⇒ sau `nodejs-worker-sync`, `/proc/<MainPID>/environ` không có `LEAK`.

### 1.2 (P1-1) Thống nhất thứ tự tham số `nodejs-worker-sync`

- Giữ thứ tự của helper (usage ở 13583 là hợp đồng): `user, site_root, app_dir, node_major, port, memory_mb, start_mode, start_arg, node_env, enabled`.
- `nodejs_manager.py:836–851`: tách phần dựng danh sách ra hàm `_worker_sync_helper_args(...) -> List[str]` để test gọi được trực tiếp, và sửa thứ tự cho khớp.
- **Ghi chú sửa lại lần 15:** lúc cài, `_sync_nodejs_worker_unit` được gọi với `strict=False` (2126), nên cài đặt **không** trả 500 như bản thẩm định ghi: nó trả 200 "Đã triển khai…" trong khi ứng dụng không chạy. Chỉ nút Start/Restart (`strict=True`, 2219) mới báo lỗi. Sửa: lúc cài dùng `strict=True`, lỗi thì hoàn tác (xem 1.4).

**Test:** mock `shell.privileged`, gọi `POST /install` qua `TestClient` với `command_dry_run=False`, bắt `helper_args` của `nodejs-worker-sync`, so từng vị trí với dòng usage lấy bằng cách chạy `bash installer/files/spanel-helper.sh nodejs-worker-sync` thiếu tham số (helper `deny` trước khi làm gì cần root). Không grep file.

### 1.3 (P1-2) Khoá trả về của `nodejs-worker-status`

- Helper `status_nodejs_worker_unit` (8222): in thêm `main_pid=`, `memory_current_bytes=`, `restarts=` (`systemctl show -p NRestarts --value`). Giữ `pid=`, `memory_bytes=` một bản để không vỡ gì khác.
- Đọc `port`, `node_major`, `start_mode`, `start_arg`, `memory_mb` từ unit thật thì tốn công; trước mắt Python ghi rõ các trường này lấy từ state (`_query_nodejs_worker_status`, 920–928) và bỏ phần đọc `kv.get("port")`… để không ai tưởng đó là số liệu sống.

**Test:** chạy hàm in của helper với `systemctl` giả (PATH trỏ tới script giả) và parse bằng chính `_parse_kv_output`.

### 1.4 (P0-2) Tạo database: làm theo đường Laravel Manager đã sửa

Laravel Manager sau lần 13–14 đã có khuôn đúng (`laravel_manager.py:2162–2330`); Node.js Manager chép lại khuôn đó, không tự viết.

`NodejsInstallRequest` (1724):
- Bỏ `db_name`, `db_user`, `db_password`, `create_database`.
- Thêm `db_mode: Literal["none", "new", "existing"] = "none"` và `existing_database_id: Optional[int]`.

`install_nodejs_instance` (1943):
- `new`: `enforce_user_database_limit(db, site_owner)` rồi `mariadb.create_database(seed, prefix="nd")`. Tên DB và user do panel sinh, khách không chọn được.
- `existing`: chỉ nhận `DatabaseAccount` có `owner_id == site.owner_id`; khác chủ ⇒ 403. Gắn `website_id = site.id`, nhớ giá trị cũ để hoàn tác.
- Lưu `db_account_id` vào state (không lưu `db_name`/`db_user` làm khoá nữa).
- Hàm `_rollback_failed_install` như Laravel: lỗi ở bất kỳ bước nào sau khi tạo DB (git clone, ghi file, `nodejs-worker-sync` strict) ⇒ `teardown.drop_database_record` cho đúng bản ghi vừa tạo, trả `website_id` cũ cho DB dùng lại, khôi phục `app_type` / `nginx_custom` của site.
- Dời `_purge_app_dir_contents` (1955) xuống **sau** các bước kiểm tra (DB, cổng, hạn mức gói ở 1.6) để request bị từ chối không xoá file của khách.

`frontend/src/pages/NodejsManager.jsx`: thay ô nhập tên DB/user/mật khẩu (2434–2470, 423–426, 472–475) bằng lựa chọn "Không / Tạo mới / Dùng database có sẵn" + danh sách database của chính khách (`GET /api/databases`). Mật khẩu chỉ hiện một lần trong phần `credentials` trả về.

Phòng thủ thêm ở `mariadb.py:184` (commit riêng, cùng PR): `create_database_credentials(..., allow_existing_database=False)` từ chối khi database đã có trên MariaDB. Trước khi đổi, liệt kê mọi nơi gọi (`databases.py`, `provisioning.py`, `websites.py`, các đường khôi phục backup và nhập DirectAdmin/cPanel) và bật `allow_existing_database=True` cho đúng các đường khôi phục.

**Test (TestClient, mock `mariadb._run_sql`):**
- User B cài với `db_mode=existing`, `existing_database_id` = DB của user A ⇒ 403, không có câu `GRANT` nào chạy.
- Gửi kèm trường cũ `db_name=<db của A>` ⇒ 422 (Pydantic `extra="forbid"`) hoặc bị bỏ qua; không có `GRANT` lên DB của A.
- Gói đã hết hạn mức database ⇒ 403/409, không tạo gì.
- Cài lỗi ở bước `nodejs-worker-sync` ⇒ DB vừa tạo bị xoá, bản ghi `DatabaseAccount` không còn.

### 1.5 (P0-3) Gỡ cài đặt chỉ xoá database do chính Node.js Manager gắn

`remove_nodejs_instance` (2803):
- Bỏ hẳn nhánh `elif db_user: mariadb.drop_database(...)` (2836–2837).
- Chỉ xoá khi `drop_database=true` **và** state có `db_account_id` **và** bản ghi đó có `owner_id == site.owner_id` **và** `website_id == site.id`. Xoá bằng `teardown.drop_database_record`.
- Không đọc tên DB từ `.env` hay từ state cho việc xoá.

`update_nodejs_env` (2368): bỏ việc ghi `db_name` / `db_user` từ `.env` vào state (2402–2405). Giao diện muốn hiện tên DB thì lấy từ `DatabaseAccount` qua `db_account_id`.

Migration state cũ (bản 1.0.9 đã có trên máy nào thì có state dạng cũ): lần đầu đọc, nếu instance có `db_name` mà chưa có `db_account_id` thì tìm `DatabaseAccount` khớp `db_name` + `owner_id` + `website_id == site.id`; không khớp thì để trống (gỡ sẽ không xoá DB, chỉ báo khách xoá tay ở mục Databases).

**Test:**
- `git grep -n "mariadb.drop_database" -- backend/app/api/nodejs_manager.py` rỗng.
- `.env` đặt `DB_DATABASE=<db của user A>` rồi user B gỡ với `drop_database=true` ⇒ không có `DROP` nào.
- Cùng chủ: `.env` trỏ DB WordPress của chính khách (khác `website_id`) ⇒ cũng không xoá.

**Ghi chú cho Laravel:** `laravel_manager.py:3600–3617` vẫn chỉ so `owner_id`, và lấy tên DB từ `.env` khi state trống. Khách tự xoá nhầm DB khác của chính mình được. Không vượt ranh giới tài khoản nên không phải P0, nhưng nên sửa cùng kiểu (thêm điều kiện `website_id == site.id`) trong một PR nhỏ riêng.

### 1.6 (mới, P1) Node.js Manager bỏ qua hạn mức gói về ứng dụng

Lần 15 chưa ghi mục này. Site Apps kiểm `node_apps_limit` của gói (`site_apps.ensure_app_quota`, `site_apps.py:228`; gói đặt 0 nghĩa là **không được chạy ứng dụng**) và trần RAM `node_app_memory_mb` (`memory_ceiling_for`, 218). Node.js Manager không gọi hàm nào trong hai hàm đó: `memory_mb` nhận tới 16.384 MB (1734, 816), và khách trên gói không có quyền ứng dụng vẫn chạy được tiến trình thường trú.

- Lúc cài và lúc `configure` (`POST /process`): gọi `ensure_app_quota`, với số ứng dụng = `SiteApp` + số instance Node.js chưa `detached` của chủ site (sửa `count_apps_for_owner` để đếm cả hai).
- `memory_mb` kẹp bằng `site_apps.validate_memory_mb(value, memory_ceiling_for(owner))`.
- Helper: `require_app_memory` giữ nguyên làm chốt cuối.
- Menu: giao diện vốn ẩn mục ứng dụng sau `appsFeatureEnabled = applicationAddonInstalled && (admin || siteApps.limit > 0)` (`App.jsx:1689`). Commit `4e57eb9` đã đưa Node.js ra khỏi cổng này, để mọi End User đều thấy. Đưa Node.js trở lại sau `appsFeatureEnabled`, và phía API kiểm cùng điều kiện (addon Application đã cài + hạn mức gói > 0), vì ẩn menu không chặn được gọi API trực tiếp.

**Test:** gói `node_apps_limit=0` ⇒ cài bị 403; gói trần 512 MB, xin 4096 ⇒ 400 hoặc bị kẹp về 512 (chọn một, test đúng cái đã chọn).

### 1.7 (mới, P1) End User cài được Node runtime bằng root

`POST /node-versions/install` (1918) chỉ qua `_require_end_user_nodejs_access`, nghĩa là **mọi End User** gọi được `site_apps.install_node`: tải và giải nén Node từ nodejs.org bằng root. Cùng họ với P2-2 của lần 15 (`terminal-exec --node-version`), nhưng ở đây là endpoint gọi thẳng.

- Endpoint này chỉ cho admin (`ensure_role(..., Role.admin)`), hoặc chuyển sang trang quản trị Node runtime đã có.
- Helper: `resolve_or_install_node_bin_dir` (8046) không tự cài nữa; đổi thành `resolve_node_bin_dir` + `deny` nếu chưa có (đóng luôn P2-1 "rơi về bản Node khác" và P2-2). Ở `terminal-exec` (13154) cũng đổi sang hàm chỉ tìm, không cài.
- Giao diện End User: chỉ cho chọn các bản đã cài (`GET /node-versions`), bản chưa cài hiện "Liên hệ quản trị".

**Test:** End User gọi `POST /node-versions/install` ⇒ 403. `nodejs-worker-sync` với major chưa cài ⇒ helper `deny`, Python trả lỗi rõ ràng, không có unit nào được ghi.

---

## Giai đoạn 2 — PR B: webhook (P1-3)

Site Apps đã có webhook đúng cách (`site_apps.py:594`): token theo app, bắt buộc chữ ký `X-Hub-Signature-256` hoặc `X-Gitlab-Token`, bỏ qua sự kiện không phải push. Node.js Manager dùng lại các hàm đó thay vì tự làm.

1. **Mặc định tắt:** `saved.get("webhook_enabled", True)` (1710, 2696) → `False`. Instance cũ chưa từng bật thì coi như tắt.
2. **Token:** bỏ token HMAC cố định theo `SECRET_KEY` (`_webhook_token_for`, 1556). Khi khách bật mới sinh `secrets.token_urlsafe(32)`, lưu mã hoá (`encrypt`), hiện một lần kèm secret ký.
3. **Chữ ký:** endpoint công khai (2685) đọc `raw_body`, bắt buộc một trong hai header như Site Apps; bỏ qua `ping` và sự kiện khác `push`; chỉ deploy khi nhánh được push trùng `git_branch`.
4. **Chạy nền + khoá theo site:** trả `202` ngay, deploy chạy trong `BackgroundTasks`/hàng đợi sẵn có của panel. Khoá theo `website_id` (khoá file hoặc dòng DB, không dùng `threading.Lock` vì có thể nhiều worker); đang deploy thì trả `409`. Nút "Deploy" trong giao diện (`POST /git-deploy`, 2643) dùng cùng khoá.
5. **Giới hạn tần suất:** tối đa N lần / phút / site; dùng limiter đang có của API nếu có, không thì bảng đếm đơn giản.

**Test:** gọi webhook không có header chữ ký ⇒ 401; chữ ký sai ⇒ 403; hai request song song ⇒ một 202, một 409; instance chưa bật ⇒ 403.

---

## Giai đoạn 3 — PR C: state, cổng và các P2

### 3.1 State sang bảng có migration (P2-3)

- Alembic migration tạo `nodejs_instances`: `website_id` (PK, FK `websites.id` ON DELETE CASCADE), `owner_id`, `subpath`, `node_major`, `start_mode`, `start_arg`, `port` (**UNIQUE**), `memory_mb`, `node_env`, `process_enabled`, `db_account_id` (FK `database_accounts.id` ON DELETE SET NULL), `git_repo`, `git_branch`, `git_build_script`, `webhook_enabled`, `webhook_token` (đã mã hoá), `installed_at`, `detached`. Log npm để ở bảng phụ hoặc giữ JSON riêng, không trộn vào bảng chính.
- Bước dữ liệu trong cùng migration: đọc khối JSON cũ (bảng tạo lúc chạy ở 284 / file `_toolkit_state_path`), chép sang bảng mới, giữ bản JSON cũ để lùi lại được.
- Bỏ `CREATE TABLE IF NOT EXISTS` lúc chạy (284) và `_NODEJS_STATE_LOCK` (54).
- Cấp cổng: chèn dòng với `port` trong transaction; trùng thì `IntegrityError` ⇒ thử cổng kế. Hết chuyện hai worker cấp trùng cổng.
- `site_apps.reserved_ports` / `app_port_for_website` (phần sửa ở `00244db`) đọc bảng mới thay vì import vòng sang `nodejs_manager`.

### 3.2 Các P2 còn lại

- **P2-4 cổng loopback không gắn user:** ghi nhận, không sửa trong vòng này (có sẵn ở Site Apps). Ghi vào lộ trình dài hạn: unix socket trong thư mục user hoặc socket activation, làm chung cho Site Apps và Node.js Manager.
- **P2-5 tên nhánh bắt đầu bằng `-`:** `GIT_BRANCH_RE` (68) → `^(?!-)[a-zA-Z0-9._/-]{1,80}$`, thêm từ chối `..` và `//`. Lệnh pull (2554) đổi thành `git pull --ff-only origin -- <branch>` hoặc `git fetch origin <branch>` + `git reset --hard FETCH_HEAD`; chọn một và ghi lý do.
- **P2-6 test grep chuỗi:** xoá `test_menu_order_nodejs_before_laravel` (83) và `test_helper_verbs_present_in_spanel_helper` (97). Thay bằng test hợp đồng helper ở 1.2 và test API: End User trên gói `node_apps_limit=0` gọi `GET /api/nodejs-manager/instances` ⇒ 403. Frontend chưa có bộ test chạy mã (các test menu hiện có ở `tests/test_frontend_chunk_recovery.py` cũng đọc chuỗi); thứ tự menu không đáng một test grep, bỏ là được.

---

## Giai đoạn 4 — PR D: test chạy thật, cờ bật/tắt, thẩm định lại

### 4.1 Job CI chạy helper thật

Lỗi P1-1 và P1-2 lọt qua vì mọi test chạy `command_dry_run`. Thêm job `helper-integration` trong `.github/workflows/ci.yml` (runner `ubuntu-latest` có systemd và `sudo`):

1. Cài Node 22 vào `/opt/spanel/node/22` (bản cố định, có checksum).
2. Tạo user `spaneltest` (uid ≥ 1000), thư mục site theo đúng bố cục `HOME_ROOT`, một `server.js` nghe `PORT`.
3. `sudo bash installer/files/spanel-helper.sh nodejs-worker-sync …` với thứ tự tham số lấy từ `_worker_sync_helper_args` của Python (chạy Python in ra), rồi `curl 127.0.0.1:<port>` phải trả 200.
4. Kiểm tra 1.1: `.env` symlink tới file root 0600 ⇒ biến không có trong `/proc/<pid>/environ`.
5. `nodejs-worker-status` ⇒ parse bằng `_parse_kv_output`, `main_pid` > 0.
6. `nodejs-worker-delete` ⇒ unit và file env đều biến mất.

### 4.2 Chốt tự động (theo lần 14 mục 6)

Thêm bước CI fail nếu:
- `EnvironmentFile=` trong helper trỏ vào `/home` hoặc biến đường dẫn của khách.
- `mariadb.drop_database` hoặc `create_database_credentials` xuất hiện ngoài `databases.py`, `teardown.py`, `mariadb.py`, `db_backends.py` và các đường khôi phục đã liệt kê ở 1.4.
- Endpoint mới trong `backend/app/api/` không có `Depends(get_current_user)` mà không nằm trong danh sách endpoint công khai được phép (webhook Site Apps, webhook Node.js).

### 4.3 Cờ bật/tắt

- Cổng theo gói (1.6) quyết định **ai** được dùng. Thêm một cờ cấp máy `nodejs_manager_enabled` (mặc định `false`) trong cài đặt panel để quyết định **khi nào** bật: router `nodejs_manager` kiểm cờ ở dependency, tắt thì 404; menu ẩn mục Node.js khi cờ tắt hoặc `appsFeatureEnabled` sai.
- Bật trên máy thử trước, chạy vòng đời đầy đủ trên VPS thật (cài từ template, cài từ Git, `.env`, restart, deploy, gỡ có xoá DB), rồi mới bật mặc định ở bản sau.

### 4.4 Thẩm định lần 16

Mở PR `feat/nodejs-manager` → `main` sau khi A–D xong và CI xanh. Thẩm định lần 16 đọc PR đó, kể cả `NodejsManager.jsx` (lần 15 mới lướt).

---

## Danh sách kiểm tra đóng

- [ ] 1.0.9 trên `main` không chứa `nodejs_manager` (giai đoạn 0)
- [ ] 1.1 Unit không còn `EnvironmentFile` trong home khách; test symlink qua
- [ ] 1.2 Tham số `nodejs-worker-sync` khớp; cài lỗi thì hoàn tác
- [ ] 1.3 Trạng thái hiện PID / RAM / số lần restart thật
- [ ] 1.4 Không chọn được tên DB; DB có sẵn phải cùng chủ; có hạn mức gói; hoàn tác khi lỗi
- [ ] 1.5 Gỡ chỉ xoá DB theo `db_account_id` cùng chủ, cùng site
- [ ] 1.6 Hạn mức `node_apps_limit`, trần RAM của gói và cổng addon được áp ở cả API lẫn menu
- [ ] 1.7 Chỉ admin cài Node runtime; helper không tự cài, không rơi về bản khác
- [ ] 2 Webhook mặc định tắt, có chữ ký, chạy nền có khoá
- [ ] 3.1 State trong bảng có migration, `port` UNIQUE
- [ ] 3.2 Tên nhánh Git chặn `-`; bỏ hai test grep chuỗi
- [ ] 4.1 Job `helper-integration` xanh
- [ ] 4.2 Chốt CI chống lặp lại họ lỗi này
- [ ] 4.3 Cờ `nodejs_manager_enabled` mặc định tắt

## Về lâu dài (sau 1.0.9)

Node.js Manager đang làm lại phần lớn những gì Site Apps đã có (bảng `site_apps`, cổng UNIQUE, file env ngoài home, hạn mức gói, webhook có chữ ký, verb helper `write_node_app_unit`), và mỗi lần làm lại đều bỏ sót một lớp kiểm tra. Hướng nên đi: Node.js Manager thành giao diện End User **trên** Site Apps (một `SiteApp` kind `node` gắn với website, thêm cột `website_id` + `subpath`), giữ các phần riêng (template, npm, kiểm tra bảo mật) ở tầng API. Không làm trong vòng này vì quá lớn; quyết định nên chốt trước khi viết tính năng ứng dụng kế tiếp (Python, Go…) để không lặp lại lần thứ ba.
