import os
import subprocess
from pathlib import Path

def restore_to_user(archive_path: str, user_id: int, import_websites: bool, import_databases: bool, import_emails: bool):
    """
    Trích xuất dữ liệu từ file backup Plesk (.tar/.zip) vào tài khoản SPanel.
    """
    archive_path = Path(archive_path)
    if not archive_path.exists():
        raise FileNotFoundError(f"Không tìm thấy file backup tại {archive_path}")

    # Tạo thư mục giải nén
    extract_dir = archive_path.parent / "extracted"
    os.makedirs(extract_dir, exist_ok=True)
    
    # Plesk thường dùng zip hoặc tar. Phân biệt bằng đuôi file:
    if str(archive_path).endswith('.zip'):
        subprocess.run(["unzip", "-q", str(archive_path), "-d", str(extract_dir)], check=True)
    else:
        subprocess.run(["tar", "-xzf", str(archive_path), "-C", str(extract_dir)], check=True)
        
    # Phục hồi Website (vhosts)
    if import_websites:
        vhosts_dir = extract_dir / "vhosts"
        if vhosts_dir.exists():
            pass
            
    # Phục hồi Database (databases)
    if import_databases:
        db_dir = extract_dir / "databases"
        if db_dir.exists():
            pass
