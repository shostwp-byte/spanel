# Vietnamese, part 3: accounts, login, 2FA, passkeys, files, cron, malware,
# addons, the panel's own settings, and every message the interface produces.

PART3 = {
    # --- accounts and packages ---
    "Add User": "Thêm người dùng",
    "Create user": "Tạo người dùng",
    "Create account": "Tạo tài khoản",
    "Save account": "Lưu tài khoản",
    "Close user editor": "Đóng trình sửa người dùng",
    "Panel user sections": "Các mục của người dùng panel",
    "Create package": "Tạo gói",
    "Custom limits": "Giới hạn riêng",
    "Storage MB": "Dung lượng MB",
    "Storage limit (MB)": "Giới hạn dung lượng (MB)",
    "Memory (MB)": "Bộ nhớ (MB)",
    "Admin account": "Tài khoản quản trị",
    "Admin user": "Tài khoản quản trị",
    "Admin email": "Email quản trị",
    "Admin password": "Mật khẩu quản trị",
    "Change password": "Đổi mật khẩu",
    "Current password": "Mật khẩu hiện tại",
    "New password": "Mật khẩu mới",
    "Repeat new password": "Nhập lại mật khẩu mới",
    "Repeat password": "Nhập lại mật khẩu",
    "Confirm password": "Xác nhận mật khẩu",
    "Set password": "Đặt mật khẩu",
    "Generate random password": "Tạo mật khẩu ngẫu nhiên",
    "Min 12 characters": "Tối thiểu 12 ký tự",
    "Credentials": "Thông tin đăng nhập",
    "Generated credentials (click to show)": "Thông tin đã tạo (bấm để xem)",
    "Current panel users and service limits.":
        "Người dùng panel hiện có và giới hạn dịch vụ.",
    "Manage users, packages, and domain ownership.":
        "Quản lý người dùng, gói và quyền sở hữu tên miền.",
    "Create, edit, delete, and review reusable user limits.":
        "Tạo, sửa, xoá và xem lại các bộ giới hạn dùng chung.",
    "Panel username is also the Linux user. Login as a user before creating websites for that account.":
        "Tên đăng nhập panel cũng chính là Linux user. Hãy đăng nhập với tư cách người dùng đó trước khi tạo website cho họ.",
    "No users found.": "Không tìm thấy người dùng nào.",
    "No packages found.": "Không tìm thấy gói nào.",
    "No permission.": "Không có quyền.",
    "This page reports on the server itself, so only administrators can see it.":
        "Trang này báo cáo về chính server, nên chỉ quản trị viên xem được.",

    # --- login, 2FA, passkeys ---
    "Login successful.": "Đăng nhập thành công.",
    "Keep me signed in for 30 days": "Giữ đăng nhập trong 30 ngày",
    "Authentication code": "Mã xác thực",
    "Authenticator code": "Mã từ ứng dụng xác thực",
    "Authentication code is required.": "Cần nhập mã xác thực.",
    "Enter the code from your authenticator app.":
        "Nhập mã từ ứng dụng xác thực của bạn.",
    "Enter your authentication code.": "Nhập mã xác thực của bạn.",
    "Use a code instead": "Dùng mã thay thế",
    "Enable 2FA": "Bật 2FA",
    "Disable 2FA": "Tắt 2FA",
    "Reset 2FA": "Đặt lại 2FA",
    "2FA enabled.": "Đã bật 2FA.",
    "2FA disabled.": "Đã tắt 2FA.",
    "Google Authenticator 2FA": "Google Authenticator 2FA",
    "Generate QR": "Tạo mã QR",
    "No setup code generated.": "Chưa tạo mã thiết lập.",
    "Your current password confirms it is you, the same as when turning on Google Authenticator.":
        "Mật khẩu hiện tại để xác nhận đúng là bạn, giống như khi bật Google Authenticator.",
    "Current password is required to change password.":
        "Cần mật khẩu hiện tại để đổi mật khẩu.",
    "Passwords do not match.": "Mật khẩu nhập lại không khớp.",
    "Password must be at least 12 characters.": "Mật khẩu phải có ít nhất 12 ký tự.",
    "Password contains invalid characters. Use only ASCII characters.":
        "Mật khẩu có ký tự không hợp lệ. Chỉ dùng ký tự ASCII.",
    "Email is required.": "Cần nhập email.",
    "Passkey": "Passkey",
    "Add passkey": "Thêm passkey",
    "Passkey name": "Tên passkey",
    "Device name, e.g. MacBook": "Tên thiết bị, ví dụ MacBook",
    "No passkeys yet.": "Chưa có passkey nào.",
    "A passkey is tied to the domain": "Passkey gắn với tên miền",
    "Touch your passkey to sign in.": "Chạm passkey để đăng nhập.",
    "Signed in with a passkey.": "Đã đăng nhập bằng passkey.",
    "Try the passkey again": "Thử lại passkey",
    "Sign in with a fingerprint, Face ID or a security key instead of typing a code.":
        "Đăng nhập bằng vân tay, Face ID hoặc khoá bảo mật thay vì gõ mã.",
    "This browser does not support passkeys.": "Trình duyệt này không hỗ trợ passkey.",
    "This browser does not support passkeys. Use the code from your authenticator app.":
        "Trình duyệt này không hỗ trợ passkey. Hãy dùng mã từ ứng dụng xác thực.",
    "That passkey did not work. Try again, or use the code from your authenticator app.":
        "Passkey đó không dùng được. Thử lại, hoặc dùng mã từ ứng dụng xác thực.",
    "Could not create the passkey. The device may have refused it, or you cancelled.":
        "Không tạo được passkey. Có thể thiết bị từ chối, hoặc bạn đã huỷ.",

    # --- files and cron ---
    "Change permissions": "Đổi quyền",
    "Check file": "Kiểm tra file",
    "No files in this folder.": "Thư mục này trống.",
    "Mode must be octal, for example 644 or 755.":
        "Quyền phải ở hệ 8, ví dụ 644 hoặc 755.",
    "Setgid — new files inside keep the folder's group. SPanel sets this on site folders; leave it on unless you know otherwise.":
        "Setgid — file mới tạo bên trong giữ nguyên group của thư mục. SPanel đặt sẵn cho thư mục site; cứ để bật trừ khi bạn có lý do khác.",
    "File download failed.": "Tải file về thất bại.",
    "File upload failed.": "Tải file lên thất bại.",
    "Upload code to": "Tải mã nguồn lên",
    "Add cron": "Thêm cron",
    "Cron runs as": "Cron chạy dưới quyền",
    "Cron job deleted.": "Đã xoá cron job.",
    "No cron jobs found for this website.": "Website này chưa có cron job nào.",

    # --- malware scanner ---
    "Install the scanner": "Cài trình quét",
    "Scan history": "Lịch sử quét",
    "Scan started.": "Đã bắt đầu quét.",
    "Scanning...": "Đang quét...",
    "Files scanned": "File đã quét",
    "Incremental scan": "Quét thay đổi",
    "Scan uploaded files": "Quét file tải lên",
    "Update signatures": "Cập nhật mẫu nhận diện",
    "Automatic schedule": "Lịch tự động",
    "Level 1 — Scheduled scans": "Mức 1 — Quét theo lịch",
    "Level 2 — Real-time protection": "Mức 2 — Bảo vệ thời gian thực",
    "The panel scans on its own, with nobody pressing anything. Pick an hour when few visitors are around.":
        "Panel tự quét, không cần ai bấm gì. Chọn giờ ít khách truy cập.",
    "These are the scanner's own family names (php.base64..., for instance), not common virus names — there is nowhere else to look them up.":
        "Đây là tên họ mã độc do chính trình quét đặt (ví dụ php.base64...), không phải tên virus phổ thông — không có chỗ nào khác để tra.",
    "This server has no resident clamd — start one first and each scan drops to milliseconds.":
        "Server này chưa chạy clamd thường trú — bật nó lên thì mỗi lần quét chỉ còn vài mili giây.",
    "Installing the scanner in the background (1-3 minutes). Press Refresh for an update.":
        "Đang cài trình quét ở chế độ nền (1-3 phút). Bấm Tải lại để xem tiến độ.",

    # --- addons, MCP, tokens ---
    "Go to Addons": "Tới trang Tiện ích",
    "No addons yet.": "Chưa có tiện ích nào.",
    "Worth knowing first": "Nên biết trước",
    "The addon is off": "Tiện ích đang tắt",
    "Installed · off": "Đã cài · đang tắt",
    "This panel needs a real certificate": "Panel này cần một chứng chỉ thật",
    "New token": "Token mới",
    "New token (copy now)": "Token mới (sao chép ngay)",
    "Create token": "Tạo token",
    "Token name is required.": "Cần nhập tên token.",
    "Token revoked.": "Đã thu hồi token.",
    "Your tokens": "Token của bạn",
    "Everyone else's tokens": "Token của những người khác",
    "No tokens yet.": "Chưa có token nào.",
    "No API tokens found.": "Không tìm thấy API token nào.",
    "Allow actions": "Cho phép thao tác",
    "Expires in": "Hết hạn sau",
    "Expires in 7 days": "Hết hạn sau 7 ngày",
    "Expires in 30 days": "Hết hạn sau 30 ngày",
    "Expires in 90 days": "Hết hạn sau 90 ngày",
    "Expires in a year": "Hết hạn sau một năm",
    "Connecting a client": "Kết nối một client",
    "What is it for - laptop, work desktop":
        "Dùng cho việc gì - laptop, máy ở công ty",
    "A token acts as you. It is shown once, when you create it.":
        "Token hành động với quyền của bạn. Nó chỉ hiện đúng một lần, lúc tạo.",
    "Copy this now. It is not shown again.":
        "Sao chép ngay. Nó sẽ không hiện lại lần nữa.",
    "Token created. Copy it now - it is not shown again.":
        "Đã tạo token. Sao chép ngay - nó sẽ không hiện lại.",
    "Every key to this server, and who holds it. You can revoke any of them.":
        "Mọi chiếc chìa khoá vào server này, và ai đang giữ. Bạn thu hồi được bất kỳ cái nào.",
    "Nothing answers on this address until an administrator installs":
        "Không có gì trả lời ở địa chỉ này cho tới khi quản trị viên cài",
    "Shown once. It is not stored anywhere the panel can read back.":
        "Chỉ hiện một lần. Nó không được lưu ở bất cứ đâu mà panel đọc lại được.",
    "This password is shown once. It is not stored anywhere the panel can read it back.":
        "Mật khẩu này chỉ hiện một lần. Nó không được lưu ở bất cứ đâu mà panel đọc lại được.",
    "Create one token for WHMCS. Paste it into WHMCS Server → Access Hash.":
        "Tạo một token cho WHMCS. Dán vào WHMCS Server → Access Hash.",
    "API token copied. Paste it into WHMCS Server Access Hash.":
        "Đã sao chép API token. Dán vào WHMCS Server Access Hash.",
    "API token created. Copy it now; it will not be shown again. Paste it into WHMCS Server Access Hash.":
        "Đã tạo API token. Sao chép ngay, nó sẽ không hiện lại. Dán vào WHMCS Server Access Hash.",
    "Leave WHMCS server IP empty to allow all IPs. Multiple IPs: separate with comma.":
        "Để trống IP máy chủ WHMCS nếu muốn cho phép mọi IP. Nhiều IP thì ngăn cách bằng dấu phẩy.",
    "WHMCS server IP": "IP máy chủ WHMCS",
    "Copy failed.": "Sao chép thất bại.",
    "Copy failed. Select the text and press Ctrl+C.":
        "Sao chép thất bại. Bôi đen đoạn chữ rồi bấm Ctrl+C.",
    "Copy failed. The token is selected; press Ctrl+C.":
        "Sao chép thất bại. Token đã được bôi đen, bấm Ctrl+C.",

    # --- SFTP, applications, panel settings, updates ---
    "SFTP account name": "Tên tài khoản SFTP",
    "SFTP account ready": "Tài khoản SFTP đã sẵn sàng",
    "SFTP password": "Mật khẩu SFTP",
    "Your own SFTP login": "Tài khoản SFTP của riêng bạn",
    "No SFTP accounts for this website yet.":
        "Website này chưa có tài khoản SFTP nào.",
    "Separate from your panel password. Changing one does not change the other.":
        "Tách biệt với mật khẩu panel. Đổi cái này không đổi cái kia.",
    "Your hosting package does not include SFTP accounts.":
        "Gói hosting của bạn không bao gồm tài khoản SFTP.",
    "Install application": "Cài ứng dụng",
    "Select an application": "Chọn một ứng dụng",
    "Pick which application this website should serve.":
        "Chọn ứng dụng mà website này sẽ phục vụ.",
    "Install an application first, on the Applications page.":
        "Hãy cài một ứng dụng trước, ở trang Ứng dụng.",
    "No applications yet. Install one above.":
        "Chưa có ứng dụng nào. Cài một cái ở trên.",
    "No applications installed yet. Install one on the":
        "Chưa cài ứng dụng nào. Cài một cái ở",
    "Install Docker": "Cài Docker",
    "Add Node version": "Thêm phiên bản Node",
    "Node version": "Phiên bản Node",
    "Prune unused layers": "Dọn layer không dùng",
    "Environment (KEY=value, one per line)":
        "Biến môi trường (KEY=value, mỗi dòng một biến)",
    "Pick free port": "Chọn cổng trống",
    "Docker disk (whole server, not counted against customer quotas):":
        "Dung lượng Docker (toàn server, không tính vào hạn mức của khách):",
    "Panel name": "Tên panel",
    "Panel hostname": "Hostname của panel",
    "Panel SSL": "SSL của panel",
    "Panel release": "Bản phát hành panel",
    "Brand assets": "Nhận diện thương hiệu",
    "Upload logo": "Tải logo lên",
    "Upload favicon": "Tải favicon lên",
    "Favicon": "Favicon",
    "Logo": "Logo",
    "Use HTTPS": "Dùng HTTPS",
    "Upload PNG, JPG, WEBP, or ICO files up to 1 MB.":
        "Tải lên file PNG, JPG, WEBP hoặc ICO, tối đa 1 MB.",
    "Server Management Panel": "Panel quản trị máy chủ",
    "Server Panel": "Panel máy chủ",
    "Auto Update OS": "Tự cập nhật hệ điều hành",
    "Save OS auto update": "Lưu cấu hình tự cập nhật",
    "Auto reboot": "Tự khởi động lại",
    "Check releases": "Kiểm tra bản phát hành",
    "Update logs": "Nhật ký cập nhật",
    "OS packages use apt; panel updates use":
        "Gói hệ điều hành dùng apt; cập nhật panel dùng",
    "Panel update completed. Reloading to apply the new version...":
        "Cập nhật panel xong. Đang tải lại để áp dụng phiên bản mới...",
    "Enable IPv6": "Bật IPv6",
    "Disable IPv6": "Tắt IPv6",
    "Could not read the server's IPv4 address.":
        "Không đọc được địa chỉ IPv4 của server.",
    "Memory warning": "Cảnh báo bộ nhớ",
    "Auto-refreshes every 10s": "Tự tải lại mỗi 10 giây",
    "Manual refresh": "Tải lại thủ công",
    "Status not loaded. Press Refresh.": "Chưa tải trạng thái. Bấm Tải lại.",
    "Click Check to read the current state.":
        "Bấm Kiểm tra để đọc trạng thái hiện tại.",
    "State file": "File trạng thái",
    "The panel reads this file and generates the one it actually runs.":
        "Panel đọc file này rồi sinh ra file mà nó thật sự chạy.",

    # --- databases, DirectAdmin import ---
    "Create database": "Tạo database",
    "Search databases": "Tìm database",
    "Clear database search": "Xoá tìm kiếm database",
    "Search by database or user name": "Tìm theo tên database hoặc tên user",
    "Database created successfully": "Đã tạo database thành công",
    "Database download failed.": "Tải database về thất bại.",
    "Database SQL downloaded.": "Đã tải bản SQL của database.",
    "Cannot open phpMyAdmin.": "Không mở được phpMyAdmin.",
    "Click phpMyAdmin to sign in directly. Token expires after 60s.":
        "Bấm phpMyAdmin để đăng nhập thẳng. Token hết hạn sau 60 giây.",
    "Please enter a database name.": "Hãy nhập tên database.",
    "Please enter a domain name.": "Hãy nhập tên miền.",
    "Enter a domain.": "Nhập một tên miền.",
    "Please fill all WordPress admin fields.":
        "Hãy điền đủ các ô tài khoản admin WordPress.",
    "Please select a website or application first.":
        "Hãy chọn một website hoặc ứng dụng trước.",
    "Database name can only contain letters, numbers and underscores (no spaces or special characters).":
        "Tên database chỉ được chứa chữ, số và dấu gạch dưới (không dấu cách, không ký tự đặc biệt).",
    "Database user can only contain letters, numbers and underscores (no spaces or special characters).":
        "Tên user database chỉ được chứa chữ, số và dấu gạch dưới (không dấu cách, không ký tự đặc biệt).",
    "Storage limit must be between 0 and 1048576 MB.":
        "Giới hạn dung lượng phải nằm trong khoảng 0 đến 1048576 MB.",
    "Website limit must be between 0 and 1000.":
        "Giới hạn website phải nằm trong khoảng 0 đến 1000.",
    "WordPress admin password must be at least 10 characters.":
        "Mật khẩu admin WordPress phải có ít nhất 10 ký tự.",
    "Package name is required.": "Cần nhập tên gói.",
    "You do not have permission to edit PHP config.":
        "Bạn không có quyền sửa cấu hình PHP.",
    "Upload DA backup": "Tải backup DA lên",
    "Import summary": "Tóm tắt lần nhập",
    "Replace existing users/websites": "Ghi đè người dùng/website đã có",
    "Import websites, databases, and users from a DirectAdmin backup archive.":
        "Nhập website, database và người dùng từ một file backup của DirectAdmin.",
    "Imports will delete any existing panel user, website, files and databases that share a name with the backup. Leave this off to have conflicting imports stop instead.":
        "Khi nhập, mọi người dùng, website, file và database trùng tên với bản backup sẽ bị xoá. Để tắt thì lần nhập bị trùng sẽ dừng lại thay vì ghi đè.",
    "No DirectAdmin backups uploaded. Upload a DA backup archive to get started.":
        "Chưa tải bản backup DirectAdmin nào lên. Tải một file backup DA lên để bắt đầu.",
    "DA import completed successfully!": "Nhập từ DA hoàn tất!",
    "DA import started. Polling for result...":
        "Đã bắt đầu nhập từ DA. Đang chờ kết quả...",
    "DA backup upload failed.": "Tải backup DA lên thất bại.",

    # --- backup and upload messages ---
    "Backup queued. It will keep running on the server.":
        "Đã xếp hàng backup. Nó sẽ tiếp tục chạy trên server.",
    "Full user backup queued. It will keep running on the server.":
        "Đã xếp hàng backup toàn bộ tài khoản. Nó sẽ tiếp tục chạy trên server.",
    "SFTP backup queued. It will keep running on the server.":
        "Đã xếp hàng backup qua SFTP. Nó sẽ tiếp tục chạy trên server.",
    "Backup downloaded.": "Đã tải backup về.",
    "Backup download failed.": "Tải backup về thất bại.",
    "Backup schedule saved.": "Đã lưu lịch backup.",
    "Full user backup downloaded.": "Đã tải backup toàn bộ tài khoản về.",
    "Full user backup download failed.": "Tải backup toàn bộ tài khoản thất bại.",
    "Full user backup upload failed.": "Tải backup toàn bộ tài khoản lên thất bại.",
    "Upload backup failed.": "Tải backup lên thất bại.",
    "Certificate and private key are required.": "Cần có chứng chỉ và khoá riêng.",

    # --- misc labels ---
    "Filter list": "Lọc danh sách",
    "Filter logs": "Lọc nhật ký",
    "Filter the list": "Lọc danh sách",
    "Filter this list...": "Lọc danh sách này...",
    "Refresh 5s": "Tải lại 5 giây",
    "Refresh 10s": "Tải lại 10 giây",
    "Refresh 30s": "Tải lại 30 giây",
    "Checked": "Đã kiểm tra",
    "Choose for me": "Để panel chọn giúp",
    "Start with": "Bắt đầu với",
    "Starter": "Khởi đầu",
    "Pointers": "Con trỏ",
    "Reaches": "Trỏ tới",
    "Up": "Lên",
    "Without": "Không có",
    "Where the file refers to": "Nơi file trỏ tới",
    "Terminal": "Terminal",
    "Write": "Ghi",
    "Delete rule #": "Xoá rule #",
    "Save and reload Nginx": "Lưu và nạp lại Nginx",
}

# Added after cross-checking against the extracted strings: these were missed
# on the first pass, and "Next" was a Vietnamese word left in the source by
# the sweep that made the interface English - its sibling button says
# "Previous".
PART3.update({
    "Next": "Sau",
    "Name, e.g. designer": "Tên, ví dụ designer",
    "Prefix, e.g. spanel/nightly (optional)": "Tiền tố, ví dụ spanel/nightly (tuỳ chọn)",
    "db_user (default = db_name)": "db_user (mặc định = db_name)",
    "password (empty = generate)": "mật khẩu (để trống = tự tạo)",
    "password (min 12 chars)": "mật khẩu (tối thiểu 12 ký tự)",
    "optional: 1.2.3.4 or 1.2.3.4, 5.6.7.8": "tuỳ chọn: 1.2.3.4 hoặc 1.2.3.4, 5.6.7.8",
    "Scan the website directories (fast), the whole server, or incrementally (only recently changed files — run by hand when you want it, never on the schedule).":
        "Quét thư mục website (nhanh), quét toàn server, hoặc quét thay đổi (chỉ file mới đổi gần đây — chạy tay khi cần, không bao giờ theo lịch).",
})
PART3.update({"Language": "Ngôn ngữ"})
