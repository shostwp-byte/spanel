<?php

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

function spanel_MetaData()
{
    return [
        'DisplayName' => 'SPanel Hosting',
        'APIVersion' => '1.1',
        'RequiresServer' => true,
    ];
}

function spanel_ConfigOptions()
{
    return [
        'Package' => [
            'Type' => 'text',
            'Size' => '25',
            'Loader' => 'spanel_PackageLoader',
            'SimpleMode' => true,
            'Default' => '1',
            'Description' => 'Select a spanel package',
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
    ];
}

function spanel_PackageLoader($params)
{
    $result = spanel_request($params, 'GET', '/api/provisioning/v1/plans');
    if (!$result['ok']) {
        throw new Exception($result['error']);
    }

    return spanel_package_options($result['data']);
}

function spanel_TestConnection($params)
{
    $result = spanel_request($params, 'GET', '/api/provisioning/v1/plans');
    return $result['ok'] ? ['success' => true] : ['success' => false, 'error' => $result['error']];
}

function spanel_CreateAccount($params)
{
    $domain = spanel_domain($params);
    $username = spanel_provision_username($params);
    $password = spanel_password($params);
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

    $result = spanel_request($params, 'POST', '/api/provisioning/v1/accounts', $payload);
    if (!$result['ok']) {
        return $result['error'];
    }

    $accountUsername = trim((string) ($result['data']['username'] ?? $username));
    spanel_save_service_record($params, $result['data'], $accountUsername, $password);
    return 'success';
}

function spanel_SuspendAccount($params)
{
    $payload = ['reason' => $params['suspendreason'] ?? 'Suspended by WHMCS'];
    $result = spanel_request($params, 'POST', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)) . '/suspend', $payload);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_UnsuspendAccount($params)
{
    $result = spanel_request($params, 'POST', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)) . '/unsuspend');
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_TerminateAccount($params)
{
    // Terminate fully removes the hosting account, its websites, and the
    // Linux/SFTP user and home directory.
    $result = spanel_request($params, 'DELETE', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)));
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_ChangePassword($params)
{
    $payload = ['password' => spanel_password($params)];
    $result = spanel_request($params, 'PATCH', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)) . '/password', $payload);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_ChangePackage($params)
{
    $payload = ['package_id' => (int) spanel_config($params, 1, '1')];
    $result = spanel_request($params, 'PATCH', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)) . '/package', $payload);
    return $result['ok'] ? 'success' : $result['error'];
}

function spanel_UsageUpdate($params)
{
    $serverid = $params['serverid'] ?? 0;
    if (empty($serverid)) {
        return 'success';
    }

    $result = spanel_request($params, 'GET', '/api/provisioning/v1/accounts/usage');
    if (!$result['ok']) {
        return $result['error'];
    }

    foreach ($result['data'] as $account) {
        $externalId = $account['external_id'] ?? '';
        if (strpos($externalId, 'whmcs:') === 0) {
            $serviceid = (int) substr($externalId, 6);
            if ($serviceid <= 0) continue;
            
            $diskLimit = (int)($account['storage_limit_bytes'] ?? 0);
            if ($diskLimit > 0) {
                $diskLimit = round($diskLimit / (1024 * 1024));
            } else {
                $diskLimit = 0; // unlimited or 0
            }

            $update = [
                'diskusage' => round(($account['storage_used_bytes'] ?? 0) / (1024 * 1024)),
                'disklimit' => $diskLimit,
                'bwusage' => round(($account['bandwidth_used_bytes'] ?? 0) / (1024 * 1024)),
                'bwlimit' => round(($account['bandwidth_limit_bytes'] ?? 0) / (1024 * 1024)),
                'lastupdate' => \WHMCS\Database\Capsule::raw('NOW()'),
            ];
            
            try {
                \WHMCS\Database\Capsule::table('tblhosting')
                    ->where('id', $serviceid)
                    ->where('server', $serverid)
                    ->update($update);
            } catch (\Throwable $e) {
                // Ignore DB update errors for individual services
            }
        }
    }

    return 'success';
}

function spanel_LoginLink($params)
{
    $url = spanel_sso_url($params);
    return '<a href="' . htmlspecialchars($url, ENT_QUOTES, 'UTF-8') . '" target="_blank">Login to Control Panel</a>';
}

function spanel_ClientArea($params)
{
    $account = [];
    if (!empty($params['serviceid'])) {
        $result = spanel_request($params, 'GET', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)));
        if ($result['ok']) {
            $account = $result['data'];
        }
    }

    return [
        'templatefile' => 'clientarea',
        'vars' => [
            'panelUrl' => spanel_base_url($params),
            'loginUrl' => spanel_sso_url($params),
            'username' => spanel_username($params),
            'serviceLabel' => spanel_service_label($params, $account),
            'packageName' => trim((string) ($account['package_name'] ?? '')),
            'domain' => trim((string) ($account['domain'] ?? '')),
            'status' => trim((string) ($account['status'] ?? '')),
        ],
    ];
}

function spanel_request($params, $method, $path, $payload = null, $query = [])
{
    if (!function_exists('curl_init')) {
        return ['ok' => false, 'error' => 'PHP cURL extension is required'];
    }

    $token = trim((string) ($params['serveraccesshash'] ?? ''));
    if ($token === '') {
        $token = trim((string) ($params['serverpassword'] ?? ''));
    }
    if ($token === '') {
        return ['ok' => false, 'error' => 'Missing spanel API token in server Access Hash or Password'];
    }

    $url = spanel_base_url($params) . $path;
    if ($query) {
        $url .= '?' . http_build_query($query);
    }

    $headers = [
        'Authorization: Bearer ' . $token,
        'Accept: application/json',
    ];

    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST => strtoupper($method),
        CURLOPT_CONNECTTIMEOUT => 15,
        CURLOPT_TIMEOUT => 90,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ]);

    if ($payload !== null) {
        $body = json_encode($payload);
        curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        $headers[] = 'Content-Type: application/json';
        curl_setopt($ch, CURLOPT_HTTPHEADER, $headers);
    }

    $body = curl_exec($ch);
    $errno = curl_errno($ch);
    $error = curl_error($ch);
    $status = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);

    if ($errno) {
        spanel_log($params, $method, $path, $payload, null, 'cURL ' . $errno . ': ' . $error);
        return ['ok' => false, 'error' => 'spanel connection failed: ' . $error];
    }

    $data = json_decode((string) $body, true);
    if ($status < 200 || $status >= 300) {
        $message = '';
        if (is_array($data)) {
            // OPanel envelope error: {"detail": {"success": false, "error": "..."}}
            if (isset($data['detail']) && is_array($data['detail']) && isset($data['detail']['error'])) {
                $message = $data['detail']['error'];
            } elseif (isset($data['detail'])) {
                $message = is_array($data['detail']) ? json_encode($data['detail']) : (string) $data['detail'];
            }
        }
        if ($message === '') {
            $message = (string) $body;
        }
        spanel_log($params, $method, $path, $payload, $body, 'HTTP ' . $status . ': ' . $message);
        return ['ok' => false, 'error' => 'spanel API error: ' . $message];
    }

    // Unwrap provisioning envelope: {"success": true, "data": {...}, "error": null}
    if (is_array($data) && array_key_exists('data', $data) && array_key_exists('success', $data)) {
        if (!$data['success']) {
            $errMsg = $data['error'] ?? 'Unknown API error';
            spanel_log($params, $method, $path, $payload, $body, 'Envelope error: ' . $errMsg);
            return ['ok' => false, 'error' => 'spanel API error: ' . $errMsg];
        }
        $data = $data['data'];
    }

    spanel_log($params, $method, $path, $payload, $body, 'OK');
    return ['ok' => true, 'data' => is_array($data) ? $data : []];
}

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

function spanel_external_id($params)
{
    return 'whmcs:' . (int) ($params['serviceid'] ?? 0);
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
    return in_array(strtolower(trim($value)), ['on', 'yes', 'true', '1'], true);
}

function spanel_package_options($packages)
{
    if (!is_array($packages)) {
        throw new Exception('spanel package list response is invalid');
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
        throw new Exception('No spanel packages found');
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
        $product = 'spanel Hosting';
    }

    $serviceId = (int) ($params['serviceid'] ?? 0);
    return $serviceId > 0 ? $product . ' #' . $serviceId : $product;
}

function spanel_sso_url($params)
{
    if (empty($params['serviceid'])) {
        spanel_log($params, 'POST', '/api/provisioning/v1/accounts/{external_id}/login', null, null, 'Skipped: params[serviceid] is empty, falling back to plain panel URL (no SSO token requested)');
        return spanel_base_url($params);
    }

    $result = spanel_request($params, 'POST', '/api/provisioning/v1/accounts/' . rawurlencode(spanel_external_id($params)) . '/login');
    if ($result['ok'] && !empty($result['data']['login_url'])) {
        $url = trim((string) $result['data']['login_url']);
        if (preg_match('#^https?://#i', $url)) {
            return $url;
        }
        return rtrim(spanel_base_url($params), '/') . '/' . ltrim($url, '/');
    }

    if (!$result['ok']) {
        spanel_log($params, 'POST', '/api/provisioning/v1/accounts/' . spanel_external_id($params) . '/login', null, null, 'SSO link request failed, falling back to plain panel URL: ' . $result['error']);
    } else {
        spanel_log($params, 'POST', '/api/provisioning/v1/accounts/' . spanel_external_id($params) . '/login', null, $result['data'], 'SSO link request returned no login_url, falling back to plain panel URL');
    }

    return spanel_base_url($params);
}

function spanel_save_service_record($params, $data, $username = '', $password = '')
{
    if (!function_exists('localAPI') || empty($params['serviceid'])) {
        return;
    }

    $lines = ['spanel'];
    foreach (['service_label', 'external_id', 'username', 'email', 'domain', 'status', 'package_id', 'package_name', 'storage_used_bytes', 'storage_limit_bytes', 'storage_percent'] as $key) {
        if (array_key_exists($key, $data)) {
            $lines[] = $key . ': ' . (is_scalar($data[$key]) ? $data[$key] : json_encode($data[$key]));
        }
    }

    $update = [
        'serviceid' => (int) $params['serviceid'],
        'notes' => implode("\n", $lines),
    ];
    if ($username !== '') {
        $update['serviceusername'] = $username;
    }
    if ($password !== '') {
        $update['servicepassword'] = $password;
    }

    try {
        localAPI('UpdateClientProduct', $update);
    } catch (Throwable $e) {
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

function spanel_log($params, $method, $path, $request, $response, $result)
{
    if (!function_exists('logModuleCall')) {
        return;
    }

    $replaceVars = [trim((string) ($params['serveraccesshash'] ?? '')), trim((string) ($params['serverpassword'] ?? ''))];
    if (is_array($request) && isset($request['password'])) {
        $replaceVars[] = (string) $request['password'];
    }

    logModuleCall(
        'spanel',
        $method . ' ' . $path,
        $request,
        $response,
        $result,
        $replaceVars
    );
}
