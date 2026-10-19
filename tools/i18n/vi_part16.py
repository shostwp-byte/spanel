# Vietnamese, part 16: DNS Manager laid out like OPanel's, and the Email
# page's zone badge (2026-09-29). Most come from OPanel's dictionary.

PART16 = {
    "A changed nameserver is written into the NS and SOA records of every zone. Register both names as glue (child nameserver) records at the registrar of their domain, pointing at this server.":
        "Nameserver thay đổi sẽ được ghi vào bản ghi NS và SOA của mọi zone. Hãy đăng ký cả hai tên làm bản ghi glue (child nameserver) tại nhà đăng ký tên miền của chúng, trỏ về server này.",
    "A mail server for the domain; the lowest priority is tried first.":
        "Máy chủ nhận thư của tên miền; số ưu tiên nhỏ nhất được thử trước.",
    "Added": "Đã thêm",
    "Checking the nameservers…": "Đang kiểm tra nameserver…",
    "DNS Manager on this server holds the zone {zone} and keeps these records in it, taking back the ones email no longer needs. \"In the zone\" is what the zone holds; the other badge is what public DNS answers, which matches once the domain's nameservers point here.":
        "Quản lý DNS trên server này đang giữ zone {zone}, tự giữ các bản ghi này trong đó và gỡ những bản ghi email không còn cần. \"Có trong zone\" là nội dung zone; nhãn còn lại là kết quả DNS công khai, sẽ khớp khi nameserver của tên miền trỏ về đây.",
    "DNS Manager's zone on this server": "Zone của Quản lý DNS trên server này",
    "Default ({ttl})": "Mặc định ({ttl})",
    "Default TTL": "TTL mặc định",
    "Delete the {type} record of {name}: {value}?": "Xoá bản ghi {type} của {name}: {value}?",
    "Deleting the record...": "Đang xoá bản ghi...",
    "Deleting the zone...": "Đang xoá zone...",
    "Every domain on the panel, answered by this server.": "Mọi tên miền trên panel, do server này trả lời.",
    "Every record of {zone} is deleted, and the domain stops resolving once its nameservers point here. Type the domain name to confirm.":
        "Mọi bản ghi của {zone} sẽ bị xoá, và tên miền sẽ không phân giải được nếu nameserver đang trỏ về đây. Gõ tên miền để xác nhận.",
    "Filter records": "Lọc bản ghi",
    "Follows DNS Manager's nameserver settings": "Theo cài đặt nameserver của Quản lý DNS",
    "Hands a subdomain to other nameservers.": "Giao một tên miền con cho nameserver khác.",
    "In the zone": "Có trong zone",
    "Kept in step with the email of {domain}": "Đồng bộ theo email của {domain}",
    "Makes the name an alias of another host. A name with a CNAME can have no other record.":
        "Biến tên này thành bí danh của một host khác. Tên đã có CNAME thì không được có bản ghi nào khác.",
    "Names are relative to {zone}: @ is the domain itself.": "Tên bản ghi tính theo {zone}: @ là chính tên miền.",
    "No domain matches the search.": "Không có tên miền nào khớp tìm kiếm.",
    "No domains yet.": "Chưa có tên miền nào.",
    "No longer on the panel": "Không còn trên panel",
    "No record matches the filter.": "Không có bản ghi nào khớp bộ lọc.",
    "Not delegated": "Chưa trỏ về",
    "Not in the zone": "Chưa có trong zone",
    "Not set yet: open Settings.": "Chưa đặt: mở Cài đặt.",
    "Open zone": "Mở zone",
    "Other nameservers": "Nameserver khác",
    "Put back the records the panel manages for {zone}: nameservers, websites and email. Records you added are kept.":
        "Khôi phục các bản ghi do panel quản lý cho {zone}: nameserver, website và email. Bản ghi bạn tự thêm vẫn được giữ.",
    "Records": "Bản ghi",
    "Restore panel records": "Khôi phục bản ghi của panel",
    "Restoring the panel's records...": "Đang khôi phục bản ghi của panel...",
    "Saving DNS settings...": "Đang lưu cài đặt DNS...",
    "Saving the record...": "Đang lưu bản ghi...",
    "Search domains": "Tìm tên miền",
    "Served from here": "Đang dùng server này",
    "Set {expected} as the nameservers of {zone} at its registrar. Until then these records are not used.":
        "Đặt {expected} làm nameserver của {zone} tại nhà đăng ký. Trước khi đổi, các bản ghi này chưa được dùng.",
    "Text such as SPF, DKIM or a site verification. A long value is split into strings for you.":
        "Văn bản như SPF, DKIM hoặc mã xác minh trang. Giá trị dài được tự tách thành nhiều chuỗi.",
    "The DNS records of your domains, answered by this server.":
        "Bản ghi DNS của các tên miền của bạn, do server này trả lời.",
    "The Email addon keeps this record for {domain} and writes it again when the email settings of {domain} change. Delete the {type} record of {name} anyway?":
        "Tiện ích Email giữ bản ghi này cho {domain} và sẽ ghi lại khi cài đặt email của {domain} thay đổi. Vẫn xoá bản ghi {type} của {name}?",
    "The IPv4 address the name points to.": "Địa chỉ IPv4 mà tên này trỏ tới.",
    "The IPv4 address the name points to. This server: {ip}.": "Địa chỉ IPv4 mà tên này trỏ tới. Server này: {ip}.",
    "The IPv6 address the name points to.": "Địa chỉ IPv6 mà tên này trỏ tới.",
    "The IPv6 address the name points to. This server: {ip}.": "Địa chỉ IPv6 mà tên này trỏ tới. Server này: {ip}.",
    "The administrator has not set the nameservers yet.": "Quản trị viên chưa đặt nameserver.",
    "The nameservers of {zone} could not be looked up.": "Không tra được nameserver của {zone}.",
    "The panel's records are back in {zone}.": "Đã khôi phục bản ghi của panel trong {zone}.",
    "This server: {address}.": "Server này: {address}.",
    "What public DNS answers now": "Kết quả DNS công khai hiện tại",
    "Where a service runs: weight port target, with the priority in its own field. The name is like _sip._tcp.":
        "Nơi một dịch vụ chạy: weight port target, số ưu tiên nhập ở ô riêng. Tên có dạng _sip._tcp.",
    "Zone {zone} deleted.": "Đã xoá zone {zone}.",
    "{n} d": "{n} ngày",
    "{n} h": "{n} giờ",
    "{n} min": "{n} phút",
    "{n} records": "{n} bản ghi",
    "{n} s": "{n} giây",
    "{zone} is answered by this server.": "{zone} đang do server này trả lời.",
    "{zone} uses other nameservers now ({found}). Set {expected} at its registrar.":
        "{zone} đang dùng nameserver khác ({found}). Hãy đặt {expected} tại nhà đăng ký.",
    "A zone for a domain that is not on the panel": "Zone cho tên miền không có trên panel",
    "The nameservers and default TTL of every zone on this server, and what a new zone holds.":
        "Nameserver và TTL mặc định của mọi zone trên server này, và những bản ghi mà một zone mới có.",
    "Which certificate authorities may issue for the domain, for example 0 issue letsencrypt.org.":
        "Những nhà cấp chứng chỉ được phép cấp SSL cho tên miền, ví dụ 0 issue letsencrypt.org.",
}
