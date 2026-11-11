# Thẩm định SPanel — Lần 16: PR #27 `feat/nodejs-manager` (sửa Node.js Manager theo kế hoạch lần 15)

- **Repo:** `github.com/shostwp-byte/spanel`
- **PR:** https://github.com/shostwp-byte/spanel/pull/27 · `feat/nodejs-manager` @ `bf51341` → `main` @ `4fcd35b`
- **Phạm vi:** 18 commit (5 commit cũ của nhánh `1.0.9` được rebase lại + 13 commit sửa), 34 file, +8.778 / −111.
- **Đối chiếu với:** `KE_HOACH_SUA_NODEJS_MANAGER_LAN15.md` (mục 1.1 → 4.3) và `THAM_DINH_DOC_LAP_SPANEL_LAN15.md`.
- **Kiểm chứng:** đọc toàn bộ diff helper, các endpoint `install` / `process` / `env` / `remove` / webhook / git-deploy, lớp state mới, migration, diff `mariadb` / `db_backends` / `remote_mysql` / `site_apps` / `websites` / `teardown`, job CI mới. Kiểm số head Alembic (1 head: `nodejs_instances_table`). Thử `git fetch --depth 1 origin -- <branch>` + `reset --hard FETCH_HEAD` trên máy: chạy đúng. Đọc log CI của run `42974257155`. Chưa chạy trên VPS; chưa đọc kỹ `NodejsManager.jsx`.
- **CI:** 13 xanh, 1 bỏ qua (Large deletion guard, chỉ chạy khi push).
- **Ngày lập:** 11/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Ba lỗi P0 của lần 15 đã đóng, P1-1/P1-2/P1-3 đã đóng, hai lỗ hổng mới của kế hoạch (1.6, 1.7) đã đóng.** Phần lớn làm đúng kế hoạch, có rollback, có test hành vi, có job CI chạy systemd thật. Đây là PR tốt nhất của dòng tính năng ứng dụng tới nay.

**Còn 2 mục P1 nên sửa ngay trong PR này trước khi merge:**

1. **P1-A — "Dùng database có sẵn" chuyển database của site khác sang site Node.js ⇒ xoá site Node.js (mặc định) hoặc gỡ Node.js có tick "xoá DB" là mất database của site kia.** Cùng một chủ, nên không vượt ranh giới tài khoản, nhưng là mất dữ liệu thật và khách không có cách nào đoán trước. Laravel Manager có sẵn lỗi này.
2. **P1-B — Bài test CI chứng minh P0-1 không kiểm được gì:** lệnh đọc `/proc/<pid>/environ` bị `Permission denied` và test vẫn qua. Log CI xác nhận.

Cờ `nodejs_manager_enabled` mặc định tắt, nên merge xong cũng chưa máy khách nào bị ảnh hưởng. Nhưng P1-A và P1-B đều nhỏ, sửa trong PR này rẻ hơn mở PR mới. Các mục P2/P3 có thể để PR sau, **trước khi bật cờ**.

---

## 1. Đối chiếu kế hoạch lần 15

| Mục | Commit | Trạng thái | Ghi chú |
|---|---|---|---|
| Giai đoạn 0 — phát hành 1.0.9 không có Node.js | PR #26 | **Đạt** | `main` @ `4fcd35b` không chứa `nodejs_manager` |
| 1.1 P0-1 `.env` ngoài home, đọc dưới quyền user | `ec8a1ef` | **Đạt** (mã) / **chưa chứng minh** (CI) | Xem P1-B, P3-1 |
| 1.2 P1-1 thứ tự tham số + hoàn tác khi lỗi | `dfcc62e` | **Đạt** | Job CI dựng tham số từ `_worker_sync_helper_args` rồi chạy helper thật, `curl` được 200. Rollback thiếu xoá unit (P2-2) |
| 1.3 P1-2 khoá trạng thái | `847b126` | **Đạt** | Có thêm `NRestarts` |
| 1.4 P0-2 tạo DB | `8cc3500`, `47ecf5b` | **Đạt** | `extra="forbid"`, `db_mode`, tên do panel sinh, kiểm hạn mức, rollback. Rà toàn bộ nơi gọi `create_database_credentials`: các đường khôi phục (backup, DirectAdmin, phpMyAdmin SSO) giữ nguyên hành vi cũ |
| 1.5 P0-3 gỡ chỉ xoá DB theo `db_account_id` cùng chủ, cùng site | `d45d4b0` | **Đạt** về chữ, **lủng** qua chế độ "existing" | Xem P1-A. Laravel cũng được sửa cùng kiểu (bỏ đọc `.env`) |
| 1.6 hạn mức gói, trần RAM, cổng addon | `06e3963` | **Đạt** | Kiểm ở cả API (`_require_end_user_nodejs_access`, `install`, `process`) và menu |
| 1.7 chỉ admin cài Node; helper không tự cài | `503cd99` | **Đạt** | `terminal-exec --node-version` cũng `deny` nếu chưa có |
| 2 Webhook | `dc47662` | **Đạt** | Mặc định tắt, token ngẫu nhiên mã hoá, bắt buộc chữ ký, 202 + khoá theo site, giới hạn 6 lần/phút |
| 3.1 State sang bảng + migration | `8cec410` | **Đạt** | `port UNIQUE`, chép JSON cũ, bỏ import vòng. Vòng thử lại có lỗi nhỏ (P2-3) |
| 3.2 Tên nhánh, `git fetch -- <branch>` | `8cec410` | **Đạt** | |
| 4.1 Job `helper-integration` | `d9f774e` | **Đạt một phần** | Chạy thật, nhưng phần kiểm 1.1 không có tác dụng (P1-B) |
| 4.2 Chốt CI | `d9f774e` | **Đạt** | Chốt xác thực endpoint dùng AST, ổn cho mục đích chặn quên |
| 4.3 Cờ bật/tắt | `d9f774e` | **Đạt** | Router trả 404, menu ẩn |

---

## 2. P1 — sửa trong PR này

### P1-A. Chế độ "dùng database có sẵn" chuyển `website_id` của database ⇒ mất database của site khác

- **Vị trí:** `nodejs_manager.py`, `install_nodejs_instance`, nhánh `db_mode == "existing"`: `existing_check.website_id = site.id`. Không kiểm database đó đang gắn với site nào.
- **Hệ quả 1 — xoá website:** `websites.py:1360–1362` `delete_website(..., delete_database=True)` (mặc định là `True`) xoá `DatabaseAccount` có `website_id == website.id`. Khách có site WordPress `a.com` (DB `wp_a`) và site `b.com`. Cài Node.js lên `b.com`, chọn dùng lại `wp_a`, rồi xoá `b.com` ⇒ `DROP DATABASE wp_a`. Site `a.com` hỏng, và khi xoá `a.com` sau này panel cũng không còn biết DB nào là của nó.
- **Hệ quả 2 — gỡ Node.js:** `remove_nodejs_instance` với `drop_database=true` khớp đúng 3 điều kiện (`id`, `owner_id`, `website_id == site.id`, vì `website_id` vừa bị đổi) ⇒ xoá `wp_a`. Giao diện ghi "xoá database của ứng dụng", khách không nghĩ đó là DB WordPress.
- **Hệ quả 3 — gỡ không xoá DB:** `website_id` không được trả về site cũ, nên hệ quả 1 vẫn còn nguyên sau khi gỡ.
- **Laravel Manager:** cùng khuôn (`laravel_manager.py` ~2195–2215 và đoạn gán `existing_check.website_id = site.id`), có từ lần 13. Kế hoạch lần 15 bảo chép khuôn Laravel, nên lỗi được chép theo.
- **Sửa (cho cả Node.js và Laravel):**
  1. Không đổi `website_id` của DB dùng lại. Chỉ lưu `db_account_id` trong state, cộng một cờ `db_created_by_manager` (true khi `db_mode == "new"`).
  2. Gỡ cài đặt chỉ `DROP` khi `db_created_by_manager` là true. DB dùng lại thì chỉ bỏ liên kết.
  3. Nếu vẫn muốn gắn `website_id` (để trang Databases hiện đúng site): từ chối dùng lại DB đang có `website_id` khác `NULL` và khác site hiện tại, và trả `website_id` cũ khi gỡ.
- **Test:** chủ A có `wp_a` gắn site 1; cài Node.js lên site 2 với `existing_database_id = wp_a` ⇒ (a) `DELETE /api/websites/2` không chạy `DROP` nào lên `wp_a`; (b) gỡ Node.js với `drop_database=true` không `DROP` `wp_a`; (c) sau khi gỡ, `wp_a.website_id == 1`. Cùng bộ test cho Laravel.

### P1-B. Phần kiểm P0-1 trong job `helper-integration` luôn qua

- **Vị trí:** `.github/workflows/ci.yml`, bước "Exercise nodejs-worker-sync…":
  ```bash
  if sudo tr '\0' '\n' < "/proc/${MAIN_PID}/environ" | grep -q "ROOT_ONLY_SECRET"; then
  ```
- **Lỗi:** phép chuyển hướng `<` do shell của user `runner` thực hiện, không phải `sudo`. Tiến trình thuộc `spaneltest`, nên `runner` không đọc được `/proc/<pid>/environ`. Lệnh lỗi, `grep` nhận đầu vào rỗng, điều kiện `if` sai, test qua. Log run `42974257155`: `line 69: /proc/3141/environ: Permission denied`.
- **Hệ quả:** bằng chứng duy nhất chạy thật cho P0-1 thật ra không kiểm gì. Nếu ai đó trả lại `EnvironmentFile=${app_target}/.env`, job vẫn xanh.
- **Sửa:**
  ```bash
  ENVIRON="$(sudo cat "/proc/${MAIN_PID}/environ" | tr '\0' '\n')"
  grep -q '^PORT=21055$' <<<"$ENVIRON"            # đối chứng: đọc được thật
  ! grep -q 'ROOT_ONLY_SECRET' <<<"$ENVIRON"
  ```
  Thêm một ca đối chứng dương: `.env` thường có `APP_MARKER=ok` ⇒ phải có trong môi trường tiến trình. Hiện không có ca nào chứng minh việc sao `.env` *có* chạy, nên nếu `copy_site_env_as_user` hỏng hẳn (ghi file rỗng mọi lúc) test vẫn xanh.

---

## 3. P2 — sửa trước khi bật cờ

### P2-1. Xoá website mà không xoá file ⇒ tiến trình Node.js vẫn chạy, cổng bị cấp lại

`delete_website(..., delete_files=False)` xoá dòng `NodejsInstance` nhưng chỉ gọi `delete_site_runtime` (nơi helper `rm-site` dừng unit) khi `delete_files=True`. Unit `Restart=always` vẫn chạy và giữ cổng, trong khi panel coi cổng đã trống và có thể cấp cho ứng dụng khác ⇒ ứng dụng mới không bind được, hoặc khách cũ nhận lưu lượng của khách mới. Sửa: luôn gọi `nodejs-worker-delete` khi xoá website có `NodejsInstance`, không phụ thuộc `delete_files`.

### P2-2. Hoàn tác khi cài lỗi không xoá unit systemd

Helper ghi unit, `enable`, rồi mới `restart`. Nếu `restart` lỗi, helper thoát khác 0 ⇒ `_sync_nodejs_worker_unit(strict=True)` ném lỗi ⇒ `_rollback_failed_install` xoá file và state nhưng **không** gọi `_delete_nodejs_worker_unit`. Kết quả: unit đã `enable`, `Restart=always`, chạy lặp vô tận trên thư mục rỗng, cổng đã trả về kho. Sửa: thêm `_delete_nodejs_worker_unit(linux_user, site_root)` vào đầu `_rollback_failed_install`.

### P2-3. `_update_instance_state`: hết 8 lần thử thì im lặng không lưu

- Vòng `for attempt in range(8)` không có `else`. Lần thứ 8 vẫn `IntegrityError` thì thoát vòng và trả state như thường, người gọi tưởng đã lưu.
- Mọi `IntegrityError` đều được coi là trùng cổng, kể cả trùng khoá chính `website_id` (hai request cài song song cùng site) ⇒ đổi cổng rồi thử lại, vô ích.
- Sửa: `for … else: raise HTTPException(409)`; chỉ thử lại khi lỗi nằm trên ràng buộc `port` (kiểm tên ràng buộc trong `exc.orig`), còn lại trả 409 ngay.

---

## 4. P3 — ghi chú

1. **Sửa lại câu sai trong kế hoạch lần 15 (lỗi của bản kế hoạch, không phải của PR):** mục 1.1 ghi "`Environment=` đặt sau `EnvironmentFile=` để panel luôn thắng". Sai: theo `systemd.exec(5)`, giá trị từ `EnvironmentFile=` **đè** `Environment=` bất kể thứ tự. Khách sửa `PORT`/`HOST` trong `.env` là đè được giá trị panel đặt. Không phải lỗ hổng mới (mã của khách vốn tự chọn cổng và địa chỉ được), nhưng khách lỡ sửa `PORT` là Nginx trả 502 mà không rõ vì sao. Nếu muốn panel luôn thắng: `copy_site_env_as_user` bỏ các khoá `PORT`, `HOST`, `NODE_ENV`, `PATH`, `HOME`, `npm_config_cache`. Đã sửa câu này trong file kế hoạch.
2. **Test còn đọc chuỗi nguồn:** `test_p0_1_…` dùng regex trên `spanel-helper.sh` để so thứ tự tham số; `test_p0_3_…` kiểm `"mariadb.drop_database" not in nm_source`. Job CI chạy thật đã phủ thứ tự tham số, chốt CI đã phủ `drop_database`, nên hai đoạn này thừa. Không chặn merge.
3. **Webhook vẫn chạy khi gói đã bị tắt quyền ứng dụng:** endpoint công khai không gọi lại `app_limit_for(owner)`. Nên kiểm thêm để hạ gói là có tác dụng ngay.
4. **Khoá deploy hết hạn sau 900 giây,** trong khi pipeline có thể dài hơn (fetch 180 + npm install 300 + build + restart). Deploy chậm có thể bị request sau coi là khoá cũ và chạy chồng. Nên tăng lên ≥ tổng thời gian chờ tối đa, hoặc ghi PID và kiểm tiến trình còn sống.
5. **Git deploy bỏ qua `git_repo` mới nếu đã có `.git`:** đổi repo trong giao diện rồi bấm Deploy vẫn kéo từ `origin` cũ. Nên `git remote set-url origin <repo>` khi khác, hoặc khoá ô repo sau khi cài.
6. **Rebase kéo theo commit nâng VERSION cũ** (`ed62b90`: "bump release to v1.0.9") trong khi `main` đã là 1.0.9. Vô hại, nhưng merge xong nên kiểm `VERSION` và CHANGELOG ghi Node.js Manager vào bản kế (1.0.10), sau cờ.

---

## 5. Đề xuất

1. Sửa P1-A (Node.js **và** Laravel) và P1-B trong PR #27, push lên cùng nhánh, chờ CI xanh rồi merge.
2. PR kế tiếp: P2-1, P2-2, P2-3 và các ghi chú P3 muốn làm.
3. Bật `NODEJS_MANAGER_ENABLED=true` trên một VPS thử, chạy vòng đời đầy đủ (template, Git, `.env`, đổi cổng, webhook GitHub thật, gỡ có/không xoá DB, xoá website có/không xoá file), rồi mới bật mặc định.

**Tiêu chí đóng lần 16:** P1-A có test cho cả Node.js và Laravel; job `helper-integration` đọc được `/proc/<pid>/environ` (có đối chứng dương) và vẫn xanh; P2-1..3 đóng trước khi bật cờ.
