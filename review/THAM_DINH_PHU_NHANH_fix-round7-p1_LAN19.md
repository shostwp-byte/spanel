# Thẩm định SPanel — Lần 19: rà lại nhánh `fix/round7-p1`

- **Bản thẩm định:** nhánh `fix/round7-p1` @ `2640cdb`. Gốc nhánh: `b98956d`.
- **Phạm vi:** `b98956d..2640cdb`, gồm 4 commit (`a2c1574`, `099debc`, `8ab4ba5`, `2640cdb`), 5 file. Không xét lại code 1.0.7 đã merge.
- **So với `main`:** `origin/main` @ `389e47b`. Nhánh **đi sau `main` 227 commit**.
- **Kiểm chứng:** đọc toàn bộ diff và hàm bao quanh, tìm các chỗ gọi `reconcile_websites_from_disk` / `move_site_runtime` / `vhost_exists`, đối chiếu từng mục với `origin/main`. `bash -n` qua `update.sh` và `spanel-helper.sh`.
- **Không chạy được test:** máy review thiếu `fastapi`.
- **Chỉ review, không sửa code.** Lần 15 (`THAM_DINH_PHU_NHANH_fix-round7-p1.md`) vẫn là bản thẩm định chính của nhánh này. Lần này chỉ ghi thêm những gì tìm được khi rà lại.
- **Ngày lập:** 21/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

Hướng đi đúng. Bỏ `_domain_matches_user` là bỏ đúng chỗ người dùng có thể chiếm website của người khác. Reconcile giờ chỉ thêm dòng bị mất và chỉ chạy từ `run_migrations`. Heredoc WAL của `update.sh` đã đúng cú pháp.

Còn 6 điểm, xếp từ nặng tới nhẹ. Điểm 1 đã được `main` sửa, nên nếu đưa nhánh này lên thì **không được merge đè bản helper của `main`**. Cũng như lần 15 đã nói, nhánh cần rebase lên `main` trước khi mở PR.

---

## Các điểm tìm được

### 1. [P1] `site-runtime-move` xoá PHP pool trước một lệnh `mv` có thể lỗi
`installer/files/spanel-helper.sh` ~11600

`delete_site_php_pools` chạy trước `mv "$old_target" "$new_target"`, mà nhánh `mkdir -p "$new_target"` (khi nguồn không tồn tại) đã bị bỏ. Helper chạy với `set -euo pipefail`.

- **Tình huống lỗi:** admin đổi chủ một website có thư mục gốc cũ đã mất, ví dụ một dòng do reconcile thêm lại nhưng trỏ tới đường dẫn không còn. Helper xoá pool trước, `mv` lỗi, script dừng giữa chừng. `update_website` trả 400 và giữ `root_path`/owner cũ trong DB, nhưng site không còn pool PHP nên trả 502 cho tới khi có người chạy lại `site-runtime-ensure`.
- **Trên `main`:** đã có `if [[ -e "$old_target" ]]; then mv -- …; else mkdir -p "$new_target"; fi`. Đây trùng với P2-2 của lần 15.

### 2. [P2] Vhost sót lại làm một domain không bao giờ tạo lại được
`backend/app/api/websites.py:520` (`create_website`)

`create_website` giờ báo lỗi 409 nếu `nginx.vhost_exists()`. Reconcile không còn xoá vhost sót lại của site đã xoá: lời gọi `nginx.delete_wordpress_vhost` trong nhánh kiểm tra audit `delete_*` đã bị bỏ. Reconcile cũng bỏ qua vhost không xác định được đúng một owner.

- **Tình huống lỗi:** một lần xoá site/user bị gián đoạn để lại `/etc/nginx/conf.d/example.com.conf`. Hoặc một vhost nằm dưới `/home/<x>` mà `x` không khớp panel user nào, hoặc khớp hai user. Website không hiện trong danh sách, nhưng mỗi lần tạo `example.com` đều báo "Domain already exists" mà không có cách gỡ từ panel. Admin phải SSH vào xoá tay.
- **Đề xuất:** đổi thông báo 409 thành thông báo riêng, nêu rõ file cấu hình sót lại, hoặc thêm thao tác dọn cho admin.

### 3. [P2] Reconcile ghi mọi dòng khôi phục trong một lần commit
`backend/app/api/websites.py` ~513

Bản cũ commit từng dòng. Giờ chỉ có một `db.commit()` ở cuối, nên một dòng lỗi làm rollback cả loạt. `run_migrations` chạy ngay khi import `app.main`, tức là ở mọi worker uvicorn, nên có thể gặp `IntegrityError` khi hai worker cùng thêm một domain.

- **Đề xuất:** commit từng dòng, hoặc dùng savepoint cho từng lần thêm.

### 4. [P3] `NGINX_CONF_DIR` ghi cứng đường dẫn đã có trong settings
`backend/app/api/websites.py:386`

Reconcile quét `/etc/nginx/conf.d` cố định, trong khi `nginx._vhost_path` / `vhost_exists` dùng `settings.nginx_sites_available`. Nếu đổi setting này, reconcile sẽ quét một thư mục khác với thư mục mà `create_website` kiểm tra.

- **Đề xuất:** dùng `Path(settings.nginx_sites_available)`.

### 5. [P3] Tạo lại domain đã có của cùng owner giờ báo 409
`backend/app/api/websites.py:520`

Bản cũ trả về chính website đó khi cùng owner gửi lại domain, tức là tạo idempotent. Giờ báo lỗi 409. Một client gọi lại `POST /websites` sau timeout (trong khi lần đầu thực ra đã thành công) sẽ nhận lỗi thay vì nhận website. Cần xác nhận frontend và provisioning không dựa vào hành vi cũ, và ghi vào release notes nếu đây là thay đổi chủ ý.

### 6. [P3] Reconcile nuốt mọi lỗi mà không ghi log
`backend/app/api/websites.py` ~511, `backend/app/core/database.py:89-95`

`except Exception: db.rollback()` trong reconcile và `except Exception: pass` ở `run_migrations` đều bỏ qua lỗi. Sau khi khôi phục DB từ snapshot, nếu reconcile lỗi thì không có site nào hiện lại và cũng không có dòng log nào để điều tra.

- **Đề xuất:** ghi log mức warning.

---

## Đối chiếu nhanh với `origin/main` @ `389e47b`

| Điểm | Trên `main` |
|---|---|
| 1. `mv` thiếu nhánh `mkdir` | **Đã sửa**, không được merge đè |
| 2. Vhost sót lại chặn tạo domain | `vhost_exists` đã nằm trong `_hostname_conflicts`, nên vấn đề vẫn còn |
| 3–6 | Reconcile trên `main` vẫn là bản cũ (quét `/etc/nginx/conf.d`, bị chặn bằng cờ, xem lần 15 mục 2). Các điểm này chỉ phát sinh khi đưa `099debc` sang `main`, nên cần xử lý lúc port |
