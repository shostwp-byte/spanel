# Thẩm định SPanel — Lần 14: bản sửa lần 13 và lộ trình lên điểm tối đa (nhánh `1.0.7` → 1.0.8)

- **Repo:** `github.com/shostwp-byte/spanel`
- **Bản thẩm định:** nhánh `1.0.7` @ `48f7c87` (29/10/2026, 09:10). `main` vẫn ở `544f296`.
- **Phạm vi** (`05c5f42..48f7c87`, 8 commit). Hai phần:

  | Phần | Commit | Nội dung |
  |---|---|---|
  | A. Sửa lỗi lần 13 (nhánh `fix/1.0.7-round13-part-a`) | `63120eb` … `cbb490b` | 19 file, +533 / −204: quota, webmail, WordPress thư mục con, chuyển chủ, frontend |
  | B. Đưa lại menu Application (nhánh `fix/1.0.7-restore-application-menu`) | `9becdd9` | **Đưa Laravel Manager trở lại `1.0.7`**, bỏ menu App Installer |

- **Kiểm chứng:** đọc toàn bộ diff (trừ Laravel Manager, vì mã không đổi: `git diff 05c5f42 48f7c87 -- laravel_manager.py LaravelManager.jsx test_laravel_manager.py` rỗng). Từng mục P1 của lần 13 được kiểm lại trên mã. Không chạy trên VPS.
- **CI:** không có PR mới ⇒ **8 commit này chưa được CI chạy** (lần chạy gần nhất là 27/10). Ta không chạy được test ở máy.
- **Ngày lập:** 29/10/2026 · **Thực hiện:** Claude (Anthropic)

---

## Kết luận

**Phần A làm tốt: cả 6 lỗi P1 của lần 13 đã sửa đúng, có test hành vi, và 13/15 mục P2 đã xử lý. Nhưng nhánh `1.0.7` lại chứa Laravel Manager nguyên trạng, gồm cả lỗi P0. Không được merge `1.0.7` vào `main` như hiện tại.**

- `63120eb` (08:18) đã tách Laravel Manager ra như lần 13 khuyến nghị. 52 phút sau, `9becdd9` (09:10) đưa lại **đúng 6.367 dòng cũ**, không sửa dòng nào. Vì vậy P0-1 (khách làm panel tự xoá `/opt/spanel/backend/.env`) và 7 lỗi P1 phần B của lần 13 **đều còn trên `1.0.7`**. Menu "Application → Laravel" nay hiện cho mọi End User.
- **Phần A:** không có P1 mới. Còn 5 lỗi P2 mới, đều nhỏ (mục 3).
- **Phát hành:** vẫn chưa có PR vào `main`, chưa có tag `v1.0.8`. Máy khách vẫn nhận `v1.0.7` (= `v1.0.6` = `0573afa`).

**Điểm:** ≈ **70** nếu merge nguyên `48f7c87`. ≈ **72** nếu chỉ phát hành phần A (gỡ Laravel, PR, CI xanh, tag). Lần 13 ước "≈ 74 sau phần A"; tính lại theo từng trụ cột (mục 5) thì là ≈ 72. Con số được chỉnh lại cho khớp bảng điểm.

**Câu hỏi "làm sao lên điểm tuyệt đối":** xem mục 6. Tóm tắt: thang này là thang của báo cáo gốc. Trên thang đó cPanel / Plesk / DirectAdmin ở khoảng 85–95. Lên ~85 làm được bằng mã trong 2–3 bản phát hành. Phần 85 → 95+ cần **bằng chứng vận hành** (audit độc lập, benchmark công khai, pilot nhiều tháng), không chỉ thêm mã. 100 tuyệt đối không có panel nào đạt; mục tiêu thực tế nên là ≥ 90.

---

## 0. Quy tắc khi sửa (nhắc lại, thêm 1 điều)

1. **Nhánh phát hành chỉ nhận mã đã qua thẩm định.** Tính năng chưa xong (Laravel Manager) để ở nhánh `feat/…`. Muốn hiện menu thì để sau cờ tính năng (feature flag / addon), mặc định tắt. Không merge vào `1.0.7` / `main`.
2. Mỗi mục là một commit riêng. Không commit thẳng vào `main`.
3. Test gọi mã thật. Không thêm test kiểu grep chuỗi trong file nguồn: test lần này vẫn khẳng định `"['laravel_manager', 'Laravel', LaravelIcon]" in app_src`, và test Laravel vẫn khẳng định file `artisan` giả.
4. Mở PR để CI chạy, CI xanh trước khi merge. Sau merge: tag **mới** `v1.0.8`, không dời tag cũ.
5. **(Mới)** Cho CI chạy cả khi push lên mọi nhánh (`on: push: branches: ['**']`), không chỉ `main` / `wndev`. Nhánh sửa lỗi có kết quả CI ngay, không phải đợi tới lúc mở PR.

---

## 1. P0 — Phải xử lý trước khi phát hành

### P0-1 (mở lại). Laravel Manager được đưa lại vào `1.0.7`, mã không đổi

- **Commit:** `9becdd9` "restore Application menu group (Docker/Node.js/Laravel/WordPress)". Thêm lại `laravel_manager.py` (2208 dòng), `LaravelManager.jsx` (3691 dòng) và `test_laravel_manager.py` (432 dòng), y hệt `05c5f42`.
- **Còn nguyên:**
  - `laravel_manager.py` dòng 2184 (`remove`), 1578 (artisan `up`) và các chỗ `unlink` trong `install`: `spanel` xoá file qua đường dẫn có thể đi qua symlink của khách ⇒ xoá được `/opt/spanel/backend/.env` ⇒ panel không khởi động lại được (chi tiết ở lần 13, P0-1).
  - Dòng 439: gọi `shell.privileged("run-as-user", …)`, nhưng helper không có action này ⇒ mọi lệnh `git` / `composer` / `artisan` thất bại, giao diện vẫn báo thành công, cài đặt ghi file `artisan` giả.
  - 7 lỗi P1 phần B của lần 13: queue worker giả, mục kiểm bảo mật tự "đạt", không tắt được bảo trì, gỡ cài đặt lệch DB/Nginx và có thể xoá DB WordPress, cài đè site bất kỳ, `.env.bak` mode 0644.
- **Sửa (chọn một):**
  - (a) Revert `9becdd9` trên nhánh phát hành. Nếu chỉ muốn giữ phần menu Docker / Node.js / WordPress thì giữ riêng các dòng đó, không kèm Laravel.
  - (b) Giữ menu nhưng ẩn mục Laravel sau cờ tính năng mặc định tắt, **và** gỡ router `laravel_manager` khỏi `main.py`. Chỉ ẩn ở frontend thì API vẫn gọi được.
- **Tiêu chí:** trên nhánh phát hành, `git grep -n "run-as-user\|laravel_manager" -- backend/app/main.py` không còn router Laravel; hoặc mọi mục P0/P1 phần B của lần 13 đã đóng, có test chạy trên VM.

### P0-2 (tiếp). Phát hành

- Ứng viên phát hành đã có sẵn: **`fix/1.0.7-round13-part-a` @ `cbb490b`**. Nhánh này chính là `1.0.7` trừ `9becdd9`, không có file Laravel nào, và đã gồm toàn bộ sửa lỗi lần 12 + 13. Mở PR từ nhánh này vào `main`, chờ CI xanh, merge, rồi tạo tag `v1.0.8` + GitHub Release. **Không** dùng `fix/1.0.8-round12`: nhánh đó nay đã trỏ tới `48f7c87`, có Laravel. Nếu muốn giữ menu Docker / Node.js / WordPress của `9becdd9` thì cherry-pick riêng phần `App.jsx` đó, bỏ mục Laravel.
- Nên ký tag (`git tag -s`) và cho `update.sh` kiểm chữ ký (`git verify-tag`; hiện chưa có chỗ nào kiểm). Xem mục 6, trụ cột 2.

---

## 2. Phần A — đối chiếu lần 13 (tất cả P1 đã đóng)

| Lần 13 | Trạng thái ở `48f7c87` |
|---|---|
| P1-1 Nới sandbox `spanel-api` | **Đã sửa** (`92e02e3`): `install.sh` trả lại `PrivateDevices=true`, `ProtectKernelModules=true`; `update.sh` bỏ hai dòng `false` (drop-in được ghi lại toàn bộ nên máy dev cũng sạch); bỏ đoạn helper tự thêm drop-in. Mọi lệnh cần thiết bị (`findmnt --verify`, `mount -fav`, `quotacheck`, `quotaon`, `modprobe`) chạy qua `systemd-run -p PrivateDevices=no`. |
| P1-2 `/home` riêng bị chuyển sang `/` | **Đã sửa**: chỉ chuyển khi `SOURCE` có `[subpath]` **và** `MAJ:MIN` của `/home` bằng của `/`. Có test cho cả hai trường hợp đĩa riêng. |
| P1-3 Webmail cổng 2096 bị 403 | **Đã sửa** (`5d09639`): so `hostname` (bỏ cổng) ở cả hai phía; Nginx gửi `Host $http_host`. Có test Origin `:2096` hợp lệ và Origin lạ cùng cổng bị chặn. |
| P1-4 Thiếu zone `limit_req` khi nâng cấp | **Đã sửa**: `mail_reconfigure` và `mail_webmail_host` đều ghi lại file chính khi thiếu zone. `mail_webmail_host` sao lưu khối cũ (`prev_conf`) và khôi phục khi `nginx -t` / certbot lỗi, thay vì xoá. Còn một chỗ chưa làm vậy: P2-3. |
| P1-5 Chặn PHP uploads thư mục con | **Đã sửa** (`5eb57a6`): luật mới `location ^~ /<sub>/wp-content/uploads/ { location ~* \.php$ { deny all; } }`; luật cũ được thay khi bật lại; custom include đặt **trước** `location ~ \.php$` ở cả hai template; `update.sh` render lại vhost vì `backend/app/templates` nằm trong `SITE_REFRESH_INPUTS`. Việc dời include có tác dụng phụ: P2-1. |
| P1-6 Test đỏ + thư không đi theo chủ mới | **Đã sửa** (`cbb490b`): các chuỗi test đều có trong `App.jsx`. Helper mới `mail-move-domain` chuyển `/home/<cũ>/mail/<domain>` sang chủ mới, đổi chủ hộp thư, xoá symlink / file đặc biệt, xoá phiên webmail, rollback khi lỗi. Đã kiểm: `/home/<user>` là `root:<user> 0751` (`spanel-helper.sh` ≈ 8551), khách không đổi tên được `mail/` ⇒ không có race symlink. Thư đến trong lúc chuyển chỉ bị hoãn (Exim gửi lại), không mất. |
| P0-1, P1 phần B (Laravel) | **Còn nguyên** — P0-1 ở trên. |
| P0-2 Phát hành | **Chưa**: không PR, không tag. |

**P2 lần 13:**

| # | Trạng thái |
|---|---|
| 1. Bucket email khoá cả mật khẩu đúng | Đã bỏ khoá, nhưng giờ bucket email **không chặn gì** — P2-2 |
| 2. Từ điển rate-limit không dọn | **Đã sửa** (dọn và giới hạn 4096 khoá) |
| 3. `requirements.txt` chép trước `pip` | **Đã sửa** (chỉ chép khi `pip` thành công) |
| 4. Login không kiểm CSRF | **Đã sửa** (kiểm Origin / `X-Requested-With`, bắt buộc JSON) |
| 5. Ký tự điều khiển trong tên hiển thị | **Đã sửa** (`[\x00-\x1f\x7f]`, cả ở to/cc/bcc/subject) |
| 6. Dependency gián tiếp chưa ghim | **Phần lớn**: đã ghim phiên bản, chưa có `--require-hashes` |
| 7. Dovecot thấy mọi đăng nhập từ `127.0.0.1` | **Đã sửa** bằng `ID x-originating-ip`, nhưng có tác dụng phụ — P2-4 |
| 8. ErrorBoundary che lỗi thật | **Đã sửa** (phân biệt lỗi chunk / lỗi render) |
| 9. `.catch` Ace, `lazyNamed` | **Đã sửa**; cửa sổ chống lặp reload vẫn tính theo thời gian |
| 10. GZip theo Content-Type | **Đã sửa**, có test thật qua middleware |
| 11. `refreshAll` hai lần | **Đã sửa** (dựa vào effect theo `currentUser?.id`) |
| 12. fstab không nguyên tử | **Đã sửa** (file tạm trong `/etc`, `sync`, `mv`, khôi phục khi lỗi) — ghi chú P3-1 |
| 13. `apt-get` module kernel trong request | **Đã bỏ**, thay bằng thông báo hướng dẫn |
| 14. `write_wp_file_text` im lặng khi lỗi | **Đã sửa**: ném 500 kèm lỗi, có test |
| 15. Giới hạn gói khi chuyển chủ | **Một phần**: chuyển chủ dùng hàm `effective_*`, nhưng luồng tạo mới vẫn theo quy ước cũ — P2-5 |

---

## 3. P2 mới — Phần A

1. **Dời custom include lên trước `location ~ \.php$` đổi ý nghĩa cấu hình tuỳ chỉnh của mọi site** (`php.conf.j2` dòng 62/65, `wordpress.conf.j2` 106/109). Location regex trong `nginx_custom` nay thắng bộ xử lý PHP. Một luật tuỳ chỉnh thường gặp như `location ~ ^/api/ { try_files $uri $uri/ /index.php?$args; }` sẽ trả `/api/x.php` dưới dạng **file tĩnh**, tức lộ mã nguồn PHP. Mọi site được render lại khi cập nhật, nên thay đổi này áp cho tất cả site cùng lúc. Chỉ riêng luật `^~` đã đủ sửa P1-5. **Sửa:** để include về chỗ cũ, giữ luật `^~` (luật cũ đã được thay khi bật lại). Nếu cần một vùng chạy trước PHP, thêm include thứ hai chỉ dành cho luật do panel sinh ra.
2. **Bucket theo email nay chỉ để trang trí** (`webmail/backend/main.py` 149–181). `_check_login_rate_limit` không còn xem bucket email. `_record_login_failure` trả 429 *sau khi* đã thử mật khẩu, nên mật khẩu đúng vẫn qua. Một botnet nhiều IP (mỗi IP dưới 10 lần / 5 phút) dò không giới hạn trên một hộp thư. Ngoài ra, khoá theo IP tính theo từng địa chỉ: với IPv6, một mạng /64 có 2⁶⁴ địa chỉ, nên giới hạn này vô tác dụng (cả `limit_req` dùng `$binary_remote_addr`). **Sửa:** sau N lần sai, làm chậm dần theo email (ví dụ chờ 2ⁿ giây, tối đa 30 giây, trước khi gọi IMAP) thay vì khoá; gộp IPv6 theo /64 ở cả app và Nginx (`map` lấy tiền tố /64).
3. **`mail_write_webmail_nginx` vẫn xoá file chính khi `nginx -t` lỗi** (`spanel-helper.sh` 3640). Hàm này nay được gọi trong `mail_reconfigure`, tức chạy lúc cập nhật. Nếu `nginx -t` lỗi vì một file khác, webmail cổng 2096 mất hẳn cho tới lần cài lại. **Sửa:** dùng cùng cách `prev_conf` như `mail_webmail_host`.
4. **`login_trusted_networks = 127.0.0.1/32 ::1/128` cho mọi tiến trình trên máy quyền khai IP gốc** (`spanel-helper.sh` 3306). PHP / cron / terminal của bất kỳ khách nào kết nối `127.0.0.1:143` đều gửi được `ID ("x-originating-ip" "<IP tuỳ ý>")`. Dovecot sẽ ghi log và tính phạt đăng nhập (auth penalty) theo IP giả, nên khách có thể đổi IP mỗi lần để né phạt khi dò mật khẩu hộp thư khách khác từ chính máy chủ. Hiện fail2ban chỉ có jail `sshd` nên chưa bị lợi dụng để chặn IP người khác. Khi thêm jail Dovecot thì lợi dụng được. **Sửa:** cho webmail kết nối qua một địa chỉ riêng (ví dụ bind nguồn `127.0.0.2`, chỉ tin địa chỉ đó), hoặc qua listener / unix socket riêng mà chỉ user `SPanel-webmail` vào được.
5. **Quy ước giới hạn gói vẫn lệch giữa các luồng:**
   - Tạo website (`websites.py` 694) và gán domain (1161): user trước, `0` = không giới hạn.
   - Chuyển chủ (`effective_website_limit`, 158): gói trước, `0` = tắt.
   - Giao diện Packages hiển thị `0` là "0 Sites"; chỉ `-1` hiện `∞`.

   Hệ quả: gói đặt `0` website (ví dụ gói chỉ có mail) vẫn tạo được **không giới hạn** site, nhưng lại không nhận được site chuyển sang. SFTP cũng tương tự: `GET /sftp-accounts/status` (≈ 299) trả `unlimited: true` khi giới hạn là `0`, trong khi `POST` từ chối. **Sửa:** một module `quota.py` với `effective_limit(user, field)` duy nhất (gói là nguồn chính, `-1` = không giới hạn, `0` = tắt), dùng cho mọi luồng tạo / gán / chuyển / hiển thị. Kèm migration chuẩn hoá dữ liệu cũ (`0` → `-1` ở những cột trước đây coi `0` là không giới hạn) và test theo bảng (field × giá trị × luồng).

**P3 (ghi nhận):**

1. `mount -fav --fstab tmp` bỏ qua mount đã gắn (luôn có `/` hoặc `/home`), nên không kiểm được chính dòng vừa sửa. Nó lại có thể báo lỗi giả với dòng `nofail` / NFS / USB vắng mặt. `findmnt --verify` đã đủ; nên bỏ `mount -fav`, hoặc chỉ coi kết quả của nó là cảnh báo.
2. Webmail tin `X-Forwarded-Host` do client gửi (`main.py` 455). Hiện chưa khai thác được qua CSRF vì header tuỳ chỉnh cần preflight, nhưng Nginx nên xoá header này (`proxy_set_header X-Forwarded-Host "";`).
3. Menu App Installer đã bỏ nhưng router `app_installer` vẫn đăng ký (`main.py` 251) và `AppInstaller.jsx` vẫn còn ⇒ API không có giao diện. Nên gỡ hoặc giữ có chủ đích.
4. Docker / Node.js hiện trong menu cả khi chưa cài addon (`App.jsx` 3316–3318). Trang có báo "addon missing", nhưng nên ẩn mục khỏi menu.
5. Luật `^~ /<sub>/wp-content/uploads/` thắng cả location regex của ảnh / CSS ⇒ ảnh trong uploads thư mục con mất header cache dài hạn. Thêm `expires` vào trong khối đó.
6. Sau chuyển chủ, lỗi `mail.sync_quietly` bị nuốt. Nên trả cảnh báo trong response để admin biết Dovecot chưa trỏ đúng home mới.

---

## 4. Điểm mạnh của đợt này

- Sửa đúng chỗ, đúng cách lần 13 mô tả: MAJ:MIN, `^~`, so `hostname`, `prev_conf`, `pip_ok`, fstab qua `mv`.
- Test gần với hành vi thật hơn: quota chạy helper bash với `findmnt` giả; GZip chạy qua middleware; webmail kiểm 403 / 429 / 200 qua `TestClient`; `write_wp_file_text` kiểm mã lỗi.
- Biết tự tách tính năng chưa xong (`63120eb`). Vấn đề chỉ là bước đưa lại sau đó.

---

## 5. Bảng điểm

| Trụ cột | Trọng số | L12 | L13 | **L14** (`48f7c87`) | Nếu chỉ phát hành phần A | Lý do |
|---|---:|---:|---:|---:|---:|---|
| 1. Bảo mật & tách đặc quyền | 15% | 58 | 58 | **59** | 64 | Sandbox trả lại; P0 Laravel còn |
| 2. Tự vá lỗi & cập nhật | 12% | 68 | 69 | **69** | 74 | Chưa PR / tag / CI; tính năng bị đưa lại |
| 3. Chịu lỗi & tự phục hồi | 12% | 71 | 72 | **73** | 74 | fstab nguyên tử; rollback vhost webmail |
| 4. Sao lưu & DR | 10% | 76 | 77 | **77** | 77 | |
| 5. Chịu tải & cách ly | 12% | 72 | 72 | **73** | 74 | Quota đúng đĩa |
| 6. Đa runtime | 10% | 73 | 72 | **72** | 74 | Uploads thư mục con kín; Laravel giả nay hiện cho End User |
| 7. WHMCS & hệ sinh thái | 10% | 70 | 70 | **70** | 70 | |
| 8. UX, RBAC & giám sát | 7% | 78 | 79 | **80** | 80 | ErrorBoundary đúng; bỏ refresh thừa |
| 9. Mail & DNS | 6% | 70 | 71 | **74** | 75 | 2096, zone, chuyển maildir, IP thật trong Dovecot |
| 10. Cụm đa máy chủ | 6% | 56 | 56 | **56** | 56 | |
| **Tổng** | 100% | ≈ 70 | ≈ 69–70 | **≈ 70** | **≈ 72** | |

---

## 6. Lộ trình lên điểm tối đa

### 6.1. Thang điểm này nói gì

- Thang và trọng số giữ nguyên của báo cáo gốc. Trên thang này, cPanel / Plesk / DirectAdmin hợp lý ở **85–95**: họ có lịch sử vận hành, audit độc lập, hệ sinh thái.
- **0 → 85: mã và quy trình.** Phần này nhóm tự làm được.
- **85 → 95+: bằng chứng.** Audit bảo mật bên ngoài, benchmark công khai có phương pháp, pilot 3–6 tháng trên máy thật có số liệu sự cố, tài liệu và cộng đồng. Không có các thứ này thì người chấm không thể cho trên ~88, dù mã tốt đến đâu.
- **Đòn bẩy lớn nhất theo trọng số:** Bảo mật (15%), rồi Tự cập nhật, Chịu lỗi, Cách ly (mỗi trụ 12%). Đưa 4 trụ này lên 90 thì tổng tăng khoảng +11, nhiều hơn mọi tính năng mới. Làm đủ các mục ở 6.3 thì tổng ≈ 70 + 19,7 ≈ **90**.

### 6.2. Ba bước

| Bước | Bản | Mục tiêu | Nội dung chính |
|---|---|---|---|
| 0 | **1.0.8** (ngay) | ≈ 72 | PR từ `fix/1.0.7-round13-part-a` (đã không có Laravel), CI xanh, tag `v1.0.8`, sửa P2-1 và P2-3 |
| 1 | **1.0.9** (2–4 tuần) | ≈ 78 | CI cài thật trên VM, ký và kiểm bản phát hành, quota thống nhất, P2 còn lại, thư viện thao tác file an toàn |
| 2 | **1.1** (1–3 tháng) | ≈ 85 | Thu nhỏ helper root, giám sát / metrics, sao lưu mã hoá + diễn tập khôi phục tự động, Laravel làm thật |
| 3 | **1.2+** (3–12 tháng) | 90+ | Audit độc lập, benchmark công khai, pilot, cụm đa máy chủ thật, hệ sinh thái |

### 6.3. Việc cụ thể theo trụ cột

Ghi chú: "đã có" là kết quả `git grep` trên `48f7c87`; một số mục cần tác giả xác nhận thêm về mức hoàn thiện.

**1. Bảo mật & tách đặc quyền (59 → 90; +4,7 điểm tổng)**

- Đóng P0-1. Sau đó **cấm theo cơ chế**, không theo trí nhớ: thêm kiểm tra CI (semgrep hoặc script) chặn `Path.unlink()`, `open()`, `write_text()`, `shutil.*` trên đường dẫn trong `site_root` ở tiến trình panel. Mọi thao tác trong cây thư mục khách phải qua một thư viện duy nhất (`safe_tenant_fs`) dùng `openat` + `O_NOFOLLOW` + kiểm `st_uid`, hoặc qua helper dưới user khách. Lỗi họ symlink đã xuất hiện ở lần 3, 6, 10, 13; chỉ có quy tắc tự động mới chặn được dứt điểm.
- **Thu nhỏ helper root (12.727 dòng bash):** tách các action nguy hiểm nhất (file, mail, quota, fstab, user) sang một chương trình nhỏ viết bằng Python / Go, dùng `openat` / `renameat` thay cho chuỗi đường dẫn. Thêm **fuzz tham số helper** trong CI (sinh tên user / domain / đường dẫn độc hại, kiểm helper luôn `deny`).
- `sudoers` theo từng action thay vì wildcard; mỗi action có schema tham số.
- Sửa P2-4 (Dovecot trusted networks).
- Thêm AppArmor profile cho `php-fpm` từng user và cho `spanel-api`, ngoài systemd sandbox.
- Mã hoá bí mật lúc lưu (mật khẩu DB của khách, token API) bằng khoá ngoài `.env`, xoay vòng được.
- **Bằng chứng để vượt 85:** pentest / audit độc lập cho helper, file manager, terminal, webmail, có báo cáo công khai; chương trình nhận báo lỗi bảo mật (`SECURITY.md`, thời hạn phản hồi).

**2. Tự vá lỗi & cập nhật (69 → 90; +2,5)**

- **CI chạy trên mọi nhánh**, và thêm job **cài thật trên VM** Ubuntu 22.04 / 24.04 và Debian 12: `install.sh` → tạo user / domain / DB / mail / SSL → `update.sh` từ tag trước → `update.sh --rollback`. Đây là thứ đã thiếu từ báo cáo gốc. Hiện CI có 5 job (backend, shell, whmcs-module, frontend, large-deletion-guard), chưa có job nào chạy trên máy thật.
- **Ký bản phát hành:** `git tag -s` (hoặc sigstore / minisign cho tarball); `update.sh` chạy `git verify-tag` trước khi `rsync`. Hiện `update.sh` không kiểm chữ ký.
- Kênh `beta` → `release` có thời gian chờ; tự rollback khi health check sau cập nhật lỗi (đã có snapshot, cần tự động hoá điều kiện).
- `apt-mark hold` / `unhold` có kiểm soát cho stack lõi (nginx, mariadb, php-fpm, exim, dovecot); cập nhật lõi qua panel có kiểm thử trước. Hiện chưa có `apt-mark hold`.
- Release tự động: tag sinh từ `VERSION`, CHANGELOG bắt buộc, không thể tạo tag trùng commit với tag cũ (lỗi `v1.0.6` = `v1.0.7`).

**3. Chịu lỗi & tự phục hồi (73 → 90; +2)**

- Gom mẫu "ghi tạm → kiểm → `mv` → khôi phục" (đã làm đúng ở fstab, vhost webmail) thành hàm dùng chung trong helper. Áp cho mọi file cấu hình hệ thống: nginx, exim, dovecot, php-fpm, sudoers, cron.
- Test **hỗn loạn** (chaos) trong job VM: kill nginx / php-fpm / mariadb / dovecot, làm đầy đĩa, `nginx -t` lỗi giữa chừng. Kiểm watchdog tự khôi phục và panel báo đúng.
- Mọi action helper phải idempotent (chạy lại 2 lần cho cùng kết quả), có test.
- SQLite đã bật WAL (`database.py` 23); thêm sao lưu nóng định kỳ + `integrity_check` có cảnh báo.

**4. Sao lưu & DR (77 → 90; +1,3)**

- Mã hoá phía client (age / AES-GCM) với khoá do admin giữ; khôi phục được trên máy mới chỉ với khoá + bản sao lưu.
- Khôi phục chọn lọc: một site, một DB, một hộp thư, một thư mục (hiện chỉ thấy `restore_user_backup` / `restore_backup` cấp tài khoản).
- **Diễn tập khôi phục tự động:** job VM sao lưu → xoá → khôi phục → so checksum.
- S3 Object Lock / bản bất biến chống ransomware; ghi RPO / RTO cam kết vào tài liệu; binlog MariaDB cho khôi phục theo thời điểm (PITR).

**5. Chịu tải & cách ly (73 → 90; +2)**

- Đã có: PHP-FPM master riêng từng user trong `user-<uid>.slice`, quota kernel. Còn: giới hạn IO (`IOWeight` / `IOReadBandwidthMax`), giới hạn số tiến trình và inode, quota MariaDB theo dung lượng.
- **Benchmark công khai** (k6 / wrk): cấu hình máy, kịch bản, số liệu; chạy lại mỗi bản phát hành để phát hiện tụt hiệu năng.
- Kiểm lại các lỗi hiệu năng của báo cáo gốc (khoá FastCGI cache có tham số quảng cáo, QUIC) bằng test render template.

**6. Đa runtime (72 → 90; +1,8)**

- Laravel làm thật, theo mục 3 lần 13: chạy lệnh qua `terminal-exec`, queue worker là unit systemd dưới user khách, mục kiểm bảo mật kiểm thật, bỏ mọi output dựng sẵn.
- Một khung "runtime" chung cho Node / Python / Laravel / Docker: cài, chạy, log, restart, health check, worker. Không viết lại cho từng loại.
- Test e2e trên VM cho mỗi runtime: tạo app mẫu, gọi HTTP, nhận 200.

**7. WHMCS & hệ sinh thái (70 → 88; +1,8)**

- Đã có module WHMCS đủ chức năng chính. Thêm FOSSBilling / Blesta / HostBill.
- API công khai có OpenAPI, token theo phạm vi (scope), giới hạn tần suất, ví dụ dùng. Tài liệu ở một site riêng.
- SDK addon (manifest, quyền, hook) để bên thứ ba viết addon mà không sửa lõi.
- **Bằng chứng:** người dùng thật, issue / PR từ bên ngoài, kênh hỗ trợ.

**8. UX, RBAC & giám sát (80 → 92; +0,8)**

- Tách `App.jsx` (5.549 dòng) theo route / context. Thêm test e2e Playwright cho các luồng chính (đăng nhập, login-as, tạo site, chuyển chủ).
- Endpoint metrics (Prometheus / OpenMetrics) + cảnh báo (đĩa, quota, chứng chỉ sắp hết hạn, hàng đợi mail, dịch vụ chết). Hiện chưa có `/metrics`.
- Xuất nhật ký audit (JSON / syslog), giữ bất biến; i18n đủ; kiểm a11y cơ bản.

**9. Mail & DNS (74 → 90; +1)**

- Sửa P2-2, P2-3, P2-4.
- MTA-STS, TLS-RPT, báo cáo DMARC, xoay vòng khoá DKIM định kỳ, autoconfig / autodiscover.
- DNSSEC: đã có mã (5 file). Cần test ký / xoay khoá và kiểm DS ở registrar.
- Theo dõi danh tiếng IP gửi (blacklist), hàng đợi và tỉ lệ bị trả về.

**10. Cụm đa máy chủ (56 → 85; +1,7)**

- Đã có đồng bộ DNS và `cluster_transfer.py`. Cần: đặt DB khách lên node khác thật, chuyển tài khoản giữa node gần như không gián đoạn (rsync hai pha + chuyển DNS), một control plane quản lý nhiều node, theo dõi sức khoẻ node, chuyển dự phòng khi node chết.
- Trọng số chỉ 6%, nên làm sau cùng, trừ khi định hướng bán cho nhà cung cấp lớn.

### 6.4. Thứ tự đề xuất (ảnh hưởng điểm / công sức)

1. Gỡ Laravel khỏi nhánh phát hành, PR, tag `v1.0.8`. *(+2, vài giờ)*
2. Job CI cài thật trên VM, chạy trên mọi nhánh. *(+3 qua trụ 2 và 3; ngăn được phần lớn lỗi kiểu "chạy giả" trong các lần 9–13)*
3. Quy tắc CI chặn thao tác file của panel trong cây khách + thư viện `safe_tenant_fs`. *(+2; chặn hẳn họ lỗi symlink)*
4. Ký và kiểm bản phát hành; `apt-mark hold`. *(+1,5)*
5. Module `quota.py` thống nhất. *(+0,5; hết lớp lỗi giới hạn gói)*
6. Thu nhỏ helper root + fuzz. *(+2–3; việc lớn nhất của trụ bảo mật)*
7. Metrics + cảnh báo; sao lưu mã hoá + diễn tập khôi phục. *(+2)*
8. Audit độc lập, benchmark, pilot. *(phần 85 → 95)*

---

*Giới hạn: chỉ đọc mã ở `48f7c87`; không chạy trên VPS, không chạy được test, chưa có CI cho 8 commit này. Mọi mục P1 của lần 13 và các P2 mới đều được kiểm trực tiếp trên mã; tình trạng "đã có / chưa có" ở mục 6 là kết quả `git grep`, cần tác giả xác nhận mức hoàn thiện. Thực hiện bởi Claude (AI của Anthropic). Mã được thẩm định không phải do Claude viết.*
