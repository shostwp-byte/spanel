# Thẩm định SPanel — Lần 21: `main` sau PR #35 (sửa N1–N7 của lần 20)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** `origin/main` @ `db15ef7` (merge PR #35 `fix/round20`, 26/11/2026).
- **Phạm vi:** `389e47b..db15ef7`, gồm 7 commit sửa N1–N7 và 1 merge. 9 file, +937 / −113.
- **Quy trình:** đi qua PR, CI xanh (run `45783464905`). Không có force-push: `main` tiến thẳng từ `389e47b` lên `db15ef7`. Chưa có tag mới, `v1.0.11` vẫn ở `08ff668`.
- **Kiểm chứng:**
  - Đọc toàn bộ diff `nodejs_manager.py`, `websites.py`, `users.py`, `mariadb.py`, `resource_monitor.py`, `spanel-helper.sh` và test round 20.
  - Lần theo: mọi chỗ lấy khoá theo site, các đường xoá website (kể cả `delete_user`), vòng đời session DB khi endpoint ném lỗi, đường gỡ chế độ chống tấn công, mọi chỗ gọi `prepare_nodejs_app_as_user`.
  - `bash -n` helper: đạt.
  - **Chưa chạy pytest** tại máy (thiếu Python/fastapi). **Chưa thử trên VPS.**
- **Ngày lập:** 26/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**PR #35 đóng được 6/7 mục của lần 20.**

- **Sửa đúng gốc:**
  - N1: các thao tác trên website giờ **lấy chính khoá theo site** thay vì chỉ đọc trạng thái.
  - N2: đổi mật khẩu DB giữ được mức hạn chế. Bỏ mức sàn theo host. Có test chạy trên MariaDB thật.
  - N5: tạo scope lá trước khi đưa tiến trình vào, có ghi log khi lỗi.
  - N6: dùng file dấu `.applied` theo digest. Node.js restart khi `.bin` được sửa.
  - N7: bảng mặc định dựng từ default của cột.
  - N3: có cảnh báo về gia hạn.
- **Chưa đạt: N4.** Bản sửa chỉ đổi giá trị trên object trong bộ nhớ, rồi ném `HTTPException`. Session không bao giờ commit, nên **DB vẫn trỏ về đường dẫn cũ** trong khi file đã nằm ở đường dẫn mới. Test chỉ kiểm object trong bộ nhớ nên không bắt được (R1).

**Cần sửa trước khi gắn tag `v1.0.12`:** R1 (P2). R2 và R3 là P3, có thể đi cùng bản vá.

---

## 1. Đối chiếu lần 20

| Mục | Kết luận | Ghi chú |
|---|---|---|
| N1 Khoá Node.js khi xoá / đổi tên / chuyển chủ | **Đạt** | `delete_website`, `_rename_single_website`, `transfer_website_owner` bọc trong `site_operation_lock`. Đổi chủ trong `update_website` lấy khoá và trả trong `finally`. Khoá không lồng nhau: đổi tên và đổi chủ trong cùng request lấy khoá lần lượt. `_get_site_operation_status` coi khoá đang giữ là "running" kể cả khi đã có file trạng thái cũ. **Còn sót:** `delete_user` (R2) |
| N2 Đổi mật khẩu gỡ mức hạn chế | **Đạt** | `preserve_existing=True` khi đổi mật khẩu và khi gắn user có sẵn. Đọc `max_user_connections` / `max_statement_time` hiện có trong `mysql.global_priv` rồi lấy mức chặt hơn. Gỡ chế độ chống tấn công (`resource_monitor.py:345`) vẫn đặt lại 25/15 với `preserve_existing=False`, nên không bị kẹt ở mức thấp. Chia đều không sàn: 8 ⇒ 3/host (tổng 9), 25 ⇒ 9/host (tổng 27). Xem ghi chú R4 |
| N3 Gia hạn LE sau khi đổi tên | **Đạt (mức cảnh báo)** | Trả cảnh báo trong `warnings`. Chưa tự xin cert cho tên mới, chấp nhận được |
| N4 Rollback đổi chủ | **Chưa đạt** | Xem R1 |
| N5 Jail không vào cgroup account | **Đạt** | `ensure_tenant_cgroup_scope` tạo slice và scope, áp limits từ `limits/<user>.env`, ghi log khi tạo hoặc ghi `cgroup.procs` thất bại. Bỏ nhánh ghi thẳng vào slice (vốn luôn lỗi `EBUSY`). `classify_all_tenant_workers` dùng chung hàm này |
| N6 `--if-changed` bỏ qua lần áp lỗi | **Đạt** | Chỉ trả `unchanged=1` khi digest trùng với `.applied`. Xoá dấu trước khi áp, chỉ ghi lại khi `daemon-reload`, `start` và `set-property` cùng thành công. `prepare_nodejs_app_as_user` trả 0 khi có sửa `.bin`. Chỉ có một chỗ gọi, nằm trong `if`, nên `set -e` không làm dừng script. Ghi chú nhỏ ở R3 |
| N7 Bảng mặc định | **Đạt** | Dựng từ `User.__table__.columns[...].default`. Test so từng trường |

---

## 2. Phát hiện

### R1 — P2. Rollback đổi chủ khi chuyển file về thất bại: DB không được lưu
`backend/app/api/websites.py`, `update_website`, nhánh `except (RuntimeError, ValueError, HTTPException)` (khoảng dòng 1565–1617)

Khi `move_site_runtime(new → old)` thất bại, hoặc khi `old_linux_user` rỗng, code gán `website.root_path = new_root_path` và `website.linux_user = new_linux_user`, rồi **ném `HTTPException` ngay**.

- `get_db` (`core/database.py:40`) chỉ `close()` session, không commit. Không có `db.commit()` nào trong nhánh này. Vì vậy các gán trên bị bỏ khi đóng session. **Trong DB, `root_path` và `linux_user` vẫn là giá trị cũ**, còn file đã nằm ở `/home/<chủ mới>/…`.
- Bản sửa có bỏ được việc viết lại vhost về đường dẫn cũ, nhưng tình trạng lệch giữa DB và đĩa của N4 vẫn còn nguyên. Các lần sync sau (worker, WAF, backup, file manager) đọc DB sẽ trỏ vào thư mục không còn tồn tại.
- `test_n4_…` chỉ kiểm `site.root_path` trên object trong bộ nhớ, cùng session, chưa rollback. Vì vậy test xanh dù hành vi thật sai.
- **Lưu ý khi sửa:** nếu chỉ thêm `db.commit()` thì sẽ ghi xuống tổ hợp `owner_id` = chủ cũ, `linux_user` / `root_path` = của chủ mới. Lúc đó chủ cũ quản lý một site chạy bằng user Linux của chủ mới, nằm trong home của chủ mới, tức là lệch quyền giữa hai tenant.
- **Đề xuất:**
  - Trong nhánh "chuyển về thất bại", đặt **cả** `owner_id` (và `NodejsInstance.owner_id`) theo chủ mới, vì file đã thuộc chủ mới. Hoặc đánh dấu site ở trạng thái cần sửa tay và chặn thao tác.
  - Sau đó `db.commit()` trước khi ném lỗi.
  - Sửa test: dùng session mới (hoặc `db.rollback()` / `expire_all()`), rồi đọc lại từ DB.

### R2 — P3. `delete_user` xoá website mà không lấy khoá theo site
`backend/app/api/users.py`: `delete_user` (dòng 826) → `_delete_owned_website` (dòng 358)

N1 đã đóng cho xoá website đơn lẻ. Nhưng khi xoá cả account, mỗi site đi qua `_delete_owned_website`: xoá DB, vhost, WAF, `wordpress.delete_wordpress(root_path)` và các dòng `NodejsInstance`, mà **không lấy `site_operation_lock`**.

- **Tình huống lỗi:** reseller hoặc admin xoá account trong lúc khách đang `npm install` hay git-deploy chạy nền. Thư mục bị xoá ngay dưới tiến trình. Tác vụ nền xong sau đó có thể ghi lại file trạng thái, unit hoặc thư mục cho một site đã bị xoá.
- **Đề xuất:** lấy khoá của mọi site trước khi xoá. Nếu có site đang bận thì trả 409 và chưa xoá gì. Hoặc lấy khoá từng site trong vòng lặp.

### R3 — P3 (ghi chú). Dấu `.applied` chưa phản ánh hết các bước
`spanel-helper.sh` `user-limits-apply`

`limits_apply_ok` chỉ theo dõi `daemon-reload`, `start` và `set-property`. Pool PHP-FPM, `apply_kernel_quota` và `classify_all_tenant_workers` nằm ngoài. Bước nào làm script dừng (do `set -e`) thì dấu không được ghi, nên vẫn được áp lại. Nhưng bước nào tự nuốt lỗi bên trong thì vẫn được ghi dấu là "đã áp". Mức độ thấp hơn nhiều so với N6 ban đầu. Có thể để như hiện tại, chỉ nên ghi rõ trong comment.

### R4 — P3 (vận hành). Mức mặc định 9 kết nối cho mỗi host
`mariadb.py` `enforce_db_user_resource_limits`

Sau khi bỏ mức sàn, mặc định 25 thành `ceil(25/3)` = **9 kết nối trên mỗi host**. Lần 18 là 10, trước đó là 25. PHP / WordPress gần như chỉ kết nối qua `localhost` (socket), nên trên thực tế một site chỉ còn 9 kết nối DB đồng thời. Với pool PHP-FPM có `pm.max_children` lớn hơn 9, lúc đông khách sẽ gặp `ERROR 1226 (max_user_connections)`.

- Đây không phải lỗi bảo mật, nhưng nên kiểm `max_children` mặc định của pool.
- Gợi ý của lần 20 vẫn còn giá trị: dồn phần lớn ngân sách về `localhost`, và để `127.0.0.1` / `::1` ở mức nhỏ.

---

## 3. Đề xuất cho bản vá `v1.0.12`

1. **R1:** lưu trạng thái đúng xuống DB khi rollback đổi chủ thất bại, không để lệch `owner_id` / `linux_user`. Sửa test để đọc lại từ DB.
2. **R2:** `delete_user` lấy khoá theo site trước khi xoá.
3. **R4:** xem lại cách chia kết nối theo host so với `pm.max_children`.
4. R3: tuỳ chọn.
