<?php
/**
 * SPanel server module for WHMCS 7.x / 8.x (PHP 7.4 - 8.3, no dependencies).
 *
 * Talks to the SPanel provisioning API (/api/provisioning/v1). Each WHMCS
 * service is the SPanel account with external_id "whmcs:<serviceid>".
 */

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

if (!defined('SPANEL_API')) {
    define('SPANEL_API', '/api/provisioning/v1');
}
// Name (prefix) of the optional product custom field that receives the
// account's SFTP password. Mark it "Admin Only" to keep it from customers,
// or leave it visible so they can read it on the product details page.
if (!defined('SPANEL_SFTP_FIELD')) {
    define('SPANEL_SFTP_FIELD', 'SFTP Password');
}

function spanel_MetaData()
{
    return [
        'DisplayName' => 'SPanel Hosting',
        'APIVersion' => '1.1',
        'RequiresServer' => true,
        'DefaultNonSSLPort' => '2222',
        'DefaultSSLPort' => '2222',
        'ServiceSingleSignOnLabel' => 'Login to SPanel',
        'AdminSingleSignOnLabel' => 'Login to SPanel as Admin',
        'ListAccountsUniqueIdentifierDisplayName' => 'Username',
        'ListAccountsUniqueIdentifierField' => 'username',
        'ListAccountsProductField' => 'configoption1',
    ];
}

function spanel_ConfigOptions()
{
    // Keep the order: WHMCS stores these as configoption1..8.
    return [
        'Package' => [
            'Type' => 'text',
            'Size' => '25',
            'Loader' => 'spanel_PackageLoader',
            'SimpleMode' => true,
            'Default' => '1',
            'Description' => 'Select a SPanel package',
        ],
        'App Type' => [
            'Type' => 'dropdown',
            'Options' => 'php,static,wordpress',
            'Default' => 'php',
        ],
        'PHP Version' => [
            'Type' => 'dropdown',
            'Options' => '8.4,8.3,8.2,8.1,8.0,7.4,5.6',
            'Default' => '8.4',
        ],
        'Install WordPress' => [
            'Type' => 'yesno',
            'Description' => 'Install WordPress during provisioning',
        ],
        'Auto SSL' => [
            'Type' => 'yesno',
            'Description' => 'Request Let\'s Encrypt SSL after provisioning',
        ],
        'Is Reseller' => [
            'Type' => 'yesno',
            'Description' => 'Create a Reseller account instead of a regular user',
        ],
        'Reseller Package' => [
            'Type' => 'text',
            'Size' => '25',
            'Loader' => 'spanel_ResellerPackageLoader',
            'SimpleMode' => true,
            'Default' => '',
            'Description' => 'Reseller quota package (only with Is Reseller)',
        ],
        'Backup Before Terminate' => [
            'Type' => 'yesno',
            'Description' => 'Take a full backup before deleting the account (terminate waits for it)',
        ],
    ];
}

function spanel_PackageLoader($params)
{
    $result = spanel_request($params, 'GET', SPANEL_API . '/plans');
    if (!$result['ok']) {
        throw new Exception($result['error']);
    }

    return spanel_package_options($result['data']);
}

function spanel_ResellerPackageLoader($params)
{
    $result = spanel_request($params, 'GET', SPANEL_API . '/reseller-plans');
    if (!$result['ok']) {
        throw new Exception($result['error']);
    }
    $options = ['' => 'None'];
    foreach ($result['data'] as $package) {
        if (!is_array($package) || (int) ($package['id'] ?? 0) <= 0) {
            continue;
        }
        $id = (int) $package['id'];
        $name = trim((string) ($package['name'] ?? '')) ?: 'Reseller package ' . $id;
        $options[(string) $id] = $name . ' (#' . $id . ')';
    }
    return $options;
}

function spanel_TestConnection($params)
{
    $result = spanel_request($params, 'GET', SPANEL_API . '/plans');
    return $result['ok'] ? ['success' => true, 'error' => ''] : ['success' => false, 'error' => $result['error']];
}

function spanel_CreateAccount($params)
{
    $domain = spanel_domain($params);
    $username = spanel_provision_username($params);
    $password = spanel_password($params);
    // Saved before the call: a retry after a timeout must send the same
    // credentials, so it gets the account the first attempt created.
    spanel_save_service_record($params, [], $username, $password);

    $payload = [
        'external_id' => spanel_external_id($params),
        'username' => $username,
        'password' => $password,
        'package_id' => (int) spanel_config($params, 1, '1'),
        'php_version' => spanel_config($params, 3, '8.4'),
        'app_type' => $domain === '' ? 'php' : spanel_config($params, 2, 'php'),
        'install_wordpress' => spanel_yesno(spanel_config($params, 4, '')),
        'enable_ssl' => spanel_yesno(spanel_config($params, 5, '')),
        'is_reseller' => spanel_yesno(spanel_config($params, 6, '')),
    ];

    if ($domain !== '') {
        $payload['domain'] = $domain;
    }

    if ($payload['install_wordpress']) {
        $payload['app_type'] = 'wordpress';
    }
    if ($domain === '' && ($payload['install_wordpress'] || $payload['enable_ssl'])) {
        return 'A domain is required when Install WordPress or Auto SSL is enabled';
    }
    $resellerPackage = (int) spanel_config($params, 7, '0');
    if ($payload['is_reseller'] && $resellerPackage > 0) {
        $payload['reseller_package_id'] = $resellerPackage;
    }
    $limits = spanel_limits_from_config_options($params['configoptions'] ?? []);
    if ($limits) {
        $payload['limits'] = $limits;
    }

    $key = 'whmcs-' . (int) ($params['serviceid'] ?? 0) . '-create-' . substr(hash('sha256', $username . "\n" . $password), 0, 24);
    $result = spanel_request($params, 'POST', SPANEL_API . '/accounts', $payload, [], $key);
    if (!$result['ok']) {
        return $result['error'];
    }

    $accountUsername = trim((string) ($result['data']['username'] ?? $username));
    spanel_save_service_record($params, $result['data'], $accountUsername, $password);
    if (!empty($result['data']['sftp_password'])) {
        spanel_store_sftp_password($params, (string) $result['data']['sftp_password']);
    }
    return 'success';
}

function spanel_SuspendAccount($params)
{
    $payload = ['reason' => (string) ($params['suspendreason'] ?? 'Suspended by WHMCS')];
    $result = spanel_request($params, 'POST', spanel_account_path($params, '/suspend'), $payload);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_UnsuspendAccount($params)
{
    $result = spanel_request($params, 'POST', spanel_account_path($params, '/unsuspend'));
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_TerminateAccount($params)
{
    // Terminate fully removes the hosting account, its websites, and the
    // Linux/SFTP user and home directory. With "Backup Before Terminate" a
    // full backup is taken first, inside the same request.
    $query = spanel_yesno(spanel_config($params, 8, '')) ? ['backup' => 'true'] : [];
    $result = spanel_request($params, 'DELETE', spanel_account_path($params), null, $query);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_ChangePassword($params)
{
    $password = spanel_password($params);
    $payload = ['password' => $password];
    // A retry of the same change replays the first answer, which may carry
    // the only copy of a newly minted SFTP password.
    $key = 'whmcs-' . (int) ($params['serviceid'] ?? 0) . '-password-' . substr(hash('sha256', $password), 0, 24);
    $result = spanel_request($params, 'PATCH', spanel_account_path($params, '/password'), $payload, [], $key);
    if (!$result['ok']) {
        return $result['error'];
    }
    if (!empty($result['data']['sftp_password'])) {
        spanel_store_sftp_password($params, (string) $result['data']['sftp_password']);
    }
    return 'success';
}

function spanel_ChangePackage($params)
{
    $payload = ['package_id' => (int) spanel_config($params, 1, '1')];
    $resellerPackage = (int) spanel_config($params, 7, '0');
    if (spanel_yesno(spanel_config($params, 6, '')) && $resellerPackage > 0) {
        $payload['reseller_package_id'] = $resellerPackage;
    }
    $limits = spanel_limits_from_config_options($params['configoptions'] ?? []);
    if ($limits) {
        $payload['limits'] = $limits;
    }
    $result = spanel_request($params, 'PATCH', spanel_account_path($params, '/package'), $payload);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_UsageUpdate($params)
{
    $serverid = (int) ($params['serverid'] ?? 0);
    if ($serverid <= 0) {
        return 'success';
    }

    $result = spanel_request($params, 'GET', SPANEL_API . '/accounts/usage');
    if (!$result['ok']) {
        return $result['error'];
    }

    foreach ($result['data'] as $account) {
        $serviceid = spanel_service_id_from_external((string) ($account['external_id'] ?? ''));
        if ($serviceid <= 0) {
            continue;
        }
        $update = spanel_usage_row($account);
        $update['lastupdate'] = \WHMCS\Database\Capsule::raw('NOW()');

        try {
            \WHMCS\Database\Capsule::table('tblhosting')
                ->where('id', $serviceid)
                ->where('server', $serverid)
                ->update($update);
        } catch (\Throwable $e) {
            // Ignore DB update errors for individual services
        }
    }

    return 'success';
}

/**
 * WHMCS server sync ("Import / Sync Accounts"): every account on the server.
 */
function spanel_ListAccounts($params)
{
    $accounts = [];
    $page = 1;
    do {
        $result = spanel_request($params, 'GET', SPANEL_API . '/accounts', null, ['page' => $page, 'per_page' => 500]);
        if (!$result['ok']) {
            return ['success' => false, 'error' => $result['error']];
        }
        $items = isset($result['data']['items']) && is_array($result['data']['items']) ? $result['data']['items'] : [];
        foreach ($items as $item) {
            $accounts[] = spanel_list_account_row($item);
        }
        $total = (int) ($result['data']['total'] ?? 0);
        $page++;
    } while ($items && count($accounts) < $total && $page <= 1000);

    return ['success' => true, 'accounts' => $accounts];
}

function spanel_GetUserCount($params)
{
    $result = spanel_request($params, 'GET', SPANEL_API . '/accounts', null, ['page' => 1, 'per_page' => 1]);
    if (!$result['ok']) {
        return ['success' => false, 'error' => $result['error']];
    }
    $total = (int) ($result['data']['total'] ?? 0);
    // Every account the token can list was created by a billing system on
    // this server; SPanel has no notion of accounts owned by another one.
    return ['success' => true, 'totalAccounts' => $total, 'ownedAccounts' => $total];
}

function spanel_ServiceSingleSignOn($params)
{
    $result = spanel_request($params, 'POST', spanel_account_path($params, '/login'));
    if (!$result['ok']) {
        return ['success' => false, 'errorMsg' => $result['error']];
    }
    $url = spanel_absolute_url($params, (string) ($result['data']['login_url'] ?? ''));
    if ($url === '') {
        return ['success' => false, 'errorMsg' => 'SPanel returned no login URL'];
    }
    return ['success' => true, 'redirectTo' => $url];
}

function spanel_AdminSingleSignOn($params)
{
    $result = spanel_request($params, 'POST', SPANEL_API . '/admin/login');
    if (!$result['ok']) {
        return ['success' => false, 'errorMsg' => $result['error'] . ' (the API token needs the sso:admin scope)'];
    }
    $url = spanel_absolute_url($params, (string) ($result['data']['login_url'] ?? ''));
    if ($url === '') {
        return ['success' => false, 'errorMsg' => 'SPanel returned no login URL'];
    }
    return ['success' => true, 'redirectTo' => $url];
}

function spanel_AdminServicesTabFields($params)
{
    $fields = [];
    $account = spanel_request($params, 'GET', spanel_account_path($params));
    if (!$account['ok']) {
        return ['SPanel' => spanel_h($account['error'])];
    }
    $data = $account['data'];
    $fields['SPanel status'] = spanel_h((string) ($data['status'] ?? '')) . (empty($data['last_action']) ? '' : ' <small>(last: ' . spanel_h((string) $data['last_action']) . ')</small>');
    $fields['SPanel username'] = spanel_h((string) ($data['username'] ?? ''));
    $fields['Primary domain'] = spanel_h((string) ($data['domain'] ?? '')) ?: '<em>none</em>';
    $fields['Package'] = spanel_h(trim((string) ($data['package_name'] ?? '') . ' #' . (string) ($data['package_id'] ?? '')));
    if (!empty($data['is_reseller'])) {
        $fields['Account type'] = 'Reseller' . (empty($data['reseller_package_id']) ? '' : ' (reseller package #' . (int) $data['reseller_package_id'] . ')');
    }

    $usage = spanel_request($params, 'GET', spanel_account_path($params, '/usage'));
    if ($usage['ok']) {
        $u = $usage['data'];
        $fields['Disk usage'] = spanel_format_usage((int) ($u['storage_used_bytes'] ?? 0), (int) ($u['storage_limit_bytes'] ?? 0));
        $fields['Bandwidth (this month)'] = spanel_format_usage((int) ($u['bandwidth_used_bytes'] ?? 0), (int) ($u['bandwidth_limit_bytes'] ?? 0));
        $fields['Websites / databases'] = (int) ($u['website_count'] ?? 0) . ' / ' . (int) ($u['database_count'] ?? 0);
        if (!empty($u['user_count'])) {
            $fields['Reseller accounts'] = (string) (int) $u['user_count'];
        }
    }
    return $fields;
}

function spanel_AdminCustomButtonArray()
{
    return [
        'Reset SFTP Password' => 'ResetSftpPassword',
        'Create Backup' => 'CreateBackup',
    ];
}

function spanel_ClientAreaCustomButtonArray()
{
    return [
        'Reset SFTP Password' => 'ResetSftpPassword',
        'Create Backup' => 'CreateBackup',
    ];
}

function spanel_ResetSftpPassword($params)
{
    // Without somewhere to put it, the new password would be shown to nobody
    // and the old one would already be gone.
    if (spanel_sftp_field_id($params) <= 0) {
        return 'Create a product custom field named "' . SPANEL_SFTP_FIELD . '" to receive the new SFTP password';
    }
    $result = spanel_request($params, 'POST', spanel_account_path($params, '/sftp-password'));
    if (!$result['ok']) {
        return $result['error'];
    }
    $password = (string) ($result['data']['sftp_password'] ?? '');
    if ($password === '' || !spanel_store_sftp_password($params, $password)) {
        return 'SFTP password was reset but could not be saved on the service';
    }
    return 'success';
}

function spanel_CreateBackup($params)
{
    $result = spanel_request($params, 'POST', spanel_account_path($params, '/backup'));
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_LoginLink($params)
{
    // The admin "Login to SPanel" button comes from ServiceSingleSignOn, which
    // mints a one-time link only when clicked. This is a plain link to the panel.
    return '<a href="' . spanel_h(spanel_base_url($params)) . '" target="_blank" rel="noopener">Open SPanel</a>';
}

function spanel_ClientArea($params)
{
    $account = [];
    $usage = [];
    if (!empty($params['serviceid'])) {
        $result = spanel_request($params, 'GET', spanel_account_path($params));
        if ($result['ok']) {
            $account = $result['data'];
        }
        $usageResult = spanel_request($params, 'GET', spanel_account_path($params, '/usage'));
        if ($usageResult['ok']) {
            $usage = $usageResult['data'];
        }
    }
    $serviceId = (int) ($params['serviceid'] ?? 0);

    return [
        'templatefile' => 'clientarea',
        'vars' => [
            'panelUrl' => spanel_base_url($params),
            // WHMCS calls ServiceSingleSignOn when this is opened, so a login
            // token is minted on click rather than on every page view.
            'loginUrl' => 'clientarea.php?action=productdetails&id=' . $serviceId . '&dosinglesignon=1',
            'serviceId' => $serviceId,
            'username' => spanel_username($params),
            'serviceLabel' => spanel_service_label($params, $account),
            'packageName' => trim((string) ($account['package_name'] ?? '')),
            'domain' => trim((string) ($account['domain'] ?? '')),
            'status' => trim((string) ($account['status'] ?? '')),
            'isReseller' => !empty($account['is_reseller']),
            'diskUsage' => $usage ? spanel_format_usage((int) ($usage['storage_used_bytes'] ?? 0), (int) ($usage['storage_limit_bytes'] ?? 0)) : '',
            'bandwidthUsage' => $usage ? spanel_format_usage((int) ($usage['bandwidth_used_bytes'] ?? 0), (int) ($usage['bandwidth_limit_bytes'] ?? 0)) : '',
            'websiteCount' => (int) ($usage['website_count'] ?? 0),
            'resellerUserCount' => (int) ($usage['user_count'] ?? 0),
        ],
    ];
}

// ── HTTP ─────────────────────────────────────────────────────────────────

function spanel_request($params, $method, $path, $payload = null, $query = [], $idempotencyKey = '')
{
    $token = trim((string) ($params['serveraccesshash'] ?? ''));
    if ($token === '') {
        $token = trim((string) ($params['serverpassword'] ?? ''));
    }
    if ($token === '') {
        return ['ok' => false, 'error' => 'Missing SPanel API token in server Access Hash or Password'];
    }

    $url = spanel_base_url($params) . $path;
    if ($query) {
        $url .= '?' . http_build_query($query);
    }

    $headers = [
        'Authorization: Bearer ' . $token,
        'Accept: application/json',
        'User-Agent: SPanel-WHMCS/2.0',
    ];
    $body = null;
    if ($payload !== null) {
        $body = json_encode($payload);
        $headers[] = 'Content-Type: application/json';
    }
    if ($idempotencyKey !== '') {
        $headers[] = 'Idempotency-Key: ' . $idempotencyKey;
    }

    $response = spanel_http_send(strtoupper($method), $url, $headers, $body);
    $secrets = spanel_request_secrets($payload);
    if ($response['errno']) {
        spanel_log($params, $method, $path, $payload, null, 'cURL ' . $response['errno'] . ': ' . $response['error'], $secrets);
        return ['ok' => false, 'error' => 'SPanel connection failed: ' . $response['error']];
    }

    $status = (int) $response['status'];
    $raw = (string) $response['body'];
    $data = json_decode($raw, true);
    if (is_array($data)) {
        foreach (['sftp_password', 'token'] as $secretKey) {
            if (!empty($data[$secretKey]) && is_string($data[$secretKey])) {
                $secrets[] = $data[$secretKey];
            }
        }
    }
    if ($status < 200 || $status >= 300) {
        $message = '';
        if (is_array($data)) {
            if (isset($data['detail']) && is_array($data['detail']) && isset($data['detail']['error'])) {
                $message = (string) $data['detail']['error'];
            } elseif (isset($data['detail'])) {
                $message = is_array($data['detail']) ? json_encode($data['detail']) : (string) $data['detail'];
            }
        }
        if ($message === '') {
            $message = $raw !== '' ? $raw : 'HTTP ' . $status;
        }
        spanel_log($params, $method, $path, $payload, $raw, 'HTTP ' . $status . ': ' . $message, $secrets);
        return ['ok' => false, 'error' => 'SPanel API error: ' . $message, 'status' => $status];
    }

    // Unwrap a {"success": true, "data": {...}} envelope if a proxy adds one.
    if (is_array($data) && array_key_exists('data', $data) && array_key_exists('success', $data)) {
        if (!$data['success']) {
            $errMsg = (string) ($data['error'] ?? 'Unknown API error');
            spanel_log($params, $method, $path, $payload, $raw, 'Envelope error: ' . $errMsg, $secrets);
            return ['ok' => false, 'error' => 'SPanel API error: ' . $errMsg];
        }
        $data = $data['data'];
    }

    spanel_log($params, $method, $path, $payload, $raw, 'OK', $secrets);
    return ['ok' => true, 'data' => is_array($data) ? $data : [], 'status' => $status];
}

/**
 * Perform the HTTP call. $GLOBALS['SPANEL_TRANSPORT'] replaces it in the
 * module's test harness (tests/php); WHMCS never sets it.
 */
function spanel_http_send($method, $url, $headers, $body)
{
    if (isset($GLOBALS['SPANEL_TRANSPORT']) && is_callable($GLOBALS['SPANEL_TRANSPORT'])) {
        return call_user_func($GLOBALS['SPANEL_TRANSPORT'], $method, $url, $headers, $body);
    }
    if (!function_exists('curl_init')) {
        return ['errno' => 1, 'error' => 'PHP cURL extension is required', 'status' => 0, 'body' => ''];
    }

    $ch = curl_init($url);
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_CONNECTTIMEOUT => 15,
        CURLOPT_TIMEOUT => 120,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ];
    if ($body !== null) {
        $options[CURLOPT_POSTFIELDS] = $body;
    }
    curl_setopt_array($ch, $options);

    $responseBody = curl_exec($ch);
    $result = [
        'errno' => curl_errno($ch),
        'error' => curl_error($ch),
        'status' => (int) curl_getinfo($ch, CURLINFO_HTTP_CODE),
        'body' => is_string($responseBody) ? $responseBody : '',
    ];
    curl_close($ch);
    return $result;
}

function spanel_request_secrets($payload)
{
    $secrets = [];
    if (is_array($payload) && !empty($payload['password'])) {
        $secrets[] = (string) $payload['password'];
    }
    return $secrets;
}

// ── Helpers ──────────────────────────────────────────────────────────────

function spanel_base_url($params)
{
    $hostname = trim((string) ($params['serverhostname'] ?? ''));
    if ($hostname === '') {
        $hostname = trim((string) ($params['serverip'] ?? ''));
    }
    $hostname = preg_replace('#^https?://#', '', $hostname);
    $hostname = rtrim($hostname, '/');

    $secure = !empty($params['serversecure']);
    $scheme = $secure ? 'https' : 'http';
    $port = trim((string) ($params['serverport'] ?? ''));

    if ($port !== '' && !in_array($port, ['80', '443'], true)) {
        return $scheme . '://' . $hostname . ':' . $port;
    }

    return $scheme . '://' . $hostname;
}

function spanel_absolute_url($params, $url)
{
    $url = trim($url);
    if ($url === '') {
        return '';
    }
    if (preg_match('#^https?://#i', $url)) {
        return $url;
    }
    return rtrim(spanel_base_url($params), '/') . '/' . ltrim($url, '/');
}

function spanel_external_id($params)
{
    return 'whmcs:' . (int) ($params['serviceid'] ?? 0);
}

function spanel_account_path($params, $suffix = '')
{
    return SPANEL_API . '/accounts/' . rawurlencode(spanel_external_id($params)) . $suffix;
}

function spanel_service_id_from_external($externalId)
{
    if (strpos($externalId, 'whmcs:') !== 0) {
        return 0;
    }
    return (int) substr($externalId, 6);
}

function spanel_username($params)
{
    $username = trim((string) ($params['username'] ?? ''));
    if ($username !== '' && preg_match('/^[a-z_][a-z0-9_-]{2,31}$/', $username)) {
        return $username;
    }

    $serviceId = (int) ($params['serviceid'] ?? 0);
    return spanel_random_username($serviceId);
}

function spanel_provision_username($params)
{
    $serviceId = (int) ($params['serviceid'] ?? 0);
    // Reuse a name this module generated for the same service on an earlier
    // attempt, so a retried create reaches the same account.
    $existing = trim((string) ($params['username'] ?? ''));
    if ($existing !== '' && preg_match('/^sp' . $serviceId . '_[a-z0-9]{8}$/', $existing)) {
        return $existing;
    }
    return spanel_random_username($serviceId);
}

function spanel_password($params)
{
    $password = (string) ($params['password'] ?? '');
    if (strlen($password) >= 12 && strlen($password) <= 72) {
        return $password;
    }

    return spanel_random_string(24, 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789');
}

function spanel_domain($params)
{
    return strtolower(trim((string) ($params['domain'] ?? '')));
}

function spanel_config($params, $index, $default)
{
    $key = 'configoption' . $index;
    $value = $params[$key] ?? $default;
    return trim((string) $value) !== '' ? trim((string) $value) : $default;
}

function spanel_yesno($value)
{
    return in_array(strtolower(trim((string) $value)), ['on', 'yes', 'true', '1'], true);
}

function spanel_h($value)
{
    return htmlspecialchars((string) $value, ENT_QUOTES, 'UTF-8');
}

/**
 * Map WHMCS configurable options to SPanel limit overrides.
 *
 * Recognised option names (case and punctuation ignored, "Name|Label" uses
 * the part before the bar): Disk Space / Disk / Storage, Bandwidth /
 * Traffic, Websites / Sites / Domains. Sizes are MB unless the value or the
 * option name says GB or TB ("Disk Space (GB)" with value 10, or "10 GB").
 * "Unlimited" or 0 means unlimited.
 */
function spanel_limits_from_config_options($configOptions)
{
    if (!is_array($configOptions)) {
        return [];
    }
    $map = [
        'storage_limit_mb' => ['diskspace', 'disk', 'storage', 'diskquota', 'disklimit', 'storagelimitmb', 'webspace'],
        'bandwidth_limit_mb' => ['bandwidth', 'traffic', 'monthlybandwidth', 'monthlytraffic', 'bandwidthlimitmb'],
        'website_limit' => ['websites', 'website', 'sites', 'domains', 'addondomains', 'websitelimit'],
    ];
    $limits = [];
    foreach ($configOptions as $name => $value) {
        $label = explode('|', (string) $name)[0];
        $unit = '';
        if (preg_match('/\b(tb|gb|mb)\b/i', $label, $m)) {
            $unit = strtolower($m[1]);
        }
        $key = preg_replace('/\(?\b(tb|gb|mb)\b\)?/i', '', strtolower($label));
        $key = preg_replace('/[^a-z0-9]/', '', $key);
        foreach ($map as $field => $aliases) {
            if (!in_array($key, $aliases, true)) {
                continue;
            }
            $parsed = $field === 'website_limit' ? spanel_parse_count($value) : spanel_parse_size_mb($value, $unit);
            if ($parsed !== null) {
                $limits[$field] = $parsed;
            }
        }
    }
    return $limits;
}

function spanel_parse_size_mb($value, $defaultUnit = '')
{
    $text = strtolower(trim((string) $value));
    if ($text === '' ) {
        return null;
    }
    if (in_array($text, ['unlimited', 'unlimit', 'none', '∞'], true)) {
        return 0;
    }
    if (!preg_match('/^([0-9]+(?:\.[0-9]+)?)\s*(tb|gb|mb)?$/', $text, $m)) {
        return null;
    }
    $unit = isset($m[2]) && $m[2] !== '' ? $m[2] : ($defaultUnit !== '' ? $defaultUnit : 'mb');
    $factor = ['mb' => 1, 'gb' => 1024, 'tb' => 1048576][$unit];
    return (int) round(((float) $m[1]) * $factor);
}

function spanel_parse_count($value)
{
    $text = strtolower(trim((string) $value));
    if ($text === '') {
        return null;
    }
    if (in_array($text, ['unlimited', 'unlimit', '∞'], true)) {
        return 0;
    }
    if (!preg_match('/^([0-9]+)/', $text, $m)) {
        return null;
    }
    return (int) $m[1];
}

function spanel_usage_row($account)
{
    $mb = 1024 * 1024;
    $diskLimit = (int) ($account['storage_limit_bytes'] ?? 0);
    return [
        'diskusage' => (int) round(((int) ($account['storage_used_bytes'] ?? 0)) / $mb),
        'disklimit' => $diskLimit > 0 ? (int) round($diskLimit / $mb) : 0,
        'bwusage' => (int) round(((int) ($account['bandwidth_used_bytes'] ?? 0)) / $mb),
        'bwlimit' => (int) round(((int) ($account['bandwidth_limit_bytes'] ?? 0)) / $mb),
    ];
}

function spanel_list_account_row($item)
{
    $statuses = ['active' => 'Active', 'suspended' => 'Suspended', 'pending' => 'Pending', 'terminated' => 'Terminated', 'failed' => 'Pending'];
    $created = (string) ($item['created_at'] ?? '');
    $timestamp = $created !== '' ? strtotime($created) : false;
    return [
        'email' => (string) ($item['email'] ?? ''),
        'username' => (string) ($item['username'] ?? ''),
        'domain' => (string) ($item['domain'] ?? ''),
        'uniqueIdentifier' => (string) ($item['username'] ?? ''),
        'product' => (string) ($item['package_id'] ?? ''),
        'primaryip' => '',
        'created' => $timestamp ? date('Y-m-d H:i:s', $timestamp) : '',
        'status' => $statuses[strtolower((string) ($item['status'] ?? ''))] ?? 'Active',
    ];
}

function spanel_format_usage($usedBytes, $limitBytes)
{
    $used = spanel_format_bytes($usedBytes);
    if ($limitBytes <= 0) {
        return $used . ' / unlimited';
    }
    $percent = round($usedBytes / $limitBytes * 100, 1);
    return $used . ' / ' . spanel_format_bytes($limitBytes) . ' (' . $percent . '%)';
}

function spanel_format_bytes($bytes)
{
    $units = ['B', 'KB', 'MB', 'GB', 'TB'];
    $value = (float) max(0, $bytes);
    $i = 0;
    while ($value >= 1024 && $i < count($units) - 1) {
        $value /= 1024;
        $i++;
    }
    return ($i === 0 ? (string) (int) $value : number_format($value, 1)) . ' ' . $units[$i];
}

function spanel_package_options($packages)
{
    if (!is_array($packages)) {
        throw new Exception('SPanel package list response is invalid');
    }

    $options = [];
    foreach ($packages as $package) {
        if (!is_array($package)) {
            continue;
        }

        $id = (int) ($package['id'] ?? 0);
        if ($id <= 0) {
            continue;
        }

        $name = trim((string) ($package['name'] ?? ''));
        if ($name === '') {
            $name = 'Package ' . $id;
        }

        $options[(string) $id] = $name . ' (#' . $id . ')';
    }

    if ($options === []) {
        throw new Exception('No SPanel packages found');
    }

    return $options;
}

function spanel_service_label($params, $account)
{
    if (is_array($account) && !empty($account['service_label'])) {
        return (string) $account['service_label'];
    }

    $product = trim((string) ($params['productname'] ?? ''));
    if ($product === '') {
        $product = 'SPanel Hosting';
    }

    $serviceId = (int) ($params['serviceid'] ?? 0);
    return $serviceId > 0 ? $product . ' #' . $serviceId : $product;
}

/**
 * Id of the product's "SFTP Password" custom field, or 0 when it has none.
 */
function spanel_sftp_field_id($params)
{
    $pid = (int) ($params['pid'] ?? $params['packageid'] ?? 0);
    if ($pid <= 0 || !class_exists('\WHMCS\Database\Capsule')) {
        return 0;
    }
    try {
        $row = \WHMCS\Database\Capsule::table('tblcustomfields')
            ->where('type', 'product')
            ->where('relid', $pid)
            ->where('fieldname', 'like', SPANEL_SFTP_FIELD . '%')
            ->first(['id']);
    } catch (\Throwable $e) {
        return 0;
    }
    return $row ? (int) $row->id : 0;
}

/**
 * Put the account's SFTP password in the service's "SFTP Password" custom
 * field. SPanel keeps no readable copy, so this is where it lives.
 */
function spanel_store_sftp_password($params, $password)
{
    $fieldId = spanel_sftp_field_id($params);
    if ($fieldId <= 0 || !function_exists('localAPI') || empty($params['serviceid'])) {
        return false;
    }
    try {
        $result = localAPI('UpdateClientProduct', [
            'serviceid' => (int) $params['serviceid'],
            'customfields' => base64_encode(serialize([$fieldId => $password])),
        ]);
    } catch (\Throwable $e) {
        return false;
    }
    return is_array($result) && ($result['result'] ?? '') === 'success';
}

function spanel_save_service_record($params, $data, $username = '', $password = '')
{
    if (!function_exists('localAPI') || empty($params['serviceid'])) {
        return;
    }

    $update = ['serviceid' => (int) $params['serviceid']];
    if ($data) {
        $lines = ['spanel'];
        foreach (['service_label', 'external_id', 'username', 'email', 'domain', 'status', 'package_id', 'package_name', 'storage_used_bytes', 'storage_limit_bytes', 'storage_percent'] as $key) {
            if (array_key_exists($key, $data)) {
                $lines[] = $key . ': ' . (is_scalar($data[$key]) ? $data[$key] : json_encode($data[$key]));
            }
        }
        $update['notes'] = implode("\n", $lines);
    }
    if ($username !== '') {
        $update['serviceusername'] = $username;
    }
    if ($password !== '') {
        $update['servicepassword'] = $password;
    }

    try {
        localAPI('UpdateClientProduct', $update);
    } catch (\Throwable $e) {
        // WHMCS record update is best effort; provisioning already succeeded.
    }
}

function spanel_save_service_note($params, $data)
{
    spanel_save_service_record($params, $data);
}

function spanel_random_username($serviceId)
{
    $prefix = 'sp' . max(0, (int) $serviceId) . '_';
    return substr($prefix . spanel_random_string(8, 'abcdefghijklmnopqrstuvwxyz0123456789'), 0, 32);
}

function spanel_random_string($length, $alphabet)
{
    $result = '';
    $max = strlen($alphabet) - 1;
    for ($i = 0; $i < $length; $i++) {
        $result .= $alphabet[random_int(0, $max)];
    }
    return $result;
}

function spanel_log($params, $method, $path, $request, $response, $result, $secrets = [])
{
    if (!function_exists('logModuleCall')) {
        return;
    }

    $replaceVars = [trim((string) ($params['serveraccesshash'] ?? '')), trim((string) ($params['serverpassword'] ?? ''))];
    foreach ($secrets as $secret) {
        if ((string) $secret !== '') {
            $replaceVars[] = (string) $secret;
        }
    }

    logModuleCall(
        'spanel',
        $method . ' ' . $path,
        $request,
        $response,
        $result,
        array_values(array_filter($replaceVars, 'strlen'))
    );
}
