[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$envPath = Join-Path $repoRoot '.env'

& git -C $repoRoot check-ignore --quiet -- '.env'
if ($LASTEXITCODE -ne 0) {
    throw 'The repository .env is not ignored by Git; refusing to write a key.'
}
& git -C $repoRoot ls-files --error-unmatch -- '.env' *> $null
if ($LASTEXITCODE -eq 0) {
    throw 'The repository .env is tracked by Git; refusing to write a key.'
}

$secureKey = Read-Host 'Enter the existing OpenAI API key (input is masked)' -AsSecureString
$pointer = [IntPtr]::Zero
$key = $null
try {
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
    $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    if ($key -notmatch '^sk-[A-Za-z0-9_-]{20,}$') {
        throw 'The input does not look like an OpenAI API key; no file was changed.'
    }

    if (-not (Test-Path -LiteralPath $envPath)) {
        [IO.File]::WriteAllText($envPath, '', [Text.UTF8Encoding]::new($false))
    }
    if ((Get-Item -Force -LiteralPath $envPath).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'The local .env is a link; refusing to write the key through it.'
    }

    $acl = Get-Acl -LiteralPath $envPath
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($existingRule in @($acl.Access)) {
        [void]$acl.RemoveAccessRuleAll($existingRule)
    }
    $allowedSids = @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User,
        [Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    )
    foreach ($sid in $allowedSids) {
        $rule = [Security.AccessControl.FileSystemAccessRule]::new(
            $sid, [Security.AccessControl.FileSystemRights]::FullControl,
            [Security.AccessControl.AccessControlType]::Allow
        )
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $envPath -AclObject $acl

    $lines = @()
    if ((Get-Item -LiteralPath $envPath).Length -gt 0) {
        $lines = [IO.File]::ReadAllLines($envPath, [Text.UTF8Encoding]::new($false))
    }
    $lines = @($lines | Where-Object { $_ -notmatch '^\s*(?:export\s+)?OPENAI_API_KEY\s*=' })
    $lines += "OPENAI_API_KEY=$key"
    [IO.File]::WriteAllText(
        $envPath, (($lines -join [Environment]::NewLine) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )

    Write-Output 'OpenAI API key stored in the ignored local .env with user/SYSTEM/Administrators-only access. The value was not displayed.'
}
finally {
    if ($pointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
    $key = $null
    $secureKey = $null
}
