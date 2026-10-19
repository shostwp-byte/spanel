<?php

if (!defined('WHMCS')) {
    die('This file cannot be accessed directly');
}

use Illuminate\Database\Capsule\Manager as Capsule;

add_hook('ClientAreaPageProductsServices', 1, function ($vars) {
    if (empty($vars['services']) || !is_array($vars['services'])) {
        return [];
    }

    $serviceIds = [];
    foreach ($vars['services'] as $service) {
        $id = spanel_hook_service_id($service);
        if ($id > 0) {
            $serviceIds[] = $id;
        }
    }
    if ($serviceIds === []) {
        return [];
    }

    $labels = spanel_hook_service_labels($serviceIds);
    if ($labels === []) {
        return [];
    }

    $services = $vars['services'];
    foreach ($services as $index => $service) {
        $id = spanel_hook_service_id($service);
        if (!isset($labels[$id])) {
            continue;
        }
        $services[$index] = spanel_hook_apply_service_label($service, $labels[$id]);
    }

    return ['services' => $services];
});

add_hook('ClientAreaPageProductDetails', 1, function ($vars) {
    $serviceId = (int) ($vars['serviceid'] ?? $vars['id'] ?? 0);
    if ($serviceId <= 0) {
        return [];
    }

    $labels = spanel_hook_service_labels([$serviceId]);
    if (empty($labels[$serviceId])) {
        return [];
    }

    return [
        'product' => $labels[$serviceId],
        'serviceLabel' => $labels[$serviceId],
    ];
});

function spanel_hook_service_id($service)
{
    if (is_array($service)) {
        return (int) ($service['id'] ?? $service['serviceid'] ?? 0);
    }
    if (is_object($service)) {
        return (int) ($service->id ?? $service->serviceid ?? 0);
    }
    return 0;
}

function spanel_hook_note_value($notes, $key)
{
    foreach (preg_split('/\r\n|\r|\n/', $notes) as $line) {
        if (strpos($line, $key . ':') === 0) {
            return trim(substr($line, strlen($key) + 1));
        }
    }
    return '';
}

function spanel_hook_service_labels($serviceIds)
{
    try {
        $rows = Capsule::table('tblhosting as h')
            ->join('tblproducts as p', 'p.id', '=', 'h.packageid')
            ->whereIn('h.id', $serviceIds)
            ->where('p.servertype', 'spanel')
            ->get(['h.id', 'h.username', 'h.notes', 'p.name as product_name']);
    } catch (Throwable $e) {
        return [];
    }

    $labels = [];
    foreach ($rows as $row) {
        $label = spanel_hook_note_value((string) $row->notes, 'service_label');
        if ($label === '') {
            $label = spanel_hook_service_fallback_label((string) $row->product_name, (int) $row->id, (string) $row->username);
        }
        $username = trim((string) $row->username);
        if ($username !== '' && strpos($label, $username) === false) {
            $label .= ' - ' . $username;
        }
        $labels[(int) $row->id] = $label;
    }

    return $labels;
}

function spanel_hook_apply_service_label($service, $label)
{
    if (is_array($service)) {
        $service['product'] = $label;
        $service['productname'] = $label;
        $service['serviceLabel'] = $label;
        return $service;
    }
    if (is_object($service)) {
        $service->product = $label;
        $service->productname = $label;
        $service->serviceLabel = $label;
    }
    return $service;
}

function spanel_hook_service_fallback_label($productName, $serviceId, $username)
{
    $label = trim($productName) !== '' ? trim($productName) : 'spanel Hosting';
    if ($serviceId > 0) {
        $label .= ' #' . $serviceId;
    }
    $username = trim($username);
    return $username !== '' ? $label . ' - ' . $username : $label;
}
