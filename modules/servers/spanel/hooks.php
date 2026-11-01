<?php
/**
 * SPanel WHMCS hooks.
 *
 * Kept intentionally minimal so WHMCS preserves each product's native
 * localized group and product name (e.g. "Managed WordPress Hosting -
 * Doanh Nghiệp" / "WordPress Hosting - Bắt Đầu") in the Client Area
 * without appending technical SPanel package slugs, service IDs, or
 * Linux usernames.
 */

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}
