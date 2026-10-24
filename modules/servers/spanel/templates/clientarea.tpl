<div class="panel panel-default card">
    <div class="panel-heading card-header">
        <h3 class="panel-title card-title">{$serviceLabel|escape}</h3>
    </div>
    <div class="panel-body card-body">
        {if $packageName}
            <p><strong>Package:</strong> {$packageName|escape}{if $isReseller} (Reseller){/if}</p>
        {/if}
        {if $status}
            <p><strong>Status:</strong> {$status|escape}</p>
        {/if}
        <p><strong>Username (panel and SFTP):</strong> {$username|escape}</p>
        <p><strong>Primary domain:</strong> {if $domain}{$domain|escape}{else}Not attached{/if}</p>
        {if $diskUsage}
            <p><strong>Disk:</strong> {$diskUsage|escape}</p>
        {/if}
        {if $bandwidthUsage}
            <p><strong>Bandwidth this month:</strong> {$bandwidthUsage|escape}</p>
        {/if}
        {if $websiteCount}
            <p><strong>Websites:</strong> {$websiteCount|escape}</p>
        {/if}
        {if $isReseller && $resellerUserCount}
            <p><strong>Customer accounts:</strong> {$resellerUserCount|escape}</p>
        {/if}
        <p>
            <a class="btn btn-primary" href="{$loginUrl|escape}" target="_blank" rel="noopener">
                Login to SPanel
            </a>
        </p>
    </div>
</div>
