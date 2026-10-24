# Provisioning API, Module WHMCS, Webhook & Trợ lý AI MCP

Tài liệu này hướng dẫn nhà cung cấp hosting tích hợp **SPanel** với **WHMCS**
(hoặc HostBill, Blesta, Perfex CRM, hệ thống billing tự viết) qua REST API cấp
phát tài khoản, nhận sự kiện qua **Outbound Webhook**, và kết nối trợ lý AI qua
**MCP**. Phần cuối là [API reference bằng tiếng Anh](#english-api-reference).

- Đặc tả máy đọc được (OpenAPI 3): [`docs/openapi.json`](openapi.json). Panel
  cũng phục vụ bản đầy đủ tại `https://<panel>:2222/openapi.json`. Trang Swagger
  `/docs` bị Content-Security-Policy của panel chặn, hãy mở `openapi.json` bằng
  một trình xem OpenAPI bất kỳ.
- Tạo lại `docs/openapi.json` sau khi đổi API:
  `PYTHONPATH=backend python tools/export_openapi.py` (test
  `tests/test_api_docs.py` báo lỗi nếu file cũ).

---

## 1. Cài Module WHMCS (`modules/servers/spanel/`)

### Bước 1: Chép module lên WHMCS

```text
<whmcs_root>/modules/servers/spanel/
├── spanel.php
├── hooks.php
└── templates/
    └── clientarea.tpl
```

Yêu cầu: WHMCS 7.x/8.x, PHP 7.4 – 8.3, extension cURL. Không cần thư viện ngoài.

### Bước 2: Tạo API Token trên SPanel

1. Đăng nhập SPanel bằng tài khoản **Admin**.
2. Vào **Cài đặt → API & Billing Integration**.
3. Chọn quyền (scopes), giới hạn IP (chấp nhận IP đơn và dải CIDR như
   `103.1.2.0/24`, cả IPv6), thời hạn và giới hạn request/phút, rồi bấm
   **Tạo API Token**. Token (`bp_...`) chỉ hiện **một lần**.

Token dùng cho WHMCS cần các scope `plans:read,accounts:read,accounts:write,usage:read,sso`
(hoặc bộ cũ `provisioning:read,provisioning:write`, vẫn hoạt động y như trước).
Chỉ thêm `sso:admin` nếu muốn nút "Login to SPanel as Admin" trên WHMCS.

### Bước 3: Thêm Server SPanel trong WHMCS

**System Settings → Servers → Add New Server**

| Trường | Giá trị |
|---|---|
| Module | SPanel Hosting |
| Hostname | tên miền hoặc IP của panel |
| Access Hash (hoặc Password) | API Token ở Bước 2 |
| Secure | tick nếu panel chạy HTTPS |
| Port | `2222` (mặc định của module) |

Bấm **Test Connection** để kiểm tra.

### Bước 4: Cấu hình sản phẩm (Module Settings)

| # | Tuỳ chọn | Ghi chú |
|---|---|---|
| 1 | Package | Gói SPanel (tải tự động từ panel) |
| 2 | App Type | `php`, `static`, `wordpress` |
| 3 | PHP Version | |
| 4 | Install WordPress | cần tên miền |
| 5 | Auto SSL | cần tên miền |
| 6 | Is Reseller | tạo tài khoản Reseller |
| 7 | Reseller Package | gói hạn mức Reseller (khi bật Is Reseller) |
| 8 | Backup Before Terminate | sao lưu toàn bộ trước khi xoá (lệnh Terminate chờ sao lưu xong) |

**Configurable Options → hạn mức riêng từng dịch vụ.** Các configurable option
có tên sau ghi đè hạn mức của gói khi tạo và khi nâng/hạ cấp:

| Tên option (không phân biệt hoa thường) | Hạn mức SPanel | Giá trị |
|---|---|---|
| Disk Space, Disk, Storage, Web Space | dung lượng (MB) | `2048`, `10 GB`, hoặc số kèm "(GB)" trong tên option |
| Bandwidth, Traffic, Monthly Bandwidth | băng thông tháng (MB) | như trên |
| Websites, Sites, Domains, Addon Domains | số website | số nguyên |

`Unlimited` hoặc `0` nghĩa là không giới hạn.

**Mật khẩu SFTP.** Mỗi tài khoản SPanel có mật khẩu SFTP/SSH riêng, khác mật
khẩu panel, và SPanel không lưu bản đọc được. Tạo một *Custom Field* cho sản
phẩm tên **SFTP Password** (tick *Admin Only* nếu không muốn khách thấy): module
ghi mật khẩu vào đó khi tạo tài khoản, khi đổi mật khẩu làm xoay vòng SFTP, và
khi bấm nút **Reset SFTP Password**.

### Chức năng module hỗ trợ

| Chức năng WHMCS | Gọi API |
|---|---|
| TestConnection, Package loader | `GET /api/provisioning/v1/plans` |
| Reseller Package loader | `GET /api/provisioning/v1/reseller-plans` |
| CreateAccount | `POST /api/provisioning/v1/accounts` |
| SuspendAccount | `POST /api/provisioning/v1/accounts/{external_id}/suspend` |
| UnsuspendAccount | `POST /api/provisioning/v1/accounts/{external_id}/unsuspend` |
| TerminateAccount | `DELETE /api/provisioning/v1/accounts/{external_id}` (kèm `?backup=true` nếu bật tuỳ chọn 8) |
| ChangePassword | `PATCH /api/provisioning/v1/accounts/{external_id}/password` |
| ChangePackage | `PATCH /api/provisioning/v1/accounts/{external_id}/package` |
| UsageUpdate (cron hằng ngày) | `GET /api/provisioning/v1/accounts/usage` |
| ListAccounts, GetUserCount (Sync/Import) | `GET /api/provisioning/v1/accounts` |
| ServiceSingleSignOn ("Login to SPanel") | `POST /api/provisioning/v1/accounts/{external_id}/login` |
| AdminSingleSignOn | `POST /api/provisioning/v1/admin/login` |
| AdminServicesTabFields, ClientArea | `GET /api/provisioning/v1/accounts/{external_id}` và `.../usage` |
| Nút Reset SFTP Password | `POST /api/provisioning/v1/accounts/{external_id}/sftp-password` |
| Nút Create Backup | `POST /api/provisioning/v1/accounts/{external_id}/backup` |

Mỗi dịch vụ WHMCS là tài khoản SPanel có `external_id = whmcs:<serviceid>`.
Username được sinh dạng `sp<serviceid>_xxxxxxxx` và lưu vào dịch vụ trên WHMCS
**trước** khi gọi API, kèm `Idempotency-Key`, nên nếu WHMCS gọi lại sau khi bị
timeout thì vẫn nhận đúng tài khoản (và đúng mật khẩu SFTP) của lần đầu.

**Đổi username không được hỗ trợ.** Username chính là tài khoản Linux: tên thư
mục home chứa mọi website (`/home/<user>/<domain>`), PHP-FPM pool và socket mà
vhost trỏ tới, crontab, cgroup slice, thư mục và tên file backup, và các tài
khoản SFTP phụ gắn vào các đường dẫn đó. Đổi tên nghĩa là di chuyển tất cả khi
đang chạy, không có bước nguyên tử nào để quay lại nếu một phần lỗi. Billing
gọi tài khoản theo `external_id` nên không cần đổi username.

---

## 2. Token, quyền và giới hạn

| Scope | Cho phép |
|---|---|
| `plans:read` | `GET /plans`, `GET /reseller-plans` |
| `accounts:read` | `GET /accounts`, `GET /accounts/{external_id}` |
| `accounts:write` | tạo, tạm khoá, mở khoá, xoá, đổi mật khẩu, đổi gói, reset SFTP, sao lưu |
| `usage:read` | `GET /accounts/usage`, `GET /accounts/{external_id}/usage` |
| `sso` | `POST /accounts/{external_id}/login` |
| `sso:admin` | `POST /admin/login`: đăng nhập panel **bằng tài khoản admin đã tạo token** |
| `tokens:rotate` | `POST /token/rotate`: token tự xoay vòng |

Token cũ giữ nguyên phạm vi: `provisioning:read` = `plans:read` + `accounts:read`
+ `usage:read`; `provisioning:write` = `accounts:write` + `sso`. Không token cũ nào
có `sso:admin` hay `tokens:rotate`.

- **IP allowlist:** địa chỉ đơn hoặc CIDR, IPv4 và IPv6; client IPv4 đến qua
  socket IPv6 (`::ffff:a.b.c.d`) được so như IPv4. Để trống = mọi IP.
- **Hết hạn:** tuỳ chọn khi tạo (`expires_in_days`). Token hết hạn trả `401`.
- **Xoay vòng:** nút **Xoay vòng** (hoặc `POST /tokens/{id}/rotate`) tạo token
  mới cùng quyền/IP/giới hạn; token cũ ngừng ngay hoặc sau `grace_minutes` phút
  để kịp đổi trên WHMCS.
- **Rate limit:** mặc định 120 request/phút cho mỗi token (chỉnh từng token,
  tối đa 6000). Vượt quá trả `429`. Mỗi địa chỉ IP chỉ được xác thực sai 20
  lần/phút, sau đó cũng nhận `429`.

## 3. Idempotency-Key

Mọi lệnh ghi (POST/PATCH/DELETE) nhận header `Idempotency-Key: <1-255 ký tự A-Z a-z 0-9 . _ : ->`.

- Câu trả lời thành công đầu tiên được lưu (mã hoá) **24 giờ** theo từng token;
  gọi lại cùng key và cùng nội dung sẽ nhận đúng câu trả lời đó, kèm header
  `Idempotent-Replayed: true`, mà không chạy lại lệnh.
- Cùng key nhưng khác method/đường dẫn/nội dung → `422`.
- Gọi lại khi lần đầu vẫn đang chạy → `409`.
- Lệnh thất bại không được ghi nhớ: có thể thử lại với cùng key.

Tạo tài khoản vốn đã idempotent theo `external_id` (tài khoản đang active được
trả về luôn). Nếu tạo thất bại giữa chừng, SPanel huỷ toàn bộ: user panel,
website, database, vhost và user Linux do chính lần tạo đó sinh ra; chỉ còn bản
ghi `failed` để billing thấy lý do và thử lại.

---

## 4. Outbound Webhook

Cấu hình tại **Cài đặt → API & Billing Integration → Outbound Event Webhooks**
(admin và reseller). Mỗi sự kiện cho mỗi endpoint là một bản ghi trong hàng đợi
bền vững (`webhook_outbox`), không mất khi panel khởi động lại.

**Thử lại:** nhận phản hồi khác 2xx hoặc lỗi mạng thì gửi lại sau ~30 giây,
2 phút, 10 phút, 30 phút, 1 giờ, 2 giờ, 4 giờ (±10%), tối đa **8 lần**, rồi đánh
dấu `failed`. Lịch sử giữ 30 ngày, có nút **Gửi lại (Replay)**. URL đích được
kiểm tra mỗi lần gửi: tên miền phân giải ra địa chỉ loopback/private/link-local
bị từ chối, và redirect không được theo.

### Danh mục sự kiện

| Sự kiện | Khi nào | `data` |
|---|---|---|
| `account.created` | billing tạo tài khoản | thông tin tài khoản |
| `account.suspended` | tạm khoá | + `reason` |
| `account.unsuspended` | mở khoá | |
| `account.terminated` | xoá tài khoản | + `deleted_websites`, `backup` |
| `account.password_changed` | đổi mật khẩu panel | + `sftp_password_rotated` (không bao giờ kèm mật khẩu) |
| `account.package_changed` | đổi gói / hạn mức | + `limits` |
| `account.sftp_password_reset` | reset mật khẩu SFTP | (không kèm mật khẩu) |
| `account.backup_completed` / `account.backup_failed` | sao lưu qua API xong / lỗi | + `backup` |
| `webhook_test` | nút "Gửi thử" | |

Ngoài ra **mọi hành động trong audit log** (ví dụ `create_website`,
`delete_database`, `issue_ssl`, `provisioning_create`) vẫn được gửi dưới đúng
tên của nó như trước. Bộ lọc sự kiện của endpoint nhận danh sách tên cách nhau
bằng dấu phẩy, `*` (tất cả) hoặc họ sự kiện như `account.*`. Sự kiện `account.*`
được gửi tới endpoint của admin và của reseller sở hữu tài khoản.

### Header và chữ ký

| Header | Ý nghĩa |
|---|---|
| `X-SPanel-Event` | tên sự kiện |
| `X-SPanel-Delivery` | id bản ghi gửi, giống nhau ở mọi lần thử lại |
| `X-SPanel-Timestamp` | Unix time (giây) lúc ký lần gửi này |
| `X-SPanel-Signature-256` | `sha256=` + HMAC-SHA256(secret, `timestamp + "." + body`) |
| `X-SPanel-Signature` | HMAC-SHA256(secret, body) — chữ ký cũ, giữ để tương thích |

Bên nhận nên: kiểm tra `X-SPanel-Signature-256` bằng so sánh hằng thời gian,
từ chối timestamp lệch quá **5 phút** (chống phát lại), và bỏ trùng theo
`X-SPanel-Delivery` hoặc `id` trong payload. Secret được mã hoá khi lưu.

Ví dụ kiểm tra chữ ký (PHP):

```php
$body = file_get_contents('php://input');
$ts = $_SERVER['HTTP_X_SPANEL_TIMESTAMP'] ?? '';
$sig = $_SERVER['HTTP_X_SPANEL_SIGNATURE_256'] ?? '';
$expected = 'sha256=' . hash_hmac('sha256', $ts . '.' . $body, $secret);
if (!ctype_digit($ts) || abs(time() - (int) $ts) > 300 || !hash_equals($expected, $sig)) {
    http_response_code(401);
    exit;
}
$event = json_decode($body, true);
```

Python:

```python
import hashlib, hmac, time

def verify(secret: str, timestamp: str, body: bytes, signature: str) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > 300:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
```

---

## 5. Kết nối Trợ lý AI qua MCP (Model Context Protocol)

Khi bật tiện ích **Trợ lý AI (MCP)** trong **Cài đặt → Tiện ích**, người dùng và
quản trị viên có thể tạo **MCP Personal Token** để kết nối các công cụ lập trình
AI (Claude Code, Cursor, VS Code Copilot / Cline) vào SPanel:

- Kiểm tra trạng thái website, đọc nhật ký lỗi (Access/Error Logs) để chẩn đoán
  lỗi 500/502; quản lý tệp mã nguồn và cấu hình cơ sở dữ liệu trong phạm vi quyền
  của token.
- Mỗi MCP Token gắn với quyền của người tạo (End User chỉ thao tác trên website
  của mình; Admin giám sát toàn máy chủ) và có thể thu hồi bất kỳ lúc nào.

---

## English API reference

Base URL: `https://<panel-host>:<port>/api/provisioning/v1` (default port 2222).
All bodies are JSON. Machine-readable schemas: [`docs/openapi.json`](openapi.json).

### Authentication

```http
Authorization: Bearer bp_xxxxxxxxxxxxxxxx
Content-Type: application/json
```

Tokens are created by an administrator in **Settings → API & Billing
Integration** (or `POST /api/provisioning/v1/tokens` with an admin session).
Only a SHA-256 hash is stored. A token can have an expiry, an address allowlist
(single addresses and CIDR ranges, IPv4/IPv6) and its own rate limit.

| Status | Meaning |
|---|---|
| 401 | missing, unknown, revoked or expired token |
| 403 | caller address not in the allowlist, or missing scope (`detail` names it) |
| 404 | unknown `external_id` / package |
| 409 | conflict: username or domain exists, account suspended, request with this Idempotency-Key in progress, backup already running |
| 422 | invalid body, or Idempotency-Key reused for a different request |
| 429 | rate limit (default 120 requests/min per token; 20 failed authentications/min per address) |

Errors use FastAPI's shape: `{"detail": "..."}`.

### Scopes

| Scope | Endpoints |
|---|---|
| `plans:read` | `GET /plans`, `GET /reseller-plans` |
| `accounts:read` | `GET /accounts`, `GET /accounts/{external_id}` |
| `accounts:write` | `POST /accounts`, `POST .../suspend`, `POST .../unsuspend`, `DELETE /accounts/{external_id}`, `PATCH .../password`, `PATCH .../package`, `POST .../sftp-password`, `POST .../backup` |
| `usage:read` | `GET /accounts/usage`, `GET /accounts/{external_id}/usage` |
| `sso` | `POST /accounts/{external_id}/login` |
| `sso:admin` | `POST /admin/login` |
| `tokens:rotate` | `POST /token/rotate` |

Legacy scopes keep their exact reach: `provisioning:read` = `plans:read`,
`accounts:read`, `usage:read`; `provisioning:write` = `accounts:write`, `sso`.

### Idempotency

Send `Idempotency-Key` on any write. The first successful response is stored
(encrypted) for 24 hours per token and replayed for identical retries with
`Idempotent-Replayed: true`. Same key with a different method, path or body:
422. Retry while the first is still running: 409. Failed requests are not
stored, so they can be retried with the same key.

### Endpoints

| Method & path | Scope | Description |
|---|---|---|
| `GET /api/provisioning/v1/plans` | plans:read | Hosting packages: `[{id, name, slug, website_limit, storage_limit_mb, database_limit, ...}]` |
| `GET /api/provisioning/v1/reseller-plans` | plans:read | Reseller packages: `[{id, name, user_limit, total_storage_mb, ...}]` |
| `POST /api/provisioning/v1/accounts` | accounts:write | Create an account (below) |
| `GET /api/provisioning/v1/accounts` | accounts:read | List: `?page=1&per_page=100&status=&q=` → `{items, total, page, per_page}`. Default status filter: active, suspended, pending; `status=all` adds failed and terminated |
| `GET /api/provisioning/v1/accounts/usage` | usage:read | Usage of every account (array of the usage object) |
| `GET /api/provisioning/v1/accounts/{external_id}` | accounts:read | One account |
| `GET /api/provisioning/v1/accounts/{external_id}/usage` | usage:read | Usage of one account |
| `POST /api/provisioning/v1/accounts/{external_id}/suspend` | accounts:write | Body `{"reason": "..."}`; suspends the account (and a reseller's customers) |
| `POST /api/provisioning/v1/accounts/{external_id}/unsuspend` | accounts:write | Reactivate |
| `DELETE /api/provisioning/v1/accounts/{external_id}` | accounts:write | Terminate; `?backup=true` takes a full backup first, inside the request → `{ok, status, deleted_websites, backup?}` |
| `PATCH /api/provisioning/v1/accounts/{external_id}/password` | accounts:write | Body `{"password": "12-72 chars"}` → `{ok, sftp_password?, sftp_password_rotated?}` |
| `PATCH /api/provisioning/v1/accounts/{external_id}/package` | accounts:write | Body `{"package_id": 2, "reseller_package_id": null, "limits": {...}}` |
| `POST /api/provisioning/v1/accounts/{external_id}/sftp-password` | accounts:write | New random SFTP/SSH password → `{ok, sftp_password, sftp_username}` (shown once) |
| `POST /api/provisioning/v1/accounts/{external_id}/backup` | accounts:write | Queue a full backup → `{ok, queued}`; result in `account.backup_*` webhooks |
| `POST /api/provisioning/v1/accounts/{external_id}/login` | sso | One-time login link (300 s) → `{login_url, url, path, expires_in}` |
| `POST /api/provisioning/v1/admin/login` | sso:admin | One-time login link for the administrator who issued the token |
| `POST /api/provisioning/v1/token/rotate` | tokens:rotate | Body `{"grace_minutes": 0}` → `{token, info}`: replaces the calling token |

Administrator endpoints (panel session cookie, admin role): `GET /api/provisioning/v1/tokens`,
`POST /api/provisioning/v1/tokens` (`{name, scopes, allowed_ips, expires_in_days, rate_limit_per_minute}`),
`POST /api/provisioning/v1/tokens/{token_id}/rotate` (`{grace_minutes}`),
`DELETE /api/provisioning/v1/tokens/{token_id}`, `GET /api/provisioning/v1/scopes`.

#### Create account

```json
POST /api/provisioning/v1/accounts
{
  "external_id": "whmcs:42",
  "username": "sp42_ab12cd34",
  "password": "at-least-12-characters",
  "package_id": 2,
  "domain": "shop.example.com",
  "php_version": "8.4",
  "app_type": "php",
  "install_wordpress": false,
  "enable_ssl": false,
  "is_reseller": false,
  "reseller_package_id": null,
  "limits": {"storage_limit_mb": 10240, "bandwidth_limit_mb": 0, "website_limit": 5}
}
```

`username`: `^[a-z_][a-z0-9_-]{2,31}$`, not a reserved system name. `domain` is
optional (required for WordPress or SSL). `limits` fields are optional; 0 means
unlimited. The account first receives the package limits the panel's own user
form applies (websites, disk, bandwidth, terminal, SFTP sub-accounts,
mailboxes; databases are enforced from the package), then the overrides.

Response (`ProvisioningAccountOut`):

```json
{
  "external_id": "whmcs:42",
  "username": "sp42_ab12cd34",
  "email": "sp42_ab12cd34@users.spanel.dev",
  "domain": "shop.example.com",
  "package_id": 2,
  "package_name": "Basic",
  "service_label": "Basic #42",
  "status": "active",
  "panel_url": "https://panel.example.com:2222",
  "created_at": "2026-10-24T10:00:00",
  "is_reseller": false,
  "reseller_package_id": null,
  "last_action": "create",
  "sftp_password": "only-in-this-response"
}
```

An `external_id` that is already active returns the existing account (no
`sftp_password`). A suspended one returns 409. A failed or terminated one is
cleared and created again. A create that fails rolls back everything it made.

#### Usage object

```json
{
  "external_id": "whmcs:42",
  "storage_used_bytes": 524288000,
  "storage_limit_bytes": 10737418240,
  "storage_percent": 4.88,
  "website_count": 2,
  "database_count": 1,
  "bandwidth_used_bytes": 1073741824,
  "bandwidth_limit_bytes": 0,
  "user_count": 0
}
```

Bandwidth is the current calendar month of nginx traffic as counted by the
resource monitor; limits of 0 mean unlimited. For a reseller, figures include
the accounts they own (`user_count`).

### Webhooks

Configured in the panel (`/api/resellers/webhooks`, session auth). The event
catalogue is served at `GET /api/resellers/webhooks/events`. Payload of a
lifecycle event:

```json
{
  "id": "evt_5f2c9a0b1d3e4f60",
  "event": "account.suspended",
  "created_at": "2026-10-24T10:00:00+00:00",
  "data": {"external_id": "whmcs:42", "username": "sp42_ab12cd34", "status": "suspended", "reason": "Overdue"},
  "user_id": 17,
  "username": "sp42_ab12cd34",
  "target": "whmcs:42",
  "detail": "",
  "timestamp": "2026-10-24T10:00:00+00:00"
}
```

Audit-log events carry `{id, event, user_id, username, target, detail, timestamp}`.

Headers: `X-SPanel-Event`, `X-SPanel-Delivery` (stable across retries),
`X-SPanel-Timestamp` (Unix seconds), `X-SPanel-Signature-256`
(`sha256=` + HMAC-SHA256 of `"<timestamp>." + body` with the endpoint secret)
and the legacy `X-SPanel-Signature` (HMAC-SHA256 of the body alone). Verify the
v256 signature with a constant-time comparison and reject timestamps older than
300 seconds. Non-2xx answers and network errors are retried after about 30 s,
2 min, 10 min, 30 min, 1 h, 2 h and 4 h (8 attempts in total); deliveries to
hosts that resolve to private, loopback or link-local addresses are refused
and redirects are not followed.
