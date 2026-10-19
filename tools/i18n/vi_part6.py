# Vietnamese, part 6: empty states, tile descriptions, status lines and the
# remaining prose.
#
# Found by auditing what t() does NOT wrap rather than by extracting what looks
# translatable. That order matters: the wrapper only touches strings that are
# already dictionary keys, so a string the first extractor missed could never
# become one - the loop was closed until the audit opened it.
#
# Fragments that sit either side of an interpolation are deliberately absent.
# "Using {n} of {m}" and "roughly {x} MB per site" cannot be translated in
# pieces, because Vietnamese puts the pieces in a different order; stitching
# them would produce a sentence that says something the English does not. Those
# need t() with parameters and a rewrite at each call site.

PART6 = {
    # --- empty states ---
    "No data yet": "Chưa có dữ liệu",
    "No databases found.": "Không tìm thấy database nào.",
    "No databases match this search.": "Không có database nào khớp tìm kiếm này.",
    "No websites match this search.": "Không có website nào khớp tìm kiếm này.",
    "No rules yet. Only the protected ports are open.":
        "Chưa có rule nào. Chỉ các cổng được bảo vệ đang mở.",
    "No rules match that filter.": "Không có rule nào khớp bộ lọc đó.",
    "No addresses are banned right now.": "Hiện không có địa chỉ nào bị cấm.",
    "No addresses match that filter.": "Không có địa chỉ nào khớp bộ lọc đó.",
    "No DirectAdmin backups uploaded": "Chưa tải lên bản backup DirectAdmin nào",
    "No file selected": "Chưa chọn file nào",
    "No output yet.": "Chưa có kết quả nào.",
    "No time recorded": "Chưa ghi nhận thời gian",
    "Log is empty.": "Nhật ký trống.",
    "Log file has not been created yet.": "File nhật ký chưa được tạo.",
    "Reading the log": "Đang đọc nhật ký",
    "Blocklist status not loaded yet.": "Chưa tải trạng thái danh sách chặn.",
    "Press Refresh to load the status.": "Bấm Tải lại để xem trạng thái.",
    "Click Refresh to load WAF status.": "Bấm Tải lại để xem trạng thái WAF.",
    "Click View logs to load update logs.":
        "Bấm Xem nhật ký để tải nhật ký cập nhật.",

    # --- dashboard tile descriptions ---
    "The parts that are not in a default install. Add what you need, remove what you do not — removing turns the feature off and deletes nothing it created.":
        "Những phần không có trong bản cài mặc định. Thêm cái cần, gỡ cái không cần — gỡ chỉ tắt tính năng, không xoá bất cứ thứ gì nó đã tạo ra.",

    # --- selects and choices ---
    "-- Select website --": "-- Chọn website --",
    "-- Select website or application --": "-- Chọn website hoặc ứng dụng --",
    "-- Scan now: pick a target --": "-- Quét ngay: chọn mục tiêu --",
    "None / static PHP": "Không / PHP tĩnh",
    "Script or package": "Script hoặc package",
    "All from the global list": "Toàn bộ từ danh sách chung",
    "Full Nginx config": "Toàn bộ cấu hình Nginx",
    "Full user backup": "Backup toàn bộ tài khoản",
    "Attach first domain": "Gắn tên miền đầu tiên",
    "Save and apply to all": "Lưu và áp dụng cho tất cả",
    "Update OS now": "Cập nhật hệ điều hành ngay",
    "Update panel now": "Cập nhật panel ngay",
    "Up to date": "Đã mới nhất",
    "Updated": "Đã cập nhật",
    "Open": "Mở",

    # --- firewall and WAF status ---
    "Bans take effect": "Lệnh cấm có hiệu lực",
    "Bans NOT reaching iptables": "Lệnh cấm KHÔNG tới được iptables",
    "Seeing NO log": "KHÔNG thấy nhật ký",
    "Custom rules are arbitrary ModSecurity directives, so only an administrator can change them. Ask your provider if you need a rule added or excluded.":
        "Rule tuỳ chỉnh là chỉ thị ModSecurity tự do, nên chỉ quản trị viên được sửa. Hãy nhờ nhà cung cấp nếu bạn cần thêm hoặc loại trừ một rule.",
    "Blocked on every website on this server. A site can add more of its own from its page.":
        "Bị chặn trên mọi website của server này. Mỗi site có thể tự thêm bot riêng ở trang của nó.",

    # --- nginx and applications ---
    "This is read-only. SPanel manages the main vhost template.":
        "Phần này chỉ đọc. SPanel quản lý mẫu vhost chính.",
    "Managed settings rewrite the main vhost safely. Custom Nginx is still stored as a separate include.":
        "Các cài đặt được quản lý sẽ ghi lại vhost chính một cách an toàn. Nginx tuỳ chỉnh vẫn được lưu thành một include riêng.",
    "Nginx will forward this domain to the selected application on 127.0.0.1, including WebSocket upgrades.":
        "Nginx sẽ chuyển tên miền này tới ứng dụng đã chọn trên 127.0.0.1, bao gồm cả nâng cấp WebSocket.",
    "Your package does not include Applications. Contact an administrator to upgrade.":
        "Gói của bạn không bao gồm Ứng dụng. Hãy liên hệ quản trị viên để nâng cấp.",
    "Admin can set directly.": "Quản trị viên đặt được trực tiếp.",

    # --- cron ---
    "Discard output so cron does not try to mail it.":
        "Bỏ kết quả đầu ra để cron không cố gửi email.",
    "Keep output in a log file inside this website.":
        "Giữ kết quả đầu ra vào một file nhật ký trong website này.",

    # --- SFTP, sessions, permissions ---
    "This login still uses your panel password. Anyone who guesses it over SFTP is also in the panel. Set a separate password — your panel password will stop working for SFTP the moment you do.":
        "Tài khoản này vẫn dùng mật khẩu panel của bạn. Ai đoán được nó qua SFTP thì cũng vào được panel. Hãy đặt một mật khẩu riêng — ngay khi đặt, mật khẩu panel sẽ không còn dùng được cho SFTP.",
    "Each account reaches one website and nothing else — not your other sites, and not the server. It signs in over SFTP on port 22 with its own password, which is separate from your panel password.":
        "Mỗi tài khoản chỉ vào được một website, không gì khác — không vào được site khác của bạn, cũng không vào được server. Nó đăng nhập qua SFTP ở cổng 22 bằng mật khẩu riêng, tách biệt với mật khẩu panel.",
    "A passkey is your only second factor. Reach the panel by a different domain and there is no second factor at all — turn on Google Authenticator below as well.":
        "Passkey là lớp xác thực thứ hai duy nhất của bạn. Nếu vào panel bằng một tên miền khác thì sẽ không có lớp thứ hai nào — hãy bật thêm Google Authenticator bên dưới.",
    "Any permission combination is allowed. The setuid and sticky bits are not — setgid on a folder is the only special bit the panel sets.":
        "Mọi tổ hợp quyền đều được phép. Bit setuid và sticky thì không — setgid trên thư mục là bit đặc biệt duy nhất panel đặt.",
    "Role changes sign the user out of existing sessions.":
        "Đổi vai trò sẽ đăng xuất người dùng khỏi các phiên đang mở.",
    "Role is locked for the active admin session.":
        "Vai trò bị khoá với phiên quản trị đang hoạt động.",
    "This account is suspended. Contact an administrator.":
        "Tài khoản này đang bị khoá. Hãy liên hệ quản trị viên.",
    "Your session expired. Please log in again.":
        "Phiên của bạn đã hết hạn. Hãy đăng nhập lại.",
    "Password changed. Please log in again.":
        "Đã đổi mật khẩu. Hãy đăng nhập lại.",
    "Minimum 12 characters.": "Tối thiểu 12 ký tự.",
    "This creates the first hosted site for the current account.":
        "Việc này tạo site đầu tiên cho tài khoản hiện tại.",

    # --- malware scanner ---
    "Watches the website directories continuously and checks new files in short batches (~15 seconds). Catches something arriving over SFTP or through a plugin at once, instead of waiting for the next Level 1 scheduled scan.":
        "Theo dõi liên tục thư mục website và kiểm tra file mới theo từng lô ngắn (~15 giây). Bắt ngay thứ vừa vào qua SFTP hoặc qua plugin, thay vì chờ tới lần quét theo lịch của Mức 1.",
    "Uploaded files are scanned.": "File tải lên được quét.",
    "Uploaded files are no longer scanned.": "File tải lên không còn được quét.",
    "Turning on real-time protection...": "Đang bật bảo vệ thời gian thực...",
    "Turning on scan-on-upload...": "Đang bật quét khi tải lên...",

    # --- panel settings and updates ---
    "Panel settings updated.": "Đã cập nhật cài đặt panel.",
    "Panel SSL installed. The panel may restart in a moment.":
        "Đã cài SSL cho panel. Panel có thể khởi động lại trong giây lát.",
    "Panel SSL disabled. The panel remains reachable by IP and port over HTTP.":
        "Đã tắt SSL của panel. Panel vẫn vào được bằng IP và cổng qua HTTP.",
    "Panel update failed.": "Cập nhật panel thất bại.",
    "This server has no IPv6 address, so this feature cannot be used.":
        "Server này không có địa chỉ IPv6, nên không dùng được tính năng này.",
    "Website WAF rules saved.": "Đã lưu rule WAF của website.",
    "Token copied. It is not shown again.":
        "Đã sao chép token. Nó sẽ không hiện lại.",
    "Create a token above and it appears in these ready to copy. Otherwise replace YOUR_TOKEN yourself.":
        "Tạo một token ở trên và nó sẽ tự điền vào đây, sẵn sàng để copy. Nếu không thì bạn tự thay YOUR_TOKEN.",
    "Give Claude Code, Cursor or VS Code a token and it can read and operate the panel with exactly your own permissions - nothing more.":
        "Cấp một token cho Claude Code, Cursor hoặc VS Code là nó đọc và thao tác được trên panel với đúng quyền của bạn — không hơn.",
    "Each application runs on its own port under its own systemd unit. Point a website at one by setting its mode to":
        "Mỗi ứng dụng chạy trên cổng riêng, dưới systemd unit riêng. Trỏ một website vào nó bằng cách đặt chế độ website thành",

    # --- progress labels the audit turned up ---
    "Saving SFTP target...": "Đang lưu đích lưu SFTP...",
    "Uploading DA backup...": "Đang tải backup DA lên...",
    "Uploading full user backups...": "Đang tải backup toàn bộ tài khoản lên...",

    # --- the toggles' own tooltips ---
    "Switch to dark mode": "Chuyển sang chế độ tối",
    "Switch to light mode": "Chuyển sang chế độ sáng",
}

# The dashboard map: tile labels, the line under each tile, and the group
# headings. These are the first Vietnamese a customer sees and the audit could
# not find them - they live in arrays and are translated where they are drawn,
# so no wrapper ever looks at them. Only reading the array and asking the
# dictionary turns them up.
PART6.update({
    # group headings
    "Accounts": "Tài khoản",
    "Server": "Máy chủ",

    # tile labels the dictionary had not reached
    "AI assistants": "Trợ lý AI",
    "Cron": "Cron",
    "SSL": "SSL",
    "WAF": "WAF",
    "Fail2ban": "Fail2ban",

    # the line under each tile
})
