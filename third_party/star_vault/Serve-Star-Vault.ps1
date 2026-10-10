param(
    [ValidateRange(1024, 65535)][int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = 'Stop'
$siteRoot = [System.IO.Path]::GetFullPath($PSScriptRoot)
$rootPrefix = $siteRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
$listener = New-Object System.Net.HttpListener
$siteUrl = "http://localhost:$Port/"
$listener.Prefixes.Add($siteUrl)
$mimeTypes = @{
    '.html' = 'text/html; charset=utf-8'
    '.js' = 'application/javascript; charset=utf-8'
    '.css' = 'text/css; charset=utf-8'
    '.json' = 'application/json; charset=utf-8'
    '.wasm' = 'application/wasm'
    '.png' = 'image/png'
    '.jpg' = 'image/jpeg'
    '.svg' = 'image/svg+xml'
    '.md' = 'text/plain; charset=utf-8'
    '.txt' = 'text/plain; charset=utf-8'
}

try {
    $listener.Start()
    Write-Host "Star Vault is running at $siteUrl" -ForegroundColor Cyan
    Write-Host 'Keep this window open. Press Ctrl+C to stop.'
    if (-not $NoBrowser) { Start-Process $siteUrl }

    while ($listener.IsListening) {
        $context = $listener.GetContext()
        $response = $context.Response
        $fileStream = $null
        try {
            $method = $context.Request.HttpMethod
            if ($method -ne 'GET' -and $method -ne 'HEAD') {
                $response.StatusCode = 405
                $response.Headers['Allow'] = 'GET, HEAD'
                continue
            }

            $relativePath = [Uri]::UnescapeDataString($context.Request.Url.AbsolutePath).TrimStart('/')
            if ([string]::IsNullOrEmpty($relativePath)) { $relativePath = 'index.html' }
            $fullPath = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine($siteRoot, $relativePath))
            if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                $response.StatusCode = 403
                continue
            }
            if (-not [System.IO.File]::Exists($fullPath)) {
                $response.StatusCode = 404
                continue
            }
            # Only public static assets are exposed; launcher files stay local.
            $extension = [System.IO.Path]::GetExtension($fullPath).ToLowerInvariant()
            if (-not $mimeTypes.ContainsKey($extension)) {
                $response.StatusCode = 403
                continue
            }

            $response.ContentType = $mimeTypes[$extension]
            $response.Headers['Cache-Control'] = 'no-cache'
            $fileStream = [System.IO.File]::OpenRead($fullPath)
            $response.ContentLength64 = $fileStream.Length
            if ($method -eq 'GET') { $fileStream.CopyTo($response.OutputStream) }
        }
        catch {
            Write-Warning $_.Exception.Message
            try { $response.StatusCode = 500 } catch {}
        }
        finally {
            if ($null -ne $fileStream) { $fileStream.Dispose() }
            $response.Close()
        }
    }
}
catch {
    Write-Host "Unable to start Star Vault: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host 'If the port is in use, close the previous server window, or run with -Port 8766.'
    exit 1
}
finally {
    $listener.Close()
}
