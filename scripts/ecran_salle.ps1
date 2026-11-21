<#
Écran de la salle (décision du 21/11/2026) : cherche l'écran physique relié
à cet ordinateur (TV ou moniteur en HDMI, ou écran sans fil connecté par
Windows) et y affiche l'écran des appels en plein écran, sans fenêtre de
navigateur visible sur l'écran de l'ordinateur.

- Tourne en continu : si l'écran de la salle est débranché, l'affichage
  s'arrête ; dès qu'il est rebranché, les appels s'y réaffichent.
- Si un écran est branché mais en mode « Dupliquer », passe Windows en mode
  « Étendre » (sinon il n'y a pas de second écran distinct).
- Rien ne passe par Internet : l'adresse est celle du serveur sur le réseau local.

Lancement manuel :
  powershell -ExecutionPolicy Bypass -File D:\gestion_file_attente\scripts\ecran_salle.ps1 -Adresse http://localhost:8000/circuit/ecran/salle/002/

Au démarrage de Windows : créer un raccourci de cette commande dans le
dossier « Démarrage » (touches Windows + R, taper shell:startup).
L'adresse exacte est affichée dans l'application : menu Écran → « Démarrage automatique ».
#>
param(
    [Parameter(Mandatory = $true)][string]$Adresse,
    [int]$IntervalleSecondes = 5,
    [switch]$SansEtendre
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Windows.Forms

function Trouver-Navigateur {
    $candidats = @(
        "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
        "$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe",
        "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
        "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe"
    )
    foreach ($chemin in $candidats) { if ($chemin -and (Test-Path $chemin)) { return $chemin } }
    throw "Ni Google Chrome ni Microsoft Edge n'a été trouvé sur cet ordinateur."
}

function Ecran-Salle {
    # L'écran de la salle : le premier écran qui n'est pas l'écran principal de l'ordinateur.
    return [System.Windows.Forms.Screen]::AllScreens | Where-Object { -not $_.Primary } | Select-Object -First 1
}

$navigateur = Trouver-Navigateur
# Profil séparé : l'écran ne mélange pas sa session avec celle des agents.
$profil = Join-Path $env:LOCALAPPDATA "gestion_file_attente\ecran_salle"
New-Item -ItemType Directory -Force -Path $profil | Out-Null
$processus = $null
$etendreTente = $false

Write-Output "Écran de la salle : recherche d'un écran relié à cet ordinateur (Ctrl+C pour arrêter)."
while ($true) {
    $ecran = Ecran-Salle
    if ($null -eq $ecran) {
        if ($processus -and -not $processus.HasExited) {
            Write-Output "$(Get-Date -Format HH:mm:ss) Écran de la salle débranché : affichage arrêté."
            Stop-Process -Id $processus.Id -Force -ErrorAction SilentlyContinue
            $processus = $null
        }
        if (-not $SansEtendre -and -not $etendreTente) {
            # Un écran branché en « Dupliquer » n'apparaît pas comme second écran.
            & "$env:windir\System32\DisplaySwitch.exe" /extend
            $etendreTente = $true
        }
    } elseif ($null -eq $processus -or $processus.HasExited) {
        $zone = $ecran.Bounds
        Write-Output "$(Get-Date -Format HH:mm:ss) Écran trouvé ($($zone.Width) x $($zone.Height)) : affichage des appels."
        $arguments = @(
            "--user-data-dir=`"$profil`"",
            "--kiosk", "--edge-kiosk-type=fullscreen",
            "--window-position=$($zone.X),$($zone.Y)",
            "--window-size=$($zone.Width),$($zone.Height)",
            "--autoplay-policy=no-user-gesture-required",
            "--no-first-run", "--disable-session-crashed-bubble",
            $Adresse
        )
        $processus = Start-Process -FilePath $navigateur -ArgumentList $arguments -PassThru
        $etendreTente = $false
    }
    Start-Sleep -Seconds $IntervalleSecondes
}
