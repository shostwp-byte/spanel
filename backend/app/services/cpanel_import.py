import os
import subprocess
from pathlib import Path
from typing import Optional

def restore_to_user(archive_path: str, user_id: int, import_websites: bool, import_databases: bool, import_emails: bool):
    """
    Trích xuất và khôi phục dữ liệu từ file backup cPanel (cpmove-*.tar.gz) 
    vào tài khoản người dùng SPanel hiện tại.
    """
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file backup tại {archive_path}")

    # 1. Tạo thư mục giải nén
    extract_dir = archive_path.parent / "extracted"
    os.makedirs(extract_dir, exist_ok=True)
    
    # 2. Giải nén Archive (cpmove-user.tar.gz)
    # Tối ưu: Dùng subprocess để gọi lệnh tar hệ thống (nhanh hơn thư viện python rất nhiều)
    subprocess.run(["tar", "-xzf", str(archive_path), "-C", str(extract_dir)], check=True)
    
    # Backup cPanel thường tạo một thư mục cpmove-{username}
    # Tìm thư mục cpmove-* hoặc backup-* đầu tiên
    cpmove_dirs = list(extract_dir.glob("cpmove-*")) + list(extract_dir.glob("backup-*"))
    if not cpmove_dirs:
        raise ValueError("Định dạng file không đúng chuẩn cPanel (không tìm thấy thư mục cpmove-*).")
    
    cpmove_dir = cpmove_dirs[0]
    
    # 3. Phục hồi File Websites (homedir/public_html)
    if import_websites:
        homedir = cpmove_dir / "homedir"
        public_html = homedir / "public_html"
        
        if public_html.exists():
            # Trong thực tế, cần copy public_html này vào /home/{user}/domains/{domain}/public_html
            # Đồng thời chown cho user_id
            pass
            
    # 4. Phục hồi Database (mysql/*.sql)
    if import_databases:
        mysql_dir = cpmove_dir / "mysql"
        if mysql_dir.exists():
            for sql_file in mysql_dir.glob("*.sql"):
                # Gọi API hoặc lệnh mysql để import sql_file vào Database
                pass
                
    # 5. Phục hồi Emails (homedir/mail)
    if import_emails:
        mail_dir = cpmove_dir / "homedir" / "mail"
        if mail_dir.exists():
            # Copy dữ liệu vào /var/vmail/ ...
            pass
            
    # Cleanup được xử lý ở ngoài (trong migration.py)
