# ============================================================
#  MOM'S PC - REMOTE MALWARE REMEDIATION SCRIPT
#  Run this in PowerShell as Administrator on her machine.
#  Right-click PowerShell -> "Run as administrator", then:
#  Set-ExecutionPolicy Bypass -Scope Process -Force
#  Then paste or run this script.
# ============================================================

$ErrorActionPreference = "SilentlyContinue"
$DownloadDir = "$env:USERPROFILE\Desktop\CleanupTools"
New-Item -ItemType Directory -Force -Path $DownloadDir | Out-Null

# ============================================================
#  STEP 0: BACKUP BOOKMARKS + LIST EXTENSIONS
#  Must run BEFORE Chrome is uninstalled.
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 0: Backing up bookmarks + extensions..." -ForegroundColor Cyan
Write-Host "============================================`n"

$chromeProfile = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default"
$bookmarksSrc  = "$chromeProfile\Bookmarks"
$extensionsDir = "$chromeProfile\Extensions"

# --- BOOKMARKS ---
if (Test-Path $bookmarksSrc) {
    Write-Host "Reading Chrome bookmarks..." -ForegroundColor Yellow

    $json = Get-Content $bookmarksSrc -Raw | ConvertFrom-Json

    Add-Type -AssemblyName System.Web

    # Recursive function: converts Chrome JSON bookmark tree -> Netscape HTML lines
    function Convert-BookmarkNode {
        param($node, $indent = 0)
        $pad = "    " * $indent
        $lines = @()

        if ($node.type -eq "url") {
            $name = [System.Web.HttpUtility]::HtmlEncode($node.name)
            $url  = [System.Web.HttpUtility]::HtmlEncode($node.url)
            $lines += "$pad<DT><A HREF=`"$url`">$name</A>"
        }
        elseif ($node.type -eq "folder") {
            $name = [System.Web.HttpUtility]::HtmlEncode($node.name)
            $lines += "$pad<DT><H3>$name</H3>"
            $lines += "$pad<DL><p>"
            foreach ($child in $node.children) {
                $lines += Convert-BookmarkNode -node $child -indent ($indent + 1)
            }
            $lines += "$pad</DL><p>"
        }
        return $lines
    }

    $html  = @()
    $html += "<!DOCTYPE NETSCAPE-Bookmark-file-1>"
    $html += "<META HTTP-EQUIV=`"Content-Type`" CONTENT=`"text/html; charset=UTF-8`">"
    $html += "<TITLE>Bookmarks</TITLE>"
    $html += "<H1>Bookmarks</H1>"
    $html += "<DL><p>"

    foreach ($root in @("bookmark_bar", "other", "synced")) {
        $rootNode = $json.roots.$root
        if ($rootNode -and $rootNode.children.Count -gt 0) {
            $html += Convert-BookmarkNode -node $rootNode -indent 1
        }
    }
    $html += "</DL><p>"

    $bookmarksOut = "$DownloadDir\bookmarks_backup.html"
    $html | Out-File -FilePath $bookmarksOut -Encoding UTF8
    Write-Host "  [OK] Bookmarks exported -> Desktop\CleanupTools\bookmarks_backup.html" -ForegroundColor Green
} else {
    Write-Host "  [WARN] No Chrome bookmarks file found." -ForegroundColor Red
}

# --- EXTENSIONS ---
if (Test-Path $extensionsDir) {
    Write-Host "`nReading installed extensions..." -ForegroundColor Yellow

    # Known built-in Chrome extensions to skip
    $builtins = @(
        "nmmhkkegccagdldgiimedpiccmgmieda",
        "pkedcjkdefgpdelpbcmbmeomcjbeemfm",
        "ghbmnnjooekpmoecnnnilnnbdlolhkhi",
        "aapocclcgogkmnckokdopfmhonfmgoek",
        "aohghmighlieiainnegkcijnfilokake",
        "felcaaldnbdncclmgdcncolpebgiejap",
        "apdfllckaahabafndbhieahigkjlhalf",
        "blpcfgokakmgnkcojhhkbfbldkacnbeo",
        "coobgpohoikkiipiblmjeljniedjpjpf"
    )

    # Known safe user-facing extensions
    $knownSafe = @(
        "cjpalhdlnbpafiamejdnhcphjbkeiagm",  # uBlock Origin
        "ddkjiahejlhfcafbddmgiahcphecmpfh",  # uBlock Origin Lite
        "cfhdojbkjhnklbpkdaibdccddilifddb",  # Adblock Plus
        "gighmmpiobklfepjocnamgkkbiglidom",  # AdBlock
        "eimadpbcbfnmbkopoojfekhnkhdbieeh",  # Dark Reader
        "hdokiejnpimakedhajhdlcegeplioahd",  # LastPass
        "aeblfdkhhhdcdjpifhhbdiojplfjncoa",  # 1Password
        "fheoggkfdfchfphceeifdbepaooicaho",  # Microsoft Autofill
        "nngceckbapebfimnlniiiahkandclblb",  # Bitwarden
        "oocalimimngaihdkbihfgmpkcpnmlaoa",  # Honey
        "bmnlcjabgnpnenekpadlanbbkooimhnj",  # Rakuten
        "mnjggcdmjocbbbhaepdhchncahnbgone",  # SponsorBlock
        "lkbebcjgcmobggallkojdlmcogkbcena"   # Google Translate
    )

    $extReport = @()
    $extReport += "<!DOCTYPE html><html><head><meta charset='UTF-8'>"
    $extReport += "<title>Chrome Extensions Report</title>"
    $extReport += "<style>
        body { font-family: Segoe UI, sans-serif; background:#111827; color:#e2e8f0; padding:32px; }
        h1 { font-size:20px; margin-bottom:4px; }
        .sub { color:#64748b; font-size:13px; margin-bottom:24px; }
        table { width:100%; border-collapse:collapse; font-size:13px; }
        th { text-align:left; padding:10px 14px; background:#1e293b; color:#94a3b8; font-weight:600; border-bottom:2px solid #334155; }
        td { padding:9px 14px; border-bottom:1px solid #1e293b; vertical-align:middle; }
        tr:hover td { background:#1a2235; }
        .tag { display:inline-block; font-size:10px; font-weight:700; padding:2px 8px; border-radius:3px; letter-spacing:.04em; }
        .safe { background:rgba(16,185,129,.15); color:#10b981; border:1px solid rgba(16,185,129,.3); }
        .review { background:rgba(245,158,11,.12); color:#f59e0b; border:1px solid rgba(245,158,11,.3); }
        .builtin { background:rgba(100,116,139,.12); color:#64748b; border:1px solid rgba(100,116,139,.25); }
        .id { font-family:monospace; font-size:11px; color:#475569; }
        a { color:#3b82f6; text-decoration:none; }
        a:hover { text-decoration:underline; }
        .note { margin-top:20px; padding:14px 18px; background:#1e293b; border-left:3px solid #f59e0b; border-radius:4px; font-size:13px; color:#94a3b8; }
    </style></head><body>"
    $extReport += "<h1>Chrome Extensions — Pre-Wipe Report</h1>"
    $extReport += "<p class='sub'>Generated $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Review each extension before reinstalling in clean Chrome.</p>"
    $extReport += "<table><tr><th>Extension Name</th><th>Version</th><th>ID (click to open Chrome Web Store)</th><th>Verdict</th></tr>"

    Get-ChildItem $extensionsDir -Directory | ForEach-Object {
        $extId = $_.Name
        if ($extId.Length -ne 32) { return }

        $versionDir = Get-ChildItem $_.FullName -Directory |
            Sort-Object Name -Descending | Select-Object -First 1
        if (-not $versionDir) { return }

        $manifestPath = Join-Path $versionDir.FullName "manifest.json"
        if (-not (Test-Path $manifestPath)) { return }

        try { $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json }
        catch { return }

        $extName    = $manifest.name
        $extVersion = $manifest.version

        # Resolve localized names
        if ($extName -like "__MSG_*") {
            $msgKey = $extName -replace "^__MSG_(.+)__$", '$1'
            foreach ($locale in @("en_US", "en")) {
                $lp = Join-Path $versionDir.FullName "_locales\$locale\messages.json"
                if (Test-Path $lp) {
                    try {
                        $msgs = Get-Content $lp -Raw | ConvertFrom-Json
                        $resolved = $msgs.$msgKey.message
                        if ($resolved) { $extName = $resolved; break }
                    } catch {}
                }
            }
        }

        $storeUrl = "https://chromewebstore.google.com/detail/$extId"
        $safeName = [System.Web.HttpUtility]::HtmlEncode($extName)
        $safeVer  = [System.Web.HttpUtility]::HtmlEncode($extVersion)

        if ($builtins -contains $extId) {
            $tag = "<span class='tag builtin'>Built-in — skip</span>"
        } elseif ($knownSafe -contains $extId) {
            $tag = "<span class='tag safe'>Known safe</span>"
        } else {
            $tag = "<span class='tag review'>Review before reinstalling</span>"
        }

        $extReport += "<tr><td>$safeName</td><td>$safeVer</td>"
        $extReport += "<td class='id'><a href='$storeUrl' target='_blank'>$extId</a></td>"
        $extReport += "<td>$tag</td></tr>"
    }

    $extReport += "</table>"
    $extReport += "<div class='note'><strong>How to use this report:</strong> Built-in extensions are Chrome internals — ignore them. Known safe extensions can be reinstalled from the Chrome Web Store. Everything else marked <em>Review</em> — click the ID link to open its Web Store page and verify it's legitimate before reinstalling.</div>"
    $extReport += "</body></html>"

    $extOut = "$DownloadDir\extensions_report.html"
    $extReport | Out-File -FilePath $extOut -Encoding UTF8
    Write-Host "  [OK] Extension report -> Desktop\CleanupTools\extensions_report.html" -ForegroundColor Green
} else {
    Write-Host "  [WARN] Extensions folder not found." -ForegroundColor Red
}

# ============================================================
#  STEP 1: DOWNLOAD CLEANUP TOOLS
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 1: Downloading cleanup tools..." -ForegroundColor Cyan
Write-Host "============================================`n"

$adwUrl = "https://downloads.malwarebytes.com/file/adwcleaner"
$adwPath = "$DownloadDir\AdwCleaner.exe"
Write-Host "Downloading AdwCleaner..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $adwUrl -OutFile $adwPath -UseBasicParsing
    Write-Host "  [OK] AdwCleaner saved." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Download failed. Get it from malwarebytes.com/adwcleaner" -ForegroundColor Red
}

$hitmanUrl = "https://dl.surfright.nl/HitmanPro_x64.exe"
$hitmanPath = "$DownloadDir\HitmanPro.exe"
Write-Host "Downloading HitmanPro..." -ForegroundColor Yellow
try {
    Invoke-WebRequest -Uri $hitmanUrl -OutFile $hitmanPath -UseBasicParsing
    Write-Host "  [OK] HitmanPro saved." -ForegroundColor Green
} catch {
    Write-Host "  [WARN] Download failed. Get it from hitmanpro.com" -ForegroundColor Red
}

# ============================================================
#  STEP 2: NUKE CHROME + INFECTED DATA
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 2: Nuking Chrome + infected data..." -ForegroundColor Cyan
Write-Host "============================================`n"

Stop-Process -Name "chrome" -Force
Start-Sleep -Seconds 2

$chromeUninstall = (Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" |
    Where-Object { $_.DisplayName -like "*Google Chrome*" }).UninstallString
if ($chromeUninstall) {
    $chromeUninstall = $chromeUninstall -replace '"',''
    Start-Process -FilePath $chromeUninstall -ArgumentList "--uninstall --force-uninstall" -Wait
    Write-Host "  [OK] Chrome uninstalled." -ForegroundColor Green
} else {
    Write-Host "  [INFO] Chrome not found in registry. Uninstall manually via Settings > Apps." -ForegroundColor Yellow
}

foreach ($path in @("$env:LOCALAPPDATA\Google\Chrome", "$env:APPDATA\Google\Chrome")) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
        Write-Host "  [OK] Deleted $path" -ForegroundColor Green
    }
}

# ============================================================
#  STEP 3: SET DNS TO QUAD9
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 3: Setting DNS to Quad9..." -ForegroundColor Cyan
Write-Host "============================================`n"

Get-NetAdapter | Where-Object { $_.Status -eq "Up" } | ForEach-Object {
    Set-DnsClientServerAddress -InterfaceIndex $_.ifIndex -ServerAddresses ("9.9.9.9","149.112.112.112")
    Write-Host "  [OK] Quad9 DNS set on: $($_.Name)" -ForegroundColor Green
}
ipconfig /flushdns | Out-Null
Write-Host "  [OK] DNS cache flushed." -ForegroundColor Green

# ============================================================
#  STEP 4: LIST STARTUP ITEMS
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 4: Startup items (review these)..." -ForegroundColor Cyan
Write-Host "============================================`n"

Write-Host "--- HKCU Run ---" -ForegroundColor Magenta
Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" | Select-Object * -ExcludeProperty PS* | Format-List

Write-Host "--- HKLM Run ---" -ForegroundColor Magenta
Get-ItemProperty "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run" | Select-Object * -ExcludeProperty PS* | Format-List

Write-Host "--- Non-Microsoft Scheduled Tasks ---" -ForegroundColor Magenta
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike "\Microsoft*" -and $_.State -ne "Disabled" } |
    Select-Object TaskName, TaskPath, State | Format-Table -AutoSize

# ============================================================
#  STEP 5: REMOVE KNOWN BAD STARTUP ENTRIES
# ============================================================

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  STEP 5: Removing known bad startup entries..." -ForegroundColor Cyan
Write-Host "============================================`n"

$badKeys = @("OneLaunch","Adware","Elex","Wajam","SearchProtect","SupTab")
$runKeys = @(
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run",
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run"
)
foreach ($key in $runKeys) {
    $props = Get-ItemProperty $key -ErrorAction SilentlyContinue
    if ($props) {
        foreach ($prop in ($props.PSObject.Properties | Where-Object { $_.Name -notlike "PS*" })) {
            foreach ($bad in $badKeys) {
                if ($prop.Name -like "*$bad*" -or $prop.Value -like "*$bad*") {
                    Write-Host "  [REMOVING] $($prop.Name)" -ForegroundColor Red
                    Remove-ItemProperty -Path $key -Name $prop.Name -Force
                }
            }
        }
    }
}
Write-Host "  [OK] Done." -ForegroundColor Green

# ============================================================
#  STEP 6: EMPTY RECYCLE BIN
# ============================================================

Clear-RecycleBin -Force
Write-Host "`n  [OK] Recycle Bin emptied." -ForegroundColor Green

# ============================================================
#  SUMMARY
# ============================================================

Write-Host "`n============================================" -ForegroundColor Green
Write-Host "  AUTOMATED STEPS COMPLETE!" -ForegroundColor Green
Write-Host "============================================"
Write-Host ""
Write-Host "Saved to Desktop\CleanupTools\:" -ForegroundColor White
Write-Host "  bookmarks_backup.html   <- Import into new Chrome" -ForegroundColor Cyan
Write-Host "  extensions_report.html  <- Review before reinstalling" -ForegroundColor Cyan
Write-Host "  AdwCleaner.exe" -ForegroundColor Yellow
Write-Host "  HitmanPro.exe" -ForegroundColor Yellow
Write-Host ""
Read-Host "Press Enter to exit"
