<#
Test de restauration : restaure une sauvegarde dans une base DISTINCTE
(par défaut gfa_restauration_test), compare le nombre de lignes de chaque
table avec la base de production, puis supprime la base de test.

La base de production n'est jamais modifiée.

Usage :
  powershell -ExecutionPolicy Bypass -File scripts\test_restauration.ps1 -Sauvegarde D:\sauvegardes\...\fichier.dump
  (sans -Sauvegarde : la plus récente du dossier par défaut)
#>
param(
    [string]$Sauvegarde = "",
    [string]$Dossier = "D:\sauvegardes\gestion_file_attente",
    [string]$BaseTest = "gfa_restauration_test",
    [switch]$Conserver,
    [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin"
)

$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $PSScriptRoot

$env_ = @{}
Get-Content (Join-Path $racine ".env") -Encoding UTF8 | ForEach-Object {
    $ligne = $_.Trim().TrimStart([char]0xFEFF)
    if ($ligne -and -not $ligne.StartsWith("#") -and $ligne.Contains("=")) {
        $cle, $valeur = $ligne.Split("=", 2); $env_[$cle.Trim()] = $valeur.Trim()
    }
}
if ($BaseTest -eq $env_["DB_NAME"]) { throw "La base de test doit être distincte de la base de production." }

if (-not $Sauvegarde) {
    $Sauvegarde = (Get-ChildItem $Dossier -Filter "*.dump" | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
}
if (-not $Sauvegarde -or -not (Test-Path $Sauvegarde)) { throw "Aucune sauvegarde trouvée." }

$env:PGPASSWORD = $env_["DB_PASSWORD"]
$hote = if ($env_["DB_HOST"]) { $env_["DB_HOST"] } else { "127.0.0.1" }
$port = if ($env_["DB_PORT"]) { $env_["DB_PORT"] } else { "5432" }
$connexion = @("-h", $hote, "-p", $port, "-U", $env_["DB_USER"])
$psql = Join-Path $PgBin "psql.exe"

& (Join-Path $PgBin "dropdb.exe") @connexion --if-exists $BaseTest
& (Join-Path $PgBin "createdb.exe") @connexion $BaseTest
if ($LASTEXITCODE -ne 0) { throw "Création de $BaseTest impossible." }

& (Join-Path $PgBin "pg_restore.exe") @connexion -d $BaseTest --no-owner --exit-on-error $Sauvegarde
if ($LASTEXITCODE -ne 0) { throw "pg_restore a échoué (code $LASTEXITCODE)." }

$requete = "select string_agg(relname || '=' || (xpath('/row/c/text()', query_to_xml('select count(*) c from ' || quote_ident(relname), false, true, '')))[1]::text, ' ' order by relname) from pg_stat_user_tables where schemaname = 'public'"
$source = & $psql @connexion -d $env_["DB_NAME"] -tAc $requete
$restauree = & $psql @connexion -d $BaseTest -tAc $requete
$triggers = & $psql @connexion -d $BaseTest -tAc "select count(*) from pg_trigger where not tgisinternal"
$tables = ($restauree -split " ").Count

Write-Output "Sauvegarde : $Sauvegarde"
Write-Output "Tables restaurées : $tables ; triggers métier : $triggers"
if ($source -eq $restauree) {
    Write-Output "RESULTAT : restauration conforme (nombre de lignes identique pour toutes les tables)."
} else {
    Write-Output "RESULTAT : différences détectées (normal si la base a changé depuis la sauvegarde) :"
    $a = $source -split " "; $b = $restauree -split " "
    Compare-Object $a $b | ForEach-Object { Write-Output "  $($_.InputObject) $($_.SideIndicator)" }
}

if (-not $Conserver) { & (Join-Path $PgBin "dropdb.exe") @connexion $BaseTest; Write-Output "Base $BaseTest supprimée." }
Remove-Item Env:\PGPASSWORD
