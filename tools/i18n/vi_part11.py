# Vietnamese, part 11: the operator's ten-point list of 2026-09-25 - cron and
# backup presets, the shorter WAF page, scan details, addons.

PART11 = {
    # Schedule presets (cron and scheduled backups)
    "Every minute": "Mỗi phút",
    "Every 5 minutes": "Mỗi 5 phút",
    "Every 15 minutes": "Mỗi 15 phút",
    "Every 30 minutes": "Mỗi 30 phút",
    "Every hour": "Mỗi giờ",
    "Every 6 hours": "Mỗi 6 giờ",
    "Every 12 hours": "Mỗi 12 giờ",
    "Every day at 00:00": "Hằng ngày lúc 00:00",
    "Every day at 02:00": "Hằng ngày lúc 02:00",
    "Every day at 03:00": "Hằng ngày lúc 03:00",
    "Every Sunday at 00:00": "Chủ nhật hằng tuần lúc 00:00",
    "Every Sunday at 02:00": "Chủ nhật hằng tuần lúc 02:00",
    "On the 1st of every month": "Ngày 1 hằng tháng",
    "On the 1st of every month at 02:00": "Ngày 1 hằng tháng lúc 02:00",
    "Custom...": "Tùy chỉnh...",
    "Cron expression": "Biểu thức cron",

    # Cron form
    "Website": "Website",
    "Command template": "Mẫu lệnh",
    "Pick a template...": "Chọn mẫu...",
    "Command": "Lệnh",
    "for this website.": "cho website này.",
    "Only PHP scripts inside public_html and the safe WP-CLI commands are allowed.":
        "Chỉ cho phép script PHP trong public_html và các lệnh WP-CLI an toàn.",

    # Scheduled backups
    "Destination": "Nơi lưu",
    "The name suffix decides how many copies are kept: nothing keeps one per account, day of week keeps seven, week of month five, and full date one a day until retention removes it.":
        "Phần thêm vào tên quyết định số bản được giữ: không thêm giữ 1 bản mỗi tài khoản, thứ trong tuần giữ 7, tuần trong tháng giữ 5, ngày đầy đủ giữ mỗi ngày một bản cho tới khi bị dọn theo hạn lưu.",

    # WAF
    "Protection for each website. Open one to set its rules, flood limit and blocked bots.":
        "Bảo vệ từng website. Mở một website để đặt rule, giới hạn flood và bot bị chặn.",
    "Protection for your websites. Open one to set its rules and blocked bots.":
        "Bảo vệ các website của bạn. Mở một website để đặt rule và bot bị chặn.",
    "Inspects each request for SQL injection, XSS and similar attacks.":
        "Kiểm tra từng request để chặn SQL injection, XSS và các kiểu tấn công tương tự.",
    "Off: requests are not inspected.": "Tắt: request không được kiểm tra.",
    "Detect only: attacks are logged, nothing is blocked.": "Chỉ phát hiện: tấn công được ghi log, không chặn gì.",
    "WAF on": "WAF bật",
    "WAF off": "WAF tắt",
    "Add SecRuleRemoveById <id> to the custom rules below to excuse this site from one rule.":
        "Thêm SecRuleRemoveById <id> vào rule tùy chỉnh bên dưới để bỏ qua một rule cho website này.",

    # Malware scan details
    "Scan details": "Chi tiết lần quét",
    "Pick a scan from the history.": "Chọn một lần quét trong lịch sử.",
    "Queued": "Đang chờ",
    "Finished": "Hoàn tất",
    "Threats found": "Phát hiện mã độc",
    "Interrupted": "Bị gián đoạn",

    # Addons
    "Details": "Chi tiết",

    # Scan titles and WAF badges
    "Whole server": "Toàn server",
    "{count} websites": "{count} website",
    "Scan": "Lần quét",
    "{scanned}/{total} files, {infected} threats, {errors} errors": "{scanned}/{total} file, {infected} mối đe doạ, {errors} lỗi",
    "Blocking": "Đang chặn",
    "{n} rule file(s) installed": "Đã cài {n} file rule",
    "Flood on": "Flood bật",
    "Flood off": "Flood tắt",
    "{n} bot(s)": "{n} bot",
    "No bots": "Không chặn bot",
    "Turn WAF on": "Bật WAF",
    "Turn WAF off": "Tắt WAF",

    # Sign-in and notices, in OPanel's words
    "Login": "Đăng nhập",
    "Logging in...": "Đang đăng nhập...",
    "Completed": "Hoàn tất",
    "Action failed": "Thao tác thất bại",
    "Logged out.": "Đã đăng xuất.",
    "Session expired.": "Phiên đăng nhập đã hết hạn.",

    # CRS as a per-site switch on the site's WAF page
    "Inspects each request for SQL injection, XSS and similar attacks. Each website turns it on from its own page.":
        "Kiểm tra từng request để chặn SQL injection, XSS và các kiểu tấn công tương tự. Mỗi website tự bật trong trang cấu hình của nó.",
    "{n} website(s) with CRS on": "{n} website bật CRS",
    "Block: attacks are refused on websites with CRS on.": "Chặn: tấn công bị từ chối trên các website đã bật CRS.",
    "Switch OWASP CRS to blocking?": "Chuyển OWASP CRS sang chế độ chặn?",
    "Websites with CRS on will start refusing requests that score above the threshold. Run Detect only first and read the logs, or a request somebody depends on may be the one it stops.":
        "Các website đã bật CRS sẽ bắt đầu từ chối request vượt ngưỡng điểm. Hãy chạy Chỉ phát hiện trước và đọc log, nếu không một request hợp lệ nào đó có thể bị chặn.",
    "Blocks known bad paths.": "Chặn các đường dẫn độc hại đã biết.",
    "Inspects each request for SQL injection, XSS and similar attacks. Uses about {n} MB of server RAM.":
        "Kiểm tra từng request để chặn SQL injection, XSS và các kiểu tấn công tương tự. Tốn khoảng {n} MB RAM của server.",
    "It loads when the WAF is on.": "CRS chỉ chạy khi WAF bật.",
    "CRS is off for the whole server, so nothing is loaded yet.": "CRS đang tắt trên toàn server nên chưa có gì được nạp.",
    "CRS on": "CRS bật",
    "CRS off": "CRS tắt",
    "Turn CRS on": "Bật CRS",
    "Turn CRS off": "Tắt CRS",
    "Turn the WAF on first": "Hãy bật WAF trước",
    "Turn on OWASP CRS for {domain}? It uses about {n} MB of server RAM.": "Bật OWASP CRS cho {domain}? CRS tốn khoảng {n} MB RAM của server.",
    "Turning CRS on for {domain}...": "Đang bật CRS cho {domain}...",
    "Turning CRS off for {domain}...": "Đang tắt CRS cho {domain}...",
    "OWASP CRS is on for {domain}.": "Đã bật OWASP CRS cho {domain}.",
    "OWASP CRS is off for {domain}.": "Đã tắt OWASP CRS cho {domain}.",
}
