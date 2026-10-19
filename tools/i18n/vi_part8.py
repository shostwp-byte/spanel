# Vietnamese, part 8: sentences that were bare strings in the source.
#
# Not between tags and not dictionary keys, so neither audit could see them: a
# confirm() message, a notice, a hint chosen by a ternary. They rendered in
# English on a screen that was otherwise Vietnamese, and the only way to find
# them was to read the source for whole sentences in quotes.
#
# Two carry blank lines, because they are confirm() dialogs where the question
# comes first and the consequences follow. gen_vi.py escapes the newline.

PART8 = {
    "Enable IPv6 for every website and the panel?\n\nSPanel adds listen [::] to every website's nginx config, checks it with nginx -t, and rolls back on any error. The panel restarts.":
        "Bật IPv6 cho mọi website và cho panel?\n\nSPanel sẽ thêm listen [::] vào cấu hình nginx của từng website, kiểm tra bằng nginx -t, và tự khôi phục nếu có lỗi. Panel sẽ khởi động lại.",
    "Disable IPv6?\n\nWebsites and the panel will accept IPv4 only. If a domain still has an AAAA record, visitors arriving over IPv6 will not get through.":
        "Tắt IPv6?\n\nWebsite và panel sẽ chỉ nhận IPv4. Nếu tên miền vẫn còn bản ghi AAAA thì khách vào bằng IPv6 sẽ không kết nối được.",

    "Real-time protection is on (level 2).": "Bảo vệ thời gian thực đang bật (lớp 2).",
    "Real-time protection is off.": "Bảo vệ thời gian thực đang tắt.",

    "Import and REPLACE? Any existing panel user, website, files and databases with the same names are deleted first.":
        "Nhập và GHI ĐÈ? Mọi tài khoản panel, website, file và database trùng tên đang có sẽ bị xoá trước.",
    "Import this DirectAdmin backup? This will create users, websites, databases, and nginx configs.":
        "Nhập bản backup DirectAdmin này? Việc này sẽ tạo tài khoản, website, database và cấu hình nginx.",

    "WordPress will be installed and the panel will show the URL, admin account, and password after creation.":
        "WordPress sẽ được cài, và sau khi tạo xong panel sẽ hiện URL, tài khoản quản trị và mật khẩu.",
    "A PHP-FPM vhost will be created with public_html/ folder. Upload your PHP, HTML, or static files via File Manager.":
        "Một vhost PHP-FPM sẽ được tạo cùng thư mục public_html/. Hãy tải file PHP, HTML hoặc file tĩnh lên bằng Quản lý file.",

    "Minimum 12 characters.": "Tối thiểu 12 ký tự.",
    "Requires current password + 2FA.": "Cần mật khẩu hiện tại + xác thực hai lớp.",

    # The create form folds away now, so the button that opens it is new.
    "New website": "Tạo website",
}

# The file list grew column headings, so the numbers under them say what they
# are. "Name" and "Mode" were already keys.
PART8.update({
    "Size": "Dung lượng",
})

# The WAF status dump folds away now, so its summary line is new.
PART8.update({
    "Module, rule files and timers": "Module, file rule và lịch hẹn",
})

# The one tile description written with double quotes, because it contains an
# apostrophe. Every check of the tile array matched single-quoted strings only,
# so this one was invisible to all of them and stayed English on a Vietnamese
# dashboard.
PART8.update({
})

# Labels that arrive from an array and are drawn by a shared line of JSX -
# backup tabs, website modes, the CRS switch, the chmod presets. Five render
# sites drew them raw, so a Vietnamese panel had English tabs. Product names
# and the two words every host says in English stay as they are.
PART8.update({
    "Backup website": "Backup website",
    "Static": "Tĩnh",
    "Container": "Container",
    "Detect only": "Chỉ phát hiện",
    "Block": "Chặn",
})
