<#
    Clean reset for a demo run.

    Re-dropping the same PDFs does nothing on its own: the ingest ledger is keyed
    on the SHA-256 of the file's CONTENTS, so identical bytes are recognised and
    left in place however the file is named. Only clearing the ledger makes them
    processable again - and the folders have to be cleared with it, or the next
    run's counts include last run's outbound files.

    Safe to run with the backend up. Folders are cleared before the ledger, so
    the scanner never sees a file it has just been told to forget.

    Usage:  .\reset-demo.ps1
            .\reset-demo.ps1 -NoCopy        # reset only, drop the faxes by hand
#>
param(
    [string] $TestRoot   = "C:\CWC_TEST",
    [string] $SampleDir  = "$env:USERPROFILE\Downloads\CWC_DATASET_PATH",
    [string] $PgBin      = "C:\Program Files\PostgreSQL\17\bin",
    [switch] $NoCopy
)

$ErrorActionPreference = "Stop"

if (Test-Path $PgBin) { $env:Path += ";$PgBin" }
$env:PGPASSWORD = "cwc_dev_password"

Write-Host "`n[1/4] Clearing folders under $TestRoot" -ForegroundColor Cyan
foreach ($dir in @("Inbound", "Outbound", "incoming")) {
    $path = Join-Path $TestRoot $dir
    if (Test-Path $path) {
        # Includes _processed and _failed, which live inside Inbound.
        Remove-Item -Recurse -Force "$path\*" -ErrorAction SilentlyContinue
        Write-Host "      cleared $dir"
    } else {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Write-Host "      created $dir"
    }
}

Write-Host "`n[2/4] Truncating pipeline tables" -ForegroundColor Cyan
# flyway_schema_history is deliberately NOT in this list: dropping it would make
# Flyway re-run every migration against a schema that already has the columns.
$sql = "TRUNCATE cwc.ingest_ledger, cwc.routing_decisions, cwc.classification_results, " +
       "cwc.extracted_metadata, cwc.fax_documents RESTART IDENTITY CASCADE;"
psql -U cwc -h localhost -d cwc -c $sql

Write-Host "`n[3/4] Verifying the ledger is empty" -ForegroundColor Cyan
$remaining = (psql -U cwc -h localhost -d cwc -tAc "SELECT count(*) FROM cwc.ingest_ledger").Trim()
if ($remaining -ne "0") {
    Write-Host "      ledger still holds $remaining rows - reset did NOT complete" -ForegroundColor Red
    exit 1
}
Write-Host "      ledger empty" -ForegroundColor Green

if ($NoCopy) {
    Write-Host "`n[4/4] Skipped (-NoCopy). Drop faxes into $TestRoot\Inbound when ready.`n" -ForegroundColor Yellow
    exit 0
}

Write-Host "`n[4/4] Copying sample faxes into Inbound" -ForegroundColor Cyan
if (-not (Test-Path $SampleDir)) {
    Write-Host "      $SampleDir not found - drop faxes in by hand" -ForegroundColor Yellow
    exit 0
}
# Top level only. "Sample for Avenir Digital" is CWC's ground-truth tree, not input.
$files = Get-ChildItem -Path $SampleDir -Filter *.pdf -File
Copy-Item -Path $files.FullName -Destination (Join-Path $TestRoot "Inbound")
Write-Host "      copied $($files.Count) PDFs" -ForegroundColor Green

Write-Host "`nDone. The scanner picks these up within 15s.`n" -ForegroundColor Green
