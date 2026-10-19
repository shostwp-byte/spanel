# Vietnamese, part 13: the Notifications addon (2026-09-27) - its page, the
# events a person can choose, the addon's description and its API errors.

PART13 = {
    # The addon card
    "Email needs an SMTP account (host, port, sender address). Mail sent straight from the server usually lands in spam.":
        "Email cần một tài khoản SMTP (máy chủ, cổng, địa chỉ gửi). Thư gửi thẳng từ server thường rơi vào spam.",

    # The events
    "A service stopped or came back": "Dịch vụ bị dừng hoặc chạy lại",
    "nginx, PHP-FPM, MariaDB, Redis or the panel itself, checked every 5 minutes.":
        "nginx, PHP-FPM, MariaDB, Redis hoặc chính panel, kiểm tra mỗi 5 phút.",
    "The disk is nearly full": "Ổ đĩa sắp đầy",
    "Once when usage passes the threshold, again only after it has dropped back.":
        "Báo một lần khi vượt ngưỡng, chỉ báo lại sau khi đã giảm xuống.",
    "The firewall is off": "Tường lửa đang tắt",
    "Switched off, or on but not filtering.": "Bị tắt, hoặc đang bật nhưng không lọc.",
    "A panel update is available": "Có bản cập nhật panel",
    "Once per new version.": "Một lần cho mỗi phiên bản mới.",
    "A scheduled backup failed": "Backup theo lịch bị lỗi",
    "Any schedule on the server, with the error.": "Bất kỳ lịch nào trên server, kèm nội dung lỗi.",
    "Malware found": "Phát hiện mã độc",
    "Any scan on the server that finds something.": "Mọi lần quét trên server tìm thấy file nghi nhiễm.",
    "Certificates about to expire": "Chứng chỉ sắp hết hạn",
    "Every website's certificate, checked once a day.": "Chứng chỉ của mọi website, kiểm tra mỗi ngày một lần.",
    "Sign-in from a new address": "Đăng nhập từ địa chỉ mới",
    "Your account signed in from an IP it has not used before.": "Tài khoản của bạn đăng nhập từ một IP chưa từng dùng.",
    "Password or two-factor changed": "Đổi mật khẩu hoặc đăng nhập hai lớp",
    "So a change you did not make does not go unnoticed.": "Để một thay đổi không phải do bạn làm không bị bỏ sót.",

    # The page
    "My notifications": "Thông báo của tôi",
    "Where the panel reaches you, and about what.": "Panel gửi thông báo cho bạn qua đâu, và về việc gì.",
    "Not set up": "Chưa cấu hình",
    "Not connected": "Chưa kết nối",
    "Sent to {email}, the address on your account.": "Gửi tới {email}, địa chỉ email của tài khoản.",
    "Email is not set up on this server yet.": "Server chưa cấu hình gửi email.",
    "Send me email": "Gửi email cho tôi",
    "Telegram is not set up on this server yet.": "Server chưa cấu hình Telegram.",
    "Send me Telegram messages": "Gửi tin Telegram cho tôi",
    "Send a test": "Gửi thử",
    "Disconnect": "Ngắt kết nối",
    "Open the bot and press Start:": "Mở bot và bấm Start:",
    "Or send the bot this message:": "Hoặc gửi cho bot tin nhắn này:",
    "Then press Check. The code works for {n} minutes.": "Sau đó bấm Kiểm tra. Mã có hiệu lực trong {n} phút.",
    "About your account": "Về tài khoản của bạn",
    "About the server": "Về server",
    "Server channels": "Kênh gửi của server",
    "What every notification on this server goes out through.": "Mọi thông báo trên server này được gửi qua đây.",
    "Email (SMTP)": "Email (máy chủ SMTP)",
    "SMTP server": "Máy chủ SMTP",
    "Encryption": "Mã hoá",
    "Saved - leave empty to keep it": "Đã lưu - để trống để giữ nguyên",
    "Sender address": "Địa chỉ gửi",
    "Sender name": "Tên người gửi",
    "Send me a test email": "Gửi email thử cho tôi",
    "Email settings saved.": "Đã lưu cấu hình email.",
    "Telegram bot": "Bot Telegram",
    "Bot token": "Token của bot",
    "Saved - paste a new one to replace it": "Đã lưu - dán token mới để thay",
    "Telegram bot saved.": "Đã lưu bot Telegram.",
    "Remove the bot": "Gỡ bot",
    "Telegram bot removed.": "Đã gỡ bot Telegram.",
    "When to warn": "Ngưỡng cảnh báo",
    "Disk usage (%)": "Ổ đĩa đã dùng (%)",
    "Certificate expiry (days)": "Chứng chỉ còn hạn (ngày)",
    "Language of the messages": "Ngôn ngữ của thông báo",
    "Saved.": "Đã lưu.",
    "Saving...": "Đang lưu...",
    "Sending...": "Đang gửi...",
    "Loading...": "Đang tải...",
    "Delivery log": "Lịch sử gửi",
    "The last 100 messages sent, and the ones that failed.": "100 thông báo gần nhất đã gửi, và những lần gửi lỗi.",
    "Nothing sent yet.": "Chưa gửi thông báo nào.",
    "Sent": "Đã gửi",
    "Telegram connected.": "Đã kết nối Telegram.",
    "Telegram disconnected.": "Đã ngắt kết nối Telegram.",
    "Test sent to {target}.": "Đã gửi thử tới {target}.",
    "The bot has not received your /start message yet. Send it, then press Check again.":
        "Bot chưa nhận được tin nhắn /start của bạn. Hãy gửi rồi bấm Kiểm tra lại.",

    # API errors a person can meet on this page
    "No Telegram bot is set up on this server yet": "Server chưa cấu hình bot Telegram",
    "Set the SMTP server and sender address first": "Hãy nhập máy chủ SMTP và địa chỉ gửi trước",
    "Your account has no email address": "Tài khoản của bạn chưa có địa chỉ email",
    "Connect your Telegram first": "Hãy kết nối Telegram trước",
    "Set the Telegram bot token first": "Hãy nhập token bot Telegram trước",
    "That does not look like a bot token from @BotFather": "Đây không giống token bot do @BotFather cấp",
    "The sender address is not an email address": "Địa chỉ gửi không phải là địa chỉ email",
    "Open Notifications to set up the SMTP server or a Telegram bot.": "Mở trang Thông báo để cấu hình máy chủ SMTP hoặc bot Telegram.",

    # Telegram: a token and a chat ID
    "Create a bot with @BotFather (/newbot) and paste its token. Then send the bot any message - or add it to a group - press Find chat ID and pick the chat.":
        "Tạo bot bằng @BotFather (/newbot) rồi dán token. Sau đó nhắn cho bot một tin bất kỳ - hoặc thêm bot vào group - bấm Tìm Chat ID và chọn chat.",
    "Find chat ID": "Tìm Chat ID",
    "No messages yet. Send the bot a message first, then press Find chat ID again.":
        "Bot chưa nhận tin nhắn nào. Hãy nhắn cho bot trước, rồi bấm Tìm Chat ID lại.",
    "Server events go to this chat once. An administrator with no chat of their own hears about their account here too.":
        "Các sự kiện của server được gửi vào chat này một lần. Quản trị viên chưa có chat riêng cũng nhận tin về tài khoản của mình tại đây.",
    "You hear in the admin chat ({chat}). Connect a chat of your own to hear there instead.":
        "Bạn đang nhận tin ở chat quản trị ({chat}). Kết nối chat riêng nếu muốn nhận ở đó.",
    "Your chat ID": "Chat ID của bạn",
    "Find my chat ID for me": "Tự tìm Chat ID giúp tôi",
    "A chat ID is a number (negative for a group) or a @channel name":
        "Chat ID là một số (số âm nếu là group) hoặc tên kênh dạng @kenh",
    "Set the bot token and the chat ID first": "Hãy nhập token bot và Chat ID trước",

    # The Settings page: everything the sidebar does not hold
    "Ports, IP rules, blocklists and Fail2ban": "Cổng, luật IP, blocklist và Fail2ban",
    "Request filtering for each website": "Lọc request cho từng website",
    "Requests and blocks": "Request và các lần chặn",
    "Password, two-factor sign-in and passkeys": "Mật khẩu, đăng nhập hai lớp và passkey",
    "Start, stop and restart": "Bật, tắt và khởi động lại",
    "Versions, limits and extensions": "Phiên bản, giới hạn và extension",
    "Hostname, branding and API tokens": "Tên miền panel, thương hiệu và API token",
    "Panel version": "Phiên bản panel",

    # WAF: the server's custom rules, now that they apply
    "Global custom rules": "Rule tuỳ chỉnh toàn cục",
    "{n} rule(s)": "{n} rule",
    "ModSecurity rules for every website with the WAF on. Rules an AI assistant added are marked # spanel-mcp; delete a rule by deleting its lines.":
        "Rule ModSecurity áp dụng cho mọi website đang bật WAF. Rule do trợ lý AI thêm có dòng # spanel-mcp; muốn xoá rule nào thì xoá các dòng của rule đó.",
    "Global WAF rules saved. They apply to every website with the WAF on.":
        "Đã lưu rule WAF toàn cục. Rule áp dụng cho mọi website đang bật WAF.",

    # Panel settings in four tabs
    "Panel settings sections": "Các mục cài đặt panel",
    "General": "Chung",
    "Panel name, hostname and the server's addresses.": "Tên panel, tên miền panel và địa chỉ IP của server.",
    "A new password needs the current one, and the authenticator code when two-factor sign-in is on.":
        "Đổi mật khẩu cần nhập mật khẩu hiện tại, và mã xác thực nếu đang bật đăng nhập hai lớp.",

    # Notifications: administrators only
    "Tells administrators what needs their attention on the server, by email and Telegram.":
        "Báo cho quản trị viên những việc cần lưu ý trên server, qua email và Telegram.",
    "The server: a service that stopped, a disk filling up, the firewall off, a failed backup, malware, certificates about to expire, a panel update.":
        "Về server: dịch vụ bị dừng, ổ đĩa sắp đầy, tường lửa tắt, backup lỗi, mã độc, chứng chỉ sắp hết hạn, có bản cập nhật panel.",
    "Your own account: a sign-in from a new place, a password or two-factor change.":
        "Về tài khoản của bạn: đăng nhập từ nơi mới, đổi mật khẩu hoặc đăng nhập hai lớp.",
    "Email goes through the SMTP server you set; Telegram through a bot you create with @BotFather. Each administrator picks their channels and mutes what they do not want.":
        "Email gửi qua máy chủ SMTP bạn cấu hình; Telegram qua bot bạn tạo bằng @BotFather. Mỗi quản trị viên tự chọn kênh và tắt những thông báo không muốn nhận.",
    "For administrators only: customers do not see it and are not sent anything.":
        "Chỉ dành cho quản trị viên: khách hàng không thấy tính năng này và không nhận thông báo nào.",
    "Turning the addon off stops every message and keeps the settings, each administrator's choices and the delivery log.":
        "Tắt addon sẽ dừng mọi thông báo nhưng giữ nguyên cấu hình, lựa chọn của từng quản trị viên và lịch sử gửi.",

    # Restore, DirectAdmin's four steps
    "Where the backups are, what that needs, which users - then restore. The same steps as DirectAdmin.":
        "Chọn nơi chứa backup, điền thông tin nếu cần, chọn người dùng rồi khôi phục - các bước giống DirectAdmin.",
    "This server": "Server này",
    "The panel's backup folder": "Thư mục backup của panel",
    "Another server": "Máy chủ khác",
    "SFTP, FTP or FTPS": "SFTP, FTP hoặc FTPS",
    "Used for this restore only and never saved. To keep a server, add it under Backup Destination.":
        "Chỉ dùng cho lần khôi phục này và không được lưu lại. Muốn giữ máy chủ này, hãy thêm nó ở Đích lưu backup.",
    "Server key": "Khoá của máy chủ",
    "No backups found in this source.": "Không tìm thấy backup nào ở nguồn này.",
    "Select all": "Chọn tất cả",
    "Filter by user...": "Lọc theo người dùng...",
    "{n} selected": "Đã chọn {n}",
    "The user is read from the backup when it is restored": "Tên người dùng sẽ được đọc từ backup khi khôi phục",
    "Exists on this server - will be overwritten": "Đã có trên server này - sẽ bị ghi đè",
    "New on this server": "Chưa có trên server này",
    "Overwrite": "Ghi đè",
    "New": "Mới",
    "Backup to restore": "Bản backup cần khôi phục",
    "Restore {n} user(s)": "Khôi phục {n} người dùng",
    "One user after another, in the background. A user that already exists is overwritten with its backup.":
        "Khôi phục lần lượt từng người dùng và chạy nền. Người dùng đã có sẽ bị ghi đè bằng bản backup.",
    "Restore {n} user(s)? An account that already exists on this server is overwritten with what is in its backup.":
        "Khôi phục {n} người dùng? Tài khoản đã có trên server này sẽ bị ghi đè bằng nội dung trong bản backup.",
    "Starting the restore...": "Đang bắt đầu khôi phục...",
    "Restore started. It keeps running on the server; this page shows each user as it goes.":
        "Đã bắt đầu khôi phục. Tiến trình chạy tiếp trên server; trang này hiện kết quả của từng người dùng.",
    "Restoring: {done} of {total} done": "Đang khôi phục: xong {done}/{total}",
    "Finished: {done} of {total} restored": "Hoàn tất: đã khôi phục {done}/{total}",
    "Waiting": "Đang chờ",
    "Downloading": "Đang tải về",
    "Restoring": "Đang khôi phục",
    "Restored": "Đã khôi phục",
    # what the server can answer
    "Pick a backup destination": "Hãy chọn một đích lưu backup",
    "Enter the server's host name or IP address": "Hãy nhập tên miền hoặc địa chỉ IP của máy chủ",
    "Enter the username": "Hãy nhập tên đăng nhập",
    "Enter the password": "Hãy nhập mật khẩu",
    "The username or folder contains a control character": "Tên đăng nhập hoặc thư mục chứa ký tự điều khiển",
    "The folder path contains a control character": "Đường dẫn thư mục chứa ký tự điều khiển",
    "The server refused the username or password": "Máy chủ từ chối tên đăng nhập hoặc mật khẩu",
    "The server did not open an SFTP session": "Máy chủ không mở phiên SFTP",
    "Not a backup in the panel's backup folder": "Không phải backup trong thư mục backup của panel",
    "That backup destination no longer exists": "Đích lưu backup này không còn nữa",
    "List the server first, so its key can be checked": "Hãy liệt kê máy chủ trước để kiểm tra khoá của nó",
    "Not a backup file": "Không phải file backup",
    "That file is not in the folder that was listed": "File này không nằm trong thư mục đã liệt kê",

    # A backup sent off-server is kept there only
    "With a destination, the backups are kept there only: each one is removed from this server once it has uploaded, and stays here only if the upload fails.":
        "Khi có đích lưu, backup chỉ được giữ ở đó: mỗi bản sẽ bị xoá khỏi server này ngay khi tải lên xong, và chỉ ở lại đây nếu tải lên thất bại.",

    # Restore, laid out by the operator: source, then per source upload + refresh
    "Source": "Nguồn",
    "S3 or SFTP": "S3 hoặc SFTP",
    "Backups on this server": "Backup trên server này",
    "Connection": "Kết nối",
    "Scheduled and manual backups kept on this server, and the ones uploaded here.":
        "Backup theo lịch và backup thủ công đang lưu trên server này, cùng các bản đã tải lên đây.",
    "No backup destination yet. Add one in the Backup Destination tab.":
        "Chưa có đích lưu backup nào. Hãy thêm ở tab Đích lưu backup.",
    "Accounts to restore": "Tài khoản cần khôi phục",
    "Press Refresh in step 2 to list the backups.": "Bấm Tải lại ở bước 2 để liệt kê các bản backup.",
    "Restore {n} account(s)": "Khôi phục {n} tài khoản",
    "Uploading {n} backup(s)...": "Đang tải lên {n} bản backup...",
    "Uploaded {n} backup(s).": "Đã tải lên {n} bản backup.",
    "The source is not valid": "Nguồn không hợp lệ",
    "Nothing to send: the archive is already on this server": "Không cần gửi: bản backup đã nằm trên server này",
    "This is not a full user backup": "Đây không phải bản backup đầy đủ của người dùng",

    # The Malware Scanner addon
    "Scans the websites and the server for malware with Linux Malware Detect and ClamAV.":
        "Quét mã độc cho các website và toàn server bằng Linux Malware Detect và ClamAV.",
    "Scans a website, every website or the whole server on demand, and on a weekly schedule.":
        "Quét một website, mọi website hoặc toàn server khi cần, và theo lịch hằng tuần.",
    "Optional real-time monitoring of the websites (Level 2) and a scan of each uploaded file.":
        "Tuỳ chọn giám sát thời gian thực các website (Lớp 2) và quét từng file được tải lên.",
    "Suspicious files are listed with their signature; nothing is deleted or quarantined without you.":
        "File nghi nhiễm được liệt kê kèm chữ ký; không file nào bị xoá hay cách ly khi bạn chưa đồng ý.",
    "Installing it installs LMD and the ClamAV engine if they are not there yet (1-3 minutes).":
        "Cài addon sẽ cài LMD và ClamAV nếu server chưa có (1-3 phút).",
    "A scan uses about 1.3 GB of memory while it runs and releases it afterwards; nothing stays resident unless Level 2 is on.":
        "Mỗi lần quét dùng khoảng 1,3 GB RAM và trả lại sau khi xong; không có gì chạy thường trực trừ khi bật Lớp 2.",
    "Removing it stops the schedule, the scan of uploads and the real-time monitor, which stays off until you turn Level 2 on again. LMD, ClamAV, the history and the schedule are kept.":
        "Gỡ addon sẽ dừng lịch quét, quét file tải lên và giám sát thời gian thực (Lớp 2 sẽ tắt cho đến khi bạn bật lại). LMD, ClamAV, lịch sử và lịch quét được giữ nguyên.",
    "Open Malware scanner under Settings. If LMD and ClamAV were not installed, they are installing now (1-3 minutes).":
        "Mở Quét mã độc trong Cài đặt. Nếu server chưa có LMD và ClamAV thì chúng đang được cài (1-3 phút).",
    "LMD, ClamAV, the scan history and the schedule settings are all kept.":
        "LMD, ClamAV, lịch sử quét và cấu hình lịch quét đều được giữ nguyên.",
    "Addon off": "Chưa bật addon",
    "Install it on the Addons page": "Cài ở trang Tiện ích",
    "LMD and ClamAV are installing (1-3 minutes). Press Refresh to see when they are ready.":
        "LMD và ClamAV đang được cài (1-3 phút). Bấm Tải lại để xem khi nào xong.",
    "To turn the scanner off, remove the Malware Scanner addon on the Addons page. The history and the settings are kept.":
        "Muốn tắt trình quét, hãy gỡ addon Quét mã độc ở trang Tiện ích. Lịch sử và cấu hình được giữ nguyên.",
    # Installing and removing an addon, in each addon's own words
    "Remove the {name} addon? Anything it runs is stopped; its data stays where it is, and installing it again picks up from there.":
        "Gỡ addon {name}? Những gì addon đang chạy sẽ dừng; dữ liệu vẫn giữ nguyên, cài lại là dùng tiếp được.",
    "Installing {name}...": "Đang cài {name}...",
    "Removing {name}...": "Đang gỡ {name}...",
    "{name} installed.": "Đã cài {name}.",
    "{name} removed.": "Đã gỡ {name}.",
    "The package, the jail configuration and the ban history are all kept.":
        "Gói phần mềm, cấu hình jail và lịch sử chặn đều được giữ nguyên.",
    "Application directories, volumes and panel data are all kept.":
        "Thư mục ứng dụng, volume và dữ liệu panel đều được giữ nguyên.",
    "Open the Application page to install Docker or the Node.js versions you need.":
        "Mở trang Ứng dụng để cài Docker hoặc các phiên bản Node.js bạn cần.",
    "SSH is protected now. Banned addresses are listed on the Firewall page.":
        "SSH đã được bảo vệ. Các địa chỉ bị chặn được liệt kê ở trang Tường lửa.",
    "The {name} addon is not installed on this server.": "Addon {name} chưa được cài trên server này.",
    "Demo mode": "Chế độ demo",
    "Public demo accounts that can look at every page and change nothing.":
        "Tài khoản demo công khai: xem được mọi trang nhưng không thay đổi được gì.",
    "One administrator account and one customer account, with sign-in buttons on the login page so a visitor is one click from either.":
        "Một tài khoản quản trị và một tài khoản khách hàng, có nút đăng nhập ngay trên trang đăng nhập để khách vào bằng một cú bấm.",
    "Every change a demo account tries is refused with a message saying this is a demo. So are file contents, downloads, phpMyAdmin, backups and the terminal.":
        "Mọi thay đổi từ tài khoản demo đều bị từ chối kèm thông báo đây là bản demo. Nội dung file, tải xuống, phpMyAdmin, backup và terminal cũng bị chặn.",
    "Your own administrator account is untouched and keeps full control.":
        "Tài khoản quản trị của bạn không bị ảnh hưởng và vẫn toàn quyền.",
    "The demo passwords are shown on the sign-in page, so pick accounts that exist only for the demo. They are never the SFTP or SSH password.":
        "Mật khẩu demo hiện công khai trên trang đăng nhập, nên hãy chọn các tài khoản chỉ dùng cho demo. Đây không bao giờ là mật khẩu SFTP hay SSH.",
    "Signing out of a demo account ends only that visitor's session, not everyone else's.":
        "Đăng xuất khỏi tài khoản demo chỉ kết thúc phiên của người đó, không đăng xuất những người khác.",
    "Removing the addon gives both accounts a random password, so the public ones stop working. Installing it again restores them.":
        "Gỡ tiện ích sẽ đặt mật khẩu ngẫu nhiên cho cả hai tài khoản để mật khẩu công khai hết tác dụng. Cài lại sẽ khôi phục chúng.",
    "Choose the demo accounts on the Addons page. Until you do, the sign-in page offers none.":
        "Chọn tài khoản demo ở trang Tiện ích. Khi chưa chọn, trang đăng nhập chưa hiện tài khoản demo nào.",
    "The demo accounts now have random passwords, so the public ones no longer work. Installing the addon again restores them.":
        "Các tài khoản demo đã được đặt mật khẩu ngẫu nhiên, mật khẩu công khai không còn dùng được. Cài lại tiện ích sẽ khôi phục chúng.",
    "No demo accounts were set.": "Chưa có tài khoản demo nào được chọn.",
    "This is a demo: you can look at everything, but changes are not saved.":
        "Đây là bản demo: bạn xem được mọi thứ, nhưng thay đổi sẽ không được lưu.",
    "This is a demo: file contents, downloads, database access and the terminal are not available.":
        "Đây là bản demo: không mở được nội dung file, tải xuống, truy cập database hay terminal.",
    "That account does not exist.": "Tài khoản này không tồn tại.",
    "You cannot make your own account a demo account.":
        "Bạn không thể đặt chính tài khoản của mình làm tài khoản demo.",
    "The administrator demo account must be an administrator.": "Tài khoản demo quản trị phải là tài khoản quản trị.",
    "The customer demo account must be a customer, not an administrator.":
        "Tài khoản demo khách hàng phải là khách hàng, không phải quản trị.",
    "A demo password must be 6 to 128 characters.": "Mật khẩu demo phải dài từ 6 đến 128 ký tự.",
    "Demo accounts saved.": "Đã lưu tài khoản demo.",
    "Demo accounts": "Tài khoản demo",
    "Visitors sign in with these from the login page. The passwords are shown to everyone, so pick accounts that exist only for the demo.":
        "Khách đăng nhập bằng các tài khoản này từ trang đăng nhập. Mật khẩu hiện công khai, nên hãy chọn tài khoản chỉ dùng cho demo.",
    "Administrator": "Quản trị",
    "Customer": "Khách hàng",
    "Not offered": "Không dùng",
    "Password shown on the login page": "Mật khẩu hiện trên trang đăng nhập",
    "At least 6 characters": "Ít nhất 6 ký tự",
    "Save demo accounts": "Lưu tài khoản demo",
    "Demo": "Bản demo",
    "Look around without changing anything.": "Xem thử mọi trang, không thay đổi được gì.",
    "Sign in": "Đăng nhập",
    "Stops SSH password guessing by banning an address that keeps getting it wrong.":
        "Chặn việc dò mật khẩu SSH bằng cách cấm các địa chỉ nhập sai liên tục.",
    "sshd on a public address collects hundreds of password attempts a day with nobody watching; one live customer server logged 679 failures in 24 hours.":
        "sshd trên địa chỉ công khai nhận hàng trăm lần thử mật khẩu mỗi ngày mà không ai để ý; một server khách hàng đang chạy ghi nhận 679 lần sai trong 24 giờ.",
    "Five failures within an hour bans an address for an hour, and longer each time it comes back, up to a week.":
        "Sai năm lần trong một giờ thì địa chỉ bị cấm một giờ, và lâu hơn mỗi lần quay lại, tối đa một tuần.",
    "Never bans the server itself: loopback and every address the machine holds are on the ignore list.":
        "Không bao giờ cấm chính server: loopback và mọi địa chỉ của máy đều nằm trong danh sách bỏ qua.",
    "The ban action is pinned to iptables-multiport rather than left for fail2ban to detect. A machine that once had ufw keeps its ufw chains, which is enough for fail2ban to pick wrong and hand every ban to a firewall that is not running.":
        "Hành động cấm được cố định là iptables-multiport thay vì để fail2ban tự dò. Máy từng dùng ufw vẫn còn các chain của ufw, đủ để fail2ban chọn sai và giao mọi lệnh cấm cho một tường lửa không chạy.",
    "On install the panel bans an address that is never routed, then checks it really is in iptables. If it is not, the install fails rather than leaving a service that looks healthy and protects nothing.":
        "Khi cài, panel cấm thử một địa chỉ không bao giờ được định tuyến rồi kiểm tra nó có thật trong iptables. Nếu không, việc cài thất bại thay vì để lại một dịch vụ trông vẫn chạy mà không bảo vệ gì.",
    "Turning the addon off only stops the service: the package, the jail file and the ban history all stay.":
        "Tắt addon chỉ dừng dịch vụ: gói phần mềm, file jail và lịch sử chặn đều được giữ nguyên.",
    "Lets Claude Code, Cursor or VS Code read and operate the panel with a personal token.":
        "Cho Claude Code, Cursor hoặc VS Code đọc và thao tác panel bằng token cá nhân.",
    "Each person creates their own token, and it acts with exactly their own permissions: an administrator's reaches the whole server, a customer's reaches only their own websites, databases, backups and files.":
        "Mỗi người tự tạo token của mình, và token chỉ có đúng quyền của người đó: token của quản trị viên với tới cả server, token của khách hàng chỉ với tới website, database, backup và file của chính họ.",
    "A token is read-only unless the person ticks Allow actions when creating it. A read-only token is not even shown the tools that write.":
        "Token chỉ đọc, trừ khi người tạo đánh dấu Cho phép thao tác. Token chỉ đọc thậm chí không thấy các công cụ ghi.",
    "Nothing new listens on the network. The assistant talks to the panel's own address, over the panel's own certificate.":
        "Không mở thêm cổng nào. Trợ lý kết nối vào chính địa chỉ của panel, qua chứng chỉ của panel.",
    "Needs a real certificate. MCP clients refuse a self-signed one, so on a panel still using the self-signed certificate this addon cannot be reached at all.":
        "Cần chứng chỉ thật. Các ứng dụng MCP từ chối chứng chỉ tự ký, nên panel còn dùng chứng chỉ tự ký thì addon này không kết nối được.",
    "Works with clients that send a Bearer token - Claude Code, Cursor, VS Code. The claude.ai and ChatGPT web connectors need OAuth, which this does not have.":
        "Dùng được với các ứng dụng gửi Bearer token: Claude Code, Cursor, VS Code. Connector web của claude.ai và ChatGPT cần OAuth, addon này chưa hỗ trợ.",
    "Turning the addon off closes the endpoint and leaves the tokens alone. Removing it revokes every token on the server.":
        "Tắt addon sẽ đóng endpoint và giữ nguyên các token. Gỡ addon sẽ thu hồi mọi token trên server.",
    "Runs Node.js apps, containers and Docker Compose, and puts them behind a domain with Nginx.":
        "Chạy ứng dụng Node.js, container và Docker Compose, đặt sau tên miền bằng Nginx.",
    "Installs Docker and the Node.js versions you need; none of it is in a default install.":
        "Cài Docker và các phiên bản Node.js bạn cần; bản cài mặc định không có sẵn những thứ này.",
    "Each app gets its own internal port, its own memory and CPU limits, and runs as the customer's user.":
        "Mỗi ứng dụng có cổng nội bộ riêng, giới hạn RAM và CPU riêng, và chạy bằng user của khách hàng.",
    "Set a website to Application mode and Nginx points at the app you installed.":
        "Đặt website sang chế độ Ứng dụng là Nginx trỏ tới ứng dụng bạn đã cài.",
    "Backups do not yet include application data (the apps directory and named volumes).":
        "Backup chưa bao gồm dữ liệu ứng dụng (thư mục apps và các named volume).",
    "Docker image and volume size is not counted against a customer's disk quota.":
        "Dung lượng image và volume của Docker không tính vào hạn mức ổ đĩa của khách hàng.",
    "The install failed and said nothing about why.": "Cài đặt thất bại mà không báo lý do.",
    "DNS": "DNS",
    "DNS Manager": "Quản lý DNS",
    "Runs the DNS for your domains on this server, and lets you edit their records in the panel.":
        "Chạy DNS cho tên miền của bạn ngay trên server này, và cho sửa bản ghi của chúng trong panel.",
    "Installs PowerDNS and opens port 53. Point a domain's nameservers at this server and its records are served from here.":
        "Cài PowerDNS và mở cổng 53. Trỏ nameserver của một tên miền về server này là bản ghi của nó được phục vụ từ đây.",
    "Set the nameservers on the DNS page, then create glue records for them at the registrar, pointing to this server's IP address.":
        "Đặt nameserver ở trang DNS, rồi tạo glue record cho chúng ở nhà đăng ký tên miền, trỏ về IP của server này.",
    "Both nameservers are this one server, so the domains it serves are only as reachable as the server itself.":
        "Cả hai nameserver đều là server này, nên các tên miền nó phục vụ chỉ truy cập được khi server còn chạy.",
    "If the server's IP address changes, remove the addon and install it again so PowerDNS listens on the new address.":
        "Nếu IP của server thay đổi, hãy gỡ rồi cài lại tiện ích để PowerDNS lắng nghe trên địa chỉ mới.",
    "Open DNS to check the nameservers, then point your domains at them.":
        "Mở trang DNS để kiểm tra nameserver, rồi trỏ các tên miền của bạn về đó.",
    "Every zone is kept in PowerDNS's database and is served again when you install the addon again.":
        "Mọi zone được giữ trong cơ sở dữ liệu của PowerDNS và được phục vụ lại khi bạn cài lại tiện ích.",
    "Nameservers": "Nameserver",
    "Zones": "Zone",
    "No owner": "Không có chủ",
    "Delete zone": "Xoá zone",
    "Owner": "Chủ sở hữu",
    "Add zone": "Thêm zone",
    "Priority": "Độ ưu tiên",
    "Value": "Giá trị",
    "Add record": "Thêm bản ghi",
    "DNS settings": "Cài đặt DNS",
    "PowerDNS is running": "PowerDNS đang chạy",
    "PowerDNS is not running": "PowerDNS không chạy",
    "Port 53 open": "Cổng 53 đang mở",
    "Port 53 closed": "Cổng 53 đang đóng",
    "Listening on": "Đang lắng nghe trên",
    "Nameserver 1": "Nameserver 1",
    "Nameserver 2": "Nameserver 2",
    "IP address for new zones": "Địa chỉ IP cho zone mới",
    "Zone created.": "Đã tạo zone.",
    "Zone deleted.": "Đã xoá zone.",
    "Record added.": "Đã thêm bản ghi.",
    "Record saved.": "Đã lưu bản ghi.",
    "Record deleted.": "Đã xoá bản ghi.",
    "DNS settings saved.": "Đã lưu cài đặt DNS.",
    "There is no such zone.": "Không có zone này.",
    "That zone already exists.": "Zone này đã có.",
    "That record already exists.": "Bản ghi này đã có.",
    "That record is no longer there. Refresh the page.": "Bản ghi này không còn nữa. Hãy tải lại trang.",
    "That is not a valid domain name.": "Đây không phải tên miền hợp lệ.",
    "That record name is too long.": "Tên bản ghi quá dài.",
    "The record name may use letters, digits, hyphens and dots, such as www or mail.":
        "Tên bản ghi chỉ dùng chữ, số, gạch ngang và dấu chấm, ví dụ www hoặc mail.",
    "Enter a hostname, such as mail.example.com.": "Nhập một hostname, ví dụ mail.example.com.",
    "Choose a record type: A, AAAA, CNAME, MX, TXT, NS, SRV or CAA.":
        "Chọn loại bản ghi: A, AAAA, CNAME, MX, TXT, NS, SRV hoặc CAA.",
    "TTL must be between 60 seconds and 7 days.": "TTL phải từ 60 giây đến 7 ngày.",
    "An A record points at an IPv4 address, such as 203.0.113.10.":
        "Bản ghi A trỏ tới một địa chỉ IPv4, ví dụ 203.0.113.10.",
    "An AAAA record points at an IPv6 address.": "Bản ghi AAAA trỏ tới một địa chỉ IPv6.",
    "A TXT record needs some text.": "Bản ghi TXT cần có nội dung.",
    "An SRV value is weight, port and target, such as 5 5060 sip.example.com.":
        "Giá trị SRV gồm weight, cổng và đích, ví dụ 5 5060 sip.example.com.",
    "A CAA value is flags, tag and value, such as 0 issue letsencrypt.org.":
        "Giá trị CAA gồm flags, tag và giá trị, ví dụ 0 issue letsencrypt.org.",
    "Priority must be a number from 0 to 65535.": "Độ ưu tiên phải là số từ 0 đến 65535.",
    "The zone's own name cannot be a CNAME; use an A record.":
        "Chính tên của zone không thể là CNAME; hãy dùng bản ghi A.",
    "A name with a CNAME record can have no other records.":
        "Tên đã có bản ghi CNAME thì không được có bản ghi nào khác.",
    "A name can have only one CNAME record.": "Mỗi tên chỉ có được một bản ghi CNAME.",
    "Only an administrator can change the zone's own nameservers.":
        "Chỉ quản trị viên mới đổi được nameserver của zone.",
    "Set the nameservers on the DNS page first.": "Hãy đặt nameserver ở trang DNS trước.",
    "A nameserver must be a full hostname, such as ns1.example.com.":
        "Nameserver phải là hostname đầy đủ, ví dụ ns1.example.com.",
    "Give two nameservers: registrars ask for at least two.":
        "Hãy nhập hai nameserver: nhà đăng ký tên miền yêu cầu ít nhất hai.",
    "The address for new zones must be an IPv4 address.": "Địa chỉ cho zone mới phải là địa chỉ IPv4.",
    "The DNS server is not answering. Check that PowerDNS is running.":
        "DNS server không trả lời. Hãy kiểm tra PowerDNS có đang chạy không.",
    "The DNS server gave an answer the panel could not read.": "DNS server trả về dữ liệu panel không đọc được.",
    "The DNS server's API key is missing. Remove the DNS Manager addon and install it again.":
        "Thiếu API key của DNS server. Hãy gỡ tiện ích Quản lý DNS rồi cài lại.",
    "Every domain on the server gets a full zone by itself: the websites already here when the addon is installed, new websites and their aliases. The domain, www and mail point at this server, with an MX and an SPF record.":
        "Mọi tên miền trên server tự có zone đầy đủ: các website đã có lúc cài tiện ích, website mới và tên miền phụ của chúng. Tên miền, www và mail trỏ về server này, kèm bản ghi MX và SPF.",
    "Administrators edit every zone; a customer edits the DNS of every domain in their account. A, AAAA, CNAME, MX, TXT, NS, SRV and CAA records.":
        "Quản trị viên sửa mọi zone; khách hàng sửa DNS của mọi tên miền trong tài khoản mình. Hỗ trợ bản ghi A, AAAA, CNAME, MX, TXT, NS, SRV và CAA.",
    "Deleting a website keeps its zone. Only administrators delete zones, and a zone whose website is still here comes back at the next sync.":
        "Xoá website vẫn giữ zone của nó. Chỉ quản trị viên được xoá zone, và zone của một website vẫn còn trên server sẽ được tạo lại ở lần đồng bộ tiếp theo.",
    "Removing the addon stops PowerDNS and closes port 53; every zone is kept and is served again when you install it again.":
        "Gỡ tiện ích sẽ dừng PowerDNS và đóng cổng 53; mọi zone được giữ nguyên và được phục vụ lại khi bạn cài lại.",
    "Every domain on the server now has its DNS zone. Open DNS to check the nameservers, then point your domains at them.":
        "Mọi tên miền trên server đã có zone DNS. Mở trang DNS để kiểm tra nameserver, rồi trỏ các tên miền của bạn về đó.",
    "Every domain on the server has its DNS zone.": "Mọi tên miền trên server đã có zone DNS.",
    "Every domain on the server has its DNS zone. New zones: {zones}, new records: {records}, owners corrected: {owners}.":
        "Mọi tên miền trên server đã có zone DNS. Zone mới: {zones}, bản ghi mới: {records}, sửa chủ sở hữu: {owners}.",
    "Set two nameservers and the IP address for new zones, and turn on the automatic zones, first.":
        "Hãy đặt hai nameserver, địa chỉ IP cho zone mới và bật tự tạo zone trước.",
    "Give every domain on the server a DNS zone": "Tự cấp zone DNS cho mọi tên miền trên server",
    "Sync zones now": "Đồng bộ zone ngay",
    "Syncing...": "Đang đồng bộ...",
}
