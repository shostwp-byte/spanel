import os
import secrets
import string
from datetime import datetime

from app.core.database import SessionLocal, run_migrations
from app.core.security import hash_password
from app.models.entities import User
from app.services import site_users


def _admin_password() -> str:
    if password := os.getenv("SPANEL_ADMIN_PASSWORD") or os.getenv("spanel_ADMIN_PASSWORD"):
        if len(password) < 12:
            raise ValueError("spanel_ADMIN_PASSWORD must be at least 12 characters")
        if any(char in password for char in (":", "\r", "\n", "\x00")):
            raise ValueError("spanel_ADMIN_PASSWORD cannot contain ':', newlines, or NUL characters")
        return password
    alphabet = string.ascii_letters + string.digits + "!@#%^*_+-"
    return "".join(secrets.choice(alphabet) for _ in range(24))


def seed_admin():
    run_migrations()
    db = SessionLocal()
    try:
        password = None
        created = False
        if not db.query(User).filter(User.username == "admin").first():
            password = _admin_password()
            admin = User(
                username="admin",
                email="admin@example.com",
                hashed_password=hash_password(password),
                role="admin",
                website_limit=999,
                storage_limit_mb=102400,
                # Its own SFTP secret from the first boot, set below.
                sftp_password_set_at=datetime.utcnow(),
            )
            db.add(admin)
            db.commit()
            created = True
            print(f"Created admin user: admin / {password}")
        else:
            print("Admin user already exists")
        # The admin's SFTP login gets its own secret rather than the panel
        # password: sshd offers password authentication on port 22, and that is
        # not somewhere the panel password belongs.
        #
        # The value is deliberately never printed. This runs inside install.sh,
        # whose output lands in terminal scrollback, CI logs and support
        # tickets - CodeQL flags it as clear-text logging of a credential and
        # is right to. The admin sets one they can see from the panel, which is
        # a click away and is the only place it is ever readable.
        sftp_password = site_users.generate_login_password() if created else None
        site_users.ensure_panel_user("admin", sftp_password)
        if created:
            print("Admin SFTP login: admin (set its password in the panel, "
                  "under SFTP accounts - it is not the panel password)")
    finally:
        db.close()


if __name__ == "__main__":
    seed_admin()
