# Vietnamese, part 7: the count sentences.
#
# Every entry here has a {placeholder}, and that is why they arrived late. In
# the source these were two JSX fragments either side of an interpolation -
# '' and ' website(s)' - and neither is a translatable unit: Vietnamese does
# not pluralise with (s) and often puts the number elsewhere in the clause.
# Translating the fragments separately produces text that reads like a machine;
# t() now takes values, so each of these is one sentence again.
#
# "website" and "database" stay as they are: a Vietnamese host says "3 website"
# and "2 database", never "3 trang mạng". The count word (cái, con, chiếc) is
# left out for the same reason - nobody writing a control panel puts one in.

PART7 = {
    # --- counts, plain ---
    "{n} website(s)": "{n} website",
    "{n} result(s)": "{n} kết quả",
    "{n} item(s)": "{n} mục",
    "{n} line(s)": "{n} dòng",
    "{n} application(s)": "{n} ứng dụng",
    "{n} database(s)": "{n} database",
    "{n} domain(s)": "{n} tên miền",
    "{d} domain(s), {b} db(s)": "{d} tên miền, {b} database",
    "{n} failures seen": "đã thấy {n} lần thất bại",
    "{n} service(s) will run.": "{n} service sẽ chạy.",
    "serves the domain.": "phục vụ tên miền này.",
    "{n} thing(s) to fix:": "{n} thứ cần sửa:",
    "{n} thing(s) to fix before importing:": "{n} thứ cần sửa trước khi nhập:",
    "{n} MB RAM free": "còn trống {n} MB RAM",
    "({n} MB free)": "(còn trống {n} MB)",

    # --- users and files ---
    "Deleted user {name}": "Đã xoá tài khoản {name}",
    "Deleted user {name} and {n} website(s)":
        "Đã xoá tài khoản {name} và {n} website",
    "Suspend user {name}? This will block login, disable all {n} website(s), lock SFTP, and kill active sessions.":
        "Tạm ngưng tài khoản {name}? Việc này sẽ chặn đăng nhập, tắt toàn bộ {n} website, khoá SFTP và ngắt các phiên đang hoạt động.",
    "Permissions set to {mode} on {n} item(s).":
        "Đã đặt quyền {mode} cho {n} mục.",
    "Delete {n} selected item(s)?": "Xoá {n} mục đã chọn?",
    "World-writable: anyone with an account on the server can change these files. Use 755 unless something really needs it.":
        "Ai cũng ghi được: bất kỳ ai có tài khoản trên server đều sửa được các file này. Hãy dùng 755 trừ khi thật sự cần.",
    "World-writable: anyone with an account on the server can change what is inside these folders. Use 755 unless something really needs it.":
        "Ai cũng ghi được: bất kỳ ai có tài khoản trên server đều sửa được nội dung trong các thư mục này. Hãy dùng 755 trừ khi thật sự cần.",

    # --- packages and allowances ---
    "Using {used} of {limit} allowed.": "Đang dùng {used} trong {limit} được phép.",
    "This package allows {n} application(s). Delete one to install another.":
        "Gói này cho phép {n} ứng dụng. Hãy xoá một cái để cài cái khác.",
    "You have used all {n} SFTP accounts in your package.":
        "Bạn đã dùng hết {n} tài khoản SFTP trong gói của mình.",
    "{used} of {limit} SFTP accounts used.":
        "Đã dùng {used} trong {limit} tài khoản SFTP.",

    # --- applications ---
    "Containers publish on 127.0.0.1 only, run as your own user with no capabilities, and are capped at the memory shown.":
        "Container chỉ mở cổng trên 127.0.0.1, chạy dưới chính user của bạn mà không có đặc quyền nào, và bị giới hạn ở mức bộ nhớ hiển thị.",
    "Images come from {list}.": "Image được lấy từ {list}.",
    "the allowed registries": "các registry được phép",

    # --- nginx and cron ---
    "Alias serves the same app. Redirect sends visitors to {domain}.":
        "Alias phục vụ cùng một ứng dụng. Redirect chuyển khách sang {domain}.",
    "and SPanel rewrites it to": "và SPanel sẽ đổi thành",

    # --- DirectAdmin import ---
    "{user} - {n} website(s)": "{user} - {n} website",
    "unknown user": "tài khoản không rõ",
    "Select all ({n})": "Chọn tất cả ({n})",
    "Restore selected ({n})": "Khôi phục mục đã chọn ({n})",
    "Delete selected ({n})": "Xoá mục đã chọn ({n})",
    "Unassigned databases ({n})": "Database chưa gán ({n})",

    # --- PHP tuning ---
    "Auto tune will change {n} setting(s) for PHP {version}":
        "Tự động tinh chỉnh sẽ đổi {n} thiết lập của PHP {version}",
    "Disable OPcache (PHP {version})": "Tắt OPcache (PHP {version})",
    "Enable OPcache (PHP {version})": "Bật OPcache (PHP {version})",

    # --- WAF and CRS ---
    "Save and apply to all {n} website(s)": "Lưu và áp dụng cho cả {n} website",
    'Panel setting says "{panel}" but the server reports "{server}".':
        'Cài đặt trong panel là "{panel}" nhưng server báo "{server}".',

    # --- malware and passkeys ---
    "Level 2 running": "Lớp 2 đang chạy",
    "Level 2 not running": "Lớp 2 không chạy",
    "You are reaching the panel by IP address ({host}). Browsers only create passkeys for domain names, so open the panel by its domain and add one there.":
        "Bạn đang vào panel bằng địa chỉ IP ({host}). Trình duyệt chỉ tạo passkey cho tên miền, nên hãy mở panel bằng tên miền rồi thêm passkey ở đó.",
}

# Wrapped later than the rest: its paragraph ended on a different line from the
# tag that closed it, so the wrapper never saw it.
PART7.update({
    "Once you dismiss the token above these go back to saying YOUR_TOKEN, because the panel cannot show it to you a second time.":
        "Khi bạn đóng token ở trên thì các đoạn này sẽ trở lại thành YOUR_TOKEN, vì panel không thể hiện lại token cho bạn lần thứ hai.",
})
