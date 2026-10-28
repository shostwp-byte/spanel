# Thẩm định SPanel — Lần 13: bản sửa lần 12 và Laravel Manager (nhánh `1.0.7` → 1.0.8)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** nhánh `1.0.7` @ `05c5f42` (28/10/2026, 22:51). `main` vẫn ở `544f296`, chưa đổi.
- **Phạm vi** (`544f296..05c5f42`, 26 commit, 31 file, +8998 / −326 dòng). Gồm hai phần:

  | Phần | Commit | Nội dung |
  |---|---|---|
  | A. Sửa lỗi lần 12 + quota + UI | `69cee36` … `d94a328` (16 commit, cũng là nhánh `fix/1.0.8-round12`) | +2456 / −324 |
  | B. Laravel Manager (tính năng mới) | `8b33190` … `05c5f42` | `laravel_manager.py` 2208 dòng, `LaravelManager.jsx` 3691 dòng |

- **Kiểm chứng:** đọc diff; đối chiếu từng mục lần 12; các điểm P0/P1 chính đều được kiểm lại trực tiếp trên mã. Không chạy trên VPS.
- **CI:** workflow chỉ chạy khi push vào `main`/`wndev` hoặc khi có PR. Nhánh `1.0.7` chưa có PR mới ⇒ **26 commit này chưa được CI chạy lần nào**. Ta không chạy được test ở máy (thiếu môi trường). Ít nhất một test chắc chắn đỏ (P1-6).
- **Ngày lập:** 28/10/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Phần A (sửa lỗi lần 12) làm tốt, nên tách ra merge trước. Phần B (Laravel Manager) chưa dùng được và có lỗi P0, không nên đưa vào 1.0.8.**

- **Phần A:** P0-A của lần 12 (root qua `WEBMAIL_REPO`) đã sửa đúng. Hầu hết P1/P2 của lần 12 đã sửa, có test hành vi thật cho webmail. Nhưng phát sinh **2 lỗi P1 ở webmail** (webmail mặc định cổng 2096 không gửi được thư; máy nâng cấp không bật được `webmail.<domain>`) và **1 lỗi P1 bảo mật ở quota** (nới sandbox của cả service panel).
- **Phần B:**
  - **P0:** khách làm panel tự xoá `/opt/spanel/backend/.env`.
  - Mọi lệnh `git` / `composer` / `artisan` đều gọi helper action `run-as-user` **không tồn tại**. Trên máy thật tất cả thất bại, nhưng giao diện vẫn báo thành công và ghi một file `artisan` giả.
  - Queue worker giả; mục kiểm bảo mật tự đặt "đạt".
- **P0-B lần 12 (phát hành):** mới một phần. `VERSION` = `1.0.8`, tag `v.10.7` đã xoá, nhưng chưa có PR vào `main`, chưa có tag `v1.0.8`. Tag `v1.0.7` vẫn trùng `v1.0.6`.

Điểm: **≈ 70** nếu merge nguyên `05c5f42`; **≈ 74** nếu merge riêng phần A sau khi sửa P1-1…P1-6; phần B làm lại sau.

---

## 0. Quy tắc khi sửa

1. **Tách phần B khỏi bản phát hành:** mở PR `fix/1.0.8-round12` → `main` (chỉ phần A). Laravel Manager để ở nhánh `feat/1.0.7-laravel-manager`, chưa merge vào `1.0.7`/`main` cho tới khi xong mục 3.
2. Mỗi mục là một commit riêng. Không commit thẳng vào `main`.
3. Mỗi mục P0/P1 có test **gọi mã thật**, kể cả nhánh `command_dry_run=False` (patch `shell.privileged`, `subprocess.run`). Không dùng test grep chuỗi trong file nguồn.
4. Mở PR để CI chạy; 6/6 job xanh trước khi merge. Sau merge: tạo tag **mới** `v1.0.8`, không dời tag cũ.

---

## 1. P0 — Phải sửa trước khi phát hành

### P0-1. Laravel Manager: panel xoá file của chính nó theo symlink của khách

**Vị trí:** `backend/app/api/laravel_manager.py` dòng 2181–2187 (`remove`), 1358–1364 (`install`), 1576–1581 (artisan `up`).

```python
for rel in ("artisan", ".env"):
    target = app_dir / rel
    if target.exists() and not target.is_symlink():
        target.unlink()
```

**Cơ chế:** chạy bằng user `spanel`, chỉ kiểm symlink ở **thành phần cuối**. Khách thay `~/<domain>/public_html` bằng symlink tới `/opt/spanel/backend` (thư mục thuộc `spanel`) rồi gọi `POST /laravel-manager/instances/{id}/remove`. `.env` không phải symlink ⇒ panel xoá `/opt/spanel/backend/.env`.

**Tác hại:** `.env` là `EnvironmentFile=` của `spanel-api` (không có tiền tố `-`) ⇒ panel không khởi động lại được; mất `SECRET_KEY` và khoá mã hoá mật khẩu DB đã lưu. Không cần cài Laravel trước. Biến thể ở `install`: trỏ `public_html` vào `frontend/dist` ⇒ xoá `index.html` của panel.

**Sửa:** không bao giờ `unlink` trong cây thư mục khách bằng `spanel`. Xoá qua helper dưới user khách (`terminal-exec` hoặc lệnh xoá có `require_safe_path`, từ chối symlink). Tối thiểu: `_has_symlink_component(target, site_root)` + kiểm `st_uid` (như `wordpress_live`).

**Tiêu chí:** test: `app_dir` đi qua một symlink tới thư mục ngoài site → không file nào ngoài site bị xoá; `grep -n "\.unlink()" backend/app/api/laravel_manager.py` không còn chỗ nào thao tác trong `site_root`.

### P0-2 (tiếp P0-B lần 12). Phát hành

- Mở PR phần A vào `main`, CI xanh, merge, tạo tag `v1.0.8` + release.
- Cho tới lúc đó, `update.sh` (kênh `release` mặc định) vẫn cài `v1.0.7` = `0573afa` cho mọi máy.

---

## 2. P1 — Phần A (sửa trước khi merge PR 1.0.8)

### P1-1. Quota: nới sandbox của toàn bộ `spanel-api`, không chỉ lệnh quota

- **Vị trí:** `installer/install.sh` dòng 869, 871; `installer/update.sh` dòng 2213–2214; `spanel-helper.sh` ≈ 9761–9764 (commit `c081461`).
- `PrivateDevices=true` → `false` và `ProtectKernelModules=true` → `false` cho service panel, **trên mọi máy**, kể cả máy không bật quota. Hai dòng này là thứ ngăn tiến trình (và helper root qua sudo) truy cập `/dev/sd*`, `/dev/mem` và nạp module kernel. Nay một lỗi chèn mã vào helper/root có thêm quyền đọc/ghi đĩa thô và nạp module.
- Không cần thiết: cùng commit đã chạy `quotacheck` / `quotaon` / `setquota` / `modprobe` trong unit tạm qua `systemd-run` (≈ 9728, 9819, 9866). Lệnh thêm drop-in lúc chạy chỉ `daemon-reload`, không restart ⇒ không có tác dụng.
- **Sửa:** trả lại `PrivateDevices=true`, `ProtectKernelModules=true` ở `install.sh`, `update.sh`, bỏ drop-in lúc chạy; chỉ giữ đường `systemd-run`.

### P1-2. Quota: `/home` riêng bị chuyển nhầm sang `/`

- **Vị trí:** `spanel-helper.sh` `quota_mount_field` ≈ dòng 9630 (commit `2c1e50f`).
- Chuyển sang dòng fstab của `/` bất cứ khi nào `/home` không có dòng fstab mà `/` có — không kiểm `/home` có thật là bind-mount của `/` không. `/home` là đĩa riêng gắn bằng `.mount` unit / script nhà cung cấp / autofs ⇒ bật quota trên `/`, `quotacheck` toàn bộ `/`, báo "đang giới hạn trên /" trong khi `/home` không giới hạn.
- **Sửa:** chỉ chuyển khi `SOURCE` của `/home` có `[subpath]` và `findmnt -no MAJ:MIN` của `/home` bằng của `/`; ngược lại từ chối.

### P1-3. Webmail mặc định (`https://<panel>:2096`) không gửi được thư

- **Vị trí:** `installer/files/webmail/backend/main.py` dòng 416–440 (`_verify_csrf_request`); Nginx `proxy_set_header Host $host` (`spanel-helper.sh` ≈ 3598, 3606).
- `$host` bỏ cổng ⇒ app chờ `panel.example.com`. Trình duyệt gửi `Origin: https://panel.example.com:2096`, so theo `netloc` (có cổng) ⇒ không khớp ⇒ **403 cho mọi POST/PUT/DELETE**: gửi thư, lưu nháp, xoá/di chuyển thư, đổi cài đặt, đăng xuất. Đăng nhập vẫn được (login không kiểm). `webmail.<domain>` cổng 443 không bị.
- **Sửa:** so `urlsplit(origin).hostname` với host đã bỏ cổng, hoặc Nginx gửi `Host $http_host`. Thêm test có `Origin` kèm cổng.

### P1-4. Máy nâng cấp: thiếu zone `limit_req` ⇒ không bật được `webmail.<domain>`

- **Vị trí:** `spanel-helper.sh` ≈ 3594, 3627, 4762–4815, 5194–5201.
- Zone `spanel_webmail_login` chỉ được khai báo trong `00-spanel-webmail.conf`, do `mail_write_webmail_nginx` ghi (chỉ chạy lúc `mail-install`). `mail_reconfigure` (do `update.sh` gọi) không ghi lại file này, nhưng khối proxy mới đã tham chiếu zone đó ⇒ trên máy cài từ trước: `mail-webmail-host` lỗi `nginx -t` rồi `rm -f "$conf"` ⇒ không bật được `webmail.<domain>` mới, bật lại cái cũ thì **xoá luôn** khối đang chạy.
- **Sửa:** khai báo zone trong file riêng mà `mail_reconfigure` luôn ghi, hoặc gọi `mail_write_webmail_nginx` từ `mail_reconfigure`.

### P1-5. Chặn PHP trong uploads cho WordPress ở thư mục con vẫn không có tác dụng

- **Vị trí:** `backend/app/api/wordpress_manager.py` `_subpath_uploads_php_rule` (≈ 514) và nhánh `uploads_php_blocked` của `apply_wp_security`; `backend/app/templates/nginx/php.conf.j2` dòng 62 và 77.
- Luật `location ~* ^/<sub>/wp-content/uploads/.*\.php$ { deny all; }` được thêm vào `nginx_custom`. Custom include nằm **sau** `location ~ \.php$` trong template (php: dòng 62 vs 77). Nginx xét location regex theo thứ tự xuất hiện ⇒ `\.php$` khớp trước ⇒ **PHP vẫn chạy**. Trong khi đó `_vhost_blocks_uploads_php` thấy luật trong `nginx_custom` ⇒ mục kiểm báo đạt.
- **Sửa:** dùng location tiền tố `location ^~ /<sub>/wp-content/uploads/ { location ~ \.php$ { deny all; } }` (`^~` thắng regex), hoặc đặt include trước `location ~ \.php$`. Test bằng `nginx -T` / render template và kiểm thứ tự.

### P1-6. Test sẽ đỏ, và chuyển chủ website làm "mất" thư

- **Test:** `tests/test_frontend_chunk_recovery.py` dòng 167 vẫn tìm chuỗi `if (currentUser && effectiveRole && !headers['X-Access-Role'])`; `d94a328` đã đổi thành `if (activeUser && activeRole …)` (App.jsx 1634) ⇒ test đỏ. Sửa test theo hành vi thay vì grep.
- **Chuyển chủ** (`websites.py`, `transfer_website_owner`, commit `542cc4c`): đổi `owner_id` của `MailAccount`, nhưng đường dẫn hộp thư là `/home/<user của chủ>/mail/<domain>/<hộp>` (`mail.py` dòng 407) và không có bước di chuyển thư ⇒ sau `mail.sync_quietly`, Dovecot trỏ sang home của chủ mới: hộp thư trống, thư cũ nằm lại (và tính dung lượng) ở home chủ cũ. **Sửa:** di chuyển maildir qua helper (giữ quyền, dừng nhận thư trong lúc chuyển), hoặc từ chối chuyển khi domain còn hộp thư và ghi rõ.

---

## 3. Phần B — Laravel Manager: P1 (làm lại trước khi phát hành tính năng)

1. **Mọi lệnh chạy thật đều thất bại** (dòng 438): gọi `shell.privileged("run-as-user", …)` — helper không có action này (`spanel-helper.sh` dòng 12690 `deny "unknown command"`). Endpoint vẫn trả "thành công"; cài đặt ghi file `artisan` giả in "Laravel Framework 11.42.0 … successfully", `composer.json` và `index.php` giữ chỗ (678–715); log / output mặc định là dữ liệu dựng sẵn (968–981, 1594–1616). `composer require/remove` sửa `composer.json` dù composer lỗi, ghi phiên bản `*` (1961–1994). **Sửa:** dùng `terminal-exec <user> <root> --php-version=X php|composer|git …` (helper đã cho phép), kiểm mã trả về, bỏ stub và output giả.
2. **Queue worker giả** (1630–1715): không tạo tiến trình nào (supervisor/systemd/`queue:work`), chỉ bật cờ JSON và in "[OK] Queue Worker active". Làm thật (unit do helper quản lý, chạy dưới user khách) hoặc bỏ.
3. **Mục kiểm bảo mật không kiểm gì** (815–842, 2070–2094): `dotenv_git_blocked`, `env_permissions_strict`, `storage_php_blocked` mặc định True qua override; nút "secure" chỉ đặt override; chặn PHP trong `storage` bằng `.htaccess` (Nginx bỏ qua). Vhost không chặn dotfile; `.env` chỉ được WAF che khi WAF bật.
4. **Không tắt được chế độ bảo trì** (1566–1581): `up` dựa vào `unlink` bằng `spanel` (EACCES) và `artisan up` (hỏng do mục 1) ⇒ site kẹt ở 503.
5. **Gỡ cài đặt lệch DB và Nginx, có thể xoá nhầm DB** (2166–2200): đổi `document_root`/`rewrite_mode` trong DB nhưng không ghi lại vhost; lần ghi vhost sau (gia hạn SSL, sửa alias) phục vụ thư mục gốc dự án ⇒ `.env`, `.env.bak`, `storage/logs` đọc được qua web. `drop_database` dùng `resolve_owned_wp_database` trả DB đầu tiên gắn với site, bỏ qua `db_name` đã lưu ⇒ với WordPress ở gốc + Laravel ở thư mục con, có thể **xoá DB WordPress**.
6. **Cài đặt và "secure" ghi đè site bất kỳ** (1281–1286, 1443, 2044–2066): không kiểm `app_type` hay thư mục trống; ghi đè `.env`, đặt `app_type="php"`, `document_root=public_html[/sub]/public` ⇒ site WordPress/Node/Docker ngừng chạy; với thư mục con, cả domain bị trỏ vào app con.
7. **`.env.bak` đọc được bởi mọi user** (1842): ghi với mode `0600` nhưng `write_wp_file_text` đổi mọi mode khác `0640` thành `0644`; `.env.bak` không có trong `SITE_SECRET_FILES`.

**P2 phần B:** `php_version` không kiểm (1270–1282, 1893–1895); một dòng state JSON chung cho mọi khách, load-sửa-lưu không nguyên tử, `GET /instances` ghi lại cả khối mỗi lần (198–315, 1148); test chỉ chạy mock và khẳng định cả file `artisan` giả; menu Docker / Node.js hiện cả khi chưa có addon (App.jsx 3305–3306).

**Phần làm đúng:** mọi endpoint qua `_get_accessible_website` (kiểm chủ site); đọc file dùng `O_NOFOLLOW` + `st_uid`; ghi qua `site-file-write`; lệnh dựng bằng danh sách tham số / `shlex.quote`, artisan có allowlist, gói composer / URL git / nhánh có kiểm regex; vhost qua `_rewrite_website_vhost`; frontend không dùng `dangerouslySetInnerHTML`.

---

## 4. P2 — Phần A, làm sau

**Webmail**

1. Bucket theo email (20 lần sai / 5 phút từ mọi IP) chặn cả mật khẩu đúng ⇒ ai biết địa chỉ cũng khoá được đăng nhập webmail của người khác (`main.py` 103, 140). Xác thực trước rồi mới áp bucket email, hoặc chỉ dùng để làm chậm.
2. Từ điển rate-limit không dọn ⇒ tăng mãi theo địa chỉ ngẫu nhiên / IPv6 (`main.py` 104–158).
3. `mail-reconfigure`: chép `requirements.txt` trước khi `pip` chạy, lỗi `pip` bị `|| true` ⇒ lần sau `cmp` thấy giống nhau, không bao giờ cài lại (helper ≈ 4791–4806).
4. `/api/auth/login` không kiểm CSRF / Content-Type (login CSRF).
5. Tên hiển thị: đã lọc `\r\n\0` nhưng `\x0b`, `\x0c`, `\x1c`–`\x1e` vẫn làm `EmailPolicy` ném `ValueError` ⇒ 500 mọi lần gửi. Lọc `[\x00-\x1f\x7f]`.
6. Dependency gián tiếp (pydantic, anyio, …) chưa ghim, không có hash.
7. Dovecot vẫn thấy mọi đăng nhập từ `127.0.0.1`.

**Frontend**

8. `ChunkErrorBoundary` hiện "Page update available" cho **mọi** lỗi render, che lỗi thật (App.jsx 55–90).
9. `CodeEditor` gọi `loadAceBundle().then(...)` không `.catch` (App.jsx 886); `MessageTemplates` dùng `React.lazy` thường; cửa sổ chống lặp reload 15 giây là theo thời gian, không theo build.
10. GZip chỉ loại trừ theo đuôi đường dẫn (`/download`, `/export`, `/raw`); `EXCLUDED_CONTENT_TYPES` khai báo mà không dùng (main.py 119–127).
11. "Login as" / thoát đóng vai gọi `refreshAll` hai lần.

**Quota / WordPress / chuyển chủ**

12. Ghi fstab không nguyên tử (`cat tmp > /etc/fstab`), không khôi phục bản sao lưu khi lỗi; `findmnt --verify` hỏng trong unit (ẩn `/dev/disk`) nên chỉ còn kiểm đếm cột. Ghi file tạm trong `/etc`, `sync`, `mv`; kiểm bằng `mount -fav --fstab tmp` qua `systemd-run`.
13. `apt-get install linux-modules-extra-$(uname -r)` chạy ngầm trong request web. Nên từ chối kèm hướng dẫn.
14. `write_wp_file_text` nay im lặng `return` khi helper ghi thất bại ⇒ Clone / bật tắt bảo mật báo thành công dù file không được ghi. Nên kiểm `returncode` và ném lỗi.
15. Kiểm giới hạn gói khi chuyển chủ đọc cột trên user trước và coi `0` là không giới hạn — trái với `effective_database_limit` (package là nguồn chính, `0` = tắt). Dùng các hàm `effective_*_limit` sẵn có.

---

## 5. Đối chiếu lần 12

| Lần 12 | Trạng thái ở `05c5f42` |
|---|---|
| P0-A `WEBMAIL_REPO` → root | **Đã sửa** (`69cee36`): bỏ biến khỏi `.env`, bỏ ứng viên `APP_DIR`, chỉ nhận thư mục thuộc root, không symlink; `pip` chạy dưới `SPanel-webmail` với `--only-binary :all:`, venv trả về root sau đó. |
| P0-B Phát hành | **Một phần**: `VERSION` 1.0.8, CHANGELOG, xoá `v.10.7`. Chưa PR, chưa tag. |
| P1-1 Phiên SSO 7 ngày | **Đã sửa**: mã thế hệ hộp thư ký trong token, kiểm mỗi request; helper xoá phiên khi hộp thư mất / tạm khoá / đổi thế hệ; TTL 2 giờ. |
| P1-2 Brute force webmail | **Một phần**: giới hạn theo cặp / IP / email + `limit_req` Nginx; `X-Forwarded-For` không giả được. Phát sinh P1-4, P2-1, P2-2. |
| P1-3 Mật khẩu không ASCII | **Đã sửa**: SASL PLAIN UTF-8. |
| P1-4 Trắng trang sau cập nhật | **Đã sửa**: ErrorBoundary, `vite:preloadError`, reset `_aceBundlePromise`. Còn P2-8, P2-9. |
| P1-5 Chuyển chủ website | **Phần lớn**: có UI, chuyển SFTP / SiteApp / DNS / mail, kiểm giới hạn, rollback khi lỗi. Còn thư không được di chuyển (P1-6) và P2-15. |
| P2-1 Chặn PHP uploads thư mục con | **Chưa có tác dụng** — P1-5. |
| P2-2 DEFINER trên dòng dữ liệu, `/tmp` | **Đã sửa**: chỉ áp dòng DDL; file tạm cạnh file SQL hoặc `/var/tmp`. |
| P2-3 Mã nhận biết test | **Đã gỡ** (nhánh `unittest`, so sánh monkeypatch). |
| P2-4 `write_wp_file_text` quay về ghi bằng panel | **Đã bỏ**, nhưng nay im lặng khi lỗi — P2-14. |
| P2-5 CSRF webmail | **Một phần**: kiểm Origin / `X-Requested-With`, `SameSite=Strict`. Làm hỏng cổng 2096 — P1-3; login chưa kiểm — P2-4. |
| P2-6, P2-7 `mail_reconfigure` pip / khoá | **Đã sửa** (còn P2-3). |
| P2-8 Lưu nháp | **Đã sửa**: APPEND trước, xoá bản cũ sau. |
| P2-9 CR/LF tên hiển thị | **Một phần** — P2-5. |
| P2-12 Race header vai trò khi đăng nhập | **Đã sửa.** |
| P2-14 CORS `X-Access-Role` | **Đã sửa.** |
| P2-15 App Installer mồ côi | **Đã sửa** (trả lại menu User). |
| P2-16 GZip | **Đã sửa** theo đường dẫn — P2-10. |

---

## 6. Bảng điểm

| Trụ cột | Trọng số | L11 | L12 | **L13** | Lý do thay đổi |
|---|---:|---:|---:|---:|---|
| 1. Bảo mật & tách đặc quyền | 15% | 57 | 58 | **58** | P0-A đóng; P0-1 Laravel mới; nới sandbox panel |
| 2. Tự vá lỗi & cập nhật | 12% | 72 | 68 | **69** | Có 1.0.8 + CHANGELOG; chưa PR / tag; 26 commit chưa qua CI |
| 3. Chịu lỗi & tự phục hồi | 12% | 70 | 71 | **72** | Tự phục hồi khi thiếu chunk; rollback chuyển chủ |
| 4. Sao lưu & DR | 10% | 75 | 76 | **77** | DEFINER chỉ DDL |
| 5. Chịu tải & cách ly | 12% | 72 | 72 | **72** | Quota tốt hơn nhưng có thể áp nhầm `/` |
| 6. Đa runtime | 10% | 71 | 73 | **72** | Laravel Manager chạy giả; uploads thư mục con vẫn hở |
| 7. WHMCS & hệ sinh thái | 10% | 70 | 70 | **70** | |
| 8. UX, RBAC & giám sát | 7% | 78 | 78 | **79** | Chuyển chủ có UI; sửa race vai trò |
| 9. Mail & DNS | 6% | 69 | 70 | **71** | SSO, brute force, UTF-8 sửa; cổng 2096 hỏng gửi thư |
| 10. Cụm đa máy chủ | 6% | 56 | 56 | **56** | |
| **Tổng** | 100% | ≈ 69 | ≈ 70 | **≈ 70** | ≈ 74 nếu merge riêng phần A sau P1-1…P1-6 |

*Giới hạn: chỉ đọc mã; không chạy trên VPS, không chạy được test (chưa có CI cho các commit này). Webmail, frontend / quota và Laravel Manager được rà bởi agent phụ; các điểm P0/P1 (P0-1, `run-as-user` không tồn tại, sandbox, test đỏ, CSRF cổng 2096, thứ tự location Nginx, thư khi chuyển chủ) đã được kiểm lại trực tiếp trên mã. Thực hiện bởi Claude (AI của Anthropic). Mã được thẩm định không phải do Claude viết.*
