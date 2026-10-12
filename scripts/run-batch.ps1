<#
.SYNOPSIS
    Permanent reusable batch runner for MANA Store Product Toolkit catalogues.

.DESCRIPTION
    Processes one or more product catalogues configured in a JSON manifest.
    Validates configuration, runs the pipeline sequentially for each entry,
    renames the final admin workbook to manastore_pre_ready.xlsx, preserves
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

# --- 3. Dynamic Adapter Validation List ---
# Known valid adapters and aliases supported by AdapterRegistry
$KnownAdapters = @(
    "generic",
    "generic_tabular",
    "amra",
    "amra_adapter",
    "amra_wholesale",
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

if (-not $Manifest.catalogues -or $Manifest.catalogues.Count -eq 0) {
    $ValidationErrors += "Manifest must contain a non-empty 'catalogues' array with at least one catalogue entry."
}

$SeenNames = @{}
$CatalogueList = @()

if ($Manifest.catalogues) {
    $idx = 0
    foreach ($cat in $Manifest.catalogues) {
        $idx++
        $catName = if ($cat.name) { [string]$cat.name.Trim() } else { "" }
        $entryLabel = "Catalogue #" + $idx + " (" + $catName + ")"

        # Name validation
        if ([string]::IsNullOrWhiteSpace($catName)) {
            $ValidationErrors += ($entryLabel + ": 'name' cannot be empty or whitespace.")
            continue
        }

        # Check placeholder tokens
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
            SupplierFolder = if ($cat.supplierFolder) { [string]$cat.supplierFolder.Trim() } else { "" }
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
    $targetFinalPath = Join-Path $catOutputDir "manastore_pre_ready.xlsx"
    $catSuccess = $false
    $errorSummary = ""

    if ($exitCode -eq 0) {
        # Check if amra_data_ready.xlsx was generated and rename it safely
        if (Test-Path $rawAmraPath) {
            try {
                if (Test-Path $targetFinalPath) {
                    Remove-Item -Path $targetFinalPath -Force
                }
                Rename-Item -Path $rawAmraPath -NewName "manastore_pre_ready.xlsx"
                $catSuccess = $true
            } catch {
                $errorSummary = "Failed to rename amra_data_ready.xlsx to manastore_pre_ready.xlsx: $_"
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

    # Update RUN_SUMMARY.txt to reference manastore_pre_ready.xlsx
    $runSummaryFile = Join-Path $catOutputDir "RUN_SUMMARY.txt"
    if (Test-Path $runSummaryFile) {
        try {
            $content = Get-Content -Path $runSummaryFile -Raw -Encoding UTF8
            $updated = $content.Replace("amra_data_ready.xlsx", "manastore_pre_ready.xlsx")
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
