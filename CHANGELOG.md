# Changelog — SPanel

Mọi thay đổi, bản vá bảo mật, tối ưu hiệu năng và tính năng mới của **SPanel** ([https://github.com/shostwp-byte/spanel](https://github.com/shostwp-byte/spanel)) được ghi nhận tại tài liệu này.

---

## [v1.0.6] — 2026-10-20

### 🛠️ Khắc phục Lỗi Triển khai VPS & Cơ sở Dữ liệu (Deployment & Migration Hotfixes)
- **Khắc phục triệt để lỗi `npm error code ERESOLVE` khi build Frontend trên VPS mới (`frontend/package.json`, `installer/install.sh`, `installer/update.sh`):**
  - Đồng bộ bộ phụ thuộc `devDependencies` trong `frontend/package.json` (`@eslint/js@^9.22.0`, `eslint@^9.22.0`, `eslint-plugin-react@^7.37.5`, `globals@^16.0.0`) để loại bỏ hoàn toàn xung đột peer dependency giữa `@eslint/js@10.x` và `eslint-plugin-react@7.37.5`.
  - Bổ sung cờ `--no-audit --no-fund --legacy-peer-deps` (kèm cơ chế fallback `--force`) vào bước `npm install` trong `installer/install.sh` và `installer/update.sh` để đảm bảo quá trình build giao diện trên VPS mới không bao giờ bị gián đoạn bởi xung đột gói phụ thuộc thượng nguồn.
- **Khắc phục lỗi khởi tạo Database Alembic trên VPS mới (`backend/alembic/versions/f7a7bbef3278_add_missing_foreign_keys_and_cascades.py`, `backend/app/core/database.py`):**
  - Kiểm tra sự tồn tại của bảng tạm `_alembic_tmp_api_tokens` trước khi xoá trong migration `f7a7bbef3278`, ngăn chặn lỗi `sqlite3.OperationalError: no such table: _alembic_tmp_api_tokens` khi chạy `python -m app.seed` trên VPS mới hoàn toàn.
  - Chuẩn hoá tên các khoá ngoại (Foreign Key Constraints) và kiểm tra tồn tại cột/chỉ mục an toàn trong `f7a7bbef3278`, đồng thời bổ sung `Base.metadata.create_all(bind=engine)` trong `run_migrations()` để đảm bảo 100% bảng dữ liệu luôn đồng bộ.
- **Hỗ trợ linh hoạt Tag và Nhánh (`main` / `1.0.6`) trong bộ cài đặt & cập nhật (`install.sh`, `installer/update.sh`, `installer/files/spanel-update`):**
  - Cho phép tải gói cài đặt từ Release Tag (`v1.0.6`) hoặc tự động chuyển sang nhánh `main` / `1.0.6` / `HEAD` trên kho GitHub `https://github.com/shostwp-byte/spanel`.

### ✨ Tính năng Mới & Cải tiến Giao diện (Features & UX/UI Enhancements)
- **Quản lý Tên miền & Subdomain chuẩn DirectAdmin (`frontend/src/pages/Websites.jsx`):**
  - **Domain Setup (`/website`):** Tích hợp thanh chọn tên miền nhanh (`Top Domain Selector Bar`), nút `+ ADD NEW`, `✎ RENAME DOMAIN`, thanh trạng thái hạn mức Quota, thao tác hàng loạt (bật/tắt Force SSL, đổi phiên bản PHP, bật/tắt cô lập `Open_basedir`, xoá tên miền) và tuỳ chỉnh ẩn/hiện cột (`Columns ⌄`).
  - **Subdomain Management (`/subdomains`):** Tách biệt trang quản lý Subdomain độc lập có định tuyến `/subdomains`, hỗ trợ tạo Subdomain tức thì với bộ lọc tiền tố tự động, tuỳ chỉnh đường dẫn `Document Root`, theo dõi băng thông và trạng thái SSL/PHP.
- **Tối ưu hoá Giao diện Đa thiết bị & Di động (`frontend/src/ui.css`, `frontend/src/pages/Websites.jsx`):**
  - Tự động tinh gọn các cột phụ trên màn hình điện thoại (`<= 640px`) trong bảng Domains và Subdomains.
  - Bổ sung hệ thống Media Queries cho Tablet (`<= 950px`), Mobile (`<= 768px`) và màn hình nhỏ (`<= 480px`), cố định khung chuông thông báo trong khung nhìn di động và hỗ trợ cuộn mượt cho bảng/hộp thoại Modal.
- **Phân loại Thông báo theo Vai trò (`Admin` / `Reseller` / `End User`):**
  - Phân luồng thông báo độc lập cho từng cấp độ người dùng kèm thanh lọc danh mục (`Tất cả`, `Chưa đọc`, `Hệ thống`, `Bảo mật`, `Sao lưu`, `Tài nguyên`).
- **Tự động Đồng bộ Menu End User theo Trạng thái Add-ons:**
  - Tự động ẩn các mục menu liên quan ở giao diện End User khi Admin chưa cài đặt hoặc tắt tiện ích mở rộng tương ứng (Application/Docker, Email Server, PowerDNS, Malware Scanner, MCP AI).
- **Biên soạn lại Toàn bộ Hệ thống Tài liệu (`docs/` & `README.md`):**
  - Làm sạch toàn bộ hình ảnh/thông tin cũ và xuất bản bộ tài liệu kỹ thuật mới của SPanel `v1.0.6` (`docs/README.md`, `docs/FEATURES.md`, `docs/INSTALLATION_AND_UPDATE.md`, `docs/ADMIN_AND_CLI_GUIDE.md`, `docs/API_AND_WHMCS.md`, `docs/index.html`).
