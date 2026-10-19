<div class="panel panel-default">
    <div class="panel-heading">
        <h3 class="panel-title">{$serviceLabel|escape}</h3>
    </div>
    <div class="panel-body">
        {if $packageName}
            <p><strong>Package:</strong> {$packageName|escape}</p>
        {/if}
        {if $status}
            <p><strong>Status:</strong> {$status|escape}</p>
        {/if}
        <p><strong>Username:</strong> {$username|escape}</p>
        <p><strong>Primary domain:</strong> {if $domain}{$domain|escape}{else}Not attached{/if}</p>
        <p>
            <a class="btn btn-primary" href="{$loginUrl|escape}" target="_blank" rel="noopener">
                Login to SPanel
            </a>
        </p>
    </div>
</div>
