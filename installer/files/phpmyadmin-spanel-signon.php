<?php
// SPanel phpMyAdmin single sign-on.
//
// Installed as /usr/share/phpmyadmin/spanel-signon.php by install.sh and
// update.sh, which replace the two __SPANEL_..__ placeholders. The panel
// hands the browser a one-shot token; this script trades it, over loopback,
// for the database credentials and the phpMyAdmin server to open: 1 is the
// local MariaDB, 2 and up are the remote MySQL hosts listed in
// /etc/phpmyadmin/conf.d/spanel-remote-servers.php. Each server entry carries
// its own host, so the session never overrides it.
declare(strict_types=1);

session_save_path('/var/lib/php/sessions');
ini_set('session.use_cookies', 'true');
$isHttpsRequest = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off')
    || (strtolower((string)($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '')) === 'https')
    || ((string)($_SERVER['SERVER_PORT'] ?? '') === '443');
$cookieSecure = __SPANEL_PMA_COOKIE_SECURE__ ? $isHttpsRequest : $isHttpsRequest;
session_set_cookie_params([
    'lifetime' => 0,
    'path' => '/',
    'domain' => '',
    'secure' => $cookieSecure,
    'httponly' => true,
    'samesite' => 'Lax',
]);
session_name('SPanelPmaSignon');
if (!session_start()) {
    http_response_code(500);
    exit('Cannot start signon session');
}

$token = $_GET['spanel_sso'] ?? '';
if (!is_string($token) || !preg_match('/^[A-Za-z0-9_-]{20,}$/', $token)) {
    http_response_code(403);
    exit('Invalid token');
}

$configuredBase = '__SPANEL_API_BASE__';
$candidateBases = [$configuredBase];
if (strpos($configuredBase, 'http://') === 0) {
    $candidateBases[] = 'https://' . substr($configuredBase, 7);
} elseif (strpos($configuredBase, 'https://') === 0) {
    $candidateBases[] = 'http://' . substr($configuredBase, 8);
}
$candidateBases[] = 'https://127.0.0.1:2222/api/databases/phpmyadmin-sso/';
$candidateBases[] = 'http://127.0.0.1:2222/api/databases/phpmyadmin-sso/';
$candidateBases[] = 'http://127.0.0.1:8000/api/databases/phpmyadmin-sso/';
$candidateBases = array_values(array_unique($candidateBases));

$response = false;
$status = 0;
foreach ($candidateBases as $baseUrl) {
    $apiUrl = $baseUrl . rawurlencode($token);
    $ch = curl_init($apiUrl);
    curl_setopt_array($ch, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 5,
        CURLOPT_SSL_VERIFYPEER => false,
        CURLOPT_SSL_VERIFYHOST => false,
        CURLOPT_HTTPHEADER => ['Accept: application/json'],
    ]);
    $res = curl_exec($ch);
    $code = (int)curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($code === 200 && $res) {
        $response = $res;
        $status = 200;
        break;
    }
    if ($code === 404) {
        $status = 404;
    }
}

if ($status !== 200 || !$response) {
    http_response_code(403);
    exit('Expired token');
}

$data = json_decode($response, true);
if (!is_array($data) || empty($data['db_user']) || empty($data['db_password'])) {
    http_response_code(403);
    exit('Invalid signon data');
}

$server = 1;
if (isset($data['server']) && is_int($data['server']) && $data['server'] >= 1 && $data['server'] <= 99) {
    $server = $data['server'];
}

session_regenerate_id(true);
$_SESSION = [];
$_SESSION['PMA_single_signon_user'] = $data['db_user'];
$_SESSION['PMA_single_signon_password'] = $data['db_password'];
$_SESSION['PMA_single_signon_cfgupdate'] = [
    'only_db' => $data['db_name'] ?? '',
];
$_SESSION['PMA_single_signon_HMAC_secret'] = bin2hex(random_bytes(16));
session_write_close();

header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('Location: /phpmyadmin/index.php?server=' . $server);
exit;
