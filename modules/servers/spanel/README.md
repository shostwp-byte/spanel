# spanel WHMCS server module

Compatible with WHMCS on PHP 8.1+.

## Install

Copy this directory to WHMCS:

```text
modules/servers/spanel/
```

## spanel token

In spanel admin API, create provisioning token with scopes:

```text
provisioning:read,provisioning:write
```

## WHMCS server config

Go to **System Settings → Servers → Add New Server**.

| Field | Value |
|---|---|
| Module | spanel Hosting |
| Hostname | panel domain or IP |
| IP Address | optional |
| Assigned IP Addresses | empty |
| NS fields | empty |
| Type | spanel Hosting |
| Username | empty |
| Password | spanel API token, if not using Access Hash |
| Access Hash | spanel API token |
| Secure | checked if HTTPS |
| Port | spanel port, usually `2222` |

## Product config

Go to **System Settings → Products/Services → Module Settings**.

| Option | Example |
|---|---|
| Package | select a spanel package |
| App Type | `php` |
| PHP Version | `8.4` |
| Install WordPress | unchecked by default |
| Auto SSL | unchecked by default |

Disable **Require Domain** on the WHMCS product if customers should be able to order without entering a domain.
If **Install WordPress** or **Auto SSL** is enabled, a domain is still required.
When no domain is provided, spanel creates only the panel user; the App Type setting is ignored until a website is added later.
Provisioning generates a unique internal spanel email alias per service. Customers log in with the spanel username.
The module stores the generated spanel username and password on the WHMCS service before returning success, so WHMCS welcome emails can include service credentials.
The module also supports one-time SSO login links through the spanel provisioning API.
After replacing the module files, save the WHMCS product Module Settings once so WHMCS reloads the service-list hook.

## Mapping

| WHMCS | spanel |
|---|---|
| Service ID | `external_id = whmcs:{serviceid}` |
| Username | generated and stored on the WHMCS service |
| Email | generated internal alias per service |
| Domain | Primary website domain, optional |
| Product Package | spanel `UserPackage.id` |

## Supported actions

- CreateAccount
- SuspendAccount
- UnsuspendAccount
- TerminateAccount
- ChangePassword
- ChangePackage
- UsageUpdate
- LoginLink
- ClientArea
- TestConnection
