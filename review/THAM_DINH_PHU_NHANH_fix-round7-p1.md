# Thẩm định SPanel — Lần 15: nhánh `fix/round7-p1` (4 commit sửa lỗi sau merge 1.0.7)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** nhánh `fix/round7-p1` @ `2640cdb` (26/10/2026, 09:36). Gốc nhánh: `b98956d` (Merge PR #5, 26/10).
- **So với `main`:** `origin/main` hiện ở `3bf2f6b` (02/11/2026, đã merge 1.0.8). Nhánh **đi sau `main` 158 commit**.
- **Phạm vi** (`b98956d..2640cdb`, 4 commit, 5 file, +350 / −294):

  | Commit | Nội dung |
  |---|---|
  | `a2c1574` | `create_website`: bỏ đoạn "chiếm" website của admin / user đã xoá; trả lại kiểm tra vhost trên đĩa |
  | `099debc` | `reconcile_websites_from_disk`: chỉ thêm dòng bị mất, không đổi chủ, không chuyển file; chỉ chạy từ `run_migrations` |
  | `8ab4ba5` | Helper `site-runtime-move`: từ chối khi đích đã tồn tại (kể cả symlink), bỏ `cp -a … \|\| true; rm -rf` |
  | `2640cdb` | `update.sh`: gỡ phần bash dư nằm trong heredoc Python của bước WAL checkpoint; thêm test biên dịch mọi heredoc `<<'PY'` |

- **Kiểm chứng:** đọc toàn bộ diff và bộ test mới `tests/test_release_round7_regressions.py`. Đối chiếu từng mục với `origin/main` @ `3bf2f6b`. Thử merge (`git merge-tree origin/main 2640cdb`). Biên dịch toàn bộ heredoc `<<'PY'` của `update.sh` / `spanel-helper.sh` trên `main`.
- **Không chạy được test:** máy review không có Python 3.12 và các gói backend (`fastapi`…). Nhánh **chưa push**, chưa có PR ⇒ **CI chưa chạy lần nào** trên 4 commit này.
- **Ngày lập:** 11/11/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Từng commit, xét riêng, sửa đúng lỗi nó nêu. Nhưng nhánh không merge được như hiện tại: nó đã cũ 16 ngày, xung đột 3 file với `main`, và 3/4 mục đã được `main` sửa theo cách khác (có chỗ tốt hơn). Phần còn giá trị là `099debc` (reconcile chỉ thêm dòng) cùng bộ test.**

- Lỗi chiếm website (P1 của commit `a2c1574`) và lỗi heredoc (`2640cdb`): **`main` đã hết**.
- `site-runtime-move` (`8ab4ba5`): `main` có cùng kiểm tra đích, **thêm** nhánh `mkdir -p` khi nguồn không tồn tại. Bản của nhánh này thiếu nhánh đó ⇒ hồi quy nếu merge đè (P2-2).
- Reconcile (`099debc`): trên `main`, hàm vẫn còn nguyên logic gán chủ theo email / tiền tố username / thư mục `/home` / DNS zone và chuyển file, chỉ bị chặn bằng cờ `_explicit_reconcile_allowed` **không chỗ nào đặt**. Tức là: an toàn nhờ code chết, và việc khôi phục dòng bị mất cũng chết theo. Bản của nhánh này đúng hướng hơn, cần đưa sang `main` (mục 2).
- Vi phạm quy tắc: 1 test kiểu grep chuỗi trong file nguồn (P2-3); chưa PR / CI (quy tắc 4–5 lần 14).

**Điểm:** không đổi so với lần 14, vì nhánh chưa vào `main` và chưa có bản phát hành mới.

---

## 0. Quy tắc áp dụng (lần 2 mục 0 + lần 14 mục 0)

| Quy tắc | Trạng thái trên nhánh |
|---|---|
| Nhánh riêng, không commit thẳng `main` | **Đạt** |
| Mỗi mục một commit, message `fix(scope): …`, không ghi điểm | **Đạt** — 4 commit, message mô tả đúng lỗi và cách sửa |
| `git show --stat` chỉ chạm file liên quan, không xoá ngoài dự kiến | **Đạt** — mỗi commit chỉ chạm file của mục + file test; phần xoá lớn ở `websites.py` (−325) là chủ ý (bỏ logic gán chủ) |
| Không ghi đè file bằng bản sao từ nơi khác | **Đạt** — sửa từng đoạn; `update.sh` chỉ gỡ đúng 8 dòng dư |
| Không tự thêm tính năng | **Đạt** |
| Test gọi mã thật, không grep chuỗi | **Chưa đạt** — `test_site_runtime_move_refuses_an_existing_target` (P2-3) |
| Chạy test bằng Python 3.12 trước khi báo xong | **Không có bằng chứng** — nhánh chưa push, không có log CI |
| Mở PR, CI xanh trước merge; CI chạy cả khi push mọi nhánh | **Chưa** — nhánh chỉ có ở máy local |
| Nhánh phát hành chỉ nhận mã đã thẩm định | Áp dụng khi đưa vào `main`: xem mục 4 |

---

## 1. Đối chiếu từng commit với `main` @ `3bf2f6b`

| Commit | Lỗi gốc | Trên `main` | Đánh giá |
|---|---|---|---|
| `a2c1574` chiếm website qua `POST /api/websites` | End User tạo trùng domain của admin ⇒ nhận website + file (`wp-config.php`) | **Đã hết.** `create_website` (`websites.py` ≈ 645–680) không còn khối `existing_website`; `_hostname_conflicts` (≈ 256) đã gồm `nginx.vhost_exists`; admin/reseller phải chọn End User làm chủ | Sửa đúng; trên `main` thừa. Giữ **2 test** của commit này (chiếm website, vhost đã có trên đĩa) — chúng đúng kiểu test hành vi và nên chạy được trên `main` |
| `099debc` reconcile gán chủ + chuyển file | Reseller đặt email khách `x@<domain admin>` ⇒ mở trang Websites là site admin chuyển sang khách | **Bị chặn tạm** bằng cờ chết (mục 2) | Bản của nhánh tốt hơn — nên port |
| `8ab4ba5` `site-runtime-move` gộp vào đích | `cp -a … 2>/dev/null \|\| true` rồi `rm -rf` nguồn ⇒ đầy đĩa / quota là mất site; root ghi theo tên vào cây của chủ mới | **Đã hết.** `spanel-helper.sh` ≈ 12570: từ chối đích tồn tại (kể cả `-L`), `mv --` khi nguồn có, `mkdir -p` khi không | `main` tốt hơn — bỏ commit này (P2-2) |
| `2640cdb` bash dư trong heredoc WAL | Python `SyntaxError`, `\|\| true` che, checkpoint không chạy | **Đã hết.** 6 heredoc `update.sh` + 29 heredoc helper trên `main` đều biên dịch được | Bỏ phần sửa `update.sh`; **giữ test** biên dịch heredoc — đây là test có giá trị, chặn được cả họ lỗi "merge làm hỏng heredoc" |

---

## 2. P1 — Reconcile trên `main` an toàn chỉ nhờ code chết

- **Vị trí (`main`):**
  - `backend/app/api/websites.py:429` `reconcile_websites_from_disk`: dòng 434 `if settings.command_dry_run or not getattr(db, "_explicit_reconcile_allowed", False): return`.
  - `git grep _explicit_reconcile_allowed origin/main` chỉ ra đúng dòng 434 ⇒ **không chỗ nào đặt cờ**, hàm luôn `return`.
  - Vẫn được gọi ở `list_websites` (934), `create_website` (649), `update_website` khi đổi domain (1047), `core/database.py:91–93` (`run_migrations`).
  - Thân hàm vẫn giữ: gán chủ theo `_domain_matches_user` (402, dùng ở 537 và 609 — email `@domain`, tiền tố username…), theo DNS zone, rồi `site_users.move_site_runtime(...)` và `_rewrite_website_vhost(...)` (≈ 612–620), có `except Exception: w.root_path = new_root_path` ghi đường dẫn mới vào DB cả khi chuyển file thất bại.
- **Rủi ro:**
  1. Chỉ cần một commit sau đặt `db._explicit_reconcile_allowed = True` (ví dụ để "bật lại self-heal") là lỗi chiếm website của lần 7 quay lại nguyên vẹn, kể cả qua `GET /api/websites`.
  2. Chức năng thật cần có — thêm lại dòng `Website` khi DB quay về snapshot cũ — **đang không chạy**. Sau một lần khôi phục DB, site vẫn phục vụ trên Nginx nhưng biến mất khỏi panel, và `_hostname_conflicts` chặn tạo lại (vhost tồn tại) ⇒ admin phải sửa tay.
- **Sửa:** port `099debc` sang `main` (thay thân hàm bằng bản "chỉ thêm dòng", bỏ cờ `_explicit_reconcile_allowed`, bỏ 3 lời gọi từ request, xoá `_domain_matches_user` nếu không còn ai dùng). Kèm `test_listing_websites_does_not_reconcile_from_disk` và `test_reconcile_adds_lost_rows_but_never_changes_an_owner`.
- **Tiêu chí:** `git grep -n "reconcile_websites_from_disk" -- backend/app/api` chỉ còn định nghĩa; `git grep -n "_domain_matches_user\|_explicit_reconcile_allowed" -- backend` rỗng; test reconcile xanh trên CI.

---

## 3. P2 — Lỗi nhỏ trong chính nhánh này (sửa khi port)

### P2-1. Kiểm tra "xoá dở" trong reconcile chỉ khớp 1 trong 3 hành động

- **Vị trí (nhánh):** `websites.py:429–437`. Lấy `AuditLog` mới nhất có `target == stem` (domain), bỏ qua nếu `action in {"delete_website", "delete_user", "provisioning_terminate"}`.
- **Thực tế:** `delete_user` ghi `target = username` (`users.py:582`), `provisioning_terminate` ghi `target = external_id` (`provisioning.py:502`). Chỉ `delete_website` (`websites.py:1276`) ghi domain. Hai hành động kia không bao giờ khớp.
- **Hậu quả:** xoá user không xong (vhost còn lại, `/home/<user>/<domain>` còn), sau đó tạo user mới **cùng username** (cùng Linux user) ⇒ lần khởi động API kế tiếp, reconcile gán site cũ cho user mới. Hiếm, nhưng là rò quyền sở hữu giữa hai khách khác nhau.
- **Sửa:** khi xoá user / terminate, ghi thêm audit `delete_website` cho từng domain (hoặc so `detail` chứa domain); hoặc bỏ qua vhost có `AuditLog` `delete_user` với `target == parts[2]` (Linux user ≈ username). Thêm test cho trường hợp này.

### P2-2. `site-runtime-move` của nhánh bỏ mất nhánh "nguồn không tồn tại"

- **Vị trí (nhánh):** `spanel-helper.sh:11599–11602`: sau kiểm tra đích là `mv "$old_target" "$new_target"` không điều kiện.
- **Hậu quả:** nguồn đã mất (khách tự xoá thư mục site) ⇒ `mv` lỗi, `set -e` dừng helper ⇒ đổi chủ website (`websites.py:1009`) thất bại. Bản trước 1.0.7 cũng vậy; `main` đã sửa (`if [[ -e "$old_target" ]]; then mv -- …; else mkdir -p …; fi`).
- **Sửa:** khi đưa nhánh lên `main`, giữ bản của `main` cho đoạn này (bỏ `8ab4ba5`).

### P2-3. Test helper là test grep chuỗi

- **Vị trí:** `tests/test_release_round7_regressions.py:184` `test_site_runtime_move_refuses_an_existing_target`: cắt khối `case` bằng regex rồi `assert '...' in block`, `assert "rm -rf" not in block`.
- **Vi phạm:** quy tắc 7 (lần 2) và quy tắc 3 (lần 14). Test này còn **đỏ trên `main`** vì `main` viết `mv -- "$old_target"` (có `--`), dù hành vi đúng.
- **Sửa:** chạy thật đoạn `site-runtime-move` trong thư mục tạm (stub `require_managed_path`, `delete_site_php_pools`, `ensure_php_pool`, `own_site_tree`… bằng hàm bash rỗng), tạo đích là thư mục / file / symlink và kiểm: exit ≠ 0, nguồn còn nguyên, đích không bị đổi. Nếu chưa làm được thì bỏ test này còn hơn giữ test khoá chuỗi.

### P2-4. Reconcile ghi `ssl_mode="letsencrypt"` cho mọi vhost có `listen 443 ssl`

- **Vị trí (nhánh):** `websites.py` ≈ 503–504.
- **Hậu quả:** site dùng chứng chỉ tự tải lên (`manual`), Cloudflare hay `shared` được khôi phục với `ssl_mode = letsencrypt`; giao diện hiển thị sai loại SSL, và thao tác SSL theo `ssl_mode` (gia hạn, bật lại) có thể đi nhầm nhánh Let's Encrypt. Chưa kiểm hết các đường gọi, nên xếp P2.
- **Sửa:** `letsencrypt` chỉ khi `has_le`; còn lại `manual` (giá trị đã có trong mã).

---

## 4. Cách đưa vào `main`

Không merge `fix/round7-p1` vào `main` (xung đột `websites.py`, `spanel-helper.sh`, `update.sh`; và sẽ ghi đè bản tốt hơn của `main` ở helper). Làm lại trên nhánh mới từ `origin/main`:

1. Nhánh `fix/1.0.8-reconcile` từ `origin/main`.
2. Commit 1 — `fix(security): make the disk reconcile add lost rows only` — port thân hàm của `099debc`, bỏ cờ chết và 3 lời gọi từ request, sửa P2-1 và P2-4 cùng lúc hoặc mỗi mục một commit.
3. Commit 2 — `test: …` — đưa `tests/test_release_round7_regressions.py` sang, **bỏ** P2-3 (hoặc viết lại thành test chạy thật). Giữ test chiếm website, vhost trên đĩa, reconcile, heredoc.
4. Không cherry-pick `a2c1574` (phần code), `8ab4ba5`, `2640cdb` (phần code): `main` đã có.
5. Push nhánh ⇒ CI chạy; mở PR vào `main`, CI xanh mới merge. Tag mới theo quy tắc, không dời tag cũ.
6. Xoá (hoặc ghi rõ "đã thay bằng …") nhánh local `fix/round7-p1` để không ai merge nhầm.

**Tiêu chí đóng lần 15:** PR từ `origin/main` có CI xanh; trên `main`, các lệnh grep ở mục 2 cho kết quả như mô tả; `pytest tests/test_release_round7_regressions.py` xanh trên Python 3.12.
