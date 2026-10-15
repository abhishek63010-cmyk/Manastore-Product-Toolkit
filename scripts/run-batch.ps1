<#
.SYNOPSIS
    Permanent reusable batch runner for MANA Store Product Toolkit catalogues.

.DESCRIPTION
    Processes one or more product catalogues configured in a JSON manifest.
    Validates configuration, runs the pipeline sequentially for each entry,
    renames the final admin workbook to a catalogue-specific filename, preserves
    detailed logs and internal/diagnostic artifacts, and outputs a batch_summary.csv.

.PARAMETER ManifestPath
    Path to the JSON batch manifest. Defaults to config/batch-manifest.json.

.PARAMETER ValidateOnly
    If specified, validates manifest format, catalogue names, source paths,
    image directories, and supplier adapters without executing the pipeline.

.EXAMPLE
    .\scripts\run-batch.ps1 -ValidateOnly

.EXAMPLE
    .\scripts\run-batch.ps1

.EXAMPLE
    .\scripts\run-batch.ps1 -ManifestPath "config/my-batch.json"
#>

[CmdletBinding()]
param(
    [string]$ManifestPath = "config/batch-manifest.json",
    [switch]$ValidateOnly
)

$ErrorActionPreference = "Stop"

# --- 1. Locate Project Root & Environment ---
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
Set-Location $ProjectRoot

$MainPy = Join-Path $ProjectRoot "main.py"
if (-not (Test-Path $MainPy)) {
    Write-Error "CRITICAL: Cannot find main.py at expected project root: $ProjectRoot"
    exit 1
}

# Python executable resolution
$PythonExe = "python"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$AltVenvPython = "c:\Users\my pc\Projects\Abhishek\ManaStore-Product-Toolkit\.venv\Scripts\python.exe"

if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} elseif (Test-Path $AltVenvPython) {
    $PythonExe = $AltVenvPython
}

# --- 2. Resolve & Load Manifest ---
if (-not [System.IO.Path]::IsPathRooted($ManifestPath)) {
    $ManifestFullPath = Join-Path $ProjectRoot $ManifestPath
} else {
    $ManifestFullPath = $ManifestPath
}

if (-not (Test-Path $ManifestFullPath)) {
    Write-Error "ERROR: Batch manifest file not found: $ManifestFullPath"
    exit 1
}

try {
    $ManifestContent = Get-Content -Path $ManifestFullPath -Raw -Encoding UTF8
    $Manifest = $ManifestContent | ConvertFrom-Json
} catch {
    Write-Error "ERROR: Failed to parse JSON manifest at $ManifestFullPath : $_"
    exit 1
}

function Get-PrimaryDiscoverySource {
    param(
        [Parameter(Mandatory = $true)] [string]$CatalogueDir,
        [Parameter(Mandatory = $true)] [string[]]$SupportedExtensions
    )

    $skipNames = @("_internal", "_reports", "images", "product-images", "product_images", "__pycache__")
    $files = Get-ChildItem -Path $CatalogueDir -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object {
            $ext = $_.Extension.ToLower()
            $SupportedExtensions -contains $ext -and
            -not ($skipNames -contains $_.Directory.Name) -and
            -not (($_.FullName -split '[\\/]') | Where-Object { $skipNames -contains $_ })
        }

    if (-not $files -or $files.Count -eq 0) {
        return $null
    }

    $ranked = foreach ($file in $files) {
        $relativePath = ((Resolve-Path -Path $file.FullName -Relative) -replace '[\\/]', '/').TrimStart('./')
        $depth = ($relativePath.Split('/').Count) - 1
        $extRank = 99
        switch ($file.Extension.ToLower()) {
            ".xlsx" { $extRank = 0 }
            ".xls" { $extRank = 1 }
            ".csv" { $extRank = 2 }
            ".tsv" { $extRank = 3 }
            ".png" { $extRank = 4 }
            ".jpg" { $extRank = 5 }
            ".jpeg" { $extRank = 6 }
            ".webp" { $extRank = 7 }
            ".pdf" { $extRank = 8 }
            ".bmp" { $extRank = 9 }
            ".tiff" { $extRank = 10 }
            default { $extRank = 99 }
        }

        [PSCustomObject]@{
            File = $file
            RelativePath = $relativePath
            Depth = [int]$depth
            ExtRank = [int]$extRank
        }
    }

    $best = $ranked | Sort-Object Depth, ExtRank, RelativePath | Select-Object -First 1
    $sameTier = $ranked | Where-Object { $_.Depth -eq $best.Depth -and $_.ExtRank -eq $best.ExtRank }
    if ($sameTier.Count -gt 1) {
        $paths = ($sameTier | ForEach-Object { $_.File.FullName }) -join '; '
        throw "Ambiguous primary source file in '$CatalogueDir': $paths"
    }

    return $best.File.FullName
}

function Get-AutoDiscoveredCatalogueEntries {
    param(
        [Parameter(Mandatory = $true)] [string]$ProjectRoot,
        [Parameter(Mandatory = $true)] [string[]]$SupportedExtensions
    )

    $knownRoots = @(
        @{ Name = "Amra_wholesale"; Adapter = "amra"; Root = (Join-Path $ProjectRoot "input\Amra_wholesale") },
        @{ Name = "Mudhra_Ethnic"; Adapter = "mudhra_ethnic"; Root = (Join-Path $ProjectRoot "input\Mudhra_Ethnic") }
    )

    $catalogues = @()
    $knownInputRoot = Join-Path $ProjectRoot "input"
    $unsupported = @()
    $baseNameCounts = @{}

    if (Test-Path $knownInputRoot) {
        $rootDirs = Get-ChildItem -Path $knownInputRoot -Directory -ErrorAction SilentlyContinue
        foreach ($dir in $rootDirs) {
            $matched = $knownRoots | Where-Object { $_.Name -eq $dir.Name }
            if (-not $matched) {
                $candidate = Get-ChildItem -Path $dir.FullName -Recurse -File -ErrorAction SilentlyContinue |
                    Where-Object { $SupportedExtensions -contains $_.Extension.ToLower() }
                if ($candidate -and $candidate.Count -gt 0) {
                    $unsupported += $dir.Name
                }
            }
        }
    }

    foreach ($root in $knownRoots) {
        if (-not (Test-Path $root.Root)) { continue }

        $candidateDirs = Get-ChildItem -Path $root.Root -Recurse -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch '[/\\](_internal|_reports|images|product-images|product_images)[/\\]' -and
                $_.Name -notin @('_internal', '_reports', 'images', 'product-images', 'product_images')
            }

        foreach ($catDir in $candidateDirs) {
            $baseKey = $catDir.Name.ToLower()
            if (-not $baseNameCounts.ContainsKey($baseKey)) { $baseNameCounts[$baseKey] = 0 }
            $baseNameCounts[$baseKey]++
        }
    }

    foreach ($root in $knownRoots) {
        if (-not (Test-Path $root.Root)) { continue }

        $candidateDirs = Get-ChildItem -Path $root.Root -Recurse -Directory -Force -ErrorAction SilentlyContinue |
            Where-Object {
                $_.FullName -notmatch '[/\\](_internal|_reports|images|product-images|product_images)[/\\]' -and
                $_.Name -notin @('_internal', '_reports', 'images', 'product-images', 'product_images')
            }

        foreach ($catDir in $candidateDirs) {
            try {
                $sourcePath = Get-PrimaryDiscoverySource -CatalogueDir $catDir.FullName -SupportedExtensions $SupportedExtensions
            } catch {
                throw "Auto-discovery validation error for '$($catDir.FullName)': $_"
            }

            if (-not $sourcePath) { continue }

            $relativeSource = ((Resolve-Path -Path $sourcePath -Relative) -replace '[\\/]', '/').TrimStart('./')
            if ($relativeSource.StartsWith("input/")) {
                $candidateName = $catDir.Name
                if ($baseNameCounts[$catDir.Name.ToLower()] -gt 1) {
                    $candidateName = "$($root.Name)_$candidateName"
                }

                $catalogues += [PSCustomObject]@{
                    name = $candidateName
                    supplierFolder = $root.Name
                    supplierAdapter = $root.Adapter
                    source = $relativeSource
                    images = $null
                }
            }
        }
    }

    $nameCounts = @{}
    foreach ($entry in $catalogues) {
        $key = $entry.name.ToLower()
        if (-not $nameCounts.ContainsKey($key)) { $nameCounts[$key] = 0 }
        $nameCounts[$key]++
    }

    $normalizedCatalogues = @()
    foreach ($entry in $catalogues) {
        $name = $entry.name
        if ($nameCounts[$name.ToLower()] -gt 1) {
            $name = "$($entry.supplierFolder)_$name"
        }
        $normalizedCatalogues += [PSCustomObject]@{
            name = $name
            supplierFolder = $entry.supplierFolder
            supplierAdapter = $entry.supplierAdapter
            source = $entry.source
            images = $entry.images
        }
    }

    return [PSCustomObject]@{
        Catalogues = $normalizedCatalogues
        UnsupportedRoots = $unsupported
    }
}

# --- 3. Dynamic Adapter Validation List ---
# Known valid adapters and aliases supported by AdapterRegistry
$KnownAdapters = @(
    "generic",
    "generic_tabular",
    "amra",
    "amra_adapter",
    "amra_wholesale",
    "mudhra",
    "mudhra_ethnic",
    "mudhra_ethnic_adapter",
    "tabular",
    "default"
)

# Supported input file extensions recognized by main.py pipeline
$SupportedExtensions = @(
    ".csv",
    ".tsv",
    ".xlsx",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".bmp",
    ".tiff",
    ".pdf"
)

# --- 4. Validate Manifest Structure ---
$ValidationErrors = @()

$BatchName = if ($Manifest.batchName) { [string]$Manifest.batchName.Trim() } else { "BatchRun" }
$InvalidChars = [System.IO.Path]::GetInvalidFileNameChars()
$hasInvalid = $false
foreach ($ch in $InvalidChars) {
    if ($BatchName.Contains($ch)) {
        $hasInvalid = $true
        break
    }
}
if ([string]::IsNullOrWhiteSpace($BatchName) -or $hasInvalid) {
    $ValidationErrors += "Manifest 'batchName' is invalid or contains prohibited filesystem characters: '$BatchName'"
}

$AutoDiscover = Get-AutoDiscoveredCatalogueEntries -ProjectRoot $ProjectRoot -SupportedExtensions $SupportedExtensions
if ($AutoDiscover.UnsupportedRoots.Count -gt 0) {
    foreach ($root in $AutoDiscover.UnsupportedRoots | Sort-Object -Unique) {
        $ValidationErrors += "Unsupported supplier root detected under 'input/': '$root'. Supported supplier roots: Amra_wholesale, Mudhra_Ethnic."
    }
}

$ManifestCatalogues = @()
if ($Manifest.catalogues) {
    $ManifestCatalogues = @($Manifest.catalogues)
}

$DiscoveredCatalogues = @($AutoDiscover.Catalogues)
$CombinedCatalogues = @($ManifestCatalogues + $DiscoveredCatalogues)

if ($CombinedCatalogues.Count -eq 0) {
    $ValidationErrors += "Manifest must contain a non-empty 'catalogues' array with at least one catalogue entry, or there must be at least one discoverable supplier root under 'input/'."
}

$SeenNames = @{}
$CatalogueList = @()

if ($CombinedCatalogues.Count -gt 0) {
    $idx = 0
    foreach ($cat in $CombinedCatalogues) {
        $idx++
        $catName = if ($cat.name) { [string]$cat.name.Trim() } else { "" }
        $entryLabel = "Catalogue #" + $idx + " (" + $catName + ")"

        # Name validation
        if ([string]::IsNullOrWhiteSpace($catName)) {
            $ValidationErrors += ($entryLabel + ": 'name' cannot be empty or whitespace.")
            continue
        }

        if ($catName.StartsWith("<") -or $catName.StartsWith("[") -or $catName.EndsWith(">") -or $catName.EndsWith("]")) {
            $ValidationErrors += ($entryLabel + ": 'name' appears to be an unresolved placeholder: '" + $catName + "'")
        }

        if ($SeenNames.ContainsKey($catName.ToLower())) {
            $ValidationErrors += ($entryLabel + ": Duplicate catalogue name detected: '" + $catName + "'. Each catalogue must have a unique name.")
        } else {
            $SeenNames[$catName.ToLower()] = $true
        }

        # Adapter validation
        $adapter = if ($cat.supplierAdapter) { [string]$cat.supplierAdapter.Trim() } else { "" }
        if ([string]::IsNullOrWhiteSpace($adapter)) {
            $ValidationErrors += ($entryLabel + ": 'supplierAdapter' cannot be empty.")
        } else {
            $adapterNorm = $adapter.ToLower().Replace("-", "_")
            if ($KnownAdapters -notcontains $adapterNorm) {
                $ValidationErrors += ($entryLabel + ": Unsupported supplierAdapter '" + $adapter + "'. Supported adapters: " + ($KnownAdapters -join ', '))
            }
        }

        # Source path validation
        $rawSource = if ($cat.source) { [string]$cat.source.Trim() } else { "" }
        if ([string]::IsNullOrWhiteSpace($rawSource)) {
            $ValidationErrors += ($entryLabel + ": 'source' path cannot be empty.")
        } else {
            $resolvedSource = if ([System.IO.Path]::IsPathRooted($rawSource)) {
                $rawSource
            } else {
                Join-Path $ProjectRoot $rawSource
            }

            if (-not (Test-Path $resolvedSource -PathType Leaf)) {
                $ValidationErrors += ($entryLabel + ": Source file does not exist: '" + $resolvedSource + "' (configured: '" + $rawSource + "')")
            } else {
                $ext = [System.IO.Path]::GetExtension($resolvedSource).ToLower()
                if ($SupportedExtensions -notcontains $ext) {
                    $ValidationErrors += ($entryLabel + ": Unsupported source file extension '" + $ext + "'. Supported: " + ($SupportedExtensions -join ', '))
                }
            }
        }

        # Supplier-root validation for discovered catalogues and explicit config conflicts
        $supplierFolder = if ($cat.supplierFolder) { [string]$cat.supplierFolder.Trim() } else { "" }
        if ($rawSource) {
            $resolvedPath = if ([System.IO.Path]::IsPathRooted($rawSource)) { $rawSource } else { Join-Path $ProjectRoot $rawSource }
            $resolvedPathNorm = [System.IO.Path]::GetFullPath($resolvedPath)
            $pathParts = $resolvedPathNorm.Split([System.IO.Path]::DirectorySeparatorChar, [System.StringSplitOptions]::RemoveEmptyEntries)
            $rootMatch = $null
            foreach ($part in $pathParts) {
                if ($part -eq "Amra_wholesale" -or $part -eq "Mudhra_Ethnic") {
                    $rootMatch = $part
                    break
                }
            }
            if ($rootMatch) {
                $expectedAdapter = if ($rootMatch -eq "Amra_wholesale") { "amra" } else { "mudhra_ethnic" }
                $adapterNorm = $adapter.ToLower().Replace("-", "_")
                if ($adapterNorm -ne $expectedAdapter -and $adapterNorm -ne $expectedAdapter + "_adapter" -and $adapterNorm -ne $expectedAdapter + "_wholesale") {
                    $ValidationErrors += ($entryLabel + ": Explicit supplierAdapter '" + $adapter + "' conflicts with supplier root '" + $rootMatch + "'. Expected adapter: '" + $expectedAdapter + "'.")
                }
            }
        }

        # Images path validation (optional)
        $rawImages = if ($cat.images) { [string]$cat.images.Trim() } else { "" }
        $resolvedImages = $null
        if (-not [string]::IsNullOrWhiteSpace($rawImages)) {
            $resolvedImages = if ([System.IO.Path]::IsPathRooted($rawImages)) {
                $rawImages
            } else {
                Join-Path $ProjectRoot $rawImages
            }

            if (-not (Test-Path $resolvedImages)) {
                $ValidationErrors += ($entryLabel + ": Configured images folder does not exist: '" + $resolvedImages + "' (configured: '" + $rawImages + "')")
            }
        }

        $CatalogueList += [PSCustomObject]@{
            Index = $idx
            Name = $catName
            SupplierFolder = $supplierFolder
            SupplierAdapter = $adapter
            SourceRaw = $rawSource
            SourceResolved = $resolvedSource
            ImagesRaw = $rawImages
            ImagesResolved = $resolvedImages
        }
    }
}

# --- 5. Display Pre-Run Summary ---
Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " MANA STORE PRODUCT TOOLKIT - BATCH RUNNER" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Project Root:  $ProjectRoot"
Write-Host " Manifest File: $ManifestFullPath"
Write-Host " Batch Name:    $BatchName"
Write-Host " Catalogues:    $($CatalogueList.Count) configured"
if ($AutoDiscover.Catalogues.Count -gt 0) {
    Write-Host " Auto-Discovered Input Catalogues: $($AutoDiscover.Catalogues.Count)"
}
Write-Host "--------------------------------------------------------------------------------"

foreach ($c in $CatalogueList) {
    Write-Host " [$($c.Index)] $($c.Name)" -ForegroundColor Yellow
    Write-Host "     Adapter: $($c.SupplierAdapter)"
    Write-Host "     Source:  $($c.SourceRaw)"
    if ($c.ImagesRaw) {
        Write-Host "     Images:  $($c.ImagesRaw)"
    } else {
        Write-Host "     Images:  (None - optional)" -ForegroundColor DarkGray
    }
}
Write-Host "================================================================================" -ForegroundColor Cyan

# Report validation errors if any
if ($ValidationErrors.Count -gt 0) {
    Write-Host ""
    Write-Host "VALIDATION FAILED ($($ValidationErrors.Count) error(s) found):" -ForegroundColor Red
    foreach ($err in $ValidationErrors) {
        Write-Host "  - $err" -ForegroundColor Red
    }
    Write-Host ""
    exit 1
}

Write-Host " Manifest validation: PASSED" -ForegroundColor Green

if ($ValidateOnly) {
    Write-Host ""
    Write-Host "ValidateOnly switch was requested. Skipping execution." -ForegroundColor Yellow
    Write-Host "Batch manifest is valid and ready to run." -ForegroundColor Green
    exit 0
}

# --- 6. Create Batch Run Directory ---
$Timestamp = (Get-Date).ToString("yyyyMMdd_HHmmss")
$BatchFolderName = "${BatchName}_${Timestamp}"
$BatchOutputDir = Join-Path (Join-Path $ProjectRoot "output") $BatchFolderName

New-Item -ItemType Directory -Path $BatchOutputDir -Force | Out-Null
Write-Host " Created Batch Output Directory: $BatchOutputDir" -ForegroundColor Green
Write-Host ""

# --- 7. Execute Batch Sequentially ---
$SummaryResults = @()
$OverallSuccess = $true

foreach ($c in $CatalogueList) {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $paddedIdx = ("{0:D2}" -f $c.Index)
    $catFolderName = "${paddedIdx}_$($c.Name)"
    $catOutputDir = Join-Path $BatchOutputDir $catFolderName
    New-Item -ItemType Directory -Path $catOutputDir -Force | Out-Null

    $logFile = Join-Path $catOutputDir "execution.log"

    Write-Host "--------------------------------------------------------------------------------"
    Write-Host " PROCESSING [$($c.Index)/$($CatalogueList.Count)]: $($c.Name)" -ForegroundColor Cyan
    Write-Host " Output Dir: $catOutputDir"

    # Build main.py CLI arguments with proper quoting
    $cliArgList = @(
        "`"$MainPy`"",
        "--input", "`"$($c.SourceResolved)`"",
        "--supplier", "`"$($c.SupplierAdapter)`"",
        "--output", "`"$catOutputDir`"",
        "--fail-on-error",
        "--verbose"
    )

    if ($c.ImagesResolved) {
        $cliArgList += "--images"
        $cliArgList += "`"$($c.ImagesResolved)`""
    }

    $cmdLineDisplay = "& `"$PythonExe`" " + ($cliArgList -join ' ')
    Add-Content -Path $logFile -Value "Command: $cmdLineDisplay`n" -Encoding UTF8

    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $PythonExe
    $psi.Arguments = ($cliArgList -join ' ')
    $psi.WorkingDirectory = $ProjectRoot
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null

    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()

    $process.WaitForExit()
    $stdoutText = $stdoutTask.Result
    $stderrText = $stderrTask.Result
    $exitCode = $process.ExitCode
    $sw.Stop()
    $durationSec = [Math]::Round($sw.Elapsed.TotalSeconds, 2)

    if ($stdoutText) {
        Add-Content -Path $logFile -Value $stdoutText -Encoding UTF8
    }
    if ($stderrText) {
        $stderrPath = Join-Path $catOutputDir "stderr.log"
        Set-Content -Path $stderrPath -Value $stderrText -Encoding UTF8
        Add-Content -Path $logFile -Value "`n[STDERR]:`n$stderrText" -Encoding UTF8
    }

    # Inspect produced workbooks
    $rawAmraPath = Join-Path $catOutputDir "amra_data_ready.xlsx"
    $targetWorkbookName = "${catFolderName}.xlsx"
    $targetFinalPath = Join-Path $catOutputDir $targetWorkbookName
    $catSuccess = $false
    $errorSummary = ""

    if ($exitCode -eq 0) {
        # The internal canonical workbook has a different schema and is never the import file.
        if (Test-Path $rawAmraPath) {
            try {
                if (Test-Path $targetFinalPath) {
                    Remove-Item -Path $targetFinalPath -Force
                }
                Rename-Item -Path $rawAmraPath -NewName $targetWorkbookName
                $catSuccess = $true
            } catch {
                $errorSummary = "Failed to rename amra_data_ready.xlsx to $targetWorkbookName`: $_"
                $catSuccess = $false
            }
        } elseif (Test-Path $targetFinalPath) {
            $catSuccess = $true
        } else {
            $errorSummary = "Nominal success (exit code 0), but final workbook 'amra_data_ready.xlsx' was not created."
            $catSuccess = $false
        }
    } else {
        $errorSummary = "Pipeline failed with exit code $exitCode. See execution.log for details."
        $catSuccess = $false
    }

    # Update RUN_SUMMARY.txt to reference the catalogue-specific workbook.
    $runSummaryFile = Join-Path $catOutputDir "RUN_SUMMARY.txt"
    if (Test-Path $runSummaryFile) {
        try {
            $content = Get-Content -Path $runSummaryFile -Raw -Encoding UTF8
            $updated = $content.Replace("amra_data_ready.xlsx", $targetWorkbookName)
            Set-Content -Path $runSummaryFile -Value $updated -Encoding UTF8
        } catch {
            # Non-fatal if summary file text replacement fails
        }
    }

    if ($catSuccess) {
        Write-Host " Status: SUCCESS ($durationSec s)" -ForegroundColor Green
        Write-Host " Final Workbook: $targetFinalPath" -ForegroundColor Green
    } else {
        Write-Host " Status: FAILED ($durationSec s)" -ForegroundColor Red
        Write-Host " Error: $errorSummary" -ForegroundColor Red
        $OverallSuccess = $false
    }

    $SummaryResults += [PSCustomObject]@{
        CatalogueIndex = $c.Index
        CatalogueName = $c.Name
        Status = if ($catSuccess) { "SUCCESS" } else { "FAILED" }
        ExitCode = $exitCode
        DurationSeconds = $durationSec
        SupplierAdapter = $c.SupplierAdapter
        SourcePath = $c.SourceRaw
        ImagesPath = if ($c.ImagesRaw) { $c.ImagesRaw } else { "N/A" }
        OutputDir = $catOutputDir
        FinalWorkbook = if ($catSuccess) { $targetFinalPath } else { "N/A" }
        ErrorSummary = $errorSummary
    }
}

# --- 8. Write batch_summary.csv at Batch Root ---
$BatchSummaryCsv = Join-Path $BatchOutputDir "batch_summary.csv"
$SummaryResults | Export-Csv -Path $BatchSummaryCsv -NoTypeInformation -Encoding UTF8

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " BATCH EXECUTION SUMMARY" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host " Batch Root Directory: $BatchOutputDir"
Write-Host " Batch Summary CSV:    $BatchSummaryCsv"
Write-Host ""

$SummaryResults | Format-Table CatalogueIndex, CatalogueName, Status, DurationSeconds, FinalWorkbook -AutoSize

$successMatches = @($SummaryResults | Where-Object { $_.Status -eq "SUCCESS" })
$successCount = $successMatches.Count
$totalCount = $SummaryResults.Count
Write-Host " Completed: $successCount / $totalCount succeeded."

if ($OverallSuccess) {
    Write-Host ""
    Write-Host " All configured catalogues processed successfully!" -ForegroundColor Green
    exit 0
} else {
    Write-Host ""
    Write-Host " One or more catalogues failed during batch execution." -ForegroundColor Red
    exit 1
}
