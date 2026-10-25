# SPanel WHMCS server module

Compatible with WHMCS 7.x / 8.x on PHP 7.4 – 8.3. No dependencies besides
the PHP cURL extension. Full API reference: [`docs/API_AND_WHMCS.md`](../../../docs/API_AND_WHMCS.md).

## Install

Copy this directory to WHMCS:

```text
<whmcs>/modules/servers/spanel/
├── spanel.php
├── hooks.php
└── templates/clientarea.tpl
```

## SPanel token

In SPanel, **Settings → API & Billing Integration**, create a token with
either the legacy scopes

```text
provisioning:read,provisioning:write
```

or the fine-grained ones

```text
plans:read,accounts:read,accounts:write,usage:read,sso
```

Add `sso:admin` only if WHMCS admins should get the server-level
"Login to SPanel as Admin" button: it signs in as the SPanel administrator
who created the token. Restrict the token to the WHMCS server's address
(CIDR ranges allowed) and give it an expiry if your policy wants rotation.

## WHMCS server config

**System Settings → Servers → Add New Server**

| Field | Value |
|---|---|
| Module | SPanel Hosting |
| Hostname | panel domain or IP |
| Access Hash | SPanel API token (or put it in Password) |
| Secure | checked if the panel uses HTTPS |
| Port | `2222` (default) |

**Test Connection** must succeed before products can be configured.

## Product config (Module Settings)

| # | Option | Notes |
|---|---|---|
| 1 | Package | SPanel package (loaded from the panel) |
| 2 | App Type | `php`, `static`, `wordpress` |
| 3 | PHP Version | |
| 4 | Install WordPress | needs a domain |
| 5 | Auto SSL | needs a domain |
| 6 | Is Reseller | create a reseller account |
| 7 | Reseller Package | reseller quota package (with Is Reseller) |
| 8 | Backup Before Terminate | full backup first; terminate waits for it |

### Configurable options → limits

Configurable options with these names override the package limits for the
service (on create and on upgrade/downgrade):

| Option name (case-insensitive) | SPanel limit | Value |
|---|---|---|
| Disk Space, Disk, Storage, Web Space | disk (MB) | `2048`, `10 GB`, or a number with "(GB)" in the option name |
| Bandwidth, Traffic, Monthly Bandwidth | monthly bandwidth (MB) | same as disk |
| Websites, Sites, Domains, Addon Domains | websites | count |

`Unlimited` (or 0) means unlimited.

### SFTP password

SPanel gives every account its own SFTP/SSH password, separate from the
panel password, and keeps no readable copy. Create a product custom field
named **SFTP Password** (text, tick *Admin Only* if customers should not see
it) and the module stores the password there on create, when a password
change rotates it, and from the **Reset SFTP Password** button.

## Supported functions

CreateAccount, SuspendAccount, UnsuspendAccount, TerminateAccount,
ChangePassword, ChangePackage, UsageUpdate (disk + monthly bandwidth),
TestConnection, ServiceSingleSignOn, AdminSingleSignOn, ListAccounts and
GetUserCount (server sync / import), AdminServicesTabFields,
AdminCustomButtonArray and ClientAreaCustomButtonArray (Reset SFTP Password,
Create Backup), ClientArea, LoginLink.

## Mapping

| WHMCS | SPanel |
|---|---|
| Service ID | `external_id = whmcs:{serviceid}` |
| Username | generated `sp{serviceid}_xxxxxxxx`, stored on the service |
| Email | generated internal alias per service |
| Domain | primary website, optional |
| configoption1 | `UserPackage.id` |

A create that times out is safe to retry: the module saves the generated
credentials before calling SPanel and sends an `Idempotency-Key`, so the
retry gets the account (and the one-time SFTP password) the first call made.

## Development

```bash
php -l modules/servers/spanel/spanel.php
php tests/php/spanel_module_test.php
```
