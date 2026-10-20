# Tài liệu Provisioning API, Module WHMCS & Trợ lý AI MCP (`v1.0.6`)

Tài liệu này hướng dẫn các nhà cung cấp dịch vụ hosting tích hợp **SPanel** vào hệ thống quản lý khách hàng & thanh toán tự động **WHMCS**, sử dụng REST API cấp phát tài khoản và kết nối trợ lý AI qua giao thức **MCP (Model Context Protocol)**.

---

## 1. Tích hợp Module WHMCS (`modules/servers/spanel/`)

SPanel đi kèm sẵn module WHMCS chính thức đặt tại thư mục [`modules/servers/spanel/`](../modules/servers/spanel/).

### Bước 1: Cài đặt Module lên máy chủ WHMCS
Sao chép toàn bộ thư mục `modules/servers/spanel` vào thư mục tương ứng trên mã nguồn WHMCS của bạn:

```text
<whmcs_root>/modules/servers/spanel/
├── spanel.php
├── hooks.php
└── templates/
    └── clientarea.tpl
```

### Bước 2: Tạo API Token trên SPanel
1. Đăng nhập vào giao diện quản trị **SPanel** với quyền **Admin**.
2. Truy cập **Cài đặt → Cài đặt Panel (Panel Settings) → API Token**.
3. Tạo một **Provisioning API Token** mới và sao chép chuỗi token này.

### Bước 3: Thêm Server SPanel trong WHMCS
1. Trong WHMCS Admin, vào **System Settings → Servers → Add New Server**.
2. Điền **Hostname** hoặc **IP Address** của máy chủ SPanel.
3. Tại mục **Server Details**:
   - **Module:** Chọn `SPanel`.
   - **Username:** Điền `admin`.
   - **Access Hash (hoặc API Token):** Dán chuỗi **Bearer Token** vừa tạo ở Bước 2.
   - **Port:** `2222` (tick chọn **Use Secure Mode (SSL)**).
4. Nhấn **Test Connection** để xác nhận kết nối thành công.

---

## 2. Đặc tả các Endpoint Provisioning API

Toàn bộ các yêu cầu gọi đến Provisioning API đều sử dụng header xác thực:
```http
Authorization: Bearer <PROVISIONING_API_TOKEN>
Content-Type: application/json
```

| Chức năng WHMCS | Phương thức & Đường dẫn API | Mô tả hoạt động |
|---|---|---|
| `TestConnection` / `PackageLoader` | `GET /api/provisioning/plans` | Kiểm tra kết nối và lấy danh sách các gói dịch vụ (`Packages`) hiện có trên SPanel. |
| `CreateAccount` | `POST /api/provisioning/accounts` | Tạo tài khoản người dùng Linux + Panel, gán gói dịch vụ và khởi tạo website cho tên miền chính. |
| `SuspendAccount` | `POST /api/provisioning/accounts/{external_id}/suspend` | Tạm khoá tài khoản, huỷ mọi phiên đăng nhập hiện hành và chuyển hướng website sang trang thông báo tạm ngưng. |
| `UnsuspendAccount` | `POST /api/provisioning/accounts/{external_id}/unsuspend` | Mở khoá tài khoản và khôi phục hoạt động bình thường cho toàn bộ website của khách hàng. |
| `TerminateAccount` | `DELETE /api/provisioning/accounts/{external_id}` | Xoá hoàn toàn tài khoản người dùng cùng toàn bộ website, cơ sở dữ liệu, tệp tin và cấu hình Linux liên quan. |
| `ChangePassword` | `PATCH /api/provisioning/accounts/{external_id}/password` | Đồng bộ mật khẩu mới cho cả giao diện SPanel và tài khoản SFTP. |
| `ChangePackage` | `PATCH /api/provisioning/accounts/{external_id}/package` | Nâng cấp hoặc hạ cấp gói dịch vụ (thay đổi hạn mức dung lượng, băng thông, số lượng website). |
| `UsageUpdate` | `GET /api/provisioning/accounts/{external_id}/usage` | Lấy thống kê dung lượng ổ đĩa và số lượng website đang sử dụng để đồng bộ về WHMCS. |
| `LoginLink` / `ClientArea` | `POST /api/provisioning/accounts/{external_id}/login` | Sinh đường dẫn đăng nhập một lần (**Single Sign-On - SSO**) có hiệu lực trong 5 phút để khách hàng bấm vào từ WHMCS Client Area mà không cần nhập mật khẩu. |

> **Lưu ý về `external_id`:** Module WHMCS tự động định danh mỗi dịch vụ bằng mã `whmcs:<serviceid>`, đồng thời tôn trọng `username` hợp lệ do WHMCS truyền sang và bảo toàn ghi chú quản trị trong `tblhosting.notes`.

---

## 3. Kết nối Trợ lý AI qua Giao thức MCP (Model Context Protocol)

Khi bật tiện ích **Trợ lý AI (MCP)** trong **Cài đặt → Tiện ích**, người dùng và quản trị viên có thể tạo **MCP Personal Token** để kết nối các công cụ lập trình AI hiện đại (như **Claude Code**, **Cursor**, **VS Code Copilot / Cline**) trực tiếp vào SPanel:

- **Khả năng hỗ trợ qua MCP:**
  - Kiểm tra trạng thái website, đọc nhật ký lỗi (Access/Error Logs) để chẩn đoán lỗi 500/502.
  - Quản lý tệp mã nguồn, kiểm tra cấu hình cơ sở dữ liệu và hỗ trợ tối ưu hoá mã nguồn ngay trong phạm vi phân quyền của token.
- **Bảo mật tuyệt đối:**
  - Mỗi MCP Token gắn chặt với quyền hạn của người tạo (End User chỉ thao tác được trên website của mình; Admin có thể giám sát toàn máy chủ) và có thể thu hồi (Revoke) bất kỳ lúc nào.
