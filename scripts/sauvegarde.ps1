<#
Sauvegarde PostgreSQL de la base des files d'attente.

- Format personnalisé pg_dump (-Fc), compressé, restaurable avec pg_restore.
- Copie sur un second support si -CopieVers est indiqué (disque USB, partage réseau).
- Conserve les N dernières sauvegardes (par défaut 30).
- Paramètres de connexion lus dans le fichier .env du projet.

Planification (Planificateur de tâches Windows), chaque jour à 22 h 00 :
  powershell -ExecutionPolicy Bypass -File D:\gestion_file_attente\scripts\sauvegarde.ps1 -CopieVers E:\sauvegardes
  (dossier par défaut D:\sauvegardes\gestion_file_attente, celui que lit test_restauration.ps1)

Si la copie sur le second support échoue (clé absente…), la rotation locale
est faite quand même et le script se termine en erreur (code 2), visible
dans l'historique du Planificateur de tâches.
#>
param(
    [string]$Dossier = "D:\sauvegardes\gestion_file_attente",
    [string]$CopieVers = "",
    [int]$Conserver = 30,
    [string]$PgBin = "C:\Program Files\PostgreSQL\18\bin"
)

$ErrorActionPreference = "Stop"
$racine = Split-Path -Parent $PSScriptRoot

function Lire-Env([string]$chemin) {
    $valeurs = @{}
    Get-Content $chemin -Encoding UTF8 | ForEach-Object {
        $ligne = $_.Trim().TrimStart([char]0xFEFF)
        if ($ligne -and -not $ligne.StartsWith("#") -and $ligne.Contains("=")) {
            $cle, $valeur = $ligne.Split("=", 2)
            # Guillemets retirés, comme le fait python-dotenv.
            $valeurs[$cle.Trim()] = $valeur.Trim().Trim('"').Trim("'")
        }
    }
    return $valeurs
}

$env_ = Lire-Env (Join-Path $racine ".env")
$env:PGPASSWORD = $env_["DB_PASSWORD"]
$hote = if ($env_["DB_HOST"]) { $env_["DB_HOST"] } else { "127.0.0.1" }
$port = if ($env_["DB_PORT"]) { $env_["DB_PORT"] } else { "5432" }

New-Item -ItemType Directory -Force -Path $Dossier | Out-Null
$horodatage = Get-Date -Format "yyyyMMdd_HHmmss"
$fichier = Join-Path $Dossier "$($env_['DB_NAME'])_$horodatage.dump"

& (Join-Path $PgBin "pg_dump.exe") -h $hote -p $port -U $env_["DB_USER"] -Fc -f $fichier $env_["DB_NAME"]
if ($LASTEXITCODE -ne 0) { throw "pg_dump a échoué (code $LASTEXITCODE)." }

# Vérifie que l'archive est lisible.
& (Join-Path $PgBin "pg_restore.exe") --list $fichier | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Archive illisible : $fichier" }

$taille = [math]::Round((Get-Item $fichier).Length / 1KB, 1)
Write-Output "Sauvegarde créée : $fichier ($taille Ko)"

Remove-Item Env:\PGPASSWORD

# Rotation : on garde les $Conserver plus récentes (avant la copie, pour qu'un
# problème de second support n'empêche jamais de libérer de la place).
Get-ChildItem $Dossier -Filter "*.dump" | Sort-Object LastWriteTime -Descending |
    Select-Object -Skip $Conserver | Remove-Item -Force

if ($CopieVers) {
    try {
        # Le lecteur doit exister (clé ou disque branché) : jamais de copie
        # dans un dossier créé ailleurs à la place du second support.
        $lecteur = Split-Path -Qualifier $CopieVers -ErrorAction SilentlyContinue
        if ($lecteur -and -not (Test-Path "$lecteur\")) { throw "le lecteur $lecteur n'est pas branché" }
        New-Item -ItemType Directory -Force -Path $CopieVers | Out-Null
        Copy-Item $fichier $CopieVers
        Write-Output "Copie sur le second support : $CopieVers"
        # Même rotation sur le second support (sinon il finit par se remplir).
        Get-ChildItem $CopieVers -Filter "*.dump" | Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $Conserver | Remove-Item -Force
    } catch {
        Write-Error "ERREUR : copie sur le second support $CopieVers NON faite : $($_.Exception.Message)" -ErrorAction Continue
        exit 2
    }
}
