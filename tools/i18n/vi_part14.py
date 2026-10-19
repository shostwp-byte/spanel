# Vietnamese, part 14: the Email addon (2026-09-29) - its card, the Email
# page (domains, mailboxes, forwarders, relays, Rspamd and the server), the
# mailbox limit in packages and the API's errors.

PART14 = {
    # The addon card
    "Mailboxes on your domains, with a webmail your customers open from the panel without a password.":
        "Hộp thư trên tên miền của bạn, kèm webmail mà khách hàng mở thẳng từ panel, không cần mật khẩu.",
    "Installs Exim to send and receive, Dovecot for IMAP and POP3, and SPanel Webmail on port 2096.":
        "Cài Exim để gửi và nhận thư, Dovecot cho IMAP và POP3, và SPanel Webmail trên cổng 2096.",
    "Customers make mailboxes on the domains of their own websites, up to the number in their package. Administrators make them on any domain.":
        "Khách hàng tạo hộp thư trên tên miền website của chính mình, trong số lượng gói cho phép. Quản trị viên tạo được trên mọi tên miền.",
    "One click in the panel opens a mailbox in the webmail, with no password to type. Once webmail.<domain> points here, it can have its own address and certificate.":
        "Một cú nhấp trong panel mở hộp thư trên webmail, không cần gõ mật khẩu. Khi webmail.<tên miền> đã trỏ về server này, nó có thể có địa chỉ và chứng chỉ SSL riêng.",
    "Mail is kept in the customer's home directory: it counts toward their disk space and is in their account backups.":
        "Thư được lưu trong thư mục home của khách hàng: tính vào dung lượng của họ và có trong bản sao lưu tài khoản.",
    "Outgoing mail is signed with DKIM. With DNS Manager on, the DKIM, DMARC and webmail records are added to the domain's zone by themselves.":
        "Thư gửi đi được ký DKIM. Khi bật Quản lý DNS, các bản ghi DKIM, DMARC và webmail tự được thêm vào zone của tên miền.",
    "Needs the panel on a domain with a real certificate: mail clients connect to that name.":
        "Cần panel chạy trên tên miền có chứng chỉ SSL thật: ứng dụng email kết nối vào tên đó.",
    "Opens ports 25, 465, 587, 143, 993, 110, 995 and 2096. Many VPS providers block outgoing port 25 until asked; the Email page says whether this server can send.":
        "Mở các cổng 25, 465, 587, 143, 993, 110, 995 và 2096. Nhiều nhà cung cấp VPS chặn cổng 25 chiều đi cho đến khi bạn yêu cầu mở; trang Email cho biết server này có gửi được thư ra ngoài không.",
    "Refuses to install next to another mail server (Postfix, Sendmail). Once installed, websites' PHP mail() is sent through Exim as well.":
        "Không cài khi server đang chạy mail server khác (Postfix, Sendmail). Sau khi cài, hàm mail() của PHP trên các website cũng gửi qua Exim.",
    "Open Email to make the first mailbox. The webmail is on port 2096 of the panel's address.":
        "Mở trang Email để tạo hộp thư đầu tiên. Webmail ở cổng 2096 trên địa chỉ của panel.",
    "The mail, the mailboxes and the DKIM keys are kept, and work again when you install the addon again.":
        "Thư, hộp thư và khoá DKIM được giữ nguyên, và hoạt động lại khi bạn cài lại tiện ích.",

    # The Email page
    "Address": "Địa chỉ",
    "Mailbox name": "Tên hộp thư",
    "Creating mailbox...": "Đang tạo hộp thư...",
    "{used} of {limit} mailboxes used.": "Đã dùng {used}/{limit} hộp thư.",
    "Copied.": "Đã sao chép.",
    "No mailboxes yet.": "Chưa có hộp thư nào.",
    "Webmail": "Webmail",
    "Opening webmail...": "Đang mở webmail...",
    "Mail domains": "Tên miền email",
    "{n} mailboxes": "{n} hộp thư",
    "Mail app settings": "Cấu hình ứng dụng email",
    "or": "hoặc",
    "Mail server": "Mail server",
    "{name} is running": "{name} đang chạy",
    "{name} is not running": "{name} không chạy",
    "Can send to other mail servers": "Gửi được thư ra ngoài",
    "Outgoing port 25 is blocked": "Cổng 25 chiều đi bị chặn",

    # Packages and users
    "Mailboxes": "Hộp thư",

    # The API's errors
    "The Email addon is not installed.": "Tiện ích Email chưa được cài.",
    "The mail server needs a name. Give the panel a domain first, in Settings.":
        "Mail server cần có tên. Hãy đặt tên miền cho panel trước, trong Cài đặt.",
    "The mailbox name may use letters, digits, dots, hyphens and underscores, such as info or sales.":
        "Tên hộp thư chỉ gồm chữ, số, dấu chấm, gạch ngang và gạch dưới, ví dụ info hoặc sales.",
    "The password cannot contain control characters.": "Mật khẩu không được chứa ký tự điều khiển.",
    "The password needs 8 to 128 characters.": "Mật khẩu cần từ 8 đến 128 ký tự.",
    "All the mailboxes in your hosting package are in use.": "Bạn đã dùng hết số hộp thư trong gói hosting.",
    "That address already exists.": "Địa chỉ này đã tồn tại.",
    "There is no such mailbox.": "Không có hộp thư này.",
    "There is no such mail domain.": "Không có tên miền email này.",
    "That webmail address is already a website on this server.": "Địa chỉ webmail này đã là một website trên server.",
    "The mail server did not accept the change.": "Mail server không nhận thay đổi này.",
    "The mail server gave an answer the panel could not read.": "Mail server trả về dữ liệu panel không đọc được.",
    "The webmail is not set up on this server. Reinstall the Email addon.":
        "Webmail chưa được cài trên server này. Hãy cài lại tiện ích Email.",

    # Spam filter, smarthost and the DNS template (2026-09-29, second round)
    "Rspamd filters incoming mail: clear spam is refused, likely spam goes to the Junk folder. The filtering log shows every decision and its reasons, and a sender blocked by mistake goes on the allowlist in one click.":
        "Rspamd lọc thư đến: thư rác rõ ràng bị từ chối, thư nghi rác vào thư mục Junk. Nhật ký lọc thư cho thấy mọi quyết định kèm lý do, và người gửi bị chặn nhầm được đưa vào danh sách cho phép chỉ với một cú nhấp.",
    "Outgoing mail can go through a smarthost (SMTP2GO, Mailgun, SendGrid...) when the provider blocks port 25; its SPF include is added to every domain's SPF.":
        "Thư gửi đi có thể đi qua smarthost (SMTP2GO, Mailgun, SendGrid...) khi nhà cung cấp chặn cổng 25; phần include SPF của smarthost được thêm vào SPF của mọi tên miền.",
    "Rspamd uses about 250 MB of memory while the spam filter is on, and keeps its log in the server's Redis.":
        "Rspamd dùng khoảng 250 MB RAM khi bật lọc thư rác, và lưu nhật ký trong Redis của server.",
    "Removing the addon stops Exim, Dovecot, Rspamd and the webmail and closes the ports. The mail, the mailboxes, the DKIM keys and the settings are kept.":
        "Gỡ tiện ích sẽ dừng Exim, Dovecot, Rspamd và webmail, và đóng các cổng. Thư, hộp thư, khoá DKIM và các cài đặt được giữ nguyên.",

    # The filtering log
    "Show": "Hiển thị",
    "Deferred": "Hoãn nhận",
    "Delivered": "Đã nhận",
    "(no subject)": "(không có tiêu đề)",
    "From": "Người gửi",
    "To": "Người nhận",
    "Why": "Lý do",
    "This sender is on the allowlist.": "Người gửi này nằm trong danh sách cho phép.",
    "Allow {sender}": "Cho phép {sender}",
    "Allow everyone at {domain}": "Cho phép mọi địa chỉ ở {domain}",
    "Never block mail from {sender}?": "Không bao giờ chặn thư từ {sender}?",
    "{sender} is on the allowlist. Ask the sender to send the message again.":
        "{sender} đã vào danh sách cho phép. Hãy nhờ người gửi gửi lại thư.",

    # The spam filter's settings
    "Spam filter": "Lọc thư rác",
    "Junk score": "Ngưỡng Junk",
    "Allowlist": "Danh sách cho phép",

    # The smarthost
    "Saved - leave empty to keep it": "Đã lưu, để trống nếu giữ nguyên",
    "Send a test message to": "Gửi thư thử tới",
    "Send a test message": "Gửi thư thử",
    "Sending a test message...": "Đang gửi thư thử...",
    "Exim logged nothing more about it.": "Exim không ghi thêm gì về thư này.",

    # The DNS template
    "Records for new zones": "Bản ghi mẫu cho zone mới",
    "One record per line: name, type, then the value; an MX value starts with its priority. {ip} is the address for new zones, {domain} the zone, and {spf} the SPF record:":
        "Mỗi dòng một bản ghi: tên, loại, rồi giá trị; giá trị MX bắt đầu bằng độ ưu tiên. {ip} là địa chỉ cho zone mới, {domain} là tên zone, {spf} là bản ghi SPF:",
    "Zones that already exist are not changed.": "Các zone đã có không bị thay đổi.",
    "Line {n}: {reason}": "Dòng {n}: {reason}",

    # The API's errors
    "Each allowlist line is an email address or a domain, such as friend@example.com or example.com.":
        "Mỗi dòng trong danh sách cho phép là một địa chỉ email hoặc một tên miền, ví dụ friend@example.com hoặc example.com.",
    "The allowlist is limited to 5000 lines.": "Danh sách cho phép tối đa 5000 dòng.",
    "Enter the address to send the test message to.": "Hãy nhập địa chỉ nhận thư thử.",
    "The spam filter is not set up on this server. Reinstall the Email addon.":
        "Bộ lọc thư rác chưa được cài trên server này. Hãy cài lại tiện ích Email.",
    "The spam filter is not answering. Check that Rspamd is running.":
        "Bộ lọc thư rác không trả lời. Hãy kiểm tra Rspamd có đang chạy không.",
    "Each line is a name, a type and a value, such as www A {ip}.":
        "Mỗi dòng gồm tên, loại và giá trị, ví dụ www A {ip}.",
    "The template can hold at most 100 records.": "Mẫu chứa tối đa 100 bản ghi.",

    # The Email page: tabs, filters and the mailbox list (2026-09-29, laid out like OPanel's)
    "Mailboxes, forwarders and DNS records of every mail domain on this server.":
        "Hộp thư, chuyển tiếp và bản ghi DNS của mọi tên miền email trên server này.",
    "{n} mailboxes.": "{n} hộp thư.",
    "Email sections": "Các mục Email",
    "Forwarders": "Chuyển tiếp",
    "Domains": "Tên miền",
    "Relays": "Relay",
    "All domains": "Tất cả tên miền",
    "Search": "Tìm kiếm",
    "Page {page} of {pages}": "Trang {page}/{pages}",
    "Mailbox limit reached": "Đã hết số hộp thư được tạo",
    "New mailbox": "Hộp thư mới",
    "You have used all {n} of your mailboxes. Delete one, or ask your provider for more.":
        "Bạn đã dùng hết {n} hộp thư. Hãy xoá bớt một hộp thư, hoặc nhờ nhà cung cấp tăng thêm.",
    "Your hosting package does not include mailboxes. Ask your provider.":
        "Gói hosting của bạn không có hộp thư. Hãy liên hệ nhà cung cấp.",
    "Choose a domain": "Chọn tên miền",
    "8+ characters, letters and digits": "Từ 8 ký tự, có cả chữ và số",
    "Size (MB)": "Dung lượng (MB)",
    "0 = unlimited": "0 = không giới hạn",
    "Copy the password now — it is not shown again. It signs in to webmail and to any mail app, with the full address as the username.":
        "Hãy sao chép mật khẩu ngay — nó sẽ không hiện lại. Mật khẩu này dùng để đăng nhập webmail và mọi ứng dụng email, với tên đăng nhập là địa chỉ email đầy đủ.",
    "No mailbox matches.": "Không có hộp thư nào khớp.",
    "Account": "Tài khoản",
    "Open this mailbox in webmail, no password needed": "Mở hộp thư này trên webmail, không cần mật khẩu",
    "Resume": "Mở lại",
    "Delete {name}": "Xoá {name}",
    "{address} created.": "Đã tạo {address}.",
    "{address} saved.": "Đã lưu {address}.",
    "Suspend {address}?\n\nIt keeps receiving mail, but nobody can sign in to it or send from it until it is resumed.":
        "Tạm khoá {address}?\n\nHộp thư vẫn nhận thư, nhưng không ai đăng nhập hay gửi thư từ nó được cho đến khi mở lại.",
    "{address} resumed.": "Đã mở lại {address}.",
    "{address} suspended.": "Đã tạm khoá {address}.",
    "Delete {address} and all of its mail?\n\nThis cannot be undone.":
        "Xoá {address} cùng toàn bộ thư trong đó?\n\nKhông thể hoàn tác.",
    "{address} deleted.": "Đã xoá {address}.",
    "(leave empty to keep)": "(để trống nếu giữ nguyên)",
    "A new password takes effect at once: mail apps using the old one must be updated.":
        "Mật khẩu mới có hiệu lực ngay: các ứng dụng email đang dùng mật khẩu cũ cần được cập nhật.",

    # Forwarders (2026-09-29, laid out like OPanel's)
    "New forwarder": "Chuyển tiếp mới",
    "Forwarder name": "Tên địa chỉ chuyển tiếp",
    "Forward to": "Chuyển tới",
    "one or more addresses, separated by commas": "một hoặc nhiều địa chỉ, cách nhau bằng dấu phẩy",
    "One or more addresses, separated by commas.": "Một hoặc nhiều địa chỉ, cách nhau bằng dấu phẩy.",
    "If a mailbox has the same address, it keeps a copy of each message as well.":
        "Nếu có hộp thư trùng địa chỉ, hộp thư đó vẫn giữ một bản sao của mỗi thư.",
    "No forwarder matches.": "Không có chuyển tiếp nào khớp.",
    "No forwarders yet.": "Chưa có chuyển tiếp nào.",
    "Keeps a copy": "Giữ bản sao",
    "Creating forwarder...": "Đang tạo chuyển tiếp...",
    "{address} now forwards to {destinations}.": "{address} giờ chuyển tiếp tới {destinations}.",
    "Delete the forwarder {address}?": "Xoá chuyển tiếp {address}?",

    # Mail domains (2026-09-29, laid out like OPanel's)
    "Turn on email for a domain": "Bật email cho một tên miền",
    "Choose one of your websites": "Chọn một website của bạn",
    "The website's owner": "Chủ sở hữu website",
    "Turn on email": "Bật email",
    "Turning on email...": "Đang bật email...",
    "Email is on for {domain}. Publish its DNS records next.":
        "Đã bật email cho {domain}. Tiếp theo hãy thêm các bản ghi DNS của nó.",
    "Every domain of your websites already has email. Add a website or a domain alias to use another one.":
        "Mọi tên miền website của bạn đều đã có email. Hãy thêm website hoặc tên miền phụ để dùng tên miền khác.",
    "No mail domains yet.": "Chưa có tên miền email nào.",
    "{mailboxes} mailboxes · {forwarders} forwarders": "{mailboxes} hộp thư · {forwarders} chuyển tiếp",
    "Catch-all": "Nhận thư mọi địa chỉ (catch-all)",
    "Off: unknown addresses are refused": "Tắt: địa chỉ không tồn tại bị từ chối",
    "Catch-all for {domain}": "Catch-all cho {domain}",
    "Mail to unknown addresses at {domain} now goes to {target}.":
        "Thư gửi tới địa chỉ không tồn tại ở {domain} giờ được chuyển tới {target}.",
    "Mail to unknown addresses at {domain} is now refused.":
        "Thư gửi tới địa chỉ không tồn tại ở {domain} giờ bị từ chối.",
    "Serve webmail at webmail.{domain} with its own certificate":
        "Chạy webmail tại webmail.{domain} với chứng chỉ SSL riêng",
    "Serve webmail at webmail.{domain}?\n\nIts A record must already point at this server: a Let's Encrypt certificate is issued for it now.":
        "Chạy webmail tại webmail.{domain}?\n\nBản ghi A của nó phải trỏ về server này rồi: chứng chỉ Let's Encrypt sẽ được cấp ngay bây giờ.",
    "Stop serving webmail at webmail.{domain}? Webmail stays available on the server's own address.":
        "Ngừng chạy webmail tại webmail.{domain}? Webmail vẫn dùng được trên địa chỉ của server.",
    "Issuing a certificate for webmail.{domain}...": "Đang cấp chứng chỉ SSL cho webmail.{domain}...",
    "Removing...": "Đang gỡ...",
    "Webmail is now at https://webmail.{domain}/": "Webmail đã chạy tại https://webmail.{domain}/",
    "webmail.{domain} removed.": "Đã gỡ webmail.{domain}.",
    "DNS records": "Bản ghi DNS",
    "This deletes every mailbox of {domain} with all its mail, and its forwarders. It cannot be undone.\n\nType the domain name to confirm:":
        "Thao tác này xoá mọi hộp thư của {domain} cùng toàn bộ thư, và các chuyển tiếp của nó. Không thể hoàn tác.\n\nGõ tên miền để xác nhận:",
    "The name did not match; nothing was deleted.": "Tên không khớp; chưa xoá gì cả.",
    "Email for {domain} deleted.": "Đã xoá email của {domain}.",

    # One domain's DNS records (2026-09-29, laid out like OPanel's)
    "DNS records for {domain}": "Bản ghi DNS cho {domain}",
    "Add these at the DNS provider of {domain}. A change can take a few hours to be seen everywhere.":
        "Hãy thêm các bản ghi này ở nhà cung cấp DNS của {domain}. Thay đổi có thể mất vài giờ để cập nhật khắp nơi.",
    "Check again": "Kiểm tra lại",
    "Checking DNS…": "Đang kiểm tra DNS…",
    "New DKIM key": "Tạo khoá DKIM mới",
    "Make a new DKIM key for {domain}?\n\nMail is signed with the new key at once, so update the DKIM record in DNS right away: until you do, receivers cannot verify the signature.":
        "Tạo khoá DKIM mới cho {domain}?\n\nThư được ký bằng khoá mới ngay lập tức, nên hãy cập nhật bản ghi DKIM trong DNS ngay: trước khi cập nhật, bên nhận không xác minh được chữ ký.",
    "Creating a new key...": "Đang tạo khoá mới...",
    "New DKIM key created. Update the DKIM record.": "Đã tạo khoá DKIM mới. Hãy cập nhật bản ghi DKIM.",
    "Outgoing mail": "Thư gửi đi",
    "Server default": "Mặc định của server",
    "Direct, without a relay": "Gửi trực tiếp, không qua relay",
    "Mail from {domain} leaves through the relay {relay}; the records it asks for are listed below.":
        "Thư từ {domain} được gửi qua relay {relay}; các bản ghi relay yêu cầu có ở bên dưới.",
    "Mail from {domain} is delivered directly from this server.": "Thư từ {domain} được gửi trực tiếp từ server này.",
    "Outgoing mail for {domain} saved.": "Đã lưu cách gửi thư cho {domain}.",
    "Found": "Đã có",
    "Missing": "Chưa có",
    "Different": "Khác",
    "Not checked": "Chưa kiểm tra",
    "Receiving mail (MX)": "Nhận thư (MX)",
    "Allowed senders (SPF)": "Máy chủ được phép gửi (SPF)",
    "Signature key (DKIM)": "Khoá chữ ký (DKIM)",
    "Policy (DMARC)": "Chính sách (DMARC)",
    "Webmail address (optional)": "Địa chỉ webmail (không bắt buộc)",
    "Asked for by the relay {relay}": "Relay {relay} yêu cầu",
    "priority {n}": "độ ưu tiên {n}",
    "Found now:": "Hiện đang có:",
    "Only needed for webmail.{domain}; turn that on in the Domains tab once this record is in place.":
        "Chỉ cần cho webmail.{domain}; hãy bật nó ở thẻ Tên miền khi bản ghi này đã có.",
    "Value ({domain} = the domain)": "Giá trị ({domain} = tên miền)",
    "Add a record": "Thêm bản ghi",
    "Names are relative to each domain that uses the relay: @ is the domain itself, brevo1._domainkey a name under it. {domain} in a value becomes the domain name.":
        "Tên được tính theo từng tên miền dùng relay: @ là chính tên miền, brevo1._domainkey là một tên bên dưới nó. {domain} trong giá trị sẽ thành tên miền.",

    # Relays and their DNS template (2026-09-29, laid out like OPanel's)
    "A relay (smarthost) sends this server's outgoing mail for it: needed where the provider blocks port 25, and it can help mail reach the inbox. Each domain uses the default relay unless its DNS page picks another one or direct delivery.":
        "Relay (smarthost) gửi thư đi thay cho server này: cần khi nhà cung cấp chặn cổng 25, và giúp thư vào hộp thư đến tốt hơn. Mỗi tên miền dùng relay mặc định, trừ khi trang DNS của nó chọn relay khác hoặc gửi trực tiếp.",
    "Default relay": "Relay mặc định",
    "None: deliver directly": "Không có: gửi trực tiếp",
    "New relay": "Relay mới",
    "No relay yet: mail leaves this server directly.": "Chưa có relay: thư được gửi trực tiếp từ server này.",
    "no login": "không đăng nhập",
    "no TLS": "không TLS",
    "{n} DNS records": "{n} bản ghi DNS",
    "Chosen by {domains}": "Được chọn bởi {domains}",
    "Edit relay {name}": "Sửa relay {name}",
    "Relay host": "Máy chủ relay",
    "None (private network only)": "Không có (chỉ dùng trong mạng riêng)",
    "Saved — leave empty to keep": "Đã lưu — để trống nếu giữ nguyên",
    "Mail DNS template": "Mẫu DNS cho email",
    "What every domain that sends through this relay must publish. Customers see it on their domain's DNS records page and set up their domain from it.":
        "Những gì mọi tên miền gửi thư qua relay này phải thêm vào DNS. Khách hàng thấy mẫu này trên trang bản ghi DNS của tên miền và cấu hình tên miền theo đó.",
    "SPF for this relay": "SPF cho relay này",
    "added to the SPF record of every domain that uses it": "được thêm vào bản ghi SPF của mọi tên miền dùng relay",
    "DNS records the relay asks for": "Bản ghi DNS relay yêu cầu",
    "Use it as the default relay": "Dùng làm relay mặc định",
    "587 uses STARTTLS and 465 SSL/TLS, and the relay's certificate must be valid. Leave the username empty for a relay that knows this server by its address.":
        "Cổng 587 dùng STARTTLS, cổng 465 dùng SSL/TLS, và chứng chỉ SSL của relay phải hợp lệ. Để trống tên đăng nhập nếu relay nhận biết server này qua địa chỉ IP.",
    "Save relay": "Lưu relay",
    "Saving relay...": "Đang lưu relay...",
    "Relay saved. Domains that use it need its DNS records.":
        "Đã lưu relay. Các tên miền dùng relay cần có các bản ghi DNS của nó.",
    "Delete the relay {name}?\n\nDomains that use it go back to the default relay.":
        "Xoá relay {name}?\n\nCác tên miền đang dùng nó sẽ quay về relay mặc định.",
    "{name} deleted.": "Đã xoá {name}.",
    "Default relay saved.": "Đã lưu relay mặc định.",
    "Mail now leaves directly, except for domains with a relay of their own.":
        "Thư giờ được gửi trực tiếp, trừ các tên miền có relay riêng.",
    "Sent from postmaster at the server name, through the relay the default route uses. What Exim logged for it is shown, the receiving server's answer included.":
        "Gửi từ postmaster trên tên server, qua relay của tuyến mặc định. Nhật ký Exim về thư này được hiển thị, kèm câu trả lời của server nhận.",

    # Rspamd (2026-09-29, laid out like OPanel's)
    "Scanned": "Đã quét",
    "Spam": "Thư rác",
    "Ham": "Thư sạch",
    "Learned": "Đã học",
    "Scan history": "Lịch sử quét",
    "Log": "Nhật ký",
    "Marked as spam": "Đánh dấu là thư rác",
    "Subject marked": "Đánh dấu tiêu đề",
    "Greylisted": "Tạm hoãn (greylist)",
    "Rejected": "Bị từ chối",
    "Sender, recipient, subject or IP": "Người gửi, người nhận, tiêu đề hoặc IP",
    "Every result": "Mọi kết quả",
    "No scanned message matches.": "Không có thư đã quét nào khớp.",
    "No message has been scanned yet.": "Chưa có thư nào được quét.",
    "signed in as {user}": "đăng nhập bằng {user}",
    "Rspamd keeps the last 2000 scans. A message sent by a signed-in mailbox is not scanned. A message stopped by a test pattern (GTUBE) is not kept.":
        "Rspamd lưu 2000 lượt quét gần nhất. Thư gửi từ hộp thư đã đăng nhập không bị quét. Thư bị chặn bởi mẫu thử (GTUBE) không được lưu.",
    "One email address or domain per line. Mail from them is never blocked or sent to Junk: use it when the scan history shows a mistake.":
        "Mỗi dòng một địa chỉ email hoặc một tên miền. Thư từ các địa chỉ này không bao giờ bị chặn hay đưa vào Junk: dùng khi lịch sử quét cho thấy bị chặn nhầm.",
    "Save allowlist": "Lưu danh sách cho phép",
    "Filter, e.g. an address or a message ID": "Lọc, ví dụ một địa chỉ hoặc message ID",
    "Filter": "Lọc",
    "Lines": "Số dòng",
    "Last {n} lines": "{n} dòng cuối",
    "No line matches.": "Không có dòng nào khớp.",
    "The log is empty.": "Nhật ký trống.",

    # The mail server's settings (2026-09-29, laid out like OPanel's)
    "Server name: {host}. Messages waiting to be sent: {queue}.": "Tên server: {host}. Thư đang chờ gửi: {queue}.",
    "This server cannot reach other mail servers on port 25, so mail to outside addresses waits in the queue and comes back after a few days. Ask the provider to open it, or send through a relay (Relays tab).":
        "Server này không kết nối được tới mail server khác qua cổng 25, nên thư gửi ra ngoài sẽ nằm chờ trong hàng đợi rồi bị trả về sau vài ngày. Hãy nhờ nhà cung cấp mở cổng, hoặc gửi qua relay (thẻ Relay).",
    "This server's provider lets DNS out only to its own resolvers, so Rspamd uses them; Spamhaus and other DNS blocklists refuse such resolvers, so those checks are off. The other checks still run.":
        "Nhà cung cấp của server này chỉ cho truy vấn DNS tới resolver của họ, nên Rspamd dùng các resolver đó; Spamhaus và các danh sách chặn qua DNS từ chối những resolver như vậy, nên các bước kiểm tra đó bị tắt. Các bước kiểm tra khác vẫn chạy.",
    "Recipients per mailbox per hour": "Số người nhận mỗi hộp thư mỗi giờ",
    "0 = no limit": "0 = không giới hạn",
    "Messages per website account per hour": "Số thư mỗi tài khoản website mỗi giờ",
    "Largest message (MB)": "Thư lớn nhất (MB)",
    "New mailbox size (MB)": "Dung lượng hộp thư mới (MB)",
    "Spam score: move to Junk": "Điểm thư rác: chuyển vào Junk",
    "Spam score: refuse": "Điểm thư rác: từ chối",
    "Spam filter (Rspamd) for mail from outside": "Lọc thư rác (Rspamd) cho thư từ bên ngoài",
    "Greylisting: doubtful senders are asked to retry a few minutes later":
        "Greylisting: người gửi đáng ngờ được yêu cầu gửi lại sau vài phút",
    "Apply mail settings": "Áp dụng cài đặt email",
    "Applying mail settings...": "Đang áp dụng cài đặt email...",
    "Mail settings applied.": "Đã áp dụng cài đặt email.",
    "Exim log": "Nhật ký Exim",

    # Mail app settings (2026-09-29, laid out like OPanel's)
    "For Outlook, Thunderbird, Apple Mail or a phone. The username is the full email address, the password the mailbox's own.":
        "Dùng cho Outlook, Thunderbird, Apple Mail hoặc điện thoại. Tên đăng nhập là địa chỉ email đầy đủ, mật khẩu là mật khẩu của hộp thư.",
    "Server (IMAP, POP3 and SMTP)": "Server (IMAP, POP3 và SMTP)",
    "port {port}, SSL/TLS": "cổng {port}, SSL/TLS",
    "port {port}, SSL/TLS — or {submission} with STARTTLS": "cổng {port}, SSL/TLS — hoặc {submission} với STARTTLS",

    # The API's errors (2026-09-29, laid out like OPanel's)
    "Enter a domain name such as example.com.": "Hãy nhập tên miền, ví dụ example.com.",
    "That is not an email address.": "Đây không phải địa chỉ email.",
    "A forwarder cannot forward to itself; add a mailbox of the same name instead.":
        "Chuyển tiếp không thể chuyển tới chính nó; hãy tạo hộp thư cùng tên thay vào đó.",
    "Enter at least one address to forward to.": "Hãy nhập ít nhất một địa chỉ để chuyển tới.",
    "A forwarder can have at most 20 addresses.": "Một chuyển tiếp có tối đa 20 địa chỉ.",
    "The password must contain both letters and digits.": "Mật khẩu phải có cả chữ và số.",
    "The password must not contain the mailbox name.": "Mật khẩu không được chứa tên hộp thư.",
    "The mailbox size is a whole number of MB.": "Dung lượng hộp thư là một số nguyên MB.",
    "The mailbox size is 0 (no limit) to 1048576 MB.": "Dung lượng hộp thư từ 0 (không giới hạn) đến 1048576 MB.",
    "The mailbox size is 1 to 51200 MB.": "Dung lượng hộp thư từ 1 đến 51200 MB.",
    "There is no such forwarder.": "Không có chuyển tiếp này.",
    "The mail server did not make a DKIM key.": "Mail server không tạo được khoá DKIM.",
    "Email is already on for that domain.": "Tên miền này đã bật email rồi.",
    "You can only turn on email for the domains of your own websites.":
        "Bạn chỉ bật email được cho tên miền các website của chính mình.",
    "The catch-all must be a mailbox or forwarder of this domain, or an outside address.":
        "Catch-all phải là một hộp thư hoặc chuyển tiếp của tên miền này, hoặc một địa chỉ bên ngoài.",
    "That domain's account no longer exists.": "Tài khoản của tên miền này không còn tồn tại.",
    "That address already forwards; edit that forwarder instead.":
        "Địa chỉ này đã có chuyển tiếp; hãy sửa chuyển tiếp đó.",
    "A DNS record has a type, a name and a value.": "Bản ghi DNS gồm loại, tên và giá trị.",
    "The record type is TXT, CNAME, MX, A or AAAA.": "Loại bản ghi là TXT, CNAME, MX, A hoặc AAAA.",
    "{domain} goes in the value; the name is relative to the domain already.":
        "{domain} chỉ dùng trong giá trị; tên đã được tính theo tên miền rồi.",
    "A record name is @ for the domain itself, or a name under it such as mail or s1._domainkey.":
        "Tên bản ghi là @ cho chính tên miền, hoặc một tên bên dưới như mail hay s1._domainkey.",
    "Every record needs a value, on one line, of at most 2048 characters.":
        "Mỗi bản ghi cần một giá trị trên một dòng, tối đa 2048 ký tự.",
    "A CNAME or MX record points at a hostname.": "Bản ghi CNAME hoặc MX phải trỏ tới một tên máy chủ.",
    "An A record points at an IPv4 address, an AAAA record at an IPv6 one.":
        "Bản ghi A trỏ tới địa chỉ IPv4, bản ghi AAAA trỏ tới địa chỉ IPv6.",
    "The MX priority is a number from 0 to 65535.": "Độ ưu tiên MX là một số từ 0 đến 65535.",
    "The relay's SPF part is at most 200 characters.": "Phần SPF của relay dài tối đa 200 ký tự.",
    "The relay's SPF part is one or more mechanisms, such as include:spf.brevo.com.":
        "Phần SPF của relay gồm một hoặc nhiều cơ chế, ví dụ include:spf.brevo.com.",
    "A name in this zone is a CNAME and cannot also hold the mail record. Change it on the DNS page.":
        "Một tên trong zone này là CNAME nên không chứa thêm bản ghi email được. Hãy sửa nó ở trang DNS.",
    "Only an administrator chooses the relay a domain sends through.":
        "Chỉ quản trị viên mới chọn được relay mà tên miền gửi thư qua.",
    "There is no such relay.": "Không có relay này.",
    "A spam score is a number from 1 to 100.": "Điểm thư rác là một số từ 1 đến 100.",
    "The reject score must be higher than the Junk score.": "Ngưỡng từ chối phải cao hơn ngưỡng Junk.",
    "At most 20 relays.": "Tối đa 20 relay.",
    "Give the relay a name of at most 64 characters.": "Hãy đặt tên relay, tối đa 64 ký tự.",
    "The relay port is a number from 1 to 65535.": "Cổng relay là một số từ 1 đến 65535.",
    "The relay's TLS is STARTTLS, SSL or none.": "TLS của relay là STARTTLS, SSL hoặc không có.",
    "A relay reached over TLS is given by name, such as smtp.example.com: its certificate is checked against it.":
        "Relay kết nối qua TLS phải nhập bằng tên, ví dụ smtp.example.com: chứng chỉ SSL của nó được kiểm tra theo tên này.",
    "The relay host is a name such as smtp.example.com.": "Máy chủ relay là một tên, ví dụ smtp.example.com.",
    "That relay user name cannot be used.": "Không dùng được tên đăng nhập relay này.",
    "A relay's DNS template has at most 10 records.": "Mẫu DNS của relay có tối đa 10 bản ghi.",
    "That relay password cannot be used; it cannot start or end with a space.":
        "Không dùng được mật khẩu relay này; mật khẩu không được bắt đầu hoặc kết thúc bằng dấu cách.",
    "Enter the relay's password, or leave the user name empty for a relay without a login.":
        "Hãy nhập mật khẩu relay, hoặc để trống tên đăng nhập nếu relay không cần đăng nhập.",
    "Another relay already signs in to that host.": "Đã có relay khác đăng nhập vào máy chủ này.",
    "This mailbox is suspended.": "Hộp thư này đang bị tạm khoá.",
    "Type the domain name to confirm.": "Hãy gõ tên miền để xác nhận.",

    # The DNS records page and DNS Manager (2026-09-29, one with DNS Manager)
    "Mail for a domain arrives here once its MX record points at this server; the DNS records to add are shown next.":
        "Thư của một tên miền sẽ về server này khi bản ghi MX trỏ về đây; các bản ghi DNS cần thêm sẽ hiện ở bước tiếp theo.",
    "Mail server address (A)": "Địa chỉ mail server (A)",
}
