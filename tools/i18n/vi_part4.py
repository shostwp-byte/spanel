# Vietnamese, part 4: the messages the API sends back.
#
# These arrive as `detail` on a 4xx and are shown to whoever pressed the
# button, so they are read more often than most of the interface - a label is
# glanced at once, an error is read carefully. Translated on display in
# formatApiError, so the backend stays language-free.
#
# Only the ones with no interpolation. A message the server builds from a
# domain name or a number keeps its English; translating those would mean
# parsing the sentence back apart, which is how a wrong translation gets
# confidently shown.

PART4 = {
    # --- not found ---
    "Not found": "Không tìm thấy",
    "Website not found": "Không tìm thấy website",
    "Website owner not found": "Không tìm thấy chủ sở hữu website",
    "Source website not found": "Không tìm thấy website nguồn",
    "Owner not found": "Không tìm thấy chủ sở hữu",
    "User not found": "Không tìm thấy người dùng",
    "Account not found": "Không tìm thấy tài khoản",
    "Account has no user": "Tài khoản không có người dùng nào",
    "Account user not found or inactive":
        "Không tìm thấy người dùng của tài khoản, hoặc tài khoản đang bị khoá",
    "Database not found": "Không tìm thấy database",
    "Package not found": "Không tìm thấy gói",
    "Alias not found": "Không tìm thấy tên miền phụ",
    "Application not found": "Không tìm thấy ứng dụng",
    "Archive not found": "Không tìm thấy file nén",
    "Backup not found": "Không tìm thấy backup",
    "Backup file not found": "Không tìm thấy file backup",
    "Backup job not found": "Không tìm thấy tiến trình backup",
    "Backup schedule not found": "Không tìm thấy lịch backup",
    "Schedule not found": "Không tìm thấy lịch",
    "File job not found": "Không tìm thấy tiến trình xử lý file",
    "Job not found": "Không tìm thấy tiến trình",
    "SFTP account not found": "Không tìm thấy tài khoản SFTP",
    "SFTP target not found": "Không tìm thấy đích lưu SFTP",
    "Passkey not found": "Không tìm thấy passkey",
    "Token not found": "Không tìm thấy token",

    # --- already exists ---
    "Domain already exists": "Tên miền đã tồn tại",
    "Domain alias already exists": "Tên miền phụ đã tồn tại",
    "Database name already exists": "Tên database đã tồn tại",
    "Database user already exists": "User database đã tồn tại",
    "Username already exists": "Tên đăng nhập đã tồn tại",
    "Email already in use": "Email đã được dùng",
    "Package name already exists": "Tên gói đã tồn tại",
    "A backup with this name already exists": "Đã có backup trùng tên này",
    "A target with that name already exists": "Đã có đích lưu trùng tên đó",
    "That SFTP account already exists": "Tài khoản SFTP đó đã tồn tại",
    "That passkey is already registered": "Passkey đó đã được đăng ký",
    "An application with that name or port already exists":
        "Đã có ứng dụng trùng tên hoặc trùng cổng đó",
    "You already have an application with that name":
        "Bạn đã có một ứng dụng trùng tên đó",
    "WordPress is already installed for this website":
        "Website này đã cài WordPress rồi",
    "Two-factor authentication is already enabled":
        "Xác thực hai lớp đã được bật",

    # --- authentication and authorisation ---
    "Invalid username or password": "Tên đăng nhập hoặc mật khẩu không đúng",
    "Could not validate credentials": "Không xác thực được thông tin đăng nhập",
    "Access denied": "Không được phép truy cập",
    "IP not allowed": "IP không được phép",
    "Origin not allowed": "Origin không được phép",
    "Missing Bearer token": "Thiếu Bearer token",
    "Invalid or expired token": "Token không hợp lệ hoặc đã hết hạn",
    "Invalid or revoked token": "Token không hợp lệ hoặc đã bị thu hồi",
    "A valid MCP token is required": "Cần một MCP token hợp lệ",
    "CSRF token missing or invalid": "Thiếu CSRF token hoặc token không hợp lệ",
    "User is suspended": "Tài khoản đang bị khoá",
    "Too many login attempts. Slow down.":
        "Đăng nhập sai quá nhiều lần. Chậm lại một chút.",
    "Too many login attempts. Try again later.":
        "Đăng nhập sai quá nhiều lần. Thử lại sau.",
    "Login rate limiter is unavailable":
        "Bộ giới hạn số lần đăng nhập không hoạt động",
    "Invalid authentication code": "Mã xác thực không đúng",
    "Two-factor authentication code required": "Cần mã xác thực hai lớp",
    "This account has no second factor configured":
        "Tài khoản này chưa thiết lập lớp xác thực thứ hai",
    "Set up two-factor authentication first": "Hãy thiết lập xác thực hai lớp trước",
    "Use the Security page to disable your own 2FA":
        "Dùng trang Bảo mật để tự tắt 2FA của mình",
    "Passkey sign-in expired, please try again":
        "Phiên đăng nhập bằng passkey đã hết hạn, hãy thử lại",
    "Passkey was not accepted": "Passkey không được chấp nhận",
    "Registration expired, please try again": "Đăng ký đã hết hạn, hãy thử lại",

    # --- refusing to let you lock yourself out ---
    "Cannot delete yourself": "Không thể tự xoá chính mình",
    "Cannot suspend yourself": "Không thể tự khoá chính mình",
    "Cannot deactivate yourself": "Không thể tự vô hiệu hoá chính mình",
    "Cannot change your own role": "Không thể tự đổi vai trò của chính mình",

    # --- limits and permissions ---
    "Website limit reached": "Đã đạt giới hạn số website",
    "Package is in use": "Gói đang được sử dụng",
    "Select at least one user": "Chọn ít nhất một người dùng",
    "Select at least one website.": "Chọn ít nhất một website.",
    "Your hosting package does not include WAF settings":
        "Gói hosting của bạn không bao gồm cài đặt WAF",
    "Terminal is not enabled for your account.":
        "Tài khoản của bạn chưa được bật Terminal.",
    "Custom WAF rules can only be changed by an administrator":
        "Chỉ quản trị viên được sửa rule WAF tuỳ chỉnh",

    # --- files and archives ---
    "Symlinks are not allowed": "Không cho phép symlink",
    "Invalid or corrupted ZIP archive": "File ZIP không hợp lệ hoặc bị hỏng",
    "Invalid or corrupted tar archive": "File tar không hợp lệ hoặc bị hỏng",
    "Only .zip, .tar.gz, and .tgz archives are supported":
        "Chỉ hỗ trợ file nén .zip, .tar.gz và .tgz",
    "No backup files uploaded": "Chưa tải file backup nào lên",
    "Pick a website or an application to browse":
        "Chọn một website hoặc ứng dụng để xem",

    # --- in progress ---
    "An import is already running. Please wait.":
        "Đang có một tiến trình nhập chạy. Vui lòng đợi.",
    "A single import is already running. Please wait.":
        "Đang có một tiến trình nhập chạy. Vui lòng đợi.",
    "A bulk import is already running. Please wait.":
        "Đang có một tiến trình nhập hàng loạt chạy. Vui lòng đợi.",

    # --- the rest ---
    "Panel database error": "Lỗi database của panel",
    "Failed to start ClamAV daemon": "Không khởi động được ClamAV daemon",
    "A website cannot borrow its own certificate":
        "Một website không thể dùng chứng chỉ của chính nó",
    "The main Nginx vhost is managed by SPanel. Use Custom Nginx instead.":
        "Vhost Nginx chính do SPanel quản lý. Hãy dùng Nginx tuỳ chỉnh.",
    "Pick an installed application for this website, or choose a different website mode.":
        "Chọn một ứng dụng đã cài cho website này, hoặc đổi sang chế độ website khác.",
    "No Cloudflare API token saved for this domain's zone. Provide one.":
        "Chưa lưu Cloudflare API token cho zone của tên miền này. Hãy cung cấp một token.",
    "Failed to access stored database password; please re-save the password in panel settings":
        "Không đọc được mật khẩu database đã lưu; hãy lưu lại mật khẩu trong cài đặt panel",
    "admin_email and admin_password are required when install_wordpress is true":
        "Cần admin_email và admin_password khi bật install_wordpress",
    "archive_path is required": "Cần có archive_path",
    "archive_paths is required": "Cần có archive_paths",

    # --- the frontend's own fallbacks ---
    "Request failed.": "Yêu cầu thất bại.",
    "Invalid value": "Giá trị không hợp lệ",
}
