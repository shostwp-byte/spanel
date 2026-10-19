# Vietnamese, part 12: the dashboard that says how things stand (2026-09-25) -
# status cards, "Needs attention" and quick actions.

PART12 = {
    # Cards
    "All running": "Tất cả đang chạy",
    "All secured": "Tất cả đã có SSL",
    "{n} without SSL": "{n} chưa có SSL",
    "{n} suspended": "{n} bị tạm ngưng",
    "No schedule": "Chưa có lịch",
    "Last run failed": "Lần chạy gần nhất bị lỗi",
    "{n} schedule(s)": "{n} lịch",
    "ModSecurity engine": "Engine ModSecurity",
    "Clean": "Sạch",
    "No scan yet": "Chưa quét",
    "{n} threat(s)": "{n} mối đe doạ",
    "Last scan": "Lần quét gần nhất",
    "Last scan {when}": "Quét lần cuối {when}",
    "Stopped: {names}": "Đang dừng: {names}",
    "On for every website": "Bật cho mọi website",
    "{n} website(s) without WAF": "{n} website chưa bật WAF",
    "Two-factor sign-in": "Đăng nhập hai lớp",

    # Needs attention
    "Needs attention": "Cần xử lý",
    "Everything looks fine.": "Mọi thứ đều ổn.",
    "The firewall is off.": "Tường lửa đang tắt.",
    "The last malware scan found {n} threat(s).": "Lần quét mã độc gần nhất phát hiện {n} mối đe doạ.",
    "{n} scheduled backup(s) failed on their last run.": "{n} lịch backup bị lỗi ở lần chạy gần nhất.",
    "{n} website(s) without SSL: {domains}": "{n} website chưa có SSL: {domains}",
    "{n} website(s) suspended.": "{n} website đang bị tạm ngưng.",
    "No scheduled backup is set up.": "Chưa có lịch backup nào.",
    "The WAF engine is not installed.": "Chưa cài engine WAF.",
    "The malware scanner is not installed.": "Chưa cài trình quét mã độc.",
    "Two-factor sign-in is off for your account.": "Tài khoản của bạn chưa bật đăng nhập hai lớp.",
    "Panel update {version} is available.": "Đã có bản cập nhật panel {version}.",
    "Set up": "Thiết lập",
    "Install SSL": "Cài SSL",

    # Quick actions
    "Quick actions": "Thao tác nhanh",
    "Back up a website": "Sao lưu website",
    "New SFTP account": "Tạo tài khoản SFTP",

    # A malware scan's stage, while LMD runs
    "Starting the scan...": "Đang bắt đầu quét...",
    "Building the file list...": "Đang lập danh sách file...",
    "Scanning files...": "Đang quét file...",
    "Reading the results...": "Đang đọc kết quả...",
    "Scan finished.": "Đã quét xong.",
    "Scan failed": "Quét thất bại",

    # File manager
    "Modified": "Ngày sửa",

    # PHP extensions
    "PHP extensions": "PHP extension",
    "Installed from the system packages; PHP-FPM reloads so websites can use it straight away. Removing is not offered here, since a website may depend on it.":
        "Cài từ gói của hệ điều hành; PHP-FPM tự nạp lại để website dùng được ngay. Không có nút gỡ, vì có thể có website đang cần extension đó.",
    "Built into PHP": "Có sẵn trong PHP",
    "Not in the package repository for this version": "Kho gói không có bản cho phiên bản PHP này",
    "All versions": "Cài cho mọi bản",
    "Installing php{version}-{extension}...": "Đang cài php{version}-{extension}...",
    "{extension} installed for PHP {versions}.": "Đã cài {extension} cho PHP {versions}.",

    # chmod presets (file manager)
    "Default": "Mặc định",
    "Executable": "Chạy được",
    "Private": "Riêng tư",
    "Read-only": "Chỉ đọc",
    "Group read": "Nhóm đọc",
    "Group write": "Nhóm ghi",
}
