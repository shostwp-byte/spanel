# Thẩm định SPanel — Lần 20: `main` = tag `v1.0.11` (PR #32, #33, #34)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** `origin/main` @ `389e47b`. Tag `v1.0.11` trỏ đúng commit này.
- **Phạm vi:** `d7d1491..389e47b` (từ chỗ lần 18 dừng). Gồm 15 commit, 26 file, +2.127 / −297:
  - **PR #32** (`fix/round18`): sửa V1–V6 và M1–M8 của lần 18.
  - **PR #33**: phát hành `v1.0.11`.
  - **PR #34**: chỉ sửa README.
- **Quy trình:** mọi thay đổi đi qua PR, CI xanh trên cả 3 lần merge (run `44683843728`, `44685632889`, `44686355000`).
- **Kiểm chứng:**
  - Đọc diff backend (`mariadb.py`, `backup.py`, `nodejs_manager.py`, `websites.py`, `users.py`, `provisioning.py`, `resources.py`), `spanel-helper.sh` và `update.sh`.
  - Lần theo các chỗ gọi `enforce_db_user_resource_limits`, `ensure_no_active_nodejs_operation`, `copy_site_env_as_user` và trạng thái khoá Node.js.
  - **Chưa chạy pytest** tại máy (thiếu `fastapi`). **Chưa thử trên VPS.** Chưa đọc kỹ frontend (`NodejsManager.jsx`) và test.
- **Ngày lập:** 21/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**`v1.0.11` đóng phần lớn những gì lần 18 nêu.**

- V1 (khoá host loopback), V5 (restore giữ khoá), M2/M3 (trần tài nguyên reseller) và M6 (thu hồi khoá cũ an toàn) được sửa đúng gốc.
- Các thao tác ghi của Node.js Manager giờ đều đi qua cùng một khoá theo site.
- `update.sh` không còn restart mọi worker và chạy lại limits mỗi lần cập nhật.

**Còn 2 điểm P2 nên sửa trong bản vá kế tiếp:**

1. **N1:** chặn xoá / đổi tên / chuyển chủ website trong lúc Node.js đang chạy tác vụ có thể **bị bỏ qua**, nên M1 chưa kín ở phía `websites.py`.
2. **N2:** đổi mật khẩu DB giờ **đặt lại giới hạn kết nối về mặc định**, gỡ mất mức hạn chế mà bộ chống tấn công đã đặt. Thêm vào đó, cách chia giới hạn theo host làm mức hạn chế yếu hơn dự định.

Các điểm P3 còn lại không chặn phát hành.

---

## 1. Đối chiếu lần 18

| Mục | Trạng thái | Ghi chú |
|---|---|---|
| V1 | **Đạt** | `CREATE USER … ACCOUNT LOCK`, khoá trước khi GRANT. `GRANT` trước `REVOKE` nên tránh được ERROR 1141. Có thêm CI chạy MariaDB thật |
| V2 | **Đạt một phần** | `site-app-*` giờ chạy trong `run_in_tenant_home_jail`. Việc đưa vào cgroup của account có thể thất bại mà không báo (N5) |
| V3 | **Đạt** | `if-changed` cho unit worker và `user-limits-apply`. Có một chỗ hở nhỏ (N6) |
| V4 | **Đạt** | Nhánh lỗi của đổi tên, đổi chủ, chuyển chủ giờ báo lỗi rollback. Đổi chủ còn một chỗ (N4) |
| V5 | **Đạt** | `_restore_database_and_reapply_locks` khôi phục lại khoá ghi, `ACCOUNT LOCK` và giới hạn theo giờ trong `finally` |
| V6 | Chưa kiểm sâu | Có commit `77c7171` |
| M1 | **Đạt một phần** | 8 endpoint ghi dùng `@_serialized_site_operation`, còn `process`, `install`, `git-deploy` và webhook tự lấy khoá. Phía `websites.py` còn N1 |
| M2 | **Đạt** | `create_user`, provisioning tạo và đổi gói đều kiểm trần trên `_preview_user_limits` |
| M3 | **Đạt** | Chỉ kiểm các trường có trong payload, trừ khi đổi gói |
| M4 | **Đạt** | LE được giữ khi đổi tên. Có một rủi ro gia hạn (N3) |
| M5, M6 | **Đạt** | Ghi `.env` nằm trong khoá. Thu hồi khoá cũ có cờ `.reclaim` dùng `O_EXCL` |
| M7 | Chưa kiểm | Frontend |
| M8 | Theo commit | `8d8b693` sửa README / CHANGELOG |

---

## 2. Phát hiện mới

### N1 — P2. `ensure_no_active_nodejs_operation` bỏ sót khoá đang giữ
`backend/app/api/nodejs_manager.py` (`_get_site_operation_status`, `ensure_no_active_nodejs_operation`). Được gọi từ `websites.py`: `_rename_single_website`, `delete_website`, `transfer_website_owner`.

`_get_site_operation_status` chỉ dựa vào khoá khi **chưa có file trạng thái** (`elif not data and lock_live`). Khi đã có file trạng thái của một tác vụ nền trước đó (status `succeeded`/`failed`), hàm trả về file đó với `active` = false, kể cả khi khoá đang bị giữ.

Các thao tác đồng bộ dưới decorator **không ghi file trạng thái**: `npm` (`npm install` tới 300 s, `npm run` tới 900 s), `remove`, `detach`, `env:update`…

- **Tình huống lỗi:** site từng git-deploy một lần nên đã có file trạng thái. Khách bấm `npm install`, rồi admin xoá hoặc đổi tên website trong lúc npm còn chạy. Kiểm tra cho qua, thư mục bị xoá hoặc chuyển ngay dưới tiến trình npm, và có thể để lại unit hoặc file mồ côi.
- **Ngoài ra:** đây là kiểu "kiểm tra rồi mới làm". Hàm không lấy khoá, nên một deploy bắt đầu ngay sau khi kiểm tra vẫn chạy song song với việc xoá.
- **Đề xuất:** để `websites.py` lấy chính `_site_operation_lock(website.id, "website:delete" …)` trong suốt thao tác. Nếu không, ít nhất phải kiểm `lock_live` độc lập với file trạng thái.

### N2 — P2. Đổi mật khẩu DB gỡ mức hạn chế chống tấn công; cách chia theo host làm mức này yếu đi
`backend/app/services/mariadb.py`: `change_database_password` (dòng 427 trên `main`) và `enforce_db_user_resource_limits`.

- `change_database_password` giờ gọi `enforce_db_user_resource_limits(safe_user)` với giá trị mặc định (25 kết nối, 15 s). Khi đang bị tấn công, `resource_monitor` (dòng 297) hạ user đó xuống 8 kết nối / 3 s. Khách chỉ cần đổi mật khẩu DB là **gỡ được mức hạn chế đó**. `create_database_credentials` cũng đặt lại như vậy, nhưng chỗ đó đã có từ trước.
- Cách chia mới `max(per_host_floor, ceil(total/3))` cho mỗi host:
  - mức hạn chế 8 ⇒ 5 kết nối/host ⇒ **tổng 15**, gần gấp đôi mức dự định;
  - mặc định 25 ⇒ 10/host ⇒ tổng 30;
  - 5 ⇒ tổng 15.

  Comment ghi "close to total_conns", nhưng ở mức thấp, đúng lúc cần chặt nhất, thì tổng lại cao nhất so với mức đặt.
- **Đề xuất:**
  - Đọc giới hạn hiện có của user (`mysql.global_priv` / `max_user_connections`) rồi giữ nguyên, hoặc lưu trạng thái hạn chế vào DB của panel và áp lại.
  - Bỏ mức sàn theo host khi đang bị hạn chế. Nếu được, dồn kết nối về một host (`localhost`) thay vì chia đều.

### N3 — P3. Giữ LE khi đổi tên phụ thuộc vào việc domain cũ vẫn trỏ về máy này
`websites.py` `_rename_single_website` (nhánh `old_ssl_mode == "letsencrypt"`)

Site được chuyển sang `ssl_mode="shared"` và dùng lineage `/etc/letsencrypt/live/<old_domain>`. Lineage này gia hạn bằng webroot. Server mặc định `00-spanel-tools.conf` phục vụ ACME cho mọi host, nên **chỉ gia hạn được khi DNS của domain cũ vẫn trỏ về máy này**. Đổi tên thường kéo theo việc bỏ hoặc chuyển domain cũ. Khi đó certbot gia hạn thất bại và chứng chỉ của site mới hết hạn sau tối đa 90 ngày, mà không có cảnh báo riêng.

- **Đề xuất:** sau khi đổi tên, xin cert LE cho tên mới ở nền rồi chuyển site sang lineage mới, hoặc ít nhất cảnh báo trong kết quả trả về.

### N4 — P3. Rollback đổi chủ đặt `root_path` cũ vào DB trước khi chuyển file về
`websites.py` `update_website`, nhánh `except (RuntimeError, ValueError, HTTPException)`

`website.root_path = old_root_path` và `linux_user = old_linux_user` được gán **trước** khi gọi `move_site_runtime(new → old)`. Nếu việc chuyển về thất bại, hoặc `old_linux_user` rỗng (site cũ), DB trỏ về đường dẫn cũ trong khi file đang ở đường dẫn mới, và vhost cũng được viết lại theo đường dẫn cũ. `transfer_website_owner` làm đúng hơn: chỉ gán sau khi chuyển về thành công.

### N5 — P3. Jail của `site-app-*` / terminal có thể không vào được cgroup của account mà không báo
`spanel-helper.sh` `run_in_tenant_home_jail`, `terminal_jail`

Tiến trình chỉ vào `user-<uid>.slice/spanel.scope` nếu thư mục đó đã có. Thư mục này chỉ được tạo trong `classify_all_tenant_workers`. Nếu chưa có (account mới, chưa qua lần quét nào), code ghi vào `cgroup.procs` của chính slice. Slice không phải lá và đã bật controller, nên cgroup v2 từ chối (`EBUSY`), và lỗi bị nuốt bởi `|| true`. Khi đó `npm install` / `pip install` của khách chạy trong cgroup của `spanel-api.service`, không chịu giới hạn của account và chia bộ nhớ với API.

- **Đề xuất:** `mkdir -p` scope (và áp limits như `classify_all_tenant_workers` làm) trước khi ghi PID, rồi ghi log khi không vào được.

### N6 — P3. `--if-changed` bỏ qua cả việc áp lại khi lần áp trước thất bại giữa chừng
`spanel-helper.sh` `user-limits-apply`

File `limits/<user>.env` và `50-spanel-limits.conf` được ghi **trước** `daemon-reload`, `set-property`, pool PHP-FPM và `setquota`. Nếu lần chạy trước dừng sau khi ghi file (reload lỗi, setquota lỗi), lần `update.sh` sau thấy file trùng và thoát với `unchanged=1`, nên limits không bao giờ được áp lại tự động. Unit Node.js / Laravel không gặp chuyện này vì còn kiểm `is-active`.

Nhỏ hơn: với Node.js, `prepare_nodejs_app_as_user` có thể cài lại `node_modules` (V6) nhưng app không được restart, vì unit và `.env` không đổi.

### N7 — P3 (dọn code). `_DEFAULT_USER_RESOURCE_LIMITS` chép lại default của model `User`
`backend/app/api/users.py`

Bảng giá trị mặc định này phải luôn khớp với default cột trong `entities.User`. Nếu đổi default ở model mà quên bảng này, kiểm trần reseller sẽ kiểm trên số khác với số mà account thực sự nhận. Nên dựng giá trị từ `User.__table__.columns[...].default`, hoặc dùng chung một hằng.

---

## 3. Đề xuất cho bản vá `v1.0.12`

1. N1: `websites.py` lấy khoá theo site của Node.js thay vì chỉ đọc trạng thái.
2. N2: giữ giới hạn kết nối hiện có khi đổi mật khẩu hoặc tạo lại quyền, và xem lại mức sàn theo host.
3. N4, N5, N6: sửa nhỏ, mỗi mục một commit, kèm test hành vi.
4. N3: cảnh báo, hoặc xin cert mới ở nền sau khi đổi tên.
