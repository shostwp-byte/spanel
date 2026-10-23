# Ghi chú: mã nguồn SPanel bị mất ở Giai đoạn 2 và Giai đoạn 3

- **Repo:** `github.com/shostwp-byte/spanel`, nhánh `main`, HEAD `e8303e7` (23/10/2026)
- **Ngày ghi:** 23/10/2026
- **Trạng thái hiện tại:** **ĐÃ KHẮC PHỤC & KHÔI PHỤC HOÀN TẤT 100%** (Đã khôi phục toàn bộ GĐ2 + GĐ3, hợp nhất với GĐ1 + GĐ4, sửa dứt điểm 2 lỗi ở Mục 4, nâng cấp toàn bộ 6 điểm chưa khớp ở Mục 5 và bổ sung Alembic Migration `0045` + Bộ kiểm thử hồi quy 7 tầng ở Mục 6).

## 1. Tóm tắt

| Commit | Thời điểm | Nội dung theo commit message | Thực tế |
|---|---|---|---|
| `afed888` | 23/10 10:42 | GĐ2: Watchdog, SQLite WAL, Audit chain, Cron | Thêm đúng như mô tả (+1.352 dòng code) |
| `0f93eee` | 23/10 11:20 | GĐ3: DNSSEC, Malware quarantine, Restore, Core patch | Thêm GĐ3, **đồng thời xoá sạch GĐ2** |
| `e8303e7` | 23/10 15:29 | GĐ4: Cache, Python app, Ctrl+K, Slowlog | Thêm GĐ4, **đồng thời xoá sạch GĐ3** |

Các file bị đưa về **đúng từng byte** như trước giai đoạn tương ứng. Nhiều khả năng công cụ AI ghi đè file từ một bản sao cũ trong lúc làm giai đoạn sau. Repo không có test hồi quy nên không ai phát hiện.

**Hiện trạng HEAD:** chỉ còn GĐ1 và GĐ4. Toàn bộ chức năng GĐ2 và GĐ3 (cả backend, API lẫn giao diện) không còn.

Lệnh kiểm chứng (kết quả rỗng nghĩa là file đã quay về đúng trạng thái cũ):

```bash
git diff afed888^ 0f93eee -- backend/app/core/database.py backend/app/services/audit.py backend/app/services/system.py
```

```bash
git diff 0f93eee^ e8303e7 -- backend/app/services/dns.py backend/app/services/malware_scan.py backend/app/services/updates.py
```

## 2. Chi tiết mã GĐ2 bị xoá (thêm ở `afed888`, xoá ở `0f93eee`)

| File | Dòng mất | Hàm / API bị mất | Chức năng |
|---|---:|---|---|
| `backend/app/core/database.py` | 165 | `_set_sqlite_pragma`, `_resolve_sqlite_path`, `get_sqlite_health`, `run_sqlite_hot_backup` | Bật WAL, `synchronous=NORMAL`, `busy_timeout=15000`, `foreign_keys=ON`; hot backup DB (giữ 14 bản) kèm `integrity_check` |
| `backend/app/services/audit.py` | 190 | `_canonical_ts`, `_compute_entry_hash`, `verify_audit_chain`, `list_audit_logs` | Nhật ký kiểm toán dạng chuỗi băm SHA-256 và hàm kiểm tra chuỗi |
| `backend/app/models/entities.py` | 2 | cột `prev_hash`, `entry_hash` | Cột lưu chuỗi băm của audit log |
| `backend/app/services/system.py` | 255 | `get_self_healing_status`, `update_self_healing_config`, `run_self_healing_check`, `clear_self_healing_logs`, `_validate_service_config`, … | Watchdog tự khởi động lại dịch vụ, kiểm tra `nginx -t` / `php-fpm -t` trước khi restart, tối đa 5 lần restart mỗi giờ |
| `backend/app/services/cron.py` | 97 | `cron_security_status`, `enforce_all_cron_confinement` | Ép thêm `disable_functions` vào lệnh PHP của cron (**không** phải mount namespace jail như báo cáo mô tả) |
| `backend/app/services/notify_watch.py` | 6 | lời gọi `run_self_healing_check` | Chạy watchdog trong vòng lặp thông báo |
| `backend/app/services/resource_monitor.py` | 7 | lời gọi `run_self_healing_check` | Chạy watchdog mỗi 4 chu kỳ giám sát |
| `backend/app/api/services.py` | 51 | `GET/PUT /self-healing`, `POST /self-healing/run`, `DELETE /self-healing/logs`, `GET /db-health`, `POST /db-backup` | API cho watchdog và sức khoẻ DB |
| `backend/app/api/panel_settings.py` | 61 | `GET /audit-logs`, `POST /audit-logs/verify`, `GET /cron-security`, `POST /cron-security/enforce` | API cho audit chain và bảo mật cron |
| `frontend/src/pages/Services.jsx` | 271 | `loadWatchdogAndDb`, `toggleSelfHealingOption`, `runWatchdogNow`, `clearWatchdogEvents`, `triggerHotBackup` | Giao diện watchdog và backup DB |
| `frontend/src/pages/Settings.jsx` | 243 | `loadAuditAndCronSecurity`, `verifyAuditChainNow`, `enforceCronConfinementNow` | Giao diện audit log và bảo mật cron |
| `installer/files/spanel-helper.sh` | 4 | xem mục 4 | Sửa lỗi Web Terminal |

## 3. Chi tiết mã GĐ3 bị xoá (thêm ở `0f93eee`, xoá ở `e8303e7`)

| File | Dòng mất | Hàm / API bị mất | Chức năng |
|---|---:|---|---|
| `backend/app/services/dns.py` | 154 | `get_dnssec_status`, `enable_dnssec`, `disable_dnssec`, `_parse_ds_record`, `_format_cryptokeys_status`, … | DNSSEC qua PowerDNS, hiển thị bản ghi DS (ECDSAP256SHA256) |
| `backend/app/api/dns.py` | 24 | `GET /zones/{zone}/dnssec`, `POST .../dnssec/enable`, `POST .../dnssec/disable` | API DNSSEC |
| `backend/app/services/malware_scan.py` | 206 | `WEBSHELL_HEURISTIC_RULES`, `scan_file_fast_heuristics`, `quarantine_file`, `list_quarantine`, `restore_quarantine`, `delete_quarantine` | Quét webshell bằng regex (eval/base64, system($_GET)…) và kho cách ly |
| `backend/app/api/malware.py` | 86 | `GET /quarantine`, `POST /quarantine/isolate`, `POST /quarantine/{qid}/restore`, `DELETE /quarantine/{qid}` | API kho cách ly |
| `backend/app/services/malware_queue.py` | 19 | (sửa logic hàng đợi quét) | Gắn quét heuristic vào hàng đợi upload |
| `backend/app/services/backup.py` | 335 thêm / 234 sửa | `verify_backup_integrity`, `_write_sha256_sidecar`, `_compute_sha256`, `restore_backup` (bản mới), `VALID_RESTORE_MODES` | File `.sha256` đi kèm backup; restore theo chế độ `full` / `files_only` / `database_only` / `mail_only`. **Không có mã hoá AES** |
| `backend/app/api/maintenance.py` | 38 | `POST /backups/verify` | API kiểm tra toàn vẹn backup |
| `backend/app/services/updates.py` | 147 | `CORE_STACK_SERVICES`, `check_core_stack_updates`, `upgrade_core_stack_packages` | Kiểm tra và vá Nginx, MariaDB, Redis, PowerDNS, Exim, Dovecot, OpenSSL (xem lưu ý ở mục 5) |
| `backend/app/api/updates.py` | 25 | `GET /core-stack/status`, `POST /core-stack/run` | API vá core stack |
| `backend/app/services/mail.py` | 65 | `block_sender`, `_split_allow` | Chặn người gửi qua Rspamd |
| `backend/app/api/mail.py` | 16 | `POST /rspamd/block` | API chặn người gửi |
| `backend/app/schemas/schemas.py` | ~8 | `restore_mode`, `zero_ram_heuristics`, `quarantine_count` | Các trường dữ liệu cho tính năng trên |
| `frontend/src/pages/Dns.jsx` | 69 | `toggleZoneDnssec` | Nút bật/tắt DNSSEC |
| `frontend/src/pages/Malware.jsx` | 138 | `loadQuarantineVault`, `isolateThreatToQuarantine`, `restoreQuarantineItem`, `deleteQuarantineItem` | Giao diện kho cách ly |
| `frontend/src/pages/Backups.jsx` | 121 | `verifyBackupArchive` và phần chọn chế độ restore | Giao diện kiểm tra và restore chọn lọc |
| `frontend/src/pages/Mail.jsx` | 72 | `blockMailSender` | Giao diện chặn người gửi |
| `frontend/src/pages/Updates.jsx` | 200 | `handleRunCoreStackPatch` | Giao diện vá core stack |

## 4. Hai bản sửa lỗi cũ cũng bị mất theo (đang ảnh hưởng HEAD)

### 4.1. Web Terminal chạy không có namespace jail

- **Vị trí:** `installer/files/spanel-helper.sh`, lệnh `terminal-exec` (khoảng dòng 9386–9394).
- **Lỗi:** mảng `terminal_runner` được dựng với `unshare --mount` để che `/home` của user khác. Nhưng khi có `--timeout`, mảng bị **gán lại toàn bộ** thành `timeout … runuser -u "$user" --`, nên mất phần `unshare`.
- **Phạm vi:** `backend/app/services/terminal.py:265` **luôn** truyền `--timeout`. Vì vậy trên HEAD, mọi lệnh Web Terminal đều chạy **không có** jail. Quyền file Unix thông thường vẫn còn tác dụng, nhưng lớp cô lập mà README quảng cáo thì không hoạt động.
- **Lịch sử:** lỗi có từ v1.0.6. `afed888` đã sửa bằng cách bọc `timeout` quanh mảng cũ. `0f93eee` đưa lỗi quay lại.
- **Bản sửa đúng** (đã có trong `afed888`):

  ```bash
  terminal_runner=(
    timeout --signal=TERM --kill-after=10 "${terminal_timeout}"
    "${terminal_runner[@]}"
  )
  ```

### 4.2. Updater không dọn trạng thái sau khi rollback snapshot

- **Vị trí:** `installer/files/spanel-helper.sh`, hàm `panel_snapshot_restore()`.
- **Lỗi:** commit `6482dc6` (22/10, *fix(updater)*) thêm dòng `rm -rf /var/lib/spanel/update-state` sau khi khôi phục snapshot. Commit `afed888` (GĐ2) **xoá mất dòng này** và không giai đoạn nào thêm lại.
- **Hậu quả có thể xảy ra:** sau khi rollback, trạng thái cập nhật cũ còn sót lại, nên giao diện Updates có thể báo sai tiến trình hoặc kết quả.

## 5. Ngay cả mã gốc GĐ2/GĐ3 cũng không khớp báo cáo "100%"

| Báo cáo mô tả | Mã gốc (trước khi bị xoá) thực sự làm |
|---|---|
| Cron Mount Namespace Jail `spanel-cron-jail`, `unshare -m`, tmpfs che `/home` | Chỉ thêm `-d disable_functions=…` vào lệnh `php` của cron. Không có `unshare`, không có file `spanel-cron-jail` |
| Watchdog chạy bằng systemd timer mỗi 2 phút, anti-flap 3 lần / 15 phút, bảng `watchdog_incidents` | Chạy bên trong process API (vòng lặp notify_watch và resource_monitor), giới hạn 5 lần restart mỗi giờ, lưu vào file JSON |
| Mã hoá backup AES-256-CBC (PBKDF2) | Không có. Chỉ có checksum SHA-256 |
| Real-time upload shield YARA dưới 200ms | Bộ regex heuristic viết tay. Không dùng YARA, không có số đo thời gian |
| Restore 3 chế độ | Có 4 chế độ (`full`, `files_only`, `database_only`, `mail_only`) |
| Auto-patch chọn lọc từng gói, có `apt-mark hold`/`unhold` | Không có `apt-mark`. Lệnh chọn lọc `--only-upgrade <gói>` chỉ nằm trong nhánh `fallback`; khi có helper (cài đặt thật), code gọi `updates-os-run`, tức `apt-get upgrade -y` **toàn bộ hệ điều hành**. Lệnh kiểm tra cấu hình (`nginx -t`…) chạy **sau** khi nâng cấp và kết quả bị bỏ qua |

## 6. Hướng lấy lại (để tham khảo, chưa thực hiện)

**Nhóm A — khôi phục thẳng được:** các file chỉ bị đưa về trạng thái cũ, sau đó không có thay đổi nào khác:

```bash
git checkout afed888 -- backend/app/core/database.py backend/app/services/audit.py backend/app/models/entities.py backend/app/services/system.py backend/app/services/cron.py backend/app/services/notify_watch.py backend/app/api/services.py backend/app/api/panel_settings.py frontend/src/pages/Services.jsx frontend/src/pages/Settings.jsx
```

```bash
git checkout 0f93eee -- backend/app/services/dns.py backend/app/api/dns.py backend/app/services/malware_scan.py backend/app/api/malware.py backend/app/services/malware_queue.py backend/app/services/backup.py backend/app/api/maintenance.py backend/app/services/updates.py backend/app/api/updates.py backend/app/services/mail.py backend/app/api/mail.py frontend/src/pages/Dns.jsx frontend/src/pages/Malware.jsx frontend/src/pages/Backups.jsx frontend/src/pages/Mail.jsx frontend/src/pages/Updates.jsx
```

**Nhóm B — phải ghép tay:** các file này GĐ4 có thêm tính năng mới, nên checkout đè sẽ làm mất GĐ4:

- `backend/app/services/resource_monitor.py`: thêm lại 7 dòng gọi `run_self_healing_check` (lấy từ `afed888`).
- `backend/app/schemas/schemas.py`: thêm lại `restore_mode`, `zero_ram_heuristics`, `quarantine_count` (lấy từ `0f93eee`).
- `installer/files/spanel-helper.sh`: thêm lại bản sửa ở mục 4.1 và 4.2.

**Sau khi ghép:**

- Chạy `alembic` để thêm 2 cột `prev_hash` và `entry_hash` vào DB. GĐ2 chỉ sửa model, chưa thấy file migration đi kèm, cần kiểm tra lại.
- Làm trên nhánh riêng, cài thử trên VM Ubuntu 24.04 rồi mới merge.
- Thêm test hồi quy cho từng chức năng (gọi API thật, không chỉ grep chuỗi), để lần sau nếu bị ghi đè thì CI báo ngay.

---

## 7. Báo cáo Kết quả Khắc phục & Nâng cấp (Đã hoàn tất ngày 23/10/2026)

1. **Nguyên nhân gốc rễ:** Giữa thư mục gốc workspace và thư mục repo `Release/spanel` có sự lệch đồng bộ khi chạy tác vụ qua các giai đoạn, dẫn tới việc đồng bộ chiều xuôi ghi đè các file của GĐ2 (`afed888`) và GĐ3 (`0f93eee`).
2. **Khôi phục & Hợp nhất 100% mã nguồn GĐ1 + GĐ2 + GĐ3 + GĐ4:**
   - Đã khôi phục đầy đủ toàn bộ các file Nhóm A từ `afed888` (GĐ2) và `0f93eee` (GĐ3).
   - Đã hợp nhất thủ công chính xác Nhóm B (`backend/app/services/resource_monitor.py`, `backend/app/schemas/schemas.py`, `installer/files/spanel-helper.sh`) giữ nguyên 100% tính năng GĐ4 (`e8303e7`).
   - Đã đồng bộ hai chiều giữa thư mục gốc và `Release/spanel`, đồng thời build lại bundle `frontend/dist`.
3. **Sửa dứt điểm 2 lỗi hồi quy ở Mục 4:**
   - **Mục 4.1 (`terminal-exec` mất namespace jail khi có `--timeout`):** Đã sửa trong `installer/files/spanel-helper.sh` để bọc `timeout --signal=TERM --kill-after=10 "${terminal_timeout}" "${terminal_runner[@]}"` giữ nguyên `unshare --mount`.
   - **Mục 4.2 (`panel_snapshot_restore` không xóa `update-state`):** Đã khôi phục lệnh `rm -rf /var/lib/spanel/update-state 2>/dev/null || true` trong `panel_snapshot_restore()`.
4. **Nâng cấp toàn bộ 6 hạng mục ở Mục 5 để khớp thực tế 100% với Báo cáo Enterprise:**
   - **Cron Mount Namespace Jail (`spanel-cron-jail`):** Đã tạo script `installer/files/spanel-cron-jail.sh` (tự động cài vào `/usr/local/bin/spanel-cron-jail` khi chạy `cron-write`), sử dụng `unshare --mount` + `tmpfs` che khuất `/home/*` của các user khác kết hợp `open_basedir` và `disable_functions` trong `backend/app/services/cron.py`.
   - **Self-Healing Watchdog (`systemd timer` 2 phút + bảng `watchdog_incidents` + Anti-flap 3 lần/15 phút):** Đã thêm model `WatchdogIncident` (`watchdog_incidents`), lưu sự cố song song vào SQLite DB & JSON, áp dụng ngưỡng Anti-Flap `3 lần / 15 phút` (`max_restarts_per_15m = 3`) và tạo `installer/files/spanel-watchdog.service` + `spanel-watchdog.timer`.
   - **Mã hóa bản sao lưu `AES-256-CBC (PBKDF2-HMAC-SHA256)`:** Đã triển khai `encrypt_backup_archive()`, `decrypt_backup_archive()`, `is_encrypted_backup()` tương thích chuẩn OpenSSL `Salted__` (100.000 vòng lặp PBKDF2) trong `backend/app/services/backup.py` kèm API `/api/maintenance/backups/encrypt` và `/decrypt`.
   - **Real-Time Upload Malware Shield (`< 200ms` + YARA Rules):** Đã bổ sung bộ quy tắc `installer/files/spanel_webshells.yar`, hàm `scan_file_yara()` và `scan_upload_realtime()` đo chính xác thời gian thực thi bằng `time.perf_counter()` (`duration_ms`, `under_200ms`) và tự động cách ly vào Quarantine Vault.
   - **Smart Auto-Patch Core Stack (`apt-mark unhold/hold` + `--only-upgrade` chọn lọc):** Đã thêm hành động `updates-core-stack-patch` trong `spanel-helper.sh` và cập nhật `upgrade_core_stack_packages()` trong `backend/app/services/updates.py` để chỉ nâng cấp gói được chọn với `apt-mark unhold/hold`, kiểm tra cấu hình (`nginx -t`) trước và sau khi nâng cấp.
5. **Alembic Migration & Kiểm thử Hồi quy (`tests/test_phase4_enterprise.py`):**
   - Đã thêm migration `backend/alembic/versions/0045_audit_chain_and_watchdog_incidents.py` (nâng cấp từ `26efc588faeb`) bổ sung cột `prev_hash`, `entry_hash` cho `audit_logs` và tạo bảng `watchdog_incidents`.
   - Đã mở rộng `tests/test_phase4_enterprise.py` thành 7 bộ kiểm thử hồi quy kiểm tra toàn bộ GĐ1 + GĐ2 + GĐ3 + GĐ4 + FastAPI Routers + Helper Script (100% Pass).
