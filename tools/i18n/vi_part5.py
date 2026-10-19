# Vietnamese, part 5: the labels that appear while something is happening, and
# the prompts that ask before it does.
#
# These are the most-read strings in the panel and the first pass missed every
# one of them: they are the third argument to request(), not text in the tree,
# so an extractor looking between tags never sees them. Coverage went from
# counting entries to counting t() calls after that.
#
# Kept formulaic on purpose. "Đang tạo...", "Đang xoá...", "Đang lưu..." repeat
# because the English repeats, and a person watching a progress line wants to
# recognise the shape at a glance rather than read a new sentence each time.

PART5 = {
    # --- creating ---
    "Creating user...": "Đang tạo người dùng...",
    "Creating package...": "Đang tạo gói...",
    "Creating database...": "Đang tạo database...",
    "Creating WordPress website...": "Đang tạo website WordPress...",
    "Creating application...": "Đang tạo ứng dụng...",
    "Creating SFTP account...": "Đang tạo tài khoản SFTP...",
    "Creating API token...": "Đang tạo API token...",
    "Creating token...": "Đang tạo token...",
    "Creating file...": "Đang tạo file...",
    "Creating folder...": "Đang tạo thư mục...",
    "Creating archive...": "Đang nén...",
    "Adding cron job...": "Đang thêm cron job...",
    "Adding IP blocklist URL...": "Đang thêm URL danh sách chặn...",

    # --- deleting ---
    "Deleting...": "Đang xoá...",
    "Deleting website...": "Đang xoá website...",
    "Deleting database...": "Đang xoá database...",
    "Deleting application...": "Đang xoá ứng dụng...",
    "Deleting cron job...": "Đang xoá cron job...",
    "Deleting rule...": "Đang xoá rule...",
    "Deleting passkey...": "Đang xoá passkey...",
    "Deleting selected files...": "Đang xoá các file đã chọn...",
    "Deleting backup...": "Đang xoá backup...",
    "Deleting backup schedule...": "Đang xoá lịch backup...",
    "Deleting full user backup...": "Đang xoá backup toàn bộ tài khoản...",
    "Deleting restore backup...": "Đang xoá file backup dùng để khôi phục...",
    "Deleting SFTP target...": "Đang xoá đích lưu SFTP...",
    "Deleting DA backup...": "Đang xoá backup DA...",
    "Deleting IP blocklist URL...": "Đang xoá URL danh sách chặn...",
    "Removing SFTP account...": "Đang gỡ tài khoản SFTP...",
    "Revoking...": "Đang thu hồi...",
    "Renaming...": "Đang đổi tên...",

    # --- saving and updating ---
    "Saving configuration...": "Đang lưu cấu hình...",
    "Saving panel settings...": "Đang lưu cài đặt panel...",
    "Saving admin account...": "Đang lưu tài khoản quản trị...",
    "Saving file...": "Đang lưu file...",
    "Saving passkey...": "Đang lưu passkey...",
    "Saving backup schedule...": "Đang lưu lịch backup...",
    "Saving scan schedule...": "Đang lưu lịch quét...",
    "Saving HTTP Flood settings...": "Đang lưu cấu hình chống dội request...",
    "Saving website WAF rules...": "Đang lưu rule WAF của website...",
    "Saving blocked bots...": "Đang lưu danh sách bot bị chặn...",
    "Saving global bad bots...": "Đang lưu danh sách bot xấu toàn server...",
    "Saving OS auto update...": "Đang lưu cấu hình tự cập nhật hệ điều hành...",
    "Updating package...": "Đang cập nhật gói...",
    "Updating PHP config...": "Đang cập nhật cấu hình PHP...",
    "Updating SFTP password...": "Đang cập nhật mật khẩu SFTP...",
    "Updating signatures...": "Đang cập nhật mẫu nhận diện...",
    "Updating OS packages...": "Đang cập nhật gói hệ điều hành...",
    "Updating SPanel...": "Đang cập nhật SPanel...",
    "Changing database password...": "Đang đổi mật khẩu database...",
    "Setting SFTP password...": "Đang đặt mật khẩu SFTP...",
    "Applying the new memory limit...": "Đang áp dụng giới hạn bộ nhớ mới...",
    "Applying the new CPU limit...": "Đang áp dụng giới hạn CPU mới...",
    "Applying Custom Nginx and reloading...":
        "Đang áp dụng Nginx tuỳ chỉnh và nạp lại...",
    "Clearing Custom Nginx...": "Đang xoá Nginx tuỳ chỉnh...",
    "Clearing access logs...": "Đang xoá nhật ký truy cập...",
    "Assigning domain to user...": "Đang gán tên miền cho người dùng...",
    "Moving application port...": "Đang chuyển cổng của ứng dụng...",
    "Fixing permissions...": "Đang sửa quyền...",
    "Rewriting Nginx security template...":
        "Đang ghi lại mẫu bảo mật Nginx...",
    "Restoring PHP defaults...": "Đang khôi phục cấu hình PHP mặc định...",
    "Tuning PHP for this machine...": "Đang tối ưu PHP cho máy này...",

    # --- loading ---
    "Loading firewall...": "Đang tải tường lửa...",
    "Loading IP blocklists...": "Đang tải danh sách chặn IP...",
    "Loading WAF rules...": "Đang tải rule WAF...",
    "Loading website WAF...": "Đang tải WAF của website...",
    "Loading blocked bots...": "Đang tải danh sách bot bị chặn...",
    "Loading access logs...": "Đang tải nhật ký truy cập...",
    "Loading PHP config...": "Đang tải cấu hình PHP...",
    "Loading PHP versions...": "Đang tải các phiên bản PHP...",
    "Loading SFTP accounts...": "Đang tải tài khoản SFTP...",
    "Loading cron jobs...": "Đang tải cron job...",
    "Loading file list...": "Đang tải danh sách file...",
    "Loading Custom Nginx...": "Đang tải Nginx tuỳ chỉnh...",
    "Loading full Nginx config...": "Đang tải toàn bộ cấu hình Nginx...",
    "Loading scanner status...": "Đang tải trạng thái trình quét...",
    "Loading update status...": "Đang tải trạng thái cập nhật...",
    "Looking for backups...": "Đang tìm backup...",
    "Reading file...": "Đang đọc file...",

    # --- starting and running ---
    "Starting...": "Đang bắt đầu...",
    "Starting scan...": "Đang bắt đầu quét...",
    "Starting extraction...": "Đang giải nén...",
    "Starting bulk restore...": "Đang bắt đầu khôi phục hàng loạt...",
    "Starting DA import...": "Đang bắt đầu nhập từ DA...",
    "Starting ClamAV daemon...": "Đang khởi động ClamAV daemon...",
    "Scanning DA backup...": "Đang quét backup DA...",
    "Queueing backup...": "Đang xếp hàng backup...",
    "Queueing full user backup...": "Đang xếp hàng backup toàn bộ tài khoản...",
    "Queueing SFTP backup...": "Đang xếp hàng backup qua SFTP...",
    "Restoring backup...": "Đang khôi phục backup...",
    "Restoring full user backup...": "Đang khôi phục backup toàn bộ tài khoản...",
    "Downloading full user backup...": "Đang tải backup toàn bộ tài khoản về...",
    "Checking the bucket...": "Đang kiểm tra bucket...",
    "Checking the compose file...": "Đang kiểm tra file compose...",
    "Refreshing IP blocklists...": "Đang làm mới danh sách chặn IP...",
    "Pruning unused Docker layers...": "Đang dọn layer Docker không dùng...",

    # --- firewall, SSL, 2FA, scanner ---
    "Enabling firewall...": "Đang bật tường lửa...",
    "Disabling firewall...": "Đang tắt tường lửa...",
    "Reloading firewall...": "Đang nạp lại tường lửa...",
    "Blocking IP...": "Đang chặn IP...",
    "Allowing IP...": "Đang cho phép IP...",
    "Opening port...": "Đang mở cổng...",
    "Installing Let's Encrypt SSL...": "Đang cài SSL Let's Encrypt...",
    "Installing manual SSL...": "Đang cài SSL thủ công...",
    "Installing panel SSL...": "Đang cài SSL cho panel...",
    "Issuing wildcard certificate via Cloudflare...":
        "Đang cấp chứng chỉ wildcard qua Cloudflare...",
    "Installing Docker, this takes a few minutes...":
        "Đang cài Docker, việc này mất vài phút...",
    "Preparing 2FA...": "Đang chuẩn bị 2FA...",
    "Enabling 2FA...": "Đang bật 2FA...",
    "Preparing passkey...": "Đang chuẩn bị passkey...",

    # --- the prompts that ask first ---
    "Delete this backup schedule?": "Xoá lịch backup này?",
    "Delete this SFTP target?": "Xoá đích lưu SFTP này?",
    "Delete this DA backup file?": "Xoá file backup DA này?",
    "Delete this website including files, vhost, database, and its SSL certificate?":
        "Xoá website này, gồm cả file, vhost, database và chứng chỉ SSL của nó?",
    "Disable the firewall? Every port will be reachable again.":
        "Tắt tường lửa? Mọi cổng sẽ mở trở lại.",
    "Enable the firewall now? SSH, the panel port and 80/443/465/587 stay open automatically.":
        "Bật tường lửa ngay? SSH, cổng panel và 80/443/465/587 vẫn tự động mở.",
    "Run apt-get update && apt-get upgrade now?":
        "Chạy apt-get update && apt-get upgrade ngay?",
    "Update SPanel from GitHub now? The API may restart and this page will reload when done.":
        "Cập nhật SPanel từ GitHub ngay? API có thể khởi động lại và trang này sẽ tự tải lại khi xong.",
    "Turn on real-time protection? The panel watches website directories and scans new files as they appear. If it is not installed yet, the panel installs it (1-3 minutes).":
        "Bật bảo vệ thời gian thực? Panel sẽ theo dõi thư mục website và quét file mới ngay khi chúng xuất hiện. Nếu chưa cài thì panel sẽ cài (1-3 phút).",

    # --- messages after the fact ---
    "Access logs cleared.": "Đã xoá nhật ký truy cập.",
    "Blocked bots saved.": "Đã lưu danh sách bot bị chặn.",
    "Global bad bots saved.": "Đã lưu danh sách bot xấu toàn server.",
    "Admin account updated.": "Đã cập nhật tài khoản quản trị.",
    "All scan schedules turned off.": "Đã tắt mọi lịch quét.",
    "Docker is ready.": "Docker đã sẵn sàng.",
}

# Labels that sit in front of a value, and two paragraphs the prose sweep
# reached only on the second pass.
PART5.update({
    "App:": "Ứng dụng:",
    "Last update:": "Cập nhật lần cuối:",
    "Missing log files:": "Thiếu file log:",
    "PHP-FPM pools:": "PHP-FPM pool:",
    "Pointers:": "Con trỏ:",
    "Release check failed:": "Kiểm tra bản phát hành thất bại:",
    "SSL enabled:": "SSL đang bật:",
    "Scan result:": "Kết quả quét:",
    "Warnings:": "Cảnh báo:",
    "Install WordPress -": "Cài WordPress -",
    "Nginx logs -": "Nhật ký Nginx -",
    "Terminal -": "Terminal -",
    "MCP clients refuse a self-signed certificate, so no assistant will connect until the panel has one. Install it under Panel settings → SSL.":
        "Client MCP từ chối chứng chỉ tự ký, nên không assistant nào kết nối được cho tới khi panel có chứng chỉ thật. Cài ở Cài đặt panel → SSL.",
    "Works with S3 and anything that speaks its API: Wasabi, Backblaze B2, DigitalOcean Spaces, Cloudflare R2, MinIO. The bucket is checked before the target is saved, so a destination that cannot be reached never gets attached to a schedule.":
        "Dùng được với S3 và mọi thứ nói cùng giao thức: Wasabi, Backblaze B2, DigitalOcean Spaces, Cloudflare R2, MinIO. Bucket được kiểm tra trước khi lưu, nên một đích lưu không kết nối được sẽ không bao giờ bị gắn vào lịch backup.",
})
