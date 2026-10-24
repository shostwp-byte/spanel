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
$area = spanel_ClientArea(params());
check($area['templatefile'] === 'clientarea', 'client area template');
check($area['vars']['loginUrl'] === 'clientarea.php?action=productdetails&id=42&dosinglesignon=1', 'client area uses WHMCS SSO');
foreach ($GLOBALS['SENT'] as $request) {
    check(strpos($request['url'], '/login') === false, 'client area does not mint login tokens');
}

if ($failures) {
    fwrite(STDERR, "$failures of $checks checks failed\n");
    exit(1);
}
echo "OK $checks checks\n";
