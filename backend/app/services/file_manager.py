import os
import secrets
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

from app.models.entities import Website
from app.services import malware_queue
from app.services import malware_scan
from app.services import site_users
from app.services import storage_quota
from app.services.shell import shell


def _env_int(name: str, default: int) -> Optional[int]:
    """Parse an integer environment variable.

    Returns None if the variable is not set or set to empty string,
    otherwise returns the parsed integer (clamped to >= 0).
    Use None return value to distinguish "not set" from "set to 0".
    """
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


MAX_TEXT_FILE_BYTES = 2 * 1024 * 1024
MAX_ARCHIVE_ITEMS = _env_int("spanel_MAX_ARCHIVE_ITEMS", 1_000_000)
MAX_ARCHIVE_UNCOMPRESSED_BYTES = _env_int("spanel_MAX_ARCHIVE_UNCOMPRESSED_BYTES", 100 * 1024 * 1024 * 1024)
# Website ownership is the permission boundary. End users must be able to deploy
# real web sources, including PHP, .htaccess, .env, and wp-config.php.
BLOCKED_WRITE_SUFFIXES: set[str] = set()
BLOCKED_WRITE_NAMES: set[str] = set()
SENSITIVE_READ_NAMES: set[str] = set()


def _clear_fastcgi_cache() -> None:
    try:
        shell.privileged("fastcgi-cache-clear", check=False)
    except RuntimeError:
        # Local development and tests do not install the privileged helper.
        pass


def _safe_upload_name(filename: str) -> str:
    name = (filename or "").replace("\\", "/").split("/")[-1].strip()
    if not name or name in {".", ".."} or "\x00" in name:
        raise ValueError("Invalid filename")
    return name


def _safe_entry_name(name: str) -> str:
    safe_name = _safe_upload_name(name)
    if safe_name in {"/", "\\"}:
        raise ValueError("Invalid filename")
    return safe_name


def _assert_write_allowed(path: Path, action: str, allow_executable: bool = False) -> None:
    if allow_executable:
        return
    name_lower = path.name.lower()
    if name_lower in BLOCKED_WRITE_NAMES:
        raise ValueError(f"{action} {path.name} requires admin permissions")
    if path.suffix.lower() in BLOCKED_WRITE_SUFFIXES:
        raise ValueError(f"{action} executable files requires admin permissions")


def _assert_sensitive_read_allowed(path: Path, action: str, allow_sensitive: bool = False) -> None:
    if allow_sensitive:
        return
    if path.name.lower() in SENSITIVE_READ_NAMES:
        raise ValueError(f"{action} {path.name} requires admin permissions")


def _assert_tree_write_allowed(
    path: Path,
    action: str,
    allow_executable: bool = False,
    allow_symlinks: bool = False,
) -> None:
    if path.is_symlink():
        if allow_symlinks:
            _assert_write_allowed(path, action, allow_executable)
            return
        raise ValueError("Symlinks are not allowed")
    if path.is_dir():
        try:
            items = list(path.rglob("*"))
        except PermissionError:
            # spanel process may not have read access into site-user directories.
            # The actual delete is executed via the privileged helper which runs
            # as the site user, so a PermissionError here only means we cannot
            # pre-validate — not that the operation is unsafe.
            return
        for item in items:
            try:
                is_sym = item.is_symlink()
            except OSError:
                continue
            if is_sym:
                if allow_symlinks:
                    continue
                raise ValueError("Symlinks are not allowed")
            try:
                is_file = item.is_file()
            except OSError:
                continue
            if is_file:
                _assert_write_allowed(item, action, allow_executable)
        return
    _assert_write_allowed(path, action, allow_executable)


def _assert_tree_read_allowed(path: Path, action: str, allow_sensitive: bool = False) -> None:
    if path.is_symlink():
        raise ValueError("Symlinks are not allowed")
    if path.is_dir():
        try:
            items = list(path.rglob("*"))
        except PermissionError:
            return
        for item in items:
            try:
                is_sym = item.is_symlink()
            except OSError:
                continue
            if is_sym:
                raise ValueError("Symlinks are not allowed")
            try:
                is_file = item.is_file()
            except OSError:
                continue
            if is_file:
                _assert_sensitive_read_allowed(item, action, allow_sensitive)
        return
    _assert_sensitive_read_allowed(path, action, allow_sensitive)


def _clean_relative_path(path: str) -> str:
    if "\x00" in (path or ""):
        raise ValueError("Invalid path")
    normalized = (path or "").replace("\\", "/")
    if normalized.startswith("/") or ":" in normalized.split("/", 1)[0]:
        raise ValueError("Path escapes website root")
    parts = []
    for part in normalized.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("Path escapes website root")
        parts.append(part)
    return "/".join(parts)


def _safe_path(website: Website, relative_path: str, allow_leaf_symlink: bool = False) -> Path:
    """Resolve a relative path under the website root and reject anything that
    escapes via .., absolute paths, or symlinks.

    Deletion can allow a symlink as the final component so Laravel-style links
    such as public/storage can be unlinked without following them.
    """
    if "\x00" in (relative_path or ""):
        raise ValueError("Invalid path")
    root = Path(website.root_path).resolve()
    if not root.exists():
        raise ValueError("Website root not found")
    # Strip leading slashes and disallow absolute paths
    rel = (relative_path or "").lstrip("/").lstrip("\\")
    candidate = root / rel
    # Walk each component and reject symlinks anywhere along the way.
    accumulated = root
    if candidate != root:
        try:
            parts = candidate.relative_to(root).parts
        except ValueError:
            # candidate is not under root (e.g. via .. before resolve)
            parts = None
        if parts is None:
            # Fallback: resolve and verify containment.
            resolved = candidate.resolve()
            if root != resolved and root not in resolved.parents:
                raise ValueError("Path escapes website root")
            return resolved
        for index, part in enumerate(parts):
            if part in ("..", "."):
                raise ValueError("Path escapes website root")
            accumulated = accumulated / part
            if accumulated.is_symlink():
                if allow_leaf_symlink and index == len(parts) - 1:
                    return accumulated
                raise ValueError("Symlinks are not allowed")
    target = accumulated.resolve()
    if root != target and root not in target.parents:
        raise ValueError("Path escapes website root")
    return target


def _relative_to_root(website: Website, path: Path) -> str:
    return str(path.relative_to(Path(website.root_path).resolve())).replace("\\", "/")


def _entry_info(website: Website, item: Path) -> Dict:
    item_stat = item.stat()
    return {
        "name": item.name,
        "path": _relative_to_root(website, item),
        "is_dir": item.is_dir(),
        "size": item_stat.st_size,
        "modified": int(item_stat.st_mtime),
        "mode": f"{stat.S_IMODE(item_stat.st_mode):03o}",
    }


def list_files(website: Website, relative_path: str = "") -> List[Dict]:
    target = _safe_path(website, relative_path)
    if not target.exists() or not target.is_dir():
        return []
    items = []
    for item in target.iterdir():
        # Hide symlinks rather than following them.
        if item.is_symlink():
            continue
        try:
            items.append(_entry_info(website, item))
        except (OSError, ValueError):
            continue
    return sorted(items, key=lambda entry: (not entry["is_dir"], entry["name"].lower()))


# What a search will not walk into, and why each one. These are the
# directories that make a grep over a WordPress site take minutes and return
# nothing anybody wanted: dependency trees, caches and the customer's uploads.
SEARCH_SKIP_DIRS = frozenset({
    ".git", ".svn", ".hg", "node_modules", "vendor", "__pycache__",
    ".cache", "cache", "uploads", ".wp-cli", ".npm", ".yarn",
})
# Beyond these a search is not answering a question, it is scanning a disk.
SEARCH_MAX_FILE_BYTES = 512 * 1024
SEARCH_MAX_FILES = 20_000
SEARCH_MAX_TOTAL_BYTES = 200 * 1024 * 1024
SEARCH_MAX_MATCHES = 100


def _looks_binary(chunk: bytes) -> bool:
    """A NUL in the first few KB. Crude, and the same test grep uses."""
    # chunk is bytes, so iterating gives ints; 0 is the NUL byte.
    return 0 in chunk


def search_text(
    website: Website,
    needle: str,
    *,
    relative_path: str = "",
    max_matches: int = SEARCH_MAX_MATCHES,
) -> Dict:
    """Find a string in a website's files, and stop before it becomes a scan.

    Written for an assistant looking for where something is configured or
    which file mentions a domain. Every limit here exists because without it
    one question could read the whole disk: a WordPress site with its uploads
    and its vendor tree is hundreds of thousands of files and tens of
    gigabytes, and the answer is never in any of them.

    Reports what it skipped rather than silently returning less, so the caller
    can tell "not found" from "gave up".
    """
    needle = (needle or "").strip()
    if not needle:
        raise ValueError("Nothing to search for")
    if len(needle) > 200:
        raise ValueError("Search text is too long")

    base = _safe_path(website, relative_path)
    if not base.is_dir():
        raise ValueError("Not a directory")

    wanted = max(1, min(int(max_matches or SEARCH_MAX_MATCHES), SEARCH_MAX_MATCHES))
    matches: List[Dict] = []
    files_read = 0
    bytes_read = 0
    skipped_binary = 0
    skipped_large = 0
    stopped = ""

    for current, directories, filenames in os.walk(base, followlinks=False):
        # Prune in place: os.walk will not descend into what we remove here,
        # which is the difference between skipping node_modules and walking it
        # and discarding the results.
        directories[:] = sorted(
            name for name in directories
            if name not in SEARCH_SKIP_DIRS and not os.path.islink(os.path.join(current, name))
        )
        for filename in sorted(filenames):
            if len(matches) >= wanted:
                stopped = f"Stopped at {wanted} matches."
                break
            if files_read >= SEARCH_MAX_FILES:
                stopped = f"Stopped after looking at {SEARCH_MAX_FILES} files."
                break
            if bytes_read >= SEARCH_MAX_TOTAL_BYTES:
                stopped = "Stopped after reading 200 MB."
                break

            item = Path(current) / filename
            if item.is_symlink() or not item.is_file():
                continue
            try:
                size = item.stat().st_size
            except OSError:
                continue
            if size > SEARCH_MAX_FILE_BYTES:
                skipped_large += 1
                continue

            try:
                raw = item.read_bytes()
            except OSError:
                continue
            files_read += 1
            bytes_read += len(raw)
            if _looks_binary(raw[:8192]):
                skipped_binary += 1
                continue

            text = raw.decode("utf-8", errors="replace")
            if needle not in text:
                continue
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line:
                    matches.append({
                        "path": _relative_to_root(website, item),
                        "line": number,
                        # One line, trimmed: an assistant needs the location
                        # and enough context to recognise it, then it reads
                        # the file if it wants more.
                        "text": line.strip()[:300],
                    })
                    if len(matches) >= wanted:
                        break
        if stopped:
            break

    return {
        "searched_in": relative_path or "/",
        "needle": needle,
        "matches": matches,
        "files_read": files_read,
        "skipped_binary": skipped_binary,
        "skipped_too_large": skipped_large,
        "note": stopped,
        "complete": not stopped,
    }


def make_directory(website: Website, parent_path: str, name: str) -> str:
    parent = _safe_path(website, parent_path or "")
    if not parent.exists() or not parent.is_dir():
        raise ValueError("Parent directory not found")
    target = parent / _safe_entry_name(name)
    if target.exists():
        raise ValueError("File or folder already exists")
    if website.linux_user:
        root = Path(website.root_path).resolve()
        _run_as_site_user(
            website,
            root,
            "mkdir",
            ["--", _helper_relative_path(website, target)],
            ["mkdir", "--", str(target)],
        )
    else:
        target.mkdir()
    site_users.fix_site_path(str(target), website.linux_user, check=True)
    _clear_fastcgi_cache()
    return str(target)


def rename_entry(website: Website, relative_path: str, new_name: str, allow_executable: bool = False) -> str:
    source = _safe_path(website, relative_path)
    if not source.exists():
        raise ValueError("File or folder not found")
    if source == Path(website.root_path).resolve():
        raise ValueError("Cannot rename website root")
    target = source.parent / _safe_entry_name(new_name)
    if target.exists():
        raise ValueError("Target already exists")
    _assert_tree_write_allowed(source, "Renaming", allow_executable)
    _assert_write_allowed(target, "Renaming", allow_executable)
    if website.linux_user:
        root = Path(website.root_path).resolve()
        _run_as_site_user(
            website,
            root,
            "mv",
            [
                "--",
                _helper_relative_path(website, source),
                _helper_relative_path(website, target),
            ],
            ["mv", "--", str(source), str(target)],
        )
    else:
        source.rename(target)
    site_users.fix_site_path(str(target), website.linux_user, check=True)
    _clear_fastcgi_cache()
    return str(target)


def chmod_entry(website: Website, relative_path: str, mode: str) -> str:
    target = _safe_path(website, relative_path)
    if not target.exists():
        raise ValueError("File or folder not found")
    if target.is_symlink():
        raise ValueError("Symlinks are not allowed")
    clean_mode = (mode or "").strip()
    if len(clean_mode) not in {3, 4} or any(char not in "01234567" for char in clean_mode):
        raise ValueError("Mode must be octal, for example 644 or 755")
    numeric_mode = int(clean_mode, 8)
    if numeric_mode > 0o7777:
        raise ValueError("Mode is out of range")
    # The nine ordinary permission bits are the owner's to set, the same way
    # DirectAdmin and cPanel expose them: executable scripts and 777 upload
    # directories are legitimate, and the website root is already the trust
    # boundary. Only the special bits stay restricted. setgid is allowed on a
    # folder because that is what keeps group inheritance working under a site
    # root; setuid and the sticky bit have no use here and are the classic
    # shape of a planted backdoor.
    if numeric_mode & 0o4000:
        raise ValueError("Mode cannot include the setuid bit")
    if numeric_mode & 0o1000:
        raise ValueError("Mode cannot include the sticky bit")
    if numeric_mode & 0o2000 and not target.is_dir():
        raise ValueError("The setgid bit can only be set on a folder")
    # chmod keeps a directory's setuid/setgid bits for an ordinary numeric mode
    # and only honours the special digit given an extra leading zero, so send
    # the fully explicit form: what the caller asked for is what gets applied.
    explicit_mode = f"0{numeric_mode:04o}"
    if website.linux_user:
        root = Path(website.root_path).resolve()
        # Deliberately not run as the site user: that account is outside the
        # site group, so the kernel would strip setgid from directories and
        # break group inheritance under public_html.
        shell.privileged(
            "site-chmod",
            helper_args=[website.linux_user, str(root), str(target), explicit_mode],
            fallback=["chmod", explicit_mode, str(target)],
        )
        return str(target)
    target.chmod(numeric_mode)
    return str(target)


def read_text_file(website: Website, relative_path: str, allow_sensitive: bool = False) -> str:
    target = _safe_path(website, relative_path)
    if not target.is_file():
        raise ValueError("File not found")
    if target.is_symlink():
        raise ValueError("Symlinks are not allowed")
    if target.name.lower() in SENSITIVE_READ_NAMES and not allow_sensitive:
        raise ValueError(f"Reading {target.name} requires admin permissions")
    if target.stat().st_size > MAX_TEXT_FILE_BYTES:
        raise ValueError("File is too large")
    if website.linux_user:
        root = Path(website.root_path).resolve()
        result = shell.privileged(
            "terminal-exec",
            helper_args=[website.linux_user, str(root), "cat", _helper_relative_path(website, target)],
            fallback=["cat", str(target)],
        )
        return result.stdout
    return target.read_text(encoding="utf-8")


QuotaCheck = Callable[[int, int], None]


def _existing_file_size(path: Path) -> int:
    if not path.exists() or not path.is_file():
        return 0
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _helper_relative_path(website: Website, target: Path) -> str:
    root = Path(website.root_path).resolve()
    return str(target.relative_to(root)).replace("\\", "/") if target != root else "."


def _run_as_site_user(
    website: Website,
    cwd: Path,
    command: str,
    args: List[str],
    fallback: Optional[List[str]] = None,
) -> None:
    if not website.linux_user:
        raise ValueError("Website has no runtime user configured")
    shell.privileged(
        "terminal-exec",
        helper_args=[website.linux_user, str(cwd), command, *args],
        fallback=fallback,
    )


def _staging_directory() -> Path:
    if os.name != "nt":
        return Path("/tmp")
    return Path(tempfile.gettempdir())


def _write_text_as_site_user(website: Website, target: Path, content: str) -> None:
    if not website.linux_user:
        target.write_text(content, encoding="utf-8")
        return
    root = Path(website.root_path).resolve()
    shell.privileged(
        "site-file-write",
        helper_args=[website.linux_user, str(root), _helper_relative_path(website, target)],
        input=content,
        fallback=["tee", str(target)],
    )


def create_text_file(
    website: Website,
    parent_path: str,
    name: str,
    allow_executable: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> str:
    parent = _safe_path(website, parent_path or "")
    if parent.exists() and not parent.is_dir():
        raise ValueError("Parent path is not a directory")
    if parent.is_symlink():
        raise ValueError("Symlinks are not allowed")
    target = parent / _safe_entry_name(name)
    if target.exists():
        raise ValueError("File or folder already exists")
    _assert_write_allowed(target, "Creating", allow_executable)
    if quota_check:
        quota_check(0, 0)
    if website.linux_user:
        _write_text_as_site_user(website, target, "")
    else:
        parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    site_users.fix_site_path(str(target), website.linux_user, check=True)
    _clear_fastcgi_cache()
    return str(target)


def write_text_file(
    website: Website,
    relative_path: str,
    content: str,
    allow_executable: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> str:
    target = _safe_path(website, relative_path)
    _assert_write_allowed(target, "Writing", allow_executable)
    content_size = len(content.encode("utf-8"))
    if content_size > MAX_TEXT_FILE_BYTES:
        raise ValueError("File content is too large")
    if target.exists() and target.is_symlink():
        raise ValueError("Refusing to write through a symlink")
    if quota_check:
        quota_check(content_size, _existing_file_size(target))
    _write_text_as_site_user(website, target, content)
    site_users.fix_site_path(str(target), website.linux_user, check=True)
    _clear_fastcgi_cache()
    return str(target)


def upload_file(
    website: Website,
    directory_path: str,
    filename: str,
    source_file,
    allow_executable: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> str:
    target_dir = _safe_path(website, directory_path or "")
    if target_dir.exists() and not target_dir.is_dir():
        raise ValueError("Upload target is not a directory")
    if target_dir.is_symlink():
        raise ValueError("Symlinks are not allowed")
    safe_name = _safe_upload_name(filename)
    target = target_dir / safe_name
    if target.exists() and target.is_symlink():
        raise ValueError("Refusing to overwrite a symlink")
    _assert_write_allowed(target, "Uploading", allow_executable)
    if quota_check:
        upload_size = storage_quota.source_file_size(source_file)
        quota_check(upload_size or 0, _existing_file_size(target))
    if website.linux_user:
        root = Path(website.root_path).resolve()
        staged_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w+b",
                prefix="spanel-upload-",
                suffix=Path(safe_name).suffix,
                dir=str(_staging_directory()),
                delete=False,
            ) as output:
                staged_path = Path(output.name)
                shutil.copyfileobj(source_file, output, length=1024 * 1024)
                output.flush()
                # No fsync. This file is renamed into the site a moment later
                # and never read again, so forcing it to disk bought nothing
                # and cost a write barrier on every upload.
            scan_res = malware_scan.scan_upload_realtime(
                str(staged_path),
                domain=website.domain,
                auto_quarantine=True,
            )
            if scan_res.get("status") == "infected":
                raise ValueError(
                    f"Upload blocked by Real-Time Malware Shield ({scan_res.get('signature')})"
                )
            # One privileged call. It renames the staged file into place, fixes
            # ownership and clears the FastCGI cache; it used to be three, and
            # each sudo + helper start-up costs about 0.2s on a loaded VPS -
            # most of what a small upload took.
            shell.privileged(
                "site-file-install",
                helper_args=[
                    website.linux_user,
                    str(root),
                    _helper_relative_path(website, target),
                    str(staged_path),
                ],
                fallback=["cp", "--", str(staged_path), str(target)],
            )
        finally:
            if staged_path is not None:
                staged_path.unlink(missing_ok=True)
    else:
        target_dir.mkdir(parents=True, exist_ok=True)
        temp_path = target_dir / f".upload-{secrets.token_hex(8)}-{safe_name}"
        try:
            with temp_path.open("wb") as output:
                shutil.copyfileobj(source_file, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            scan_res = malware_scan.scan_upload_realtime(
                str(temp_path),
                domain=website.domain,
                auto_quarantine=True,
            )
            if scan_res.get("status") == "infected":
                raise ValueError(
                    f"Upload blocked by Real-Time Malware Shield ({scan_res.get('signature')})"
                )
            temp_path.replace(target)
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise
        # Only this branch still needs them; site-file-install does both itself.
        site_users.fix_site_path(str(target), website.linux_user, check=True)
        _clear_fastcgi_cache()
    # Upload is upload. The file is in place and the request is finished; the
    # virus scan runs next, out of the caller's way, and quarantines the file
    # if it finds something.
    #
    # Scanning inline made a 20 MB upload take three and a half seconds - and,
    # because clamd refuses a stream over 25 MB, did nothing at all for
    # anything larger while still reporting success. Slow where it worked,
    # silent where it did not.
    if malware_queue.scan_on_upload_enabled():
        malware_queue.enqueue(website.id, str(target), filename)
    return str(target)


def delete_file(website: Website, relative_path: str, allow_executable: bool = False) -> str:
    target = _safe_path(website, relative_path, allow_leaf_symlink=True)
    root = Path(website.root_path).resolve()
    if target.is_dir() and not target.is_symlink():
        raise ValueError("Cannot delete directory")
    _assert_write_allowed(target, "Deleting", allow_executable)
    if website.linux_user:
        shell.privileged(
            "rm-site",
            helper_args=[website.linux_user, str(root), str(target)],
            fallback=["rm", "-f", "--", str(target)],
        )
    else:
        target.unlink(missing_ok=True)
    _clear_fastcgi_cache()
    return str(target)


def delete_entries(website: Website, paths: Iterable[str], allow_executable: bool = False) -> List[str]:
    deleted = []
    root = Path(website.root_path).resolve()
    targets = []
    for relative_path in paths:
        target = _safe_path(website, relative_path, allow_leaf_symlink=True)
        if not target.exists() and not target.is_symlink():
            raise ValueError("File or folder not found")
        if target == root:
            raise ValueError("Cannot delete website root")
        _assert_tree_write_allowed(target, "Deleting", allow_executable, allow_symlinks=True)
        targets.append(target)

    deleted_dirs = []
    for target in targets:
        if any(parent in target.parents for parent in deleted_dirs):
            continue
        if target.is_dir():
            if website.linux_user:
                shell.privileged(
                    "rm-site",
                    helper_args=[website.linux_user, str(root), str(target)],
                    fallback=["rm", "-rf", "--", str(target)],
                )
            else:
                shutil.rmtree(target)
            deleted_dirs.append(target)
        else:
            if website.linux_user:
                shell.privileged(
                    "rm-site",
                    helper_args=[website.linux_user, str(root), str(target)],
                    fallback=["rm", "-f", "--", str(target)],
                )
            else:
                target.unlink(missing_ok=True)
        deleted.append(str(target))
    _clear_fastcgi_cache()
    return deleted


def _transfer_sources(website: Website, paths: Iterable[str], action: str, allow_executable: bool, allow_sensitive: bool) -> list[Path]:
    root = Path(website.root_path).resolve()
    sources = []
    for relative_path in paths:
        source = _safe_path(website, relative_path)
        if not source.exists():
            raise ValueError("File or folder not found")
        if source == root:
            raise ValueError(f"Cannot {action.lower()} website root")
        if source.is_symlink():
            raise ValueError("Symlinks are not allowed")
        if action == "Copying":
            _assert_tree_read_allowed(source, action, allow_sensitive)
            _assert_tree_write_allowed(source, action, allow_executable)
        else:
            _assert_tree_write_allowed(source, action, allow_executable)
        sources.append(source)
    if not sources:
        raise ValueError("Select files or folders first")

    top_level = []
    for source in sorted(dict.fromkeys(sources), key=lambda item: len(item.parts)):
        if any(parent.is_dir() and parent in source.parents for parent in top_level):
            continue
        top_level.append(source)
    return top_level


def _transfer_destination(website: Website, destination_path: str) -> Path:
    destination = _safe_path(website, destination_path or "")
    if not destination.exists() or not destination.is_dir():
        raise ValueError("Destination folder not found")
    if destination.is_symlink():
        raise ValueError("Symlinks are not allowed")
    return destination


def _assert_transfer_target(source: Path, destination: Path, target: Path, action: str) -> None:
    if source.is_dir() and (destination == source or source in destination.parents):
        raise ValueError(f"Cannot {action.lower()} a folder into itself")
    if target.exists() or target.is_symlink():
        raise ValueError(f"Target already exists: {target.name}")


def copy_entries(
    website: Website,
    paths: Iterable[str],
    destination_path: str,
    allow_executable: bool = False,
    allow_sensitive: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> List[str]:
    sources = _transfer_sources(website, paths, "Copying", allow_executable, allow_sensitive)
    destination = _transfer_destination(website, destination_path)
    targets = [destination / source.name for source in sources]
    for source, target in zip(sources, targets):
        _assert_transfer_target(source, destination, target, "copy")
        _assert_write_allowed(target, "Copying", allow_executable)
    if quota_check:
        quota_check(_total_size(sources), 0)

    copied = []
    for source, target in zip(sources, targets):
        if website.linux_user:
            root = Path(website.root_path).resolve()
            _run_as_site_user(
                website,
                root,
                "cp",
                [
                    "-R",
                    "--",
                    _helper_relative_path(website, source),
                    _helper_relative_path(website, target),
                ],
                ["cp", "-R", "--", str(source), str(target)],
            )
        elif source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, target)
        site_users.fix_site_path(str(target), website.linux_user, check=True)
        copied.append(str(target))
    _clear_fastcgi_cache()
    return copied


def move_entries(
    website: Website,
    paths: Iterable[str],
    destination_path: str,
    allow_executable: bool = False,
    allow_sensitive: bool = False,
) -> List[str]:
    sources = _transfer_sources(website, paths, "Moving", allow_executable, allow_sensitive)
    destination = _transfer_destination(website, destination_path)
    targets = [destination / source.name for source in sources]
    for source, target in zip(sources, targets):
        _assert_transfer_target(source, destination, target, "move")
        _assert_write_allowed(target, "Moving", allow_executable)

    moved = []
    for source, target in zip(sources, targets):
        if website.linux_user:
            root = Path(website.root_path).resolve()
            _run_as_site_user(
                website,
                root,
                "mv",
                [
                    "--",
                    _helper_relative_path(website, source),
                    _helper_relative_path(website, target),
                ],
                ["mv", "--", str(source), str(target)],
            )
        else:
            shutil.move(str(source), str(target))
        site_users.fix_site_path(str(target), website.linux_user, check=True)
        moved.append(str(target))
    _clear_fastcgi_cache()
    return moved


def _total_size(paths: Iterable[Path]) -> int:
    total = 0
    for path in paths:
        if path.is_dir():
            total += storage_quota.path_usage_bytes(path)
        elif path.is_file():
            total += path.stat().st_size
    return total


def _archive_output_name(output_name: str, archive_format: str) -> str:
    name = _safe_entry_name(output_name or f"archive-{int(time.time())}")
    if archive_format == "zip":
        return name if name.lower().endswith(".zip") else f"{name}.zip"
    if archive_format == "tar.gz":
        return name if name.lower().endswith(".tar.gz") else f"{name}.tar.gz"
    raise ValueError("Unsupported archive format")


def _archive_arcname(base: Path, path: Path) -> str:
    try:
        relative = path.relative_to(base)
    except ValueError as exc:
        raise ValueError("Archive items must be inside the current folder") from exc
    arcname = str(relative).replace("\\", "/")
    if not arcname:
        raise ValueError("Cannot archive the current folder into itself")
    return arcname


def archive_entries(
    website: Website,
    base_path: str,
    paths: Iterable[str],
    output_name: str,
    archive_format: str = "zip",
    allow_sensitive: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> str:
    base = _safe_path(website, base_path or "")
    if not base.exists() or not base.is_dir():
        raise ValueError("Archive directory not found")
    selected = [_safe_path(website, path) for path in paths]
    if not selected:
        raise ValueError("Select files or folders to archive")
    for path in selected:
        if not path.exists():
            raise ValueError("File or folder not found")
        if path.is_symlink():
            raise ValueError("Symlinks are not allowed")
        _assert_tree_read_allowed(path, "Archiving", allow_sensitive)
        _archive_arcname(base, path)
    output_path = base / _archive_output_name(output_name, archive_format)
    if output_path.exists():
        raise ValueError("Archive output already exists")
    for path in selected:
        if path == output_path or (path.is_dir() and path in output_path.parents):
            raise ValueError("Archive output cannot be inside a selected folder")
    if quota_check:
        quota_check(_total_size(selected), 0)
    if website.linux_user:
        archive_items = [_archive_arcname(base, path) for path in selected]
        if archive_format == "zip":
            _run_as_site_user(
                website,
                base,
                "zip",
                ["-r", output_path.name, "--", *archive_items],
            )
        else:
            _run_as_site_user(
                website,
                base,
                "tar",
                ["-czf", output_path.name, "--", *archive_items],
            )
    elif archive_format == "zip":
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in selected:
                if path.is_dir():
                    archive.writestr(f"{_archive_arcname(base, path).rstrip('/')}/", b"")
                    for item in path.rglob("*"):
                        if item.is_symlink():
                            continue
                        if item.is_dir():
                            archive.writestr(f"{_archive_arcname(base, item).rstrip('/')}/", b"")
                            continue
                        if item.is_file():
                            archive.write(item, arcname=_archive_arcname(base, item))
                elif path.is_file():
                    archive.write(path, arcname=_archive_arcname(base, path))
    else:
        with tarfile.open(output_path, "w:gz") as archive:
            for path in selected:
                archive.add(
                    path,
                    arcname=_archive_arcname(base, path),
                    recursive=True,
                    filter=lambda member: None if (member.issym() or member.islnk() or member.isdev()) else member,
                )
    site_users.fix_site_path(str(output_path), website.linux_user, check=True)
    _clear_fastcgi_cache()
    return str(output_path)


def _validate_archive_destination(base: Path, member_name: str) -> Path:
    clean = _clean_relative_path(member_name)
    if not clean:
        raise ValueError("Invalid archive member path")
    target = (base / clean).resolve()
    if base != target and base not in target.parents:
        raise ValueError("Archive contains unsafe paths")
    return target


def _is_source_archive_target(target: Path, archive_file: Path) -> bool:
    return target.resolve() == archive_file.resolve()


def _get_implied_dirs(archive: zipfile.ZipFile) -> set[str]:
    implied = set()
    for info in archive.infolist():
        normalized = info.filename.replace('\\', '/')
        parts = normalized.split('/')
        for i in range(1, len(parts)):
            implied.add('/'.join(parts[:i]))
    return implied


def _zip_info_is_dir(info: zipfile.ZipInfo, implied_dirs: Optional[set[str]] = None) -> bool:
    """Return True if the ZIP entry is a directory.

    Some archivers (e.g. zip on Linux) write directory entries *without* a
    trailing slash, so `ZipInfo.is_dir()` returns False even though the
    external_attr mode bits say S_ISDIR.  We check both to catch those.
    """
    normalized = info.filename.replace('\\', '/')
    if info.is_dir() or normalized.endswith('/'):
        return True
    mode = (info.external_attr >> 16) & 0o170000
    if bool(stat.S_ISDIR(mode)) and info.file_size == 0:
        return True
    if implied_dirs and info.file_size == 0 and normalized.rstrip('/') in implied_dirs:
        return True
    return False


def _zip_uncompressed_size(
    archive: zipfile.ZipFile,
    destination: Path,
    archive_file: Path,
    implied_dirs: Optional[set[str]] = None,
    allow_executable: bool = False,
) -> int:
    total = 0
    for index, info in enumerate(archive.infolist(), start=1):
        if MAX_ARCHIVE_ITEMS is not None and index > MAX_ARCHIVE_ITEMS:
            raise ValueError(f"Archive has too many files (limit {MAX_ARCHIVE_ITEMS})")
        mode = (info.external_attr >> 16) & 0o170000
        if stat.S_ISLNK(mode):
            raise ValueError("Archive symlinks are not allowed")
        target = _validate_archive_destination(destination, info.filename)
        if _is_source_archive_target(target, archive_file):
            continue
        if target.exists() and target.is_symlink():
            raise ValueError("Refusing to overwrite a symlink")
        if _zip_info_is_dir(info, implied_dirs):
            if target.exists() and not target.is_dir():
                try:
                    if target.stat().st_size == 0:
                        pass # allow overwriting empty file
                    else:
                        raise ValueError("Archive directory conflicts with an existing file")
                except OSError:
                    raise ValueError("Archive directory conflicts with an existing file")
            continue
        if target.exists() and target.is_dir():
            raise ValueError("Archive file conflicts with an existing directory")
        _assert_write_allowed(target, "Extracting", allow_executable)
        total += info.file_size
        if MAX_ARCHIVE_UNCOMPRESSED_BYTES is not None and total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("Archive is too large")
    return total


def _tar_uncompressed_size(
    archive: tarfile.TarFile,
    destination: Path,
    archive_file: Path,
    allow_executable: bool = False,
) -> int:
    total = 0
    for index, member in enumerate(archive, start=1):
        if MAX_ARCHIVE_ITEMS is not None and index > MAX_ARCHIVE_ITEMS:
            raise ValueError(f"Archive has too many files (limit {MAX_ARCHIVE_ITEMS})")
        if member.issym() or member.islnk() or member.isdev():
            raise ValueError("Archive links and devices are not allowed")
        target = _validate_archive_destination(destination, member.name)
        if _is_source_archive_target(target, archive_file):
            continue
        if target.exists() and target.is_symlink():
            raise ValueError("Refusing to overwrite a symlink")
        if member.isdir():
            if target.exists() and not target.is_dir():
                raise ValueError("Archive directory conflicts with an existing file")
            continue
        if not member.isfile():
            raise ValueError("Archive contains unsupported entries")
        if target.exists() and target.is_dir():
            raise ValueError("Archive file conflicts with an existing directory")
        _assert_write_allowed(target, "Extracting", allow_executable)
        total += member.size
        if MAX_ARCHIVE_UNCOMPRESSED_BYTES is not None and total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError("Archive is too large")
    return total


def _extract_zip_archive(archive: zipfile.ZipFile, destination: Path, archive_file: Path, implied_dirs: Optional[set[str]] = None) -> None:
    for info in archive.infolist():
        target = _validate_archive_destination(destination, info.filename)
        if _is_source_archive_target(target, archive_file):
            continue
        if _zip_info_is_dir(info, implied_dirs):
            if target.exists() and not target.is_dir():
                try:
                    target.unlink()
                except OSError:
                    pass
            target.mkdir(parents=True, exist_ok=True)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except (FileExistsError, NotADirectoryError):
            # If the parent exists as an empty file, we unlink and retry (useful for broken previous extractions or messy zips)
            for p in reversed(target.parents):
                if p == destination or p in destination.parents:
                    break
                if p.exists() and not p.is_dir():
                    try:
                        if p.stat().st_size == 0:
                            p.unlink()
                            p.mkdir(parents=True, exist_ok=True)
                    except OSError:
                        pass
            target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        except RuntimeError as exc:
            raise ValueError("Archive entry cannot be extracted") from exc


def _extract_tar_archive(archive: tarfile.TarFile, destination: Path, archive_file: Path) -> None:
    for member in archive:
        target = _validate_archive_destination(destination, member.name)
        if _is_source_archive_target(target, archive_file):
            continue
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        source = archive.extractfile(member)
        if source is None:
            raise ValueError("Archive entry cannot be extracted")
        target.parent.mkdir(parents=True, exist_ok=True)
        with source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def extract_archive(
    website: Website,
    archive_path: str,
    destination_path: str = "",
    allow_executable: bool = False,
    quota_check: Optional[QuotaCheck] = None,
) -> str:
    archive_file = _safe_path(website, archive_path)
    if not archive_file.exists() or not archive_file.is_file():
        raise ValueError("Archive not found")
    if archive_file.is_symlink():
        raise ValueError("Symlinks are not allowed")
    destination = _safe_path(website, destination_path or str(Path(archive_path).parent))
    if not destination.exists() or not destination.is_dir():
        raise ValueError("Extract destination not found")

    suffix = archive_file.name.lower()
    archive_kind = ""
    incoming = 0
    if suffix.endswith(".zip"):
        archive_kind = "zip"
        with zipfile.ZipFile(archive_file) as archive:
            implied_dirs = _get_implied_dirs(archive)
            incoming = _zip_uncompressed_size(archive, destination, archive_file, implied_dirs, allow_executable)
            if quota_check:
                quota_check(incoming, 0)
    elif suffix.endswith(".tar.gz") or suffix.endswith(".tgz"):
        archive_kind = "tar.gz"
        with tarfile.open(archive_file, "r:gz") as archive:
            incoming = _tar_uncompressed_size(archive, destination, archive_file, allow_executable)
            if quota_check:
                quota_check(incoming, 0)
    else:
        raise ValueError("Only .zip, .tar.gz, and .tgz archives can be extracted")
    if website.linux_user:
        max_items = 0 if MAX_ARCHIVE_ITEMS is None else MAX_ARCHIVE_ITEMS
        max_bytes = 0 if MAX_ARCHIVE_UNCOMPRESSED_BYTES is None else MAX_ARCHIVE_UNCOMPRESSED_BYTES
        shell.privileged(
            "site-archive-extract",
            helper_args=[
                website.linux_user,
                str(Path(website.root_path).resolve()),
                _helper_relative_path(website, archive_file),
                _helper_relative_path(website, destination),
                archive_kind,
                str(max_items),
                str(max_bytes),
            ],
        )
    elif archive_kind == "zip":
        with zipfile.ZipFile(archive_file) as archive:
            _extract_zip_archive(archive, destination, archive_file, _get_implied_dirs(archive))
    else:
        with tarfile.open(archive_file, "r:gz") as archive:
            _extract_tar_archive(archive, destination, archive_file)
    site_users.fix_site_path(str(destination), website.linux_user, check=True)
    # Real-Time YARA + Heuristic scan on all files inside destination after extraction
    infected_findings: list[str] = []
    for root_dir, _, filenames in os.walk(destination):
        for fname in filenames:
            candidate_file = Path(root_dir) / fname
            if candidate_file.is_symlink() or not candidate_file.is_file():
                continue
            if candidate_file.resolve() == archive_file.resolve():
                continue
            scan_res = malware_scan.scan_upload_realtime(
                str(candidate_file),
                domain=getattr(website, "domain", "") or "",
                auto_quarantine=True,
            )
            if scan_res.get("status") == "infected":
                candidate_file.unlink(missing_ok=True)
                infected_findings.append(f"{fname} ({scan_res.get('signature')})")
    _clear_fastcgi_cache()
    if infected_findings:
        raise ValueError(
            f"Archive extraction blocked by Real-Time Malware Shield: {', '.join(infected_findings[:5])}"
        )
    return str(destination)


def download_file_path(website: Website, relative_path: str, allow_sensitive: bool = False) -> Path:
    target = _safe_path(website, relative_path)
    if not target.exists() or not target.is_file():
        raise ValueError("File not found")
    if target.is_symlink():
        raise ValueError("Symlinks are not allowed")
    _assert_sensitive_read_allowed(target, "Downloading", allow_sensitive)
    return target
