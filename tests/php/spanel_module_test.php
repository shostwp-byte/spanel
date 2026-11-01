<?php
/**
 * Test harness for the WHMCS server module (no WHMCS, no network).
 *
 *     php tests/php/spanel_module_test.php
 *
 * Exits non-zero on the first failing check. Run by
 * tests/test_api_whmcs_module.py and by CI on PHP 7.4 and 8.3.
 */

require __DIR__ . '/whmcs_stubs.php';
require __DIR__ . '/../../modules/servers/spanel/spanel.php';
require __DIR__ . '/../../modules/servers/spanel/hooks.php';

$failures = 0;
$checks = 0;

function check($condition, $message)
{
    global $failures, $checks;
    $checks++;
    if (!$condition) {
        $failures++;
        fwrite(STDERR, "FAIL: $message\n");
    }
}

/** Scripted SPanel API: [ "METHOD path" => [status, body] ]. */
function fake_api($routes)
{
    $GLOBALS['SENT'] = [];
    $GLOBALS['SPANEL_TRANSPORT'] = function ($method, $url, $headers, $body) use ($routes) {
        $GLOBALS['SENT'][] = compact('method', 'url', 'headers', 'body');
        $parts = parse_url($url);
        $path = $parts['path'] . (isset($parts['query']) ? '?' . $parts['query'] : '');
        $key = $method . ' ' . $path;
        if (!isset($routes[$key])) {
            return ['errno' => 0, 'error' => '', 'status' => 404, 'body' => json_encode(['detail' => "no route $key"])];
        }
        $route = $routes[$key];
        return ['errno' => 0, 'error' => '', 'status' => $route[0], 'body' => json_encode($route[1])];
    };
}

function reset_recorders()
{
    $GLOBALS['LOCAL_API_CALLS'] = [];
    $GLOBALS['MODULE_LOG'] = [];
    $GLOBALS['CAPSULE_FIRST'] = [];
    $GLOBALS['CAPSULE_UPDATES'] = [];
}

function header_value($headers, $name)
{
    foreach ($headers as $line) {
        if (stripos($line, $name . ':') === 0) {
            return trim(substr($line, strlen($name) + 1));
        }
    }
    return null;
}

function params($extra = [])
{
    return array_merge([
        'serviceid' => 42,
        'pid' => 7,
        'serverid' => 3,
        'serverhostname' => 'panel.example.com',
        'serversecure' => 'on',
        'serverport' => '2222',
        'serveraccesshash' => 'bp_secret_token',
        'username' => '',
        'password' => 'whmcs-generated-pass',
        'domain' => 'shop.example.com',
        'configoption1' => '2',
        'configoption2' => 'php',
        'configoption3' => '8.3',
        'configoption4' => '',
        'configoption5' => '',
        'configoption6' => '',
        'configoption7' => '',
        'configoption8' => '',
        'configoptions' => [],
    ], $extra);
}

$base = '/api/provisioning/v1';

// --- metadata ------------------------------------------------------------------
$meta = spanel_MetaData();
check($meta['ServiceSingleSignOnLabel'] === 'Login to SPanel', 'SSO label');
check($meta['DefaultSSLPort'] === '2222' && $meta['DefaultNonSSLPort'] === '2222', 'default ports');
check($meta['ListAccountsProductField'] === 'configoption1', 'list accounts product field');
check(count(spanel_ConfigOptions()) === 8, 'eight product options');

// --- configurable options -------------------------------------------------------
$limits = spanel_limits_from_config_options([
    'Disk Space (GB)' => '10',
    'Bandwidth|Monthly traffic' => '500 GB',
    'Websites' => 'Unlimited',
    'Dedicated IP' => '1',
]);
check($limits === ['storage_limit_mb' => 10240, 'bandwidth_limit_mb' => 512000, 'website_limit' => 0], 'config options mapping: ' . json_encode($limits));
check(spanel_parse_size_mb('1.5 TB') === 1572864, 'TB parsing');
check(spanel_parse_size_mb('abc') === null, 'garbage ignored');
check(spanel_limits_from_config_options('nope') === [], 'non-array options');

// --- create ------------------------------------------------------------------------
reset_recorders();
$GLOBALS['CAPSULE_FIRST']['tblcustomfields'] = ['id' => 99];
fake_api([
    "POST $base/accounts" => [200, [
        'external_id' => 'whmcs:42', 'username' => 'sp42_abcdefgh', 'status' => 'active',
        'service_label' => 'Basic #42', 'sftp_password' => 'Sftp_Once_123',
    ]],
]);
$p = params([
    'configoption6' => 'on',
    'configoption7' => '5',
    'configoptions' => ['Disk Space' => '2048', 'Websites' => '3'],
]);
check(spanel_CreateAccount($p) === 'success', 'create succeeds');
$sent = $GLOBALS['SENT'][0];
$payload = json_decode($sent['body'], true);
check($sent['url'] === "https://panel.example.com:2222$base/accounts", 'create url ' . $sent['url']);
check($payload['external_id'] === 'whmcs:42' && $payload['package_id'] === 2, 'create payload ids');
check($payload['is_reseller'] === true && $payload['reseller_package_id'] === 5, 'reseller package sent');
check($payload['limits'] === ['storage_limit_mb' => 2048, 'website_limit' => 3], 'limits sent: ' . json_encode($payload['limits']));
check(header_value($sent['headers'], 'Authorization') === 'Bearer bp_secret_token', 'bearer header');
check(strpos((string) header_value($sent['headers'], 'Idempotency-Key'), 'whmcs-42-create-') === 0, 'idempotency key on create');
check(preg_match('/^sp42_[a-z0-9]{8}$/', $payload['username']) === 1, 'generated username');
$stored = null;
foreach ($GLOBALS['LOCAL_API_CALLS'] as $call) {
    if (isset($call['values']['customfields'])) {
        $stored = unserialize(base64_decode($call['values']['customfields']));
    }
}
check($stored === [99 => 'Sftp_Once_123'], 'sftp password stored in custom field');
$log = end($GLOBALS['MODULE_LOG']);
check(in_array('Sftp_Once_123', $log['replaceVars'], true), 'sftp password masked in module log');
check(in_array('whmcs-generated-pass', $log['replaceVars'], true), 'account password masked in module log');
check(in_array('bp_secret_token', $log['replaceVars'], true), 'token masked in module log');
// The username is saved before the API call, so a retry reuses it.
check($GLOBALS['LOCAL_API_CALLS'][0]['values']['serviceusername'] === $payload['username'], 'username saved before the call');
check(spanel_provision_username(params(['username' => $payload['username']])) === $payload['username'], 'retry reuses username');
check(spanel_provision_username(params(['username' => 'sp41_abcdefgh'])) !== 'sp41_abcdefgh', 'other service username not reused');

reset_recorders();
fake_api(["POST $base/accounts" => [409, ['detail' => 'Username already exists']]]);
check(spanel_CreateAccount(params()) === 'SPanel API error: Username already exists', 'API error surfaces');
check(spanel_CreateAccount(params(['domain' => '', 'configoption5' => 'on'])) === 'A domain is required when Install WordPress or Auto SSL is enabled', 'domain required for SSL');

// --- password, package, terminate ---------------------------------------------------
reset_recorders();
$GLOBALS['CAPSULE_FIRST']['tblcustomfields'] = ['id' => 99];
fake_api(["PATCH $base/accounts/whmcs%3A42/password" => [200, ['ok' => true, 'sftp_password' => 'Rotated_456', 'sftp_password_rotated' => true]]]);
check(spanel_ChangePassword(params()) === 'success', 'change password');
check(strpos((string) header_value($GLOBALS['SENT'][0]['headers'], 'Idempotency-Key'), 'whmcs-42-password-') === 0, 'idempotency key on password change');
$last = end($GLOBALS['LOCAL_API_CALLS']);
check(unserialize(base64_decode($last['values']['customfields'])) === [99 => 'Rotated_456'], 'rotated sftp password stored');

reset_recorders();
fake_api(["PATCH $base/accounts/whmcs%3A42/package" => [200, ['ok' => true, 'package_id' => 4]]]);
check(spanel_ChangePackage(params(['configoption1' => '4', 'configoptions' => ['Bandwidth (GB)' => '100']])) === 'success', 'change package');
$payload = json_decode($GLOBALS['SENT'][0]['body'], true);
check($payload === ['package_id' => 4, 'limits' => ['bandwidth_limit_mb' => 102400]], 'package payload ' . json_encode($payload));

reset_recorders();
fake_api(["DELETE $base/accounts/whmcs%3A42?backup=true" => [200, ['ok' => true, 'status' => 'terminated']], "DELETE $base/accounts/whmcs%3A42" => [200, ['ok' => true]]]);
check(spanel_TerminateAccount(params(['configoption8' => 'on'])) === 'success', 'terminate with backup');
check(substr($GLOBALS['SENT'][0]['url'], -12) === '?backup=true', 'backup flag sent');
check(spanel_TerminateAccount(params()) === 'success', 'terminate without backup');
check(strpos($GLOBALS['SENT'][1]['url'], 'backup') === false, 'no backup flag by default');

// --- usage ---------------------------------------------------------------------------
reset_recorders();
fake_api(["GET $base/accounts/usage" => [200, [
    ['external_id' => 'whmcs:42', 'storage_used_bytes' => 5 * 1048576, 'storage_limit_bytes' => 100 * 1048576, 'bandwidth_used_bytes' => 7 * 1048576, 'bandwidth_limit_bytes' => 0],
    ['external_id' => 'other:1', 'storage_used_bytes' => 1],
]]]);
check(spanel_UsageUpdate(params()) === 'success', 'usage update');
check(count($GLOBALS['CAPSULE_UPDATES']) === 1, 'only whmcs services updated');
$u = $GLOBALS['CAPSULE_UPDATES'][0]['values'];
check($u['diskusage'] === 5 && $u['disklimit'] === 100 && $u['bwusage'] === 7 && $u['bwlimit'] === 0, 'usage values ' . json_encode($u));

// --- list accounts / user count ---------------------------------------------------------
reset_recorders();
$page1 = ['items' => [['external_id' => 'whmcs:1', 'username' => 'sp1_aaaaaaaa', 'email' => 'a@x', 'domain' => 'a.test', 'package_id' => 2, 'status' => 'active', 'created_at' => '2026-01-02T03:04:05']], 'total' => 2, 'page' => 1, 'per_page' => 500];
$page2 = ['items' => [['external_id' => 'whmcs:2', 'username' => 'sp2_bbbbbbbb', 'package_id' => 3, 'status' => 'suspended']], 'total' => 2, 'page' => 2, 'per_page' => 500];
fake_api([
    "GET $base/accounts?page=1&per_page=500" => [200, $page1],
    "GET $base/accounts?page=2&per_page=500" => [200, $page2],
    "GET $base/accounts?page=1&per_page=1" => [200, ['items' => [], 'total' => 17, 'page' => 1, 'per_page' => 1]],
]);
$list = spanel_ListAccounts(params());
check($list['success'] === true && count($list['accounts']) === 2, 'list accounts paginates');
check($list['accounts'][0]['uniqueIdentifier'] === 'sp1_aaaaaaaa' && $list['accounts'][0]['product'] === '2', 'list row identity');
check($list['accounts'][0]['created'] === '2026-01-02 03:04:05', 'list row date');
check($list['accounts'][1]['status'] === 'Suspended', 'status mapping');
$count = spanel_GetUserCount(params());
check($count === ['success' => true, 'totalAccounts' => 17, 'ownedAccounts' => 17], 'user count');

// --- single sign-on ----------------------------------------------------------------------
reset_recorders();
fake_api([
    "POST $base/accounts/whmcs%3A42/login" => [200, ['login_url' => '/api/auth/sso/abc', 'expires_in' => 300]],
    "POST $base/admin/login" => [403, ['detail' => 'Missing scope: sso:admin']],
]);
$sso = spanel_ServiceSingleSignOn(params());
check($sso === ['success' => true, 'redirectTo' => 'https://panel.example.com:2222/api/auth/sso/abc'], 'service sso ' . json_encode($sso));
$admin = spanel_AdminSingleSignOn(params());
check($admin['success'] === false && strpos($admin['errorMsg'], 'sso:admin') !== false, 'admin sso error explains scope');

// --- custom buttons ----------------------------------------------------------------------
check(array_values(spanel_ClientAreaCustomButtonArray()) === ['ResetSftpPassword', 'CreateBackup'], 'client buttons');
reset_recorders();
fake_api(["POST $base/accounts/whmcs%3A42/sftp-password" => [200, ['ok' => true, 'sftp_password' => 'New_789']]]);
check(strpos(spanel_ResetSftpPassword(params()), 'custom field') !== false, 'reset refused without custom field');
check(count($GLOBALS['SENT']) === 0, 'no reset call without custom field');
$GLOBALS['CAPSULE_FIRST']['tblcustomfields'] = ['id' => 12];
check(spanel_ResetSftpPassword(params()) === 'success', 'reset with custom field');
$last = end($GLOBALS['LOCAL_API_CALLS']);
check(unserialize(base64_decode($last['values']['customfields'])) === [12 => 'New_789'], 'reset password stored');

reset_recorders();
fake_api(["POST $base/accounts/whmcs%3A42/backup" => [200, ['ok' => true, 'queued' => true]]]);
check(spanel_CreateBackup(params()) === 'success', 'create backup');

// --- admin tab ------------------------------------------------------------------------------
reset_recorders();
fake_api([
    "GET $base/accounts/whmcs%3A42" => [200, ['status' => 'active', 'username' => '<b>x</b>', 'domain' => 'a.test', 'package_name' => 'Basic', 'package_id' => 2]],
    "GET $base/accounts/whmcs%3A42/usage" => [200, ['storage_used_bytes' => 1073741824, 'storage_limit_bytes' => 2147483648, 'bandwidth_used_bytes' => 0, 'bandwidth_limit_bytes' => 0, 'website_count' => 1, 'database_count' => 2]],
]);
$tab = spanel_AdminServicesTabFields(params());
check($tab['SPanel username'] === '&lt;b&gt;x&lt;/b&gt;', 'admin tab escapes');
check($tab['Disk usage'] === '1.0 GB / 2.0 GB (50%)', 'disk usage ' . $tab['Disk usage']);
check($tab['Bandwidth (this month)'] === '0 B / unlimited', 'bandwidth usage');

// --- client area -------------------------------------------------------------------------------
$GLOBALS['SENT'] = [];
$area = spanel_ClientArea(params());
check($area['templatefile'] === 'clientarea', 'client area template');
check($area['vars']['loginUrl'] === 'clientarea.php?action=productdetails&id=42&dosinglesignon=1', 'client area uses WHMCS SSO');
check(count($GLOBALS['SENT']) === 0, 'client area makes zero API calls on page view');

// --- TestConnection, port 2222 default, Nginx 404 -> :2222 fallback & HTML cleaning ---
reset_recorders();
fake_api([
    "GET $base/plans" => [200, [['id' => 1, 'name' => 'Starter']]],
]);
$tcEmptyPort = spanel_TestConnection(params(['serverport' => '']));
check($tcEmptyPort === ['success' => true, 'error' => ''], 'TestConnection succeeds with empty serverport');
check($GLOBALS['SENT'][0]['url'] === "https://panel.example.com:2222$base/plans", 'empty serverport defaults to 2222: ' . $GLOBALS['SENT'][0]['url']);

$tcZeroPort = spanel_TestConnection(params(['serverport' => '0']));
check($tcZeroPort['success'] === true && $GLOBALS['SENT'][1]['url'] === "https://panel.example.com:2222$base/plans", 'zero serverport defaults to 2222');

check(
    spanel_base_url(params(['serverhostname' => 'https://panel.example.com:2222/', 'serverport' => '2222'])) === 'https://panel.example.com:2222',
    'inline hostname port is not duplicated'
);
check(
    spanel_base_url(params(['serverhostname' => '', 'serverip' => '2001:db8::1', 'serverport' => ''])) === 'https://[2001:db8::1]:2222',
    'bare IPv6 address is bracketed with default port 2222'
);

// WHMCS serverport=80/443 targets SPanel :2222 directly without leaking token to tenant Nginx,
// and falls back to 443 only if :2222 refuses connection (reverse-proxy deployment).
fake_api([
    "GET $base/plans" => [200, [['id' => 1, 'name' => 'Starter']]],
]);
$tcPort443 = spanel_TestConnection(params(['serverport' => '443']));
check($tcPort443 === ['success' => true, 'error' => ''], 'TestConnection with serverport=443 succeeds on :2222');
check(count($GLOBALS['SENT']) === 1 && $GLOBALS['SENT'][0]['url'] === "https://panel.example.com:2222$base/plans", 'serverport=443 goes directly to :2222');

$GLOBALS['SENT'] = [];
$GLOBALS['SPANEL_TRANSPORT'] = function ($method, $url, $headers, $body) {
    $GLOBALS['SENT'][] = compact('method', 'url', 'headers', 'body');
    if (strpos($url, 'https://panel.example.com:2222/') === 0) {
        return ['errno' => 7, 'error' => 'Connection refused', 'status' => 0, 'body' => ''];
    }
    return [
        'errno' => 0,
        'error' => '',
        'status' => 200,
        'body' => json_encode([['id' => 1, 'name' => 'Starter']]),
    ];
};
$tcProxyFallback = spanel_TestConnection(params(['serverport' => '443']));
check($tcProxyFallback === ['success' => true, 'error' => ''], 'TestConnection falls back to 443 reverse proxy when :2222 is closed');
check(count($GLOBALS['SENT']) === 2 && $GLOBALS['SENT'][1]['url'] === "https://panel.example.com$base/plans", 'second request used reverse proxy on 443');

// Auto-upgrade http:// -> https:// on :2222 when WHMCS "Secure" checkbox was left unchecked
$GLOBALS['SENT'] = [];
$GLOBALS['SPANEL_TRANSPORT'] = function ($method, $url, $headers, $body) {
    $GLOBALS['SENT'][] = compact('method', 'url', 'headers', 'body');
    if (strpos($url, 'http://panel.example.com:2222/') === 0) {
        return ['errno' => 52, 'error' => 'Empty reply from server', 'status' => 0, 'body' => ''];
    }
    return [
        'errno' => 0,
        'error' => '',
        'status' => 200,
        'body' => json_encode([['id' => 1, 'name' => 'Starter']]),
    ];
};
$tcTlsUpgrade = spanel_TestConnection(params(['serversecure' => '', 'serverport' => '2222']));
check($tcTlsUpgrade === ['success' => true, 'error' => ''], 'Unchecked Secure auto-upgrades HTTP to HTTPS on :2222');
check(count($GLOBALS['SENT']) === 2 && $GLOBALS['SENT'][1]['url'] === "https://panel.example.com:2222$base/plans", 'auto-upgraded URL uses https://:2222');

// DirectAdmin / foreign panel on :2222 returning 302 Found. or 405 surfaces endpoint diagnostic
$GLOBALS['SPANEL_TRANSPORT'] = function ($method, $url, $headers, $body) {
    if ($method === 'GET') {
        return ['errno' => 0, 'error' => '', 'status' => 302, 'body' => '<a href="/login">Found</a>.' . "\n\n"];
    }
    return ['errno' => 0, 'error' => '', 'status' => 405, 'body' => ''];
};
$daTab = spanel_AdminServicesTabFields(params(['serverhostname' => 'da01.example.com']));
check(isset($daTab['SPanel']) && strpos($daTab['SPanel'], 'da01.example.com:2222') !== false && strpos($daTab['SPanel'], 'DirectAdmin') !== false, '302 Found surfaces endpoint & DirectAdmin hint: ' . json_encode($daTab));
$daCreate = spanel_CreateAccount(params(['serverhostname' => 'da01.example.com']));
check(strpos($daCreate, 'HTTP 405') !== false && strpos($daCreate, 'da01.example.com:2222') !== false, '405 surfaces endpoint hint: ' . $daCreate);

// Suspend, Unsuspend, short ChangePassword validation, unprovisioned Admin tab, and package name resolution
reset_recorders();
fake_api([
    "POST $base/accounts/whmcs%3A42/suspend" => [200, ['ok' => true, 'status' => 'suspended']],
    "POST $base/accounts/whmcs%3A42/unsuspend" => [200, ['ok' => true, 'status' => 'active']],
]);
check(spanel_SuspendAccount(params(['suspendreason' => ''])) === 'success', 'suspend with empty reason succeeds');
$suspPayload = json_decode($GLOBALS['SENT'][0]['body'], true);
check($suspPayload === ['reason' => 'Suspended by WHMCS'], 'empty suspendreason defaults cleanly');
check(spanel_SuspendAccount(params(['suspendreason' => 'Overdue invoice'])) === 'success', 'suspend with custom reason succeeds');
$suspPayload2 = json_decode($GLOBALS['SENT'][1]['body'], true);
check($suspPayload2 === ['reason' => 'Overdue invoice'], 'custom suspendreason forwarded');
check(spanel_UnsuspendAccount(params()) === 'success', 'unsuspend succeeds');

reset_recorders();
fake_api([]);
check(spanel_ChangePassword(params(['password' => 'short123'])) === 'Password must be between 12 and 72 characters', 'short password rejected cleanly');
check(count($GLOBALS['SENT']) === 0, 'short password makes no API call');

fake_api(["GET $base/accounts/whmcs%3A42" => [404, ['detail' => 'Account not found']]]);
$pendingTab = spanel_AdminServicesTabFields(params());
check(isset($pendingTab['SPanel status']) && strpos($pendingTab['SPanel status'], 'Not provisioned yet') !== false, 'unprovisioned tab shows clean status');

fake_api([
    "GET $base/plans" => [200, [['id' => 2, 'name' => 'Starter', 'slug' => 'starter'], ['id' => 3, 'name' => 'Business', 'slug' => 'business']]],
    "POST $base/accounts" => [200, ['external_id' => 'whmcs:42', 'username' => 'sp42_abcdefgh', 'status' => 'active']],
    "PATCH $base/accounts/whmcs%3A42/package" => [200, ['ok' => true, 'package_id' => 3]],
]);
check(spanel_CreateAccount(params(['configoption1' => 'Starter (#2)'])) === 'success', 'create accepts Loader label format');
check(count($GLOBALS['SENT']) === 1 && json_decode($GLOBALS['SENT'][0]['body'], true)['package_id'] === 2, 'label (#2) resolved without extra GET');
check(spanel_ChangePackage(params(['configoption1' => 'Business'])) === 'success', 'change package resolves package name via /plans');
$chgPkgBody = json_decode(end($GLOBALS['SENT'])['body'], true);
check($chgPkgBody['package_id'] === 3, 'Business resolved to package_id 3');

fake_api([
    "POST $base/accounts" => [404, ['detail' => 'Package not found']],
    "GET $base/plans" => [200, [['id' => 2, 'name' => 'Starter']]],
]);
$missingPkgErr = spanel_CreateAccount(params(['configoption1' => '1']));
check(strpos($missingPkgErr, 'Starter (#2)') !== false, 'deleted default package #1 hints available packages: ' . $missingPkgErr);

// Migrate existing WHMCS Product from DirectAdmin where configoption2..8 hold stale DirectAdmin values
$cfgOpts = spanel_ConfigOptions();
foreach ($cfgOpts as $optName => $optDef) {
    check(!empty($optDef['SimpleMode']), "ConfigOption '$optName' enables SimpleMode so WHMCS saves all 8 fields");
}
check(strpos($cfgOpts['PHP Version']['Options'], '8.5') !== false, 'PHP Version options include 8.5');

reset_recorders();
fake_api([
    "POST $base/accounts" => [200, ['external_id' => 'whmcs:858', 'username' => 'sp858_fyzalqlb', 'status' => 'active']],
]);
$daMigrated = params([
    'serviceid' => 858,
    'username' => 'sp858_fyzalqlb',
    'domain' => 'cokhihoangphan.com',
    'configoption1' => '2',
    'configoption2' => 'shared',
    'configoption3' => 'IPv4 shared; IPv6 none',
    'configoption4' => 'on',
    'configoption6' => 'on',
]);
check(spanel_CreateAccount($daMigrated) === 'success', 'CreateAccount succeeds even with leftover DirectAdmin configoption2..8');
$migratedBody = json_decode($GLOBALS['SENT'][0]['body'], true);
check($migratedBody['php_version'] === '8.4' && $migratedBody['app_type'] === 'php', 'stale DirectAdmin php_version/app_type sanitized to 8.4/php');
check($migratedBody['install_wordpress'] === false && $migratedBody['is_reseller'] === false, 'stale DirectAdmin boolean flags ignored');
$keyBeforeChange = (string) header_value($GLOBALS['SENT'][0]['headers'], 'Idempotency-Key');
reset_recorders();
fake_api([
    "POST $base/accounts" => [200, ['external_id' => 'whmcs:858', 'username' => 'sp858_fyzalqlb', 'status' => 'active']],
]);
check(spanel_CreateAccount($daMigrated) === 'success', 'CreateAccount identical retry succeeds');
$keySamePayload = (string) header_value($GLOBALS['SENT'][0]['headers'], 'Idempotency-Key');
check($keySamePayload === $keyBeforeChange, 'identical CreateAccount payload keeps same Idempotency-Key');
reset_recorders();
$GLOBALS['SENT'] = [];
$daModified = $daMigrated;
$daModified['configoption1'] = '3';
check(spanel_CreateAccount($daModified) === 'success', 'CreateAccount modified package succeeds');
$keyModifiedPayload = (string) header_value($GLOBALS['SENT'][0]['headers'], 'Idempotency-Key');
check($keyModifiedPayload !== $keyBeforeChange, 'modified CreateAccount payload generates distinct Idempotency-Key');

// FastAPI 422 validation error arrays are formatted cleanly instead of raw JSON
fake_api([
    "POST $base/accounts" => [422, ['detail' => [
        ['type' => 'value_error', 'loc' => ['body', 'domain'], 'msg' => 'Value error, Invalid domain'],
    ]]],
]);
$err422 = spanel_CreateAccount(params());
check($err422 === 'SPanel API error: domain — Invalid domain', 'clean 422 validation message: ' . $err422);

// Simulate HTML error page when :2222 also fails: strips raw HTML tags
$GLOBALS['SENT'] = [];
$GLOBALS['SPANEL_TRANSPORT'] = function ($method, $url, $headers, $body) {
    return [
        'errno' => 0,
        'error' => '',
        'status' => 404,
        'body' => "<html><script>alert(1)</script><body><h1>404 Not Found</h1><hr>nginx/1.24.0 (Ubuntu)</body></html>",
    ];
};
$tcHtmlErr = spanel_TestConnection(params(['serverport' => '2222']));
check($tcHtmlErr['success'] === false, 'HTML 404 fails cleanly');
check(strpos($tcHtmlErr['error'], '<') === false && strpos($tcHtmlErr['error'], 'alert(1)') === false, 'HTML and script tags stripped: ' . $tcHtmlErr['error']);
check(strpos($tcHtmlErr['error'], '2222') !== false, '404 diagnostic mentions port 2222');

// Simulate 401 when admin typed plain panel password instead of bp_ API token
fake_api(["GET $base/plans" => [401, ['detail' => 'Invalid, expired or revoked token']]]);
$tcWrongToken = spanel_TestConnection(params(['serveraccesshash' => '', 'serverpassword' => 'MyAdminPass123!']));
check($tcWrongToken['success'] === false && strpos($tcWrongToken['error'], 'bp_') !== false, '401 non-bp_ password gives token hint');

// Server-level SSO supports both Admin and Reseller tokens + IP allowlist hints
check($meta['AdminSingleSignOnLabel'] === 'Login to SPanel', 'AdminSingleSignOnLabel neutral for Admin/Reseller');
fake_api(["POST $base/admin/login" => [403, ['detail' => 'sso:reseller requires the token to have an IP allowlist']]]);
$ssoNoIp = spanel_AdminSingleSignOn(params());
check($ssoNoIp['success'] === false && strpos($ssoNoIp['errorMsg'], 'Allowed IPs') !== false, 'SSO IP allowlist hint: ' . $ssoNoIp['errorMsg']);
fake_api(["POST $base/admin/login" => [200, ['login_url' => '/api/auth/sso/res123', 'role' => 'reseller', 'username' => 'res1']]]);
$ssoResOk = spanel_AdminSingleSignOn(params());
check($ssoResOk === ['success' => true, 'redirectTo' => 'https://panel.example.com:2222/api/auth/sso/res123'], 'Reseller Server SSO redirect');

if ($failures) {
    fwrite(STDERR, "$failures of $checks checks failed\n");
    exit(1);
}
echo "OK $checks checks\n";
